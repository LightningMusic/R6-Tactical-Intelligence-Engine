import pytest
from pathlib import Path
from unittest.mock import MagicMock

from app.config import settings
from app.upload_queue import UploadQueue
from app.uploader import UploadResult
from app.sync_coordinator import SyncCoordinator, backoff_seconds


@pytest.fixture(autouse=True)
def _reset_settings():
    yield
    settings.set_many({
        "analysis_mode": "local", "server_url": "", "api_key": "",
        "upload_automatically": True, "max_upload_retries": 5,
    })


@pytest.fixture
def queue(tmp_path: Path) -> UploadQueue:
    return UploadQueue(queue_dir=tmp_path / "queue")


def _make_pending_item(queue: UploadQueue, session_id: str) -> Path:
    pkg = queue.queue_dir / f"{session_id}.r6session"
    pkg.parent.mkdir(parents=True, exist_ok=True)
    pkg.write_bytes(b"FAKE_PACKAGE_DATA")
    queue.add_item(session_id=session_id, package_path=pkg, package_status="pending_upload")
    return pkg


# =====================================================
# MODE POLICY
# =====================================================

@pytest.mark.parametrize(
    "mode,server_url,upload_auto,expected",
    [
        ("local", "http://x:8000", True, False),
        ("remote", "", True, False),
        ("remote", "http://x:8000", True, True),
        ("automatic", "http://x:8000", False, False),
        ("automatic", "http://x:8000", True, True),
        ("automatic", "", True, False),
    ],
)
def test_should_attempt_upload_modes(queue, mode, server_url, upload_auto, expected):
    settings.set_many({"analysis_mode": mode, "server_url": server_url, "upload_automatically": upload_auto})
    coordinator = SyncCoordinator(upload_queue=queue, uploader=MagicMock())
    assert coordinator.should_attempt_upload() is expected


def test_sync_once_local_mode_never_calls_uploader(queue):
    settings.set_many({"analysis_mode": "local"})
    _make_pending_item(queue, "session_1")
    fake_uploader = MagicMock()

    coordinator = SyncCoordinator(upload_queue=queue, uploader=fake_uploader)
    summary = coordinator.sync_once()

    assert summary["mode_blocked"] is True
    fake_uploader.upload_package.assert_not_called()
    # Item must remain untouched.
    item = queue.get_item("session_1")
    assert item.package_status == "pending_upload"


# =====================================================
# UPLOAD SUCCESS / FAILURE
# =====================================================

def test_sync_once_uploads_pending_items_successfully(queue):
    settings.set_many({"analysis_mode": "remote", "server_url": "http://localhost:8000"})
    _make_pending_item(queue, "session_2")

    fake_uploader = MagicMock()
    fake_uploader.upload_package.return_value = UploadResult(
        success=True, session_id="session_2", job_id="job_xyz", status="queued", is_duplicate=False,
    )

    coordinator = SyncCoordinator(upload_queue=queue, uploader=fake_uploader)
    summary = coordinator.sync_once()

    assert summary["attempted"] == 1
    assert summary["succeeded"] == 1
    fake_uploader.upload_package.assert_called_once()

    item = queue.get_item("session_2")
    assert item.package_status == "uploaded"
    assert item.remote_analysis_status == "queued"
    assert item.job_id == "job_xyz"
    assert item.last_error is None


def test_sync_once_marks_failure_and_increments_retry(queue):
    settings.set_many({"analysis_mode": "remote", "server_url": "http://localhost:8000"})
    _make_pending_item(queue, "session_3")

    fake_uploader = MagicMock()
    fake_uploader.upload_package.return_value = UploadResult(success=False, error="connection refused")

    coordinator = SyncCoordinator(upload_queue=queue, uploader=fake_uploader)
    summary = coordinator.sync_once()

    assert summary["failed"] == 1
    item = queue.get_item("session_3")
    assert item.package_status == "upload_failed"
    assert item.retry_count == 1
    assert item.last_error == "connection refused"


def test_sync_once_skips_item_missing_package_file(queue):
    settings.set_many({"analysis_mode": "remote", "server_url": "http://localhost:8000"})
    queue.add_item(session_id="session_ghost", package_path=queue.queue_dir / "session_ghost.r6session", package_status="pending_upload")

    fake_uploader = MagicMock()
    coordinator = SyncCoordinator(upload_queue=queue, uploader=fake_uploader)
    summary = coordinator.sync_once()

    assert summary["attempted"] == 0
    fake_uploader.upload_package.assert_not_called()


# =====================================================
# RETRY LIMIT + BACKOFF
# =====================================================

def test_max_retries_exceeded_is_skipped(queue):
    settings.set_many({"analysis_mode": "remote", "server_url": "http://localhost:8000", "max_upload_retries": 2})
    pkg = _make_pending_item(queue, "session_4")
    queue.update_item("session_4", package_status="upload_failed", retry_count=2)

    fake_uploader = MagicMock()
    coordinator = SyncCoordinator(upload_queue=queue, uploader=fake_uploader)
    summary = coordinator.sync_once()

    assert summary["skipped_backoff_or_retries"] == 1
    fake_uploader.upload_package.assert_not_called()


