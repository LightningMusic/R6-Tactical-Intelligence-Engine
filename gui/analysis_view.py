from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QComboBox,
    QPushButton, QTableWidget, QTableWidgetItem, QMessageBox, QHeaderView
)
from PySide6.QtCore import Qt

from app.app_controller import AppController


class AnalysisView(QWidget):
    def __init__(self, parent, controller: AppController):
        super().__init__(parent)
        self.controller = controller
        self._build_layout()
        self.load_matches()

    def _build_layout(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 20, 20, 20)
        layout.setSpacing(15)

        header = QLabel("Match Analysis")
        header.setStyleSheet("font-size: 24px; font-weight: bold;")
        header.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(header)

        selection_layout = QHBoxLayout()
        selection_layout.addWidget(QLabel("Select Match:"))
        self.match_dropdown = QComboBox()
        self.match_dropdown.setMinimumWidth(300)
        selection_layout.addWidget(self.match_dropdown)

        run_button = QPushButton("Run Analysis")
        run_button.clicked.connect(self.run_analysis)
        selection_layout.addWidget(run_button)
        layout.addLayout(selection_layout)

        self.results_table = QTableWidget(0, 2)
        self.results_table.setHorizontalHeaderLabels(["Metric", "Value"])
        self.results_table.horizontalHeader().setSectionResizeMode(
            0, QHeaderView.ResizeMode.Stretch
        )
        self.results_table.setAlternatingRowColors(True)
        self.results_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        layout.addWidget(self.results_table)

        export_button = QPushButton("Generate Report")
        export_button.setMinimumHeight(40)
        export_button.clicked.connect(self.generate_report)
        layout.addWidget(export_button)

    def load_matches(self, select_match_id: int | None = None) -> None:
        try:
            from database.repositories import Repository
            repo = Repository()
            matches = repo.get_all_matches()

            self.match_dropdown.blockSignals(True)
            self.match_dropdown.clear()

            target_index = 0
            for i, m in enumerate(matches):
                label = f"{m.match_id}: {m.opponent_name} ({m.map})"
                self.match_dropdown.addItem(label, m.match_id)
                if select_match_id is not None and m.match_id == select_match_id:
                    target_index = i

            self.match_dropdown.blockSignals(False)
            if matches:
                self.match_dropdown.setCurrentIndex(target_index)

        except Exception as e:
            QMessageBox.critical(self, "Error", str(e))

    def run_analysis(self) -> None:
        match_id = self.match_dropdown.currentData()
        if match_id is None:
            QMessageBox.warning(self, "Warning", "Select a match first.")
            return
        try:
            metrics = self.controller.fetch_match_intel(match_id)
            self.display_metrics(metrics)
        except Exception as e:
            QMessageBox.critical(self, "Analysis Error", str(e))

    def display_metrics(self, metrics: dict) -> None:
        self.results_table.setRowCount(0)
        for key, value in metrics.items():
            row = self.results_table.rowCount()
            self.results_table.insertRow(row)
            self.results_table.setItem(row, 0, QTableWidgetItem(str(key)))
            self.results_table.setItem(row, 1, QTableWidgetItem(str(value)))

    def generate_report(self) -> None:
        match_id = self.match_dropdown.currentData()
        if match_id is None:
            QMessageBox.warning(self, "Warning", "Select a match first.")
            return
        try:
            path = self.controller.regenerate_report(match_id)
            QMessageBox.information(self, "Report Generated", f"Saved to:\n{path}")
        except Exception as e:
            QMessageBox.critical(self, "Report Error", str(e))