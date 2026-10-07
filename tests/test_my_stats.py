"""
"My stats" on a player's invite link: their own numbers only, from what is already stored, and nothing
for any credential that isn't one player's link. The server is the host's gaming PC, so this must never
start analysis (the fake repo below would fail loudly if anything but reads were attempted).
"""
import hashlib
import json
import sqlite3
from datetime import datetime
from types import SimpleNamespace as NS

import pytest
from fastapi.testclient import TestClient

from server.config import server_settings
from server.main import app
from server.services.player_stats import player_stats

API, VOICE = "main-token", "voice-token"


def _h(t):
    return hashlib.sha256(t.encode()).hexdigest()


def stat(name, k, d, op="Lion"):
    return NS(player=NS(name=name), kills=k, deaths=d, assists=0, operator=NS(name=op))


def rnd(n, side, outcome, stats):
    return NS(round_number=n, side=side, outcome=outcome, player_stats=stats)


class Repo:
    """Matches in memory, round events and debrief text in a real SQLite file (as on the server)."""
    def __init__(self, path, matches, metrics):
        self.matches = matches
        c = sqlite3.connect(path)
        c.execute("create table derived_metrics (match_id int, metric_name text, metric_text text)")
        c.executemany("insert into derived_metrics values (?,?,?)", metrics)
        c.commit(); c.close()
        self.db = NS(get_connection=lambda: sqlite3.connect(path))

    def get_all_matches_full(self):
        return self.matches


def build(tmp_path):
    def m(mid, day, plan):
        return NS(match_id=mid, datetime_played=datetime(2026, 10, day, 18), map="Border",
                  rounds=[rnd(i, side, out, sts) for i, (side, out, sts) in enumerate(plan, 1)])
    ours = lambda h, e: [stat("Hector", *h), stat("Elijah", *e, op="Twitch"), stat("Enemy", 1, 1)]
    usual = [("attack", "win", ours((1, 1), (0, 1))), ("attack", "loss", ours((0, 1), (1, 1))),
             ("defense", "win", ours((1, 0), (1, 0))), ("defense", "loss", ours((0, 1), (0, 1)))]
    big = [("attack", "win", ours((3, 0), (0, 0)))] * 3 + [("defense", "loss", ours((0, 1), (0, 1)))]
    matches = [m(1, 1, usual), m(2, 2, usual), m(3, 3, usual), m(4, 4, usual), m(5, 6, big),
               m(9, 5, [("attack", "win", [stat("Hector", 5, 0), stat("Somebody", 0, 1)])])]   # Hector against us
    team = json.dumps(["Hector", "Elijah"])
    ev = lambda **kw: json.dumps({"kill_order": "elapsed", **kw})
    metrics = [(i, "our_players", team) for i in (1, 2, 3, 4, 5)] + [(9, "our_players", json.dumps(["Somebody"]))]
    metrics += [(5, "round_1_events", ev(first_blood_killer="Hector", first_blood_victim="Enemy",
                                         objective_attempts=[{"kind": "plant", "completed": True, "ours": True, "username": "Hector"}])),
                (5, "round_2_events", ev(clutch_player="Hector", clutch_kills=0)),
                (5, "round_3_events", json.dumps({"first_blood_killer": "Hector"})),     # stored before the fix: not counted
                (5, "round_4_events", ev(first_blood_killer="Enemy", first_blood_victim="Elijah")),
                (5, "ai_player_intel::Hector", "STRENGTH: opened rounds"),
                (5, "ai_player_intel::Elijah", "FOCUS: someone else's notes")]
    return Repo(tmp_path / "m.db", matches, metrics)


def test_a_player_gets_their_own_lines_newest_first(tmp_path):
    s = player_stats(build(tmp_path), "hector")
    assert [m["match_id"] for m in s["matches"]] == [5, 4, 3, 2, 1]          # not match 9 (played against us)
    latest = s["matches"][0]
    assert (latest["k"], latest["d"], latest["score_us"], latest["score_them"]) == (9, 1, 3, 1)
    assert latest["first_kills"] == 1 and latest["opening_rounds_known"] == 3   # round 3 predates the fix
    assert latest["plants"] == [1] and latest["clutches"] == [{"round": 2, "kills": 0}]
    assert latest["intel"] == "STRENGTH: opened rounds"                         # never Elijah's notes
    assert s["totals"]["matches"] == 5 and s["totals"]["top_operators"][0]["op"] == "Lion"
    assert s["latest_vs_usual"]["kd"] == [9.0, 0.67] and s["latest_vs_usual"]["matches_before"] == 4


def test_nobody_else_appears_anywhere_in_the_answer(tmp_path):
    text = json.dumps(player_stats(build(tmp_path), "Hector"))
    assert "someone else's notes" not in text and "Somebody" not in text


def test_too_little_history_gives_no_comparison(tmp_path):
    repo = build(tmp_path)
    repo.matches = repo.matches[3:]
    assert player_stats(repo, "Hector")["latest_vs_usual"] is None


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
    monkeypatch.setattr("server.api.v1.repo", ServerRepository(db))
    repo = build(tmp_path)
    monkeypatch.setattr("server.match_db.get_match_repo", lambda: repo)
    with TestClient(app) as c:
        yield c


def test_the_invite_link_sees_only_its_own_player(client):
    inv = client.post("/api/v1/invites", headers={"Authorization": f"Bearer {API}"}, json={"username": "Hector"}).json()
    r = client.get("/api/v1/join/mystats", headers={"Authorization": f"Bearer {inv['token']}"})
    assert r.status_code == 200 and r.json()["username"] == "Hector" and r.json()["matches"][0]["match_id"] == 5


def test_keys_that_are_not_one_players_link_are_refused(client):
    for t in (API, VOICE):
        assert client.get("/api/v1/join/mystats", headers={"Authorization": f"Bearer {t}"}).status_code == 403
    assert client.get("/api/v1/join/mystats").status_code == 401
    assert client.get("/api/v1/join/mystats", headers={"Authorization": "Bearer inv_nope_nope"}).status_code == 401
