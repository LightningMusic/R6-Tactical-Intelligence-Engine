"""
OBS password rotation (this session's fix).

Previously, OBSController.connect() only ever read the legacy single
obs_host/obs_port/obs_password settings keys -- which nothing in the app
ever synced from the "active" OBS profile picked in Settings. So switching
profiles in Settings and clicking "Use This Profile" silently did nothing
useful for connect(); it kept using whatever the legacy keys happened to
hold, which is exactly why manually switching profiles "didn't work most
of the time."

connect() now reads settings.get_obs_profiles() directly and rotates
through every saved profile's host/port/password until one connects,
trying the profile recorded as "active" first, then falling through the
rest, and only reporting failure once every saved profile has been tried.
It also promotes whichever profile actually worked to be the new active
profile, so the next connect() on the same PC succeeds on the first try.
"""
from typing import Optional

import pytest

from integration import obs_controller as obs_controller_module
from integration.obs_controller import OBSController


class _FakeResponse:
    """Minimal stand-in for an obswebsocket response object."""
    def getScenes(self):
        return []


class _FakeObsws:
    """Stands in for obswebsocket.obsws. Connects successfully only when
    constructed with `correct_password`; otherwise raises, mirroring
    obswebsocket's real behavior of raising ConnectionFailure on a bad
    password (it doesn't use a distinct exception type for auth vs. other
    failures, so callers can't tell them apart -- connect() doesn't try
    to)."""
    correct_password = "right-pw"
    attempts: list = []  # (host, port, password) for every construction

    def __init__(self, host, port, password):
        self.host, self.port, self.password = host, port, password
        _FakeObsws.attempts.append((host, port, password))

    def connect(self):
        if self.password != _FakeObsws.correct_password:
            raise RuntimeError("Empty response to Identify, password may be inconnect.")

    def call(self, request):
        return _FakeResponse()

    def disconnect(self):
        pass


class _FakeSettings:
    """Lightweight stand-in for app.config's settings singleton, isolated
    from the real data/settings.json so tests never touch disk."""
    def __init__(self, profiles, active_idx=0):
        self._profiles = profiles
        self._active_idx = active_idx
        self.saved = False
        # Legacy fallback properties (only used if profiles is empty)
        self.OBS_HOST = "localhost"
        self.OBS_PORT = 4455
        self.OBS_PASSWORD = ""

    def get_obs_profiles(self):
        return self._profiles

    def get(self, key):
        if key == "obs_active_profile":
            return self._active_idx
        return None

    def set_obs_profiles(self, profiles, active_idx=0):
        self._profiles = list(profiles)
        self._active_idx = int(active_idx)

    def save(self):
        self.saved = True


@pytest.fixture(autouse=True)
def _patch_obs_dependencies(monkeypatch):
    """Every test gets: OBS already 'running' (skip launch), the fake
    obsws client instead of the real websocket library, and no real
    sleeps slowing the retry-round loop down."""
    monkeypatch.setattr(obs_controller_module, "_obs_is_running", lambda: True)
    monkeypatch.setattr(obs_controller_module.obswebsocket, "obsws", _FakeObsws)
    monkeypatch.setattr(obs_controller_module.time, "sleep", lambda *_: None)
    _FakeObsws.attempts = []
    yield


def _profiles(*passwords, host="localhost", port=4455):
    return [
        {"name": f"PC {i+1}", "host": host, "port": port, "password": pw}
        for i, pw in enumerate(passwords)
    ]


def test_connects_immediately_when_active_profile_password_is_correct(monkeypatch):
    profiles = _profiles("wrong-1", "right-pw", "wrong-2")
    fake_settings = _FakeSettings(profiles, active_idx=1)
    monkeypatch.setattr(obs_controller_module, "settings", fake_settings)

    controller = OBSController()
    assert controller.connect() is True
    assert controller.is_connected is True
    # Only the active (correct) profile should have been tried at all.
    assert _FakeObsws.attempts == [("localhost", 4455, "right-pw")]
    # Active profile didn't change, so nothing needed to be persisted.
    assert fake_settings.saved is False


def test_rotates_past_wrong_active_profile_to_find_the_right_one(monkeypatch):
    profiles = _profiles("wrong-1", "wrong-2", "right-pw")
    fake_settings = _FakeSettings(profiles, active_idx=0)
    monkeypatch.setattr(obs_controller_module, "settings", fake_settings)

    controller = OBSController()
    assert controller.connect() is True
    assert controller.is_connected is True

    # Tried active profile 0 first, then fell through in order until the
    # correct one (index 2) worked -- one attempt per candidate, no
    # wasted repeats on the same wrong password.
    assert _FakeObsws.attempts == [
        ("localhost", 4455, "wrong-1"),
        ("localhost", 4455, "wrong-2"),
        ("localhost", 4455, "right-pw"),
    ]

    # The winning profile is promoted to active and persisted, so the
    # next connect() on this PC succeeds on the first try.
    assert fake_settings._active_idx == 2
    assert fake_settings.saved is True


def test_all_profiles_wrong_reports_failure_without_crashing(monkeypatch):
    profiles = _profiles("wrong-1", "wrong-2")
    fake_settings = _FakeSettings(profiles, active_idx=0)
    monkeypatch.setattr(obs_controller_module, "settings", fake_settings)

    controller = OBSController()
    assert controller.connect() is False
    assert controller.is_connected is False
    # Every saved profile was tried at least once per round.
    tried = {(h, p, pw) for h, p, pw in _FakeObsws.attempts}
    assert tried == {("localhost", 4455, "wrong-1"), ("localhost", 4455, "wrong-2")}
    # No profile worked, so nothing should be promoted/persisted.
    assert fake_settings.saved is False


def test_duplicate_credentials_across_profiles_are_not_retried_twice_per_round(monkeypatch):
    # Two profiles that happen to share the exact same host/port/password
    # (e.g. copy-pasted profiles) shouldn't cost an extra wasted attempt.
    profiles = _profiles("same-pw", "same-pw", "right-pw")
    fake_settings = _FakeSettings(profiles, active_idx=0)
    monkeypatch.setattr(obs_controller_module, "settings", fake_settings)

    controller = OBSController()
    assert controller.connect() is True
    assert _FakeObsws.attempts == [
        ("localhost", 4455, "same-pw"),
        ("localhost", 4455, "right-pw"),
    ]


def test_no_saved_profiles_falls_back_to_legacy_settings_keys(monkeypatch):
    fake_settings = _FakeSettings(profiles=[], active_idx=0)
    fake_settings.OBS_HOST = "localhost"
    fake_settings.OBS_PORT = 4455
    fake_settings.OBS_PASSWORD = "right-pw"
    monkeypatch.setattr(obs_controller_module, "settings", fake_settings)

    controller = OBSController()
    assert controller.connect() is True
    assert _FakeObsws.attempts == [("localhost", 4455, "right-pw")]
