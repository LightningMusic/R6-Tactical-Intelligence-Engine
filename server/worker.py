import time
import threading
from pathlib import Path
from typing import Optional

from server.config import server_settings
from server.repositories import ServerRepository
from server.services.session_processing import SessionProcessingService


class ServerWorker:
    """
    Headless background job worker for the remote server.
    Polls server_jobs table for queued jobs and executes them.
    Recoverable: On startup, recovers processing jobs back to queued.
    """

    def __init__(self, repo: Optional[ServerRepository] = None) -> None:
        self.repo = repo or ServerRepository()
        self._running = False
        self._thread: Optional[threading.Thread] = None

    def recover_on_startup(self) -> int:
        """Recovers any jobs stuck in 'processing' state from a past crash/restart."""
        count = self.repo.recover_interrupted_jobs()
        if count > 0:
            print(f"[ServerWorker] Recovered {count} interrupted job(s) back to queued.")
        return count

    def process_single_job(self) -> bool:
        """Claims and processes one queued job if available. Returns True if job was processed."""
        job = self.repo.claim_next_queued_job()
        if not job:
            return False

        job_id = job["job_id"]
        session_id = job["session_id"]
        package_hash = job["package_hash"]

        print(f"[ServerWorker] Processing job {job_id} for session {session_id}...")

        archive_path = server_settings.UPLOADS_DIR / f"{package_hash}.r6session"

        try:
            if not archive_path.exists():
                raise FileNotFoundError(f"Package archive not found at {archive_path}")

            SessionProcessingService.process_session_job(
                job_id=job_id,
                archive_path=archive_path,
                session_id=session_id,
            )

            self.repo.update_job_status(job_id=job_id, status="completed", error_message=None)
            print(f"[ServerWorker] Job {job_id} completed successfully.")
            return True

        except Exception as e:
            error_msg = f"Job processing error: {e}"
            print(f"[ServerWorker] Job {job_id} failed: {error_msg}")
            self.repo.update_job_status(job_id=job_id, status="failed", error_message=error_msg)
            return True

    def start_in_background(self) -> None:
        """Starts worker loop in background daemon thread."""
        self.recover_on_startup()
        self._running = True

        def _worker_loop() -> None:
            while self._running:
                try:
                    processed = self.process_single_job()
                    if not processed:
                        time.sleep(server_settings.WORKER_POLL_INTERVAL)
                except Exception as e:
                    print(f"[ServerWorker] Loop error: {e}")
                    time.sleep(server_settings.WORKER_POLL_INTERVAL)

        self._thread = threading.Thread(target=_worker_loop, daemon=True, name="ServerWorker")
        self._thread.start()

    def stop(self) -> None:
        self._running = False
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=3.0)


server_worker = ServerWorker()
