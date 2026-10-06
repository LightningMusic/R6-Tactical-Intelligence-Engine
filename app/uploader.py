"""
Headless HTTP client for uploading .r6session packages to the remote
FastAPI server (see server/api/v1.py). No GUI/Qt dependency, no module-level
network calls, and safe to use from any thread — connection settings
(server_url, api_key, timeout) are read from app.config.settings at call
time so a settings change takes effect without restarting the app.

Error messages are always redacted before being returned or logged: the
configured api_key is never allowed to appear in an exception string,
log line, or returned UploadResult.
"""

import io
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, BinaryIO, Optional

try:
    import requests  # type: ignore[import-untyped]
except ImportError:  # pragma: no cover - exercised only when requests is absent
    requests = None  # type: ignore[assignment]


class UploadError(Exception):
    """Raised for configuration errors that prevent even attempting a request."""


def redact(text: str, *secrets: Optional[str]) -> str:
    """Replaces any occurrence of a configured secret (e.g. api_key) with a
    placeholder. Safe to call with None/empty secrets."""
    for secret in secrets:
        if secret:
            text = text.replace(secret, "[REDACTED]")
    return text


class MultipartFileBody:
    """
    A multipart/form-data body with a single file field, read from disk on
    demand instead of being built in memory.

    requests' own files= handling reads the whole file into one bytes object
    and hands it to a single sendall(). On Python's SSL sockets the socket
    timeout then covers that whole send, so a 50-100 MB package over a slow
    uplink hit "The write operation timed out" no matter how healthy the
    connection was. Streamed in small blocks, the timeout only has to cover
    one block at a time -- a slow link just takes longer instead of failing.
    """

    def __init__(self, field: str, filename: str, fileobj: BinaryIO, size: int,
                 content_type: str = "application/octet-stream") -> None:
        boundary = uuid.uuid4().hex
        self.content_type = f"multipart/form-data; boundary={boundary}"
        head = (
            f'--{boundary}\r\n'
            f'Content-Disposition: form-data; name="{field}"; filename="{filename}"\r\n'
            f'Content-Type: {content_type}\r\n\r\n'
        ).encode("utf-8")
        tail = f"\r\n--{boundary}--\r\n".encode("utf-8")
        self._parts: list[Any] = [io.BytesIO(head), fileobj, io.BytesIO(tail)]
        self._index = 0
        self._length = len(head) + size + len(tail)

    def __len__(self) -> int:
        return self._length

    def __iter__(self):
        while True:
            block = self.read(64 * 1024)
            if not block:
                return
            yield block

    def read(self, size: int = -1) -> bytes:
        if size is None or size < 0:
            return b"".join(iter(lambda: self.read(64 * 1024), b""))
        while self._index < len(self._parts):
            block = self._parts[self._index].read(size)
            if block:
                return block
            self._index += 1
        return b""


_KEY_OK_TTL = 600.0       # a key the server accepted is trusted for ten minutes
_KEY_BAD_TTL = 30.0       # a refusal is rechecked soon: it may just have been fixed
_key_cache: dict = {}


@dataclass
class KeyCheck:
    key: str                    # the key to send ("" when there is none)
    accepted: Optional[bool]    # True: the server took it. False: it refused every key. None: not checked


def probe_api_key(http: Any = None) -> KeyCheck:
    """Which API key does the server accept?

    Only matters when the app has two different keys to choose from: the one saved in Settings
    and the one built into the exe. After a key rotation the saved one goes stale, and because it is
    preferred it locked a whole practice night out of its own server (every upload "Invalid or
    expired API token"). Here the saved key is tried first, then the built-in one; the first the
    server accepts wins, and a refused saved key is dropped so it cannot do this again. A server that
    can't be reached, or answers with anything but 200/401, decides nothing (the real request will
    report the trouble). With one key (or none) there is nothing to choose and nothing is sent."""
    from app.config import settings

    url = (settings.SERVER_URL or "").rstrip("/")
    candidates = settings.api_key_candidates()
    if len(candidates) < 2 or not url:
        return KeyCheck(candidates[0] if candidates else "", None)
    client = http if http is not None else requests
    if client is None:
        return KeyCheck(candidates[0], None)

    cache_key = (url, tuple(candidates))
    now = time.monotonic()
    hit = _key_cache.get(cache_key)
    if hit and now - hit[2] < (_KEY_OK_TTL if hit[1] else _KEY_BAD_TTL):
        return KeyCheck(hit[0], hit[1])

    for i, key in enumerate(candidates):
        try:
            resp = client.get(f"{url}/api/v1/auth/test", headers={"Authorization": f"Bearer {key}"}, timeout=10)
        except Exception:
            return KeyCheck(candidates[0], None)
        code = getattr(resp, "status_code", None)
        if code == 200:
            _key_cache[cache_key] = (key, True, now)
            if i > 0:
                print("[Uploader] The API key saved in Settings was refused by the server; "
                      "using the one built into this app and clearing the saved one.")
                settings.forget_manual_api_key()
            return KeyCheck(key, True)
        if code != 401:
            return KeyCheck(candidates[0], None)
    _key_cache[cache_key] = (candidates[0], False, now)
    return KeyCheck(candidates[0], False)


def resolve_api_key(http: Any = None) -> str:
    return probe_api_key(http).key


@dataclass
class UploadResult:
    success: bool
    session_id: Optional[str] = None
    job_id: Optional[str] = None
    status: Optional[str] = None
    is_duplicate: bool = False
    error: Optional[str] = None
    status_code: Optional[int] = None


