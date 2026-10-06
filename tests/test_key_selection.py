"""
Choosing the API key the server accepts. After the 2026-10-02 key rotation a stale key saved in
Settings overrode the correct one built into the exe, and a whole practice night's uploads were
refused ("Invalid or expired API token") and then, past the retry cap, never retried.
"""
from pathlib import Path
from unittest.mock import MagicMock

import pytest

import app.config as config
import app.uploader as uploader
from app.config import settings
from app.sync_coordinator import SyncCoordinator, _is_auth_failure
from app.upload_queue import UploadQueue
from app.uploader import SessionUploader, UploadResult, probe_api_key

URL = "http://server.example:8000"


class FakeResponse:
    def __init__(self, status_code=200, payload=None):
        self.status_code = status_code
        self._payload = payload or {}
        self.text = ""

    def json(self):
        return self._payload


class KeyedHttp:
    """Accepts only the keys in `good`; records every request."""

    def __init__(self, good, error=None, other_status=None):
        self.good, self.error, self.other_status = set(good), error, other_status
        self.calls = []

    def _auth(self, headers):
        return (headers or {}).get("Authorization", "").replace("Bearer ", "")

    def get(self, url, headers=None, timeout=None):
        self.calls.append(("GET", url, self._auth(headers)))
        if self.error:
            raise self.error
        if self.other_status:
            return FakeResponse(self.other_status)
        return FakeResponse(200 if self._auth(headers) in self.good else 401, {"detail": "Invalid or expired API token."})

    def post(self, url, headers=None, data=None, files=None, timeout=None):
        self.calls.append(("POST", url, self._auth(headers)))
        if self._auth(headers) in self.good:
            return FakeResponse(200, {"session_id": "s1", "job_id": "j1", "status": "queued"})
        return FakeResponse(401, {"detail": "Invalid or expired API token."})


@pytest.fixture(autouse=True)
def _isolated(monkeypatch):
    saved = {k: settings.get(k) for k in ("server_url", "api_key", "max_upload_retries")}
    settings.set_many({"max_upload_retries": 5})                  # the retry tests depend on the cap; other tests change it
    monkeypatch.setattr(settings, "save", lambda: None)          # never touch the real settings.json
    uploader._key_cache.clear()
    yield
    settings.set_many(saved)
    uploader._key_cache.clear()


def configure(monkeypatch, manual, embedded):
    settings.set_many({"server_url": URL, "api_key": manual})
    monkeypatch.setattr(config, "_embedded_api_key", lambda: embedded)


def test_candidates_are_the_saved_key_then_the_built_in_one(monkeypatch):
    configure(monkeypatch, "saved", "built-in")
    assert settings.api_key_candidates() == ["saved", "built-in"]


def test_candidates_collapse_duplicates_and_blanks(monkeypatch):
    configure(monkeypatch, "same", "same")
    assert settings.api_key_candidates() == ["same"]
    configure(monkeypatch, "", "built-in")
    assert settings.api_key_candidates() == ["built-in"]
    configure(monkeypatch, "saved", "")
    assert settings.api_key_candidates() == ["saved"]
    configure(monkeypatch, "", "")
    assert settings.api_key_candidates() == []


def test_a_stale_saved_key_gives_way_to_the_built_in_one_and_is_forgotten(monkeypatch):
    configure(monkeypatch, "stale", "fresh")
    http = KeyedHttp(good={"fresh"})
    check = probe_api_key(http)
    assert (check.key, check.accepted) == ("fresh", True)
    assert [c[2] for c in http.calls] == ["stale", "fresh"]
    assert settings.get("api_key") == ""                         # the stale one is dropped for good


def test_a_working_saved_key_is_kept(monkeypatch):
    configure(monkeypatch, "dev-key", "built-in")
    http = KeyedHttp(good={"dev-key", "built-in"})
    check = probe_api_key(http)
    assert (check.key, check.accepted) == ("dev-key", True)
    assert settings.get("api_key") == "dev-key"
    assert len(http.calls) == 1


def test_when_the_server_refuses_both_keys_that_is_reported_not_guessed(monkeypatch):
    configure(monkeypatch, "stale", "also-stale")
    check = probe_api_key(KeyedHttp(good=set()))
    assert check.accepted is False
    assert settings.get("api_key") == "stale"                    # nothing is forgotten on a total refusal


