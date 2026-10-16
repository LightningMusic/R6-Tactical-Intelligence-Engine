"""
A teammate's own analyzer (/solo) and their sensitivity plug-in (/plugin): what a player key can and can't
reach, upload checks, the per-player numbers, and settings snapshots.
"""
import hashlib
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from models.round import Round
from models.round_events import KillEvent, RoundEvents
from server.config import server_settings
from server.main import app
from server.services import solo

API = "main-token"


def _h(token):
    return hashlib.sha256(token.encode()).hexdigest()


def _auth(token):
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
def server(tmp_path, monkeypatch):
    d = tmp_path / "server_data"
    for attr, path in (("DATA_DIR", d), ("DATABASE_PATH", d / "server_matches.db"), ("UPLOADS_DIR", d / "uploads"),
                       ("WORK_DIR", d / "work"), ("REPORTS_DIR", d / "reports"), ("LOGS_DIR", d / "logs"),
                       ("VOICE_DIR", d / "voice"), ("COMMS_DIR", d / "comms"),
                       ("MATCHES_DB_PATH", d / "no_matches.db")):
        monkeypatch.setattr(server_settings, attr, path)
    monkeypatch.setattr(server_settings, "API_TOKEN_HASH", _h(API))
    monkeypatch.setattr(server_settings, "VOICE_TOKEN_HASH", _h("voice-token"))
    monkeypatch.setattr(server_settings, "PUBLIC_URL", "https://example.test")
    server_settings.ensure_directories()
    from server.database import ServerDatabase
    from server.repositories import ServerRepository
    db = ServerDatabase(d / "server_matches.db")
    for mod in ("server.repositories", "server.services.comms_service", "server.invites", "server.players",
                "server.services.solo", "server.roster"):
        monkeypatch.setattr(f"{mod}.server_db", db)
    monkeypatch.setattr("server.api.v1.repo", ServerRepository(db))
    monkeypatch.setattr(solo, "start_worker", lambda: None)      # tests run the reader themselves
    from server.api import solo as solo_api
    solo_api._calls.clear()
    with TestClient(app) as client:
        yield client, db


def _key(client, username="K3nyooo"):
    r = client.post("/api/v1/players", headers=_auth(API), json={"username": username, "label": "Hector"})
    assert r.status_code == 200, r.text
    assert r.json()["solo_url"].startswith("https://example.test/solo#ply_")
    return r.json()


def _fill(path: str) -> str:
    import re
    return re.sub(r"\{[^}]+\}", "x", path)


def test_a_player_key_is_refused_everywhere_but_its_own_routes(server):
    client, _ = server
    tok = _key(client)["token"]
    checked = 0
    app.openapi_schema = None
    for path, ops in app.openapi()["paths"].items():
        if not path.startswith("/api/v1") or path == "/api/v1/health":
            continue
        if path.startswith("/api/v1/solo") or path.startswith("/api/v1/plugin"):
            continue
        for method in sorted(m.upper() for m in ops):
            r = client.request(method, _fill(path), headers=_auth(tok))
            assert r.status_code in (401, 403), f"{method} {path} answered {r.status_code} to a player key"
            checked += 1
    assert checked > 20
    # The host's key-making routes in particular:
    assert client.get("/api/v1/players", headers=_auth(tok)).status_code == 401
    assert client.post("/api/v1/players", headers=_auth(tok), json={"username": "Someone"}).status_code == 401


def test_a_player_key_sees_only_its_own_player(server):
    client, _ = server
    tok = _key(client)["token"]
    assert client.get("/api/v1/solo/matches", headers=_auth(tok)).status_code == 200
    assert client.get("/api/v1/solo/matches?player=Teddy_Dance", headers=_auth(tok)).status_code == 403
    assert client.get("/api/v1/plugin/performance?player=Teddy_Dance", headers=_auth(tok)).status_code == 403
    me = client.get("/api/v1/solo/me", headers=_auth(tok)).json()
    assert me == {"kind": "player", "player": "K3nyooo", "players": None}


