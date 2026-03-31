import sys
from PySide6.QtWidgets import QApplication
from PySide6.QtGui import QFont
from PySide6.QtWidgets import QApplication

from gui.main_window import MainWindow

from database.db_manager import DatabaseManager
from database.migrations import run_migrations
from database.seed_operators import seed_database


def initialize_system():
    """
    Bootstraps the entire system:
    - Database
    - Migrations
    - Seed data
    """

    print("🔧 Initializing database...")

    db = DatabaseManager()

    print("📦 Running migrations...")
    run_migrations(db)

    print("🌱 Seeding operators & gadgets...")
    seed_database(db)

    print("✅ System initialization complete.")



def main():
    # --- Initialize backend ---
    initialize_system()

    # --- UI Fix ---

    # --- Start GUI ---


    app = QApplication([])
    # Debug: Print global font size before fix
    from PySide6.QtGui import QFontInfo

    f = app.font()
    app.setFont(QFont("Segoe UI", 10))
    window = MainWindow()
    window.show()

    sys.exit(app.exec())


if __name__ == "__main__":
    main()