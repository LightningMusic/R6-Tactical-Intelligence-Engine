import json
import zipfile
from pathlib import Path
from datetime import datetime, timezone
from typing import Dict, Any

from server.database import server_db
from server.storage import storage_manager
from server.services.package_validation import ServerPackageValidator


class SessionProcessingService:
    """
    Processes an uploaded .r6session archive:
    1. Re-validates archive integrity.
    2. Extracts to isolated work directory server_data/work/<job_id>/.
    3. Reads metadata.json and telemetry.json.
    4. Saves summary results into server database.
    5. Cleans up isolated work directory upon completion.
    """

    @classmethod
    def process_session_job(cls, job_id: str, archive_path: Path, session_id: str) -> Dict[str, Any]:
        valid, msg, manifest = ServerPackageValidator.validate_package(archive_path)
        if not valid:
            raise ValueError(f"Package validation failed: {msg}")

        work_dir = storage_manager.create_work_directory(job_id)

        try:
            with zipfile.ZipFile(archive_path, "r") as zf:
                zf.extractall(work_dir)

            metadata_file = work_dir / "metadata.json"
            meta_data = {}
            if metadata_file.exists():
                meta_data = json.loads(metadata_file.read_text(encoding="utf-8"))

            telemetry_file = work_dir / "telemetry.json"
            telem_data = {}
            if telemetry_file.exists():
                telem_data = json.loads(telemetry_file.read_text(encoding="utf-8"))

            map_name = meta_data.get("map_name", "Unknown")
            rounds_count = int(telem_data.get("rounds_parsed", 0))

            summary = {
                "session_id": session_id,
                "map_name": map_name,
                "score_us": meta_data.get("score_us"),
                "score_them": meta_data.get("score_them"),
                "rounds_parsed": rounds_count,
                "client_name": meta_data.get("client_name", "Unknown"),
                "processed_at": datetime.now(timezone.utc).isoformat(),
            }

            now = datetime.now(timezone.utc).isoformat()
            with server_db.get_connection() as conn:
                conn.execute(
                    """
                    INSERT INTO server_parsed_matches (session_id, map_name, rounds_count, summary_json, created_at)
                    VALUES (?, ?, ?, ?, ?)
                    ON CONFLICT(session_id) DO UPDATE SET
                        map_name = excluded.map_name,
                        rounds_count = excluded.rounds_count,
                        summary_json = excluded.summary_json,
                        created_at = excluded.created_at
                    """,
                    (session_id, map_name, rounds_count, json.dumps(summary), now),
                )
                conn.commit()

            return summary

        finally:
            storage_manager.cleanup_work_directory(job_id)
