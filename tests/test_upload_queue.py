import json
import pytest
from pathlib import Path

from app.upload_queue import UploadQueue, QueueItem
from app.packaging import SessionPackage


def test_queue_add_and_update(tmp_path: Path):
    queue = UploadQueue(queue_dir=tmp_path)
    pkg_file = tmp_path / "session_123.r6session"
    pkg_file.write_bytes(b"PK_DUMMY_CONTENT")

    item = queue.add_item(
        session_id="session_123",
        package_path=pkg_file,
        package_status="pending_upload",
    )

    assert item.session_id == "session_123"
    assert item.package_status == "pending_upload"
    assert item.local_analysis_status == "not_started"

    # Update item
    updated = queue.update_item("session_123", package_status="uploaded", remote_analysis_status="queued")
    assert updated is not None
    assert updated.package_status == "uploaded"
    assert updated.remote_analysis_status == "queued"

    # Verify atomic queue file on disk
    queue_disk = json.loads((tmp_path / "queue.json").read_text(encoding="utf-8"))
    assert "session_123" in queue_disk
    assert queue_disk["session_123"]["package_status"] == "uploaded"


def test_queue_restart_persistence(tmp_path: Path):
    queue1 = UploadQueue(queue_dir=tmp_path)
    pkg_file = tmp_path / "session_456.r6session"
    pkg_file.write_bytes(b"PK_DUMMY_CONTENT")

    queue1.add_item("session_456", pkg_file, package_status="pending_upload")

    # Simulate app restart by instantiating new UploadQueue on same dir
    queue2 = UploadQueue(queue_dir=tmp_path)
    item = queue2.get_item("session_456")
    assert item is not None
    assert item.package_status == "pending_upload"


def test_queue_crash_recovery_pruning_and_orphans(tmp_path: Path, sample_rec_folder: Path):
    # Create valid package in tmp_path
    rec_files = sorted(sample_rec_folder.glob("*.rec"))
    sid = "session_valid_789"
    pkg_path = SessionPackage.create_package(
        output_dir=tmp_path,
        session_id=sid,
        rec_files=rec_files,
        metadata={"map_name": "Oregon"},
    )

    # Create orphaned .tmp file simulating crashed write
    orphan_tmp = tmp_path / "session_crash.r6session.tmp"
    orphan_tmp.write_bytes(b"INCOMPLETE_WRITE_DATA")

    # Create queue file with missing package reference
    ghost_item = {
        "session_ghost": {
            "session_id": "session_ghost",
            "package_path": str(tmp_path / "non_existent.r6session"),
            "package_status": "pending_upload",
        }
    }
    (tmp_path / "queue.json").write_text(json.dumps(ghost_item), encoding="utf-8")

    # Initialize UploadQueue (triggers recovery)
    queue = UploadQueue(queue_dir=tmp_path)

    # 1. Orphaned .tmp file should be deleted
    assert not orphan_tmp.exists()

    # 2. Missing ghost entry should be pruned from queue
    assert queue.get_item("session_ghost") is None

    # 3. Unqueued valid package should be discovered and added
    recovered = queue.get_item(sid)
    assert recovered is not None
    assert recovered.package_status == "pending_upload"


@pytest.fixture
def sample_rec_folder(tmp_path: Path) -> Path:
    rec_dir = tmp_path / "Match-2026-08-26_12-00-00-000"
    rec_dir.mkdir()
    rec1 = rec_dir / "Match-2026-08-26_12-00-00-000-R01.rec"
    rec1.write_bytes(b"REPLAY_CONTENT")
    return rec_dir
