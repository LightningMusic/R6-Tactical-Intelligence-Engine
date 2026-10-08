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
        self.mic_name = ""
        self.mics = []                 # [(device id, name)] this PC's microphones, as OBS would list them
        self.set_mic_calls = []

    def microphones(self):
        return list(self.mics)

    def set_mic(self, device_id):
        self.set_mic_calls.append(device_id)
        self.mic_device = device_id
        return True

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


def test_the_last_file_is_shipped_right_after_the_host_stops(comp, monkeypatch):
    import core
    queued, passes = [], []
    monkeypatch.setattr(comp, "_in_background", queued.append)
    monkeypatch.setattr(comp, "export_all", lambda: passes.append(1))
    monkeypatch.setattr(core.time, "sleep", lambda s: None)
    server_says(monkeypatch, comp, recording=True)
    comp.tick()
    assert not queued                                   # nothing extra while recording
    server_says(monkeypatch, comp, recording=False, changed_at=2.0)
    comp.tick()
    assert len(queued) == 1                             # the hand-off is scheduled the moment OBS stops
    queued[0]()
    assert len(passes) == 2                             # two tries, in case OBS is slow to close the file


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

def test_saved_settings_and_state_leave_no_temp_files(comp, tmp_path):
    import json
    comp._save_state({"a.mkv": {"done": True}})
    comp.save_settings(username="Someone_Else")
    assert comp._load_state() == {"a.mkv": {"done": True}}
    assert json.loads(comp.settings_path.read_text(encoding="utf-8"))["username"] == "Someone_Else"
    assert not list(tmp_path.rglob("*.tmp"))


def test_a_power_cut_mid_save_keeps_the_old_name_and_state(comp, monkeypatch):
    import json
    import core
    comp._save_state({"a.mkv": {"done": True}})
    name_before = json.loads(comp.settings_path.read_text(encoding="utf-8"))["username"]

    def power_cut(src, dst):
        raise OSError("power cut between writing and renaming")

    with monkeypatch.context() as m:
        m.setattr(core.os, "replace", power_cut)
        with pytest.raises(OSError):
            comp.save_settings(username="Someone_Else")
        with pytest.raises(OSError):
            comp._save_state({"a.mkv": {"done": True}, "b.mkv": {"done": True}})
    assert json.loads(comp.settings_path.read_text(encoding="utf-8"))["username"] == name_before
    assert comp._load_state() == {"a.mkv": {"done": True}}


class _Resp:
    def __init__(self, code):
        self.status_code = code


class _Put:
    """Stands in for requests: answers PUT with a chosen status or exception."""
    def __init__(self, code=200, error=None):
        self.code, self.error, self.calls = code, error, []

    def put(self, url, headers=None, json=None, timeout=None):
        self.calls.append((url, json))
        if self.error:
            raise self.error
        return _Resp(self.code)


@pytest.fixture
def _server_settings():
    """Server URL and key for the host-link tests, put back afterwards (settings is one shared object)."""
    from app.config import settings
    saved = {k: settings.get(k) for k in ("server_url", "api_key")}
    settings.set_many({"server_url": "http://srv:8000", "api_key": "k"})
    yield
    settings.set_many(saved)


def _link(http):
    from app.companion_link import CompanionLink
    return CompanionLink(http=http)


def test_telling_recorders_to_start_works_and_leaves_no_error(_server_settings):
    link = _link(_Put(200))
    assert link.set_recording(True) is True and link.last_error == ""


def test_a_refused_key_is_reported_in_words_not_swallowed(_server_settings):
    # 2026-10-05: every start/stop signal got 401 and nothing on the host's screen said so, so teammates'
    # browser recorders (which follow the host) never recorded.
    link = _link(_Put(401))
    assert link.set_recording(True) is False
    assert "refused" in link.last_error and "API key" in link.last_error
    link = _link(_Put(500))
    assert link.set_recording(True) is False and "HTTP 500" in link.last_error
    link = _link(_Put(error=ConnectionError("down")))
    assert link.set_recording(False) is False and "could not be reached" in link.last_error


