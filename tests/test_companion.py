"""
R6Companion: the server relay (host sets the team state, companions check in
and follow it), the companion's own decision loop against a fake OBS and a
fake server, and the host app's status lines. The real-OBS path was also run
on 2026-09-30 against a portable OBS 32.1.1: launched, set up and recording
in 6 s; a 15 s recording came out as one mic track plus 2 kb/s video.
"""
import hashlib
import sys
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from server.config import server_settings
from server.main import app

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "companion"))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "voice_recorder"))

API, VOICE = "main-token", "voice-token"


def _h(t):
    return hashlib.sha256(t.encode()).hexdigest()


@pytest.fixture
def client(tmp_path, monkeypatch):
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
    monkeypatch.setattr("server.api.v1.repo", ServerRepository(db))
    with TestClient(app) as c:
        yield c, db


def auth(t):
    return {"Authorization": f"Bearer {t}"}


def beat(c, username="Teddy_Dance", device="dev1", **status):
    return c.post("/api/v1/companion/heartbeat", headers=auth(VOICE),
                  json={"device_id": device, "username": username, "status": status})


def test_relay_host_sets_state_companions_follow(client):
    c, _ = client
    assert beat(c).json()["control"]["recording"] is False
    r = c.put("/api/v1/companion/control", headers=auth(API), json={"recording": True})
    assert r.status_code == 200
    first_change = r.json()["control"]["changed_at"]
    assert beat(c, recording=True, since=time.time()).json()["control"]["recording"] is True
    # Re-sending "recording" (the host's once-a-minute keepalive) isn't a change.
    time.sleep(0.01)
    assert c.put("/api/v1/companion/control", headers=auth(API),
                 json={"recording": True}).json()["control"]["changed_at"] == first_change
    st = c.get("/api/v1/companion/status", headers=auth(API)).json()
    assert st["control"]["recording"] is True
    assert [(x["username"], x["status"]["recording"]) for x in st["companions"]] == [("Teddy_Dance", True)]


def test_relay_permissions(client):
    c, _ = client
    # The voice key (what companions carry) can check in but not drive the team or read status.
    assert c.put("/api/v1/companion/control", headers=auth(VOICE), json={"recording": True}).status_code == 401
    assert c.get("/api/v1/companion/status", headers=auth(VOICE)).status_code == 401
    assert c.post("/api/v1/companion/heartbeat", headers=auth("nope"),
                  json={"device_id": "d", "username": "Teddy_Dance"}).status_code == 401
    assert beat(c, username="has spaces!").status_code == 400


def test_companions_stop_if_the_host_app_goes_silent(client):
    c, db = client
    c.put("/api/v1/companion/control", headers=auth(API), json={"recording": True})
    with db.get_connection() as conn:
        conn.execute("UPDATE companion_control SET host_seen = ?", (time.time() - 21 * 60,))
        conn.commit()
    assert beat(c).json()["control"]["recording"] is False


# ── The companion's own loop ─────────────────────────────────────────────

class FakeObs:
    def __init__(self):
        self.ws, self.rec, self.starts, self.stops, self.fail_connect = None, False, 0, 0, False
        self.last_error, self.mic_device, self.set_up = "", "default", False

    def running(self):
        return self.ws is not None

    def connect(self):
        if self.fail_connect:
            self.last_error = "OBS not on the stick"
            return False
        self.ws = object()
        return True

    def is_recording(self):
        return self.rec

    def start(self):
        self.rec, self.starts = True, self.starts + 1
        return True

    def stop(self):
        self.rec, self.stops = False, self.stops + 1
        return None

    def drop(self):
        self.ws = None

    def quit(self):
        self.ws = None


class FakeUploader:
    sent = 0

    def waiting(self):
        return 0

    def enqueue_file(self, *a, **k):
        pass


@pytest.fixture
def comp(tmp_path, monkeypatch):
    import core
    monkeypatch.setattr(core.threading, "Thread", lambda target, daemon=True, **k: type(
        "T", (), {"start": lambda self: None})())         # no background export in these tests
    c = core.Companion(tmp_path, lambda: {"server_url": "https://srv", "voice_token": "vk"},
                       uploader=FakeUploader(), obs=FakeObs(), log=lambda m: None)
    c.save_settings(username="Teddy_Dance")
    return c


