import os
import shutil
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QComboBox,
    QPushButton, QMessageBox, QFileDialog
)
from PySide6.QtCore import Qt

class ExportView(QWidget):
    def __init__(self, parent, controller):
        super().__init__(parent)
        self.controller = controller
        self._build_layout()
        self.load_matches()

    def _build_layout(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(30, 30, 30, 30)
        layout.setSpacing(15)

        title = QLabel("Export Data Center")
        title.setStyleSheet("font-size: 22px; font-weight: bold;")
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(title)

        # Match Selection
        select_layout = QHBoxLayout()
        select_layout.addWidget(QLabel("Select Match:"))
        self.dropdown = QComboBox()
        self.dropdown.setMinimumWidth(350)
        select_layout.addWidget(self.dropdown)
        layout.addLayout(select_layout)

        # Export Buttons
        actions = [
            ("Export CSV", self.export_csv),
            ("Export Transcript (TXT)", self.export_txt),
            ("Export Report (HTML)", self.export_html),
            ("Export Recording (MP4)", self.export_recording)
        ]

        for text, func in actions:
            btn = QPushButton(text)
            btn.setMinimumHeight(35)
            btn.clicked.connect(func)
            layout.addWidget(btn)

        layout.addStretch() # Pushes everything to the top

    def load_matches(self):
        try:
            matches = self.controller.get_all_matches()
            self.dropdown.clear()
            for m in matches:
                # Assuming m.map and m.opponent_name based on your snippet
                text = f"{m.match_id} - {m.map} vs {m.opponent_name}"
                self.dropdown.addItem(text, m.match_id)
        except Exception as e:
            QMessageBox.critical(self, "Error", str(e))

    def _get_match_id(self):
        match_id = self.dropdown.currentData()
        if match_id is None:
            QMessageBox.warning(self, "Warning", "Please select a match first.")
        return match_id

    def _choose_path(self, default_name, filter_text="All Files (*.*)"):
        path, _ = QFileDialog.getSaveFileName(
            self, "Save File", default_name, filter_text
        )
        return path

    def export_csv(self):
        mid = self._get_match_id()
        if mid is None: return
        path = self._choose_path(f"match_{mid}.csv", "CSV Files (*.csv)")
        if path:
            try:
                self.controller.export_match_csv(mid, path)
                QMessageBox.information(self, "Success", f"CSV exported to:\n{path}")
            except Exception as e:
                QMessageBox.critical(self, "Export Error", str(e))

    def export_txt(self):
        mid = self._get_match_id()
        if mid is None: return
        path = self._choose_path(f"match_{mid}_transcript.txt", "Text Files (*.txt)")
        if path:
            try:
                text = self.controller.get_transcript_text(mid)
                with open(path, "w", encoding="utf-8") as f:
                    f.write(text)
                QMessageBox.information(self, "Success", "Transcript exported.")
            except Exception as e:
                QMessageBox.critical(self, "Export Error", str(e))

    def export_html(self):
        mid = self._get_match_id()
        if mid is None: return
        path = self._choose_path(f"match_{mid}_report.html", "HTML Files (*.html)")
        if path:
            try:
                report_path = self.controller.generate_match_report(mid)
                shutil.copy(report_path, path)
                QMessageBox.information(self, "Success", "Report exported.")
            except Exception as e:
                QMessageBox.critical(self, "Export Error", str(e))

    def export_recording(self):
        mid = self._get_match_id()
        if mid is None: return
        path = self._choose_path(f"match_{mid}_recording.mp4", "Video Files (*.mp4)")
        if path:
            try:
                rec_path = self.controller.get_recording_path(mid)
                if not os.path.exists(rec_path):
                    raise FileNotFoundError("Recording file not found.")
                shutil.copy(rec_path, path)
                QMessageBox.information(self, "Success", "Recording exported.")
            except Exception as e:
                QMessageBox.critical(self, "Export Error", str(e))