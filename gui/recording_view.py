from pathlib import Path

from PySide6.QtCore import Qt, QThread, QObject, Signal
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel,
    QPushButton, QTextEdit, QFileDialog, QMessageBox
)
from torch import layout

from app.app_controller import AppController
from app.config import R6_DISSECT_PATH, get_replay_folder, settings
from app.session_manager import SessionManager
from integration.obs_controller import OBSController
from integration.rec_importer import RecImporter
from models.import_result import ImportResult, ImportStatus


class _ImportWorker(QObject):
    finished = Signal(list)
    error    = Signal(str)
    progress = Signal(str)

    def __init__(self, session_manager: SessionManager) -> None:
        super().__init__()
        self._session = session_manager

    def run(self) -> None:
        try:
            results = self._session.end_session(
                status_callback=lambda msg: self.progress.emit(msg)
            )
            self.finished.emit(results)
        except Exception as e:
            self.error.emit(str(e))


class RecordingView(QWidget):
    navigate_to_analysis         = Signal(int)
    navigate_to_match_input      = Signal()
    navigate_to_match_input_partial = Signal(object)

    def __init__(
        self,
        controller: AppController,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.controller      = controller
        self.obs             = OBSController()
        self._session_active = False
        self._replay_folder: Path | None     = get_replay_folder()
        self._session_manager: SessionManager | None = None
        self._thread: QThread | None         = None
        self._recording_path: str | None     = None
        self._build_ui()

    # =====================================================
    # UI
    # =====================================================

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 24, 24, 24)
        layout.setSpacing(14)

        header = QLabel("Recording Session")
        header.setStyleSheet("font-size: 22px; font-weight: bold;")
        header.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(header)

        # OBS row
        obs_layout = QHBoxLayout()
        self._obs_status_label = QLabel("OBS: Disconnected")
        self._obs_status_label.setStyleSheet("color: #e05555;")
        connect_btn = QPushButton("Connect to OBS")
        connect_btn.clicked.connect(self._connect_obs)
        obs_layout.addWidget(self._obs_status_label, stretch=1)
        obs_layout.addWidget(connect_btn)
        layout.addLayout(obs_layout)

        # Replay folder row
        folder_layout = QHBoxLayout()
        if self._replay_folder:
            self._folder_label = QLabel(str(self._replay_folder))
            self._folder_label.setStyleSheet("color: #55e07a;")
        else:
            self._folder_label = QLabel("No replay folder found — select manually.")
            self._folder_label.setStyleSheet("color: #e05555;")
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

        # Buttons
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

        # Progress label
        self._progress_label = QLabel("")
        self._progress_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._progress_label.setStyleSheet("font-size: 11px; color: #aaa;")
        layout.addWidget(self._progress_label)

        # Log
        self._log = QTextEdit()
        self._log.setReadOnly(True)
        self._log.setStyleSheet(
            "background: #1a1a1a; color: #ccc; font-family: monospace; font-size: 11px;"
        )

        # ── Hotkey: Ctrl+Shift+R toggles session ─────────────────
        from PySide6.QtGui import QKeySequence, QShortcut
        self._hotkey = QShortcut(QKeySequence("Ctrl+Shift+R"), self)
        self._hotkey.setContext(Qt.ShortcutContext.ApplicationShortcut)
        self._hotkey.activated.connect(self._hotkey_triggered)

        hotkey_label = QLabel("Hotkey: Ctrl+Shift+R — Start / Stop Session")
        hotkey_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        hotkey_label.setStyleSheet("font-size: 10px; color: #666;")
        layout.addWidget(hotkey_label)
        
        self._log.setMinimumHeight(220)
        layout.addWidget(self._log)
        layout.addStretch()


    # =====================================================
    # OBS
    # =====================================================

    def _connect_obs(self) -> None:
        self._log_message("Connecting to OBS...")
        self._obs_status_label.setText("OBS: Connecting...")
        self._obs_status_label.setStyleSheet("color: #e0a830;")

        from PySide6.QtWidgets import QApplication
        QApplication.processEvents()

        if self.obs.connect():
            self._obs_status_label.setText("OBS: Connected ✅")
            self._obs_status_label.setStyleSheet("color: #55e07a;")
            self._log_message("OBS connected.")
        else:
            self._obs_status_label.setText("OBS: Failed ❌")
            self._obs_status_label.setStyleSheet("color: #e05555;")
            self._log_message(
                "OBS connection failed. Check OBS is running and "
                "websocket is enabled with the correct password in Settings."
            )
        self._update_start_button()

    # =====================================================
    # FOLDER
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
        ready = self.obs.is_connected and self._replay_folder is not None
        self._start_btn.setEnabled(ready)

    # =====================================================
    # SESSION START
    # =====================================================

    def _start_session(self) -> None:
        if not self._replay_folder:
            return

        if not self.obs.start_recording():
            QMessageBox.critical(
                self, "OBS Error",
                "Failed to start recording.\n"
                "Check OBS is connected and the scene exists."
            )
            return

        try:
            importer = RecImporter(dissect_path=R6_DISSECT_PATH)
        except FileNotFoundError as e:
            QMessageBox.critical(self, "Error", str(e))
            self.obs.stop_recording()
            return

        self._session_manager = SessionManager(
            replay_folder=self._replay_folder,
            importer=importer,
            transcribe=settings.TRANSCRIBE_AUTO,
            stability_wait=settings.STABILITY_WAIT,
            stability_checks=settings.STABILITY_CHECKS,
        )
        self._session_manager.start_session()

        self._session_active = True
        self._start_btn.setEnabled(False)
        self._stop_btn.setEnabled(True)
        self._set_status("🔴 Recording...", "#e05555")
        self._log_message("Session started. OBS recording. Folder snapshot taken.")

    def _hotkey_triggered(self) -> None:
        if not self._session_active:
            if self._start_btn.isEnabled():
                self._log_message("🎮 Hotkey: Starting session (Ctrl+Shift+R)")
                self._start_session()
            else:
                self._log_message(
                    "⚠ Hotkey: not ready — connect OBS and set replay folder first."
                )
        else:
            self._log_message("🎮 Hotkey: Stopping session (Ctrl+Shift+R)")
            self._stop_session()
            
    # =====================================================
    # SESSION STOP
    # =====================================================

    def _stop_session(self) -> None:
        if not self._session_manager:
            return

        self._stop_btn.setEnabled(False)
        self._set_status("⏳ Stopping OBS...", "#e0a830")

        recording_path = self.obs.stop_recording()
        if recording_path:
            self._recording_path = recording_path
            self._session_manager.recording_path = Path(recording_path)
            self._log_message(f"Recording saved: {recording_path}")
        else:
            self._log_message("Warning: OBS did not return a recording path.")

        self._set_status("⏳ Importing replays...", "#e0a830")
        self._log_message("Starting import...")

        self._thread = QThread()
        self._worker = _ImportWorker(self._session_manager)
        self._worker.moveToThread(self._thread)

        self._thread.started.connect(self._worker.run)
        self._worker.progress.connect(self._on_progress)
        self._worker.finished.connect(self._on_import_finished)
        self._worker.error.connect(self._on_import_error)
        self._worker.finished.connect(self._thread.quit)
        self._worker.error.connect(self._thread.quit)

        self._thread.start()

    def _on_progress(self, msg: str) -> None:
        self._progress_label.setText(msg)
        self._log_message(msg)

    # =====================================================
    # IMPORT RESULT HANDLING
    # =====================================================

    def _on_import_finished(self, results: list) -> None:
        self._session_active = False
        self._start_btn.setEnabled(True)
        self._progress_label.setText("")

        if not results:
            self._log_message("No results returned.")
            self._set_status("❌ No results.", "#e05555")
            self.navigate_to_match_input.emit()
            return

        self._set_status("✅ Import complete.", "#55e07a")

        statuses = {r.status for r in results}

        # Log summary
        for r in results:
            self._log_message(
                f"  {r.status.value}: {len(r.rounds)} rounds"
                + (f" | match_id={r.match_id}" if r.match_id else "")
                + (f" | {r.error_message}" if r.error_message else "")
            )

        if ImportStatus.CRITICAL_FAILURE in statuses and all(
            r.status == ImportStatus.CRITICAL_FAILURE for r in results
        ):
            # Everything failed — no match created
            self._log_message("All imports critically failed — going to Manual Entry.")
            QMessageBox.warning(
                self, "Import Failed",
                "Could not parse any replays.\n"
                "Going to Manual Entry — create a match manually."
            )
            self.navigate_to_match_input.emit()
            return

        # At least some rounds parsed — match records were auto-created
        # Find the best result to route with
        success_results = [r for r in results if r.status == ImportStatus.SUCCESS]
        partial_results = [r for r in results if r.status == ImportStatus.PARTIAL_FAILURE]

        if success_results:
            last_match_id = success_results[-1].match_id
            if last_match_id is not None:
                self._log_message(
                    f"Routing to Analysis (match {last_match_id})."
                )
                self.navigate_to_analysis.emit(last_match_id)
            else:
                self.navigate_to_match_input.emit()
        elif partial_results:
            partial = partial_results[0]
            self._log_message(
                f"Partial import — routing to Manual Entry "
                f"(match {partial.match_id} pre-created)."
            )
            QMessageBox.information(
                self, "Partial Import",
                f"Parsed {len(partial.rounds)} rounds.\n"
                f"Match record created — you can save rounds directly.\n"
                f"Missing data shown in Manual Entry."
            )
            self.navigate_to_match_input_partial.emit(partial)

    def _on_import_error(self, message: str) -> None:
        self._session_active = False
        self._start_btn.setEnabled(True)
        self._set_status("❌ Error.", "#e05555")
        self._log_message(f"Error: {message}")
        QMessageBox.critical(self, "Import Error", message)
        self.navigate_to_match_input.emit()

    # =====================================================
    # HELPERS
    # =====================================================

    def _set_status(self, text: str, color: str) -> None:
        self._status_label.setText(f"Status: {text}")
        self._status_label.setStyleSheet(f"font-size: 14px; color: {color};")

    def _log_message(self, message: str) -> None:
        self._log.append(message)
        # Auto-scroll to bottom
        sb = self._log.verticalScrollBar()
        sb.setValue(sb.maximum())