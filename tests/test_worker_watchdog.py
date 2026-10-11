"""The worker restarts a server that is alive but broken (server/worker.py restart_reason)."""
from server import worker


def test_running_out_of_file_handles_means_restart():
    assert worker.restart_reason((1010, 1024), None, 0.0) == "1010 of 1024 file handles in use"
    assert worker.restart_reason((300, 1024), None, 0.0) is None
    assert worker.restart_reason(None, None, 0.0) is None              # not Linux: nothing to judge


def test_a_worker_failing_non_stop_for_ten_minutes_means_restart():
    assert worker.restart_reason((10, 1024), 1000.0, 1000.0 + 599) is None
    assert "failed for 600 s" in worker.restart_reason((10, 1024), 1000.0, 1000.0 + 600)
