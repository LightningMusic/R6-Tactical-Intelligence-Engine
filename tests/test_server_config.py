"""
Tests for server/config.py's token provisioning — the piece that lets the
packaged R6Server.exe be double-clicked with zero manual setup instead of
requiring an environment variable to be set by hand first.
"""
import hashlib
import json
import pytest

from server.config import ServerSettings


def _clear_token_env(monkeypatch):
    monkeypatch.delenv("R6_SERVER_API_TOKEN", raising=False)
    monkeypatch.delenv("R6_SERVER_API_TOKEN_HASH", raising=False)


def test_env_var_raw_token_takes_precedence_and_skips_provisioning(tmp_path, monkeypatch):
    _clear_token_env(monkeypatch)
    monkeypatch.setenv("R6_SERVER_API_TOKEN", "my_plain_token")
    monkeypatch.setenv("R6_SERVER_DATA_DIR", str(tmp_path / "srv"))

    s = ServerSettings()

    assert s.API_TOKEN_PLAINTEXT == "my_plain_token"
    assert s.API_TOKEN_HASH == hashlib.sha256(b"my_plain_token").hexdigest().lower()
    assert "environment" in s.TOKEN_SOURCE
    assert not s.CONFIG_FILE.exists(), "env-var token should never touch server_config.json"


def test_env_var_hash_wins_over_raw_and_plaintext_is_unknown(tmp_path, monkeypatch):
    monkeypatch.setenv("R6_SERVER_API_TOKEN", "raw_token")
    monkeypatch.setenv("R6_SERVER_API_TOKEN_HASH", "deadbeef" * 8)
    monkeypatch.setenv("R6_SERVER_DATA_DIR", str(tmp_path / "srv"))

    s = ServerSettings()

    assert s.AMBIGUOUS_TOKEN_CONFIG is True
    assert s.API_TOKEN_HASH == "deadbeef" * 8
    assert s.API_TOKEN_PLAINTEXT is None, "a supplied hash can't be reversed into a plaintext"


def test_self_provisions_token_on_first_run(tmp_path, monkeypatch):
    _clear_token_env(monkeypatch)
    data_dir = tmp_path / "srv"
    monkeypatch.setenv("R6_SERVER_DATA_DIR", str(data_dir))

    s = ServerSettings()

    assert s.CONFIG_FILE.exists()
    saved = json.loads(s.CONFIG_FILE.read_text(encoding="utf-8"))
    assert saved["api_token"]
    assert s.API_TOKEN_PLAINTEXT == saved["api_token"]
    assert s.API_TOKEN_HASH == hashlib.sha256(saved["api_token"].encode()).hexdigest().lower()
    assert "newly generated" in s.TOKEN_SOURCE


def test_reuses_existing_token_on_subsequent_launch(tmp_path, monkeypatch):
    _clear_token_env(monkeypatch)
    data_dir = tmp_path / "srv"
    data_dir.mkdir(parents=True)
    config_file = data_dir / "server_config.json"
    config_file.write_text(json.dumps({"api_token": "already_here_token"}), encoding="utf-8")
    monkeypatch.setenv("R6_SERVER_DATA_DIR", str(data_dir))

    s = ServerSettings()

    assert s.API_TOKEN_PLAINTEXT == "already_here_token"
    assert s.API_TOKEN_HASH == hashlib.sha256(b"already_here_token").hexdigest().lower()
    assert s.TOKEN_SOURCE == "server_config.json"
    # Must not have overwritten the file with a freshly generated token.
    assert json.loads(config_file.read_text())["api_token"] == "already_here_token"


def test_two_launches_in_a_row_produce_the_same_token(tmp_path, monkeypatch):
    """Regression guard for the exact scenario this feature exists for:
    double-click the exe, copy the token into the client, close the server,
    double-click it again later — the token must not change out from under
    an already-configured client."""
    _clear_token_env(monkeypatch)
    monkeypatch.setenv("R6_SERVER_DATA_DIR", str(tmp_path / "srv"))

    first = ServerSettings()
    second = ServerSettings()

    assert first.API_TOKEN_PLAINTEXT == second.API_TOKEN_PLAINTEXT
    assert first.API_TOKEN_HASH == second.API_TOKEN_HASH


