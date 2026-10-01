"""
Milestone 5: the client's SERVER_URL/API_KEY settings fall back to whatever
build_and_deploy.bat baked into app/server_credentials.py, but only when the
user hasn't manually configured their own in Settings -> Remote Sync. This
is what lets a built R6Analyzer.exe connect to a configured server with zero
manual setup, while still letting a manual override (e.g. pointing a build
at a different/local server for testing) win.

Also covers scripts/embed_client_credentials.py's write/clear round trip,
since that's the mechanism that actually populates app/server_credentials.py
at build time and must restore it exactly afterward.
"""
import importlib
import sys
from pathlib import Path

import pytest

import app.config as config_module
import app.server_credentials as creds_module


REPO_ROOT = Path(__file__).parent.parent


@pytest.fixture
def fresh_settings():
    """A standalone _Settings instance, isolated from whatever data/settings.json
    happens to exist on disk in this checkout — we only care about the
    in-memory _data dict here, never save()."""
    s = config_module._Settings()
    s._data = dict(config_module._Settings.DEFAULTS)
    return s


def _set_embedded(monkeypatch, url: str, key: str):
    monkeypatch.setattr(creds_module, "EMBEDDED_SERVER_URL", url)
    monkeypatch.setattr(creds_module, "EMBEDDED_API_KEY", key)


def test_falls_back_to_embedded_when_no_manual_value(fresh_settings, monkeypatch):
    _set_embedded(monkeypatch, "https://built-in.example.ts.net", "embedded-key-123")

    assert fresh_settings.SERVER_URL == "https://built-in.example.ts.net"
    assert fresh_settings.API_KEY == "embedded-key-123"


def test_manual_value_overrides_embedded(fresh_settings, monkeypatch):
    _set_embedded(monkeypatch, "https://built-in.example.ts.net", "embedded-key-123")
    fresh_settings._data["server_url"] = "http://100.64.1.2:8000"
    fresh_settings._data["api_key"] = "manually-pasted-key"

    assert fresh_settings.SERVER_URL == "http://100.64.1.2:8000"
    assert fresh_settings.API_KEY == "manually-pasted-key"


def test_no_manual_no_embedded_is_empty(fresh_settings, monkeypatch):
    _set_embedded(monkeypatch, "", "")

    assert fresh_settings.SERVER_URL == ""
    assert fresh_settings.API_KEY == ""


def test_embedded_url_trailing_slash_is_stripped(fresh_settings, monkeypatch):
    _set_embedded(monkeypatch, "https://built-in.example.ts.net/", "k")

    assert fresh_settings.SERVER_URL == "https://built-in.example.ts.net"


def test_missing_credentials_module_degrades_to_empty(fresh_settings, monkeypatch):
    """If app/server_credentials.py somehow can't be imported (corrupted by a
    failed build step, e.g.), the client must still start up rather than
    crash — it just behaves as if nothing were embedded."""
    monkeypatch.setitem(sys.modules, "app.server_credentials", None)

    assert fresh_settings.SERVER_URL == ""
    assert fresh_settings.API_KEY == ""


# ── scripts/embed_client_credentials.py write/clear round trip ────────────


@pytest.fixture
def embed_script():
    import importlib.util

    spec = importlib.util.spec_from_file_location(
        "embed_client_credentials", REPO_ROOT / "scripts" / "embed_client_credentials.py"
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_clear_restores_exact_checked_in_placeholder(embed_script):
    on_disk = (REPO_ROOT / "app" / "server_credentials.py").read_text(encoding="utf-8")
    assert embed_script.PLACEHOLDER == on_disk, (
        "the checked-in app/server_credentials.py placeholder must stay "
        "byte-for-byte identical to what --clear regenerates, or every "
        "build+clear cycle would show a spurious git diff"
    )


def test_write_then_clear_round_trips_to_placeholder(embed_script, tmp_path, monkeypatch):
    fake_target = tmp_path / "server_credentials.py"
    fake_target.write_text(embed_script.PLACEHOLDER, encoding="utf-8")
    monkeypatch.setattr(embed_script, "CREDENTIALS_FILE", fake_target)

    class _FakeSettings:
        API_TOKEN_PLAINTEXT = "abc123token"
        PUBLIC_URL = "https://server.example.ts.net"
        TOKEN_SOURCE = "server_config.json"

    fake_server_config = type(sys)("server.config")
    fake_server_config.server_settings = _FakeSettings()
    monkeypatch.setitem(sys.modules, "server.config", fake_server_config)

    assert embed_script.write_real_values() == 0
    written = fake_target.read_text(encoding="utf-8")
    assert "abc123token" in written
    assert "https://server.example.ts.net" in written
    assert written != embed_script.PLACEHOLDER

    assert embed_script.clear() == 0
    assert fake_target.read_text(encoding="utf-8") == embed_script.PLACEHOLDER


def test_write_fails_loudly_with_no_api_token(embed_script, tmp_path, monkeypatch):
    fake_target = tmp_path / "server_credentials.py"
    fake_target.write_text(embed_script.PLACEHOLDER, encoding="utf-8")
    monkeypatch.setattr(embed_script, "CREDENTIALS_FILE", fake_target)

    class _FakeSettingsNoToken:
        API_TOKEN_PLAINTEXT = None
        PUBLIC_URL = ""
        TOKEN_SOURCE = "unconfigured"

    fake_server_config = type(sys)("server.config")
    fake_server_config.server_settings = _FakeSettingsNoToken()
    monkeypatch.setitem(sys.modules, "server.config", fake_server_config)

    assert embed_script.write_real_values() == 1
    # Must not have touched the file when it refuses to embed nothing.
    assert fake_target.read_text(encoding="utf-8") == embed_script.PLACEHOLDER
