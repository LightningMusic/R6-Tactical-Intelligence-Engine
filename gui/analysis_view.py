from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QComboBox,
    QPushButton, QTableWidget, QTableWidgetItem, QMessageBox, QHeaderView
)
from PySide6.QtCore import Qt

class AnalysisView(QWidget):
    def __init__(self, parent, controller):
        super().__init__(parent)
        self.controller = controller
        self._build_layout()
        self.load_matches()

    def _build_layout(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 20, 20, 20)
        layout.setSpacing(15)

        # Header
        header = QLabel("Match Analysis")
        header.setStyleSheet("font-size: 24px; font-weight: bold;")
        header.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(header)

        # Match selection row
        selection_layout = QHBoxLayout()
        selection_layout.addWidget(QLabel("Select Match:"))

        self.match_dropdown = QComboBox()
        self.match_dropdown.setMinimumWidth(300)
        selection_layout.addWidget(self.match_dropdown)

        run_button = QPushButton("Run Analysis")
        run_button.clicked.connect(self.run_analysis)
        selection_layout.addWidget(run_button)
        
        layout.addLayout(selection_layout)

        # Results table (Replacing Treeview)
        self.results_table = QTableWidget(0, 2)
        self.results_table.setHorizontalHeaderLabels(["Metric", "Value"])
        self.results_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self.results_table.setAlternatingRowColors(True)
        self.results_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        layout.addWidget(self.results_table)

        # Export button
        export_button = QPushButton("Generate Report")
        export_button.setMinimumHeight(40)
        export_button.clicked.connect(self.generate_report)
        layout.addWidget(export_button)

    def load_matches(self):
        try:
            matches = self.controller.get_all_matches()
            self.match_dropdown.clear()
            for m in matches:
                # We store the match_id in the UserData for easy retrieval
                text = f"{m.match_id} - {m.map_name} vs {m.enemy_team}"
                self.match_dropdown.addItem(text, m.match_id)
        except Exception as e:
            QMessageBox.critical(self, "Error", str(e))

    def run_analysis(self):
        match_id = self.match_dropdown.currentData()
        if match_id is None:
            QMessageBox.warning(self, "Warning", "Select a match first.")
            return

        try:
            metrics = self.controller.analyze_match(match_id)
            self.display_metrics(metrics)
        except Exception as e:
            QMessageBox.critical(self, "Analysis Error", str(e))

    def display_metrics(self, metrics):
        self.results_table.setRowCount(0)
        for key, value in metrics.items():
            row = self.results_table.rowCount()
            self.results_table.insertRow(row)
            self.results_table.setItem(row, 0, QTableWidgetItem(str(key)))
            self.results_table.setItem(row, 1, QTableWidgetItem(str(value)))

    def generate_report(self):
        match_id = self.match_dropdown.currentData()
        if match_id is None:
            QMessageBox.warning(self, "Warning", "Select a match first.")
            return

        try:
            path = self.controller.generate_match_report(match_id)
            QMessageBox.information(self, "Report Generated", f"Report saved to:\n{path}")
        except Exception as e:
            QMessageBox.critical(self, "Report Error", str(e))