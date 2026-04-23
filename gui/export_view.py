import os
import shutil
from pathlib import Path

from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QComboBox,
    QPushButton, QMessageBox, QFileDialog, QGroupBox
)
from PySide6.QtCore import Qt

from app.app_controller import AppController
from app.config import REPORTS_DIR


class ExportView(QWidget):

    def __init__(self, parent: QWidget | None, controller: AppController) -> None:
        super().__init__(parent)
        self.controller = controller
        self._build_layout()
        self.load_matches()

    # =====================================================
    # UI
    # =====================================================

    def _build_layout(self) -> None:
        layout = QVBoxLayout(self)
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
        layout.addStretch()

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
