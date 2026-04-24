import sys
from PySide6.QtWidgets import QApplication
from PySide6.QtGui import QFont

from gui.main_window import MainWindow
from database.db_manager import DatabaseManager
from database.migrations import run_migrations
from database.seed_operators import seed_database
from app.config import ensure_data_dirs, settings


def initialize_system() -> None:
    ensure_data_dirs()
    # settings singleton loads from disk automatically on import

    print("🔧 Initializing database...")
    db = DatabaseManager()

    print("📦 Running migrations...")
    run_migrations(db)

    print("🌱 Seeding operators & gadgets...")
    seed_database(db)

    print("✅ System initialization complete.")


import atexit

def main() -> None:
    initialize_system()
    app = QApplication(sys.argv)
    app.setFont(QFont("Segoe UI", 10))
    window = MainWindow()
    window.show()

    # Shut down Ollama server cleanly when app exits
    from analysis.intel_engine import IntelEngine
    _intel = IntelEngine()
    atexit.register(_intel.shutdown)

    sys.exit(app.exec())


if __name__ == "__main__":
    main()