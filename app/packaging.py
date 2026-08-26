import os
import json
import zipfile
import hashlib
import uuid
from pathlib import Path
from datetime import datetime, timezone
from typing import Optional, Tuple, Dict, Any


def generate_session_id(*args: Any, **kwargs: Any) -> str:
    """
    Generates a unique UUIDv4 session ID when a package is first created.
    Format: session_<uuid4>
    """
    return f"session_{uuid.uuid4()}"


def generate_source_fingerprint(folder_name: str, rec_files: list[Path]) -> str:
    """
    Generates a canonical fingerprint from match folder name and replay header contents.
    Used exclusively for source content duplication checks.
    """
    first_hash = ""
    if rec_files and rec_files[0].exists():
        first_hash = calculate_sha256(rec_files[0])[:16]
    return f"{folder_name}:{first_hash}"


def calculate_sha256(file_path: Path) -> str:
    """Computes SHA-256 checksum for a file in 64KB chunks."""
    sha = hashlib.sha256()
    with file_path.open("rb") as f:
        while chunk := f.read(65536):
            sha.update(chunk)
    return sha.hexdigest()


def calculate_bytes_sha256(data: bytes) -> str:
    """Computes SHA-256 checksum for in-memory bytes."""
    return hashlib.sha256(data).hexdigest()


