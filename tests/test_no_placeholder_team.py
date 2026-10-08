"""
"Player1".."Player5" were seeded as team members on every start and sat in every player dropdown next to
the real team (2026-10-08). Seeding no longer adds them, and removes leftovers that never played.
"""
from database.db_manager import DatabaseManager
from database.migrations import run_migrations
from database.repositories import Repository
from database.seed_operators import seed_database
from pathlib import Path

SCHEMA = Path(__file__).resolve().parent.parent / "database" / "schema.sql"


def make(tmp_path):
    db = DatabaseManager(db_path=tmp_path / "m.db", schema_path=SCHEMA)
    run_migrations(db)
    return db


def test_seeding_adds_no_placeholder_team(tmp_path):
    db = make(tmp_path)
    seed_database(db)
    seed_database(db)
    assert [p.name for p in Repository(db_manager=db).get_team_players()] == []


def test_leftover_placeholders_go_but_real_players_and_anyone_with_history_stay(tmp_path):
    db = make(tmp_path)
    seed_database(db)
    with db.get_connection() as c:
        c.executemany("INSERT INTO players (name, is_team_member) VALUES (?, 1)",
                      [("Player1",), ("Player2",), ("Elijah",), ("Player9",)])
        pid = c.execute("SELECT player_id FROM players WHERE name = 'Player9'").fetchone()[0]
        c.execute("INSERT INTO player_aliases (player_id, alias) VALUES (?, 'someone')", (pid,))
        c.commit()
    seed_database(db)
    names = sorted(p.name for p in Repository(db_manager=db).get_team_players())
    assert names == ["Elijah", "Player9"]          # Player9 carries an alias someone set: never deleted