def test_recent_failure_is_skipped_until_backoff_elapses(queue):
    settings.set_many({"analysis_mode": "remote", "server_url": "http://localhost:8000"})
    _make_pending_item(queue, "session_5")
    # Simulate a failure that "just happened" (updated_at defaults to now).
    queue.update_item("session_5", package_status="upload_failed", retry_count=1)

    fake_uploader = MagicMock()
    coordinator = SyncCoordinator(upload_queue=queue, uploader=fake_uploader)
    summary = coordinator.sync_once()

    assert summary["skipped_backoff_or_retries"] == 1
    fake_uploader.upload_package.assert_not_called()

    # Now backdate updated_at to simulate the backoff window having elapsed.
    # update_item() always stamps updated_at=now() itself, so the only way to
    # simulate elapsed time is to mutate the in-memory item directly and
    # re-save — exactly what a real clock tick would produce.
    from datetime import datetime, timezone, timedelta
    old_ts = (datetime.now(timezone.utc) - timedelta(seconds=backoff_seconds(1) + 5)).isoformat()
    with queue._lock:
        queue.items["session_5"].updated_at = old_ts
        queue._atomic_save_unlocked()

    fake_uploader.upload_package.return_value = UploadResult(success=True, session_id="session_5", job_id="j", status="queued")
    summary2 = coordinator.sync_once()
    assert summary2["attempted"] == 1
    fake_uploader.upload_package.assert_called_once()


def test_backoff_seconds_grows_and_caps():
    assert backoff_seconds(0) == 10.0
    assert backoff_seconds(1) == 20.0
    assert backoff_seconds(2) == 40.0
    assert backoff_seconds(20) == 600.0  # capped
    # An item that has been failing for days must not overflow the float.
    assert backoff_seconds(100_000) == 600.0


def _backdate(queue, session_id, seconds):
    from datetime import datetime, timezone, timedelta
    old_ts = (datetime.now(timezone.utc) - timedelta(seconds=seconds)).isoformat()
    with queue._lock:
        queue.items[session_id].updated_at = old_ts
        queue._atomic_save_unlocked()


def test_network_failures_are_never_abandoned_after_max_retries(queue):
    """A dropped connection says nothing about the package, so an item that
    only ever failed to *reach* the server keeps retrying past the limit."""
    settings.set_many({"analysis_mode": "remote", "server_url": "http://localhost:8000", "max_upload_retries": 2})
    _make_pending_item(queue, "session_net")
    queue.update_item(
        "session_net", package_status="upload_failed", retry_count=7,
        last_error="Upload request failed: ('Connection aborted.', TimeoutError('The write operation timed out'))",
    )
    _backdate(queue, "session_net", 700)

    fake_uploader = MagicMock()
    fake_uploader.upload_package.return_value = UploadResult(success=True, session_id="session_net", job_id="j", status="queued")
    summary = SyncCoordinator(upload_queue=queue, uploader=fake_uploader).sync_once()

    assert summary["attempted"] == 1
    assert queue.get_item("session_net").package_status == "uploaded"


def test_server_rejections_still_stop_at_the_retry_limit(queue):
    settings.set_many({"analysis_mode": "remote", "server_url": "http://localhost:8000", "max_upload_retries": 2})
    _make_pending_item(queue, "session_rejected")
    queue.update_item(
        "session_rejected", package_status="upload_failed", retry_count=2,
        last_error="Package validation failed: checksum mismatch",
    )
    _backdate(queue, "session_rejected", 700)

    fake_uploader = MagicMock()
    summary = SyncCoordinator(upload_queue=queue, uploader=fake_uploader).sync_once()

    assert summary["skipped_backoff_or_retries"] == 1
    fake_uploader.upload_package.assert_not_called()


# =====================================================
# STATUS POLLING
# =====================================================

def test_poll_remote_statuses_updates_completed(queue):
    settings.set_many({"analysis_mode": "remote", "server_url": "http://localhost:8000"})
    _make_pending_item(queue, "session_6")
    queue.update_item("session_6", package_status="uploaded", remote_analysis_status="queued")

    fake_uploader = MagicMock()
    fake_uploader.get_status.return_value = UploadResult(success=True, session_id="session_6", status="completed")

    coordinator = SyncCoordinator(upload_queue=queue, uploader=fake_uploader)
    updated_count = coordinator.poll_remote_statuses()

    assert updated_count == 1
    assert queue.get_item("session_6").remote_analysis_status == "completed"


def test_poll_remote_statuses_noop_in_local_mode(queue):
    settings.set_many({"analysis_mode": "local"})
    _make_pending_item(queue, "session_7")
    queue.update_item("session_7", package_status="uploaded", remote_analysis_status="queued")

    fake_uploader = MagicMock()
    coordinator = SyncCoordinator(upload_queue=queue, uploader=fake_uploader)
    coordinator.poll_remote_statuses()

    fake_uploader.get_status.assert_not_called()
