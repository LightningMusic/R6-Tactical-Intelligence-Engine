"""
Server side of the comms timeline.

  1. transcribe_host_audio(): when a session package is processed, each of the
     host's audio tracks is transcribed on its own -- "self" (the host's mic,
     so every line is theirs) and "team" (the Discord app's output: everyone
     else, mixed). Older packages carry one mixed track and are transcribed
     as before, just without speaker names.
  2. save_rounds(): the rounds and kill feed (with elapsed seconds) from the
     replays, once the match record exists.
  3. merge_voice(): teammates running R6Voice upload their own mic in 5-minute
     chunks, often after the match was already processed. For each session
     whose time window their recording overlaps, their audio is assembled,
     lined up against the host's Discord track (analysis.voice_align), gated
     to where they're actually speaking, and transcribed under their name.
  4. build(): all speech + the kill feed -> analysis.comms_timeline, saved
     here and written into the match database (speaker-labelled transcript,
     the timeline itself, and a measured comms summary for the AI debrief).

process_pending() is called by the worker whenever it's idle; it picks up
teammate recordings that arrived since a session was last built.
"""
from __future__ import annotations

import hashlib
import json
import shutil
import tempfile
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from server.config import server_settings
from server.database import server_db

SR = 16000
VOICE_MARGIN_SEC = 120.0      # teammate audio gathered this far either side of the match window
SETTLE_SEC = 90.0             # wait this long after a teammate's newest chunk before rebuilding

_transcriber_lock = threading.Lock()
_transcriber = None


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _get_transcriber():
    global _transcriber
    with _transcriber_lock:
        if _transcriber is None:
            from integration.whisper_transcriber import WhisperTranscriber
            _transcriber = WhisperTranscriber(
                model_path=server_settings.WHISPER_MODEL_PATH,
                model_size=server_settings.WHISPER_MODEL_SIZE,
            )
        return _transcriber


def _load_audio(path: Path):
    """Any audio file -> mono float32 at 16 kHz."""
    from integration.whisper_transcriber import _find_ffmpeg, _load_whisper_audio
    return _load_whisper_audio(_find_ffmpeg(), path)


def _local_utc_offset() -> float:
    return float(time.localtime().tm_gmtoff)


class ClipClock:
    """Maps seconds into a clip onto epoch time. A clip can be stitched from
    several recording files (pieces), each starting at its own epoch."""

    def __init__(self, pieces: list[list[float]]):
        self.pieces = [(float(a), float(d)) for a, d in pieces if d > 0]

    def epoch(self, t: float) -> float:
        acc = 0.0
        for start, dur in self.pieces:
            if t < acc + dur or (start, dur) == self.pieces[-1]:
                return start + (t - acc)
            acc += dur
        return t

    @property
    def start(self) -> float:
        return self.pieces[0][0]

    @property
    def end(self) -> float:
        return self.pieces[-1][0] + self.pieces[-1][1]


def _utterances(segments: list[dict], clock: ClipClock, source: str, speaker: str,
                username: str = "", time_map=None) -> list[dict]:
    """Whisper segments -> utterances in epoch time. Word timestamps give
    tighter edges than the segment's own (which include leading silence)."""
    out = []
    for s in segments:
        text = (s.get("text") or "").strip()
        if not text:
            continue
        words = [w for w in (s.get("words") or []) if isinstance(w, dict)]
        t0 = float(words[0]["start"]) if words else float(s.get("start", 0.0))
        t1 = float(words[-1]["end"]) if words else float(s.get("end", t0))
        if time_map is not None:
            t0, t1 = time_map(t0), time_map(t1)
        out.append({"speaker": speaker, "username": username, "source": source,
                    "start": round(clock.epoch(t0), 3), "end": round(clock.epoch(max(t1, t0 + 0.2)), 3),
                    "text": text})
    return out


