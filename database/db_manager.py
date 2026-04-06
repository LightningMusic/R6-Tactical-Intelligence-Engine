import sqlite3
from pathlib import Path

from app.config import DB_PATH, SCHEMA_PATH


class DatabaseManager:
    """
    Centralized SQLite database manager.
    Responsibilities:
    - Connection lifecycle
    - Schema initialization
    - Foreign key enforcement
    - Schema version tracking
    """

    def __init__(self) -> None:
        self.db_path: Path = DB_PATH
        self.schema_path: Path = SCHEMA_PATH
        self._ensure_database_exists()
        self._initialize_schema()

    # ----------------------------------
    # Connection Handling
    # ----------------------------------

    def get_connection(self) -> sqlite3.Connection:
        """
        Returns a new SQLite connection with foreign keys enabled.
        """
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON;")
        return conn

    # ----------------------------------
    # Initialization
    # ----------------------------------

    def _ensure_database_exists(self) -> None:
        """
        Ensures the data directory exists before SQLite tries to open the file.
        """
        self.db_path.parent.mkdir(parents=True, exist_ok=True)

    def _initialize_schema(self) -> None:
        """
        Applies schema.sql if tables do not exist.
        """
        with self.get_connection() as conn:
            schema_sql = self.schema_path.read_text(encoding="utf-8")
            conn.executescript(schema_sql)
            conn.commit()

    # ----------------------------------
    # Schema Versioning
    # ----------------------------------

    def get_schema_version(self) -> int:
        """
        Returns current schema version from metadata table.
        Defaults to 0 if not yet set.
        """
        with self.get_connection() as conn:
            result = conn.execute(
                "SELECT value FROM metadata WHERE key = 'schema_version'"
            ).fetchone()
            return int(result["value"]) if result else 0

    def set_schema_version(self, version: int) -> None:
        """
        Updates schema version in metadata table.
        """
        with self.get_connection() as conn:
            conn.execute(
                """
                INSERT INTO metadata (key, value)
                VALUES ('schema_version', ?)
                ON CONFLICT(key) DO UPDATE SET value = excluded.value
                """,
                (str(version),),
            )
            conn.commit()