"""
Voice recognition on the Discord track. A stand-in embedding model maps each
synthetic "voice" (a distinct tone) to a distinct vector, so these exercise the
learning, matching, refusing and caching logic exactly, without any ML weights.
"""
import hashlib

import numpy as np
import pytest
from fastapi.testclient import TestClient

from server import voice_id
from server.config import server_settings
from server.main import app
from server.services.comms_service import ClipClock

SR = 16000
TONES = {"alice": 220.0, "bob": 330.0, "cara": 480.0}


def voice(name, seconds=2.0):
    t = np.arange(int(SR * seconds)) / SR
    return (np.sin(2 * np.pi * TONES[name] * t) * 0.3).astype(np.float32)


def stand_in(wav):
    spec = np.abs(np.fft.rfft(wav))
    freq = int(np.argmax(spec)) * SR / len(wav)
    v = np.zeros(16)
    v[int(round(freq / 40)) % 16] = 1.0
    return v + np.random.default_rng(len(wav)).normal(0, 0.02, 16)


@pytest.fixture
def db(tmp_path, monkeypatch):
    from server.database import ServerDatabase
    d = ServerDatabase(tmp_path / "server_matches.db")
    monkeypatch.setattr("server.voice_id.server_db", d)
    voice_id.set_encoder(stand_in)
    monkeypatch.delenv("R6_VOICE_ID", raising=False)
    yield d
    voice_id.set_encoder(None)


def discord_track(parts):
    """parts: [(name, seconds)] laid end to end."""
    return np.concatenate([voice(n, s) for n, s in parts])


def learn(name, n=8):
    ref = discord_track([(name, 3.0)] * n)
    segs = [{"start": i * 3.0, "end": i * 3.0 + 2.5} for i in range(n)]
    return voice_id.enroll_from_alignment(ref, lambda t: t, segs, name)


def test_a_voice_is_learned_from_verified_stretches_of_the_discord_track(db):
    assert learn("alice") == 8
    p = voice_id.list_profiles()
    assert p[0]["username"] == "alice" and p[0]["samples"] == 8 and p[0]["usable"] is True


def test_too_little_audio_does_not_make_a_usable_profile(db):
    assert learn("alice", n=3) == 3
    assert voice_id.list_profiles()[0]["usable"] is False
    assert voice_id.load_profiles() == {}


def test_a_clear_match_is_named_and_a_stranger_is_not(db):
    learn("alice")
    learn("bob")
    profiles = voice_id.load_profiles()
    assert voice_id.identify(stand_in(voice("alice")), profiles)[0] == "alice"
    assert voice_id.identify(stand_in(voice("bob")), profiles)[0] == "bob"
    assert voice_id.identify(stand_in(voice("cara")), profiles) is None      # nobody we know


def test_two_profiles_that_sound_alike_are_never_guessed_between():
    a = np.array([1.0, 0.0, 0.0])
    b = np.array([0.97, 0.243, 0.0])
    probe = np.array([0.99, 0.12, 0.0])
    assert voice_id.identify(probe, {"x": a, "y": b / np.linalg.norm(b)}) is None


def test_a_name_can_be_excluded(db):
    learn("alice")
    learn("bob")
    profiles = voice_id.load_profiles()
    assert voice_id.identify(stand_in(voice("alice")), profiles, exclude=["Alice"]) is None


def team_lines(parts):
    out, t = [], 1000.0
    for name, secs in parts:
        out.append({"speaker": "Team (unassigned)", "username": "", "source": "team",
                    "start": t, "end": t + secs, "text": f"{name} talking"})
        t += secs + 0.5
    return out


def test_unassigned_lines_get_the_right_names(db):
    learn("alice")
    learn("bob")
    clock = ClipClock([[1000.0, 60.0]])
    parts = [("alice", 2.0), ("bob", 2.0), ("cara", 2.0), ("alice", 1.5)]
    ref = np.zeros(SR * 60, dtype=np.float32)
    for line, (name, secs) in zip(team_lines(parts), parts):
        a = int((line["start"] - 1000.0) * SR)
        ref[a:a + int(secs * SR)] = voice(name, secs)
    lines, stats = voice_id.label_team_lines(team_lines(parts), ref, clock.offset, "Team (unassigned)",
                                             [], {"alice": "Alice N"})
    assert [l["speaker"] for l in lines] == ["Alice N", "bob", "Team (unassigned)", "Alice N"]
    assert stats == {"profiles": 2, "considered": 4, "labelled": 3}
    assert lines[1]["username"] == "bob" and lines[2]["username"] == ""


def test_lines_of_a_person_with_their_own_recording_are_left_alone(db):
    learn("alice")
    clock = ClipClock([[1000.0, 10.0]])
    ref = np.zeros(SR * 10, dtype=np.float32)
    ref[:SR * 2] = voice("alice", 2.0)
    lines, stats = voice_id.label_team_lines(team_lines([("alice", 2.0)]), ref, clock.offset,
                                             "Team (unassigned)", ["alice"], {})
    assert lines[0]["speaker"] == "Team (unassigned)" and stats["labelled"] == 0