def test_the_host_key_names_a_player_and_other_keys_are_refused(server):
    client, _ = server
    _key(client)
    assert client.get("/api/v1/solo/matches", headers=_auth(API)).status_code == 400
    assert client.get("/api/v1/solo/matches?player=K3nyooo", headers=_auth(API)).status_code == 200
    assert client.get("/api/v1/solo/me", headers=_auth(API)).json()["players"] == ["K3nyooo"]
    for bad in ("voice-token", "inv_12345678_nope", "ply_12345678_nope", ""):
        assert client.get("/api/v1/solo/matches", headers=_auth(bad)).status_code in (401, 403)


def test_a_revoked_key_stops_working(server):
    client, _ = server
    k = _key(client)
    assert client.delete(f"/api/v1/players/{k['key_id']}", headers=_auth(API)).status_code == 200
    assert client.get("/api/v1/solo/matches", headers=_auth(k["token"])).status_code == 401


def _send(client, tok, folder="Match-2026-10-14_19-00-00", files=None, setup=None):
    files = files or [("files", ("Match-2026-10-14_19-00-00-R01.rec", b"x" * 100))]
    data = {"folder": folder}
    if setup:
        data["setup"] = setup
    return client.post("/api/v1/solo/upload", headers=_auth(tok), data=data, files=files)


def test_uploads_take_only_replay_files_with_safe_names(server):
    client, _ = server
    tok = _key(client)["token"]
    assert _send(client, tok, files=[("files", ("notes.txt", b"hi"))]).status_code == 400
    assert _send(client, tok, files=[("files", ("../../evil.rec", b"hi"))]).status_code == 400
    assert _send(client, tok, folder="../escape").status_code == 400
    assert _send(client, tok, files=[("files", ("a.rec", b""))]).status_code == 400
    assert _send(client, tok, setup="bad/setup").status_code == 400
    r = _send(client, tok, setup="school")
    assert r.status_code == 200 and r.json()["status"] == "queued"
    assert _send(client, tok).json()["status"] == "known"
    known = client.post("/api/v1/solo/known", headers=_auth(tok), json={"folders": ["Match-2026-10-14_19-00-00", "Other"]})
    assert known.json()["known"] == {"Match-2026-10-14_19-00-00": "queued"}


def _ke(t, killer, victim, opening=False, trade=False, hs=False):
    return KillEvent(time_str="", time_sec=t, killer=killer, victim=victim, headshot=hs, killer_team_index=0,
                     victim_team_index=1, is_trade=trade, is_opening=opening)


def _track(x, y=0.0, z=0.0):
    return [[float(t), x, y, z, None] for t in range(0, 200)]


def _result():
    me = {"username": "K3nyooo", "operator": "Lion", "operator_game_id": None, "role_name": "", "kills": 0,
          "deaths": 1, "assists": 0, "teamIndex": 0, "is_our_team": True}
    mate = {**me, "username": "Mate", "deaths": 0, "operator": "Sledge"}
    enemy = {**me, "username": "Enemy", "teamIndex": 1, "is_our_team": False, "deaths": 1}
    r1 = Round(round_id=None, match_id=None, round_number=1, side="attack", site="", outcome="loss", resources=None,
               raw_player_stats=[{**me, "side": "attack"}, {**mate, "side": "attack"}, {**enemy, "side": "defense"}],
               round_events=RoundEvents(kills=[_ke(55.0, "Enemy", "K3nyooo", opening=True)]),
               positions={"players": {"K3nyooo": {"team": 0, "pts": _track(0.0)}, "Mate": {"team": 0, "pts": _track(30.0)},
                                      "Enemy": {"team": 1, "pts": _track(5.0)}},
                          "died": {"K3nyooo": 55.0},
                          "drones": [{"t": 50.0, "prep": False, "who": "K3nyooo", "path_m": 20},
                                     {"t": 20.0, "prep": True, "who": None, "path_m": 20}]})
    r2 = Round(round_id=None, match_id=None, round_number=2, side="defense", site="", outcome="win", resources=None,
               raw_player_stats=[{**me, "deaths": 0, "operator": "Maestro", "side": "defense"},
                                 {**mate, "side": "defense"}, {**enemy, "side": "attack"}],
               round_events=RoundEvents(kills=[_ke(100.0, "K3nyooo", "Enemy", opening=True, hs=True)]),
               positions={"players": {"K3nyooo": {"team": 0, "pts": _track(0.0)}}, "died": {"Enemy": 100.0},
                          "walls": {"reinforcements": [{"t": 10, "who": "K3nyooo"}, {"t": 12, "who": "Mate"}]}})
    return SimpleNamespace(rounds=[r1, r2], map_name="BorderY10", map_game_id=None,
                           played_at="2026-10-14T00:04:01Z", score_us=1, score_them=1)


