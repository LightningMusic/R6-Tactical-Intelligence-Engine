"""
Background client-side sync coordinator (Milestone 3).

Periodically uploads queued .r6session packages to the remote server
according to the configured analysis_mode, with bounded retry count and
exponential backoff between attempts on the same item. Pure Python
(threading only, no Qt/PySide6 dependency) so it is headlessly testable;
GUI code wraps its callbacks with Qt signals rather than this module
depending on Qt directly (see gui/export_view.py's _SyncWorker).

Mode behavior (mirrors app/config.py's ANALYSIS_MODE):
  - "local":     never uploads. sync_once() is a no-op.
  - "remote":    always attempts to upload pending/failed items.
  - "automatic": attempts to upload only if upload_automatically is enabled
                 AND a server_url is configured; otherwise items simply stay
                 queued (fallback_to_local_analysis already ran at session-end
                 time in SessionManager — this coordinator never blocks or
                 substitutes for local analysis, it only handles the upload
                 leg).
"""

import threading
import time
from datetime import datetime, timezone
from typing import Callable, Optional

from app.config import settings
from app.upload_queue import UploadQueue, QueueItem
from app.uploader import SessionUploader


def backoff_seconds(retry_count: int) -> float:
    """Exponential backoff: 10s, 20s, 40s, 80s, ... capped at 10 minutes."""
    return min(600.0, 10.0 * (2 ** min(max(0, retry_count), 16)))


def _is_network_failure(error: Optional[str]) -> bool:
    """True for failures where the request never got a real answer (timeout,
    dropped connection, TLS reset) -- see SessionUploader.upload_package."""
    return bool(error) and error.startswith("Upload request failed")


def _is_auth_failure(error: Optional[str]) -> bool:
    """True when the server refused the API key. That says nothing about the package: it is fixed
    by correcting the key, after which the same package uploads fine. Treating it as a refusal of
    the package (retry cap, then never again) stranded a whole night's matches on 2026-10-05."""
    low = (error or "").lower()
    return "api token" in low or "authentication credentials" in low


def _seconds_since(iso_timestamp: Optional[str]) -> float:
    if not iso_timestamp:
        return float("inf")
    try:
        dt = datetime.fromisoformat(iso_timestamp)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return (datetime.now(timezone.utc) - dt).total_seconds()
    except Exception:
        return float("inf")


