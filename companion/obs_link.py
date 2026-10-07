"""
Drives the portable OBS that lives next to R6Companion on the USB stick.

The companion owns this copy of OBS outright (portable mode: every setting
lives in OBS-Studio\\config on the stick, nothing on the school PC), so
instead of poking at whatever state OBS happens to be in, it writes the
profile it needs before launching:

  * one audio track: the player's mic, and nothing else on it
  * a black 640x360, 10 fps picture at CRF 35 -- OBS always records video,
    this makes it nearly free (a couple of MB a minute)
  * no first-run wizard, and the websocket on with a password only the
    companion knows

Then, over the websocket, it makes sure the mic source exists and is alone
on track 1, and starts/stops recording.
"""
from __future__ import annotations

import configparser
import json
import os
import subprocess
import time
from pathlib import Path
from typing import Optional

import psutil

PROFILE = "R6Companion"
SCENE = "R6_Companion"
MIC = "My_Mic"
MIC_TRACK = 1


def _ini(path: Path) -> configparser.ConfigParser:
    cp = configparser.ConfigParser(interpolation=None)
    cp.optionxform = str                      # OBS keys are case-sensitive
    if path.exists():
        cp.read(path, encoding="utf-8-sig")
    return cp


def _set(cp: configparser.ConfigParser, section: str, **values) -> None:
    if not cp.has_section(section):
        cp.add_section(section)
    for k, v in values.items():
        cp.set(section, k, str(v))


