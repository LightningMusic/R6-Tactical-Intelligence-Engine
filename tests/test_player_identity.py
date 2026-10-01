"""
Milestone 6 — player identity: aliases ("tie usernames to a name") and
merging duplicate/"ghost" players, plus the regression fix for renaming a
player once they have match history.

Before this milestone, Settings > Players saved by calling
clear_team_players() (DELETE FROM players WHERE is_team_member=1) and
re-inserting from scratch. That's fine on a fresh team, but the moment a
player has a player_round_stats row, deleting them violates the FK
constraint (PRAGMA foreign_keys=ON) and the save silently fails with a
generic error dialog. update_player_name() replaces that flow by editing
the existing row in place.
"""
from pathlib import Path

import pytest

from database.db_manager import DatabaseManager
from database.migrations import run_migrations
from database.repositories import Repository
from database.seed_operators import seed_database
from models.player import Player
from models.round import Round
from models.round_resources import RoundResources
from models.player_round_stats import PlayerRoundStats
from analysis.match_builder import save_raw_player_stats


@pytest.fixture
def repo(tmp_path):
    db_path = tmp_path / "t.db"
    schema_path = Path("database/schema.sql")
    db = DatabaseManager(db_path=db_path, schema_path=schema_path)
    run_migrations(db)
    seed_database(db)
    return Repository(db_manager=db)


def _add_stat(repo, player_id, round_id, operator):
    stat = PlayerRoundStats(
        stat_id=None, round_id=round_id, player_id=player_id, player=None,
        operator=operator, kills=1, deaths=0, assists=0,
        engagements_taken=1, engagements_won=1,
        ability_start=operator.ability_max_count, ability_used=0,
        secondary_gadget=None, secondary_start=0, secondary_used=0,
        plant_attempted=False, plant_successful=False,
    )
    repo.insert_player_round_stats(stat, round_id, player_id)


def _make_round(repo, match_id, round_number=1):
    r = Round(
        round_id=None, match_id=match_id, round_number=round_number,
        side="attack", site="Site", outcome="win",
        resources=RoundResources(
            resource_id=None, round_id=None, side="attack",
            team_drones_start=10, team_drones_lost=0,
            team_reinforcements_start=10, team_reinforcements_used=0,
        ),
        player_stats=[],
    )
    round_id = repo.insert_round(r, match_id)
    repo.insert_round_resources(r.resources, round_id)
    return round_id


# =====================================================
# Aliases
# =====================================================

class TestAliases:
    def test_resolve_by_canonical_name_case_insensitive(self, repo):
        pid = repo.insert_player(Player(player_id=None, name="Ash_Main", is_team_member=True))
        assert repo.resolve_player_by_username("ash_main").player_id == pid

    def test_resolve_by_alias(self, repo):
        pid = repo.insert_player(Player(player_id=None, name="Ash_Main", is_team_member=True))
        repo.set_player_aliases(pid, ["AshAlt", "old_tag_99"])
        assert repo.resolve_player_by_username("AshAlt").player_id == pid
        assert repo.resolve_player_by_username("OLD_TAG_99").player_id == pid

    def test_unknown_username_resolves_to_none(self, repo):
        repo.insert_player(Player(player_id=None, name="Ash_Main", is_team_member=True))
        assert repo.resolve_player_by_username("SomeoneElse") is None

    def test_set_player_aliases_replaces_full_set(self, repo):
        pid = repo.insert_player(Player(player_id=None, name="Ash_Main", is_team_member=True))
        repo.set_player_aliases(pid, ["A", "B"])
        repo.set_player_aliases(pid, ["C"])
        assert repo.get_player_aliases(pid) == ["C"]

    def test_alias_already_claimed_elsewhere_is_skipped_not_errored(self, repo):
        p1 = repo.insert_player(Player(player_id=None, name="P1", is_team_member=True))
        p2 = repo.insert_player(Player(player_id=None, name="P2", is_team_member=True))
        repo.set_player_aliases(p1, ["shared_tag"])
        repo.set_player_aliases(p2, ["shared_tag"])  # should not raise
        assert repo.get_player_aliases(p2) == []
        assert repo.get_player_aliases(p1) == ["shared_tag"]


# =====================================================
# Renaming preserves history (the bug this replaces)
# =====================================================

