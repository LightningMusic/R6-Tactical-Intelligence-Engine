"""
Milestone 4, phase 2: analysis/match_builder.py is the extracted, standalone
version of what app/session_manager.py's SessionManager._auto_create_matches
and _save_raw_player_stats have always done locally — turning RecImporter's
ImportResult objects (parsed straight from .rec replay files, no manual
entry) into match/round/player-stat rows. It's factored out so the server's
phase-2 pipeline can build an identical match record without a second,
drifting copy of this logic.

These tests exercise it directly against a real DatabaseManager/Repository
on a throwaway sqlite file (fast — no mocking needed, matching how
test_local_regression.py already tests the DB layer for real), rather than
only indirectly through SessionManager, so both call sites are covered by
one source of truth.
"""
from pathlib import Path

import pytest

from database.db_manager import DatabaseManager
from database.repositories import Repository
from database.migrations import run_migrations
from database.seed_operators import seed_database
from analysis.match_builder import build_matches_from_import_results
from models.import_result import ImportResult, ImportStatus
from models.round import Round


SCHEMA_PATH = Path(__file__).parent.parent / "database" / "schema.sql"


@pytest.fixture
def repo(tmp_path: Path) -> Repository:
    db = DatabaseManager(db_path=tmp_path / "test_matches.db", schema_path=SCHEMA_PATH)
    run_migrations(db)
    seed_database(db)
    return Repository(db_manager=db)


def _make_result(operator_name: str = "Ash") -> ImportResult:
    round_obj = Round(
        round_id=None,
        match_id=None,
        round_number=1,
        side="attack",
        site="Site A",
        outcome="win",
        resources=None,
        player_stats=[],
        raw_player_stats=[
            {
                "username": "TeamPlayer1",
                "operator": operator_name,
                "kills": 3,
                "deaths": 1,
                "assists": 0,
                "is_our_team": True,
            },
            {
                "username": "EnemyPlayer1",
                "operator": "Jäger",
                "kills": 1,
                "deaths": 3,
                "assists": 0,
                "is_our_team": False,
            },
        ],
        round_events=None,
    )
    return ImportResult(
        status=ImportStatus.SUCCESS,
        map_name="Clubhouse",
        score_us=1,
        score_them=0,
        rounds=[round_obj],
    )


def test_creates_match_and_player_stats(repo: Repository):
    result = _make_result()

    build_matches_from_import_results(repo, [result], log=print)

    assert result.match_id is not None
    match = repo.get_match_full(result.match_id)
    assert match is not None
    assert match.map == "Clubhouse"
    assert len(match.rounds) == 1

    stats = match.rounds[0].player_stats
    names = {s.player.name: s for s in stats}
    assert names["TeamPlayer1"].kills == 3
    assert names["TeamPlayer1"].operator.name.lower() == "ash"
    # Accent-stripped fuzzy operator match (Jäger) still resolves.
    assert names["EnemyPlayer1"].operator is not None


def test_skips_results_with_no_rounds(repo: Repository):
    result = ImportResult(status=ImportStatus.CRITICAL_FAILURE, rounds=[], error_message="no rec files")

    build_matches_from_import_results(repo, [result], log=print)

    assert result.match_id is None


def test_does_not_recreate_existing_match(repo: Repository):
    result = _make_result()
    build_matches_from_import_results(repo, [result], log=print)
    first_id = result.match_id

    # Calling again with the same (already-linked) result must be a no-op —
    # this is what makes it safe for the server to call this helper without
    # separately tracking "have I already processed this session".
    build_matches_from_import_results(repo, [result], log=print)

    assert result.match_id == first_id


def test_unknown_operator_skips_that_players_stat_row_only(repo: Repository):
    result = _make_result(operator_name="TotallyNotARealOperator")

    build_matches_from_import_results(repo, [result], log=print)

    match = repo.get_match_full(result.match_id)
    stats = match.rounds[0].player_stats
    # The unresolved-operator row is skipped; the other player's row is not.
    assert len(stats) == 1
    assert stats[0].player.name == "EnemyPlayer1"
