import os
import re
import tempfile
import time
import uuid
from pathlib import Path
from typing import Optional
from fastapi import APIRouter, Depends, UploadFile, File, Form, HTTPException, status
from fastapi.responses import JSONResponse

from server import invites
from server.auth import VoicePrincipal, verify_api_token, verify_voice_token
from server.repositories import ServerRepository
from server.storage import storage_manager
from server.services.package_validation import ServerPackageValidator
from server.config import server_settings

router = APIRouter(prefix="/api/v1")
repo = ServerRepository()


@router.get("/health")
def get_health() -> dict:
    """Unauthenticated health check endpoint."""
    return {
        "status": "ok",
        "service": "R6Analyzer Remote Server",
        "version": server_settings.ALLOWED_PACKAGE_VERSION,
    }


@router.get("/auth/test")
def test_auth(client_name: str = Depends(verify_api_token)) -> dict:
    """Authenticated auth test endpoint."""
    return {"status": "authenticated", "client": client_name}


@router.post("/sessions/upload")
def upload_session_package(
    file: UploadFile = File(...),
    client_name: str = Depends(verify_api_token),
) -> dict:
    """
    Authenticated upload endpoint for immutable .r6session archives.
    Streams upload content, validates package integrity, deduplicates, and enqueues processing job.
    """
    filename = file.filename or "upload.r6session"
    if not filename.endswith(".r6session") and not filename.endswith(".zip"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid file extension. Expected .r6session archive.",
        )

    # 1. Stream upload and enforce size limits
    try:
        temp_file, file_hash, file_size = storage_manager.stream_upload(file.file, filename)
    except ValueError as val_err:
        raise HTTPException(status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, detail=str(val_err))
    except Exception as err:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=f"Upload stream error: {err}")

    # 2. Validate package structure and manifest checksums
    valid, err_msg, manifest = ServerPackageValidator.validate_package(temp_file)
    if not valid:
        if temp_file.exists():
            try:
                temp_file.unlink()
            except Exception:
                pass
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"Package validation failed: {err_msg}")

    session_id = str(manifest.get("session_id", ""))
    if not session_id:
        if temp_file.exists():
            try:
                temp_file.unlink()
            except Exception:
                pass
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Manifest missing session_id")

    is_complete = bool(manifest.get("is_complete", True))

    # 3. Duplicate handling policy
    existing_session = repo.get_session(session_id)
    existing_package = repo.get_package(file_hash)

    # Case 1: Same session ID + same package hash => idempotent success
    if existing_session and existing_package:
        if temp_file.exists():
            try:
                temp_file.unlink()
            except Exception:
                pass
        job = repo.get_job_by_session(session_id)
        return {
            "session_id": session_id,
            "job_id": job["job_id"] if job else "unknown",
            "status": job["status"] if job else "completed",
            "is_duplicate": True,
            "message": "Session package already uploaded and registered.",
        }

    # Case 2: Same session ID + different package hash => Conflict 409
    if existing_session and not existing_package:
        if temp_file.exists():
            try:
                temp_file.unlink()
            except Exception:
                pass
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Session ID {session_id} already exists with a different package content.",
        )

    # Case 3: Different session ID + same package hash => content duplicate
    if not existing_session and existing_package:
        if temp_file.exists():
            try:
                temp_file.unlink()
            except Exception:
                pass
        orig_session_id = existing_package["session_id"]
        job = repo.get_job_by_session(orig_session_id)
        return {
            "session_id": orig_session_id,
            "job_id": job["job_id"] if job else "unknown",
            "status": job["status"] if job else "completed",
            "is_duplicate": True,
            "message": "Content duplicate package already exists under an existing session.",
        }

    # 4. Store archive permanently in server_data/uploads/<hash>.r6session
    final_archive = storage_manager.store_permanent_archive(temp_file, file_hash)

    # 5. Create database records and enqueue job
    repo.create_session(
        session_id=session_id,
        client_name=client_name,
        map_name="Pending",
    )
    repo.create_package(
        package_hash=file_hash,
        session_id=session_id,
        file_name=final_archive.name,
        file_size_bytes=file_size,
        is_complete=is_complete,
    )

    job_id = f"job_{uuid.uuid4().hex[:12]}"
    job = repo.create_job(job_id=job_id, session_id=session_id, package_hash=file_hash, initial_status="queued")

    return {
        "session_id": session_id,
        "job_id": job_id,
        "status": "queued",
        "is_duplicate": False,
        "message": "Package uploaded and queued for processing.",
    }