def test_an_unreachable_or_unwell_server_decides_nothing(monkeypatch):
    configure(monkeypatch, "stale", "fresh")
    assert probe_api_key(KeyedHttp(good=set(), error=ConnectionError("down"))).accepted is None
    uploader._key_cache.clear()
    assert probe_api_key(KeyedHttp(good=set(), other_status=503)).accepted is None
    assert settings.get("api_key") == "stale"


def test_one_key_means_no_extra_requests(monkeypatch):
    configure(monkeypatch, "only", "")
    http = KeyedHttp(good={"only"})
    assert probe_api_key(http).accepted is None and http.calls == []


def test_the_answer_is_remembered_for_a_while(monkeypatch):
    configure(monkeypatch, "stale", "fresh")
    monkeypatch.setattr(settings, "forget_manual_api_key", lambda: None)   # keep both keys in play
    http = KeyedHttp(good={"fresh"})
    probe_api_key(http)
    assert len(http.calls) == 2                                  # stale refused, fresh accepted
    probe_api_key(http)
    probe_api_key(http)
    assert len(http.calls) == 2                                  # the later answers come from memory
    monkeypatch.setattr(uploader, "time", MagicMock(monotonic=lambda: uploader._KEY_OK_TTL + 1e6))
    probe_api_key(http)
    assert len(http.calls) == 4                                  # and are rechecked once they are old


def test_an_upload_goes_out_with_the_key_the_server_accepts(monkeypatch, tmp_path: Path):
    configure(monkeypatch, "stale", "fresh")
    pkg = tmp_path / "s.r6session"
    pkg.write_bytes(b"x" * 1000)
    http = KeyedHttp(good={"fresh"})
    result = SessionUploader(http=http).upload_package(pkg)
    assert result.success
    posts = [c for c in http.calls if c[0] == "POST"]
    assert len(posts) == 1 and posts[0][2] == "fresh"


def test_an_upload_is_not_even_sent_when_every_key_is_refused(monkeypatch, tmp_path: Path):
    configure(monkeypatch, "stale", "also-stale")
    pkg = tmp_path / "s.r6session"
    pkg.write_bytes(b"x" * 1000)
    http = KeyedHttp(good=set())
    result = SessionUploader(http=http).upload_package(pkg)
    assert not result.success and result.status_code == 401
    assert "API token" in result.error and _is_auth_failure(result.error)
    assert not [c for c in http.calls if c[0] == "POST"]         # no 80 MB body sent to a server that will refuse it


# ── the retry policy ──────────────────────────────────────────────────────

def test_which_errors_count_as_a_refused_key():
    assert _is_auth_failure("Invalid or expired API token.")
    assert _is_auth_failure("Authentication credentials were not provided.")
    assert not _is_auth_failure("Invalid package: checksum mismatch")
    assert not _is_auth_failure(None) and not _is_auth_failure("")


def make_queue(tmp_path: Path, error: str, retries: int) -> tuple[UploadQueue, SyncCoordinator]:
    queue = UploadQueue(queue_dir=tmp_path / "q")
    pkg = queue.queue_dir / "session_1.r6session"
    pkg.write_bytes(b"PKG")
    queue.add_item("session_1", pkg, package_status="pending_upload")
    queue.update_item("session_1", package_status="upload_failed", retry_count=retries, last_error=error)
    item = queue.get_item("session_1")
    item.updated_at = "2020-01-01T00:00:00+00:00"                # long enough ago that no backoff applies
    return queue, SyncCoordinator(upload_queue=queue, uploader=MagicMock())


def test_a_package_the_server_refused_stays_given_up_on_past_the_retry_cap(tmp_path: Path):
    queue, coord = make_queue(tmp_path, "Invalid package: checksum mismatch", retries=9)
    assert coord._eligible_for_retry(queue.get_item("session_1")) is False


def test_a_refused_key_never_uses_up_the_retries(tmp_path: Path):
    queue, coord = make_queue(tmp_path, "Invalid or expired API token.", retries=9)
    assert coord._eligible_for_retry(queue.get_item("session_1")) is True


def test_a_network_failure_still_keeps_retrying(tmp_path: Path):
    queue, coord = make_queue(tmp_path, "Upload request failed: boom", retries=9)
    assert coord._eligible_for_retry(queue.get_item("session_1")) is True
