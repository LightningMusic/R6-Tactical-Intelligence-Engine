import pytest
from pathlib import Path

from app.config import settings
from app.uploader import SessionUploader, UploadResult, redact


class FakeResponse:
    def __init__(self, status_code: int, json_data: dict | None = None, text: str = ""):
        self.status_code = status_code
        self._json_data = json_data
        self.text = text

    def json(self):
        if self._json_data is None:
            raise ValueError("no json body")
        return self._json_data


class FakeHttp:
    """Records calls and returns a pre-programmed response/exception, so no
    real network access is ever needed to exercise SessionUploader."""

    def __init__(self, response: FakeResponse | None = None, raise_exc: Exception | None = None):
        self._response = response
        self._raise_exc = raise_exc
        self.calls: list[dict] = []

    def post(self, url, headers=None, files=None, data=None, timeout=None):
        # Read the streamed body now, while the file handle is still open.
        body = data.read() if data is not None else None
        self.calls.append({
            "method": "POST", "url": url, "headers": headers, "timeout": timeout,
            "body": body, "declared_length": len(data) if data is not None else None,
        })
        if self._raise_exc:
            raise self._raise_exc
        return self._response

    def get(self, url, headers=None, timeout=None):
        self.calls.append({"method": "GET", "url": url, "headers": headers, "timeout": timeout})
        if self._raise_exc:
            raise self._raise_exc
        return self._response


@pytest.fixture(autouse=True)
def _reset_settings():
    yield
    settings.set_many({"analysis_mode": "local", "server_url": "", "api_key": ""})


@pytest.fixture
def sample_package(tmp_path: Path) -> Path:
    pkg = tmp_path / "session_abc.r6session"
    pkg.write_bytes(b"FAKE_ARCHIVE_BYTES")
    return pkg


def test_upload_fails_without_server_url(sample_package: Path):
    settings.set_many({"server_url": "", "api_key": ""})
    uploader = SessionUploader(http=FakeHttp())
    result = uploader.upload_package(sample_package)
    assert result.success is False
    assert "server_url" in result.error


def test_upload_fails_when_package_missing(tmp_path: Path):
    settings.set_many({"server_url": "http://localhost:8000", "api_key": "k"})
    uploader = SessionUploader(http=FakeHttp())
    result = uploader.upload_package(tmp_path / "does_not_exist.r6session")
    assert result.success is False
    assert "not found" in result.error


def test_upload_success(sample_package: Path):
    settings.set_many({"server_url": "http://localhost:8000/", "api_key": "dev_secret_key"})
    fake = FakeHttp(response=FakeResponse(200, {
        "session_id": "session_abc", "job_id": "job_1", "status": "queued", "is_duplicate": False,
    }))
    uploader = SessionUploader(http=fake)
    result = uploader.upload_package(sample_package)

    assert result.success is True
    assert result.session_id == "session_abc"
    assert result.job_id == "job_1"
    assert result.is_duplicate is False
    # server_url trailing slash must not be duplicated
    assert fake.calls[0]["url"] == "http://localhost:8000/api/v1/sessions/upload"
    assert fake.calls[0]["headers"]["Authorization"] == "Bearer dev_secret_key"


def _parse_multipart(content_type: str, body: bytes):
    from email.parser import BytesParser
    from email.policy import HTTP

    msg = BytesParser(policy=HTTP).parsebytes(
        b"Content-Type: " + content_type.encode() + b"\r\n\r\n" + body
    )
    parts = list(msg.iter_parts())
    assert len(parts) == 1
    return parts[0]


def test_upload_streams_a_well_formed_multipart_body(tmp_path: Path):
    settings.set_many({"server_url": "http://localhost:8000", "api_key": "k"})
    payload = bytes(range(256)) * 5000          # 1.28 MB, includes every byte value
    pkg = tmp_path / "session_big.r6session"
    pkg.write_bytes(payload)
    fake = FakeHttp(response=FakeResponse(200, {"session_id": "s", "job_id": "j", "status": "queued"}))

    assert SessionUploader(http=fake).upload_package(pkg).success is True

    call = fake.calls[0]
    assert call["declared_length"] == len(call["body"])     # Content-Length will be honest
    part = _parse_multipart(call["headers"]["Content-Type"], call["body"])
    assert part.get_param("name", header="content-disposition") == "file"
    assert part.get_filename() == "session_big.r6session"
    assert part.get_content() == payload


