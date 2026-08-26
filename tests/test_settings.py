import pytest
from app.config import settings


def test_settings_defaults():
    assert settings.ANALYSIS_MODE in ("local", "remote", "automatic")
    assert settings.SERVER_URL == ""
    assert settings.API_KEY == ""
    assert settings.UPLOAD_REPLAYS is True
    assert settings.UPLOAD_VOICE is False
    assert settings.UPLOAD_AUTOMATICALLY is True
    assert settings.UPLOAD_LATER_WHEN_OFFLINE is True
    assert settings.FALLBACK_TO_LOCAL_ANALYSIS is True
    assert settings.REQUEST_TIMEOUT_SECONDS == 30
    assert settings.MAX_UPLOAD_RETRIES == 5
    assert settings.CLIENT_NAME == "USB_Client"


def test_settings_updates_and_validation():
    settings.set_many({
        "analysis_mode": "remote",
        "server_url": "http://192.168.1.100:8000/",
        "api_key": "secret_token_123",
        "upload_replays": True,
        "upload_voice": True,
        "upload_automatically": False,
        "upload_later_when_offline": True,
        "fallback_to_local_analysis": False,
        "request_timeout_seconds": 60,
        "max_upload_retries": 10,
        "client_name": "Test_USB",
    })

    assert settings.ANALYSIS_MODE == "remote"
    assert settings.SERVER_URL == "http://192.168.1.100:8000"  # stripped trailing slash
    assert settings.API_KEY == "secret_token_123"
    assert settings.UPLOAD_REPLAYS is True
    assert settings.UPLOAD_VOICE is True
    assert settings.UPLOAD_AUTOMATICALLY is False
    assert settings.UPLOAD_LATER_WHEN_OFFLINE is True
    assert settings.FALLBACK_TO_LOCAL_ANALYSIS is False
    assert settings.REQUEST_TIMEOUT_SECONDS == 60
    assert settings.MAX_UPLOAD_RETRIES == 10
    assert settings.CLIENT_NAME == "Test_USB"

    # Revert to default for clean state
    settings.set_many({
        "analysis_mode": "local",
        "server_url": "",
        "api_key": "",
        "upload_voice": False,
        "upload_automatically": True,
        "fallback_to_local_analysis": True,
    })
    assert settings.ANALYSIS_MODE == "local"
