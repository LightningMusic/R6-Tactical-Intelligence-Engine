import json
import shutil
import threading
from pathlib import Path
from datetime import datetime, timezone
from typing import Optional, Dict, Any, List

from app.config import DATA_DIR
from app.packaging import SessionPackage

QUEUE_DIR = DATA_DIR / "queue"
QUEUE_FILE = QUEUE_DIR / "queue.json"


class QueueItem:
    def __init__(
        self,
        session_id: str,
        package_relpath: str,
        package_status: str = "created",
        local_analysis_status: str = "not_started",
        remote_analysis_status: str = "none",
        created_at: Optional[str] = None,
        updated_at: Optional[str] = None,
        retry_count: int = 0,
        last_error: Optional[str] = None,
        job_id: Optional[str] = None,
        source_fingerprint: Optional[str] = None,
        queue_dir: Optional[Path] = None,
    ) -> None:
        self.session_id = session_id
        # Standardize package_relpath (ensure relative string filename)
        p = Path(package_relpath)
        self.package_relpath = p.name
        self.package_status = package_status
        self.local_analysis_status = local_analysis_status
        self.remote_analysis_status = remote_analysis_status
        self.created_at = created_at or datetime.now(timezone.utc).isoformat()
        self.updated_at = updated_at or datetime.now(timezone.utc).isoformat()
        self.retry_count = retry_count
        self.last_error = last_error
        self.job_id = job_id
        self.source_fingerprint = source_fingerprint
        self._queue_dir = queue_dir or QUEUE_DIR

    @property
    def package_path(self) -> Path:
        """Dynamically resolves package path relative to runtime QUEUE_DIR for USB portability."""
        return self._queue_dir / self.package_relpath

    def to_dict(self) -> Dict[str, Any]:
        return {
            "session_id": self.session_id,
            "package_relpath": self.package_relpath,
            "package_status": self.package_status,
            "local_analysis_status": self.local_analysis_status,
            "remote_analysis_status": self.remote_analysis_status,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "retry_count": self.retry_count,
            "last_error": self.last_error,
            "job_id": self.job_id,
            "source_fingerprint": self.source_fingerprint,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any], queue_dir: Optional[Path] = None) -> "QueueItem":
        # Migration logic: if old record contains absolute 'package_path' but missing 'package_relpath'
        relpath = data.get("package_relpath")
        if not relpath and "package_path" in data:
            relpath = Path(data["package_path"]).name

        return cls(
            session_id=data["session_id"],
            package_relpath=relpath or f"{data['session_id']}.r6session",
            package_status=data.get("package_status", "created"),
            local_analysis_status=data.get("local_analysis_status", "not_started"),
            remote_analysis_status=data.get("remote_analysis_status", "none"),
            created_at=data.get("created_at"),
            updated_at=data.get("updated_at"),
            retry_count=data.get("retry_count", 0),
            last_error=data.get("last_error"),
            job_id=data.get("job_id"),
            source_fingerprint=data.get("source_fingerprint"),
            queue_dir=queue_dir,
        )


class UploadQueue:
    """
    USB-portable, thread-safe, crash-resilient queue manager for .r6session packages.
    All queue metadata file writes are atomic (write to .tmp and rename).
    Queue items store USB-portable relative paths ('package_relpath').
    """

    def __init__(self, queue_dir: Path = QUEUE_DIR) -> None:
        self._lock = threading.Lock()
        self.queue_dir = queue_dir
        self.queue_file = self.queue_dir / "queue.json"
        self.queue_dir.mkdir(parents=True, exist_ok=True)
        self.items: Dict[str, QueueItem] = {}
        with self._lock:
            self.load_unlocked()
            self.recover_queue_unlocked()

    def _atomic_save_unlocked(self) -> None:
        """Saves queue items atomically to disk via temporary file rename (unlocked)."""
        tmp_file = self.queue_dir / "queue.json.tmp"
        data = {sid: item.to_dict() for sid, item in self.items.items()}
        content = json.dumps(data, indent=2).encode("utf-8")

        tmp_file.write_bytes(content)
        if self.queue_file.exists():
            self.queue_file.unlink()
        tmp_file.rename(self.queue_file)

    def load_unlocked(self) -> None:
        """Loads queue metadata from disk (unlocked), performing path migration if needed."""
        if not self.queue_file.exists():
            self.items = {}
            return

        try:
            raw = self.queue_file.read_text(encoding="utf-8")
            data = json.loads(raw)
            self.items = {
                sid: QueueItem.from_dict(val, queue_dir=self.queue_dir)
                for sid, val in data.items()
            }
        except Exception as e:
            print(f"[UploadQueue] Warning: Failed to load queue.json: {e}")
            self.items = {}

    def load(self) -> None:
        with self._lock:
            self.load_unlocked()

    def add_item(
        self,
        session_id: str,
        package_path: Path,
        package_status: str = "created",
        local_analysis_status: str = "not_started",
        remote_analysis_status: str = "none",
        source_fingerprint: Optional[str] = None,
    ) -> QueueItem:
        """Adds a new package to the queue and saves state atomically."""
        with self._lock:
            item = QueueItem(
                session_id=session_id,
                package_relpath=package_path.name,
                package_status=package_status,
                local_analysis_status=local_analysis_status,
                remote_analysis_status=remote_analysis_status,
                source_fingerprint=source_fingerprint,
                queue_dir=self.queue_dir,
            )
            self.items[session_id] = item
            self._atomic_save_unlocked()
            return item

    def update_item(self, session_id: str, **kwargs: Any) -> Optional[QueueItem]:
        """Updates attributes of an existing queue item and saves state atomically."""
        with self._lock:
            item = self.items.get(session_id)
            if not item:
                return None

            for key, value in kwargs.items():
                if hasattr(item, key):
                    setattr(item, key, value)

            item.updated_at = datetime.now(timezone.utc).isoformat()
            self._atomic_save_unlocked()
            return item

    def get_item(self, session_id: str) -> Optional[QueueItem]:
        with self._lock:
            return self.items.get(session_id)

    def list_items(self) -> List[QueueItem]:
        with self._lock:
            return list(self.items.values())

    def get_pending_uploads(self) -> List[QueueItem]:
        with self._lock:
            return [
                item for item in self.items.values()
                if item.package_status in ("pending_upload", "upload_failed")
            ]

    def recover_queue_unlocked(self) -> None:
        """
        Scans queue directory on startup (unlocked) to:
        1. Delete orphaned .tmp files from crashed package writes.
        2. Remove metadata entries pointing to missing package files.
        3. Register unqueued valid .r6session files found in queue directory.
        """
        for tmp_file in self.queue_dir.glob("*.tmp"):
            try:
                tmp_file.unlink()
            except Exception:
                pass

        missing_ids = [
            sid for sid, item in self.items.items()
            if not item.package_path.exists()
        ]
        for sid in missing_ids:
            del self.items[sid]

        for pkg_file in self.queue_dir.glob("*.r6session"):
            session_id = pkg_file.stem
            if session_id not in self.items:
                valid, msg = SessionPackage.verify_package(pkg_file)
                if valid:
                    item = QueueItem(
                        session_id=session_id,
                        package_relpath=pkg_file.name,
                        package_status="pending_upload",
                        queue_dir=self.queue_dir,
                    )
                    self.items[session_id] = item
                else:
                    print(f"[UploadQueue] Recover: Skipping invalid archive {pkg_file.name}: {msg}")

        self._atomic_save_unlocked()

    def recover_queue(self) -> None:
        with self._lock:
            self.recover_queue_unlocked()
