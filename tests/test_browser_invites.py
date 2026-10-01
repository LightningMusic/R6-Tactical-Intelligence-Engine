"""
Browser-recorder invite links: what a teammate's link can and cannot do.

An invite is the weakest credential on the server -- one in-game name, upload
and check-in only, expiring, revocable -- so most of these tests are about
what it is refused.
"""
import hashlib
import io
import time
import wave
from pathlib import Path

import numpy as np
import pytest
from fastapi.testclient import TestClient

from server import invites
from server.config import server_settings
from server.main import app

API = "main-token"
VOICE = "voice-token"
SR = 16000


def _h(token):
    return hashlib.sha256(token.encode()).hexdigest()


def _auth(token):
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
def server(tmp_path, monkeypatch):
    d = tmp_path / "server_data"
    for attr, path in (("DATA_DIR", d), ("DATABASE_PATH", d / "server_matches.db"), ("UPLOADS_DIR", d / "uploads"),
                       ("WORK_DIR", d / "work"), ("REPORTS_DIR", d / "reports"), ("LOGS_DIR", d / "logs"),
                       ("VOICE_DIR", d / "voice"), ("COMMS_DIR", d / "comms")):
        monkeypatch.setattr(server_settings, attr, path)
    monkeypatch.setattr(server_settings, "API_TOKEN_HASH", _h(API))
    monkeypatch.setattr(server_settings, "VOICE_TOKEN_HASH", _h(VOICE))
    monkeypatch.setattr(server_settings, "PUBLIC_URL", "https://example.test")
    server_settings.ensure_directories()
    from server.database import ServerDatabase
    from server.repositories import ServerRepository
    db = ServerDatabase(d / "server_matches.db")
    monkeypatch.setattr("server.repositories.server_db", db)
    monkeypatch.setattr("server.services.comms_service.server_db", db)
    monkeypatch.setattr("server.invites.server_db", db)
    monkeypatch.setattr("server.api.v1.repo", ServerRepository(db))
    with TestClient(app) as client:
        yield client, db


def _invite(client, username="Teddy_Dance", **extra):
    r = client.post("/api/v1/invites", headers=_auth(API), json={"username": username, **extra})
    assert r.status_code == 200, r.text
    return r.json()


def _wav_bytes(seconds=2.0, freq=220.0) -> bytes:
    t = np.arange(int(SR * seconds)) / SR
    pcm = (np.sin(2 * np.pi * freq * t) * 12000).astype("<i2")
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(SR)
        w.writeframes(pcm.tobytes())
    return buf.getvalue()


def _upload(client, token, body=None, username="Teddy_Dance", chunk_index=0, filename="c.wav", **fields):
    data = {"recording_id": "b" * 32, "chunk_index": chunk_index, "username": username,
            "start_epoch": time.time() - 30, "duration_sec": 2, "sample_rate": SR, "is_final": False}
    data.update(fields)
    return client.post("/api/v1/voice/chunks", headers=_auth(token),
                       files={"file": (filename, body if body is not None else _wav_bytes(), "audio/wav")},
                       data={k: str(v) for k, v in data.items()})


def test_host_creates_lists_and_the_token_is_never_stored(server):
    client, db = server
    made = _invite(client, label="James")
    assert made["token"].startswith("inv_") and made["join_url"] == f"https://example.test/join#{made['token']}"
    listed = client.get("/api/v1/invites", headers=_auth(API)).json()["invites"]
    assert [i["username"] for i in listed] == ["Teddy_Dance"] and listed[0]["label"] == "James"
    assert "token" not in listed[0] and "token_hash" not in listed[0]
    with db.get_connection() as conn:
        stored = conn.execute("SELECT token_hash FROM invites").fetchone()["token_hash"]
    assert stored == _h(made["token"]) and made["token"] not in stored


def test_only_the_main_key_manages_invites(server):
    client, _ = server
    body = {"username": "Teddy_Dance"}
    assert client.post("/api/v1/invites", headers=_auth(VOICE), json=body).status_code == 401
    assert client.post("/api/v1/invites", json=body).status_code == 401
    made = _invite(client)
    for call in (lambda t: client.get("/api/v1/invites", headers=_auth(t)),
                 lambda t: client.post("/api/v1/invites", headers=_auth(t), json=body),
                 lambda t: client.post(f"/api/v1/invites/{made['invite_id']}/regenerate", headers=_auth(t)),
                 lambda t: client.delete(f"/api/v1/invites/{made['invite_id']}", headers=_auth(t))):
        assert call(made["token"]).status_code == 401      # an invite can't mint or revoke invites
        assert call(VOICE).status_code == 401
    assert client.post("/api/v1/invites", headers=_auth(API), json={"username": "no spaces!"}).status_code == 400


