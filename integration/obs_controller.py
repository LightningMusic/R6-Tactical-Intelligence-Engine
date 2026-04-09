import obswebsocket
from obswebsocket import requests as obs_requests

from app.config import RECORDINGS_DIR, OBS_HOST, OBS_PORT, OBS_PASSWORD, OBS_SCENE_NAME


class OBSController:
    """
    Manages OBS Studio connection and recording lifecycle
    via obs-websocket.
    """

    def __init__(self) -> None:
        self.client = obswebsocket.obsws(OBS_HOST, OBS_PORT, OBS_PASSWORD)
        self._connected = False

    # =====================================================
    # CONNECTION
    # =====================================================

    def connect(self) -> bool:
        if self._connected:
            return True
        try:
            self.client.connect()
            self._connected = True
            # Point OBS output to the USB recordings folder
            self.client.call(
                obs_requests.SetRecordDirectory(
                    recordDirectory=str(RECORDINGS_DIR)
                )
            )
            return True
        except Exception as e:
            print(f"[OBS] Failed to connect: {e}")
            self._connected = False
            return False

    def disconnect(self) -> None:
        if not self._connected:
            return
        try:
            self.client.disconnect()
        except Exception as e:
            print(f"[OBS] Error during disconnect: {e}")
        finally:
            self._connected = False

    @property
    def is_connected(self) -> bool:
        return self._connected

    # =====================================================
    # RECORDING
    # =====================================================

    def start_recording(self) -> bool:
        """
        Switches to the R6 scene and starts recording.
        Returns True if recording is active after the call.
        """
        if not self._connected:
            print("[OBS] Not connected.")
            return False

        try:
            self.client.call(
                obs_requests.SetCurrentProgramScene(sceneName=OBS_SCENE_NAME)
            )

            status = self.client.call(obs_requests.GetRecordStatus())
            if not status.getOutputActive():
                self.client.call(obs_requests.StartRecord())
                print(f"[OBS] Recording started → {RECORDINGS_DIR}")
            else:
                print("[OBS] Already recording.")

            return True

        except Exception as e:
            print(f"[OBS] Error starting recording: {e}")
            return False

    def stop_recording(self) -> str | None:
        """
        Stops the recording.
        Returns the output file path, or None on failure.
        """
        if not self._connected:
            print("[OBS] Not connected.")
            return None

        try:
            response = self.client.call(obs_requests.StopRecord())
            save_path: str = response.getOutputPath()
            print(f"[OBS] Recording saved → {save_path}")
            return save_path
        except Exception as e:
            print(f"[OBS] Error stopping recording: {e}")
            return None

    def get_recording_status(self) -> bool:
        """Returns True if OBS is currently recording."""
        if not self._connected:
            return False
        try:
            status = self.client.call(obs_requests.GetRecordStatus())
            return bool(status.getOutputActive())
        except Exception:
            return False