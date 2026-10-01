import os
import shutil
from pathlib import Path

from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QComboBox,
    QPushButton, QMessageBox, QFileDialog, QGroupBox,
    QTableWidget, QTableWidgetItem, QHeaderView, QAbstractItemView,
    QScrollArea, QFrame
)
from PySide6.QtCore import Qt, QThread, QObject, Signal

from app.app_controller import AppController
from app.config import settings


class _SyncWorker(QObject):
    """
    Runs SyncCoordinator.sync_once() off the UI thread. Pure Qt plumbing —
    all actual upload logic lives in app/sync_coordinator.py, which has no
    Qt dependency and is unit-tested headlessly.
    """
    finished = Signal(dict)
    error    = Signal(str)
    progress = Signal(str)

    def run(self) -> None:
        try:
            from app.sync_coordinator import SyncCoordinator
            coordinator = SyncCoordinator(log_callback=lambda msg: self.progress.emit(msg))
            summary = coordinator.sync_once()
            self.finished.emit(summary)
        except Exception as e:
            self.error.emit(str(e))


class ExportView(QWidget):

    def __init__(self, parent: QWidget | None, controller: AppController) -> None:
        super().__init__(parent)
        self.controller = controller
        self._sync_thread: QThread | None = None
        self._sync_worker: _SyncWorker | None = None
        self._build_layout()
        self.load_matches()
        self.refresh_queue_table()

    # =====================================================
    # UI
    # =====================================================

    def _build_layout(self) -> None:
        # Wrapped in a QScrollArea (same pattern as dashboard_view.py) so
        # the title + match selector + actions group + sync group (with
        # its 160px-minimum queue table) scroll instead of clipping on a
        # short screen.
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)

        content = QWidget()
        layout = QVBoxLayout(content)
        layout.setContentsMargins(30, 30, 30, 30)
        layout.setSpacing(20)

        title = QLabel("Export")
        title.setStyleSheet("font-size: 22px; font-weight: bold;")
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(title)

        # ── Match selector ─────────────────────────────────
        sel_group = QGroupBox("Select Match")
        sel_layout = QHBoxLayout(sel_group)
        self.dropdown = QComboBox()
        self.dropdown.setMinimumWidth(350)
        refresh_btn = QPushButton("↺")
        refresh_btn.setFixedWidth(32)
        refresh_btn.setToolTip("Refresh match list")
        refresh_btn.clicked.connect(self.load_matches)
        sel_layout.addWidget(self.dropdown, stretch=1)
        sel_layout.addWidget(refresh_btn)
        layout.addWidget(sel_group)

        # ── Export actions ─────────────────────────────────
        actions_group = QGroupBox("Export Actions")
        actions_layout = QVBoxLayout(actions_group)
        actions_layout.setSpacing(10)

        buttons = [
            ("📊  Export CSV  (Player Stats)",        self.export_csv),
            ("📄  Export Report  (HTML)",              self.export_html),
            ("📝  Export Report  (TXT)",               self.export_txt),
            ("🎙  Export Transcript",                  self.export_transcript),
            ("📝  Export Full Session Transcript", self.export_full_transcript),
            ("🎬  Copy Recording  (MP4)",              self.export_recording),
        ]

        for label, slot in buttons:
            btn = QPushButton(label)
            btn.setMinimumHeight(38)
            btn.clicked.connect(slot)
            actions_layout.addWidget(btn)

        layout.addWidget(actions_group)

        # ── Remote sync ──────────────────────────────────────
        sync_group = QGroupBox("Remote Sync")
        sync_layout = QVBoxLayout(sync_group)
        sync_layout.setSpacing(8)

        self.sync_status_label = QLabel("")
        self.sync_status_label.setWordWrap(True)
        self.sync_status_label.setStyleSheet("color: #999;")

        self.sync_button = QPushButton("🔄  Sync Pending Sessions")
        self.sync_button.setMinimumHeight(38)
        self.sync_button.clicked.connect(self.sync_pending_sessions)

        sync_layout.addWidget(self.sync_button)
        sync_layout.addWidget(self.sync_status_label)

        # ── Per-session queue status table ──────────────────
        queue_header_row = QHBoxLayout()
        queue_label = QLabel("Session Queue")
        queue_label.setStyleSheet("font-weight: bold;")
        queue_refresh_btn = QPushButton("↺")
        queue_refresh_btn.setFixedWidth(28)
        queue_refresh_btn.setToolTip("Refresh queue status")
        queue_refresh_btn.clicked.connect(self.refresh_queue_table)
        queue_header_row.addWidget(queue_label)
        queue_header_row.addStretch()
        queue_header_row.addWidget(queue_refresh_btn)
        sync_layout.addLayout(queue_header_row)

        self.queue_table = QTableWidget(0, 6)
        self.queue_table.setHorizontalHeaderLabels(
            ["Session", "Package", "Local Analysis", "Remote Analysis", "Retries", "Last Update"]
        )
        self.queue_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self.queue_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        self.queue_table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        self.queue_table.horizontalHeader().setSectionResizeMode(3, QHeaderView.ResizeMode.ResizeToContents)
        self.queue_table.horizontalHeader().setSectionResizeMode(4, QHeaderView.ResizeMode.ResizeToContents)
        self.queue_table.horizontalHeader().setSectionResizeMode(5, QHeaderView.ResizeMode.ResizeToContents)
        self.queue_table.verticalHeader().setVisible(False)
        self.queue_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.queue_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.queue_table.setMinimumHeight(160)
        sync_layout.addWidget(self.queue_table)

        layout.addWidget(sync_group)

        layout.addStretch()

        scroll.setWidget(content)
        outer.addWidget(scroll)

    # =====================================================
    # LOAD MATCHES
    # =====================================================

    def load_matches(self) -> None:
        try:
            from database.repositories import Repository
            matches = Repository().get_all_matches()
            self.dropdown.blockSignals(True)
            self.dropdown.clear()
            for m in matches:
                label = f"{m.match_id}: {m.opponent_name} ({m.map})"
                self.dropdown.addItem(label, m.match_id)
            self.dropdown.blockSignals(False)
        except Exception as e:
            QMessageBox.critical(self, "Error", str(e))

    # =====================================================
    # HELPERS
    # =====================================================

    def _get_match_id(self) -> int | None:
        match_id = self.dropdown.currentData()
        if match_id is None:
            QMessageBox.warning(self, "Warning", "Please select a match first.")
        return match_id

    def _save_dialog(self, default_name: str, filter_str: str) -> str | None:
        path, _ = QFileDialog.getSaveFileName(
            self, "Save File", default_name, filter_str
        )
        return path if path else None

    # =====================================================
    # EXPORT ACTIONS
    # =====================================================

    def export_csv(self) -> None:
        mid = self._get_match_id()
        if mid is None:
            return
        path = self._save_dialog(f"match_{mid}.csv", "CSV Files (*.csv)")
        if not path:
            return
        try:
            self.controller.export_match_csv(mid, path)
            QMessageBox.information(self, "Exported", f"CSV saved to:\n{path}")
        except Exception as e:
            QMessageBox.critical(self, "Export Error", str(e))

    def export_html(self) -> None:
        mid = self._get_match_id()
        if mid is None:
            return
        path = self._save_dialog(f"match_{mid}_report.html", "HTML Files (*.html)")
        if not path:
            return
        try:
            report_path = self.controller.regenerate_report(mid)
            shutil.copy(report_path, path)
            QMessageBox.information(self, "Exported", f"HTML report saved to:\n{path}")
        except Exception as e:
            QMessageBox.critical(self, "Export Error", str(e))

    def export_txt(self) -> None:
        mid = self._get_match_id()
        if mid is None:
            return
        path = self._save_dialog(f"match_{mid}_report.txt", "Text Files (*.txt)")
        if not path:
            return
        try:
            # Regenerate report which writes the TXT file, then copy it
            report_html_path = self.controller.regenerate_report(mid)
            txt_path = Path(report_html_path).with_suffix(".txt")
            if txt_path.exists():
                shutil.copy(txt_path, path)
                QMessageBox.information(self, "Exported", f"TXT report saved to:\n{path}")
            else:
                QMessageBox.warning(
                    self, "Not Found",
                    "TXT report was not generated. Try exporting HTML first."
                )
        except Exception as e:
            QMessageBox.critical(self, "Export Error", str(e))

    def export_transcript(self) -> None:
        mid = self._get_match_id()
        if mid is None:
            return
        path = self._save_dialog(
            f"match_{mid}_transcript.txt", "Text Files (*.txt)"
        )
        if not path:
            return
        try:
            text = self.controller.get_transcript_text(mid)
            if not text:
                QMessageBox.warning(
                    self, "No Transcript",
                    "No transcript found for this match.\n"
                    "Transcripts are generated automatically during session import."
                )
                return
            Path(path).write_text(text, encoding="utf-8")
            QMessageBox.information(self, "Exported", f"Transcript saved to:\n{path}")
        except Exception as e:
            QMessageBox.critical(self, "Export Error", str(e))

    def export_full_transcript(self) -> None:
        from app.config import TRANSCRIPTS_DIR
        import shutil

        # Find the most recent full session transcript
        full_transcripts = sorted(
            TRANSCRIPTS_DIR.glob("session_*_full.txt"),
            key=lambda f: f.stat().st_mtime,
            reverse=True,
        )

        if not full_transcripts:
            QMessageBox.warning(
                self, "Not Found",
                "No full session transcript found.\n"
                "Full transcripts are generated automatically during session import."
            )
            return

        # Show list if multiple
        if len(full_transcripts) > 1:
            from PySide6.QtWidgets import QInputDialog
            names = [f.name for f in full_transcripts]
            choice, ok = QInputDialog.getItem(
                self, "Select Transcript", "Session:", names, 0, False
            )
            if not ok:
                return
            src = TRANSCRIPTS_DIR / choice
        else:
            src = full_transcripts[0]

        path = self._save_dialog(src.name, "Text Files (*.txt)")
        if not path:
            return

        try:
            shutil.copy(src, path)
            QMessageBox.information(self, "Exported", f"Full transcript saved to:\n{path}")
        except Exception as e:
            QMessageBox.critical(self, "Export Error", str(e))

    def export_recording(self) -> None:
        mid = self._get_match_id()
        if mid is None:
            return
        path = self._save_dialog(
            f"match_{mid}_recording.mp4", "Video Files (*.mp4 *.mkv *.flv)"
        )
        if not path:
            return
        try:
            rec_path = self.controller.get_recording_path(mid)
            if not rec_path:
                QMessageBox.warning(
                    self, "No Recording",
                    "No recording path stored for this match.\n"
                    "Recordings are linked automatically during session import."
                )
                return
            if not os.path.exists(rec_path):
                QMessageBox.warning(
                    self, "File Missing",
                    f"Recording file not found at:\n{rec_path}"
                )
                return
            shutil.copy(rec_path, path)
            QMessageBox.information(self, "Exported", f"Recording copied to:\n{path}")
        except Exception as e:
            QMessageBox.critical(self, "Export Error", str(e))

    # =====================================================
    # REMOTE SYNC
    # =====================================================

    def sync_pending_sessions(self) -> None:
        if settings.ANALYSIS_MODE == "local":
            QMessageBox.information(
                self, "Local Mode",
                "analysis_mode is set to 'local' in Settings — nothing to sync.\n"
                "Switch to 'remote' or 'automatic' to enable uploading."
            )
            return

        if not settings.SERVER_URL:
            QMessageBox.warning(
                self, "No Server Configured",
                "No server_url is configured in Settings — nothing to sync to."
            )
            return

        if self._sync_thread is not None and self._sync_thread.isRunning():
            return  # Already syncing — ignore duplicate clicks.

        self.sync_button.setEnabled(False)
        self.sync_status_label.setText("Syncing...")

        self._sync_thread = QThread()
        self._sync_worker = _SyncWorker()
        self._sync_worker.moveToThread(self._sync_thread)

        self._sync_thread.started.connect(self._sync_worker.run)
        self._sync_worker.progress.connect(self._on_sync_progress)
        self._sync_worker.finished.connect(self._on_sync_finished)
        self._sync_worker.error.connect(self._on_sync_error)

        self._sync_worker.finished.connect(self._sync_thread.quit)
        self._sync_worker.error.connect(self._sync_thread.quit)
        self._sync_thread.finished.connect(self._sync_cleanup)

        self._sync_thread.start()

    def _on_sync_progress(self, msg: str) -> None:
        self.sync_status_label.setText(msg)

    def _on_sync_finished(self, summary: dict) -> None:
        self.sync_button.setEnabled(True)
        if summary.get("mode_blocked"):
            self.sync_status_label.setText("Sync skipped — check analysis_mode in Settings.")
            self.refresh_queue_table()
            return
        self.sync_status_label.setText(
            f"Sync complete — {summary.get('succeeded', 0)} uploaded "
            f"({summary.get('duplicate', 0)} already existed), "
            f"{summary.get('failed', 0)} failed, "
            f"{summary.get('skipped_backoff_or_retries', 0)} skipped (backoff/retry limit)."
        )
        self.refresh_queue_table()

    def _on_sync_error(self, message: str) -> None:
        self.sync_button.setEnabled(True)
        self.sync_status_label.setText(f"Sync error: {message}")
        QMessageBox.critical(self, "Sync Error", message)
        self.refresh_queue_table()

    def _sync_cleanup(self) -> None:
        if self._sync_thread is not None:
            self._sync_thread.deleteLater()
        self._sync_thread = None
        self._sync_worker = None

    # =====================================================
    # QUEUE STATUS TABLE
    # =====================================================

    # Status values that mean "this needs attention" get a warning/error tint;
    # everything else (created/uploaded/analyzed/none) stays neutral.
    _QUEUE_STATUS_COLORS = {
        "upload_failed": "#e05555",
        "failed": "#e05555",
        "pending_upload": "#e0c455",
        "uploading": "#e0c455",
    }

    def refresh_queue_table(self) -> None:
        """Repopulates the queue table from app/upload_queue.py's on-disk
        state. Safe to call anytime — read-only, no network or Qt threads."""
        try:
            from app.upload_queue import UploadQueue
            items = UploadQueue().list_items()
        except Exception as e:
            self.queue_table.setRowCount(0)
            self.sync_status_label.setText(f"Could not read upload queue: {e}")
            return

        items.sort(key=lambda it: it.updated_at, reverse=True)

        self.queue_table.setRowCount(len(items))
        for row, item in enumerate(items):
            values = [
                item.session_id,
                item.package_status,
                item.local_analysis_status,
                item.remote_analysis_status,
                str(item.retry_count),
                item.updated_at.split("T")[0] if item.updated_at else "",
            ]
            for col, value in enumerate(values):
                cell = QTableWidgetItem(value)
                color = self._QUEUE_STATUS_COLORS.get(value)
                if color and col in (1, 2, 3):
                    cell.setForeground(Qt.GlobalColor.red if color == "#e05555" else Qt.GlobalColor.yellow)
                if item.last_error and col == 1:
                    cell.setToolTip(item.last_error)
                self.queue_table.setItem(row, col, cell)