def test_invite_whoami_and_upload_for_its_own_player(server):
    client, db = server
    token = _invite(client)["token"]
    who = client.get("/api/v1/join/whoami", headers=_auth(token)).json()
    assert who["kind"] == "invite" and who["username"] == "Teddy_Dance" and abs(who["server_time"] - time.time()) < 5
    assert _upload(client, token).status_code == 200
    assert _upload(client, token, username="teddy_dance", chunk_index=1).status_code == 200   # case-insensitive
    with db.get_connection() as conn:
        rows = conn.execute("SELECT username, file_path FROM voice_chunks ORDER BY chunk_index").fetchall()
    assert [r["username"] for r in rows] == ["Teddy_Dance", "Teddy_Dance"]
    # WAV is stored as Opus, not as 115 MB an hour of PCM.
    assert all(Path(r["file_path"]).suffix == ".ogg" and Path(r["file_path"]).stat().st_size < 30_000 for r in rows)
    assert not list(server_settings.VOICE_DIR.glob("*.wav")) and not list(server_settings.VOICE_DIR.glob("*.ogg"))


def test_invite_cannot_speak_for_someone_else(server):
    client, db = server
    token = _invite(client)["token"]
    assert _upload(client, token, username="lammtozzz").status_code == 403
    with db.get_connection() as conn:
        assert conn.execute("SELECT COUNT(*) AS n FROM voice_chunks").fetchone()["n"] == 0


def test_invite_can_read_nothing(server):
    client, _ = server
    token = _invite(client)["token"]
    for path in ("/api/v1/sessions", "/api/v1/voice/recordings", "/api/v1/companion/status",
                 "/api/v1/auth/test", "/api/v1/sessions/abc/comms"):
        assert client.get(path, headers=_auth(token)).status_code == 401, path
    r = client.put("/api/v1/companion/control", headers=_auth(token), json={"recording": True})
    assert r.status_code == 401
    r = client.post("/api/v1/sessions/upload", headers=_auth(token))
    assert r.status_code in (401, 422)


def test_revoked_expired_and_regenerated_links_stop_working(server):
    client, db = server
    a = _invite(client)
    assert client.get("/api/v1/join/whoami", headers=_auth(a["token"])).status_code == 200

    assert client.delete(f"/api/v1/invites/{a['invite_id']}", headers=_auth(API)).status_code == 200
    assert client.get("/api/v1/join/whoami", headers=_auth(a["token"])).status_code == 401
    assert _upload(client, a["token"]).status_code == 401
    assert client.delete(f"/api/v1/invites/{a['invite_id']}", headers=_auth(API)).status_code == 404

    b = _invite(client, username="lammtozzz")
    new = client.post(f"/api/v1/invites/{b['invite_id']}/regenerate", headers=_auth(API)).json()
    assert new["invite_id"] == b["invite_id"] and new["token"] != b["token"]
    assert client.get("/api/v1/join/whoami", headers=_auth(b["token"])).status_code == 401
    assert client.get("/api/v1/join/whoami", headers=_auth(new["token"])).json()["username"] == "lammtozzz"
    # Regenerating a revoked invite brings it back with a fresh link.
    back = client.post(f"/api/v1/invites/{a['invite_id']}/regenerate", headers=_auth(API)).json()
    assert client.get("/api/v1/join/whoami", headers=_auth(back["token"])).status_code == 200
    assert client.post("/api/v1/invites/deadbeef/regenerate", headers=_auth(API)).status_code == 404

    with db.get_connection() as conn:
        conn.execute("UPDATE invites SET expires_at = ? WHERE invite_id = ?", (time.time() - 1, b["invite_id"]))
        conn.commit()
    assert client.get("/api/v1/join/whoami", headers=_auth(new["token"])).status_code == 401
    listed = {i["invite_id"]: i for i in client.get("/api/v1/invites", headers=_auth(API)).json()["invites"]}
    assert listed[b["invite_id"]]["expired"] is True


def test_malformed_and_forged_invite_tokens_are_rejected(server):
    client, _ = server
    made = _invite(client)
    forged = f"inv_{made['invite_id']}_" + "x" * 32
    for bad in ("inv_", "inv_zz_abc", "inv_deadbeef_abc", forged, made["token"] + "x", made["token"][:-1]):
        assert client.get("/api/v1/join/whoami", headers=_auth(bad)).status_code == 401, bad
    assert invites.authenticate("") is None and invites.authenticate("not-an-invite") is None