@router.get("/sessions")
def list_sessions(limit: int = 50, _client: str = Depends(verify_api_token)) -> dict:
    """Authenticated endpoint listing recent server sessions."""
    sessions = repo.list_sessions(limit=limit)
    return {"sessions": sessions, "count": len(sessions)}


@router.get("/sessions/{session_id}")
def get_session_details(session_id: str, _client: str = Depends(verify_api_token)) -> dict:
    """Authenticated endpoint fetching session details."""
    session = repo.get_session(session_id)
    if not session:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Session {session_id} not found.")

    job = repo.get_job_by_session(session_id)
    return {"session": session, "job": job}


@router.get("/sessions/{session_id}/status")
def get_session_status(session_id: str, _client: str = Depends(verify_api_token)) -> dict:
    """Authenticated endpoint fetching processing status for a session."""
    job = repo.get_job_by_session(session_id)
    if not job:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"No job found for session {session_id}.")
    return {
        "session_id": session_id,
        "job_id": job["job_id"],
        "status": job["status"],
        "attempts": job["attempts"],
        "error_message": job["error_message"],
        "updated_at": job["updated_at"],
    }


@router.post("/sessions/{session_id}/retry")
def retry_failed_session_job(session_id: str, _client: str = Depends(verify_api_token)) -> dict:
    """Authenticated endpoint to re-queue a failed processing job."""
    job = repo.get_job_by_session(session_id)
    if not job:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"No job found for session {session_id}.")

    if job["status"] not in ("failed", "interrupted"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Cannot retry job in status '{job['status']}'. Only failed jobs can be retried.",
        )

    repo.update_job_status(job_id=job["job_id"], status="queued", error_message=None)
    return {
        "session_id": session_id,
        "job_id": job["job_id"],
        "status": "queued",
        "message": "Job successfully re-queued for processing.",
    }


@router.get("/sessions/{session_id}/summary")
def get_session_summary(session_id: str, _client: str = Depends(verify_api_token)) -> dict:
    """Authenticated endpoint returning the parsed analysis summary for a
    session (map, score, rounds parsed, etc.), produced once its upload job
    finishes processing. 404 until then — poll /status in the meantime."""
    parsed = repo.get_parsed_match(session_id)
    if not parsed:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"No parsed summary yet for session {session_id} (check /status).",
        )
    return {"session_id": session_id, "summary": parsed}


@router.get("/sessions/{session_id}/transcript")
def get_session_transcript(session_id: str, _client: str = Depends(verify_api_token)) -> dict:
    """Authenticated endpoint returning the server-side Whisper transcript
    for a session (Milestone 4, phase 1), if the upload included audio and
    transcription has finished. 404 if there's no transcript yet — either
    still processing, no audio was uploaded, or transcription failed
    (check the server console log for the latter)."""
    transcript = repo.get_transcript(session_id)
    if not transcript:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=(
                f"No transcript for session {session_id} yet. This means either "
                "processing isn't finished, the client didn't upload audio for "
                "this session, or transcription failed."
            ),
        )
    return {"session_id": session_id, "transcript": transcript}


