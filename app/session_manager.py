from pathlib import Path
from typing import Optional
import time

from integration.rec_importer import RecImporter
from integration.whisper_transcriber import WhisperTranscriber
from models.import_result import ImportResult, ImportStatus


class SessionManager:
    """
    Handles recording session lifecycle:
    - Snapshot the R6 replay folder
    - Detect new match folders after session ends
    - Wait for file stability before importing
    - Transcribe the session audio and clip to each match window
    - Pass results to RecImporter
    """

    def __init__(
        self,
        replay_folder: Path,
        importer: RecImporter,
        recording_path: Optional[Path] = None,   # OBS output file
        transcribe: bool = True,
        stability_wait: float = 5.0,
        stability_checks: int = 4,
    ):
        self.replay_folder   = replay_folder
        self.importer        = importer
        self.recording_path  = recording_path
        self.transcribe      = transcribe
        self.stability_wait  = stability_wait
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

    def end_session(self) -> list[ImportResult]:
        current_folders = self._scan_match_folders()
        new_folders = current_folders - self._snapshot

        if not new_folders:
            return [ImportResult(
                status=ImportStatus.CRITICAL_FAILURE,
                error_message="No new match folders detected since session start.",
            )]

        stable_folders = self._filter_stable_folders(new_folders)

        if not stable_folders:
            return [ImportResult(
                status=ImportStatus.CRITICAL_FAILURE,
                error_message="New folders found but none were stable enough to read.",
            )]

        results = self.importer.import_multiple_folders(stable_folders)

        # ── Transcription (optional, non-blocking on failure) ───
        if self.transcribe and self.recording_path:
            try:
                self._run_transcription(results, stable_folders)
            except Exception as e:
                print(f"[SessionManager] Transcription failed (non-fatal): {e}")

        return results

    # =====================================================
    # TRANSCRIPTION
    # =====================================================

    def _run_transcription(
        self,
        results: list[ImportResult],
        folders: list[Path],
    ) -> None:
        """
        Transcribes the full session recording, then clips
        each match's segment using timestamps from the .rec files.
        Attaches the transcript text to each ImportResult.
        """
        from analysis.timeline_aligner import TimelineAligner

        if self._transcriber is None:
            self._transcriber = WhisperTranscriber()

        print("[SessionManager] Starting transcription of session audio...")
        full_result = self._transcriber.transcribe(self.recording_path)  # type: ignore[arg-type]

        aligner = TimelineAligner()

        for i, (result, folder) in enumerate(zip(results, folders)):
            try:
                start_sec, end_sec = aligner.get_match_window(folder)
                clipped = self._transcriber.clip_to_match(
                    full_result, start_sec, end_sec
                )
                result.transcript_text = clipped.get("text", "")
                result.transcript_segments = clipped.get("segments", [])
                print(
                    f"[SessionManager] Match {i+1} transcript clipped "
                    f"({start_sec:.0f}s – {end_sec:.0f}s)"
                )
            except Exception as e:
                print(f"[SessionManager] Clip failed for folder {folder.name}: {e}")

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