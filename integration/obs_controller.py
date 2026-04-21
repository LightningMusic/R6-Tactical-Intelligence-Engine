import subprocess
import time
from typing import Optional

import psutil
import obswebsocket
from obswebsocket import requests as obs_requests

from app.config import RECORDINGS_DIR, OBS_EXE_PATH, settings


def _obs_is_running() -> bool:
    for proc in psutil.process_iter(["name"]):
        try:
            if proc.info["name"] and "obs64" in proc.info["name"].lower():
                return True
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            pass
    return False


class OBSController:
    """
    Manages OBS Studio connection and recording lifecycle.
    Auto-launches OBS Portable from the USB if not already running.
    Re-reads credentials from settings at connect time so Settings
    changes take effect without restarting the app.
    """

    LAUNCH_WAIT_SEC    = 12   # max seconds to wait for OBS to open
    CONNECT_RETRIES    = 4    # websocket connection attempts after launch
    CONNECT_RETRY_WAIT = 2    # seconds between websocket retries

    def __init__(self) -> None:
        self._connected       = False
        self._client: Optional[obswebsocket.obsws] = None
        self._launched_by_us  = False

    # =====================================================
    # LAUNCH
    # =====================================================

    def _launch_obs(self) -> bool:
        if not OBS_EXE_PATH.exists():
            print(f"[OBS] Not found at: {OBS_EXE_PATH}")
            return False

        print(f"[OBS] Launching: {OBS_EXE_PATH}")
        try:
            subprocess.Popen(
                [str(OBS_EXE_PATH)],   # ← remove --minimize-to-tray
                cwd=str(OBS_EXE_PATH.parent),
                # no CREATE_NO_WINDOW — let it show
            )
            self._launched_by_us = True
        except Exception as e:
            print(f"[OBS] Launch failed: {e}")
            return False

        print(f"[OBS] Waiting up to {self.LAUNCH_WAIT_SEC}s...")
        for i in range(self.LAUNCH_WAIT_SEC):
            time.sleep(1)
            if _obs_is_running():
                print(f"[OBS] Detected after {i+1}s. Waiting 4s for websocket...")
                time.sleep(4)
                return True

        print("[OBS] Never appeared.")
        return False

    # =====================================================
    # CONNECTION
    # =====================================================

    def connect(self) -> bool:
        if self._connected:
            return True

        # ── Ensure OBS is running ─────────────────────────────
        if not _obs_is_running():
            print("[OBS] OBS not running — attempting launch...")
            if not self._launch_obs():
                print("[OBS] Could not launch OBS.")
                return False
        else:
            print("[OBS] OBS already running.")

        # ── Connect to websocket with retries ─────────────────
        for attempt in range(1, self.CONNECT_RETRIES + 1):
            print(f"[OBS] Websocket connect attempt {attempt}/{self.CONNECT_RETRIES}...")
            try:
                # Always create a fresh client — avoids stale connection state
                self._client = obswebsocket.obsws(
                    settings.OBS_HOST,
                    settings.OBS_PORT,
                    settings.OBS_PASSWORD,
                )
                self._client.connect()
                self._connected = True

                # Point OBS at the USB recordings folder
                self._client.call(
                    obs_requests.SetRecordDirectory(
                        recordDirectory=str(RECORDINGS_DIR)
                    )
                )
                print(f"[OBS] Connected. Recording dir → {RECORDINGS_DIR}")
                return True

            except Exception as e:
                print(f"[OBS] Attempt {attempt} failed: {e}")
                self._client = None
                if attempt < self.CONNECT_RETRIES:
                    time.sleep(self.CONNECT_RETRY_WAIT)

        print("[OBS] All connection attempts failed.")
        return False

    def disconnect(self) -> None:
        if not self._connected or self._client is None:
            return
        try:
            self._client.disconnect()
        except Exception as e:
            print(f"[OBS] Disconnect error: {e}")
        finally:
            self._connected = False
            self._client    = None

    @property
    def is_connected(self) -> bool:
        return self._connected

    # =====================================================
    # RECORDING
    # =====================================================

    def start_recording(self) -> bool:
        if not self._connected or self._client is None:
            print("[OBS] Not connected.")
            return False
        try:
            self._client.call(
                obs_requests.SetCurrentProgramScene(
                    sceneName=settings.OBS_SCENE_NAME
                )
            )
            status = self._client.call(obs_requests.GetRecordStatus())
            if not status.getOutputActive():
                self._client.call(obs_requests.StartRecord())
                print(f"[OBS] Recording started.")
            else:
                print("[OBS] Already recording.")
            return True
        except Exception as e:
            print(f"[OBS] Error starting recording: {e}")
            return False

    def stop_recording(self) -> Optional[str]:
        if not self._connected or self._client is None:
            return None
        try:
            response = self._client.call(obs_requests.StopRecord())

            # Try all known path field names across obs-websocket versions
            save_path = None
            for getter in ["getOutputPath", "getOutputFilePath"]:
                try:
                    fn = getattr(response, getter, None)
                    if fn:
                        result = fn()
                        if result:
                            save_path = result
                            break
                except Exception:
                    pass

            if not save_path:
                try:
                    d = response.datain
                    save_path = (
                        d.get("outputPath")
                        or d.get("outputFilePath")
                        or d.get("output-path")
                    )
                except Exception:
                    pass

            if save_path:
                print(f"[OBS] Recording saved → {save_path}")
            else:
                # Fall back to latest file in recordings dir
                try:
                    files = sorted(
                        RECORDINGS_DIR.glob("*.mp4"),
                        key=lambda f: f.stat().st_mtime,
                        reverse=True,
                    )
                    if not files:
                        files = sorted(
                            RECORDINGS_DIR.glob("*.mkv"),
                            key=lambda f: f.stat().st_mtime,
                            reverse=True,
                        )
                    if files:
                        save_path = str(files[0])
                        print(f"[OBS] Path not returned — using latest file: {save_path}")
                    else:
                        print("[OBS] Warning: no recording file found in recordings folder.")
                except Exception as e:
                    print(f"[OBS] Fallback path detection failed: {e}")

            return save_path
        except Exception as e:
            print(f"[OBS] Error stopping recording: {e}")
            return None

    def get_recording_status(self) -> bool:
        if not self._connected or self._client is None:
            return False
        try:
            status = self._client.call(obs_requests.GetRecordStatus())
            return bool(status.getOutputActive())
        except Exception:
            return False