from pathlib import Path
from typing import Callable, Optional
import time

from integration.rec_importer import RecImporter
from integration.whisper_transcriber import WhisperTranscriber
from models.import_result import ImportResult, ImportStatus
from models.round_resources import RoundResources


class SessionManager:
    """
    Handles recording session lifecycle.
    Auto-creates a DB match record for every ImportResult that has rounds,
    whether SUCCESS or PARTIAL_FAILURE — so manual entry can always save.
    """

    def __init__(
        self,
        replay_folder: Path,
        importer: RecImporter,
        recording_path: Optional[Path] = None,
        transcribe: bool = True,
        stability_wait: float = 5.0,
        stability_checks: int = 4,
    ) -> None:
        self.replay_folder    = replay_folder
        self.importer         = importer
        self.recording_path   = recording_path
        self.transcribe       = transcribe
        self.stability_wait   = stability_wait
        self.stability_checks = stability_checks
        self._snapshot: set[Path] = set()
        self._transcriber: Optional[WhisperTranscriber] = None

    # =====================================================
    # SESSION START
    # =====================================================

    def start_session(self) -> None:
        self._snapshot = self._scan_match_folders()

    # =====================================================
    # SESSION END
    # =====================================================

    def end_session(
        self,
        status_callback: Optional[Callable[[str], None]] = None,
    ) -> list[ImportResult]:

        def log(msg: str) -> None:
            print(f"[SessionManager] {msg}")
            if status_callback:
                status_callback(msg)

        log("Scanning for new match folders...")
        current_folders = self._scan_match_folders()
        new_folders     = current_folders - self._snapshot

        if not new_folders:
            log("No new match folders found.")
            return [ImportResult(
                status=ImportStatus.CRITICAL_FAILURE,
                error_message="No new match folders detected since session start.",
            )]

        log(f"Found {len(new_folders)} new folder(s). Checking stability...")
        stable_folders = self._filter_stable_folders(new_folders)

        if not stable_folders:
            log("Folders found but not yet stable — try stopping again in a moment.")
            return [ImportResult(
                status=ImportStatus.CRITICAL_FAILURE,
                error_message="New folders found but none were stable enough to read.",
            )]

        log(f"Importing {len(stable_folders)} stable folder(s)...")

        # Pass log callback into importer so UI log updates during parsing
        self.importer._log = log
        results = self.importer.import_multiple_folders(
            stable_folders, log_callback=log
        )

        # ── Auto-create match records for anything with rounds ──
        log("Creating match records...")
        self._auto_create_matches(results, log)

        # ── Transcription ─────────────────────────────────────
        if self.transcribe and self.recording_path:
            log("Starting transcription (this may take a few minutes)...")
            try:
                self._run_transcription(results, stable_folders, log_callback=log)
            except Exception as e:
                log(f"Transcription failed (non-fatal): {e}")

        return results

    # =====================================================
    # AUTO-CREATE MATCH RECORDS
    # =====================================================

    def _auto_create_matches(
        self,
        results: list[ImportResult],
        log: Callable[[str], None],
    ) -> None:
        """
        For every result that has at least one parsed round,
        create a match record in the DB and attach the match_id
        to the result. Works for both SUCCESS and PARTIAL_FAILURE.
        """
        from database.repositories import Repository
        from models.match import Match
        from datetime import datetime

        repo = Repository()

        for result in results:
            if not result.rounds:
                log(f"  Skipping match creation — no rounds parsed.")
                continue
            if result.match_id is not None:
                log(f"  Match {result.match_id} already exists.")
                continue

            # Resolve map name
            map_name = result.map_name or "Unknown"
            if map_name and map_name.startswith("Map("):
                map_name = "Unknown"   # unresolved numeric ID

            try:
                # Resolve map_id if possible
                map_id = repo.get_map_id_by_name(map_name)

                match = Match(
                    match_id=None,
                    datetime_played=datetime.now(),
                    opponent_name="Imported",
                    map=map_name,
                    result=None,
                    recording_path=str(self.recording_path)
                        if self.recording_path else None,
                    rounds=[],
                )
                match_id = repo.insert_match(match)
                result.match_id = match_id
                result.map_id   = map_id

                # Insert rounds
                for round_obj in result.rounds:
                    round_obj.match_id = match_id

                    if round_obj.side == "attack":
                        resources = RoundResources(
                            resource_id=None, round_id=0, side="attack",
                            team_drones_start=10,        # ← must be 10, schema CHECK constraint
                            team_drones_lost=0,
                            team_reinforcements_start=10, # ← must be 10 too
                            team_reinforcements_used=0,
                        )
                    else:
                        resources = RoundResources(
                            resource_id=None, round_id=0, side="defense",
                            team_drones_start=10,        # ← must be 10
                            team_drones_lost=0,
                            team_reinforcements_start=10, # ← must be 10
                            team_reinforcements_used=0,
                        )
                    round_id = repo.insert_round(round_obj, match_id)
                    repo.insert_round_resources(resources, round_id)

                log(
                    f"  ✓ Created match {match_id}: {map_name} "
                    f"({len(result.rounds)} rounds)"
                )

            except Exception as e:
                log(f"  ✗ Failed to create match record: {e}")

    # =====================================================
    # TRANSCRIPTION
    # =====================================================

    def _run_transcription(
        self,
        results: list[ImportResult],
        folders: list[Path],
        log_callback: Optional[Callable[[str], None]] = None,
    ) -> None:
        from analysis.timeline_aligner import TimelineAligner
        from analysis.transcript_parser import TranscriptParser
        from database.repositories import Repository
        import json

        if self._transcriber is None:
            if log_callback:
                log_callback("Loading Whisper model...")
            self._transcriber = WhisperTranscriber()

        if self.recording_path is None:
            return

        full_result = self._transcriber.transcribe(self.recording_path)
        aligner     = TimelineAligner()
        parser      = TranscriptParser()
        repo        = Repository()

        for i, (result, folder) in enumerate(zip(results, folders)):
            clipped: dict = {"text": "", "segments": []}

            try:
                start_sec, end_sec = aligner.get_match_window(folder)
                clipped = self._transcriber.clip_to_match(
                    full_result, start_sec, end_sec
                )
                if log_callback:
                    log_callback(
                        f"Match {i+1} transcript: "
                        f"{start_sec:.0f}s – {end_sec:.0f}s"
                    )
            except Exception as e:
                if log_callback:
                    log_callback(f"Clip failed for {folder.name}: {e}")

            text     = clipped.get("text", "")
            segments = clipped.get("segments", [])
            parsed   = parser.parse_segments_list(segments, match_id=result.match_id)
            storage  = parser.to_storage_dict(parsed)

            if result.match_id is not None:
                try:
                    with repo.db.get_connection() as conn:
                        conn.execute(
                            """
                            INSERT INTO transcripts
                                (match_id, raw_text, processed_segments_json)
                            VALUES (?, ?, ?)
                            ON CONFLICT DO NOTHING
                            """,
                            (result.match_id, text, json.dumps(storage))
                        )
                        conn.commit()
                except Exception as db_err:
                    print(f"[SessionManager] DB transcript store failed: {db_err}")

            result.transcript_text     = text
            result.transcript_segments = segments

    # =====================================================
    # FOLDER SCAN
    # =====================================================

    def _scan_match_folders(self) -> set[Path]:
        if not self.replay_folder.exists():
            return set()
        return {
            p for p in self.replay_folder.iterdir()
            if p.is_dir() and p.name.startswith("Match-")
        }

    # =====================================================
    # STABILITY CHECKS
    # =====================================================

    def _filter_stable_folders(self, folders: set[Path]) -> list[Path]:
        return [f for f in folders if self._is_folder_stable(f)]

    def _is_folder_stable(self, folder: Path) -> bool:
        previous_size = -1
        for _ in range(self.stability_checks):
            if not folder.exists():
                return False
            current_size = self._get_folder_rec_size(folder)
            if current_size == previous_size and current_size > 0:
                return True
            previous_size = current_size
            time.sleep(self.stability_wait)
        return False

    def _get_folder_rec_size(self, folder: Path) -> int:
        return sum(
            f.stat().st_size
            for f in folder.glob("*.rec")
            if f.exists()
        )