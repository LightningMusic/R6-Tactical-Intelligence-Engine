"""
On a PC whose OBS is someone's personal setup (the host's home PC), the app records in its own OBS profile and
scene collection and switches back afterwards; a PC already set up for R6 (the practice PC) is left alone.
"""
import pytest

pytest.importorskip("obswebsocket")
import integration.obs_controller as oc  # noqa: E402


class FakeOBS:
    def __init__(self, collection="Untitled", scenes=("Scene",), recording=False):
        self.profiles = ["Untitled"]
        self.profile = "Untitled"
        self.collections = {collection: list(scenes)}
        self.collection = collection
        self.params = {"Untitled": {("Output", "Mode"): "Simple"}}
        self.recording = recording
        self.calls = []

    def call(self, req):
        d, name = req.dataout, req.name
        self.calls.append(name)
        if name == "GetProfileList":
            req.datain = {"currentProfileName": self.profile, "profiles": list(self.profiles)}
        elif name == "GetSceneCollectionList":
            req.datain = {"currentSceneCollectionName": self.collection, "sceneCollections": list(self.collections)}
        elif name == "GetSceneList":
            req.datain = {"scenes": [{"sceneName": s} for s in self.collections[self.collection]]}
        elif name == "GetRecordStatus":
            req.datain = {"outputActive": self.recording}
        elif name in ("CreateProfile", "SetCurrentProfile"):
            if self.recording:
                raise RuntimeError("can't switch profiles while recording")
            if name == "CreateProfile":
                self.profiles.append(d["profileName"])
                self.params[d["profileName"]] = {}
            self.profile = d["profileName"]
        elif name in ("CreateSceneCollection", "SetCurrentSceneCollection"):
            self.collections.setdefault(d["sceneCollectionName"], [])
            self.collection = d["sceneCollectionName"]
        elif name == "SetProfileParameter":
            self.params[self.profile][(d["parameterCategory"], d["parameterName"])] = d["parameterValue"]
        return req


@pytest.fixture
def ctl(tmp_path, monkeypatch):
    monkeypatch.setattr(oc, "RESTORE_FILE", tmp_path / "obs_restore.json")
    monkeypatch.setattr(oc.time, "sleep", lambda s: None)
    c = oc.OBSController()
    c._connected = True
    return c


def test_a_personal_obs_is_switched_to_our_setup_and_back(ctl):
    obs = ctl._client = FakeOBS()
    assert ctl.use_own_setup() is True
    assert (obs.profile, obs.collection) == ("R6Analyzer", "R6Analyzer")
    assert obs.params["R6Analyzer"][("AdvOut", "RecFormat2")] == "mkv"
    assert obs.params["Untitled"] == {("Output", "Mode"): "Simple"}          # the user's profile untouched
    assert ctl.restore_user_setup() is True
    assert (obs.profile, obs.collection) == ("Untitled", "Untitled")
    assert not oc.RESTORE_FILE.exists()


def test_the_second_time_it_reuses_our_profile(ctl):
    obs = ctl._client = FakeOBS()
    ctl.use_own_setup()
    ctl.restore_user_setup()
    obs.calls.clear()
    ctl.use_own_setup()
    assert "CreateProfile" not in obs.calls and "SetCurrentProfile" in obs.calls
    assert obs.profile == "R6Analyzer"


def test_a_pc_already_set_up_for_r6_is_left_alone(ctl):
    obs = ctl._client = FakeOBS(collection="Practice", scenes=("R6_Comms", "R6_Game"))
    assert ctl.use_own_setup() is False
    assert (obs.profile, obs.collection) == ("Untitled", "Practice")
    assert ctl.restore_user_setup() is True and obs.profile == "Untitled"


def test_it_never_switches_while_obs_is_recording(ctl):
    obs = ctl._client = FakeOBS(recording=True)
    assert ctl.use_own_setup() is False
    assert obs.profile == "Untitled"


def test_switching_back_waits_for_the_recording_to_stop(ctl):
    obs = ctl._client = FakeOBS()
    ctl.use_own_setup()
    obs.recording = True
    assert ctl.restore_user_setup() is False and obs.profile == "R6Analyzer"
    obs.recording = False
    assert ctl.restore_user_setup() is True and obs.profile == "Untitled"


def test_after_a_crash_the_next_run_still_knows_what_to_switch_back_to(ctl):
    obs = ctl._client = FakeOBS()
    ctl.use_own_setup()
    fresh = oc.OBSController()          # the app died mid-session and started again
    fresh._connected = True
    fresh._client = obs
    assert fresh.use_own_setup() is True
    assert fresh.restore_user_setup() is True
    assert (obs.profile, obs.collection) == ("Untitled", "Untitled")