class SessionPackage:
    """
    Handles atomic creation, checksum verification, and safe extraction of immutable .r6session ZIP archives.
    Archives are checksummed and manifest-verified.
    """

    SCHEMA_VERSION = "1.0"

    @classmethod
    def create_package(
        cls,
        output_dir: Path,
        session_id: str,
        rec_files: list[Path],
        metadata: Dict[str, Any],
        telemetry: Optional[Dict[str, Any]] = None,
        audio_files: Optional[list[Path]] = None,
        transcripts: Optional[Dict[str, Any]] = None,
        is_complete: bool = True,
        source_fingerprint: str = "",
    ) -> Path:
        """
        Atomically creates an immutable .r6session zip archive.
        Writes to a temporary .tmp file first, then renames upon completion.
        """
        output_dir.mkdir(parents=True, exist_ok=True)
        final_path = output_dir / f"{session_id}.r6session"
        tmp_path = output_dir / f"{session_id}.r6session.tmp"

        if tmp_path.exists():
            try:
                tmp_path.unlink()
            except Exception:
                pass

        file_manifest: list[Dict[str, Any]] = []

        # Enforce metadata inclusion of session_id and source_fingerprint
        full_metadata = dict(metadata)
        full_metadata["session_id"] = session_id
        if source_fingerprint:
            full_metadata["source_fingerprint"] = source_fingerprint

        metadata_bytes = json.dumps(full_metadata, indent=2).encode("utf-8")
        file_manifest.append({
            "path": "metadata.json",
            "sha256": calculate_bytes_sha256(metadata_bytes),
            "size_bytes": len(metadata_bytes),
        })

        telemetry_bytes: Optional[bytes] = None
        if telemetry is not None:
            telemetry_bytes = json.dumps(telemetry, indent=2).encode("utf-8")
            file_manifest.append({
                "path": "telemetry.json",
                "sha256": calculate_bytes_sha256(telemetry_bytes),
                "size_bytes": len(telemetry_bytes),
            })

        transcripts_bytes: Optional[bytes] = None
        transcript_status = "omitted"
        if transcripts is not None:
            transcript_status = "included"
            transcripts_bytes = json.dumps(transcripts, indent=2).encode("utf-8")
            file_manifest.append({
                "path": "transcripts/transcripts.json",
                "sha256": calculate_bytes_sha256(transcripts_bytes),
                "size_bytes": len(transcripts_bytes),
            })

        # Add replay files to manifest
        for rec in rec_files:
            if rec.exists():
                arc_path = f"replays/{rec.name}"
                file_manifest.append({
                    "path": arc_path,
                    "sha256": calculate_sha256(rec),
                    "size_bytes": rec.stat().st_size,
                })

        # Add audio files to manifest if present
        if audio_files:
            for audio in audio_files:
                if audio.exists():
                    arc_path = f"audio/{audio.name}"
                    file_manifest.append({
                        "path": arc_path,
                        "sha256": calculate_sha256(audio),
                        "size_bytes": audio.stat().st_size,
                    })

        # Create manifest dict
        manifest_data = {
            "schema_version": cls.SCHEMA_VERSION,
            "session_id": session_id,
            "source_fingerprint": source_fingerprint,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "is_complete": is_complete,
            "transcript_status": transcript_status,
            "files": file_manifest,
        }

        manifest_bytes = json.dumps(manifest_data, indent=2).encode("utf-8")

        # Write atomic ZIP archive
        try:
            with zipfile.ZipFile(tmp_path, "w", zipfile.ZIP_DEFLATED) as zf:
                zf.writestr("manifest.json", manifest_bytes)
                zf.writestr("metadata.json", metadata_bytes)
                if telemetry_bytes is not None:
                    zf.writestr("telemetry.json", telemetry_bytes)
                if transcripts_bytes is not None:
                    zf.writestr("transcripts/transcripts.json", transcripts_bytes)

                for rec in rec_files:
                    if rec.exists():
                        zf.write(rec, arcname=f"replays/{rec.name}")

                if audio_files:
                    for audio in audio_files:
                        if audio.exists():
                            zf.write(audio, arcname=f"audio/{audio.name}")

            # Atomic rename
            if final_path.exists():
                final_path.unlink()
            tmp_path.rename(final_path)
            return final_path

        except Exception as e:
            if tmp_path.exists():
                try:
                    tmp_path.unlink()
                except Exception:
                    pass
            raise RuntimeError(f"Failed to create session package: {e}") from e

    @classmethod
    def verify_package(cls, archive_path: Path) -> Tuple[bool, str]:
        """
        Verifies an immutable .r6session archive for:
        1. Valid ZIP structure
        2. Safe archive paths (no path traversal, absolute paths, or '..')
        3. Presence and validity of manifest.json
        4. SHA-256 integrity for all payload files listed in manifest
        """
        if not archive_path.exists():
            return False, f"Archive file does not exist: {archive_path}"

        if not zipfile.is_zipfile(archive_path):
            return False, "File is not a valid ZIP archive"

        try:
            with zipfile.ZipFile(archive_path, "r") as zf:
                names = zf.namelist()

                # 1. Path traversal check
                for name in names:
                    p = Path(name)
                    if p.is_absolute() or ".." in p.parts or name.startswith("/") or name.startswith("\\"):
                        return False, f"Unsafe file path detected in archive: {name}"

                # 2. Manifest presence
                if "manifest.json" not in names:
                    return False, "Missing manifest.json in archive"

                manifest_data = json.loads(zf.read("manifest.json").decode("utf-8"))
                expected_files = manifest_data.get("files", [])

                # 3. Checksum verification for each manifest file
                for item in expected_files:
                    rel_path = item.get("path")
                    expected_sha = item.get("sha256")

                    if not rel_path or not expected_sha:
                        return False, f"Invalid manifest entry: {item}"

                    if rel_path not in names:
                        return False, f"Manifest file missing from zip archive: {rel_path}"

                    actual_data = zf.read(rel_path)
                    actual_sha = calculate_bytes_sha256(actual_data)

                    if actual_sha.lower() != expected_sha.lower():
                        return False, f"Checksum mismatch for {rel_path}: expected {expected_sha}, got {actual_sha}"

                return True, "Valid package"

        except Exception as e:
            return False, f"Verification failed with error: {e}"

    @classmethod
    def extract_package(cls, archive_path: Path, target_dir: Path) -> Path:
        """
        Safely extracts an immutable .r6session package into target_dir.
        Enforces path traversal safety before extraction.
        """
        valid, msg = cls.verify_package(archive_path)
        if not valid:
            raise ValueError(f"Cannot extract invalid package: {msg}")

        target_dir.mkdir(parents=True, exist_ok=True)
        resolved_target = target_dir.resolve()

        with zipfile.ZipFile(archive_path, "r") as zf:
            for member in zf.infolist():
                member_path = (resolved_target / member.filename).resolve()
                if not str(member_path).startswith(str(resolved_target)):
                    raise ValueError(f"Path traversal attempt blocked: {member.filename}")
            zf.extractall(target_dir)

        return target_dir
