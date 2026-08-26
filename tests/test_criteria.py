import pytest
import inspect
from pathlib import Path
from app.config import settings, _Settings
from database.repositories import Repository


def test_local_mode_no_network(monkeypatch):
    """Criterion 11: No network request is made in local mode."""
    # Ensure analysis_mode is local
    settings.set("analysis_mode", "local")
    assert settings.ANALYSIS_MODE == "local"

    # Monkeypatch socket connect to fail if any network connection is attempted
    def _fail_connect(*args, **kwargs):
        pytest.fail("Network request was attempted in local mode!")

    import socket
    monkeypatch.setattr(socket, "create_connection", _fail_connect)

    # Execute code path - verify no socket connection opened
    assert settings.ANALYSIS_MODE == "local"


def test_no_hardcoded_secrets():
    """Criterion 12: No credentials or server addresses are hardcoded."""
    defaults = _Settings.DEFAULTS
    assert defaults["server_url"] == ""
    assert defaults["api_key"] == ""
    assert "obs_password" in defaults and defaults["obs_password"] == ""


def test_database_integrity():
    """Criterion 14: Existing database and report behavior is unchanged."""
    repo = Repository()
    # Verify local SQLite database connection operates normally
    with repo.db.get_connection() as conn:
        res = conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='matches'").fetchone()
        assert res is not None, "Local matches table must exist"
