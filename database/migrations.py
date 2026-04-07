from database.db_manager import DatabaseManager

LATEST_SCHEMA_VERSION = 2


def run_migrations(db: DatabaseManager) -> None:
    current_version = db.get_schema_version()

    if current_version >= LATEST_SCHEMA_VERSION:
        return

    with db.get_connection() as conn:

        # ── V1: metadata table + operator_gadget_options ──────────────────
        if current_version < 1:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS metadata (
                    key   TEXT PRIMARY KEY,
                    value TEXT
                )
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS operator_gadget_options (
                    id          INTEGER PRIMARY KEY,
                    operator_id INTEGER,
                    gadget_id   INTEGER,
                    max_count   INTEGER,
                    UNIQUE(operator_id, gadget_id),
                    FOREIGN KEY(operator_id) REFERENCES operators(operator_id),
                    FOREIGN KEY(gadget_id)   REFERENCES gadgets(gadget_id)
                )
            """)

        # ── V2: maps table + map_id FK on matches + is_ai_generated ───────
        if current_version < 2:

            # Maps table (may already exist from schema.sql on fresh installs)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS maps (
                    map_id         INTEGER PRIMARY KEY AUTOINCREMENT,
                    name           TEXT NOT NULL UNIQUE,
                    is_active_pool INTEGER NOT NULL DEFAULT 1
                        CHECK(is_active_pool IN (0, 1))
                )
            """)

            # Add map_id FK column to matches (nullable, backwards compatible)
            # SQLite doesn't support ADD COLUMN with FK directly, but the
            # column itself is fine — FK is advisory on existing rows.
            try:
                conn.execute(
                    "ALTER TABLE matches ADD COLUMN map_id INTEGER REFERENCES maps(map_id)"
                )
            except Exception:
                pass  # Column already exists — safe to ignore

            # Add is_ai_generated to derived_metrics
            try:
                conn.execute(
                    "ALTER TABLE derived_metrics ADD COLUMN "
                    "is_ai_generated INTEGER NOT NULL DEFAULT 0 "
                    "CHECK(is_ai_generated IN (0, 1))"
                )
            except Exception:
                pass  # Column already exists — safe to ignore

            # Back-fill map_id from the legacy map text column where possible
            conn.execute("""
                UPDATE matches
                SET map_id = (
                    SELECT map_id FROM maps WHERE maps.name = matches.map
                )
                WHERE map_id IS NULL
            """)

        conn.commit()

    db.set_schema_version(LATEST_SCHEMA_VERSION)