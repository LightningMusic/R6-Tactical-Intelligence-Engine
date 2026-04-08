from PySide6.QtWidgets import QMainWindow, QWidget, QVBoxLayout, QTabWidget

from app.app_controller import AppController
from gui.match_view import MatchView
from gui.recording_view import RecordingView
from gui.analysis_view import AnalysisView
from gui.settings_view import SettingsView


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("R6 Tactical Intelligence Engine")
        self.setMinimumSize(1200, 750)
        self.controller = AppController()
        self.init_ui()

    def init_ui(self):
        central_widget = QWidget()
        layout = QVBoxLayout()

        self.tabs = QTabWidget()

        # ── Views ─────────────────────────────────────────────
        self.recording_view = RecordingView(controller=self.controller)
        self.match_view     = MatchView()
        self.analysis_view  = AnalysisView(parent=self, controller=self.controller)
        self.settings_view  = SettingsView()

        self.tabs.addTab(self.recording_view, "🎙 Recording")
        self.tabs.addTab(self.match_view,     "📋 Match Input")
        self.tabs.addTab(self.analysis_view,  "📊 Analysis")
        self.tabs.addTab(self.settings_view,  "⚙ Settings")

        # ── Routing signals from RecordingView ─────────────────
        self.recording_view.navigate_to_analysis.connect(
            lambda match_id: self._go_to_analysis(match_id)
        )
        self.recording_view.navigate_to_match_input.connect(
            lambda: self.tabs.setCurrentWidget(self.match_view)
        )

        layout.addWidget(self.tabs)
        central_widget.setLayout(layout)
        self.setCentralWidget(central_widget)

    def _go_to_analysis(self, match_id: int) -> None:
        self.analysis_view.load_matches(select_match_id=match_id)
        self.tabs.setCurrentWidget(self.analysis_view)