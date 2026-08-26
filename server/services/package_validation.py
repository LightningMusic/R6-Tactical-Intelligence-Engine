import json
import zipfile
from pathlib import Path
from typing import Tuple, Dict, Any

from app.packaging import SessionPackage, calculate_bytes_sha256
from server.config import server_settings


class ServerPackageValidator:
    """
    Headless package validation service for the server.
    Enforces strict rules:
    - Valid ZIP archive
    - Path traversal prevention
    - Supported schema version
    - SHA-256 checksum match for all manifest files
    """

    @classmethod
    def validate_package(cls, archive_path: Path) -> Tuple[bool, str, Dict[str, Any]]:
        if not archive_path.exists():
            return False, "Archive file does not exist", {}

        if not zipfile.is_zipfile(archive_path):
            return False, "File is not a valid ZIP archive", {}

        try:
            with zipfile.ZipFile(archive_path, "r") as zf:
                names = zf.namelist()

                # Path traversal security check
                for name in names:
                    p = Path(name)
                    if p.is_absolute() or ".." in p.parts or name.startswith("/") or name.startswith("\\"):
                        return False, f"Path traversal attempt blocked: {name}", {}

                # Check manifest.json
                if "manifest.json" not in names:
                    return False, "Missing manifest.json in archive", {}

                manifest_data = json.loads(zf.read("manifest.json").decode("utf-8"))
                version = str(manifest_data.get("schema_version", "")).strip()

                if version != server_settings.ALLOWED_PACKAGE_VERSION:
                    return (
                        False,
                        f"Unsupported package version: {version} (allowed: {server_settings.ALLOWED_PACKAGE_VERSION})",
                        {},
                    )

                # Check checksums for all manifest files
                expected_files = manifest_data.get("files", [])
                for item in expected_files:
                    rel_path = item.get("path")
                    expected_sha = item.get("sha256")

                    if not rel_path or not expected_sha:
                        return False, f"Invalid manifest entry: {item}", {}

                    if rel_path not in names:
                        return False, f"Manifest file missing from archive: {rel_path}", {}

                    actual_data = zf.read(rel_path)
                    actual_sha = calculate_bytes_sha256(actual_data)

                    if actual_sha.lower() != expected_sha.lower():
                        return False, f"Checksum mismatch for {rel_path}", {}

                return True, "Valid package", manifest_data

        except Exception as e:
            return False, f"Package validation error: {e}", {}