def test_a_later_success_clears_the_error(_server_settings):
    http = _Put(401)
    link = _link(http)
    link.set_recording(True)
    http.code = 200
    assert link.set_recording(True) is True and link.last_error == ""


# ── is the microphone actually delivering sound? (2026-10-06: a headset opened fine and delivered only zeros) ──

def recording_now(comp, monkeypatch, peaks, seconds_in=100.0):
    """A companion that has been recording for `seconds_in` s, whose file reads back as the given peaks in turn."""
    import core
    now = 1_800_000_000.0
    comp.recording, comp.recording_since = True, now - seconds_in
    name = time.strftime("%Y-%m-%d %H-%M-%S", time.localtime(now - seconds_in)) + ".mp4"
    (comp.rec_dir / name).write_bytes(b"x")
    answers = iter(peaks)
    calls = []
    monkeypatch.setattr(core, "measure_peak", lambda *a, **k: (calls.append(a), next(answers))[1])
    return now, calls


def test_a_microphone_that_delivers_only_zeros_is_reported(comp, monkeypatch):
    now, calls = recording_now(comp, monkeypatch, [0.0])
    logs = []
    comp.log = logs.append                                         # (no other microphones on this fake PC)
    assert comp.check_mic(now) is False
    assert comp.mic_ok is False and comp.mic_peak == 0.0
    assert comp.status()["mic_ok"] is False                        # goes to the host with the next check-in
    assert "silence" in logs[0] and "mute switch" in logs[-1]


def test_a_dead_microphone_is_replaced_by_one_that_works_without_anyone_doing_anything(comp, monkeypatch):
    import core
    monkeypatch.setattr(core.time, "sleep", lambda s: None)
    now, calls = recording_now(comp, monkeypatch, [0.0, 0.0, 0.18])    # the one in use, the first spare, the second spare
    comp.obs.mics = [("default", "Default"), ("{usb}", "Microphone (USB Live Camera audio)"),
                     ("{a}", "Speakers (Loopback)"), ("{b}", "Headset Microphone (AWPRO H Wireless Chat)"),
                     ("{c}", "Microphone (Realtek Audio)")]
    comp.obs.mic_device = "{dead}"
    logs = []
    comp.log = logs.append
    assert comp.check_mic(now) is True
    # camera and loopback are never tried; the system default goes first, then the headset
    assert comp.obs.set_mic_calls == ["default", "{b}"]
    assert comp.mic_ok is True and comp.mic_healed == "Headset Microphone (AWPRO H Wireless Chat)"
    assert comp.settings["mic_device"] == "{b}" and comp.settings["mic_name"].startswith("Headset Microphone")
    assert comp.status()["mic_healed"].startswith("Headset") and comp.status()["mic_ok"] is True
    assert any("Switched to another microphone automatically" in line for line in logs)


def test_when_no_microphone_works_the_original_is_put_back_and_the_host_is_told(comp, monkeypatch):
    import core
    monkeypatch.setattr(core.time, "sleep", lambda s: None)
    now, calls = recording_now(comp, monkeypatch, [0.0] * 6)
    comp.obs.mics = [("default", "Default"), ("{b}", "Headset Microphone (X)"), ("{c}", "Microphone (Y)")]
    comp.obs.mic_device = "{orig}"
    logs = []
    comp.log = logs.append
    assert comp.check_mic(now) is False
    assert comp.obs.set_mic_calls == ["default", "{b}", "{c}", "{orig}"]      # everything tried, then back where it was
    assert comp.mic_ok is False and comp.status()["mic_ok"] is False
    assert any("No microphone on this PC is delivering sound" in line for line in logs)
    # and it doesn't hammer OBS: the next look is a few minutes away
    assert comp._heal_after >= core.time.time() + core.MIC_HEAL_RETRY_SEC - 5