def test_falls_closed_when_data_dir_cannot_be_created(tmp_path, monkeypatch):
    _clear_token_env(monkeypatch)
    # Create a plain file where the data dir needs to be a directory, so
    # mkdir() raises instead of succeeding.
    blocker = tmp_path / "srv_blocked"
    blocker.write_text("not a directory")
    monkeypatch.setenv("R6_SERVER_DATA_DIR", str(blocker))

    s = ServerSettings()

    assert s.API_TOKEN_HASH is None
    assert "error" in s.TOKEN_SOURCE


# ── Milestone 5: permanent public address (Tailscale Funnel URL) ──────────


def test_public_url_defaults_to_empty_when_unset(tmp_path, monkeypatch):
    _clear_token_env(monkeypatch)
    monkeypatch.delenv("R6_SERVER_PUBLIC_URL", raising=False)
    monkeypatch.setenv("R6_SERVER_DATA_DIR", str(tmp_path / "srv"))

    s = ServerSettings()

    assert s.PUBLIC_URL == ""


def test_env_var_public_url_overrides_file(tmp_path, monkeypatch):
    _clear_token_env(monkeypatch)
    data_dir = tmp_path / "srv"
    data_dir.mkdir(parents=True)
    (data_dir / "server_config.json").write_text(
        json.dumps({"api_token": "tok", "public_url": "https://from-file.example.ts.net"}),
        encoding="utf-8",
    )
    monkeypatch.setenv("R6_SERVER_DATA_DIR", str(data_dir))
    monkeypatch.setenv("R6_SERVER_PUBLIC_URL", "https://from-env.example.ts.net")

    s = ServerSettings()

    assert s.PUBLIC_URL == "https://from-env.example.ts.net"
    monkeypatch.delenv("R6_SERVER_PUBLIC_URL", raising=False)


def test_public_url_read_from_config_file_regardless_of_token_source(tmp_path, monkeypatch):
    """A public_url saved to server_config.json must still be picked up even
    when the API token itself came from an env var (a scripted/CI deployment
    that never touches server_config.json for its token) — the two are
    tracked completely independently."""
    monkeypatch.delenv("R6_SERVER_PUBLIC_URL", raising=False)
    data_dir = tmp_path / "srv"
    data_dir.mkdir(parents=True)
    (data_dir / "server_config.json").write_text(
        json.dumps({"public_url": "https://from-file.example.ts.net"}), encoding="utf-8"
    )
    monkeypatch.setenv("R6_SERVER_DATA_DIR", str(data_dir))
    monkeypatch.setenv("R6_SERVER_API_TOKEN", "env_token")

    s = ServerSettings()

    assert s.API_TOKEN_PLAINTEXT == "env_token"
    assert s.PUBLIC_URL == "https://from-file.example.ts.net"
    monkeypatch.delenv("R6_SERVER_API_TOKEN", raising=False)


def test_save_public_url_preserves_existing_token(tmp_path, monkeypatch):
    _clear_token_env(monkeypatch)
    monkeypatch.delenv("R6_SERVER_PUBLIC_URL", raising=False)
    monkeypatch.setenv("R6_SERVER_DATA_DIR", str(tmp_path / "srv"))

    s = ServerSettings()  # self-provisions a token first, like a real first run
    original_token = s.API_TOKEN_PLAINTEXT
    assert original_token

    s.save_public_url("https://my-server.tailnet.ts.net/")

    assert s.PUBLIC_URL == "https://my-server.tailnet.ts.net"  # trailing slash stripped
    saved = json.loads(s.CONFIG_FILE.read_text(encoding="utf-8"))
    assert saved["public_url"] == "https://my-server.tailnet.ts.net"
    assert saved["api_token"] == original_token, "saving the URL must not disturb the token"


def test_save_public_url_creates_file_if_missing(tmp_path, monkeypatch):
    _clear_token_env(monkeypatch)
    monkeypatch.delenv("R6_SERVER_PUBLIC_URL", raising=False)
    data_dir = tmp_path / "srv"
    monkeypatch.setenv("R6_SERVER_DATA_DIR", str(data_dir))
    monkeypatch.setenv("R6_SERVER_API_TOKEN", "env_token_only")  # skip token provisioning

    s = ServerSettings()
    assert not s.CONFIG_FILE.exists()

    s.save_public_url("https://brand-new.example.ts.net")

    assert s.CONFIG_FILE.exists()
    assert json.loads(s.CONFIG_FILE.read_text())["public_url"] == "https://brand-new.example.ts.net"
    monkeypatch.delenv("R6_SERVER_API_TOKEN", raising=False)
