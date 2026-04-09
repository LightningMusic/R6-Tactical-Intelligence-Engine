from pathlib import Path

from PySide6.QtCore import Qt, QThread, QObject, Signal
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel,
    QPushButton, QTextEdit, QFileDialog, QMessageBox
)

from app.app_controller import AppController
from app.config import R6_DISSECT_PATH
from app.session_manager import SessionManager
from integration.obs_controller import OBSController
from integration.rec_importer import RecImporter
from models.import_result import ImportResult, ImportStatus
from app.config import R6_DISSECT_PATH, get_replay_folder


class _ImportWorker(QObject):
    finished = Signal(list)
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
    navigate_to_analysis    = Signal(int)
    navigate_to_match_input = Signal()
    navigate_to_match_input_partial = Signal(object)  # carries ImportResult

    def __init__(self, controller: AppController, parent: QWidget | None = None):
        super().__init__(parent)
        self.controller  = controller
        self.obs         = OBSController()
        self._session_active    = False
        self._replay_folder: Path | None = get_replay_folder()
        self._session_manager: SessionManager | None = None
        self._thread: QThread | None         = None
        self._build_ui()

    # =====================================================
    # UI
    # =====================================================

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 24, 24, 24)
        layout.setSpacing(16)

        header = QLabel("Recording Session")
        header.setStyleSheet("font-size: 22px; font-weight: bold;")
        header.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(header)

        # OBS connection row
        obs_layout = QHBoxLayout()
        self._obs_status_label = QLabel("OBS: Disconnected")
        self._obs_status_label.setStyleSheet("color: #e05555;")
        connect_btn = QPushButton("Connect to OBS")
        connect_btn.clicked.connect(self._connect_obs)
        obs_layout.addWidget(self._obs_status_label, stretch=1)
        obs_layout.addWidget(connect_btn)
        layout.addLayout(obs_layout)

        # Replay folder selector row
        folder_layout = QHBoxLayout()
        
        # Update label based on whether R6_REPLAY_FOLDER was found
        if self._replay_folder:
            self._folder_label = QLabel(str(self._replay_folder))
            self._folder_label.setStyleSheet("color: #55e07a;") # Green if found
        else:
            self._folder_label = QLabel("No replay folder found. Please select manually.")
            self._folder_label.setStyleSheet("color: #e05555;") # Red if missing
            self._folder_label.setToolTip("Please select the R6 replay folder manually.")
        folder_btn = QPushButton("Change Folder")
        folder_btn.clicked.connect(self._select_folder)
        folder_layout.addWidget(self._folder_label, stretch=1)
        folder_layout.addWidget(folder_btn)
        layout.addLayout(folder_layout)

        # Status
        self._status_label = QLabel("Status: Idle")
        self._status_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._status_label.setStyleSheet("font-size: 14px; color: #888;")
        layout.addWidget(self._status_label)

        # Start / Stop
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

        # Log
        self._log = QTextEdit()
        self._log.setReadOnly(True)
        self._log.setStyleSheet(
            "background: #1a1a1a; color: #ccc; font-family: monospace;"
        )
        self._log.setMinimumHeight(200)
        layout.addWidget(self._log)
        layout.addStretch()

        self._update_start_button
    # =====================================================
    # OBS CONNECTION
    # =====================================================

    def _connect_obs(self) -> None:
        self._log_message("Connecting to OBS...")
        if self.obs.connect():
            self._obs_status_label.setText("OBS: Connected ✅")
            self._obs_status_label.setStyleSheet("color: #55e07a;")
            self._log_message("OBS connected successfully.")
            self._update_start_button()
        else:
            self._obs_status_label.setText("OBS: Connection Failed ❌")
            self._obs_status_label.setStyleSheet("color: #e05555;")
            self._log_message(
                "Could not connect to OBS. Check that OBS is open "
                "and obs-websocket is enabled."
            )

    # =====================================================
    # FOLDER SELECTION
    # =====================================================

    def _select_folder(self) -> None:
        folder = QFileDialog.getExistingDirectory(
            self, "Select R6 Replay Folder", str(Path.home())
        )
        if not folder:
            return
        self._replay_folder = Path(folder)
        self._folder_label.setText(str(self._replay_folder))
        self._folder_label.setStyleSheet("color: #55e07a;")
        self._log_message(f"Replay folder: {self._replay_folder}")
        self._update_start_button()

    def _update_start_button(self) -> None:
        """Enable Start only when both OBS is connected and folder is set."""
        ready = self.obs.is_connected and self._replay_folder is not None
        self._start_btn.setEnabled(ready)

    # =====================================================
    # SESSION START
    # =====================================================

    def _start_session(self) -> None:
        if not self._replay_folder:
            return

        # Start OBS recording
        if not self.obs.start_recording():
            QMessageBox.critical(
                self, "OBS Error",
                "Failed to start OBS recording. Check OBS is open and the scene exists."
            )
            return

        # Snapshot replay folder
        try:
            importer = RecImporter(dissect_path=R6_DISSECT_PATH)
        except FileNotFoundError as e:
            QMessageBox.critical(self, "Error", str(e))
            self.obs.stop_recording()
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
        self._log_message("Session started. OBS recording. Snapshot taken.")

    # =====================================================
    # SESSION STOP + IMPORT
    # =====================================================

    def _stop_session(self) -> None:
        if not self._session_manager:
            return

        self._stop_btn.setEnabled(False)
        self._set_status("⏳ Stopping OBS and importing...", "#e0a830")

        # Stop OBS — save the recording path for later use
        recording_path = self.obs.stop_recording()
        self._session_manager.recording_path = (
            Path(recording_path) if recording_path else None
        )
        if recording_path:
            self._log_message(f"OBS recording saved: {recording_path}")
        else:
            self._log_message("Warning: OBS did not return a recording path.")

        self._recording_path = recording_path
        self._log_message("Detecting new replay folders...")

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

        statuses = {r.status for r in results}

        if ImportStatus.CRITICAL_FAILURE in statuses:
            self._log_message("Critical failure — routing to Manual Entry.")
            QMessageBox.warning(
                self, "Import Failed",
                "One or more replays could not be imported.\n"
                "You will be routed to Manual Entry."
            )
            self.navigate_to_match_input.emit()

        elif ImportStatus.PARTIAL_FAILURE in statuses:
            partial_result = next(
                r for r in results if r.status == ImportStatus.PARTIAL_FAILURE
            )
            self._log_message("Partial failure — routing to Manual Entry with pre-fill.")
            QMessageBox.warning(
                self, "Partial Import",
                "Some data could not be parsed.\nPre-filling what was recovered."
            )
            self.navigate_to_match_input_partial.emit(partial_result)

        else:
            try:
                last_match_id = self._save_results(results)
                self._log_message(
                    f"All matches saved. Routing to Analysis (match {last_match_id})."
                )
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
        last_match_id = -1
        for result in results:
            result: ImportResult
            if not result.is_success:
                continue
            # Attach the OBS recording path to the first match saved
            if hasattr(self, "_recording_path") and self._recording_path:
                result.recording_path = self._recording_path
                self._recording_path = None  # only attach to first match
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