def test_a_merely_quiet_microphone_is_flagged_but_never_swapped(comp, monkeypatch):
    import core
    monkeypatch.setattr(core.time, "sleep", lambda s: None)
    now, calls = recording_now(comp, monkeypatch, [0.001])                     # noisy-floor quiet, not digital silence
    comp.obs.mics = [("default", "Default"), ("{b}", "Headset Microphone (X)")]
    assert comp.check_mic(now) is False and comp.obs.set_mic_calls == []


def test_the_search_stops_when_the_recording_does(comp, monkeypatch):
    import core
    monkeypatch.setattr(core.time, "sleep", lambda s: setattr(comp, "recording", False))
    now, calls = recording_now(comp, monkeypatch, [0.0, 0.5])
    comp.obs.mics = [("default", "Default"), ("{b}", "Headset Microphone (X)")]
    comp.obs.mic_device = "{orig}"
    logs = []
    comp.log = logs.append
    comp.check_mic(now)
    # the first candidate was set, the session ended before it was proven, so nothing is kept and the original is back
    assert comp.obs.set_mic_calls == ["default", "{orig}"] and comp.mic_healed == ""
    assert not comp.settings.get("mic_name")                                      # nothing saved
    assert not any("No microphone on this PC" in line for line in logs)


def test_a_working_microphone_is_not_flagged(comp, monkeypatch):
    now, calls = recording_now(comp, monkeypatch, [0.1])
    assert comp.check_mic(now) is True and comp.status()["mic_ok"] is True


def test_the_check_waits_for_obs_to_get_going_and_is_not_repeated_every_few_seconds(comp, monkeypatch):
    now, calls = recording_now(comp, monkeypatch, [0.1, 0.1], seconds_in=20)
    assert comp.mic_check_due(now) is False and comp.check_mic(now) is None and not calls     # too early
    assert comp.mic_check_due(now + 30) is True                                                  # 50 s in
    comp.check_mic(now + 30)
    assert len(calls) == 1
    assert comp.mic_check_due(now + 40) is False and comp.check_mic(now + 40) is True and len(calls) == 1
    assert comp.mic_check_due(now + 95) is True                                                  # a minute later: again


def test_a_file_that_cant_be_read_yet_is_unknown_not_silent(comp, monkeypatch):
    now, calls = recording_now(comp, monkeypatch, [None])
    assert comp.check_mic(now) is None and comp.mic_ok is None                                  # no false alarm
    now2, _ = recording_now(comp, monkeypatch, [0.0, None], seconds_in=100)
    comp.mic_ok = None
    comp._mic_probe_at = 0.0
    assert comp.check_mic(now2) is False
    assert comp.check_mic(now2 + 70) is False and comp.mic_ok is False                           # an unreadable probe keeps the last answer


def test_recovery_is_logged_and_stopping_clears_the_answer(comp, monkeypatch):
    now, calls = recording_now(comp, monkeypatch, [0.0, 0.2])
    logs = []
    comp.log = logs.append
    comp.check_mic(now)
    comp.check_mic(now + 70)
    assert comp.mic_ok is True and any("picking up sound now" in line for line in logs)
    comp.recording = False
    assert comp.mic_check_due(now + 80) is True                                                  # one call to clear it
    comp.check_mic(now + 80)
    assert comp.mic_ok is None and comp.mic_peak is None and comp.mic_check_due(now + 90) is False


def test_the_host_log_tells_the_host_a_companions_mic_is_silent_while_it_records():
    from app.companion_link import CompanionLink
    c = {"username": "Comp_User", "seconds_since_seen": 3, "status": {"recording": True, "since": 0, "mic_ok": False}}
    assert "mic looks silent" in CompanionLink.describe(c, now=600)
    c["status"]["mic_ok"] = True
    assert "silent" not in CompanionLink.describe(c, now=600)
    c["status"].update(recording=False, mic_ok=False)                                            # not recording: nothing to say
    assert "silent" not in CompanionLink.describe(c, now=600)


