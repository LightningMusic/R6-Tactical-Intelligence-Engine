"""
Import pipeline fix: kills/deaths/assists were never captured because
_extract_player_stats() looked for them nested inside each player object,
but r6-dissect's real schema (confirmed against its actual Go source, not
guessed) puts them in a *separate, top-level* "stats" array, joined back to
"players" by username — and uses a `died` bool rather than a `deaths` int.

These tests build a minimal dict shaped like real r6-dissect round JSON
(top-level players/stats/teams/map, no matchFeedback needed) and exercise
RecImporter._parse_round() directly — no subprocess, no real .rec file.
"""
from pathlib import Path

import pytest

from integration.rec_importer import RecImporter


@pytest.fixture
def importer():
    # _parse_round() never touches self.dissect_path — only
    # _run_dissect_with_retry() does — so any existing file satisfies the
    # constructor's existence check.
    return RecImporter(dissect_path=Path(__file__), log_callback=lambda msg: None)


def _round_data(stats_entries=None, players=None, include_stats_key=True):
    base_players = players if players is not None else [
        {"id": "p1", "username": "Ash_Main", "teamIndex": 0, "operator": {"name": "Ash"}},
        {"id": "p2", "username": "Thermite_Guy", "teamIndex": 0, "operator": {"name": "Thermite"}},
        {"id": "p3", "username": "EnemyOne", "teamIndex": 1, "operator": {"name": "Jager"}},
    ]
    data = {
        "roundNumber": 0,
        "site": "Site A",
        "recordingPlayerID": "p1",
        "map": {"id": 108179795804},
        "teams": [
            {"role": "attack", "score": 1, "startingScore": 0, "won": True, "winCondition": "KilledOpponents"},
            {"role": "defense", "score": 0, "startingScore": 0, "won": False, "winCondition": ""},
        ],
        "players": base_players,
        "matchFeedback": [],
    }
    if include_stats_key:
        data["stats"] = stats_entries if stats_entries is not None else []
    return data


class TestRealStatsArray:
    def test_kills_and_deaths_join_by_username(self, importer):
        data = _round_data(stats_entries=[
            {"username": "Ash_Main", "kills": 2, "died": False, "assists": 1, "headshots": 1},
            {"username": "Thermite_Guy", "kills": 0, "died": True, "assists": 0, "headshots": 0},
            {"username": "EnemyOne", "kills": 1, "died": True, "assists": 0, "headshots": 0},
        ])
        round_obj, _ = importer._parse_round(data)
        by_name = {p["username"]: p for p in round_obj.raw_player_stats}

        assert by_name["Ash_Main"]["kills"] == 2
        assert by_name["Ash_Main"]["deaths"] == 0
        assert by_name["Ash_Main"]["assists"] == 1

        assert by_name["Thermite_Guy"]["kills"] == 0
        assert by_name["Thermite_Guy"]["deaths"] == 1

        assert by_name["EnemyOne"]["kills"] == 1
        assert by_name["EnemyOne"]["deaths"] == 1

    def test_died_bool_converts_to_deaths_int(self, importer):
        """The real schema has no `deaths` field at all — just `died: bool`.
        This is the part a naive rename-the-key fix would have missed."""
        data = _round_data(stats_entries=[
            {"username": "Ash_Main", "kills": 3, "died": True, "assists": 0},
        ])
        round_obj, _ = importer._parse_round(data)
        assert round_obj.raw_player_stats[0]["deaths"] == 1

    def test_username_join_is_case_insensitive(self, importer):
        data = _round_data(stats_entries=[
            {"username": "ASH_MAIN", "kills": 5, "died": False, "assists": 2},
            {"username": "thermite_guy", "kills": 1, "died": True, "assists": 0},
            {"username": "enemyone", "kills": 0, "died": False, "assists": 0},
        ])
        round_obj, _ = importer._parse_round(data)
        by_name = {p["username"]: p for p in round_obj.raw_player_stats}
        assert by_name["Ash_Main"]["kills"] == 5
        assert by_name["Thermite_Guy"]["deaths"] == 1

    def test_player_with_no_stats_entry_defaults_to_zero_not_crash(self, importer):
        """A player missing from the stats array (e.g. left mid-round)
        should degrade to zeros via the fallback path, not raise."""
        data = _round_data(stats_entries=[
            {"username": "Ash_Main", "kills": 4, "died": False, "assists": 1},
            # Thermite_Guy and EnemyOne intentionally have no stats entry.
        ])
        round_obj, _ = importer._parse_round(data)
        by_name = {p["username"]: p for p in round_obj.raw_player_stats}
        assert by_name["Ash_Main"]["kills"] == 4
        assert by_name["Thermite_Guy"]["kills"] == 0
        assert by_name["Thermite_Guy"]["deaths"] == 0

    def test_missing_stats_key_entirely_falls_back_without_crashing(self, importer):
        """Old/unexpected dissect output with no top-level "stats" array at
        all — should not crash, and (documenting current fallback behavior)
        still reports zeros rather than raising."""
        data = _round_data(include_stats_key=False)
        round_obj, _ = importer._parse_round(data)
        assert len(round_obj.raw_player_stats) == 3
        assert all(p["kills"] == 0 and p["deaths"] == 0 for p in round_obj.raw_player_stats)

    def test_is_our_team_flag_still_correct_alongside_real_stats(self, importer):
        data = _round_data(stats_entries=[
            {"username": "Ash_Main", "kills": 2, "died": False, "assists": 0},
            {"username": "Thermite_Guy", "kills": 1, "died": False, "assists": 0},
            {"username": "EnemyOne", "kills": 0, "died": True, "assists": 0},
        ])
        round_obj, _ = importer._parse_round(data)
        by_name = {p["username"]: p for p in round_obj.raw_player_stats}
        assert by_name["Ash_Main"]["is_our_team"] is True
        assert by_name["Thermite_Guy"]["is_our_team"] is True
        assert by_name["EnemyOne"]["is_our_team"] is False
