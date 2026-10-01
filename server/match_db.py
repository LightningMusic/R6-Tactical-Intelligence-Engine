"""
server/match_db.py

Milestone 4, phase 2: bootstraps and provides access to the server's own
match-schema database (server_data/matches.db) — a byte-for-byte copy of
the client's schema, seeded and migrated the exact same way. This is what
lets database.repositories.Repository and analysis.intel_engine.IntelEngine
run here unmodified against server-side data instead of duplicating either
class for the server.

Kept separate from server_matches.db (server/database.py's ServerDatabase),
which only tracks upload/job bookkeeping (sessions, packages, jobs) and
knows nothing about operators, rounds, or player stats.
"""
from database.db_manager import DatabaseManager
from database.migrations import run_migrations
from database.repositories import Repository
from server.config import server_settings

_bootstrapped = False


def ensure_match_database() -> DatabaseManager:
    """
    Creates server_data/matches.db (if missing), applies the client's
    migrations, and seeds operators/gadgets/maps — idempotent, safe to call
    on every server startup and lazily before first use. Reference data
    (operators, gadgets) must exist before any match import can resolve an
    operator name against it, exactly as it does on the client.
    """
    global _bootstrapped

    db = DatabaseManager(
        db_path=server_settings.MATCHES_DB_PATH,
        schema_path=server_settings.MATCHES_SCHEMA_PATH,
    )
    run_migrations(db)

    if not _bootstrapped:
        from database.seed_operators import seed_database

        seed_database(db)
        _bootstrapped = True

    return db


def get_match_repo() -> Repository:
    """A Repository bound to the server's match database. Cheap to call
    repeatedly — sqlite connections are opened per-call either way, same
    as the client's own Repository."""
    db = ensure_match_database()
    return Repository(db_manager=db)


def get_match_analysis(match_id: int) -> dict:
    """
    Reads back whatever IntelEngine has stored for this match: the overall
    match summary (analyze_match) and any per-player intel (get_player_intel,
    stored via IntelEngine.store_ai_text since that method doesn't persist
    its own results). Returns {} for either key if that piece hasn't been
    generated yet or failed — never raises.
    """
    repo = get_match_repo()
    result: dict = {"match_summary": None, "player_intel": {}}

    try:
        with repo.db.get_connection() as conn:
            rows = conn.execute(
                """SELECT metric_name, metric_text FROM derived_metrics
                   WHERE match_id = ? AND is_ai_generated = 1""",
                (match_id,),
            ).fetchall()
    except Exception:
        return result

    for row in rows:
        name = row["metric_name"]
        text = row["metric_text"]
        if not text:
            continue
        if name == "ai_match_summary":
            result["match_summary"] = text
        elif name.startswith("ai_player_intel::"):
            player_name = name.split("::", 1)[1]
            result["player_intel"][player_name] = text

    return result
