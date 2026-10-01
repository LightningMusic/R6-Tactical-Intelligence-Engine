"""
Teammates' R6Voice recordings, end to end on the server: upload through the
API with the narrow voice key, then CommsService stitches the chunks,
lines them up against the host's Discord track, keeps only what actually
went out over Discord, and labels every line with the teammate's name.
Whisper is replaced by a stand-in that "transcribes" each burst of sound.
"""
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pytest
import soundfile as sf
from fastapi.testclient import TestClient

from server.config import server_settings
from server.main import app
from server.services import comms_service as cs
from tests.test_voice_align import SR, talking, through_discord

API = "main-token"
VOICE = "voice-token"


def _h(token):
    return hashlib.sha256(token.encode()).hexdigest()


@pytest.fixture
def server(tmp_path, monkeypatch):
    d = tmp_path / "server_data"
    for attr, path in (("DATA_DIR", d), ("DATABASE_PATH", d / "server_matches.db"), ("UPLOADS_DIR", d / "uploads"),
                       ("WORK_DIR", d / "work"), ("REPORTS_DIR", d / "reports"), ("LOGS_DIR", d / "logs"),
                       ("VOICE_DIR", d / "voice"), ("COMMS_DIR", d / "comms")):
        monkeypatch.setattr(server_settings, attr, path)
    monkeypatch.setattr(server_settings, "API_TOKEN_HASH", _h(API))
    monkeypatch.setattr(server_settings, "VOICE_TOKEN_HASH", _h(VOICE))
    server_settings.ensure_directories()
    from server.database import ServerDatabase
    from server.repositories import ServerRepository
    db = ServerDatabase(d / "server_matches.db")
    monkeypatch.setattr("server.repositories.server_db", db)
    monkeypatch.setattr("server.services.comms_service.server_db", db)
    monkeypatch.setattr("server.api.v1.repo", ServerRepository(db))
    with TestClient(app) as client:
        yield client, db


def _ogg(path: Path, x: np.ndarray) -> Path:
    sf.write(path, x, SR, format="OGG", subtype="OPUS")
    return path


def _upload(client, path, token=VOICE, **fields):
    data = {"recording_id": "a" * 32, "chunk_index": 0, "username": "lammtozzz", "start_epoch": 1790700000.0,
            "duration_sec": 300, "sample_rate": SR, "is_final": False}
    data.update(fields)
    with path.open("rb") as fh:
        return client.post("/api/v1/voice/chunks", headers={"Authorization": f"Bearer {token}"},
                           files={"file": (path.name, fh, "audio/ogg")},
                           data={k: str(v) for k, v in data.items()})


def test_voice_upload_accepts_the_voice_key_and_nothing_else(server, tmp_path):
    client, db = server
    f = _ogg(tmp_path / "c.ogg", talking(5, seed=1))
    assert _upload(client, f).status_code == 200
    assert _upload(client, f, token=API, chunk_index=1).status_code == 200      # main key works too
    assert _upload(client, f, token="wrong", chunk_index=2).status_code == 401
    assert _upload(client, f, username="no spaces allowed!").status_code == 400
    assert _upload(client, f, start_epoch=1000.0).status_code == 400            # PC clock nonsense
    with db.get_connection() as conn:
        rows = conn.execute("SELECT chunk_index, file_path FROM voice_chunks ORDER BY chunk_index").fetchall()
    assert [r["chunk_index"] for r in rows] == [0, 1] and all(Path(r["file_path"]).exists() for r in rows)
    # The voice key can upload but can't read anything back.
    assert client.get("/api/v1/voice/recordings", headers={"Authorization": f"Bearer {VOICE}"}).status_code == 401
    recs = client.get("/api/v1/voice/recordings", headers={"Authorization": f"Bearer {API}"}).json()["recordings"]
    assert recs[0]["username"] == "lammtozzz" and recs[0]["chunks"] == 2
    # Re-sending the same chunk (dropped connection) is harmless.
    assert _upload(client, f).status_code == 200


