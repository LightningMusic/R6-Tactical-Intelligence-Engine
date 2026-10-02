"""
R6Companion's brain, without any UI (companion_app.py is the window).

Built on the assumption that the teammate does nothing but double-click it
at the start of practice -- and then probably just shuts the PC off at the
end:

  * Follows the host: the host's R6Analyzer tells the server "the team is
    recording" / "stopped"; this checks in every few seconds and makes OBS
    match. Losing the server mid-practice changes nothing -- it keeps doing
    whatever it was doing rather than stopping on a network blip.
  * Keeps OBS recording: every few seconds, relaunching OBS or restarting
    the recording if either stopped when it shouldn't have.
  * Ships the audio as it goes: OBS starts a new file every 5 minutes
    (gapless), and each one is queued for upload as soon as OBS closes it
    (the same queue R6Voice uses, kept on the stick, retried until the
    server has it). Waiting for a file to close matters on a FAT32 stick,
    where a file still being written reads as 0 bytes to everything but
    OBS. So when the PC is switched off, at most the last few minutes are
    missing, and anything finished but not yet sent goes up the next time
    the companion runs.
"""
from __future__ import annotations

import json
import os
import shutil
import sys
import threading
import time
import uuid
from pathlib import Path
from typing import Callable, Optional

import requests

from audio_export import export_piece, recording_id, recording_start_epoch
from obs_link import ObsLink

TICK_SEC = 3.0
HEARTBEAT_SEC = 5.0
EXPORT_EVERY_SEC = 15.0       # look for newly closed recording files this often
PIECE_SEC = 330.0             # one upload chunk per 5-minute OBS file (a little slack so it's never split)
CHAIN_TOLERANCE_SEC = 2.5     # a file starting this close to the previous one's end continues it
STARTUP_SLACK_SEC = 8.0       # OBS's first file gets audio this long after it's named, at most
KEEP_RECORDINGS_DAYS = 14
VIDEO_SUFFIXES = {".mp4", ".mkv", ".mov"}


def in_use(path: Path) -> bool:
    """True while another process (OBS) has the file open. The stick is
    FAT32, where a file's modified time doesn't move until it's closed, so
    "recently modified" can't be used to spot the file being written."""
    if sys.platform != "win32":
        return False
    import ctypes
    from ctypes import wintypes
    k = ctypes.WinDLL("kernel32", use_last_error=True)
    k.CreateFileW.restype = wintypes.HANDLE
    k.CreateFileW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, wintypes.LPVOID,
                              wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE]
    h = k.CreateFileW(str(path), 0x80000000, 0, None, 3, 0, None)
    if h in (None, wintypes.HANDLE(-1).value):
        return ctypes.get_last_error() == 32
    k.CloseHandle(h)
    return False


def _atomic_write(path: Path, text: str) -> None:
    """Write then rename: a power cut leaves the old file or the new one, never half of one."""
    tmp = path.with_name(path.name + ".tmp")
    with tmp.open("w", encoding="utf-8") as f:
        f.write(text)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)


