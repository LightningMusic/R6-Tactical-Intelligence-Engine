"""The Start self-check reports what would ruin a recording: no OBS, Discord closed, a missing comms source, or
the wrong recording format."""
import pytest

pytest.importorskip("obswebsocket")
import integration.obs_controller as oc  # noqa: E402


class FakeOBS:
    def __init__(self, scene_items=("Discord_Audio", "My_Mic"), fmt="mkv"):
        self.scene_items = list(scene_items)
        self.fmt = fmt

    def call(self, req):
        name = req.name
        if name == "GetSceneItemList":
            req.datain = {"sceneItems": [{"sourceName": s} for s in self.scene_items]}
        elif name == "GetProfileParameter":
            req.datain = {"parameterValue": self.fmt}
        return req


@pytest.fixture
def ctl(monkeypatch):
    monkeypatch.setattr(oc, "_discord_running", lambda: True)
    c = oc.OBSController()
    c._connected = True
    c._client = FakeOBS()
    return c


def _by_ok(checks):
    return [c["msg"] for c in checks if not c["ok"]]


def test_all_good(ctl):
    assert _by_ok(ctl.session_preflight()) == []
    assert all(c["level"] == "ok" for c in ctl.session_preflight())


def test_discord_closed_is_flagged(ctl, monkeypatch):
    monkeypatch.setattr(oc, "_discord_running", lambda: False)
    bad = ctl.session_preflight()
    assert any("Discord does not appear to be running" in c["msg"] and c["level"] == "error" for c in bad)


def test_a_missing_comms_source_is_an_error(ctl):
    ctl._client = FakeOBS(scene_items=["My_Mic"])
    bad = [c for c in ctl.session_preflight() if not c["ok"]]
    assert any("Discord audio" in c["msg"] and c["level"] == "error" for c in bad)


def test_wrong_format_is_only_a_warning(ctl):
    ctl._client = FakeOBS(fmt="hybrid_mp4")
    probs = [c for c in ctl.session_preflight() if not c["ok"]]
    assert len(probs) == 1 and probs[0]["level"] == "warn" and "Matroska" in probs[0]["msg"]


def test_no_obs_connection_short_circuits(ctl):
    ctl._connected = False
    checks = ctl.session_preflight()
    assert len(checks) == 1 and not checks[0]["ok"] and "not connected" in checks[0]["msg"]
