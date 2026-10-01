"""
OBSController.ensure_comms_tracks against a fake OBS that behaves like the
user's real setup on 2026-09-29: OBS's global Desktop Audio and Mic/Aux on
every track, and a Discord_Audio source pointed at a bare "Discord.exe"
(which Application Audio Capture never matched -- the OBS log said "Failed
to find window"). The same routing was also run against a real portable OBS
32.1.1 with Discord open; see the 2026-09-30 notes in obs_controller.py.
"""
import json

import pytest

pytest.importorskip("obswebsocket")
import integration.obs_controller as oc  # noqa: E402


class FakeOBS:
    def __init__(self, fail_on=()):
        self.inputs = {"Desktop Audio": "wasapi_output_capture", "Mic/Aux": "wasapi_input_capture",
                       "Discord_Audio": "wasapi_process_output_capture", "Game Capture": "game_capture"}
        self.scene_items = {"R6_Comms": ["Discord_Audio"]}
        self.tracks = {n: {str(t): True for t in range(1, 7)} for n in self.inputs}
        self.settings = {"Discord_Audio": {"window": "Discord.exe"}}
        self.profile = {("Output", "Mode"): "Advanced", ("AdvOut", "RecTracks"): "3"}
        self.fail_on = set(fail_on)

    def call(self, req):
        d, name = req.dataout, req.name
        if name in self.fail_on:
            raise RuntimeError(f"{name} failed")
        if name == "GetSceneItemList":
            req.datain = {"sceneItems": [{"sourceName": s} for s in self.scene_items.get(d["sceneName"], [])]}
        elif name == "GetInputList":
            req.datain = {"inputs": [{"inputName": n, "inputKind": k} for n, k in self.inputs.items()]}
        elif name == "CreateInput":
            self.inputs[d["inputName"]] = d["inputKind"]
            self.scene_items.setdefault(d["sceneName"], []).append(d["inputName"])
            self.tracks[d["inputName"]] = {str(t): True for t in range(1, 7)}
            self.settings[d["inputName"]] = dict(d["inputSettings"])
        elif name == "CreateSceneItem":
            self.scene_items.setdefault(d["sceneName"], []).append(d["sourceName"])
        elif name == "SetInputSettings":
            self.settings.setdefault(d["inputName"], {}).update(d["inputSettings"])
        elif name == "GetSpecialInputs":
            req.datain = {"desktop1": "Desktop Audio", "desktop2": None, "mic1": "Mic/Aux",
                          "mic2": None, "mic3": None, "mic4": None}
        elif name == "SetInputAudioTracks":
            if self.inputs.get(d["inputName"]) == "game_capture":
                raise RuntimeError("no audio")          # video-only source
            self.tracks[d["inputName"]] = dict(d["inputAudioTracks"])
        elif name == "GetProfileParameter":
            req.datain = {"parameterValue": self.profile.get((d["parameterCategory"], d["parameterName"]))}
        elif name == "SetProfileParameter":
            self.profile[(d["parameterCategory"], d["parameterName"])] = d["parameterValue"]
        return req


def on(tracks):
    return sorted(int(t) for t, v in tracks.items() if v)


@pytest.fixture
def controller(tmp_path, monkeypatch):
    monkeypatch.setattr(oc, "TRACK_LAYOUT_FILE", tmp_path / "track_layouts.json")
    ctl = oc.OBSController()
    ctl._connected = True
    return ctl


def test_every_audio_source_is_routed(controller):
    obs = FakeOBS()
    controller._client = obs
    assert controller.ensure_comms_tracks("R6_Comms") is True
    assert on(obs.tracks["Discord_Audio"]) == [1]
    assert on(obs.tracks["My_Mic"]) == [2]
    assert on(obs.tracks["Desktop Audio"]) == [3]         # was on every track: Discord leaked into both
    assert on(obs.tracks["Mic/Aux"]) == [3]
    assert "My_Mic" in obs.scene_items["R6_Comms"]
    assert obs.settings["Discord_Audio"] == {"window": "Discord:Chrome_WidgetWin_1:Discord.exe", "priority": 2}
    assert obs.profile[("AdvOut", "RecTracks")] == "7"


def test_simple_output_mode_is_switched_to_advanced(controller):
    obs = FakeOBS()
    obs.profile[("Output", "Mode")] = "Simple"
    controller._client = obs
    controller.ensure_comms_tracks("R6_Comms")
    assert obs.profile[("Output", "Mode")] == "Advanced"


def test_layout_is_recorded_only_when_routing_worked(controller):
    controller._client = FakeOBS()
    controller.ensure_comms_tracks("R6_Comms")
    entries = json.loads(oc.TRACK_LAYOUT_FILE.read_text())
    assert entries[-1]["layout"] == {"team": 0, "self": 1, "other": 2}

    controller._client = FakeOBS(fail_on={"SetInputAudioTracks"})
    assert controller.ensure_comms_tracks("R6_Comms") is False
    assert json.loads(oc.TRACK_LAYOUT_FILE.read_text())[-1]["layout"] is None


def test_track_layout_for_a_recording(tmp_path):
    f = tmp_path / "layouts.json"
    oc.record_track_layout(1000.0, oc.TRACK_LAYOUT, f)
    oc.record_track_layout(50000.0, None, f)                   # later session: routing failed
    assert oc.track_layout_for(1006.0, f) == oc.TRACK_LAYOUT  # OBS names its file a few s later
    assert oc.track_layout_for(990.0, f) is None               # recording started before routing
    assert oc.track_layout_for(50010.0, f) is None             # failed routing wins for its session
    assert oc.track_layout_for(1000.0 + 13 * 3600, f) is None  # far too old to trust
    assert oc.track_layout_for(1000.0, tmp_path / "missing.json") is None
