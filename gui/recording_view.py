import time
from pathlib import Path

from PySide6.QtCore import Qt, QThread, QObject, Signal
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel,
    QPushButton, QTextEdit, QFileDialog, QMessageBox, QInputDialog,
    QScrollArea, QFrame
)

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


class _LiveScanWorker(QObject):
    """
    Runs one mid-session import pass off the GUI thread.

    Each match that has finished gets parsed, packaged and queued for upload
    while you are still playing the next one, so the stop button has almost
    nothing left to do and the USB can come out as soon as the queue drains.
    """
    finished = Signal(int)   # matches handled by this pass
    progress = Signal(str)

    def __init__(self, session_manager: SessionManager) -> None:
        super().__init__()
        self._session = session_manager

    def run(self) -> None:
        try:
            results = self._session.process_pending_matches(
                status_callback=lambda msg: self.progress.emit(msg),
                quiet_when_idle=True,
            )
            self.finished.emit(len(results))
        except Exception as e:
            # Never surface as a session-ending failure: the final pass in
            # end_session() will retry anything this missed.
            self.progress.emit(f"Live import pass failed (will retry at stop): {e}")
            self.finished.emit(0)


class _CompanionSync(QObject):
    """Talks to the team server about R6Companion off the GUI thread; log
    lines come back through the signal."""
    line = Signal(str)

    def __init__(self) -> None:
        super().__init__()
        from app.companion_link import CompanionLink
        self.link = CompanionLink()
        self.names: dict[str, str] = {}
        try:
            from database.repositories import Repository
            with Repository().db.get_connection() as conn:
                for name, alias in conn.execute(
                    "SELECT p.name, a.alias FROM players p LEFT JOIN player_aliases a "
                    "ON a.player_id = p.player_id WHERE p.is_team_member = 1"
                ):
                    self.names[str(alias or name).lower()] = str(name)
        except Exception:
            pass

    def push(self, recording: bool) -> None:
        import threading
        threading.Thread(target=self._push, args=(recording,), daemon=True).start()

    def _push(self, recording: bool) -> None:
        ok = self.link.set_recording(recording)
        if not ok:
            return
        for line in self.link.changed_lines(self.names):
            self.line.emit("👥 " + line)