@pytest.mark.skipif(not (Path(__file__).resolve().parent.parent / "ffmpeg.exe").exists(), reason="ffmpeg.exe not in the repo root")
def test_measure_peak_hears_a_tone_and_a_silent_stretch_in_a_real_file(tmp_path):
    import subprocess
    import audio_export
    ff = Path(__file__).resolve().parent.parent / "ffmpeg.exe"
    f = tmp_path / "2026-10-06 20-00-00.mp4"
    graph = ("sine=frequency=440:sample_rate=48000,volume=4,atrim=0:10[a];"
             "anullsrc=r=48000:cl=mono,atrim=0:10[b];[a][b]concat=n=2:v=0:a=1[o]")
    subprocess.run([str(ff), "-y", "-hide_banner", "-loglevel", "error", "-f", "lavfi", "-i", "color=c=black:s=160x90:r=5",
                    "-filter_complex", graph, "-map", "0:v", "-map", "[o]", "-c:v", "libx264", "-preset", "ultrafast",
                    "-c:a", "aac", "-t", "20", str(f)], check=True, timeout=120)
    assert audio_export.measure_peak(ff, f, 0, 6) > 0.1                                           # the tone
    assert audio_export.measure_peak(ff, f, 12, 6) < 0.002                                        # the silence
    assert audio_export.measure_peak(ff, f, 500, 6) is None                                       # past the end: unknown


def test_one_missed_minute_in_the_middle_of_a_session_does_not_raise_an_alarm():
    # 2026-10-06: a single 10 s timeout (while a package uploaded) warned that recorders "will NOT record",
    # and it had fixed itself a minute later.
    from app.companion_link import SignalWatch
    w = SignalWatch()
    assert w.record(True) is None
    assert w.record(False) is None            # one miss: say nothing
    assert w.record(True) is None             # and nothing when it comes back, since nothing was said
    assert w.record(False) is None and w.record(False) == "warn"      # two in a row: now it matters
    assert w.record(False) is None            # only said once
    assert w.record(True) == "recovered" and w.record(True) is None


def test_a_signal_that_has_never_worked_this_session_warns_at_once():
    from app.companion_link import SignalWatch
    w = SignalWatch()
    assert w.record(False) == "warn"           # nothing has told the teammates to start yet
    assert w.record(False) is None and w.record(True) == "recovered"


def test_a_failed_stop_signal_warns_at_once_because_there_is_no_next_attempt():
    from app.companion_link import SignalWatch
    w = SignalWatch()
    assert w.record(True) is None
    assert w.record(False, last_chance=True) == "warn"


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


def test_a_stick_left_at_home_is_reported_once_not_every_minute():
    from app.companion_link import CompanionLink
    link = CompanionLink()
    seen = iter(range(80_700, 85_000, 60))                      # 2026-10-07: 1345 min, 1346 min, ...
    link.status = lambda: {"companions": [{"device_id": "d", "username": "Stick_Owner", "seconds_since_seen": next(seen),
                                           "status": {"recording": False}}]}
    lines = [line for _ in range(70) for line in link.changed_lines({"stick_owner": "Pat"})]
    assert len(lines) == 1 and "not checking in" in lines[0]


def test_the_host_hears_when_a_recorder_switched_mics_by_itself():
    from app.companion_link import CompanionLink
    line = CompanionLink.describe({"username": "Zander_Web", "seconds_since_seen": 1,
                                   "status": {"kind": "browser", "started": True, "recording": True, "since": time.time(),
                                              "mic_ok": True, "mic_healed": "Microphone (USB Audio Device)"}})
    assert "switched to a working mic by itself: Microphone (USB Audio Device)" in line
    assert "pick the headset" not in line