@router.get("/sessions/{session_id}/analysis")
def get_session_analysis(session_id: str, _client: str = Depends(verify_api_token)) -> dict:
    """Authenticated endpoint returning the server-generated AI analysis for
    a session (Milestone 4, phase 2) — the same match summary and per-player
    intel the client's Intel Engine produces locally, generated here instead
    so the client never has to wait on it. Always 200: the response's
    `status` field ("pending" | "analyzing" | "completed" | "failed") tells
    you where things stand, with `error` explaining a failure (most likely
    cause: no AI backend configured on the server yet — see server console)
    rather than the client just seeing a bare 404 and not knowing why."""
    parsed = repo.get_parsed_match(session_id)
    if not parsed:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Unknown session {session_id} (check /status).",
        )

    analysis_status = parsed.get("analysis_status", "pending")
    match_id = parsed.get("match_id")
    response = {
        "session_id": session_id,
        "status": analysis_status,
        "error": parsed.get("analysis_error"),
        "match_summary": None,
        "player_intel": {},
    }

    if analysis_status == "completed" and match_id is not None:
        from server.match_db import get_match_analysis

        analysis = get_match_analysis(match_id)
        response["match_summary"] = analysis.get("match_summary")
        response["player_intel"] = analysis.get("player_intel", {})

    return response


@router.get("/sessions/{session_id}/comms")
def get_session_comms(session_id: str, _client: str = Depends(verify_api_token)) -> dict:
    """The session's comms timeline: every line of speech with who said it,
    the kill feed on the same clock, per-speaker stats and flagged moments.
    Rebuilt automatically when a teammate's R6Voice recording arrives."""
    from server.services.comms_service import CommsService
    timeline = CommsService.get_timeline(session_id)
    if timeline is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"No comms timeline for session {session_id} yet.",
        )
    return {"session_id": session_id, "timeline": timeline}


# ── Teammate voice recordings (R6Voice) ─────────────────────────────────

_VOICE_USERNAME = re.compile(r"^[A-Za-z0-9_.\-]{2,32}$")
_VOICE_RECORDING = re.compile(r"^[0-9a-f]{32}$")
_VOICE_SUFFIXES = {".ogg", ".opus", ".flac", ".wav"}
_VOICE_MAX_BYTES = 60 * 1024 * 1024


_INVITE_MAX_BYTES = 25 * 1024 * 1024   # a browser chunk is ~0.6 MB; anything bigger isn't one
_INVITE_MAX_SEC_PER_DAY = 14 * 3600    # far more than any practice, far less than a disk


@router.get("/voice/ping")
def voice_ping(_client: VoicePrincipal = Depends(verify_voice_token)) -> dict:
    """R6Voice's connection check. Also returns the server's clock, so the
    recorder can show how far off its own PC clock is."""
    return {"status": "ok", "server_time": time.time()}


