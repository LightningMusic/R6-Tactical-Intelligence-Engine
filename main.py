import sys

# Configured before anything else in the app gets a chance to print --
# see app/logging_setup.py for why this matters. The client exe is built
# with console=False (no console window at all), so without this, every
# print()-based error message throughout the codebase has nowhere to go.
from app.logging_setup import configure_logging
from app.config import LOGS_DIR, ensure_data_dirs, settings, BUNDLE_DIR
configure_logging(LOGS_DIR, "r6analyzer.log")

from PySide6.QtWidgets import QApplication
from PySide6.QtGui import QFont, QIcon

from gui.main_window import MainWindow
from database.db_manager import DatabaseManager
from database.migrations import run_migrations
from database.seed_operators import seed_database


def initialize_system() -> None:
    ensure_data_dirs()
    # settings singleton loads from disk automatically on import

    print("🔧 Initializing database...")
    db = DatabaseManager()

    print("📦 Running migrations...")
    run_migrations(db)

    print("🌱 Seeding operators & gadgets...")
    seed_database(db)

    # Pick up new operators, gadgets and loadout changes from Ubisoft's
    # official operator pages. Background thread so a slow or offline
    # network never delays startup; a no-op if it ran in the last day.
    from integration.ubisoft_catalog import start_background_sync
    start_background_sync(db)

    print("✅ System initialization complete.")


import atexit

def main() -> None:
    initialize_system()
    app = QApplication(sys.argv)
    app.setFont(QFont("Segoe UI", 10))

    # App/taskbar icon (see R6Analyzer.spec for the matching .exe icon).
    # BUNDLE_DIR resolves to _internal/ in the frozen build and the repo
    # root in dev, matching the "resources/icons" datas entry the spec
    # bundles the file under either way.
    icon_path = BUNDLE_DIR / "resources" / "icons" / "icon.ico"
    if icon_path.exists():
        app.setWindowIcon(QIcon(str(icon_path)))

    window = MainWindow()
    window.show()

    # Shut down Ollama server cleanly when app exits
    from analysis.intel_engine import IntelEngine
    _intel = IntelEngine()
    atexit.register(_intel.shutdown)

    sys.exit(app.exec())


if __name__ == "__main__":
    main()