from pathlib import Path

from PySide6.QtCore import Qt, QThread, QObject, Signal
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel,
    QPushButton, QTextEdit, QFileDialog, QMessageBox
)

from app.app_controller import AppController
from app.config import R6_DISSECT_PATH
from app.session_manager import SessionManager
from integration.rec_importer import RecImporter
from models.import_result import ImportResult, ImportStatus


# ── Background worker so the UI doesn't freeze during import ──

class _ImportWorker(QObject):
    finished = Signal(list)   # list[ImportResult]
    error    = Signal(str)

    def __init__(self, session_manager: SessionManager):
        super().__init__()
        self._session = session_manager

    def run(self) -> None:
        try:
            results = self._session.end_session()
            self.finished.emit(results)
        except Exception as e:
            self.error.emit(str(e))


class RecordingView(QWidget):
    # Emitted after a successful import — carries the new match_id
    navigate_to_analysis    = Signal(int)
    # Emitted on partial/critical failure — routes to manual entry
    navigate_to_match_input = Signal()

    def __init__(self, controller: AppController, parent: QWidget | None = None):
        super().__init__(parent)
        self.controller = controller
        self._session_active = False
        self._replay_folder: Path | None = None
        self._session_manager: SessionManager | None = None
        self._thread: QThread | None = None
        self._build_ui()

    # =====================================================
    # UI
    # =====================================================

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 24, 24, 24)
        layout.setSpacing(16)

        # Header
        header = QLabel("Recording Session")
        header.setStyleSheet("font-size: 22px; font-weight: bold;")
        header.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(header)

        # Replay folder selector
        folder_layout = QHBoxLayout()
        self._folder_label = QLabel("No replay folder selected.")
        self._folder_label.setStyleSheet("color: #aaa;")
        folder_btn = QPushButton("Select R6 Replay Folder")
        folder_btn.clicked.connect(self._select_folder)
        folder_layout.addWidget(self._folder_label, stretch=1)
        folder_layout.addWidget(folder_btn)
        layout.addLayout(folder_layout)

        # Status indicator
        self._status_label = QLabel("Status: Idle")
        self._status_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._status_label.setStyleSheet("font-size: 14px; color: #888;")
        layout.addWidget(self._status_label)

        # Start / Stop buttons
        btn_layout = QHBoxLayout()
        self._start_btn = QPushButton("▶  Start Session")
        self._start_btn.setMinimumHeight(44)
        self._start_btn.setEnabled(False)
        self._start_btn.clicked.connect(self._start_session)

        self._stop_btn = QPushButton("⏹  Stop & Import")
        self._stop_btn.setMinimumHeight(44)
        self._stop_btn.setEnabled(False)
        self._stop_btn.clicked.connect(self._stop_session)

        btn_layout.addWidget(self._start_btn)
        btn_layout.addWidget(self._stop_btn)
        layout.addLayout(btn_layout)

        # Log output
        self._log = QTextEdit()
        self._log.setReadOnly(True)
        self._log.setStyleSheet("background: #1a1a1a; color: #ccc; font-family: monospace;")
        self._log.setMinimumHeight(200)
        layout.addWidget(self._log)

        layout.addStretch()

    # =====================================================
    # FOLDER SELECTION
    # =====================================================

    def _select_folder(self) -> None:
        folder = QFileDialog.getExistingDirectory(
            self,
            "Select R6 Replay Folder",
            str(Path.home()),
        )
        if not folder:
            return

        self._replay_folder = Path(folder)
        self._folder_label.setText(str(self._replay_folder))
        self._folder_label.setStyleSheet("color: #fff;")
        self._start_btn.setEnabled(True)
        self._log_message(f"Replay folder set: {self._replay_folder}")

    # =====================================================
    # SESSION START
    # =====================================================

    def _start_session(self) -> None:
        if not self._replay_folder:
            return

        try:
            importer = RecImporter(dissect_path=R6_DISSECT_PATH)
        except FileNotFoundError as e:
            QMessageBox.critical(self, "Error", str(e))
            return

        self._session_manager = SessionManager(
            replay_folder=self._replay_folder,
            importer=importer,
        )
        self._session_manager.start_session()

        self._session_active = True
        self._start_btn.setEnabled(False)
        self._stop_btn.setEnabled(True)
        self._set_status("🔴 Recording in progress...", "#e05555")
        self._log_message("Session started. Snapshot taken.")

    # =====================================================
    # SESSION STOP + IMPORT
    # =====================================================

    def _stop_session(self) -> None:
        if not self._session_manager:
            return

        self._stop_btn.setEnabled(False)
        self._set_status("⏳ Importing replays...", "#e0a830")
        self._log_message("Session stopped. Detecting new replay folders...")

        # Run import in background thread
        self._thread = QThread()
        self._worker = _ImportWorker(self._session_manager)
        self._worker.moveToThread(self._thread)

        self._thread.started.connect(self._worker.run)
        self._worker.finished.connect(self._on_import_finished)
        self._worker.error.connect(self._on_import_error)
        self._worker.finished.connect(self._thread.quit)
        self._worker.error.connect(self._thread.quit)

        self._thread.start()

    # =====================================================
    # IMPORT RESULT HANDLING
    # =====================================================

    def _on_import_finished(self, results: list) -> None:
        self._session_active = False
        self._start_btn.setEnabled(True)
        self._set_status("✅ Import complete.", "#55e07a")

        if not results:
            self._log_message("No results returned.")
            self.navigate_to_match_input.emit()
            return

        for result in results:
            result: ImportResult
            self._log_message(f"Result: {result.status.value}")
            if result.error_message:
                self._log_message(f"  ↳ {result.error_message}")

        # Route based on worst result
        statuses = {r.status for r in results}

        if ImportStatus.CRITICAL_FAILURE in statuses:
            self._log_message("Critical failure detected — routing to Manual Entry.")
            QMessageBox.warning(
                self, "Import Failed",
                "One or more replays could not be imported.\nYou will be routed to Manual Entry."
            )
            self.navigate_to_match_input.emit()

        elif ImportStatus.PARTIAL_FAILURE in statuses:
            self._log_message("Partial failure — routing to Manual Entry for review.")
            QMessageBox.warning(
                self, "Partial Import",
                "Some data could not be parsed.\nPlease review in Manual Entry."
            )
            self.navigate_to_match_input.emit()

        else:
            # Full success — save all matches and go to analysis
            try:
                last_match_id = self._save_results(results)
                self._log_message(f"All matches saved. Routing to Analysis (match {last_match_id}).")
                self.navigate_to_analysis.emit(last_match_id)
            except Exception as e:
                self._log_message(f"Save error: {e}")
                QMessageBox.critical(self, "Save Error", str(e))
                self.navigate_to_match_input.emit()

    def _on_import_error(self, message: str) -> None:
        self._session_active = False
        self._start_btn.setEnabled(True)
        self._set_status("❌ Import error.", "#e05555")
        self._log_message(f"Error: {message}")
        QMessageBox.critical(self, "Import Error", message)
        self.navigate_to_match_input.emit()

    # =====================================================
    # SAVE RESULTS TO DB
    # =====================================================

    def _save_results(self, results: list) -> int:
        """
        Saves all successful ImportResults to the DB.
        Returns the match_id of the last saved match.
        """
        last_match_id = -1

        for result in results:
            result: ImportResult
            if not result.is_success:
                continue

            match_id = self.controller.save_imported_match(result)
            last_match_id = match_id

        if last_match_id == -1:
            raise RuntimeError("No matches were saved successfully.")

        return last_match_id

    # =====================================================
    # HELPERS
    # =====================================================

    def _set_status(self, text: str, color: str) -> None:
        self._status_label.setText(f"Status: {text}")
        self._status_label.setStyleSheet(f"font-size: 14px; color: {color};")

    def _log_message(self, message: str) -> None:
        self._log.append(message)