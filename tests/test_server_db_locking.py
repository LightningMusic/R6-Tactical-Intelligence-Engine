"""
2026-10-05: a teammate's browser recorder was refused with "sqlite3.OperationalError: database is locked"
while it checked its invite, because the server's database files used SQLite's default journal and a
5-second wait. Readers and a writer must now coexist, and a collision must wait instead of fail.
"""
import sqlite3
import threading
import time
from pathlib import Path

import pytest

from database.db_manager import DatabaseManager
from server.database import ServerDatabase


def make_server_db(tmp_path: Path) -> ServerDatabase:
    return ServerDatabase(db_path=tmp_path / "server_matches.db")


def test_the_server_database_uses_write_ahead_logging_and_a_long_wait(tmp_path):
    db = make_server_db(tmp_path)
    with db.get_connection() as conn:
        assert conn.execute("PRAGMA journal_mode").fetchone()[0].lower() == "wal"
        assert conn.execute("PRAGMA busy_timeout").fetchone()[0] == 30000
        assert conn.execute("PRAGMA foreign_keys").fetchone()[0] == 1


def _reader_during_a_big_write(open_conn):
    """A big write is in progress (it spills SQLite's page cache and so takes the exclusive lock early);
    what happens to a plain SELECT, such as the invite lookup that was refused on 2026-10-05?"""
    setup = open_conn()
    setup.execute("CREATE TABLE IF NOT EXISTS big (n INTEGER, pad TEXT)")
    setup.executemany("INSERT INTO big VALUES (?, ?)", [(i, "x" * 200) for i in range(100)])
    setup.commit()
    writer = open_conn()
    writer.execute("PRAGMA cache_size = 5")
    writer.execute("BEGIN")
    writer.executemany("INSERT INTO big VALUES (?, ?)", [(i, "y" * 1000) for i in range(3000)])
    reader = open_conn()
    reader.execute("PRAGMA busy_timeout = 200")
    try:
        return reader.execute("SELECT COUNT(*) FROM big").fetchone()[0]
    finally:
        writer.rollback()
        for c in (setup, writer, reader):
            c.close()


def test_the_old_plain_connection_refused_a_reader_during_a_big_write(tmp_path):
    """The failure this guards against, reproduced with the plain connection the server used to open."""
    path = tmp_path / "plain.db"
    with pytest.raises(sqlite3.OperationalError, match="locked"):
        _reader_during_a_big_write(lambda: sqlite3.connect(path, timeout=0.2))


def test_the_server_database_serves_a_reader_during_a_big_write(tmp_path):
    db = make_server_db(tmp_path)
    assert _reader_during_a_big_write(db.get_connection) == 100          # the committed rows, undisturbed


def test_two_writers_taking_turns_both_succeed(tmp_path):
    db = make_server_db(tmp_path)
    with db.get_connection() as conn:
        conn.execute("CREATE TABLE IF NOT EXISTS turns (who TEXT)")
        conn.commit()
    holder = db.get_connection()
    holder.execute("BEGIN IMMEDIATE")
    holder.execute("INSERT INTO turns VALUES ('first')")
    errors = []

    def second():
        try:
            c = db.get_connection()
            c.execute("INSERT INTO turns VALUES ('second')")      # waits for the first to finish...
            c.commit()
            c.close()
        except Exception as e:                                    # noqa: BLE001
            errors.append(e)

    t = threading.Thread(target=second)
    t.start()
    time.sleep(0.5)
    holder.commit()                                               # ...and goes through once it does
    t.join(10)
    holder.close()
    assert not errors and not t.is_alive()
    with db.get_connection() as conn:
        assert sorted(r[0] for r in conn.execute("SELECT who FROM turns")) == ["first", "second"]


SCHEMA = Path(__file__).resolve().parent.parent / "database" / "schema.sql"


def test_the_match_database_waits_longer_everywhere_but_only_the_server_uses_wal(tmp_path):
    client = DatabaseManager(db_path=tmp_path / "client.db", schema_path=SCHEMA)
    server = DatabaseManager(db_path=tmp_path / "server.db", schema_path=SCHEMA, wal=True)
    with client.get_connection() as c, server.get_connection() as s:
        assert c.execute("PRAGMA busy_timeout").fetchone()[0] == 30000
        assert s.execute("PRAGMA busy_timeout").fetchone()[0] == 30000
        assert c.execute("PRAGMA journal_mode").fetchone()[0].lower() != "wal"     # the USB stick keeps the safe default
        assert s.execute("PRAGMA journal_mode").fetchone()[0].lower() == "wal"
