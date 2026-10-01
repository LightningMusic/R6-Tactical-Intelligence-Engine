from database.db_manager import DatabaseManager

LATEST_SCHEMA_VERSION = 5


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

            conn.execute("""
                CREATE TABLE IF NOT EXISTS maps (
                    map_id         INTEGER PRIMARY KEY AUTOINCREMENT,
                    name           TEXT NOT NULL UNIQUE,
                    is_active_pool INTEGER NOT NULL DEFAULT 1
                        CHECK(is_active_pool IN (0, 1))
                )
            """)

            try:
                conn.execute(
                    "ALTER TABLE matches ADD COLUMN map_id INTEGER REFERENCES maps(map_id)"
                )
            except Exception:
                pass  # Column already exists

            try:
                conn.execute(
                    "ALTER TABLE derived_metrics ADD COLUMN "
                    "is_ai_generated INTEGER NOT NULL DEFAULT 0 "
                    "CHECK(is_ai_generated IN (0, 1))"
                )
            except Exception:
                pass

            conn.execute("""
                UPDATE matches
                SET map_id = (
                    SELECT map_id FROM maps WHERE maps.name = matches.map
                )
                WHERE map_id IS NULL
            """)

        # ── V3: metric_text column on derived_metrics ──────────────────────
        if current_version < 3:
            try:
                conn.execute(
                    "ALTER TABLE derived_metrics ADD COLUMN metric_text TEXT"
                )
            except Exception:
                pass  # Column already exists — safe to ignore

        # ── V4: player_aliases + transcript_speaker_labels ─────────────────
        # Player-identity/alias system + speaker-tagging tables, added
        # Milestone 6. CREATE TABLE IF NOT EXISTS makes this safe to run
        # even if schema.sql already created them on a brand-new DB.
        if current_version < 4:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS player_aliases (
                    alias_id  INTEGER PRIMARY KEY AUTOINCREMENT,
                    player_id INTEGER NOT NULL,
                    alias     TEXT NOT NULL COLLATE NOCASE,
                    UNIQUE(alias),
                    FOREIGN KEY (player_id) REFERENCES players(player_id) ON DELETE CASCADE
                )
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS transcript_speaker_labels (
                    label_id    INTEGER PRIMARY KEY AUTOINCREMENT,
                    match_id    INTEGER NOT NULL,
                    speaker_tag TEXT NOT NULL,
                    player_id   INTEGER,
                    UNIQUE(match_id, speaker_tag),
                    FOREIGN KEY (match_id) REFERENCES matches(match_id) ON DELETE CASCADE,
                    FOREIGN KEY (player_id) REFERENCES players(player_id) ON DELETE SET NULL
                )
            """)

        # ── V5: self-updating game catalog ─────────────────────────────────
        # The catalog tables themselves come from schema.sql (re-applied on
        # every startup); only the new columns on existing tables need
        # ALTERs. operators.source records where a row came from so the
        # Ubisoft sync knows it may correct the display name of an operator
        # learned from a replay ("IQ" arrives as "IQ", but a new one might
        # arrive as "NOOR") without ever renaming hand-seeded ones.
        # matches.map_game_id lets a match on a not-yet-named map be
        # backfilled once someone names it.
        if current_version < 5:
            for ddl in (
                "ALTER TABLE operators ADD COLUMN source TEXT NOT NULL DEFAULT 'seed'",
                "ALTER TABLE matches ADD COLUMN map_game_id INTEGER",
            ):
                try:
                    conn.execute(ddl)
                except Exception:
                    pass  # column already exists

        conn.commit()

    db.set_schema_version(LATEST_SCHEMA_VERSION)