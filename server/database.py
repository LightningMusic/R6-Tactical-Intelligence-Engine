import sqlite3
from pathlib import Path
from server.config import server_settings


class ServerDatabase:
    """
    Manages the server-side SQLite database connection and schema initialization.
    Database file: server_data/server_matches.db (isolated from client matches.db).
    """

    def __init__(self, db_path: Path = server_settings.DATABASE_PATH) -> None:
        self.db_path = db_path
        self._ensure_database_exists()
        self.init_schema()

    def _ensure_database_exists(self) -> None:
        self.db_path.parent.mkdir(parents=True, exist_ok=True)

    def get_connection(self) -> sqlite3.Connection:
        # Many threads share this file (the dashboard polls it every few seconds, teammates' recorders
        # check in every few seconds, the worker writes). In SQLite's default mode a reader makes a writer
        # wait and gives up after 5 s with "database is locked", which refused a teammate's browser
        # recorder as it checked its invite (2026-10-05). Write-ahead logging lets readers and one
        # writer coexist, and a long busy timeout makes the rare collision wait instead of fail.
        conn = sqlite3.connect(self.db_path, timeout=30)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA busy_timeout = 30000;")
        conn.execute("PRAGMA journal_mode = WAL;")
        conn.execute("PRAGMA synchronous = NORMAL;")
        conn.execute("PRAGMA foreign_keys = ON;")
        return conn

    def init_schema(self) -> None:
        with self.get_connection() as conn:
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS server_sessions (
                    session_id TEXT PRIMARY KEY,
                    client_name TEXT NOT NULL,
                    map_name TEXT,
                    score_us INTEGER,
                    score_them INTEGER,
                    status TEXT NOT NULL DEFAULT 'uploaded',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS server_packages (
                    package_hash TEXT PRIMARY KEY,
                    session_id TEXT NOT NULL,
                    file_name TEXT NOT NULL,
                    file_size_bytes INTEGER NOT NULL,
                    is_complete INTEGER NOT NULL DEFAULT 1,
                    created_at TEXT NOT NULL,
                    FOREIGN KEY (session_id) REFERENCES server_sessions(session_id) ON DELETE CASCADE
                );

                CREATE TABLE IF NOT EXISTS server_jobs (
                    job_id TEXT PRIMARY KEY,
                    session_id TEXT NOT NULL,
                    package_hash TEXT NOT NULL,
                    status TEXT NOT NULL CHECK(status IN ('uploaded', 'validated', 'queued', 'processing', 'completed', 'failed')),
                    attempts INTEGER NOT NULL DEFAULT 0,
                    error_message TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    FOREIGN KEY (session_id) REFERENCES server_sessions(session_id) ON DELETE CASCADE,
                    FOREIGN KEY (package_hash) REFERENCES server_packages(package_hash) ON DELETE CASCADE
                );

                CREATE TABLE IF NOT EXISTS server_parsed_matches (
                    match_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    session_id TEXT NOT NULL UNIQUE,
                    map_name TEXT NOT NULL,
                    rounds_count INTEGER NOT NULL DEFAULT 0,
                    summary_json TEXT,
                    created_at TEXT NOT NULL,
                    FOREIGN KEY (session_id) REFERENCES server_sessions(session_id) ON DELETE CASCADE
                );

                -- Teammates' own mic recordings, as R6Voice uploads them:
                -- one row per 5-minute chunk. start_epoch is that teammate's
                -- PC clock at the chunk's first sample (recording start +
                -- samples so far, so chunks of one recording tile exactly).
                CREATE TABLE IF NOT EXISTS voice_chunks (
                    recording_id TEXT NOT NULL,
                    chunk_index INTEGER NOT NULL,
                    username TEXT NOT NULL,
                    start_epoch REAL NOT NULL,
                    duration_sec REAL NOT NULL,
                    sample_rate INTEGER NOT NULL,
                    file_path TEXT NOT NULL,
                    sha256 TEXT NOT NULL,
                    is_final INTEGER NOT NULL DEFAULT 0,
                    uploaded_at TEXT NOT NULL,
                    PRIMARY KEY (recording_id, chunk_index)
                );
                CREATE INDEX IF NOT EXISTS idx_voice_chunks_time ON voice_chunks(start_epoch);

                -- Everything needed to (re)build one session's comms
                -- timeline without re-reading its package: the host's own
                -- transcribed tracks, the rounds with their kill feed, and
                -- which teammate recordings have been folded in so far.
                CREATE TABLE IF NOT EXISTS session_comms (
                    session_id TEXT PRIMARY KEY,
                    match_id INTEGER,
                    audio_meta_json TEXT NOT NULL DEFAULT '{}',
                    rounds_json TEXT NOT NULL DEFAULT '[]',
                    host_utterances_json TEXT NOT NULL DEFAULT '[]',
                    voice_state_json TEXT NOT NULL DEFAULT '{}',
                    timeline_json TEXT,
                    window_start REAL,
                    window_end REAL,
                    updated_at TEXT NOT NULL
                );

                -- A teammate's transcribed speech for one session, kept so a
                -- rebuild only transcribes recordings it hasn't seen.
                CREATE TABLE IF NOT EXISTS session_voice (
                    session_id TEXT NOT NULL,
                    username TEXT NOT NULL,
                    chunks_signature TEXT NOT NULL,
                    alignment_json TEXT,
                    utterances_json TEXT NOT NULL DEFAULT '[]',
                    updated_at TEXT NOT NULL,
                    PRIMARY KEY (session_id, username)
                );

                -- R6Companion relay (2026-09-30). Teammates' school PCs can't
                -- take incoming connections, so the host's app and every
                -- companion talk through here: the host sets whether the
                -- team should be recording; companions check in every few
                -- seconds, follow it, and report back.
                CREATE TABLE IF NOT EXISTS companion_control (
                    id INTEGER PRIMARY KEY CHECK (id = 1),
                    recording INTEGER NOT NULL DEFAULT 0,
                    changed_at REAL NOT NULL DEFAULT 0,
                    host_seen REAL NOT NULL DEFAULT 0
                );
                CREATE TABLE IF NOT EXISTS companions (
                    device_id TEXT PRIMARY KEY,
                    username TEXT NOT NULL,
                    status_json TEXT NOT NULL DEFAULT '{}',
                    last_seen REAL NOT NULL
                );

                -- Browser-recorder invite links (server/invites.py). Only a
                -- hash of each token is kept.
                CREATE TABLE IF NOT EXISTS invites (
                    invite_id TEXT PRIMARY KEY,
                    username TEXT NOT NULL,
                    label TEXT NOT NULL DEFAULT '',
                    token_hash TEXT NOT NULL,
                    created_at REAL NOT NULL,
                    expires_at REAL NOT NULL,
                    revoked_at REAL,
                    last_seen REAL
                );

                -- The host's saved team list (server/roster.py): in-game names
                -- offered as a pick-list when making invite links.
                CREATE TABLE IF NOT EXISTS team_roster (
                    username TEXT PRIMARY KEY,
                    label TEXT NOT NULL DEFAULT '',
                    position INTEGER NOT NULL DEFAULT 0
                );

                -- Learned voices (server/voice_id.py): a running-mean voice
                -- embedding per in-game name, built from the Discord track.
                CREATE TABLE IF NOT EXISTS voice_profiles (
                    username TEXT PRIMARY KEY,
                    embedding_json TEXT NOT NULL,
                    n_samples INTEGER NOT NULL DEFAULT 0,
                    seconds REAL NOT NULL DEFAULT 0,
                    updated_at TEXT NOT NULL DEFAULT ''
                );

                CREATE TABLE IF NOT EXISTS server_transcripts (
                    session_id TEXT PRIMARY KEY,
                    raw_text TEXT NOT NULL DEFAULT '',
                    processed_segments_json TEXT,
                    word_count INTEGER NOT NULL DEFAULT 0,
                    created_at TEXT NOT NULL,
                    FOREIGN KEY (session_id) REFERENCES server_sessions(session_id) ON DELETE CASCADE
                );
            """)
            conn.commit()

            # Milestone 4, phase 2: link each session to the match row it
            # produced in the separate matches.db (see server/match_db.py),
            # and record why AI analysis didn't happen when it didn't.
            # ALTER TABLE, not part of the CREATE TABLE IF NOT EXISTS above,
            # because a server_data/ directory created before this change
            # already has server_parsed_matches without these columns —
            # CREATE TABLE IF NOT EXISTS is a no-op against an existing table.
            for ddl in (
                "ALTER TABLE server_parsed_matches ADD COLUMN match_id INTEGER",
                "ALTER TABLE server_parsed_matches ADD COLUMN analysis_status TEXT NOT NULL DEFAULT 'pending'",
                "ALTER TABLE server_parsed_matches ADD COLUMN analysis_error TEXT",
                # Loudest sample of a voice chunk (0..1), measured when it arrives. NULL = not measured yet.
                # Lets the server say "this recording is silent" (a teammate's mic picked up nothing).
                "ALTER TABLE voice_chunks ADD COLUMN peak REAL",
            ):
                try:
                    conn.execute(ddl)
                except Exception:
                    pass  # column already exists — safe to ignore
            conn.commit()


server_db = ServerDatabase()