def test_upload_timeout_is_per_operation_not_for_the_whole_send(sample_package: Path):
    settings.set_many({"server_url": "http://localhost:8000", "api_key": "k"})
    fake = FakeHttp(response=FakeResponse(200, {"session_id": "s", "job_id": "j", "status": "queued"}))
    SessionUploader(http=fake).upload_package(sample_package)
    connect, per_op = fake.calls[0]["timeout"]
    assert connect > 0 and per_op == settings.REQUEST_TIMEOUT_SECONDS


def test_upload_body_survives_a_real_requests_round_trip(tmp_path: Path):
    """Loopback check with the real `requests` library: it must accept the
    streamed body, send an accurate Content-Length, and deliver every byte."""
    pytest.importorskip("requests")
    import json
    import threading
    from http.server import BaseHTTPRequestHandler, HTTPServer

    received: dict = {}

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            length = int(self.headers["Content-Length"])
            received["headers"] = dict(self.headers)
            received["body"] = self.rfile.read(length)
            reply = json.dumps({"session_id": "s", "job_id": "j", "status": "queued"}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(reply)))
            self.end_headers()
            self.wfile.write(reply)

        def log_message(self, *args):
            pass

    server = HTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        payload = b"\x00\x01\xfe\xff" * 300_000     # ~1.2 MB
        pkg = tmp_path / "session_real.r6session"
        pkg.write_bytes(payload)
        settings.set_many({"server_url": f"http://127.0.0.1:{server.server_port}", "api_key": "k"})

        result = SessionUploader().upload_package(pkg)
    finally:
        server.shutdown()

    assert result.success is True and result.job_id == "j"
    assert received["headers"]["Authorization"] == "Bearer k"
    part = _parse_multipart(received["headers"]["Content-Type"], received["body"])
    assert part.get_content() == payload


def test_upload_http_error_response(sample_package: Path):
    settings.set_many({"server_url": "http://localhost:8000", "api_key": "k"})
    fake = FakeHttp(response=FakeResponse(409, {"detail": "Session ID already exists with a different package content."}))
    uploader = SessionUploader(http=fake)
    result = uploader.upload_package(sample_package)

    assert result.success is False
    assert result.status_code == 409
    assert "already exists" in result.error


def test_upload_network_exception_redacts_api_key(sample_package: Path):
    secret = "super_secret_token_xyz"
    settings.set_many({"server_url": "http://localhost:8000", "api_key": secret})
    fake = FakeHttp(raise_exc=ConnectionError(f"could not connect, auth was Bearer {secret}"))
    uploader = SessionUploader(http=fake)
    result = uploader.upload_package(sample_package)

    assert result.success is False
    assert secret not in result.error
    assert "[REDACTED]" in result.error


def test_redact_helper_handles_empty_and_none():
    assert redact("hello world") == "hello world"
    assert redact("hello world", None, "") == "hello world"
    assert redact("token=abc123 leaked", "abc123") == "token=[REDACTED] leaked"


def test_get_status_success():
    settings.set_many({"server_url": "http://localhost:8000", "api_key": "k"})
    fake = FakeHttp(response=FakeResponse(200, {"job_id": "job_1", "status": "completed"}))
    uploader = SessionUploader(http=fake)
    result = uploader.get_status("session_abc")

    assert result.success is True
    assert result.status == "completed"
    assert fake.calls[0]["url"] == "http://localhost:8000/api/v1/sessions/session_abc/status"


def test_get_status_not_found():
    settings.set_many({"server_url": "http://localhost:8000", "api_key": "k"})
    fake = FakeHttp(response=FakeResponse(404, {"detail": "No job found for session session_abc."}))
    uploader = SessionUploader(http=fake)
    result = uploader.get_status("session_abc")

    assert result.success is False
    assert result.status_code == 404
