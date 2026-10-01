from PySide6.QtWidgets import QMainWindow, QWidget, QVBoxLayout, QTabWidget, QApplication

from app.app_controller import AppController
from gui.dashboard_view import DashboardView
from gui.match_view import MatchView
from gui.recording_view import RecordingView
from gui.analysis_view import AnalysisView
from gui.settings_view import SettingsView
from models.import_result import ImportResult
from gui.export_view import ExportView

# Preferred window size on a normal desktop display. Every view already
# scrolls internally (QScrollArea), so this is a comfortable default, not
# a hard requirement.
_PREFERRED_SIZE = (1200, 750)

# Real floor for usability — below this, tabs/buttons start overlapping.
# Deliberately lower than _PREFERRED_SIZE: this app is built for
# "no admin rights, USB-portable" use on whatever computer is at hand
# (school/lab machines included), and those commonly run 1366x768 or
# smaller with a taskbar eating into the usable height.
_MINIMUM_SIZE = (1024, 600)


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("R6 Tactical Intelligence Engine")
        self.setMinimumSize(*_MINIMUM_SIZE)
        self.controller = AppController()
        self.init_ui()
        self._fit_to_screen()

    def _fit_to_screen(self) -> None:
        """Size and place the window so it never opens larger than the
        screen actually has room for.

        A window taller than the available desktop (screen minus taskbar)
        doesn't get shrunk by Windows — it gets shoved partly off-screen,
        which reads as the whole UI being "cut off" at the top and bottom
        even though nothing inside is actually broken. Clamping the
        initial size (and centering within the *available* area, not the
        raw screen) keeps the window fully on-screen on smaller displays;
        the user can still resize it larger by hand if their monitor
        supports it.
        """
        screen = QApplication.primaryScreen()
        if screen is None:
            self.resize(*_PREFERRED_SIZE)
            return

        avail = screen.availableGeometry()
        target_w = max(self.minimumWidth(), min(_PREFERRED_SIZE[0], avail.width()))
        target_h = max(self.minimumHeight(), min(_PREFERRED_SIZE[1], avail.height()))
        self.resize(target_w, target_h)

        frame = self.frameGeometry()
        frame.moveCenter(avail.center())
        self.move(frame.topLeft())

    def init_ui(self):
        central_widget = QWidget()
        layout = QVBoxLayout()

        self.tabs = QTabWidget()

        # ── Views ─────────────────────────────────────────────
        self.dashboard_view = DashboardView()
        self.recording_view = RecordingView(controller=self.controller)
        self.match_view     = MatchView()
        self.analysis_view  = AnalysisView(parent=self, controller=self.controller)
        self.settings_view  = SettingsView()
        self.export_view    = ExportView(parent=self, controller=self.controller)

        self.tabs.addTab(self.dashboard_view, "🏠 Dashboard")
        self.tabs.addTab(self.recording_view, "🎙 Recording")
        self.tabs.addTab(self.match_view,     "📋 Match Input")
        self.tabs.addTab(self.analysis_view,  "📊 Analysis")
        self.tabs.addTab(self.settings_view,  "⚙ Settings")
        self.tabs.addTab(self.export_view,    "📦 Export")

        # ── Routing signals from RecordingView ─────────────────
        self.recording_view.navigate_to_analysis.connect(
            lambda match_id: self._go_to_analysis(match_id)
        )
        self.recording_view.navigate_to_match_input.connect(
            lambda: self.tabs.setCurrentWidget(self.match_view)
        )
        self.recording_view.navigate_to_match_input_partial.connect(
            lambda result: self._go_to_match_partial(result)
        )

        layout.addWidget(self.tabs)
        central_widget.setLayout(layout)
        self.setCentralWidget(central_widget)

    def _go_to_match_partial(self, result: ImportResult) -> None:
        self.tabs.setCurrentWidget(self.match_view)
        self.match_view.prefill_from_import(result)

    def _go_to_analysis(self, match_id: int) -> None:
        self.analysis_view.load_matches(select_match_id=match_id)
        self.tabs.setCurrentWidget(self.analysis_view)