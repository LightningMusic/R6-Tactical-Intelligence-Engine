import os
import json
import zipfile
import pytest
from pathlib import Path

from app.packaging import SessionPackage, generate_session_id, generate_source_fingerprint, calculate_sha256


@pytest.fixture
def sample_rec_folder(tmp_path: Path) -> Path:
    rec_dir = tmp_path / "Match-2026-08-26_12-00-00-000"
    rec_dir.mkdir()
    rec1 = rec_dir / "Match-2026-08-26_12-00-00-000-R01.rec"
    rec1.write_bytes(b"REPLAY_HEADER_DATA_ROUND_1_SAMPLE_CONTENT")
    rec2 = rec_dir / "Match-2026-08-26_12-00-00-000-R02.rec"
    rec2.write_bytes(b"REPLAY_HEADER_DATA_ROUND_2_SAMPLE_CONTENT")
    return rec_dir


def test_generate_session_id_and_fingerprint(sample_rec_folder: Path):
    rec_files = sorted(sample_rec_folder.glob("*.rec"))
    sid1 = generate_session_id()
    sid2 = generate_session_id()

    assert sid1.startswith("session_")
    assert sid2.startswith("session_")
    assert sid1 != sid2, "Session IDs generated per package creation must be unique UUID4s"

    fp1 = generate_source_fingerprint(sample_rec_folder.name, rec_files)
    fp2 = generate_source_fingerprint(sample_rec_folder.name, rec_files)
    assert fp1 == fp2, "Source fingerprint must be deterministic for identical folder and content"


def test_create_and_verify_package(tmp_path: Path, sample_rec_folder: Path):
    rec_files = sorted(sample_rec_folder.glob("*.rec"))
    sid = generate_session_id()

    meta = {"map_name": "Oregon", "score_us": 4, "score_them": 2}
    telemetry = {"rounds_parsed": 2}

    pkg_path = SessionPackage.create_package(
        output_dir=tmp_path,
        session_id=sid,
        rec_files=rec_files,
        metadata=meta,
        telemetry=telemetry,
        is_complete=True,
    )

    assert pkg_path.exists()
    assert pkg_path.name == f"{sid}.r6session"

    valid, msg = SessionPackage.verify_package(pkg_path)
    assert valid, f"Verification failed: {msg}"

    # Extract package and inspect
    extract_dir = tmp_path / "extracted"
    SessionPackage.extract_package(pkg_path, extract_dir)

    manifest_file = extract_dir / "manifest.json"
    assert manifest_file.exists()

    manifest_data = json.loads(manifest_file.read_text(encoding="utf-8"))
    assert manifest_data["session_id"] == sid
    assert manifest_data["is_complete"] is True
    assert len(manifest_data["files"]) >= 3  # metadata, telemetry, 2 recs


def test_partial_package_marked_incomplete(tmp_path: Path, sample_rec_folder: Path):
    rec_files = sorted(sample_rec_folder.glob("*.rec"))
    sid = generate_session_id()

    pkg_path = SessionPackage.create_package(
        output_dir=tmp_path,
        session_id=sid,
        rec_files=rec_files,
        metadata={"map_name": "Clubhouse"},
        is_complete=False,
    )

    valid, msg = SessionPackage.verify_package(pkg_path)
    assert valid, f"Verification failed: {msg}"

    extract_dir = tmp_path / "extracted_partial"
    SessionPackage.extract_package(pkg_path, extract_dir)

    manifest_data = json.loads((extract_dir / "manifest.json").read_text(encoding="utf-8"))
    assert manifest_data["is_complete"] is False


def test_corrupt_package_detection(tmp_path: Path, sample_rec_folder: Path):
    rec_files = sorted(sample_rec_folder.glob("*.rec"))
    sid = generate_session_id()

    pkg_path = SessionPackage.create_package(
        output_dir=tmp_path,
        session_id=sid,
        rec_files=rec_files,
        metadata={"map_name": "Bank"},
    )

    # Tamper with archive content
    corrupt_path = tmp_path / f"{sid}_corrupt.r6session"
    with zipfile.ZipFile(pkg_path, "r") as src_zip:
        with zipfile.ZipFile(corrupt_path, "w") as dst_zip:
            for item in src_zip.infolist():
                data = src_zip.read(item.filename)
                if item.filename == "metadata.json":
                    data = b'{"map_name": "TAMPERED"}'
                dst_zip.writestr(item, data)

    valid, msg = SessionPackage.verify_package(corrupt_path)
    assert not valid
    assert "Checksum mismatch" in msg


def test_path_traversal_prevention(tmp_path: Path):
    malicious_zip = tmp_path / "malicious.r6session"
    manifest_bytes = json.dumps({"schema_version": "1.0", "files": []}).encode()

    with zipfile.ZipFile(malicious_zip, "w") as zf:
        zf.writestr("manifest.json", manifest_bytes)
        zf.writestr("../evil.txt", b"malicious content")

    valid, msg = SessionPackage.verify_package(malicious_zip)
    assert not valid
    assert "Unsafe file path" in msg
