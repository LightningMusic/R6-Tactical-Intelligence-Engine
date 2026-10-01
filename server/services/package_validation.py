import json
import zipfile
from pathlib import Path
from typing import Tuple, Dict, Any

from app.packaging import SessionPackage
from server.config import server_settings


class ServerPackageValidator:
    """
    Headless package validation service for the server.

    Delegates the core, client-identical checks — ZIP validity, path
    traversal / unsafe-path prevention, manifest.json presence, and
    per-file SHA-256 checksum verification — to the shared
    app.packaging.SessionPackage.verify_package() validator, so the client
    and server can never silently drift apart on what makes a package
    valid. This class layers on only the one check that is genuinely
    server-specific policy: rejecting package schema versions the server
    does not support.
    """

    @classmethod
    def validate_package(cls, archive_path: Path) -> Tuple[bool, str, Dict[str, Any]]:
        # Shared/headless validation (identical rules the client itself
        # verifies against when queuing a package).
        valid, msg = SessionPackage.verify_package(archive_path)
        if not valid:
            return False, msg, {}

        # verify_package() already proved this is a well-formed zip with a
        # readable manifest.json; re-read it here only to apply the
        # server-specific version-allowlist policy and to hand the caller
        # the parsed manifest.
        try:
            with zipfile.ZipFile(archive_path, "r") as zf:
                manifest_data = json.loads(zf.read("manifest.json").decode("utf-8"))
        except Exception as e:
            return False, f"Package validation error: {e}", {}

        version = str(manifest_data.get("schema_version", "")).strip()
        if version != server_settings.ALLOWED_PACKAGE_VERSION:
            return (
                False,
                f"Unsupported package version: {version} (allowed: {server_settings.ALLOWED_PACKAGE_VERSION})",
                {},
            )

        return True, "Valid package", manifest_data
