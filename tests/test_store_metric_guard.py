"""A failed AI generation must never overwrite text that was generated fine."""
from pathlib import Path

import pytest

from analysis.intel_engine import IntelEngine
from database.db_manager import DatabaseManager
from database.migrations import run_migrations


class _Repo:
    def __init__(self, db):
        self.db = db


@pytest.fixture
def repo_and_engine(tmp_path: Path):
    schema = Path(__file__).resolve().parent.parent / "database" / "schema.sql"
    db = DatabaseManager(tmp_path / "m.db", schema)
    run_migrations(db)
    with db.get_connection() as conn:
        conn.execute("INSERT INTO matches (datetime, opponent_name, map) VALUES ('2026-09-24T12:00:00', 'x', 'Villa')")
        conn.commit()
    engine = IntelEngine.__new__(IntelEngine)   # _store_metric touches nothing on self
    return _Repo(db), engine


def _text(repo, name="ai_match_summary"):
    with repo.db.get_connection() as conn:
        row = conn.execute(
            "SELECT metric_text FROM derived_metrics WHERE match_id = 1 AND metric_name = ?", (name,)
        ).fetchone()
    return row[0] if row else None


def test_a_failure_does_not_replace_a_good_summary(repo_and_engine):
    repo, engine = repo_and_engine
    engine._store_metric(repo, 1, "ai_match_summary", "## MATCH SUMMARY\nThe team won.")
    engine._store_metric(repo, 1, "ai_match_summary", "[AI] Generation failed after retries.")
    assert _text(repo) == "## MATCH SUMMARY\nThe team won."


def test_unavailable_backend_message_does_not_replace_it_either(repo_and_engine):
    repo, engine = repo_and_engine
    engine._store_metric(repo, 1, "ai_match_summary", "A real summary.")
    engine._store_metric(repo, 1, "ai_match_summary", "[AI unavailable]\nTo enable AI analysis: ...")
    assert _text(repo) == "A real summary."


def test_a_failure_is_still_recorded_when_nothing_better_exists(repo_and_engine):
    repo, engine = repo_and_engine
    engine._store_metric(repo, 1, "ai_match_summary", "[AI] Generation failed after retries.")
    assert _text(repo) == "[AI] Generation failed after retries."


def test_a_good_result_replaces_an_earlier_failure_and_an_earlier_good_one(repo_and_engine):
    repo, engine = repo_and_engine
    engine._store_metric(repo, 1, "ai_match_summary", "[AI] Generation failed after retries.")
    engine._store_metric(repo, 1, "ai_match_summary", "First real summary.")
    engine._store_metric(repo, 1, "ai_match_summary", "Second, better summary.")
    assert _text(repo) == "Second, better summary."