class TestRenamePreservesHistory:
    def test_rename_with_existing_stats_does_not_raise(self, repo):
        pid = repo.insert_player(Player(player_id=None, name="Ash_Main", is_team_member=True))
        match_id = repo.create_match("Opp", "Bank")
        round_id = _make_round(repo, match_id)
        op = repo.get_all_operators()[0]
        _add_stat(repo, pid, round_id, op)

        repo.update_player_name(pid, "Ash_Renamed")
        assert repo.get_player_by_id(pid).name == "Ash_Renamed"
        # Stats are still attached to the same player_id.
        with repo.db.get_connection() as conn:
            row = conn.execute(
                "SELECT COUNT(*) c FROM player_round_stats WHERE player_id = ?", (pid,)
            ).fetchone()
        assert row["c"] == 1

    def test_old_clear_team_players_would_break_once_stats_exist(self, repo):
        """Documents *why* update_player_name replaced clear-and-recreate:
        this is the failure a delete-based rename hits once a team has
        played any match."""
        pid = repo.insert_player(Player(player_id=None, name="Ash_Main", is_team_member=True))
        match_id = repo.create_match("Opp", "Bank")
        round_id = _make_round(repo, match_id)
        op = repo.get_all_operators()[0]
        _add_stat(repo, pid, round_id, op)

        import sqlite3
        with pytest.raises(sqlite3.IntegrityError):
            repo.clear_team_players()


# =====================================================
# Merge
# =====================================================

class TestMerge:
    def test_merge_moves_stats_and_adds_alias(self, repo):
        target = repo.insert_player(Player(player_id=None, name="Ash_Main", is_team_member=True))
        ghost = repo.insert_player(Player(player_id=None, name="AshM4in_typo", is_team_member=False))

        match_id = repo.create_match("Opp", "Bank")
        round_id = _make_round(repo, match_id)
        op = repo.get_all_operators()[0]
        _add_stat(repo, ghost, round_id, op)

        repo.merge_player(ghost, target)

        with repo.db.get_connection() as conn:
            row = conn.execute(
                "SELECT player_id FROM player_round_stats WHERE round_id = ?", (round_id,)
            ).fetchone()
        assert row["player_id"] == target
        assert repo.get_player_by_id(ghost) is None
        assert "AshM4in_typo" in repo.get_player_aliases(target)
        # Future imports of the typo'd username now resolve to the real player.
        assert repo.resolve_player_by_username("AshM4in_typo").player_id == target

    def test_merge_refuses_when_both_played_same_round(self, repo):
        p1 = repo.insert_player(Player(player_id=None, name="RealPlayerA", is_team_member=True))
        p2 = repo.insert_player(Player(player_id=None, name="RealPlayerB", is_team_member=False))
        match_id = repo.create_match("Opp", "Bank")
        round_id = _make_round(repo, match_id)
        op = repo.get_all_operators()[0]
        _add_stat(repo, p1, round_id, op)
        _add_stat(repo, p2, round_id, op)

        with pytest.raises(ValueError):
            repo.merge_player(p2, p1)

        # Nothing was touched by the aborted merge.
        assert repo.get_player_by_id(p1) is not None
        assert repo.get_player_by_id(p2) is not None

    def test_merge_into_self_raises(self, repo):
        pid = repo.insert_player(Player(player_id=None, name="Solo", is_team_member=True))
        with pytest.raises(ValueError):
            repo.merge_player(pid, pid)


# =====================================================
# Import pipeline wiring (analysis/match_builder.py)
# =====================================================

class TestImportUsesAliasResolution:
    def test_alias_prevents_duplicate_ghost_on_reimport(self, repo):
        pid = repo.insert_player(Player(player_id=None, name="Ash_Main", is_team_member=True))
        repo.set_player_aliases(pid, ["AshAltTag"])

        match_id = repo.create_match("Opp", "Bank")
        round_id = _make_round(repo, match_id)
        op_name = repo.get_all_operators()[0].name

        class _FakeRound:
            raw_player_stats = [
                {"username": "AshAltTag", "operator": op_name, "kills": 2,
                 "deaths": 1, "assists": 0, "is_our_team": True},
            ]

        saved = save_raw_player_stats(repo, round_id, _FakeRound())
        assert saved == 1

        with repo.db.get_connection() as conn:
            row = conn.execute(
                "SELECT player_id FROM player_round_stats WHERE round_id = ?", (round_id,)
            ).fetchone()
        assert row["player_id"] == pid
        # No new ghost player was created for the aliased username.
        assert repo.get_non_team_players() == []