def server_says(monkeypatch, comp, recording=None, changed_at=1.0, down=False):
    import core

    class R:
        def raise_for_status(self):
            pass

        def json(self):
            return {"control": {"recording": recording, "changed_at": changed_at}}

    def post(*a, **k):
        if down:
            raise ConnectionError("no network")
        return R()

    monkeypatch.setattr(core.requests, "post", post)
    comp.heartbeat()


def test_follows_the_team_and_keeps_recording_through_a_network_drop(comp, monkeypatch):
    server_says(monkeypatch, comp, recording=True)
    comp.tick()
    assert comp.recording and comp.obs.starts == 1
    server_says(monkeypatch, comp, down=True)          # school wifi blip
    comp.tick()
    assert comp.recording and comp.server_ok is False and comp.obs.stops == 0
    server_says(monkeypatch, comp, recording=False, changed_at=2.0)
    comp.tick()
    assert not comp.recording and comp.obs.stops == 1


def test_restarts_obs_recording_if_it_stops_on_its_own(comp, monkeypatch):
    server_says(monkeypatch, comp, recording=True)
    comp.tick()
    comp.obs.rec = False                               # OBS stopped by itself
    comp.tick()
    assert comp.recording and comp.obs.starts == 2


def test_manual_override_holds_until_the_host_changes_state(comp, monkeypatch):
    server_says(monkeypatch, comp, recording=False, changed_at=1.0)
    comp.set_manual(True)                              # James presses Start now
    comp.tick()
    server_says(monkeypatch, comp, recording=False, changed_at=1.0)
    comp.tick()
    assert comp.recording and comp.manual is True      # host hasn't changed anything: keep going
    server_says(monkeypatch, comp, recording=True, changed_at=5.0)
    assert comp.manual is None                         # host started a session: follow it again
    server_says(monkeypatch, comp, recording=False, changed_at=9.0)
    comp.tick()
    assert not comp.recording


def test_reports_obs_problems(comp, monkeypatch):
    comp.obs.fail_connect = True
    server_says(monkeypatch, comp, recording=True)
    comp.tick()
    assert not comp.recording and "OBS" in comp.obs_status


class SplitRecording:
    """A fake OBS split recording on disk: file name = second it was opened,
    content = seconds of audio (the first file's audio starts ~3 s late)."""

    def __init__(self, comp, monkeypatch):
        import core
        self.comp, self.lengths, self.open = comp, {}, set()
        self.queued = []
        monkeypatch.setattr(core, "export_piece", self.piece)
        monkeypatch.setattr(core, "in_use", lambda f: f.name in self.open)
        comp.uploader.enqueue_file = lambda path, **m: self.queued.append(m)

    def add(self, stamp: str, seconds: float, still_writing=False):
        f = self.comp.rec_dir / f"2026-09-30 {stamp}.mp4"
        f.write_bytes(b"x" * 2048)
        self.lengths[f.name] = seconds
        if still_writing:
            self.open.add(f.name)
        return f

    def piece(self, ffmpeg, recording, out, start, max_sec):
        dur = max(0.0, min(max_sec, self.lengths[recording.name] - start))
        if dur > 0:
            out.write_bytes(b"ogg")
        return dur


def epoch(stamp):
    return time.mktime(time.strptime(f"2026-09-30 {stamp}", "%Y-%m-%d %H-%M-%S"))


def test_split_files_go_up_as_they_close_as_one_gapless_recording(comp, monkeypatch):
    """Mirrors the real run on James's FAT32 stick (2026-09-30): first file
    opened 18-00-00 but holding only 297 s (audio starts ~3 s late), then
    exact 5-minute splits."""
    rec = SplitRecording(comp, monkeypatch)
    rec.add("18-00-00", 297.0)
    rec.add("18-05-00", 300.0)
    rec.add("18-10-00", 120.0, still_writing=True)          # OBS is on this one right now
    comp.recording = True
    assert comp.export_all() == 2                           # the two closed files, not the open one
    starts = [q["start_epoch"] for q in rec.queued]
    assert starts == [epoch("18-05-00") - 297.0, epoch("18-05-00")]   # first file anchored on the split
    assert len({q["recording_id"] for q in rec.queued}) == 1
    assert [q["chunk_index"] for q in rec.queued] == [0, 1]

    rec.open.clear()                                        # host stopped: OBS closed the last file
    comp.recording = False
    comp._note_stop(epoch("18-10-00") + 120.2)
    assert comp.export_all() == 1
    last = rec.queued[-1]
    assert last["start_epoch"] == epoch("18-10-00") and last["chunk_index"] == 2 and last["is_final"]
    assert comp.export_all() == 0                           # never twice