class Companion:
    def __init__(self, base_dir: Path, get_server: Callable[[], dict], uploader=None,
                 obs: Optional[ObsLink] = None, log: Callable[[str], None] = print) -> None:
        self.base = base_dir
        self.data = base_dir / "data"
        self.rec_dir = base_dir / "recordings"
        self.pending = self.data / "pending"
        for d in (self.data, self.rec_dir, self.pending):
            d.mkdir(parents=True, exist_ok=True)
        self.settings_path = self.data / "settings.json"
        self.state_path = self.data / "exported.json"
        self.settings = self._load_settings()
        self.get_server = get_server
        self.log = log
        self.obs = obs or ObsLink(base_dir / "OBS-Studio", self.rec_dir,
                                  self.settings["ws_port"], self.settings["ws_password"], log=log)
        self.obs.mic_device = self.settings.get("mic_device", "default")
        if uploader is None:
            from uploader import Uploader
            uploader = Uploader(self.pending, self.get_server)
        self.uploader = uploader
        self.ffmpeg = base_dir / "ffmpeg.exe"

        self.control = {"recording": False, "changed_at": 0.0}
        self.server_ok: Optional[bool] = None
        self.manual: Optional[bool] = None          # None = follow the host
        self._manual_base = 0.0
        self.recording = False
        self.recording_since = 0.0
        self.current_file: Optional[Path] = None
        self.obs_status = "waiting"
        self.exporting = ""
        self._stop = False
        self._export_lock = threading.Lock()

    # ── settings ────────────────────────────────────────────────────

    def _load_settings(self) -> dict:
        try:
            s = json.loads(self.settings_path.read_text(encoding="utf-8-sig"))
        except Exception:
            s = {}
        changed = False
        for key, default in (("device_id", uuid.uuid4().hex), ("ws_password", uuid.uuid4().hex),
                             ("ws_port", 4466), ("username", ""), ("mic_device", "default")):
            if key not in s:
                s[key], changed = default, True
        if changed:
            _atomic_write(self.settings_path, json.dumps(s, indent=2))
        return s

    def save_settings(self, **updates) -> None:
        self.settings.update(updates)
        _atomic_write(self.settings_path, json.dumps(self.settings, indent=2))
        if "mic_device" in updates:
            self.obs.mic_device = updates["mic_device"]
            self.obs.set_up = False           # re-applied before the next recording

    # ── control ─────────────────────────────────────────────────────

    def wanted(self) -> bool:
        return self.manual if self.manual is not None else bool(self.control.get("recording"))

    def set_manual(self, recording: Optional[bool]) -> None:
        """Start/Stop pressed on this PC. Holds until the host next changes
        the team state, then following resumes."""
        self.manual = recording
        self._manual_base = float(self.control.get("changed_at", 0.0))

    def heartbeat(self) -> None:
        cfg = self.get_server()
        if not cfg.get("server_url") or not self.settings.get("username"):
            self.server_ok = False
            return
        try:
            r = requests.post(f"{cfg['server_url']}/api/v1/companion/heartbeat",
                              headers={"Authorization": f"Bearer {cfg.get('voice_token', '')}"},
                              json={"device_id": self.settings["device_id"], "username": self.settings["username"],
                                    "status": self.status()}, timeout=10)
            r.raise_for_status()
            control = r.json().get("control") or {}
            if self.manual is not None and float(control.get("changed_at", 0.0)) != self._manual_base:
                self.manual = None           # the host changed the team state: follow it again
            self.control, self.server_ok = control, True
        except Exception:
            self.server_ok = False           # keep the last known state -- never stop on a blip

    def status(self) -> dict:
        try:
            free_gb = round(shutil.disk_usage(self.base).free / 1e9, 1)
        except OSError:
            free_gb = None
        return {"recording": self.recording, "since": self.recording_since,
                "obs": self.obs_status, "mode": "manual" if self.manual is not None else "following",
                "uploads_waiting": self.uploader.waiting(), "uploaded": self.uploader.sent,
                "exporting": self.exporting, "free_gb": free_gb, "clock": time.time()}

    # ── OBS ─────────────────────────────────────────────────────────

    def tick(self) -> None:
        want = self.wanted()
        try:
            if want or self.obs.ws is not None or self.obs.running():
                if not self.obs.connect():
                    self.obs_status = self.obs.last_error or "OBS not reachable"
                    self.recording = False
                    return
            else:
                self.obs_status = "waiting for the team"
                return
            rec = self.obs.is_recording()
            if want and not rec:
                if self.obs.start():
                    self.log("OBS had stopped recording -- restarted it." if self.recording
                             else "Recording started.")
                    if not self.recording:
                        self.recording_since = time.time()
                    rec = True
                else:
                    self.obs_status = self.obs.last_error
            elif not want and rec:
                out = self.obs.stop()
                rec = False
                self._note_stop(time.time())
                self.log(f"Recording stopped{f': {out.name}' if out else ''}.")
                self._export_soon()
            self.recording = rec
            self.obs_status = "ok"
        except Exception as e:
            self.obs.drop()
            self.obs_status = f"OBS error: {e}"

    # ── audio ───────────────────────────────────────────────────────

    def _in_background(self, fn) -> None:
        threading.Thread(target=fn, daemon=True).start()

    def _export_soon(self) -> None:
        """Ship the last file right after OBS closes it instead of waiting for
        the 15 s loop: teammates often switch the PC off the moment practice ends."""
        def later() -> None:
            for delay in (3.0, 6.0):
                time.sleep(delay)
                self.export_all()
        self._in_background(later)

    def _load_state(self) -> dict:
        try:
            return json.loads(self.state_path.read_text(encoding="utf-8-sig"))
        except Exception:
            return {}

    def _note_stop(self, when: float) -> None:
        """Remembers when a recording was stopped: the exact end of its last
        file, which anchors a recording that never got split."""
        with self._export_lock:
            state = self._load_state()
            state["_stops"] = (state.get("_stops", []) + [when])[-20:]
            self._save_state(state)

    def _save_state(self, state: dict) -> None:
        _atomic_write(self.state_path, json.dumps(state, indent=1))

    def _chain(self, state: dict, f: Path, name_start: float, duration: float) -> tuple[float, str, int]:
        """
        (start epoch of the file's first audio sample, recording id, first
        chunk index).

        OBS names each file after the second it was opened, and the first
        file of a recording only gets audio ~3 s after that (encoders
        starting up; seen on the stick: 70.0 s of content in a file opened
        72.9 s before the split). After that, OBS's splits are gapless to the
        millisecond. So times are anchored on moments that are exact:
          * a file continuing the previous one: previous start + its length
            (same recording id, next chunk numbers);
          * the first file, if OBS already split it: the next file's opening
            second minus this file's length;
          * the first file of a recording stopped before any split: the
            moment the companion stopped it, minus its length;
          * otherwise (PC switched off before the first split): its name.
        """
        prior = [(recording_start_epoch(Path(n)) or 0.0, st) for n, st in state.items()
                 if not n.startswith("_") and st.get("done") and st.get("start") is not None and n != f.name]
        prev = max((p for p in prior if p[0] < name_start), key=lambda p: p[0], default=None)
        if prev is not None:
            prev_end = prev[1]["start"] + prev[1]["exported"]
            if abs(name_start - prev_end) <= CHAIN_TOLERANCE_SEC:
                return prev_end, prev[1]["rid"], prev[1]["base"] + prev[1]["pieces"]

        rid = recording_id(self.settings["device_id"], f)
        later = [recording_start_epoch(g) for g in self.rec_dir.iterdir()
                 if g.suffix.lower() in VIDEO_SUFFIXES and g != f]
        nxt = min((t for t in later if t is not None and t > name_start), default=None)
        if nxt is not None and -CHAIN_TOLERANCE_SEC <= nxt - (name_start + duration) <= STARTUP_SLACK_SEC:
            return nxt - duration, rid, 0
        for stopped in state.get("_stops", []):
            if -CHAIN_TOLERANCE_SEC <= stopped - (name_start + duration) <= STARTUP_SLACK_SEC:
                return stopped - duration, rid, 0
        return name_start, rid, 0

    def export_file(self, f: Path) -> int:
        """Queues one finished (closed) recording file for upload, in pieces
        of at most PIECE_SEC. Returns how many pieces it queued. Call with
        _export_lock held."""
        state = self._load_state()
        st = state.get(f.name) or {}
        if st.get("done") or not self.settings.get("username"):
            return 0
        name_start = recording_start_epoch(f)
        if name_start is None:
            return 0
        self.exporting = f.name
        # Cut first (to a scratch name), then work out the timing from the
        # file's actual length, then queue under the final names.
        cut: list[tuple[Path, float]] = []
        exported = 0.0
        while True:
            path = self.pending / f"_cut_{f.stem}_{len(cut):03d}.ogg"
            dur = export_piece(self.ffmpeg, f, path, exported, PIECE_SEC)
            if dur < 0.5:
                break
            cut.append((path, dur))
            exported += dur
            if dur < PIECE_SEC - 1.0:
                break
        start, rid, base = self._chain(state, f, name_start, exported)
        offset = 0.0
        for i, (path, dur) in enumerate(cut):
            final_path = self.pending / f"{rid}_{base + i:04d}.ogg"
            path.replace(final_path)
            self.uploader.enqueue_file(final_path, recording_id=rid, chunk_index=base + i,
                                       username=self.settings["username"], start_epoch=start + offset,
                                       duration_sec=dur, is_final=not self.recording)
            offset += dur
        pieces = len(cut)
        attempts = int(st.get("attempts", 0)) + 1
        if pieces == 0 and f.stat().st_size > 1024 and attempts < 3:
            state[f.name] = {"attempts": attempts}        # has data but ffmpeg failed: try again later
        else:
            state[f.name] = {"done": True, "at": time.time(), "start": start, "exported": exported,
                             "rid": rid, "base": base, "pieces": pieces}
        self._save_state(state)
        return pieces

    def export_all(self) -> int:
        """Every closed recording file not uploaded yet -- each finished
        5-minute split while recording, the last one when recording stops,
        and anything left over from a PC that was switched off."""
        if not self._export_lock.acquire(blocking=False):
            return 0
        try:
            for scratch in self.pending.glob("_cut_*"):     # a cut interrupted by a crash
                scratch.unlink(missing_ok=True)
            total = 0
            files = [f for f in self.rec_dir.iterdir()
                     if f.suffix.lower() in VIDEO_SUFFIXES and recording_start_epoch(f) is not None]
            for f in sorted(files, key=recording_start_epoch):
                if in_use(f):
                    continue                                 # OBS is still writing it
                total += self.export_file(f)
            if total:
                self.log(f"Queued {total} piece(s) for upload.")
            self._prune()
            return total
        finally:
            self.exporting = ""
            self._export_lock.release()

    def _prune(self) -> None:
        state = self._load_state()
        cutoff = time.time() - KEEP_RECORDINGS_DAYS * 86400
        for name, st in state.items():
            if name.startswith("_"):
                continue
            if st.get("done") and st.get("at", time.time()) < cutoff:
                try:
                    (self.rec_dir / name).unlink()
                except OSError:
                    pass

    # ── loops ───────────────────────────────────────────────────────

    def run_forever(self) -> None:
        self._in_background(self.export_all)          # leftovers from last time

        def beat():
            while not self._stop:
                self.heartbeat()
                time.sleep(HEARTBEAT_SEC)

        threading.Thread(target=beat, daemon=True, name="Heartbeat").start()
        last_export = time.time()
        while not self._stop:
            self.tick()
            if time.time() - last_export >= EXPORT_EVERY_SEC:
                last_export = time.time()
                self._in_background(self.export_all)
            time.sleep(TICK_SEC)

    def shutdown(self) -> None:
        """Window closed or Windows shutting down: stop recording and close
        OBS without asking anything. Whatever isn't uploaded yet stays
        queued on the stick for next time."""
        self._stop = True
        if self.recording:
            try:
                self.obs.stop()
                time.sleep(1.5)             # let OBS finish writing the file
            except Exception:
                pass
            self.recording = False
        self.current_file = None
        try:
            self.obs.quit()
        except Exception:
            pass
