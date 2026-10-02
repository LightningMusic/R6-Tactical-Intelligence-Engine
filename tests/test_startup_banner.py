"""The startup banner goes into container logs, so it must never contain the API key."""
from server import main as server_main
from server.config import server_settings


def test_banner_never_prints_the_api_token(monkeypatch, capsys):
    secret = "S3cretTokenValueThatMustNeverAppearInLogs_0123456789"
    monkeypatch.setattr(server_settings, "API_TOKEN_PLAINTEXT", secret)
    monkeypatch.setattr(server_settings, "API_TOKEN_HASH", "x" * 64)
    monkeypatch.setattr(server_settings, "TOKEN_SOURCE", "server_config.json")
    server_main._print_startup_banner()
    out = capsys.readouterr().out
    assert secret not in out
    assert "not shown" in out