def test_a_recording_stopped_before_any_split_is_anchored_on_the_stop(comp, monkeypatch):
    rec = SplitRecording(comp, monkeypatch)
    rec.add("18-00-00", 200.0)
    comp._note_stop(epoch("18-00-00") + 203.1)              # stopped 203 s after the file was opened
    comp.export_all()
    assert rec.queued[0]["start_epoch"] == pytest.approx(epoch("18-00-00") + 3.1)


def test_leftovers_after_the_pc_was_switched_off_go_up_next_launch(comp, monkeypatch):
    rec = SplitRecording(comp, monkeypatch)
    rec.add("18-00-00", 297.0)
    rec.add("18-05-00", 41.0)                               # cut short by the power button, no stop recorded
    comp.export_all()                                       # (the next launch)
    assert [q["chunk_index"] for q in rec.queued] == [0, 1]
    assert rec.queued[1]["start_epoch"] == epoch("18-05-00")
    assert len({q["recording_id"] for q in rec.queued}) == 1


def test_an_unreadable_file_is_retried_then_given_up_on(comp, monkeypatch):
    rec = SplitRecording(comp, monkeypatch)
    rec.add("18-00-00", 0.0)                                # has bytes, but nothing ffmpeg can read
    for _ in range(3):
        comp.export_all()
    assert comp._load_state()["2026-09-30 18-00-00.mp4"]["done"] is True
    assert rec.queued == []


def test_export_chunks_a_finished_recording(tmp_path):
    """Real ffmpeg on a real OBS-named file (skipped if ffmpeg isn't around)."""
    import shutil
    import subprocess
    from audio_export import export_chunks, recording_start_epoch
    ffmpeg = Path(__file__).resolve().parent.parent / "ffmpeg.exe"
    if not ffmpeg.exists():
        ffmpeg = Path(shutil.which("ffmpeg") or "")
    if not ffmpeg or not ffmpeg.exists():
        pytest.skip("ffmpeg not available")
    rec = tmp_path / "2026-09-30 18-01-05.mp4"
    subprocess.run([str(ffmpeg), "-y", "-f", "lavfi", "-i", "color=black:s=64x36:d=12", "-f", "lavfi",
                    "-i", "sine=frequency=300:duration=12", "-shortest", "-c:v", "libx264", "-c:a", "aac",
                    str(rec)], capture_output=True, check=True)
    import audio_export
    audio_export.CHUNK_SEC = 5
    try:
        chunks = export_chunks(ffmpeg, rec, tmp_path / "out", "r" * 32)
    finally:
        audio_export.CHUNK_SEC = 300
    assert len(chunks) == 3
    assert [round(o) for _, o, _ in chunks] == [0, 5, 10]
    assert recording_start_epoch(rec) == time.mktime(time.strptime("2026-09-30 18:01:05", "%Y-%m-%d %H:%M:%S"))


# ── Host app's view ──────────────────────────────────────────────────────

def test_host_log_lines_only_when_something_changes():
    from app.companion_link import CompanionLink
    now = time.time()
    snapshots = [
        {"companions": [{"device_id": "d", "username": "Teddy_Dance", "seconds_since_seen": 3,
                         "status": {"recording": True, "since": now - 600}}]},
        {"companions": [{"device_id": "d", "username": "Teddy_Dance", "seconds_since_seen": 3,
                         "status": {"recording": True, "since": now - 660}}]},
        {"companions": [{"device_id": "d", "username": "Teddy_Dance", "seconds_since_seen": 400,
                         "status": {"recording": True, "since": now - 700}}]},
    ]
    link = CompanionLink()
    it = iter(snapshots)
    link.status = lambda: next(it)
    names = {"teddy_dance": "James"}
    assert link.changed_lines(names) == ["James (Teddy_Dance): recording ✓ 10 min"]
    assert link.changed_lines(names) == []                      # just a minute longer: not news
    assert "not checking in" in link.changed_lines(names)[0]
