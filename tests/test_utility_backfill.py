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


def test_events_are_replaced_not_duplicated_and_unknown_rounds_are_skipped():
    c = db()
    done = apply_round_updates(c, 22, rounds())
    got = c.execute("SELECT metric_text FROM derived_metrics WHERE match_id = 22 AND metric_name = 'round_1_events'").fetchall()
    assert len(got) == 1 and json.loads(got[0][0])["our_role"] == "attack"
    assert c.execute("SELECT COUNT(*) FROM derived_metrics WHERE metric_name = 'round_7_events'").fetchone()[0] == 0
    assert done["events"] == 1
