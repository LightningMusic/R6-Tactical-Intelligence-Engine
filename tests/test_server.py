import os
import io
import json
import zipfile
import hashlib
import pytest
from pathlib import Path
from fastapi.testclient import TestClient

# Verify server imports cleanly without PySide6 or GUI modules
def test_server_imports_without_gui():
    import sys
    assert "PySide6" not in sys.modules
    import server.main
    assert server.main.app is not None


from server.main import app
from server.config import server_settings
from server.repositories import ServerRepository
from server.services.package_validation import ServerPackageValidator
from server.worker import ServerWorker
from app.packaging import SessionPackage, generate_session_id


VALID_TOKEN = "dev_secret_key"
VALID_HASH = hashlib.sha256(VALID_TOKEN.encode("utf-8")).hexdigest().lower()
AUTH_HEADER = {"Authorization": f"Bearer {VALID_TOKEN}"}


@pytest.fixture
def test_repo(tmp_path: Path, monkeypatch):
    # Set isolated temporary server data directory
    srv_data = tmp_path / "server_data"
    monkeypatch.setattr(server_settings, "DATA_DIR", srv_data)
    monkeypatch.setattr(server_settings, "DATABASE_PATH", srv_data / "server_matches.db")
    monkeypatch.setattr(server_settings, "UPLOADS_DIR", srv_data / "uploads")
    monkeypatch.setattr(server_settings, "WORK_DIR", srv_data / "work")
    monkeypatch.setattr(server_settings, "REPORTS_DIR", srv_data / "reports")
    monkeypatch.setattr(server_settings, "LOGS_DIR", srv_data / "logs")
    monkeypatch.setattr(server_settings, "API_TOKEN_HASH", VALID_HASH)
    server_settings.ensure_directories()

    # Reinit database
    from server.database import ServerDatabase
    db = ServerDatabase(srv_data / "server_matches.db")
    repo = ServerRepository(db)
    monkeypatch.setattr("server.api.v1.repo", repo)
    monkeypatch.setattr("server.repositories.server_db", db)
    monkeypatch.setattr("server.services.session_processing.server_db", db)
    return repo


@pytest.fixture
def client(test_repo):
    with TestClient(app) as tc:
        yield tc


@pytest.fixture
def valid_package_file(tmp_path: Path) -> Path:
    rec_dir = tmp_path / "Match-2026-08-26_14-00-00-000"
    rec_dir.mkdir()
    rec1 = rec_dir / "Match-2026-08-26_14-00-00-000-R01.rec"
    rec1.write_bytes(b"REPLAY_HEADER_DATA_ROUND_1")

    sid = generate_session_id()
    pkg = SessionPackage.create_package(
        output_dir=tmp_path,
        session_id=sid,
        rec_files=[rec1],
        metadata={"map_name": "Border", "score_us": 4, "score_them": 1},
        telemetry={"rounds_parsed": 1},
        is_complete=True,
    )
    return pkg


def test_health_endpoint_unauthenticated(client: TestClient):
    resp = client.get("/api/v1/health")
    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"


def test_auth_token_configuration_modes(monkeypatch, client: TestClient):
    # 1. Unconfigured token -> fails closed HTTP 500
    monkeypatch.setattr(server_settings, "API_TOKEN_HASH", None)
    r1 = client.get("/api/v1/auth/test", headers=AUTH_HEADER)
    assert r1.status_code == 500
    assert "No server API token is configured" in r1.json()["detail"]

    # 2. Re-enable hash -> valid token accepts, invalid rejects
    monkeypatch.setattr(server_settings, "API_TOKEN_HASH", VALID_HASH)
    r2 = client.get("/api/v1/auth/test", headers=AUTH_HEADER)
    assert r2.status_code == 200

    r3 = client.get("/api/v1/auth/test", headers={"Authorization": "Bearer WRONG_TOKEN"})
    assert r3.status_code == 401


def test_upload_rejects_unauthenticated(client: TestClient, valid_package_file: Path):
    with valid_package_file.open("rb") as f:
        resp = client.post("/api/v1/sessions/upload", files={"file": (valid_package_file.name, f, "application/zip")})
    assert resp.status_code == 401


def test_upload_valid_package_returns_queued(client: TestClient, valid_package_file: Path):
    with valid_package_file.open("rb") as f:
        resp = client.post(
            "/api/v1/sessions/upload",
            headers=AUTH_HEADER,
            files={"file": (valid_package_file.name, f, "application/zip")},
        )
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "queued"
    assert data["is_duplicate"] is False
    assert "session_id" in data
    assert "job_id" in data


def test_upload_corrupt_and_tampered_packages(client: TestClient, tmp_path: Path):
    # 1. Corrupt ZIP
    corrupt_file = tmp_path / "corrupt.r6session"
    corrupt_file.write_bytes(b"NOT_A_ZIP_FILE_DATA")

    with corrupt_file.open("rb") as f:
        r1 = client.post(
            "/api/v1/sessions/upload",
            headers=AUTH_HEADER,
            files={"file": (corrupt_file.name, f, "application/zip")},
        )
    assert r1.status_code == 400
    assert "Package validation failed" in r1.json()["detail"]

    # 2. Checksum mismatch
    rec1 = tmp_path / "rec.rec"
    rec1.write_bytes(b"DATA")
    sid = generate_session_id()
    good_pkg = SessionPackage.create_package(tmp_path, sid, [rec1], {"map_name": "House"})

    tampered_pkg = tmp_path / "tampered.r6session"
    with zipfile.ZipFile(good_pkg, "r") as sz:
        with zipfile.ZipFile(tampered_pkg, "w") as dz:
            for item in sz.infolist():
                d = sz.read(item.filename)
                if item.filename == "metadata.json":
                    d = b'{"map_name": "TAMPERED"}'
                dz.writestr(item, d)

    with tampered_pkg.open("rb") as f:
        r2 = client.post(
            "/api/v1/sessions/upload",
            headers=AUTH_HEADER,
            files={"file": (tampered_pkg.name, f, "application/zip")},
        )
    assert r2.status_code == 400
    assert "Checksum mismatch" in r2.json()["detail"]