class SyncCoordinator:
    """Uploads pending/failed queue items and polls remote job status."""

    def __init__(
        self,
        upload_queue: Optional[UploadQueue] = None,
        uploader: Optional[SessionUploader] = None,
        log_callback: Optional[Callable[[str], None]] = None,
        poll_interval: float = 30.0,
    ) -> None:
        self.upload_queue = upload_queue or UploadQueue()
        self.uploader = uploader or SessionUploader()
        self.log_callback = log_callback
        self.poll_interval = poll_interval
        self._running = False
        self._thread: Optional[threading.Thread] = None
        self._state_lock = threading.Lock()

    def _log(self, msg: str) -> None:
        print(f"[SyncCoordinator] {msg}")
        if self.log_callback:
            self.log_callback(msg)

    # =====================================================
    # MODE POLICY
    # =====================================================

    def should_attempt_upload(self) -> bool:
        mode = settings.ANALYSIS_MODE
        if mode == "remote":
            return bool(settings.SERVER_URL)
        if mode == "automatic":
            return settings.UPLOAD_AUTOMATICALLY and bool(settings.SERVER_URL)
        return False  # "local" (and any unrecognized value, which settings normalizes to "local")

    def _eligible_for_retry(self, item: QueueItem) -> bool:
        # The retry limit is for packages the server actually refused (bad
        # archive, wrong key...) -- resending those can't help. A network
        # failure says nothing about the package, and giving up on it after a
        # few minutes of bad connection left finished matches stranded on the
        # USB for days, so those keep retrying at the backoff cap.
        if (item.retry_count >= settings.MAX_UPLOAD_RETRIES
                and not _is_network_failure(item.last_error)
                and not _is_auth_failure(item.last_error)):
            return False
        if item.package_status == "upload_failed":
            required_wait = backoff_seconds(item.retry_count)
            if _seconds_since(item.updated_at) < required_wait:
                return False
        return True

    # =====================================================
    # ONE-SHOT SYNC (also used by the GUI's "Sync Pending Sessions" button)
    # =====================================================

    def sync_once(self) -> dict:
        """
        Runs a single upload pass over all pending/failed queue items.
        Safe to call directly (outside the background loop) — this is what
        the GUI's "Sync Pending Sessions" action invokes on demand.
        """
        summary = {
            "attempted": 0,
            "succeeded": 0,
            "duplicate": 0,
            "failed": 0,
            "skipped_backoff_or_retries": 0,
            "mode_blocked": False,
        }

        if not self.should_attempt_upload():
            summary["mode_blocked"] = True
            self._log(
                f"Sync skipped — analysis_mode='{settings.ANALYSIS_MODE}' "
                f"does not permit remote upload right now."
            )
            return summary

        for item in self.upload_queue.get_pending_uploads():
            if not self._eligible_for_retry(item):
                summary["skipped_backoff_or_retries"] += 1
                continue

            if not item.package_path.exists():
                self._log(f"Skipping {item.session_id} — package file missing on disk: {item.package_path}")
                continue

            summary["attempted"] += 1
            self.upload_queue.update_item(item.session_id, package_status="uploading")
            result = self.uploader.upload_package(item.package_path)

            if result.success:
                summary["succeeded"] += 1
                if result.is_duplicate:
                    summary["duplicate"] += 1
                self.upload_queue.update_item(
                    item.session_id,
                    package_status="uploaded",
                    remote_analysis_status=(result.status or "queued"),
                    job_id=result.job_id,
                    last_error=None,
                )
                self._log(
                    f"Uploaded {item.session_id} -> job {result.job_id} "
                    f"(status={result.status}, duplicate={result.is_duplicate})"
                )
                self._delete_uploaded_package(item)
            else:
                summary["failed"] += 1
                self.upload_queue.update_item(
                    item.session_id,
                    package_status="upload_failed",
                    retry_count=item.retry_count + 1,
                    last_error=result.error,
                )
                self._log(f"Upload failed for {item.session_id}: {result.error}")

        return summary

    def _delete_uploaded_package(self, item: QueueItem) -> None:
        """
        Removes the local .r6session archive once the server has confirmed it.

        The server checksum-validates the archive on receipt (see
        ServerPackageValidator) and keeps its own copy under uploads/, which
        it can re-run processing against on its own via /sessions/{id}/retry.
        So the client's copy is redundant from the moment an upload succeeds.
        Keeping it is not free: on a 64GB USB stick shared with multi-GB OBS
        recordings, retained uploaded packages are what fill the drive and
        eventually break the database with "disk is full" mid-import.

        The queue entry itself is deliberately kept (see
        UploadQueue.recover_queue_unlocked) so the upload record survives and
        remote status polling keeps working with the file gone.
        """
        path = item.package_path
        if not path.exists():
            return
        try:
            mb = path.stat().st_size / (1024 * 1024)
            path.unlink()
            self._log(
                f"Freed {mb:.0f} MB — removed the local copy of "
                f"{item.session_id}; the server has it now."
            )
        except Exception as e:
            self._log(
                f"Could not remove the local package for {item.session_id} "
                f"(non-fatal, it stays on disk): {e}"
            )

    def poll_remote_statuses(self) -> int:
        """
        Polls /status for already-uploaded items whose remote_analysis_status
        isn't terminal yet. Returns the number of items updated.
        """
        if settings.ANALYSIS_MODE == "local" or not settings.SERVER_URL:
            return 0

        updated = 0
        for item in self.upload_queue.list_items():
            if item.package_status != "uploaded":
                continue
            if item.remote_analysis_status in ("completed", "failed"):
                continue

            result = self.uploader.get_status(item.session_id)
            if result.success and result.status and result.status != item.remote_analysis_status:
                self.upload_queue.update_item(item.session_id, remote_analysis_status=result.status)
                updated += 1

        return updated

    # =====================================================
    # BACKGROUND LOOP
    # =====================================================

    def start_in_background(self) -> None:
        with self._state_lock:
            if self._running:
                return
            self._running = True

        def _loop() -> None:
            while True:
                with self._state_lock:
                    if not self._running:
                        return
                try:
                    self.sync_once()
                    self.poll_remote_statuses()
                except Exception as e:
                    self._log(f"Sync loop error (non-fatal): {e}")

                # Sleep in short increments so stop() is responsive.
                waited = 0.0
                while waited < self.poll_interval:
                    with self._state_lock:
                        if not self._running:
                            return
                    time.sleep(0.5)
                    waited += 0.5

        self._thread = threading.Thread(target=_loop, daemon=True, name="SyncCoordinator")
        self._thread.start()

    def stop(self) -> None:
        with self._state_lock:
            self._running = False
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=3.0)