class CommsService:

    # ── 1. Host tracks ─────────────────────────────────────────────────

    @classmethod
    def transcribe_host_audio(cls, work_dir: Path, meta: dict, session_id: str) -> Optional[dict]:
        """Returns {"text": ..., "word_count": ..., "segments": [...]} for the
        legacy transcript tables, or None when there's no audio."""
        audio_dir = work_dir / "audio"
        if not audio_dir.is_dir():
            return None
        audio_meta = dict(meta.get("audio") or {})
        files = {p.stem: p for p in audio_dir.iterdir() if p.is_file()}
        if audio_meta.get("layout") == "split" and "team" in files:
            tracks = [(name, files[name]) for name in ("self", "team") if name in files]
        elif files:
            tracks = [("mixed", sorted(files.values())[0])]
        else:
            return None

        roster = {k.lower(): v for k, v in (audio_meta.get("roster") or {}).items()}
        self_user = str(audio_meta.get("self_username") or "")
        self_name = roster.get(self_user.lower(), self_user or "You")
        pieces = audio_meta.get("pieces") or []
        # Older packages don't say where the clip sits in time. Their
        # utterances are kept relative to the clip start until save_rounds()
        # can anchor them to the first round.
        clip_relative = not pieces
        audio_meta.setdefault("utc_offset_sec", _local_utc_offset())

        transcriber = _get_transcriber()
        all_utts: list[dict] = []
        legacy_segments: list[dict] = []
        comms_dir = server_settings.COMMS_DIR / session_id
        comms_dir.mkdir(parents=True, exist_ok=True)
        from analysis.comms_timeline import UNKNOWN_TEAM_SPEAKER
        from analysis.voice_align import keep_voiced_segments, speech_regions

        for source, path in tracks:
            result = transcriber.transcribe_full(path)
            segments = result.get("segments", []) or []
            if source == "self":
                # One person's mic: drop Whisper's inventions over silence.
                segments = keep_voiced_segments(segments, speech_regions(_load_audio(path), SR))
            legacy_segments.extend(segments)
            if clip_relative:
                from integration.whisper_transcriber import _find_ffmpeg, _get_audio_duration
                pieces = [[0.0, float(_get_audio_duration(_find_ffmpeg(), path))]]
            speaker = {"self": self_name, "team": UNKNOWN_TEAM_SPEAKER}.get(source, "Comms (mixed)")
            all_utts.extend(_utterances(segments, ClipClock(pieces), source, speaker,
                                        self_user if source == "self" else ""))
            if source in ("team", "mixed"):
                shutil.copy2(path, comms_dir / f"team{path.suffix}")
            print(f"[Comms] {session_id[:16]} {source} track: {len(segments)} lines")

        audio_meta["pieces"] = pieces
        audio_meta["clip_relative"] = clip_relative
        audio_meta["tracks"] = [t for t, _ in tracks]
        cls._upsert(session_id, audio_meta_json=json.dumps(audio_meta),
                    host_utterances_json=json.dumps(all_utts),
                    window_start=None if clip_relative else ClipClock(pieces).start,
                    window_end=None if clip_relative else ClipClock(pieces).end)
        legacy_segments.sort(key=lambda s: s.get("start", 0))
        text = " ".join((u["text"] for u in sorted(all_utts, key=lambda u: u["start"])))
        return {"text": text, "segments": legacy_segments, "word_count": len(text.split())}

    # ── 2. Rounds ─────────────────────────────────────────────────────

    @classmethod
    def save_rounds(cls, session_id: str, rounds: list[dict], match_id: Optional[int]) -> None:
        row = cls._get(session_id)
        meta = json.loads(row["audio_meta_json"]) if row else {}
        fields: dict[str, Any] = {"rounds_json": json.dumps(rounds), "match_id": match_id}
        if row and meta.get("clip_relative") and rounds:
            # No clip timing in the package (older client): anchor the clip
            # 30 s before the first round, which is how the client cut it --
            # wrong only when the recording itself started later than that.
            from analysis.comms_timeline import local_stamp_to_epoch
            offset = float(meta.get("utc_offset_sec", _local_utc_offset()))
            first = min(filter(None, (local_stamp_to_epoch(r.get("timestamp", ""), offset) for r in rounds)),
                        default=None)
            if first is not None:
                duration = sum(float(d) for _, d in meta.get("pieces") or [])
                start = first - 30.0
                meta["pieces"] = [[start, duration]]
                meta["clip_relative"] = False
                meta["clip_start_estimated"] = True
                fields.update(audio_meta_json=json.dumps(meta), window_start=start, window_end=start + duration)
                utts = json.loads(row["host_utterances_json"] or "[]")
                for u in utts:          # were stored relative to clip start (0)
                    u["start"] += start
                    u["end"] += start
                fields["host_utterances_json"] = json.dumps(utts)
        cls._upsert(session_id, **fields)

    # ── 3. Teammates' recordings ──────────────────────────────────────

    @classmethod
    def voice_chunks_for_window(cls, start: float, end: float) -> dict[str, list[dict]]:
        with server_db.get_connection() as conn:
            rows = conn.execute(
                """SELECT * FROM voice_chunks
                   WHERE start_epoch < ? AND start_epoch + duration_sec > ?
                   ORDER BY username, start_epoch""",
                (end + VOICE_MARGIN_SEC, start - VOICE_MARGIN_SEC),
            ).fetchall()
        by_user: dict[str, list[dict]] = {}
        for r in rows:
            by_user.setdefault(r["username"], []).append(dict(r))
        return by_user

    @staticmethod
    def _signature(chunks: list[dict]) -> str:
        return hashlib.sha1("|".join(f"{c['recording_id']}:{c['chunk_index']}:{c['sha256']}"
                                     for c in chunks).encode()).hexdigest()

    @classmethod
    def merge_voice(cls, session_id: str, force: bool = False) -> bool:
        """Folds in any teammate recordings overlapping this session that
        haven't been processed yet. Returns True if anything changed."""
        row = cls._get(session_id)
        if not row or row["window_start"] is None:
            return False
        ws, we = float(row["window_start"]), float(row["window_end"])
        meta = json.loads(row["audio_meta_json"] or "{}")
        team_clip = next(iter(sorted((server_settings.COMMS_DIR / session_id).glob("team.*"))), None)
        if team_clip is None:
            return False
        roster = {k.lower(): v for k, v in (meta.get("roster") or {}).items()}
        self_user = str(meta.get("self_username") or "").lower()

        changed = False
        ref = None
        for username, chunks in cls.voice_chunks_for_window(ws, we).items():
            if username.lower() == self_user:
                continue            # the host's own mic is already the "self" track
            sig_hash = cls._signature(chunks)
            with server_db.get_connection() as conn:
                prev = conn.execute("SELECT chunks_signature FROM session_voice WHERE session_id=? AND username=?",
                                    (session_id, username)).fetchone()
            if prev and prev["chunks_signature"] == sig_hash and not force:
                continue
            if ref is None:
                ref = _load_audio(team_clip)
            utts, info = cls._voice_utterances(ref, meta, ws, we, username,
                                               roster.get(username.lower(), username), chunks)
            with server_db.get_connection() as conn:
                conn.execute(
                    """INSERT INTO session_voice (session_id, username, chunks_signature, alignment_json,
                                                  utterances_json, updated_at)
                       VALUES (?, ?, ?, ?, ?, ?)
                       ON CONFLICT(session_id, username) DO UPDATE SET
                         chunks_signature=excluded.chunks_signature, alignment_json=excluded.alignment_json,
                         utterances_json=excluded.utterances_json, updated_at=excluded.updated_at""",
                    (session_id, username, sig_hash, json.dumps(info), json.dumps(utts), _now()),
                )
                conn.commit()
            print(f"[Comms] {session_id[:16]} {username}: {info.get('status')} -- {len(utts)} lines")
            changed = True
        if changed:
            cls.build(session_id)
        return changed

    @classmethod
    def _voice_utterances(cls, ref, meta, ws, we, username, speaker, chunks) -> tuple[list[dict], dict]:
        import numpy as np
        import soundfile as sf
        from analysis.voice_align import HEARD_MIN_CORR, align, heard_on_ref, speech_regions, keep_voiced_segments

        clock = ClipClock(meta.get("pieces") or [[ws, we - ws]])
        n = len(ref)
        sig = np.zeros(n, dtype=np.float32)
        covered = 0.0
        for c in chunks:
            try:
                data, sr = sf.read(c["file_path"], dtype="float32", always_2d=False)
            except Exception as e:
                print(f"[Comms] could not read voice chunk {c['file_path']}: {e}")
                continue
            if data.ndim > 1:
                data = data.mean(axis=1)
            if sr != SR:
                data = np.interp(np.arange(0, len(data), sr / SR), np.arange(len(data)), data).astype(np.float32)
            at = int(round((float(c["start_epoch"]) - clock.start) * SR))
            lo, hi = max(0, at), min(n, at + len(data))
            if hi > lo:
                sig[lo:hi] = data[lo - at:hi - at]
                covered += (hi - lo) / SR
        info: dict[str, Any] = {"username": username, "covered_sec": round(covered, 1)}
        if covered < 30:
            info["status"] = "not in this match"
            return [], info

        a = align(ref, sig, SR)
        if a is None:
            info["status"] = "could not line up with the Discord track"
            return [], info
        info.update(status="aligned", offset_sec=round(a.offset_sec, 3),
                    drift_ppm=round(a.drift_per_sec * 1e6, 1), confidence=round(a.confidence, 1))

        # Silence everything that isn't speech before Whisper sees it: a
        # single person's mic is mostly quiet, and Whisper fills quiet with
        # invented phrases.
        regions = speech_regions(sig, SR)
        mask = np.zeros(n, dtype=np.float32)
        for r0, r1 in regions:
            mask[int(r0 * SR):int(r1 * SR)] = 1.0
        info["speech_sec"] = round(float(mask.sum()) / SR, 1)
        if info["speech_sec"] < 1.0:
            return [], info
        with tempfile.TemporaryDirectory() as tmp:
            wav = Path(tmp) / f"{username}.wav"
            sf.write(wav, sig * mask, SR, subtype="PCM_16")
            segments = _get_transcriber().transcribe_full(wav).get("segments", []) or []
        segments = keep_voiced_segments(segments, regions)

        # Keep only what actually went out over Discord: the recorder hears
        # the mic whether or not the teammate is muted (or holding push-to-
        # talk), and what they say off-comms isn't comms -- nor anyone's
        # business. A line counts if the host's Discord track moves with
        # this teammate's voice at that (aligned) moment -- not merely "was
        # someone talking on Discord then", since someone often was.
        on_discord = [s for s in segments
                      if heard_on_ref(ref, sig, SR, float(s["start"]), float(s["end"]), a) >= HEARD_MIN_CORR]
        info["dropped_off_discord"] = len(segments) - len(on_discord)
        return _utterances(on_discord, clock, "voice", speaker, username, time_map=a.to_ref), info

    # ── 4. Build ──────────────────────────────────────────────────────

    @classmethod
    def build(cls, session_id: str) -> Optional[dict]:
        from analysis.comms_timeline import (build_timeline, drop_echoes, drop_team_duplicates,
                                             transcript_text, ai_summary_text)
        row = cls._get(session_id)
        if not row:
            return None
        meta = json.loads(row["audio_meta_json"] or "{}")
        rounds = json.loads(row["rounds_json"] or "[]")
        host = json.loads(row["host_utterances_json"] or "[]")
        with server_db.get_connection() as conn:
            voice_rows = [dict(r) for r in conn.execute(
                "SELECT username, alignment_json, utterances_json FROM session_voice WHERE session_id=?",
                (session_id,))]
        voice = [u for r in voice_rows for u in json.loads(r["utterances_json"] or "[]")]
        team_all = [u for u in host if u["source"] == "team"]
        own, echoes = drop_echoes([u for u in host if u["source"] == "self"], team_all)
        own_total = sum(1 for u in host if u["source"] == "self")
        mic_hears_discord = bool(own_total and echoes / own_total > 0.3)
        if mic_hears_discord:
            # The two tracks aren't actually separated (each holds everyone),
            # so "you" can't be told apart from the team: use the Discord
            # track alone rather than invent talk-overs between two copies.
            own = []
        team = drop_team_duplicates(team_all, voice)
        speech = own + [u for u in host if u["source"] not in ("self", "team")] + team + voice

        roster = dict(meta.get("roster") or {})
        tracked =([] if mic_hears_discord else [meta.get("self_username", "")]) + [r["username"] for r in voice_rows
                                                     if json.loads(r["alignment_json"] or "{}").get("status") == "aligned"]
        timeline = build_timeline(rounds, speech, float(meta.get("utc_offset_sec", _local_utc_offset())),
                                  names=roster, tracked_usernames=tracked)
        timeline["voice"] = [json.loads(r["alignment_json"] or "{}") for r in voice_rows]
        timeline["audio"] = {"tracks": meta.get("tracks", []),
                             "clip_start_estimated": bool(meta.get("clip_start_estimated")),
                             "echoes_removed": echoes,
                             # Many of your lines also on the Discord track: the tracks aren't separated.
                             "mic_hears_discord": mic_hears_discord}
        cls._upsert(session_id, timeline_json=json.dumps(timeline))
        if row["match_id"] is not None:
            cls._write_match(int(row["match_id"]), transcript_text(timeline), timeline, ai_summary_text(timeline))
        return timeline

    @staticmethod
    def _write_match(match_id: int, text: str, timeline: dict, summary: str) -> None:
        from server.match_db import get_match_repo
        repo = get_match_repo()
        with repo.db.get_connection() as conn:
            old = conn.execute("SELECT processed_segments_json FROM transcripts WHERE match_id=?",
                               (match_id,)).fetchone()
            conn.execute("DELETE FROM transcripts WHERE match_id=?", (match_id,))
            if text:
                conn.execute("INSERT INTO transcripts (match_id, raw_text, processed_segments_json) VALUES (?, ?, ?)",
                             (match_id, text, old[0] if old else None))
            conn.execute("DELETE FROM derived_metrics WHERE match_id=? AND metric_name IN ('comms_timeline','comms_summary')",
                         (match_id,))
            conn.execute("INSERT INTO derived_metrics (match_id, metric_name, metric_value, is_ai_generated, metric_text) "
                         "VALUES (?, 'comms_timeline', ?, 0, ?)",
                         (match_id, float(len(timeline.get("flags") or [])), json.dumps(timeline)))
            if summary:
                conn.execute("INSERT INTO derived_metrics (match_id, metric_name, metric_value, is_ai_generated, metric_text) "
                             "VALUES (?, 'comms_summary', 0, 0, ?)", (match_id, summary))
            conn.commit()

    @classmethod
    def get_timeline(cls, session_id: str) -> Optional[dict]:
        row = cls._get(session_id)
        if not row or not row["timeline_json"]:
            return None
        return json.loads(row["timeline_json"])

    # ── Pending work (worker idle loop) ───────────────────────────────

    @classmethod
    def process_pending(cls) -> bool:
        """Rebuilds one session whose window has teammate audio it hasn't
        folded in yet, then refreshes its AI debrief. True if it did work."""
        with server_db.get_connection() as conn:
            sessions = [dict(r) for r in conn.execute(
                "SELECT session_id, match_id, window_start, window_end, audio_meta_json FROM session_comms "
                "WHERE window_start IS NOT NULL AND rounds_json != '[]' ORDER BY window_start DESC LIMIT 60")]
        now = time.time()
        for s in sessions:
            chunks_by_user = cls.voice_chunks_for_window(s["window_start"], s["window_end"])
            self_user = str(json.loads(s["audio_meta_json"] or "{}").get("self_username") or "").lower()
            for username, chunks in chunks_by_user.items():
                if username.lower() == self_user:
                    continue
                with server_db.get_connection() as conn:
                    prev = conn.execute("SELECT chunks_signature FROM session_voice WHERE session_id=? AND username=?",
                                        (s["session_id"], username)).fetchone()
                if prev and prev["chunks_signature"] == cls._signature(chunks):
                    continue
                newest = max(datetime.fromisoformat(c["uploaded_at"]).timestamp() for c in chunks)
                covers_end = any(c["start_epoch"] + c["duration_sec"] >= s["window_end"] or c["is_final"]
                                 for c in chunks)
                if now - newest < SETTLE_SEC and not covers_end:
                    continue        # more of this recording is probably on its way
                if cls.merge_voice(s["session_id"]):
                    cls._refresh_ai(s["match_id"])
                return True
        return False

    @staticmethod
    def _refresh_ai(match_id: Optional[int]) -> None:
        if match_id is None:
            return
        try:
            from analysis.intel_engine import IntelEngine
            intel = IntelEngine(
                ollama_exe=server_settings.OLLAMA_EXE,
                ollama_models=server_settings.OLLAMA_MODELS_DIR,
                default_model=server_settings.OLLAMA_MODEL,
                db_path=server_settings.MATCHES_DB_PATH,
                schema_path=server_settings.MATCHES_SCHEMA_PATH,
                ollama_options=server_settings.OLLAMA_OPTIONS,
                ollama_url=server_settings.OLLAMA_URL or None,
            )
            intel.analyze_match(match_id)
            print(f"[Comms] match {match_id}: AI debrief refreshed with teammate comms.")
        except Exception as e:
            print(f"[Comms] match {match_id}: AI refresh failed: {e}")

    # ── Storage helpers ───────────────────────────────────────────────

    @staticmethod
    def _get(session_id: str):
        with server_db.get_connection() as conn:
            return conn.execute("SELECT * FROM session_comms WHERE session_id=?", (session_id,)).fetchone()

    @staticmethod
    def _upsert(session_id: str, **fields: Any) -> None:
        fields["updated_at"] = _now()
        cols = ", ".join(fields)
        marks = ", ".join("?" for _ in fields)
        updates = ", ".join(f"{k}=excluded.{k}" for k in fields)
        with server_db.get_connection() as conn:
            conn.execute(
                f"INSERT INTO session_comms (session_id, {cols}) VALUES (?, {marks}) "
                f"ON CONFLICT(session_id) DO UPDATE SET {updates}",
                (session_id, *fields.values()),
            )
            conn.commit()

    # ── Voice chunk storage (API) ─────────────────────────────────────

    @staticmethod
    def _compress_wav(src: Path) -> tuple[Path, str]:
        """The browser recorder sends plain 16 kHz WAV (about 115 MB an hour);
        keep it as Opus like R6Voice does (about a tenth of that). A WAV that
        won't decode isn't audio and is refused; one that merely won't encode
        is kept as it came."""
        import soundfile as sf
        try:
            data, sr = sf.read(str(src), dtype="float32", always_2d=False)
        except Exception as exc:
            raise ValueError(f"That isn't readable audio: {exc}") from exc
        if getattr(data, "ndim", 1) > 1:
            data = data.mean(axis=1)
        if len(data) == 0:
            raise ValueError("That audio chunk is empty.")
        out = src.with_suffix(".ogg")
        try:
            sf.write(str(out), data, sr, format="OGG", subtype="OPUS")
        except Exception as exc:
            print(f"[Comms] Opus encode failed ({exc}); keeping the WAV.")
            out.unlink(missing_ok=True)
            return src, ".wav"
        src.unlink(missing_ok=True)
        return out, ".ogg"

    @staticmethod
    def store_voice_chunk(src: Path, *, recording_id: str, chunk_index: int, username: str,
                          start_epoch: float, duration_sec: float, sample_rate: int,
                          is_final: bool, suffix: str) -> dict:
        if suffix == ".wav":
            src, suffix = CommsService._compress_wav(src)
        sha = hashlib.sha256(src.read_bytes()).hexdigest()
        dest_dir = server_settings.VOICE_DIR / username.lower() / recording_id
        dest_dir.mkdir(parents=True, exist_ok=True)
        dest = dest_dir / f"{chunk_index:04d}{suffix}"
        shutil.move(str(src), dest)
        with server_db.get_connection() as conn:
            conn.execute(
                """INSERT INTO voice_chunks (recording_id, chunk_index, username, start_epoch, duration_sec,
                                             sample_rate, file_path, sha256, is_final, uploaded_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                   ON CONFLICT(recording_id, chunk_index) DO UPDATE SET
                     username=excluded.username, start_epoch=excluded.start_epoch,
                     duration_sec=excluded.duration_sec, sample_rate=excluded.sample_rate,
                     file_path=excluded.file_path, sha256=excluded.sha256, is_final=excluded.is_final,
                     uploaded_at=excluded.uploaded_at""",
                (recording_id, chunk_index, username, start_epoch, duration_sec, sample_rate,
                 str(dest), sha, int(is_final), _now()),
            )
            conn.commit()
        return {"recording_id": recording_id, "chunk_index": chunk_index, "sha256": sha}

    @staticmethod
    def list_voice_recordings(limit: int = 50) -> list[dict]:
        with server_db.get_connection() as conn:
            rows = conn.execute(
                """SELECT recording_id, username, MIN(start_epoch) AS start_epoch,
                          MAX(start_epoch + duration_sec) AS end_epoch, COUNT(*) AS chunks,
                          SUM(duration_sec) AS seconds, MAX(is_final) AS finished, MAX(uploaded_at) AS last_upload
                   FROM voice_chunks GROUP BY recording_id ORDER BY start_epoch DESC LIMIT ?""",
                (limit,),
            ).fetchall()
        return [dict(r) for r in rows]
