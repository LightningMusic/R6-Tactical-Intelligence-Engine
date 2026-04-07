from pathlib import Path
from typing import Optional
import time

from integration.rec_importer import RecImporter
from models.import_result import ImportResult, ImportStatus


class SessionManager:
    """
    Handles recording session lifecycle:
    - Snapshot the R6 replay folder (match subfolders, not raw .rec files)
    - Detect new match folders after session ends
    - Wait for file stability before importing
    - Pass new folders to RecImporter
    """

    def __init__(
        self,
        replay_folder: Path,
        importer: RecImporter,
        stability_wait: float = 2.0,
        stability_checks: int = 3,
    ):
        self.replay_folder = replay_folder
        self.importer = importer
        self.stability_wait = stability_wait
        self.stability_checks = stability_checks
        self._snapshot: set[Path] = set()

    # =====================================================
    # SESSION START
    # =====================================================

    def start_session(self) -> None:
        """
        Snapshot current match folders so we can diff later.
        """
        self._snapshot = self._scan_match_folders()

    # =====================================================
    # SESSION END
    # =====================================================

    def end_session(self) -> list[ImportResult]:
        """
        Detect new match folders since start_session(),
        wait for stability, and import them all.
        Returns one ImportResult per new match folder.
        """
        current_folders = self._scan_match_folders()
        new_folders = current_folders - self._snapshot

        if not new_folders:
            return [
                ImportResult(
                    status=ImportStatus.CRITICAL_FAILURE,
                    error_message="No new match folders detected since session start.",
                )
            ]

        stable_folders = self._filter_stable_folders(new_folders)

        if not stable_folders:
            return [
                ImportResult(
                    status=ImportStatus.CRITICAL_FAILURE,
                    error_message="New folders found but none were stable enough to read.",
                )
            ]

        return self.importer.import_multiple_folders(stable_folders)

    # =====================================================
    # INTERNAL: FOLDER SCAN
    # =====================================================

    def _scan_match_folders(self) -> set[Path]:
        """
        Returns all match subdirectories in the replay folder.
        R6 creates one folder per match (e.g. Match-2026-03-26_22-22-49-61612/).
        """
        if not self.replay_folder.exists():
            return set()

        return {
            p for p in self.replay_folder.iterdir()
            if p.is_dir() and p.name.startswith("Match-")
        }

    # =====================================================
    # INTERNAL: STABILITY CHECKS
    # =====================================================

    def _filter_stable_folders(self, folders: set[Path]) -> list[Path]:
        """
        Returns only folders whose contents have stopped changing.
        """
        return [f for f in folders if self._is_folder_stable(f)]

    def _is_folder_stable(self, folder: Path) -> bool:
        """
        Checks total size of all .rec files in a folder across
        several timed checks. Stable = size unchanged between checks.
        """
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
        """
        Returns total byte size of all .rec files in a folder.
        """
        return sum(
            f.stat().st_size
            for f in folder.glob("*.rec")
            if f.exists()
        )