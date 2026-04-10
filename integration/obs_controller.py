import obswebsocket
from obswebsocket import requests as obs_requests

from app.config import RECORDINGS_DIR, settings


class OBSController:
    """
    Manages OBS Studio connection and recording lifecycle
    via obs-websocket. Reads credentials from settings singleton
    at call time so changes in Settings UI take effect immediately.
    """

    def __init__(self) -> None:
        self._connected = False
        self._client: obswebsocket.obsws | None = None

    def _make_client(self) -> obswebsocket.obsws:
        return obswebsocket.obsws(
            settings.OBS_HOST,
            settings.OBS_PORT,
            settings.OBS_PASSWORD,
        )

    # =====================================================
    # CONNECTION
    # =====================================================

    def connect(self) -> bool:
        if self._connected:
            return True
        try:
            self._client = self._make_client()
            self._client.connect()
            self._connected = True
            self._client.call(
                obs_requests.SetRecordDirectory(
                    recordDirectory=str(RECORDINGS_DIR)
                )
            )
            return True
        except Exception as e:
            print(f"[OBS] Failed to connect: {e}")
            self._connected = False
            self._client = None
            return False

    def disconnect(self) -> None:
        if not self._connected or self._client is None:
            return
        try:
            self._client.disconnect()
        except Exception as e:
            print(f"[OBS] Error during disconnect: {e}")
        finally:
            self._connected = False
            self._client = None

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
                print(f"[OBS] Recording started → {RECORDINGS_DIR}")
            else:
                print("[OBS] Already recording.")
            return True
        except Exception as e:
            print(f"[OBS] Error starting recording: {e}")
            return False

    def stop_recording(self) -> str | None:
        if not self._connected or self._client is None:
            print("[OBS] Not connected.")
            return None
        try:
            response  = self._client.call(obs_requests.StopRecord())
            save_path = response.getOutputPath()
            print(f"[OBS] Recording saved → {save_path}")
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