def test_garbage_audio_is_refused_not_stored(server):
    client, db = server
    token = _invite(client)["token"]
    assert _upload(client, token, body=b"this is not a wav file at all").status_code == 400
    assert _upload(client, token, filename="c.exe").status_code == 400
    with db.get_connection() as conn:
        assert conn.execute("SELECT COUNT(*) AS n FROM voice_chunks").fetchone()["n"] == 0
    assert not any(p.is_file() for p in server_settings.VOICE_DIR.rglob("*"))


def test_invite_has_a_daily_recording_cap(server, monkeypatch):
    client, _ = server
    monkeypatch.setattr("server.api.v1._INVITE_MAX_SEC_PER_DAY", 5)
    token = _invite(client)["token"]
    assert _upload(client, token, duration_sec=2).status_code == 200
    assert _upload(client, token, duration_sec=2, chunk_index=1).status_code == 200
    assert _upload(client, token, duration_sec=2, chunk_index=2).status_code == 429
    # The cap is per player, and the host's own keys aren't limited by it.
    other = _invite(client, username="lammtozzz")["token"]
    assert _upload(client, other, username="lammtozzz", duration_sec=2).status_code == 200
    assert _upload(client, VOICE, duration_sec=2, chunk_index=3).status_code == 200


def test_invite_heartbeat_is_forced_to_its_own_player_and_marked_browser(server):
    client, _ = server
    a, b = _invite(client), _invite(client, username="lammtozzz")
    hb = lambda tok, **body: client.post("/api/v1/companion/heartbeat", headers=_auth(tok), json=body)
    # Claims to be someone else, claims to be a USB companion: neither sticks.
    assert hb(a["token"], device_id="same", username="lammtozzz", status={"kind": "usb", "recording": True}).status_code == 200
    assert hb(b["token"], device_id="same", username="lammtozzz", status={"recording": False}).status_code == 200
    comps = client.get("/api/v1/companion/status", headers=_auth(API)).json()["companions"]
    by_user = {c["username"]: c for c in comps}
    assert set(by_user) == {"Teddy_Dance", "lammtozzz"}                  # same device id, two separate rows
    assert all(c["status"]["kind"] == "browser" for c in comps)
    assert by_user["Teddy_Dance"]["status"]["recording"] is True
    assert all(c["device_id"].startswith("web-") and len(c["device_id"]) <= 64 for c in comps)
    # The host's recording state comes back to the browser.
    client.put("/api/v1/companion/control", headers=_auth(API), json={"recording": True})
    assert hb(a["token"], device_id="same").json()["control"]["recording"] is True


def test_host_log_describes_browser_recorders():
    from app.companion_link import CompanionLink
    now = 1_000_000.0

    def line(seen=2, **status):
        return CompanionLink.describe({"username": "Teddy_Dance", "seconds_since_seen": seen,
                                       "status": {"kind": "browser", **status}}, now=now)

    assert "browser recorder not checking in" in line(seen=300)
    assert "recording ✓ 3 min" in line(recording=True, since=now - 200, started=True, mic_ok=True)
    assert "mic looks silent" in line(recording=True, since=now - 60, started=True, mic_ok=False)
    assert "mic paused" in line(paused=True, started=True)
    assert "isn't started" in line(started=False)
    assert "ready, waiting" in line(started=True, mic_ok=True)
    assert "uploading (4 chunk(s) waiting)" in line(started=True, uploads_waiting=4)
    # A USB companion's wording is unchanged.
    usb = CompanionLink.describe({"username": "x", "seconds_since_seen": 2, "status": {"obs": "OBS not running"}}, now=now)
    assert usb == "x: not recording -- OBS not running"


def test_join_page_is_served_with_locked_down_headers(server):
    client, _ = server
    r = client.get("/join")
    assert r.status_code == 200 and "text/html" in r.headers["content-type"]
    assert r.headers["cache-control"] == "no-store" and r.headers["referrer-policy"] == "no-referrer"
    assert "microphone=(self)" in r.headers["permissions-policy"]
    csp = r.headers["content-security-policy"]
    assert "default-src 'none'" in csp and "connect-src 'self'" in csp and "frame-ancestors 'none'" in csp
    assert "https://" not in r.text.replace("https://example.test", "")   # no third-party loads at all
