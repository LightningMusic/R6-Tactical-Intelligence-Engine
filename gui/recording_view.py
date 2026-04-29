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
        self._game_recording_active = False
        self._streaming_active      = False
        self._build_ui()

    def _shutdown_and_eject(self) -> None:
            from PySide6.QtWidgets import QMessageBox
            confirm = QMessageBox.question(
                self, "Shut Down & Eject",
                "This will:\n"
                "  1. Stop OBS recording (if active)\n"
                "  2. Terminate OBS process\n"
                "  3. Shut down the Ollama AI server\n"
                "  4. Close R6 Analyzer\n"
                "  5. Eject the USB drive\n\n"
                "Proceed?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            )
            if confirm != QMessageBox.StandardButton.Yes:
                return

            self._log_message("Shutting down...")

            # ── Step 1: Stop OBS watchdog ─────────────────────────────
            if hasattr(self, "_obs_watchdog"):
                self._obs_watchdog.stop()

            # ── Step 2: Stop OBS recording via websocket ──────────────
            if self._session_active:
                try:
                    self.obs.stop_recording()
                    self._log_message("OBS recording stopped.")
                except Exception as e:
                    self._log_message(f"OBS stop error: {e}")

            # ── Step 3: Disconnect OBS websocket ──────────────────────
            try:
                self.obs.disconnect()
                self._log_message("OBS disconnected.")
            except Exception:
                pass

            # ── Step 4: Kill OBS process entirely ─────────────────────
            try:
                import psutil
                killed = 0
                for proc in psutil.process_iter(["name", "pid"]):
                    try:
                        if proc.info["name"] and "obs64" in proc.info["name"].lower():
                            proc.terminate()
                            killed += 1
                    except (psutil.NoSuchProcess, psutil.AccessDenied):
                        pass
                if killed:
                    self._log_message(f"OBS process terminated ({killed} instance(s)).")
                else:
                    self._log_message("OBS process not found (already closed).")
            except Exception as e:
                self._log_message(f"OBS process kill error: {e}")

            # ── Step 5: Stop Ollama via IntelEngine + kill process ─────
            try:
                from analysis.intel_engine import IntelEngine
                _e = IntelEngine()
                _e.shutdown()
                self._log_message("Ollama server stopped via API.")
            except Exception as e:
                self._log_message(f"Ollama shutdown error: {e}")

            # Kill ollama.exe process directly as backup
            try:
                import psutil
                killed = 0
                for proc in psutil.process_iter(["name", "pid"]):
                    try:
                        name = proc.info["name"] or ""
                        if "ollama" in name.lower():
                            proc.terminate()
                            killed += 1
                    except (psutil.NoSuchProcess, psutil.AccessDenied):
                        pass
                if killed:
                    self._log_message(f"Ollama process terminated ({killed} instance(s)).")
            except Exception as e:
                self._log_message(f"Ollama process kill error: {e}")

            # ── Step 6: Eject USB ─────────────────────────────────────
            try:
                from app.config import BASE_DIR
                import subprocess
                drive = BASE_DIR.drive   # e.g. "E:"
                if drive and drive.upper() != "C:":
                    ps_cmd = (
                        f"(New-Object -comObject Shell.Application)"
                        f".Namespace(17).ParseName('{drive}\\').InvokeVerb('Eject')"
                    )
                    subprocess.Popen(
                        ["powershell", "-NoProfile", "-WindowStyle", "Hidden",
                        "-Command", ps_cmd],
                        creationflags=0x08000000,
                    )
                    self._log_message(f"Ejecting {drive}...")
                else:
                    self._log_message("Skipping eject (running from C: drive).")
            except Exception as e:
                self._log_message(f"Eject error: {e}")

            # ── Step 7: Exit after brief delay so log updates ─────────
            from PySide6.QtCore import QTimer
            QTimer.singleShot(2000, lambda: __import__("sys").exit(0))
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

        # ── Storage indicator ─────────────────────────────────────
        self._storage_label = QLabel("💾 Checking storage...")
        self._storage_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._storage_label.setStyleSheet("font-size: 11px; color: #888;")
        layout.addWidget(self._storage_label)
        self._refresh_storage_display()

        # ── Storage management ────────────────────────────────────
        storage_layout = QHBoxLayout()
        cleanup_btn = QPushButton("🗑  Clean Old Recordings")
        cleanup_btn.setMinimumHeight(32)
        cleanup_btn.clicked.connect(self._cleanup_recordings)
        storage_layout.addWidget(cleanup_btn)
        layout.addLayout(storage_layout)

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

        # ── Game recording / streaming row ────────────────────────
        game_layout = QHBoxLayout()

        self._game_rec_btn = QPushButton("🎬  Start Game Recording")
        self._game_rec_btn.setMinimumHeight(38)
        self._game_rec_btn.setEnabled(False)
        self._game_rec_btn.clicked.connect(self._toggle_game_recording)
        game_layout.addWidget(self._game_rec_btn)

        self._stream_btn = QPushButton("📡  Start Stream")
        self._stream_btn.setMinimumHeight(38)
        self._stream_btn.setEnabled(False)
        self._stream_btn.clicked.connect(self._toggle_stream)
        game_layout.addWidget(self._stream_btn)

        layout.addLayout(game_layout)

        # Scene setup button
        setup_btn = QPushButton("⚙  Set Up OBS Scenes (run once)")
        setup_btn.setMinimumHeight(32)
        setup_btn.setStyleSheet("font-size: 10px; color: #888;")
        setup_btn.clicked.connect(self._setup_obs_scenes)
        layout.addWidget(setup_btn)

        # At the bottom of the button layout
        shutdown_btn = QPushButton("⏏  Shut Down & Eject USB")
        shutdown_btn.setMinimumHeight(38)
        shutdown_btn.setStyleSheet(
            "QPushButton { color: #e05555; border: 1px solid #e05555; }"
            "QPushButton:hover { background: #3a1a1a; }"
        )
        shutdown_btn.clicked.connect(self._shutdown_and_eject)
        layout.addWidget(shutdown_btn)

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

    def _check_obs_health(self) -> None:
        if not self._session_active:
            return
        if not self.obs.ensure_recording():
            self._log_message("⚠ OBS recording check failed — see log above.")
        else:
            self._log_message("✓ OBS recording active.")

    def _setup_obs_scenes(self) -> None:
        if not self.obs.is_connected:
            QMessageBox.warning(self, "OBS", "Connect to OBS first.")
            return
        ok = self.obs.setup_scenes()
        if ok:
            self._log_message("✅ OBS scenes configured: R6_Comms + R6_Game")
            QMessageBox.information(
                self, "OBS Scenes",
                "Created scenes:\n"
                "  R6_Comms — Discord audio (used during sessions)\n"
                "  R6_Game  — Game capture (for personal recordings / streaming)\n\n"
                "You can customise sources further in OBS."
            )
        else:
            self._log_message(
                "⚠ Auto scene setup failed — create R6_Comms and R6_Game "
                "manually in OBS."
            )

    def _toggle_game_recording(self) -> None:
        if not self._game_recording_active:
            if self.obs.start_game_recording():
                self._game_recording_active = True
                self._game_rec_btn.setText("⏹  Stop Game Recording")
                self._log_message("Game recording started (R6_Game scene).")
            else:
                self._log_message("Failed to start game recording.")
        else:
            self.obs.stop_recording()
            self._game_recording_active = False
            self._game_rec_btn.setText("🎬  Start Game Recording")
            self._log_message("Game recording stopped.")

    def _toggle_stream(self) -> None:
        if not self._streaming_active:
            if self.obs.start_streaming():
                self._streaming_active = True
                self._stream_btn.setText("⏹  Stop Stream")
                self._stream_btn.setStyleSheet(
                    "QPushButton { color: #e05555; border: 1px solid #e05555; }"
                )
                self._log_message("🔴 Twitch stream started (R6_Game scene).")
            else:
                self._log_message(
                    "Stream failed. Configure Twitch key in OBS: "
                    "Settings → Stream → Service: Twitch → Stream Key."
                )
        else:
            self.obs.stop_streaming()
            self._streaming_active = False
            self._stream_btn.setText("📡  Start Stream")
            self._stream_btn.setStyleSheet("")
            self._log_message("Stream stopped.")
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
        obs_ready = self.obs.is_connected
        self._game_rec_btn.setEnabled(obs_ready)
        self._stream_btn.setEnabled(obs_ready)

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

        # ── Watchdog timer — checks OBS is still recording every 60s ──
        from PySide6.QtCore import QTimer
        self._obs_watchdog = QTimer(self)
        self._obs_watchdog.setInterval(60_000)   # every 60 seconds
        self._obs_watchdog.timeout.connect(self._check_obs_health)
        self._obs_watchdog.start()

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
        if hasattr(self, "_obs_watchdog"):
            self._obs_watchdog.stop()

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


    # =====================================================
    # Clean Old Storage
    # =====================================================
    def _refresh_storage_display(self) -> None:
        """Update the storage indicator."""
        try:
            from app.config import BASE_DIR, RECORDINGS_DIR
            import shutil
            usage = shutil.disk_usage(str(BASE_DIR))
            free_gb  = usage.free  / (1024**3)
            total_gb = usage.total / (1024**3)
            pct_used = usage.used  / usage.total * 100

            recordings = list(RECORDINGS_DIR.glob("*.mp4")) + \
                        list(RECORDINGS_DIR.glob("*.mkv"))
            rec_gb = sum(f.stat().st_size for f in recordings) / (1024**3)

            color = "#55e07a"        # green
            if pct_used > 80:
                color = "#e0a830"    # yellow
            if pct_used > 90:
                color = "#e05555"    # red

            self._storage_label.setText(
                f"💾 USB: {free_gb:.1f} GB free of {total_gb:.0f} GB  "
                f"({pct_used:.0f}% used)  |  "
                f"Recordings: {rec_gb:.1f} GB ({len(recordings)} files)"
            )
            self._storage_label.setStyleSheet(
                f"font-size: 11px; color: {color};"
            )
        except Exception:
            self._storage_label.setText("💾 Storage info unavailable")

    def _cleanup_recordings(self) -> None:
        """Delete old recordings, keeping the 3 most recent."""
        from PySide6.QtWidgets import QInputDialog, QMessageBox
        from app.config import RECORDINGS_DIR

        # Show what's there first
        recordings = sorted(
            list(RECORDINGS_DIR.glob("*.mp4")) +
            list(RECORDINGS_DIR.glob("*.mkv")),
            key=lambda f: f.stat().st_mtime,
            reverse=True,
        )

        if not recordings:
            QMessageBox.information(self, "Cleanup", "No recordings found.")
            return

        total_gb = sum(f.stat().st_size for f in recordings) / (1024**3)

        keep_n, ok = QInputDialog.getInt(
            self,
            "Clean Old Recordings",
            f"Found {len(recordings)} recording(s) using {total_gb:.1f} GB.\n\n"
            f"Keep how many most recent recordings?",
            3, 1, len(recordings), 1
        )
        if not ok:
            return

        to_delete = recordings[keep_n:]
        if not to_delete:
            QMessageBox.information(
                self, "Cleanup",
                f"Nothing to delete — only {len(recordings)} recording(s) found."
            )
            return

        delete_gb = sum(f.stat().st_size for f in to_delete) / (1024**3)
        confirm = QMessageBox.question(
            self, "Confirm Delete",
            f"Delete {len(to_delete)} recording(s) ({delete_gb:.1f} GB)?\n\n"
            + "\n".join(f.name for f in to_delete[:5])
            + ("\n..." if len(to_delete) > 5 else ""),
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if confirm != QMessageBox.StandardButton.Yes:
            return

        deleted = 0
        for f in to_delete:
            try:
                f.unlink()
                self._log_message(f"Deleted: {f.name}")
                deleted += 1
            except Exception as e:
                self._log_message(f"Could not delete {f.name}: {e}")

        self._log_message(f"✅ Cleaned {deleted} recording(s), freed {delete_gb:.1f} GB.")
        self._refresh_storage_display()
        QMessageBox.information(
            self, "Done",
            f"Deleted {deleted} recording(s), freed approximately {delete_gb:.1f} GB."
        )