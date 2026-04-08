import obswebsocket
from obswebsocket import requests
# Import the variables directly from your config file
from app.config import RECORDINGS_DIR

class OBSController:
    def __init__(self, host="localhost", port=4455, password="gr17aGAe8WkxZO6i"):
        """
        Initialize the controller. 
        Note: You should eventually add OBS_HOST/PORT/PASS to your config.py
        """
        self.host = host
        self.port = port
        self.password = password
        self.client = obswebsocket.obsws(self.host, self.port, self.password)

    def connect(self):
        try:
            self.client.connect()
            # Dynamic Pathing: Tell OBS to use your USB recordings folder
            # We convert the Path object to a string for the OBS API
            self.client.call(requests.SetRecordDirectory(recordDirectory=str(RECORDINGS_DIR)))
            return True
        except Exception as e:
            print(f"Failed to connect to OBS: {e}")
            return False

    def start_recording(self):
        """
        Presets OBS to the correct scene and starts the session recording.
        """
        try:
            # 1. Ensure we are on the correct scene for Discord/Mic capture
            # Change "R6_Intelligence" to whatever your scene is named in OBS
            self.client.call(requests.SetCurrentProgramScene(sceneName="R6_Intelligence"))

            # 2. Check status and start
            status = self.client.call(requests.GetRecordStatus())
            if not status.getOutputActive():
                self.client.call(requests.StartRecord())
                print(f"Recording started. Saving to: {RECORDINGS_DIR}")
                return True
            else:
                print("OBS is already recording.")
                return True
        except Exception as e:
            print(f"Error starting OBS recording: {e}")
            return False

    def stop_recording(self):
        """
        Stops the recording and returns the path to the new file.
        """
        try:
            response = self.client.call(requests.StopRecord())
            # StopRecord returns the output path of the saved file
            save_path = response.getOutputPath()
            self.client.disconnect()
            return save_path
        except Exception as e:
            print(f"Error stopping OBS recording: {e}")
            return None