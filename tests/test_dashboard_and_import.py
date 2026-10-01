"""Bulk match loading, dashboard stats, duplicate-import guard, fast stability."""
import os
import time
from datetime import datetime
from pathlib import Path

import pytest

from analysis.dashboard_stats import build_dashboard, effective_result
from analysis.match_builder import build_matches_from_import_results
from analysis.timeline_aligner import TimelineAligner
from database.db_manager import DatabaseManager
from database.migrations import run_migrations
from database.repositories import Repository
from database.seed_operators import seed_database
from models.import_result import ImportResult, ImportStatus
from models.round import Round

SCHEMA = Path(__file__).resolve().parent.parent / "database" / "schema.sql"


@pytest.fixture
def repo(tmp_path: Path) -> Repository:
    db = DatabaseManager(tmp_path / "m.db", SCHEMA)
    run_migrations(db)
    seed_database(db)
    return Repository(db)


def _raw(name, op, k, d, ours, side):
    return {"username": name, "operator": op, "kills": k, "deaths": d, "assists": 0,
            "is_our_team": ours, "side": side}


def _result(played_at, outcomes, map_name="Villa", map_game_id=409325881472):
    rounds = []
    for i, (side, outcome) in enumerate(outcomes, 1):
        enemy = "defense" if side == "attack" else "attack"
        ours_op, theirs_op = ("Ash", "Jäger") if side == "attack" else ("Jäger", "Ash")
        r = Round(round_id=None, match_id=None, round_number=i, side=side, site="1F Kitchen",
                  outcome=outcome, resources=None, player_stats=[])
        r.raw_player_stats = [_raw("me", ours_op, 2, 1, True, side), _raw("them", theirs_op, 1, 2, False, enemy)]
        r.round_events = None
        rounds.append(r)
    wins = sum(o == "win" for _, o in outcomes)
    return ImportResult(status=ImportStatus.SUCCESS, map_name=map_name, map_game_id=map_game_id,
                        dissect_map_name=map_name, played_at=played_at, rounds=rounds,
                        score_us=wins, score_them=len(outcomes) - wins)


def test_bulk_loader_matches_the_single_match_loader(repo):
    build_matches_from_import_results(repo, [
        _result("2026-09-24T16:44:12Z", [("attack", "win"), ("defense", "loss"), ("attack", "win")]),
        _result("2026-09-24T17:31:36Z", [("defense", "loss"), ("attack", "loss")], "Coastline", 436375283234),
    ])
    bulk = repo.get_all_matches_full()
    single = [repo.get_match_full(m.match_id) for m in repo.get_all_matches()]

    def key(m):
        return (m.match_id, m.map, m.result, [(r.round_number, r.side, r.outcome,
                [(s.player.name, s.operator.name, s.kills, s.deaths) for s in r.player_stats]) for r in m.rounds])

    assert [key(m) for m in bulk] == [key(m) for m in single]
    assert len(bulk) == 2 and all(len(m.rounds) for m in bulk)


def test_the_same_replay_is_never_imported_twice(repo):
    first = _result("2026-09-24T16:44:12Z", [("attack", "win"), ("defense", "win")])
    build_matches_from_import_results(repo, [first])
    again = _result("2026-09-24T16:44:12Z", [("attack", "win"), ("defense", "win")])
    build_matches_from_import_results(repo, [again])
    assert again.match_id == first.match_id
    assert len(repo.get_all_matches()) == 1


def test_a_different_match_is_still_imported(repo):
    build_matches_from_import_results(repo, [_result("2026-09-24T16:44:12Z", [("attack", "win")])])
    build_matches_from_import_results(repo, [_result("2026-09-24T17:31:36Z", [("attack", "loss")])])
    assert len(repo.get_all_matches()) == 2


def test_data_signature_changes_only_when_data_does(repo):
    s1 = repo.data_signature()
    assert repo.data_signature() == s1
    build_matches_from_import_results(repo, [_result("2026-09-24T16:44:12Z", [("attack", "win")])])
    assert repo.data_signature() != s1


def test_dashboard_counts_old_matches_without_a_stored_result(repo):
    build_matches_from_import_results(repo, [_result("2026-09-24T16:44:12Z", [("attack", "win"), ("defense", "win")])])
    with repo.db.get_connection() as conn:
        conn.execute("UPDATE matches SET result = NULL")
        conn.commit()
    m = repo.get_all_matches_full()[0]
    assert effective_result(m) == ("win", True)
    vm = build_dashboard([m], set(), [])
    assert (vm["decided"], vm["wins"]) == (1, 1)


def test_operator_table_only_counts_your_teams_picks(repo):
    # We attack with Ash (and win), the enemy defends with Jäger.
    build_matches_from_import_results(repo, [_result("2026-09-24T16:44:12Z", [("attack", "win"), ("attack", "win")])])
    vm = build_dashboard(repo.get_all_matches_full(), set(), [])
    assert [o["operator"] for o in vm["operators"]["attack"]] == ["Ash"]
    assert vm["operators"]["defense"] == []
    assert vm["operators"]["attack"][0]["win_rate"] == 1.0


def test_frequent_unlinked_players_are_found(repo):
    stamps = ["2026-09-20T10:00:00Z", "2026-09-21T10:00:00Z", "2026-09-22T10:00:00Z"]
    build_matches_from_import_results(repo, [_result(s, [("attack", "win")]) for s in stamps])
    names = dict(repo.get_frequent_unlinked_players(min_matches=3))
    assert names.get("me") == 3 and names.get("them") == 3


def test_round_timestamps_give_the_same_window_as_parsing_the_files():
    stamps = ["2026-09-24T16:44:12Z", "2026-09-24T17:07:02Z"]
    epochs = TimelineAligner.epochs_from_stamps(stamps)
    assert epochs == sorted(datetime.fromisoformat(s.rstrip("Z")).timestamp() for s in stamps)


def test_finished_folders_are_stable_without_waiting(tmp_path: Path):
    pytest.importorskip("discord")
    from app.session_manager import SessionManager

    folder = tmp_path / "Match-old"
    folder.mkdir()
    rec = folder / "R01.rec"
    rec.write_bytes(b"x" * 100)
    old = time.time() - 600
    os.utime(rec, (old, old))
    sm = SessionManager.__new__(SessionManager)
    sm.stability_checks, sm.stability_wait = 10, 15.0
    t = time.time()
    assert sm._is_folder_stable(folder) is True
    assert time.time() - t < 1.0