def test_voice_key_is_created_alongside_a_self_provisioned_server(tmp_path, monkeypatch):
    from server.config import ServerSettings
    for var in ("R6_SERVER_API_TOKEN", "R6_SERVER_API_TOKEN_HASH", "R6_SERVER_VOICE_TOKEN"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("R6_SERVER_DATA_DIR", str(tmp_path / "a"))
    s = ServerSettings()
    saved = json.loads(s.CONFIG_FILE.read_text())
    assert s.VOICE_TOKEN_PLAINTEXT and saved["voice_token"] == s.VOICE_TOKEN_PLAINTEXT
    assert saved["voice_token"] != saved["api_token"]
    assert ServerSettings().VOICE_TOKEN_PLAINTEXT == s.VOICE_TOKEN_PLAINTEXT      # stable across restarts

    monkeypatch.setenv("R6_SERVER_DATA_DIR", str(tmp_path / "b"))
    monkeypatch.setenv("R6_SERVER_API_TOKEN", "env-token")
    env = ServerSettings()
    assert env.VOICE_TOKEN_PLAINTEXT is None and not env.CONFIG_FILE.exists()
    monkeypatch.setenv("R6_SERVER_VOICE_TOKEN", "env-voice")
    assert ServerSettings().VOICE_TOKEN_PLAINTEXT == "env-voice"


class FakeWhisper:
    """One segment per burst of sound in the file."""

    def transcribe_full(self, path):
        from analysis.voice_align import speech_regions
        x, sr = sf.read(path, dtype="float32")
        return {"segments": [{"start": a, "end": b, "text": f"line at {a:.1f}"}
                             for a, b in speech_regions(x, sr)]}


def test_teammate_recording_is_aligned_labelled_and_filtered(server, tmp_path, monkeypatch):
    client, db = server
    monkeypatch.setattr(cs, "_get_transcriber", lambda: FakeWhisper())
    monkeypatch.setattr(cs, "_load_audio", lambda p: sf.read(p, dtype="float32")[0])

    ws, length = 1790700000.0, 600.0
    discord_delay, clock_error = 0.4, 1.3          # teammate's PC clock runs 1.3 s fast
    own = talking(length, seed=11)
    ref = through_discord(own, discord_delay, 0.0, seed=12)
    # Something said while muted in Discord, in one of their pauses: on
    # their mic, not in the Discord track.
    from analysis.voice_align import speech_regions
    pauses = [(b, c) for (_, b), (c, _) in zip(speech_regions(own, SR), speech_regions(own, SR)[1:])
              if c - b > 3.0 and 200 < b < 400]
    muted_at = pauses[0][0] + 0.5
    muted = np.zeros_like(own)
    muted[int(muted_at * SR):int((muted_at + 1.8) * SR)] = np.random.default_rng(3).standard_normal(int(1.8 * SR)) * 0.3
    own_mic = own + muted

    sid = "session_test"
    (server_settings.COMMS_DIR / sid).mkdir(parents=True)
    sf.write(server_settings.COMMS_DIR / sid / "team.wav", ref, SR)
    stamp = datetime.fromtimestamp(ws + 30, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    cs.CommsService._upsert(
        sid,
        audio_meta_json=json.dumps({"pieces": [[ws, length]], "utc_offset_sec": 0,
                                    "roster": {"lammtozzz": "Zander", "lightningmusic6": "Elijah"},
                                    "self_username": "LightningMusic6", "tracks": ["self", "team"]}),
        rounds_json=json.dumps([{"round_number": 1, "timestamp": stamp, "recording_username": "LightningMusic6",
                                 "ours": ["LightningMusic6", "lammtozzz"], "theirs": ["X"], "events": []}]),
        host_utterances_json="[]", window_start=ws, window_end=ws + length,
    )

    # Two 5-minute chunks, stamped with the teammate's (fast) clock.
    half = int(300 * SR)
    for i, part in enumerate((own_mic[:half], own_mic[half:])):
        f = _ogg(tmp_path / f"{i}.ogg", part)
        r = _upload(client, f, chunk_index=i, start_epoch=ws + clock_error + i * 300.0,
                    duration_sec=300.0, is_final=(i == 1))
        assert r.status_code == 200, r.text

    assert cs.CommsService.process_pending() is True
    with db.get_connection() as conn:
        row = conn.execute("SELECT alignment_json, utterances_json FROM session_voice").fetchone()
    info = json.loads(row["alignment_json"])
    utts = json.loads(row["utterances_json"])
    assert info["status"] == "aligned"
    assert info["offset_sec"] == pytest.approx(discord_delay - clock_error, abs=0.08)
    assert info["dropped_off_discord"] >= 1
    assert utts and all(u["speaker"] == "Zander" and u["source"] == "voice" for u in utts)
    # Placed where Discord heard them. The fake transcript names each line's
    # position in the teammate's audio as laid out by their own clock; the
    # alignment moves it by (Discord delay - clock error) onto the host's.
    first = min(utts, key=lambda u: u["start"])
    at_their_clock = float(first["text"].split()[-1])
    assert first["start"] == pytest.approx(ws + at_their_clock + (discord_delay - clock_error), abs=0.1)
    muted_on_host_clock = ws + clock_error + muted_at + (discord_delay - clock_error)
    assert not any(abs(u["start"] - muted_on_host_clock) < 1.0 for u in utts)      # the muted line is gone

    tl = cs.CommsService.get_timeline(sid)
    assert "Zander" in {s["speaker"] for s in tl["speakers"]}
    assert tl["voice"][0]["status"] == "aligned"
    # Nothing new since: a second pass has nothing to do.
    assert cs.CommsService.process_pending() is False