def _write(cp: configparser.ConfigParser, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        cp.write(f, space_around_delimiters=False)


def prepare_portable_obs(obs_dir: Path, ws_port: int, ws_password: str, rec_dir: Path,
                         split_minutes: int = 5) -> None:
    """Writes the settings this companion needs into the stick's OBS."""
    (obs_dir / "portable_mode.txt").touch()
    cfg = obs_dir / "config" / "obs-studio"

    for name in ("user.ini", "global.ini"):          # OBS 30+ reads user.ini; older, global.ini
        cp = _ini(cfg / name)
        _set(cp, "General", FirstRun="false", LastVersion=cp.get("General", "LastVersion", fallback="0"))
        _set(cp, "Basic", Profile=PROFILE, ProfileDir=PROFILE,
             SceneCollection=PROFILE, SceneCollectionFile=PROFILE)
        _write(cp, cfg / name)

    prof = cfg / "basic" / "profiles" / PROFILE
    cp = _ini(prof / "basic.ini")
    _set(cp, "General", Name=PROFILE)
    _set(cp, "Output", Mode="Advanced")
    # A new file every few minutes, gapless (OBS's own automatic splitting).
    # Each finished file is closed, so it can be uploaded straight away even
    # on a FAT32 stick -- where a file still being written reads as 0 bytes
    # to everything but OBS until it's closed.
    _set(cp, "AdvOut", RecType="Standard", RecEncoder="obs_x264", Encoder="obs_x264",
         RecTracks=1 << (MIC_TRACK - 1), RecFormat2="hybrid_mp4", RecFilePath=str(rec_dir),
         TrackIndex=MIC_TRACK, RecSplitFile="true", RecSplitFileType="Time",
         RecSplitFileTime=split_minutes, RecSplitFileResetTimestamps="true")
    _set(cp, "Video", BaseCX=640, BaseCY=360, OutputCX=640, OutputCY=360,
         FPSType=1, FPSInt=10, ScaleType="bilinear")
    _set(cp, "SimpleOutput", FilePath=str(rec_dir))
    _write(cp, prof / "basic.ini")
    (prof / "recordEncoder.json").write_text(
        json.dumps({"rate_control": "CRF", "crf": 35, "preset": "veryfast", "keyint_sec": 10}),
        encoding="utf-8")

    ws = cfg / "plugin_config" / "obs-websocket"
    ws.mkdir(parents=True, exist_ok=True)
    (ws / "config.json").write_text(json.dumps({
        "alerts_enabled": False, "auth_required": True, "first_load": False,
        "server_enabled": True, "server_password": ws_password, "server_port": ws_port,
    }), encoding="utf-8")


class ObsLink:
    def __init__(self, obs_dir: Path, rec_dir: Path, ws_port: int, ws_password: str,
                 log=print) -> None:
        self.obs_dir = obs_dir
        self.exe = obs_dir / "bin" / "64bit" / "obs64.exe"
        self.rec_dir = rec_dir
        self.port = ws_port
        self.password = ws_password
        self.log = log
        self.ws = None
        self.set_up = False
        self.mic_device = "default"
        self.mic_name = ""                  # remembered by name: ids differ from PC to PC, a headset's name doesn't
        self.last_error = ""
        self.split_minutes = 5

    # ── process ─────────────────────────────────────────────────────

    def _ours(self) -> list:
        """This stick's OBS processes. Paths are compared fully resolved:
        Windows can report the same folder as C:\\Users\\ELIJAH~1\\... in one
        place and C:\\Users\\Elijah Duchene\\... in another."""
        want = os.path.normcase(os.path.realpath(self.exe))
        found = []
        for p in psutil.process_iter(["exe"]):
            try:
                exe = p.info["exe"]
                if exe and os.path.normcase(os.path.realpath(exe)) == want:
                    found.append(p)
            except (psutil.NoSuchProcess, psutil.AccessDenied, OSError):
                continue
        return found

    def running(self) -> bool:
        return bool(self._ours())

    def launch(self) -> bool:
        if not self.exe.exists():
            self.last_error = f"OBS isn't on the stick ({self.exe})."
            return False
        prepare_portable_obs(self.obs_dir, self.port, self.password, self.rec_dir, self.split_minutes)
        # OBS leaves a run_<id> marker in .sentinel while it runs and removes it
        # on a clean exit. A PC switched off mid-practice leaves one behind,
        # and OBS then opens with a "start in Safe Mode?" dialog that blocks
        # everything -- the websocket included -- until someone clicks it.
        # This only runs when the stick's OBS isn't running, so any marker
        # left is stale.
        for marker in (self.obs_dir / "config" / "obs-studio" / ".sentinel").glob("run_*"):
            try:
                marker.unlink()
            except OSError:
                pass
        subprocess.Popen([str(self.exe), "--portable", "--minimize-to-tray",
                          "--disable-updater", "--disable-shutdown-check"],
                         cwd=str(self.exe.parent))
        self.log("Started OBS.")
        time.sleep(3.0)            # let it load before the first request (see _try_connect)
        return True

    def connect(self, wait_sec: float = 30.0) -> bool:
        if self.ws is not None:
            return True
        if not self.running():
            return self.launch() and self._try_connect(wait_sec)
        if self._try_connect(wait_sec):
            return True
        # Our OBS is running but won't talk to us -- left over from a crash,
        # stuck on a dialog, or started with other settings. It's the
        # stick's own copy, so restart it clean.
        self.log("OBS isn't responding -- restarting it.")
        self.quit()
        time.sleep(1.0)
        return self.launch() and self._try_connect(wait_sec)

    def _try_connect(self, wait_sec: float) -> bool:
        import obswebsocket
        deadline = time.time() + wait_sec
        while time.time() < deadline:
            try:
                ws = obswebsocket.obsws("localhost", self.port, self.password)
                ws.connect()
                # The websocket comes up before OBS itself has finished
                # starting, and a connection that asks too early can stay
                # stuck getting empty answers (seen on the first launch from
                # a stick: 60 s of them). So: give each connection 5 s to
                # answer properly, otherwise drop it and start a fresh one.
                from obswebsocket import requests as R
                ready_by = min(deadline, time.time() + 5.0)
                while time.time() < ready_by:
                    try:
                        reply = ws.call(R.GetRecordStatus())
                        if "outputActive" in (reply.datain or {}):
                            self.ws, self.set_up, self.last_error = ws, False, ""
                            return True
                    except Exception:
                        pass
                    time.sleep(0.5)
                try:
                    ws.disconnect()
                except Exception:
                    pass
                self.last_error = "OBS is still starting"
                time.sleep(1.0)
            except Exception as e:
                self.last_error = f"Waiting for OBS ({type(e).__name__})"
                time.sleep(1.5)
        return False

    def quit(self) -> None:
        """Close this stick's OBS (only ever called once recording has
        stopped, so the file is already finished)."""
        self.drop()
        for p in self._ours():
            try:
                p.terminate()
                p.wait(timeout=10)
            except psutil.TimeoutExpired:
                p.kill()
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                continue

    def drop(self) -> None:
        try:
            if self.ws is not None:
                self.ws.disconnect()
        except Exception:
            pass
        self.ws, self.set_up = None, False

    def call(self, req):
        try:
            return self.ws.call(req)
        except Exception:
            self.drop()
            raise

    # ── setup ───────────────────────────────────────────────────────

    def setup(self) -> bool:
        """Mic source in the scene, alone on track 1; everything else off."""
        from obswebsocket import requests as R
        try:
            scenes = {s["sceneName"] for s in self.call(R.GetSceneList()).getScenes() or []}
            if SCENE not in scenes:
                self.call(R.CreateScene(sceneName=SCENE))
            self.call(R.SetCurrentProgramScene(sceneName=SCENE))
            inputs = {i["inputName"]: i["inputKind"] for i in self.call(R.GetInputList()).getInputs() or []}
            if MIC not in inputs:
                self.call(R.CreateInput(sceneName=SCENE, inputName=MIC, inputKind="wasapi_input_capture",
                                        inputSettings={"device_id": self.mic_device}, sceneItemEnabled=True))
                inputs[MIC] = "wasapi_input_capture"
            else:
                self.call(R.SetInputSettings(inputName=MIC, inputSettings={"device_id": self.mic_device},
                                             overlay=True))
            self._use_remembered_mic()
            special = self.call(R.GetSpecialInputs()).datain or {}
            for key in ("desktop1", "desktop2", "mic1", "mic2", "mic3", "mic4"):
                if special.get(key):
                    inputs.setdefault(special[key], "global")
            for name in inputs:
                try:
                    self.call(R.SetInputAudioTracks(inputName=name, inputAudioTracks={
                        str(t): (name == MIC and t == MIC_TRACK) for t in range(1, 7)}))
                except Exception:
                    if self.ws is None:
                        raise
            self.call(R.SetRecordDirectory(recordDirectory=str(self.rec_dir)))
            self.set_up = True
            return True
        except Exception as e:
            self.last_error = f"OBS setup failed: {e}"
            return False

    def set_mic(self, device_id: str) -> bool:
        """Point OBS's microphone at another device, in the middle of a recording if need be."""
        from obswebsocket import requests as R
        try:
            self.call(R.SetInputSettings(inputName=MIC, inputSettings={"device_id": device_id}, overlay=True))
            self.mic_device = device_id
            return True
        except Exception:
            return False

    def _use_remembered_mic(self) -> None:
        """A microphone chosen (or found) on another PC is remembered by name; use the same-named device here."""
        if not self.mic_name:
            return
        try:
            from mic_pick import find_by_name
            hit = find_by_name(self.microphones(), self.mic_name)
            if hit and hit[0] != self.mic_device:
                self.set_mic(hit[0])
        except Exception:
            pass

    def microphones(self) -> list[tuple[str, str]]:
        """(device_id, name) of every mic OBS can see, "default" first."""
        from obswebsocket import requests as R
        try:
            items = self.call(R.GetInputPropertiesListPropertyItems(
                inputName=MIC, propertyName="device_id")).datain.get("propertyItems") or []
            return [(str(i.get("itemValue")), str(i.get("itemName"))) for i in items if i.get("itemEnabled", True)]
        except Exception:
            return []

    # ── recording ───────────────────────────────────────────────────

    def is_recording(self) -> bool:
        from obswebsocket import requests as R
        return bool(self.call(R.GetRecordStatus()).getOutputActive())

    def start(self) -> bool:
        from obswebsocket import requests as R
        if not self.set_up and not self.setup():
            return False
        self.call(R.StartRecord())
        for _ in range(20):
            time.sleep(0.25)
            if self.is_recording():
                return True
        self.last_error = "OBS didn't start recording."
        return False

    def stop(self, wait_sec: float = 15.0) -> Optional[Path]:
        """Stops and waits for OBS to finish writing the file (a couple of
        seconds; until then it's still open, and on FAT32 still reads as
        empty)."""
        from obswebsocket import requests as R
        out = self.call(R.StopRecord()).datain.get("outputPath")
        deadline = time.time() + wait_sec
        while time.time() < deadline:
            try:
                if not self.is_recording():
                    break
            except Exception:
                break
            time.sleep(0.5)
        return Path(out) if out else None