class SessionUploader:
    """
    Headless HTTP client for the client -> server upload/status API.

    A custom `http` object (anything exposing `.post()` / `.get()` with the
    `requests`-compatible signature/response shape) can be injected for
    testing, so unit tests never need a real network connection or the
    `requests` package to be installed.
    """

    def __init__(self, http: Any = None) -> None:
        self._http = http

    def _client(self) -> Any:
        if self._http is not None:
            return self._http
        if requests is None:
            raise UploadError(
                "The 'requests' package is not installed — cannot perform network uploads."
            )
        return requests

    @staticmethod
    def _extract_detail(resp: Any) -> str:
        try:
            data = resp.json()
            if isinstance(data, dict) and "detail" in data:
                return str(data["detail"])
            return str(data)
        except Exception:
            return str(getattr(resp, "text", "")) or f"HTTP {getattr(resp, 'status_code', '?')}"

    def upload_package(self, package_path: Path) -> UploadResult:
        """
        Uploads a single .r6session archive. Never raises — all failure
        modes (missing config, network error, non-2xx response, malformed
        response) are returned as an UploadResult(success=False, ...).
        """
        from app.config import settings

        server_url = settings.SERVER_URL
        timeout = settings.REQUEST_TIMEOUT_SECONDS

        if not server_url:
            return UploadResult(success=False, error="No server_url configured.")
        if not package_path.exists():
            return UploadResult(success=False, error=f"Package file not found: {package_path.name}")

        # Pick the key the server accepts BEFORE sending tens of megabytes: a server that refuses
        # the key closes the connection early, which surfaces as a bogus "SSL EOF" network error.
        check = probe_api_key(self._http)
        api_key = check.key
        if check.accepted is False:
            return UploadResult(
                success=False, status_code=401,
                error="Invalid or expired API token. The server refused both the key saved in Settings "
                      "and the one built into this app.",
            )

        url = f"{server_url}/api/v1/sessions/upload"
        headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}

        try:
            http = self._client()
        except UploadError as e:
            return UploadResult(success=False, error=str(e))

        try:
            with package_path.open("rb") as f:
                body = MultipartFileBody(
                    "file", package_path.name, f,
                    size=package_path.stat().st_size, content_type="application/zip",
                )
                headers = {**headers, "Content-Type": body.content_type}
                # (connect, per-operation) -- see MultipartFileBody for why the
                # second number no longer has to cover the whole upload.
                resp = http.post(url, headers=headers, data=body, timeout=(10, timeout))
        except Exception as e:
            return UploadResult(success=False, error=redact(f"Upload request failed: {e}", api_key))

        status_code = getattr(resp, "status_code", None)
        if status_code == 200:
            try:
                data = resp.json()
            except Exception:
                return UploadResult(success=False, error="Server returned an invalid response.", status_code=status_code)
            return UploadResult(
                success=True,
                session_id=data.get("session_id"),
                job_id=data.get("job_id"),
                status=data.get("status"),
                is_duplicate=bool(data.get("is_duplicate", False)),
                status_code=status_code,
            )

        return UploadResult(
            success=False,
            error=redact(self._extract_detail(resp), api_key),
            status_code=status_code,
        )

    def get_status(self, session_id: str) -> UploadResult:
        """Polls remote processing status for an already-uploaded session."""
        from app.config import settings

        server_url = settings.SERVER_URL
        api_key = resolve_api_key(self._http)
        timeout = settings.REQUEST_TIMEOUT_SECONDS

        if not server_url:
            return UploadResult(success=False, error="No server_url configured.")

        url = f"{server_url}/api/v1/sessions/{session_id}/status"
        headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}

        try:
            http = self._client()
        except UploadError as e:
            return UploadResult(success=False, error=str(e))

        try:
            resp = http.get(url, headers=headers, timeout=timeout)
        except Exception as e:
            return UploadResult(success=False, error=redact(f"Status request failed: {e}", api_key))

        status_code = getattr(resp, "status_code", None)
        if status_code == 200:
            try:
                data = resp.json()
            except Exception:
                return UploadResult(success=False, error="Server returned an invalid response.", status_code=status_code)
            return UploadResult(
                success=True,
                session_id=session_id,
                job_id=data.get("job_id"),
                status=data.get("status"),
                status_code=status_code,
            )

        return UploadResult(
            success=False,
            error=redact(self._extract_detail(resp), api_key),
            status_code=status_code,
        )

    def test_connection(self) -> UploadResult:
        """Hits the authenticated /auth/test endpoint — used by a Settings 'Test Connection' action."""
        from app.config import settings

        server_url = settings.SERVER_URL
        api_key = resolve_api_key(self._http)
        timeout = settings.REQUEST_TIMEOUT_SECONDS

        if not server_url:
            return UploadResult(success=False, error="No server_url configured.")

        url = f"{server_url}/api/v1/auth/test"
        headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}

        try:
            http = self._client()
        except UploadError as e:
            return UploadResult(success=False, error=str(e))

        try:
            resp = http.get(url, headers=headers, timeout=timeout)
        except Exception as e:
            return UploadResult(success=False, error=redact(f"Connection test failed: {e}", api_key))

        status_code = getattr(resp, "status_code", None)
        if status_code == 200:
            return UploadResult(success=True, status="authenticated", status_code=status_code)

        return UploadResult(
            success=False,
            error=redact(self._extract_detail(resp), api_key),
            status_code=status_code,
        )