@router.post("/voice/chunks")
def upload_voice_chunk(
    file: UploadFile = File(...),
    recording_id: str = Form(...),
    chunk_index: int = Form(...),
    username: str = Form(...),
    start_epoch: float = Form(...),
    duration_sec: float = Form(...),
    sample_rate: int = Form(...),
    is_final: bool = Form(False),
    principal: VoicePrincipal = Depends(verify_voice_token),
) -> dict:
    """One chunk (up to ~5 minutes) of a teammate's own mic. Idempotent:
    re-sending the same chunk after a dropped connection just overwrites it."""
    username = username.strip()
    suffix = Path(file.filename or "").suffix.lower()
    max_bytes = _VOICE_MAX_BYTES
    if principal.kind == "invite":
        if (principal.username or "").lower() != username.lower():
            raise HTTPException(status_code=403, detail="This invite link is for a different player.")
        username = principal.username or username
        max_bytes = _INVITE_MAX_BYTES
    if not _VOICE_USERNAME.match(username):
        raise HTTPException(status_code=400, detail="Username must be your in-game name (2-32 letters, digits, . _ -).")
    if not _VOICE_RECORDING.match(recording_id):
        raise HTTPException(status_code=400, detail="Bad recording_id.")
    if suffix not in _VOICE_SUFFIXES:
        raise HTTPException(status_code=400, detail=f"Unsupported audio type {suffix!r}.")
    if not (0 <= chunk_index < 10000 and 0 < duration_sec <= 900 and 8000 <= sample_rate <= 48000):
        raise HTTPException(status_code=400, detail="Chunk index, duration or sample rate out of range.")
    if abs(start_epoch - time.time()) > 30 * 24 * 3600:
        raise HTTPException(status_code=400, detail="start_epoch is not a plausible time -- check the PC clock.")
    if principal.kind == "invite":
        # A leaked link must not be able to fill the disk: a day of one player's audio is capped.
        from datetime import datetime, timedelta, timezone
        since = (datetime.now(timezone.utc) - timedelta(hours=24)).isoformat()
        with repo.db.get_connection() as conn:
            used = conn.execute(
                "SELECT COALESCE(SUM(duration_sec), 0) AS s FROM voice_chunks WHERE lower(username) = ? AND uploaded_at > ?",
                (username.lower(), since),
            ).fetchone()["s"]
        if used + duration_sec > _INVITE_MAX_SEC_PER_DAY:
            raise HTTPException(status_code=429, detail="Daily recording limit reached for this link.")

    server_settings.VOICE_DIR.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(suffix=suffix, dir=server_settings.VOICE_DIR)
    os.close(fd)
    tmp = Path(tmp_name)
    try:
        size = 0
        with tmp.open("wb") as out:
            while block := file.file.read(1 << 20):
                size += len(block)
                if size > max_bytes:
                    raise HTTPException(status_code=413, detail="Voice chunk too large.")
                out.write(block)
        from server.services.comms_service import CommsService
        try:
            stored = CommsService.store_voice_chunk(
                tmp, recording_id=recording_id, chunk_index=chunk_index, username=username,
                start_epoch=start_epoch, duration_sec=duration_sec, sample_rate=sample_rate,
                is_final=is_final, suffix=suffix,
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc))
        return {"status": "stored", **stored}
    finally:
        # _compress_wav may have swapped `tmp` for a sibling .ogg, so sweep both.
        for leftover in (tmp, tmp.with_suffix(".ogg")):
            if leftover.exists():
                try:
                    leftover.unlink()
                except OSError:
                    pass


@router.get("/join/whoami")
def join_whoami(principal: VoicePrincipal = Depends(verify_voice_token)) -> dict:
    """The browser recorder's first call: whose link is this, and what time
    does the server think it is (for the page's clock-offset estimate)."""
    return {"kind": principal.kind, "username": principal.username, "server_time": time.time()}


@router.get("/join/mystats")
def join_mystats(principal: VoicePrincipal = Depends(verify_voice_token)) -> dict:
    """A player's own numbers from the team's stored matches, for their invite link. Only an invite (one
    named player) can call it, and it only reads what is already stored: no analysis, no AI."""
    if principal.kind != "invite" or not principal.username:
        raise HTTPException(status_code=403, detail="Only a player's own invite link can see their stats.")
    from server.match_db import get_match_repo
    from server.services.player_stats import player_stats
    try:
        return player_stats(get_match_repo(), principal.username)
    except Exception as e:
        print(f"[MyStats] {principal.username}: {e}")
        raise HTTPException(status_code=503, detail="Stats are unavailable right now.")


# ── Browser-recorder invite links (host only) ────────────────────────────

def _invite_links(created: dict) -> dict:
    path = f"/join#{created['token']}"
    base = server_settings.PUBLIC_URL.rstrip("/")
    return {**created, "join_path": path, "join_url": f"{base}{path}" if base else ""}


@router.get("/invites")
def list_invites(_client: str = Depends(verify_api_token)) -> dict:
    return {"invites": invites.list_all()}


@router.post("/invites")
def create_invite(body: dict, _client: str = Depends(verify_api_token)) -> dict:
    """{"username": "<in-game name>", "days": 180, "label": "James"}. The token
    in the reply is the only time it is shown; the server keeps just a hash."""
    try:
        created = invites.create(str(body.get("username", "")), int(body.get("days") or invites.DEFAULT_DAYS),
                                 str(body.get("label", "")))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return _invite_links(created)