def test_the_numbers_come_from_the_players_point_of_view(server):
    rep = solo.player_report(_result(), "k3nyooo")
    assert rep["map"] == "Border" and rep["score"] == [1, 1]
    r1, r2 = rep["rounds"]
    assert r1["death"] == {"killer": "Enemy", "clock": "2:50", "into_action": 10.0, "opening": True, "traded": False,
                           "spawn_peek": True}
    assert r1["nearest_mate_m"] == 30.0 and r1["drones"] == 1 and r1["drones_before_first_death"] == 1
    assert r2["death"] is None and r2["won"] and r2["reinforced"] == 1 and r2["operator"] == "Maestro"
    assert r2["kills"] == [{"victim": "Enemy", "clock": "2:05", "headshot": True, "opening": True}]
    t = rep["totals"]
    assert (t["kills"], t["deaths"], t["headshots"], t["opening_kills"], t["opening_deaths"]) == (1, 1, 1, 1, 1)
    assert (t["spawn_peeked"], t["deaths_isolated"], t["deaths_checked"], t["survived"]) == (1, 1, 1, 1)
    assert rep["killed_by"] == [["Enemy", 1]] or rep["killed_by"] == [("Enemy", 1)]
    assert solo.player_report(_result(), "NotInIt") is None


def test_an_upload_is_read_and_its_files_removed(server, monkeypatch):
    client, _ = server
    tok = _key(client)["token"]
    assert _send(client, tok).status_code == 200
    import integration.rec_importer as ri

    class FakeImporter:
        def __init__(self, *a, **k):
            pass

        def import_match_folder(self, folder):
            assert any(folder.glob("*.rec"))
            return _result()
    monkeypatch.setattr(ri, "RecImporter", FakeImporter)
    job = solo._claim()
    solo._process(job["owner"], job["match_key"])
    assert not solo._work_dir(job["owner"], job["match_key"]).exists()
    ms = client.get("/api/v1/solo/matches", headers=_auth(tok)).json()["matches"]
    assert ms[0]["status"] == "done" and ms[0]["map"] == "Border" and ms[0]["totals"]["kills"] == 1
    detail = client.get("/api/v1/solo/matches/Match-2026-10-14_19-00-00", headers=_auth(tok)).json()
    assert len(detail["report"]["rounds"]) == 2
    tr = client.get("/api/v1/solo/trends", headers=_auth(tok)).json()
    assert tr["overall"]["rounds"] == 2 and set(tr["by"]["side"]) == {"attack", "defense"}
    perf = client.get("/api/v1/plugin/performance", headers=_auth(tok)).json()["matches"]
    assert perf[0]["source"] == "solo" and perf[0]["kills"] == 1


