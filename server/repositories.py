from datetime import datetime, timezone
from typing import Optional, Dict, Any, List
from server.database import ServerDatabase, server_db


class ServerRepository:
    """Data access repository for server database operations."""

    def __init__(self, db: ServerDatabase = server_db) -> None:
        self.db = db

    def get_session(self, session_id: str) -> Optional[Dict[str, Any]]:
        with self.db.get_connection() as conn:
            row = conn.execute(
                "SELECT * FROM server_sessions WHERE session_id = ?", (session_id,)
            ).fetchone()
            return dict(row) if row else None

    def create_session(
        self,
        session_id: str,
        client_name: str,
        map_name: str = "Unknown",
        score_us: Optional[int] = None,
        score_them: Optional[int] = None,
    ) -> Dict[str, Any]:
        now = datetime.now(timezone.utc).isoformat()
        with self.db.get_connection() as conn:
            conn.execute(
                """
                INSERT INTO server_sessions (session_id, client_name, map_name, score_us, score_them, status, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, 'uploaded', ?, ?)
                ON CONFLICT(session_id) DO UPDATE SET
                    map_name = excluded.map_name,
                    score_us = excluded.score_us,
                    score_them = excluded.score_them,
                    updated_at = excluded.updated_at
                """,
                (session_id, client_name, map_name, score_us, score_them, now, now),
            )
            conn.commit()
        return self.get_session(session_id)  # type: ignore

    def get_package(self, package_hash: str) -> Optional[Dict[str, Any]]:
        with self.db.get_connection() as conn:
            row = conn.execute(
                "SELECT * FROM server_packages WHERE package_hash = ?", (package_hash,)
            ).fetchone()
            return dict(row) if row else None

    def create_package(
        self,
        package_hash: str,
        session_id: str,
        file_name: str,
        file_size_bytes: int,
        is_complete: bool = True,
    ) -> Dict[str, Any]:
        now = datetime.now(timezone.utc).isoformat()
        with self.db.get_connection() as conn:
            conn.execute(
                """
                INSERT INTO server_packages (package_hash, session_id, file_name, file_size_bytes, is_complete, created_at)
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(package_hash) DO NOTHING
                """,
                (package_hash, session_id, file_name, file_size_bytes, 1 if is_complete else 0, now),
            )
            conn.commit()
        return self.get_package(package_hash)  # type: ignore

    def get_job(self, job_id: str) -> Optional[Dict[str, Any]]:
        with self.db.get_connection() as conn:
            row = conn.execute(
                "SELECT * FROM server_jobs WHERE job_id = ?", (job_id,)
            ).fetchone()
            return dict(row) if row else None

    def get_job_by_session(self, session_id: str) -> Optional[Dict[str, Any]]:
        with self.db.get_connection() as conn:
            row = conn.execute(
                "SELECT * FROM server_jobs WHERE session_id = ? ORDER BY created_at DESC LIMIT 1",
                (session_id,),
            ).fetchone()
            return dict(row) if row else None

    def create_job(self, job_id: str, session_id: str, package_hash: str, initial_status: str = "queued") -> Dict[str, Any]:
        now = datetime.now(timezone.utc).isoformat()
        with self.db.get_connection() as conn:
            conn.execute(
                """
                INSERT INTO server_jobs (job_id, session_id, package_hash, status, attempts, created_at, updated_at)
                VALUES (?, ?, ?, ?, 0, ?, ?)
                """,
                (job_id, session_id, package_hash, initial_status, now, now),
            )
            conn.commit()
        return self.get_job(job_id)  # type: ignore

    def update_job_status(
        self,
        job_id: str,
        status: str,
        error_message: Optional[str] = None,
        increment_attempts: bool = False,
    ) -> None:
        now = datetime.now(timezone.utc).isoformat()
        with self.db.get_connection() as conn:
            if increment_attempts:
                conn.execute(
                    """
                    UPDATE server_jobs
                    SET status = ?, error_message = ?, attempts = attempts + 1, updated_at = ?
                    WHERE job_id = ?
                    """,
                    (status, error_message, now, job_id),
                )
            else:
                conn.execute(
                    """
                    UPDATE server_jobs
                    SET status = ?, error_message = ?, updated_at = ?
                    WHERE job_id = ?
                    """,
                    (status, error_message, now, job_id),
                )
            conn.commit()

    def claim_next_queued_job(self) -> Optional[Dict[str, Any]]:
        now = datetime.now(timezone.utc).isoformat()
        with self.db.get_connection() as conn:
            row = conn.execute(
                """
                SELECT * FROM server_jobs
                WHERE status = 'queued'
                ORDER BY created_at ASC
                LIMIT 1
                """
            ).fetchone()
            if not row:
                return None

            job_id = row["job_id"]
            conn.execute(
                """
                UPDATE server_jobs
                SET status = 'processing', attempts = attempts + 1, updated_at = ?
                WHERE job_id = ? AND status = 'queued'
                """,
                (now, job_id),
            )
            conn.commit()
            return self.get_job(job_id)

    def recover_interrupted_jobs(self) -> int:
        now = datetime.now(timezone.utc).isoformat()
        with self.db.get_connection() as conn:
            cur = conn.execute(
                """
                UPDATE server_jobs
                SET status = 'queued', updated_at = ?
                WHERE status = 'processing'
                """,
                (now,),
            )
            count = cur.rowcount
            conn.commit()
            return count

    def list_sessions(self, limit: int = 50) -> List[Dict[str, Any]]:
        with self.db.get_connection() as conn:
            rows = conn.execute(
                "SELECT * FROM server_sessions ORDER BY created_at DESC LIMIT ?", (limit,)
            ).fetchall()
            return [dict(r) for r in rows]