def test_short_blips_stay_unassigned(db):
    learn("alice")
    clock = ClipClock([[1000.0, 10.0]])
    ref = np.zeros(SR * 10, dtype=np.float32)
    ref[:SR] = voice("alice", 1.0)
    lines, stats = voice_id.label_team_lines(team_lines([("alice", 0.6)]), ref, clock.offset,
                                             "Team (unassigned)", [], {})
    assert stats["considered"] == 0 and lines[0]["speaker"] == "Team (unassigned)"


def test_voiceprints_are_cached_so_a_rebuild_does_not_recompute(db, tmp_path):
    learn("alice")
    calls = []

    def counting(wav):
        calls.append(1)
        return stand_in(wav)

    voice_id.set_encoder(counting)
    clock = ClipClock([[1000.0, 10.0]])
    ref = np.zeros(SR * 10, dtype=np.float32)
    ref[:SR * 2] = voice("alice", 2.0)
    cache = tmp_path / "emb.json"
    voice_id.label_team_lines(team_lines([("alice", 2.0)]), ref, clock.offset, "Team (unassigned)", [], {}, cache)
    first = len(calls)
    lines, _ = voice_id.label_team_lines(team_lines([("alice", 2.0)]), ref, clock.offset, "Team (unassigned)", [], {}, cache)
    assert first == 1 and len(calls) == first and lines[0]["username"] == "alice"


def test_it_can_be_switched_off(db, monkeypatch):
    learn("alice")
    monkeypatch.setenv("R6_VOICE_ID", "0")
    ref = np.zeros(SR * 10, dtype=np.float32)
    ref[:SR * 2] = voice("alice", 2.0)
    lines, stats = voice_id.label_team_lines(team_lines([("alice", 2.0)]), ref, ClipClock([[1000.0, 10.0]]).offset,
                                             "Team (unassigned)", [], {})
    assert lines[0]["speaker"] == "Team (unassigned)" and stats["labelled"] == 0
    assert voice_id.enroll_from_alignment(ref, lambda t: t, [{"start": 0, "end": 2}], "alice") == 0


def test_the_running_mean_follows_a_voice_over_sessions(db):
    voice_id.enroll("alice", [np.array([1.0, 0.0])] * 5, 10.0)
    voice_id.enroll("alice", [np.array([0.0, 1.0])] * 5, 10.0)
    p = voice_id.load_profiles()["alice"]
    assert p[0] == pytest.approx(p[1], abs=1e-6)         # an even blend of the two sessions
    assert voice_id.list_profiles()[0]["samples"] == 10


def test_clipclock_offset_inverts_epoch_across_pieces():
    c = ClipClock([[1000.0, 60.0], [5000.0, 30.0]])
    for t in (0.0, 59.0, 60.5, 80.0):
        assert c.offset(c.epoch(t)) == pytest.approx(t)


# ── API ──────────────────────────────────────────────────────────────────

def _h(token):
    return hashlib.sha256(token.encode()).hexdigest()


@pytest.fixture
def client(tmp_path, monkeypatch):
    d = tmp_path / "server_data"
    for attr, path in (("DATA_DIR", d), ("DATABASE_PATH", d / "server_matches.db"), ("UPLOADS_DIR", d / "uploads"),
                       ("WORK_DIR", d / "work"), ("REPORTS_DIR", d / "reports"), ("LOGS_DIR", d / "logs"),
                       ("VOICE_DIR", d / "voice"), ("COMMS_DIR", d / "comms")):
        monkeypatch.setattr(server_settings, attr, path)
    monkeypatch.setattr(server_settings, "API_TOKEN_HASH", _h("main-token"))
    monkeypatch.setattr(server_settings, "VOICE_TOKEN_HASH", _h("voice-token"))
    server_settings.ensure_directories()
    from server.database import ServerDatabase
    from server.repositories import ServerRepository
    db = ServerDatabase(d / "server_matches.db")
    for target in ("server.repositories.server_db", "server.services.comms_service.server_db",
                   "server.invites.server_db", "server.roster.server_db", "server.voice_id.server_db"):
        monkeypatch.setattr(target, db)
    monkeypatch.setattr("server.api.v1.repo", ServerRepository(db))
    with TestClient(app) as c:
        yield c


def test_the_host_can_see_and_forget_learned_voices(client):
    auth = {"Authorization": "Bearer main-token"}
    voice_id.enroll("alice", [np.array([1.0, 0.0])] * 6, 12.0)
    got = client.get("/api/v1/voices", headers=auth).json()
    assert got["enabled"] is True and got["profiles"][0]["username"] == "alice" and got["profiles"][0]["usable"]
    assert client.delete("/api/v1/voices/alice", headers=auth).status_code == 200
    assert client.get("/api/v1/voices", headers=auth).json()["profiles"] == []
    assert client.delete("/api/v1/voices/alice", headers=auth).status_code == 404


def test_teammates_cannot_see_or_erase_voices(client):
    for token in ("voice-token", "wrong"):
        h = {"Authorization": f"Bearer {token}"}
        assert client.get("/api/v1/voices", headers=h).status_code in (401, 403)
        assert client.delete("/api/v1/voices/alice", headers=h).status_code in (401, 403)
