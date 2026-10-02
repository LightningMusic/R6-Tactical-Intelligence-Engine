"""
The host's saved team list (pick-list for invite links): auth, validation, and
that a bad list never half-replaces a good one.
"""
import hashlib

import pytest
from fastapi.testclient import TestClient

from server.config import server_settings
from server.main import app

API = "main-token"
VOICE = "voice-token"


def _h(token):
    return hashlib.sha256(token.encode()).hexdigest()


def _auth(token):
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
def client(tmp_path, monkeypatch):
    d = tmp_path / "server_data"
    for attr, path in (("DATA_DIR", d), ("DATABASE_PATH", d / "server_matches.db"), ("UPLOADS_DIR", d / "uploads"),
                       ("WORK_DIR", d / "work"), ("REPORTS_DIR", d / "reports"), ("LOGS_DIR", d / "logs"),
                       ("VOICE_DIR", d / "voice"), ("COMMS_DIR", d / "comms")):
        monkeypatch.setattr(server_settings, attr, path)
    monkeypatch.setattr(server_settings, "API_TOKEN_HASH", _h(API))
    monkeypatch.setattr(server_settings, "VOICE_TOKEN_HASH", _h(VOICE))
    server_settings.ensure_directories()
    from server.database import ServerDatabase
    from server.repositories import ServerRepository
    db = ServerDatabase(d / "server_matches.db")
    monkeypatch.setattr("server.repositories.server_db", db)
    monkeypatch.setattr("server.services.comms_service.server_db", db)
    monkeypatch.setattr("server.invites.server_db", db)
    monkeypatch.setattr("server.roster.server_db", db)
    monkeypatch.setattr("server.api.v1.repo", ServerRepository(db))
    with TestClient(app) as c:
        yield c


def _put(client, entries, token=API):
    return client.put("/api/v1/roster", headers=_auth(token), json={"roster": entries})


def test_starts_empty(client):
    r = client.get("/api/v1/roster", headers=_auth(API))
    assert r.status_code == 200
    assert r.json() == {"roster": []}


def test_save_and_read_back_in_order(client):
    entries = [{"username": "zeta_1", "label": "Zed"}, {"username": "Alpha.2", "label": ""}]
    assert _put(client, entries).json()["roster"] == entries
    assert client.get("/api/v1/roster", headers=_auth(API)).json()["roster"] == entries


def test_saving_replaces_the_whole_list(client):
    _put(client, [{"username": "old_name", "label": ""}])
    _put(client, [{"username": "new_name", "label": "New"}])
    assert [e["username"] for e in client.get("/api/v1/roster", headers=_auth(API)).json()["roster"]] == ["new_name"]


def test_duplicates_are_dropped_case_insensitively(client):
    got = _put(client, [{"username": "Same_Name", "label": "a"}, {"username": "same_name", "label": "b"}]).json()["roster"]
    assert got == [{"username": "Same_Name", "label": "a"}]


def test_a_bad_name_rejects_the_save_and_keeps_the_old_list(client):
    good = [{"username": "keep_me", "label": ""}]
    _put(client, good)
    r = _put(client, [{"username": "fine_name", "label": ""}, {"username": "has space!", "label": ""}])
    assert r.status_code == 400
    assert client.get("/api/v1/roster", headers=_auth(API)).json()["roster"] == good


def test_too_many_names_rejected(client):
    r = _put(client, [{"username": f"player_{i:03d}", "label": ""} for i in range(51)])
    assert r.status_code == 400


@pytest.mark.parametrize("token", [VOICE, "wrong", ""])
def test_only_the_main_key_may_touch_the_list(client, token):
    assert client.get("/api/v1/roster", headers=_auth(token)).status_code in (401, 403)
    assert _put(client, [{"username": "nope_x", "label": ""}], token).status_code in (401, 403)
