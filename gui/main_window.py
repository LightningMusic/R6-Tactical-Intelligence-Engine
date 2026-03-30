from PySide6.QtWidgets import (
    QMainWindow,
    QWidget,
    QVBoxLayout,
    QTabWidget
)

from gui.match_view import MatchView
from gui.settings_view import SettingsView

class MainWindow(QMainWindow):
    """
    Main application window.

    Holds all primary views as tabs.
    """

    def __init__(self):
        super().__init__()

        self.setWindowTitle("R6 Tactical Intelligence Engine")
        self.setMinimumSize(1000, 700)

        self.init_ui()

    def init_ui(self):
        central_widget = QWidget()
        layout = QVBoxLayout()

        self.tabs = QTabWidget()
        self.settings_view = SettingsView()
        self.tabs.addTab(self.settings_view, "Settings")
        # --- Core Tabs ---
        self.match_view = MatchView()

        self.tabs.addTab(self.match_view, "Match Input")

        # Future tabs (placeholder)
        # self.tabs.addTab(QWidget(), "Dashboard")
        # self.tabs.addTab(QWidget(), "Analysis")
        # etc...

        layout.addWidget(self.tabs)
        central_widget.setLayout(layout)

        self.setCentralWidget(central_widget)