@router.get("/roster")
def get_roster(_client: str = Depends(verify_api_token)) -> dict:
    from server import roster
    return {"roster": roster.get_all()}


@router.put("/roster")
def put_roster(body: dict, _client: str = Depends(verify_api_token)) -> dict:
    """{"roster": [{"username": "<in-game name>", "label": "<nickname>"}, ...]} replaces the saved list."""
    from server import roster
    try:
        return {"roster": roster.replace_all(body.get("roster") or [])}
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@router.post("/sessions/{session_id}/reread-replays", status_code=202)
def reread_replays(session_id: str, _client: str = Depends(verify_api_token)) -> dict:
    """Re-read this session's stored replays for gadget usage and objective facts (no
    transcription), then regenerate its AI text. Runs in the background."""
    import threading

    def run() -> None:
        from server.services.comms_service import CommsService
        from server.services.utility_backfill import backfill_session
        result = backfill_session(session_id)
        if "match_id" in result:
            CommsService._refresh_ai(result["match_id"], refresh_players=True)

    threading.Thread(target=run, daemon=True, name="RereadReplays").start()
    return {"status": "started", "session_id": session_id}


@router.get("/voices")
def list_voices(_client: str = Depends(verify_api_token)) -> dict:
    """Whose voice the server has learned (from teammates' recordings) and how much it has to go on."""
    from server import voice_id
    return {"enabled": voice_id.enabled(), "profiles": voice_id.list_profiles()}


@router.delete("/voices/{username}")
def forget_voice(username: str, _client: str = Depends(verify_api_token)) -> dict:
    from server import voice_id
    if not voice_id.delete_profile(username):
        raise HTTPException(status_code=404, detail="No learned voice for that name.")
    return {"status": "forgotten", "username": username}


@router.post("/invites/{invite_id}/regenerate")
def regenerate_invite(invite_id: str, _client: str = Depends(verify_api_token)) -> dict:
    created = invites.regenerate(invite_id)
    if created is None:
        raise HTTPException(status_code=404, detail="No such invite.")
    return _invite_links(created)


@router.delete("/invites/{invite_id}")
def revoke_invite(invite_id: str, _client: str = Depends(verify_api_token)) -> dict:
    if not invites.revoke(invite_id):
        raise HTTPException(status_code=404, detail="No such active invite.")
    return {"status": "revoked", "invite_id": invite_id}


@router.get("/voice/recordings")
def list_voice_recordings(limit: int = 50, _client: str = Depends(verify_api_token)) -> dict:
    """Teammates' recordings the server has, newest first (for the dashboard)."""
    from server.services.comms_service import CommsService
    return {"recordings": CommsService.list_voice_recordings(limit=limit)}


# ── R6Companion relay ────────────────────────────────────────────────────
# The host's app says whether the team should be recording; companions on
# teammates' PCs check in, follow it, and report status. Everything goes
# through the server because school-managed PCs accept no incoming
# connections -- only outgoing ones, like these.

HOST_SILENT_STOP_SEC = 20 * 60   # host app gone this long while "recording": companions stop


def _companion_control(conn) -> dict:
    row = conn.execute("SELECT recording, changed_at, host_seen FROM companion_control WHERE id = 1").fetchone()
    if not row:
        return {"recording": False, "changed_at": 0.0, "host_seen": 0.0}
    rec = bool(row["recording"])
    if rec and time.time() - float(row["host_seen"]) > HOST_SILENT_STOP_SEC:
        rec = False          # the host's app crashed or lost its connection long ago
    return {"recording": rec, "changed_at": float(row["changed_at"]), "host_seen": float(row["host_seen"])}