class RecordingView(QWidget):
    navigate_to_analysis         = Signal(int)
    navigate_to_match_input      = Signal()
    navigate_to_match_input_partial = Signal(object)
    _obs_started                 = Signal(bool)

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
        self._live_thread: QThread | None    = None
        self._recording_path: str | None     = None
        self._game_recording_active = False
        self._streaming_active      = False
        self._starting              = False
        self._obs_started.connect(self._finish_start_session)
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
            if hasattr(self, "_live_scan_timer"):
                self._live_scan_timer.stop()

            # Stop the background uploader before anything else touches the
            # drive -- it writes queue.json and deletes uploaded packages on
            # the USB, and ejecting out from under a write is how a queue
            # file gets truncated.
            if self._session_manager:
                try:
                    self._session_manager.stop_background_sync()
                    self._log_message("Background upload sync stopped.")
                except Exception as e:
                    self._log_message(f"Sync stop error: {e}")

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
        # This view stacks a lot of rows (OBS status, folder picker, status
        # label, storage indicator, cleanup button, start/stop buttons,
        # game-rec/stream row, scene setup, shutdown button, progress
        # label, hotkey label, and a 220px-minimum log box) in one column
        # with no scroll fallback — on any screen where the visible client
        # area is at or below MainWindow's 750px minimum height (a laptop
        # with taskbar/window chrome eating into that, e.g.), the bottom
        # rows get clipped with no way to reach them. Wrapping everything
        # in a QScrollArea (same pattern as dashboard_view.py) fixes that:
        # the outer layout on `self` holds only the scroll area, and
        # `content`/`layout` below is exactly the same column of widgets
        # this view always built, just now inside something that scrolls.
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)

        content = QWidget()
        layout = QVBoxLayout(content)
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
            self._folder_label.setWordWrap(True)
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

        scroll.setWidget(content)
        outer.addWidget(scroll)


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
                "Failed to connect to OBS. Every saved profile in Settings "
                "was tried and none worked -- check that OBS is running "
                "with the websocket server enabled, and that one of the "
                "saved profiles has the correct password for this PC."
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
        if not self._replay_folder or self._starting:
            return

        # OBS can be slow to answer and each call may wait up to a minute, so
        # it is driven from a worker thread; the window stays responsive.
        self._starting = True
        self._start_btn.setEnabled(False)
        self._set_status("⏳ Starting OBS recording...", "#e0a830")
        self._log_message("Starting OBS recording (can take a few seconds)...")
        import threading
        threading.Thread(target=self._obs_start_worker, daemon=True, name="ObsStart").start()

    def _obs_start_worker(self) -> None:
        try:
            ok = bool(self.obs.start_recording())
        except Exception as e:
            print(f"[OBS] Start failed: {e}")
            ok = False
        self._obs_started.emit(ok)

    def _finish_start_session(self, started: bool) -> None:
        self._starting = False
        if not started:
            self._update_start_button()
            self._set_status("❌ OBS did not start recording.", "#e05555")
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
            self._update_start_button()
            return

        self._session_manager = SessionManager(
            replay_folder=self._replay_folder,
            importer=importer,
            transcribe=settings.TRANSCRIBE_AUTO,
            stability_wait=settings.STABILITY_WAIT,
            stability_checks=settings.STABILITY_CHECKS,
        )
        self._session_manager.start_session()

        # Resolve the in-progress recording now rather than at stop. The live
        # import passes below slice each finished match's audio out of this
        # file while the session is still running, so they need it up front;
        # without it they would package replays with no voice at all.
        active_recording = self.obs.get_active_recording_path()
        if active_recording:
            self._session_manager.recording_path = Path(active_recording)
            self._log_message(f"Recording to: {Path(active_recording).name}")
        else:
            self._log_message(
                "⚠ Could not identify the active recording file — matches will be "
                "packaged without voice until the session stops."
            )

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

        # ── Live import timer — imports, packages and queues each match a
        # couple of minutes after it ends, instead of saving the entire
        # session's work for the stop button. By the time you stop, the
        # uploads are usually already done and the USB is safe to pull.
        self._live_scan_timer = QTimer(self)
        self._live_scan_timer.setInterval(120_000)   # every 2 minutes
        self._live_scan_timer.timeout.connect(self._run_live_scan)
        self._live_scan_timer.start()

        # ── Teammates' R6Companion: start with us, and say how they're doing.
        # Re-sent every minute -- that's how companions know this app is
        # still running. First status report comes on the next tick, once
        # they've had a chance to check in.
        self._companions = _CompanionSync()
        self._companions.line.connect(self._log_message)
        self._companions.push(True)
        self._companion_timer = QTimer(self)
        self._companion_timer.setInterval(60_000)
        self._companion_timer.timeout.connect(lambda: self._companions.push(True))
        self._companion_timer.start()

    def _run_live_scan(self) -> None:
        """Fires one mid-session import pass, unless the previous one is
        still going — in which case the next tick picks it up."""
        if not self._session_active or not self._session_manager:
            return
        if self._live_thread is not None and self._live_thread.isRunning():
            return

        self._live_thread = QThread()
        self._live_worker = _LiveScanWorker(self._session_manager)
        self._live_worker.moveToThread(self._live_thread)

        self._live_thread.started.connect(self._live_worker.run)
        self._live_worker.progress.connect(self._log_message)
        self._live_worker.finished.connect(self._on_live_scan_finished)
        self._live_worker.finished.connect(self._live_thread.quit)

        self._live_thread.start()

    def _on_live_scan_finished(self, count: int) -> None:
        if count:
            self._log_message(
                f"📤 {count} finished match(es) packaged and queued — "
                f"uploading in the background while you play."
            )

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
        # No new live passes from here on. One already in flight is fine and
        # is NOT interrupted -- end_session()'s final sweep waits for it
        # rather than skipping past its results.
        if hasattr(self, "_live_scan_timer"):
            self._live_scan_timer.stop()
        if hasattr(self, "_companion_timer"):
            self._companion_timer.stop()
            self._companions.push(False)       # companions stop and upload their audio

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
    # MAPS THE CATALOG COULDN'T IDENTIFY
    # =====================================================

    def _prompt_for_unknown_maps(self) -> None:
        """
        Asks once per map ID the game catalog couldn't identify. Most "new"
        map IDs are reworks, which the catalog already recognizes by their
        site names, so this only comes up for genuinely new maps or when
        sources disagree. Cancelling leaves it flagged; it's asked again
        after the next import.
        """
        try:
            from database.db_manager import DatabaseManager
            from database.game_catalog import pending_unknown_maps, name_unknown_map
            db = DatabaseManager()
            pending = pending_unknown_maps(db)
        except Exception as e:
            self._log_message(f"Could not check for unrecognized maps: {e}")
            return

        for item in pending:
            with db.get_connection() as conn:
                known_maps = [r[0] for r in conn.execute("SELECT name FROM maps ORDER BY name")]
            sites = item.get("sites") or "none recorded"
            hint = item.get("dissect_name")
            hint_line = f"\nThe replay parser calls it: {hint}" if hint and not hint.startswith("Map(") else ""
            name, ok = QInputDialog.getItem(
                self,
                "New map detected",
                f"A match was played on a map the app doesn't recognize yet.\n\n"
                f"Map ID: {item['game_id']}\n"
                f"Bomb sites seen: {sites}{hint_line}\n"
                f"Matches waiting on it: {item.get('matches', 0)}\n\n"
                f"Pick the map if it's a rework of one below, or type the new map's name:",
                [""] + known_maps,  # blank first: an accidental Enter mustn't mislabel it
                0,
                True,
            )
            if not ok or not name.strip():
                self._log_message(f"⚑ Map ID {item['game_id']} left unnamed -- you'll be asked again next import.")
                continue
            try:
                final, backfilled = name_unknown_map(db, int(item["game_id"]), name)
                self._log_message(
                    f"✓ Map ID {item['game_id']} is now {final}; "
                    f"updated {backfilled} match(es) already recorded on it."
                )
            except Exception as e:
                self._log_message(f"Could not name map {item['game_id']}: {e}")

    # =====================================================
    # "CAN I PULL THE USB YET?"
    # =====================================================

    def _watch_upload_drain(self) -> None:
        """
        Polls the upload queue after a session until it empties, then says so
        outright. The whole point of the server is being able to pack up and
        leave with the team, and that needs a definite answer to "is it all
        sent?" rather than a guess based on the log scrolling past.
        """
        if not self._session_manager:
            return

        from PySide6.QtCore import QTimer

        state = self._session_manager.upload_readiness()

        if not state["upload_enabled"]:
            self._log_message(
                "Uploads are off for this client (local mode or no server configured) — "
                "nothing is waiting to send."
            )
            return

        if state["safe_to_remove"]:
            self._set_status("✅ All sessions uploaded — safe to remove USB.", "#55e07a")
            self._log_message(
                f"✅ All {state['uploaded']} session(s) confirmed on the server — "
                f"safe to remove the USB."
            )
            return

        self._set_status(
            f"⏳ Uploading — {state['pending']} session(s) left...", "#e0a830"
        )
        # This runs every 5 seconds; repeating the same reason each time
        # buried the real log under hundreds of identical lines. Say it when
        # it changes, and otherwise only once a minute.
        reasons = tuple(state["blocked_reasons"][:2])
        now = time.monotonic()
        if reasons and (
            reasons != getattr(self, "_drain_logged_reasons", ())
            or now - getattr(self, "_drain_logged_at", 0.0) >= 60.0
        ):
            for reason in reasons:
                self._log_message(f"   upload not through yet (will keep retrying): {reason}")
            self._drain_logged_reasons = reasons
            self._drain_logged_at = now

        # Keep watching. The background sync loop retries with backoff, so
        # this resolves on its own once the server is reachable again.
        QTimer.singleShot(5000, self._watch_upload_drain)

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

        # Most matches were already packaged and sent during the session, so
        # this usually confirms "safe to remove" within a few seconds.
        self._watch_upload_drain()
        self._prompt_for_unknown_maps()

        statuses = {r.status for r in results}

        for r in results:
            self._log_message(
                f"  {r.status.value}: {len(r.rounds)} rounds"
                + (f" | match_id={r.match_id}" if r.match_id else "")
                + (f" | {r.error_message}" if r.error_message else "")
            )

        if ImportStatus.CRITICAL_FAILURE in statuses and all(
            r.status == ImportStatus.CRITICAL_FAILURE for r in results
        ):
            self._log_message("All imports critically failed — going to Manual Entry.")
            QMessageBox.warning(
                self, "Import Failed",
                "Could not parse any replays.\n"
                "Going to Manual Entry — create a match manually."
            )
            self.navigate_to_match_input.emit()
            return

        # ── Prompt for opponent name for each created match ───────
        success_results  = [r for r in results if r.status == ImportStatus.SUCCESS]
        partial_results  = [r for r in results if r.status == ImportStatus.PARTIAL_FAILURE]
        created_results  = [r for r in results if r.match_id is not None]

        if created_results:
            self._prompt_opponent_names(created_results)

        if success_results:
            last_match_id = success_results[-1].match_id
            if last_match_id is not None:
                self._log_message(f"Routing to Analysis (match {last_match_id}).")
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
            self._refresh_storage_display()

    def _prompt_opponent_names(self, results: list) -> None:
        """Ask the user to name each imported match before routing."""
        from database.repositories import Repository
        repo = Repository()

        for result in results:
            if result.match_id is None:
                continue

            map_display = result.map_name or "Unknown"
            rounds_display = len(result.rounds)

            opponent, ok = QInputDialog.getText(
                self,
                "Name This Match",
                f"Match {result.match_id} imported:\n"
                f"  Map: {map_display}  |  {rounds_display} rounds\n\n"
                f"Who did you play against?\n"
                f"(Leave blank to keep as 'Imported')",
            )

            if ok and opponent.strip():
                try:
                    with repo.db.get_connection() as conn:
                        conn.execute(
                            "UPDATE matches SET opponent_name = ? WHERE match_id = ?",
                            (opponent.strip(), result.match_id)
                        )
                        conn.commit()
                    self._log_message(
                        f"Match {result.match_id} named: vs {opponent.strip()}"
                    )
                except Exception as e:
                    self._log_message(f"Could not save opponent name: {e}")


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