def _snap(client, tok, **over):
    body = {"setup": "home", "captured_at": "2026-10-14T23:02:11Z", "game": "r6siege", "mouse_dpi": 800,
            "settings": {"MouseYawSensitivity": "12", "MouseSensitivityMultiplierUnit": "0.02"}, "tool": "test"}
    body.update(over)
    return client.post("/api/v1/plugin/sensitivity/snapshots", headers=_auth(tok), json=body)


def test_snapshots_keep_only_settings_numbers(server):
    client, _ = server
    tok = _key(client)["token"]
    assert _snap(client, tok).status_code == 201
    assert _snap(client, tok, captured_at="2026-10-14T23:30:00Z").json()["stored"] is False   # same settings
    assert _snap(client, tok, settings={"Path": "C:\\Users\\hector\\x.ini"}).status_code == 400
    assert _snap(client, tok, captured_at="2026-10-14 23:02").status_code == 400            # no time zone
    assert _snap(client, tok, mouse_dpi=5).status_code == 400
    assert _snap(client, tok, settings={}).status_code == 400
    assert _snap(client, tok, setup="school", mouse_dpi=1600, captured_at="2026-10-15T01:00:00Z").status_code == 201
    snaps = client.get("/api/v1/plugin/sensitivity/snapshots", headers=_auth(tok)).json()["snapshots"]
    assert len(snaps) == 3 and snaps[0]["setup"] == "school"
    setups = {s["name"]: s for s in client.get("/api/v1/solo/setups", headers=_auth(tok)).json()["setups"]}
    assert setups["home"]["relative_speed"] == 1.0 and setups["school"]["relative_speed"] == 2.0


def test_a_match_gets_the_setup_of_the_latest_snapshot_before_it(server):
    client, _ = server
    tok = _key(client)["token"]
    _snap(client, tok, setup="school", captured_at="2026-10-13T23:50:00Z")
    assert solo.inferred_setup("K3nyooo", "2026-10-14T00:04:01Z") == "school"
    assert solo.inferred_setup("K3nyooo", "2026-10-13T20:00:00Z") is None        # before any snapshot
    assert solo.inferred_setup("K3nyooo", "2026-10-15T20:00:00Z") is None        # too long after


def test_the_replay_clock_is_converted_with_the_uploaders_time_zone():
    # r6-dissect writes the recording PC's local time with a "Z" it hasn't earned; a US Central browser sends 300.
    assert solo.replay_time_utc("2026-10-13T18:33:32Z", 300) == "2026-10-13T23:33:32+00:00"
    assert solo.replay_time_utc("2026-10-13T18:33:32Z", None) == "2026-10-13T18:33:32"
    assert solo.replay_time_utc(None, 300) is None


def test_rounds_are_grouped_by_how_much_of_the_team_was_there(server, monkeypatch):
    client, _ = server
    tok = _key(client)["token"]
    _send(client, tok)
    import integration.rec_importer as ri

    class FakeImporter:
        def __init__(self, *a, **k):
            pass

        def import_match_folder(self, folder):
            return _result()
    monkeypatch.setattr(ri, "RecImporter", FakeImporter)
    job = solo._claim()
    solo._process(job["owner"], job["match_key"])
    # Without a roster nobody can say who's "the team".
    groups = client.get("/api/v1/solo/compare", headers=_auth(tok)).json()["groups"]
    assert [g["name"] for g in groups] == ["Own uploads, teammates unknown"]
    client.put("/api/v1/roster", headers=_auth(API), json={"roster": [{"username": "K3nyooo"}, {"username": "Mate"}]})
    c = client.get("/api/v1/solo/compare", headers=_auth(tok)).json()
    assert [(g["name"], g["matches"], g["summary"]["rounds"]) for g in c["groups"]] == [("2-3 of the team", 1, 2)]
    assert c["differences"] == []                       # no full-team rounds to compare against
    assert client.get("/api/v1/solo/compare?player=Mate", headers=_auth(tok)).status_code == 403
    assert client.get("/api/v1/solo/me", headers=_auth(API)).json()["players"] == ["K3nyooo", "Mate"]