@router.put("/companion/control")
def set_companion_control(body: dict, _client: str = Depends(verify_api_token)) -> dict:
    """The host's app: {"recording": true|false}. Re-sent every minute or so
    while recording, which is how companions know the host is still there."""
    want = bool(body.get("recording"))
    now = time.time()
    with repo.db.get_connection() as conn:
        prev = conn.execute("SELECT recording FROM companion_control WHERE id = 1").fetchone()
        changed = prev is None or bool(prev["recording"]) != want
        conn.execute(
            """INSERT INTO companion_control (id, recording, changed_at, host_seen) VALUES (1, ?, ?, ?)
               ON CONFLICT(id) DO UPDATE SET recording = excluded.recording, host_seen = excluded.host_seen,
                 changed_at = CASE WHEN ? THEN excluded.changed_at ELSE companion_control.changed_at END""",
            (int(want), now, now, int(changed)),
        )
        conn.commit()
        return {"control": _companion_control(conn)}


@router.post("/companion/heartbeat")
def companion_heartbeat(body: dict, principal: VoicePrincipal = Depends(verify_voice_token)) -> dict:
    """A companion's check-in: its status in, the team's recording state out."""
    import json as _json
    device_id = str(body.get("device_id", ""))[:64]
    username = str(body.get("username", "")).strip()[:32]
    status_body = body.get("status") if isinstance(body.get("status"), dict) else {}
    if principal.kind == "invite":
        # An invite is one named player on a browser: it can't speak for anyone
        # else, and its row can't collide with a companion's device id.
        username = principal.username or username
        device_id = f"web-{principal.invite_id}-{device_id}"[:64]
        status_body = {**status_body, "kind": "browser"}
    if not device_id or not _VOICE_USERNAME.match(username):
        raise HTTPException(status_code=400, detail="device_id and an in-game username are required.")
    status_json = _json.dumps(status_body)[:4000]
    with repo.db.get_connection() as conn:
        conn.execute(
            """INSERT INTO companions (device_id, username, status_json, last_seen) VALUES (?, ?, ?, ?)
               ON CONFLICT(device_id) DO UPDATE SET username = excluded.username,
                 status_json = excluded.status_json, last_seen = excluded.last_seen""",
            (device_id, username, status_json, time.time()),
        )
        conn.commit()
        return {"control": _companion_control(conn), "server_time": time.time()}


@router.get("/companion/status")
def companion_status(_client: str = Depends(verify_api_token)) -> dict:
    """Every companion that has checked in during the last day, for the host's
    app and the dashboard."""
    import json as _json
    now = time.time()
    with repo.db.get_connection() as conn:
        rows = conn.execute("SELECT * FROM companions WHERE last_seen > ? ORDER BY username",
                            (now - 24 * 3600,)).fetchall()
        control = _companion_control(conn)
    # What each person's newest recording on the server sounded like: a companion reports nothing about its
    # microphone while it records, so this is how the host finds out a mic picked up nothing.
    from server.services.comms_service import CommsService
    try:
        last = CommsService.last_recording_by_user([r["username"] for r in rows])
    except Exception:
        last = {}
    return {
        "control": control,
        "companions": [{"device_id": r["device_id"], "username": r["username"],
                        "seconds_since_seen": round(now - float(r["last_seen"]), 1),
                        "status": _json.loads(r["status_json"] or "{}"),
                        "last_recording": last.get(str(r["username"]).lower())} for r in rows],
    }


@router.get("/sessions/{session_id}/report")
def get_session_report(session_id: str, _client: str = Depends(verify_api_token)) -> dict:
    """Stubbed endpoint for report retrieval (deferred feature)."""
    raise HTTPException(
        status_code=status.HTTP_501_NOT_IMPLEMENTED,
        detail="Report generation endpoint is deferred to a future milestone.",
    )


@router.get("/sessions/{session_id}/pdf")
def download_session_pdf(session_id: str, _client: str = Depends(verify_api_token)) -> dict:
    """Stubbed endpoint for PDF downloads (deferred feature)."""
    raise HTTPException(
        status_code=status.HTTP_501_NOT_IMPLEMENTED,
        detail="PDF export endpoint is deferred to a future milestone.",
    )
