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
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
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
            """)
            conn.commit()


server_db = ServerDatabase()
