"""Backfilling an already-stored match with gadget usage and objective events."""
import json
import sqlite3
from types import SimpleNamespace as NS

from server.services.utility_backfill import apply_round_updates


def db():
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    c.executescript("""
        CREATE TABLE rounds (round_id INTEGER PRIMARY KEY, match_id INT, round_number INT);
        CREATE TABLE players (player_id INTEGER PRIMARY KEY, name TEXT);
        CREATE TABLE player_round_stats (round_id INT, player_id INT, ability_start INT, ability_used INT);
        CREATE TABLE derived_metrics (match_id INT, metric_name TEXT, metric_value REAL, is_ai_generated INT, metric_text TEXT);
        INSERT INTO rounds VALUES (1, 22, 1), (2, 22, 2), (9, 99, 1);
        INSERT INTO players VALUES (1, 'RealName'), (2, 'Other');
        INSERT INTO player_round_stats VALUES (1, 1, 2, 0), (1, 2, 2, 0), (2, 1, 2, 0), (9, 1, 2, 0);
        INSERT INTO derived_metrics VALUES (22, 'round_1_events', 0, 0, '{"old": true}');
    """)
    return c


def rounds():
    ev = lambda **k: NS(to_dict=lambda: {"bomb_planted": True, **k})
    return [
        NS(round_number=1, raw_player_stats=[{"username": "RealName", "gadget_start": 3, "gadget_used": 2},
                                             {"username": "Other", "gadget_start": 0, "gadget_used": 0},
                                             {"username": "NotStored", "gadget_start": 1, "gadget_used": 1}],
           round_events=ev(our_role="attack")),
        NS(round_number=2, raw_player_stats=[{"username": "RealName"}], round_events=None),       # replay unreadable
        NS(round_number=7, raw_player_stats=[{"username": "RealName", "gadget_start": 1, "gadget_used": 1}],
           round_events=ev()),                                                                          # a round we never stored
    ]


def test_measured_gadget_numbers_land_on_the_right_player_round():
    c = db()
    done = apply_round_updates(c, 22, rounds())
    rows = {(r["round_id"], r["player_id"]): (r["ability_start"], r["ability_used"])
            for r in c.execute("SELECT * FROM player_round_stats")}
    assert rows[(1, 1)] == (3, 2)                 # matched case-insensitively
    assert rows[(1, 2)] == (0, 0)                 # a no-gadget operator is stored as 0, not left at the catalog count
    assert rows[(2, 1)] == (2, 0)                 # no measurement for that round: untouched
    assert rows[(9, 1)] == (2, 0)                 # another match: untouched
    assert done["stats"] == 2


def _run_backfill(monkeypatch, tmp_path, host_utterances, timeline_rounds):
    """backfill_session with the importer, database and comms service replaced by fakes."""
    import contextlib
    import zipfile

    import integration.rec_importer as rec_importer
    import server.match_db as match_db
    import server.services.comms_service as comms
    import server.services.utility_backfill as ub

    pkg = tmp_path / "p.r6session"
    with zipfile.ZipFile(pkg, "w") as z:
        z.writestr("replays/R01.rec", b"x")
    monkeypatch.setattr(ub, "package_path", lambda sid: pkg)
    monkeypatch.setattr(ub, "apply_round_updates", lambda conn, mid, rounds: {"stats": 1, "events": 1})

    calls = []

    class FakeImporter:
        def __init__(self, **kw): pass
        def import_match_folder(self, folder):
            return NS(rounds=[NS(round_number=1)], timeline_rounds=timeline_rounds, error_message=None)

    class FakeComms:
        @staticmethod
        def _get(sid): return {"match_id": 22, "host_utterances_json": json.dumps(host_utterances)}
        @staticmethod
        def save_rounds(sid, rounds, mid): calls.append(("save_rounds", rounds, mid))
        @staticmethod
        def build(sid): calls.append(("build",)); return {"flags": []}

    class FakeRepo:
        class db:
            @staticmethod
            @contextlib.contextmanager
            def get_connection(): yield object()

    monkeypatch.setattr(rec_importer, "RecImporter", FakeImporter)
    monkeypatch.setattr(comms, "CommsService", FakeComms)
    monkeypatch.setattr(match_db, "get_match_repo", lambda: FakeRepo)
    return ub.backfill_session("session_x", log=lambda m: None), calls


def test_a_session_with_a_transcript_gets_its_rounds_and_timeline_refreshed(monkeypatch, tmp_path):
    rounds = [{"round": 1, "ours": ["Me"]}]
    result, calls = _run_backfill(monkeypatch, tmp_path, [{"text": "hi"}], rounds)
    assert result["match_id"] == 22 and result["timeline_rebuilt"] is True
    assert calls == [("save_rounds", rounds, 22), ("build",)]


def test_a_session_without_a_transcript_is_left_for_the_normal_pipeline(monkeypatch, tmp_path):
    result, calls = _run_backfill(monkeypatch, tmp_path, [], [{"round": 1}])
    assert "timeline_rebuilt" not in result and calls == []


def test_events_are_replaced_not_duplicated_and_unknown_rounds_are_skipped():
    c = db()
    done = apply_round_updates(c, 22, rounds())
    got = c.execute("SELECT metric_text FROM derived_metrics WHERE match_id = 22 AND metric_name = 'round_1_events'").fetchall()
    assert len(got) == 1 and json.loads(got[0][0])["our_role"] == "attack"
    assert c.execute("SELECT COUNT(*) FROM derived_metrics WHERE metric_name = 'round_7_events'").fetchone()[0] == 0
    assert done["events"] == 1