def test_zip_slip_rejection(client: TestClient, tmp_path: Path):
    malicious = tmp_path / "evil.r6session"
    m_data = json.dumps({"schema_version": "1.0", "files": []}).encode()
    with zipfile.ZipFile(malicious, "w") as zf:
        zf.writestr("manifest.json", m_data)
        zf.writestr("../evil.txt", b"EVIL")

    with malicious.open("rb") as f:
        resp = client.post(
            "/api/v1/sessions/upload",
            headers=AUTH_HEADER,
            files={"file": (malicious.name, f, "application/zip")},
        )
    assert resp.status_code == 400
    assert "Path traversal attempt blocked" in resp.json()["detail"]


def test_duplicate_policy_cases(client: TestClient, valid_package_file: Path, tmp_path: Path):
    # Upload 1
    with valid_package_file.open("rb") as f:
        r1 = client.post(
            "/api/v1/sessions/upload",
            headers=AUTH_HEADER,
            files={"file": (valid_package_file.name, f, "application/zip")},
        )
    assert r1.status_code == 200
    assert r1.json()["is_duplicate"] is False
    sid = r1.json()["session_id"]

    # Case 1: Same session ID + same package hash => Idempotent success (200, is_duplicate=True)
    with valid_package_file.open("rb") as f:
        r2 = client.post(
            "/api/v1/sessions/upload",
            headers=AUTH_HEADER,
            files={"file": (valid_package_file.name, f, "application/zip")},
        )
    assert r2.status_code == 200
    assert r2.json()["is_duplicate"] is True

    # Case 2: Same session ID + different package hash => Conflict 409
    rec2 = tmp_path / "rec2.rec"
    rec2.write_bytes(b"DIFFERENT_REPLAY_DATA")
    diff_pkg = SessionPackage.create_package(
        output_dir=tmp_path,
        session_id=sid,
        rec_files=[rec2],
        metadata={"map_name": "Kanal"},
    )
    with diff_pkg.open("rb") as f:
        r3 = client.post(
            "/api/v1/sessions/upload",
            headers=AUTH_HEADER,
            files={"file": (diff_pkg.name, f, "application/zip")},
        )
    assert r3.status_code == 409

    # Case 3: Different session ID + same package hash => Content duplicate success
    new_sid = generate_session_id()
    pkg3 = SessionPackage.create_package(
        output_dir=tmp_path,
        session_id=new_sid,
        rec_files=[rec2],
        metadata={"map_name": "Kanal"},
    )
    # Pre-register pkg3's SHA-256 in test_repo under a dummy session ID to simulate prior upload
    from app.packaging import calculate_sha256
    hash3 = calculate_sha256(pkg3)
    test_repo.create_session("session_dummy_orig", "Client", "Kanal")
    test_repo.create_package(hash3, "session_dummy_orig", pkg3.name, pkg3.stat().st_size)

    with pkg3.open("rb") as f:
        r4 = client.post(
            "/api/v1/sessions/upload",
            headers=AUTH_HEADER,
            files={"file": (pkg3.name, f, "application/zip")},
        )
    assert r4.status_code == 200
    assert r4.json()["is_duplicate"] is True
    assert r4.json()["session_id"] == "session_dummy_orig"


def test_worker_processing_flow_and_retry(client: TestClient, valid_package_file: Path, test_repo: ServerRepository):
    # 1. Upload valid package
    with valid_package_file.open("rb") as f:
        up_resp = client.post(
            "/api/v1/sessions/upload",
            headers=AUTH_HEADER,
            files={"file": (valid_package_file.name, f, "application/zip")},
        )
    assert up_resp.status_code == 200
    sid = up_resp.json()["session_id"]
    jid = up_resp.json()["job_id"]

    # Check status endpoint
    st1 = client.get(f"/api/v1/sessions/{sid}/status", headers=AUTH_HEADER)
    assert st1.status_code == 200
    assert st1.json()["status"] == "queued"

    # Execute worker processing manually with test_repo
    worker = ServerWorker(repo=test_repo)
    processed = worker.process_single_job()
    assert processed is True

    # Verify job status is completed
    st2 = client.get(f"/api/v1/sessions/{sid}/status", headers=AUTH_HEADER)
    assert st2.status_code == 200
    assert st2.json()["status"] == "completed"

    # Simulate job failure and verify retry
    test_repo.update_job_status(job_id=jid, status="failed", error_message="Test failure")

    retry_resp = client.post(f"/api/v1/sessions/{sid}/retry", headers=AUTH_HEADER)
    assert retry_resp.status_code == 200
    assert retry_resp.json()["status"] == "queued"


def test_deferred_report_and_pdf_endpoints(client: TestClient):
    r1 = client.get("/api/v1/sessions/session_test/report", headers=AUTH_HEADER)
    assert r1.status_code == 501
    r2 = client.get("/api/v1/sessions/session_test/pdf", headers=AUTH_HEADER)
    assert r2.status_code == 501
