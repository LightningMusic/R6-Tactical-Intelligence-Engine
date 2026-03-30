from database.db_manager import DatabaseManager


LATEST_SCHEMA_VERSION = 1

def add_operator_gadget_options(conn):
    conn.execute("""
    CREATE TABLE IF NOT EXISTS operator_gadget_options (
        id INTEGER PRIMARY KEY,
        operator_id INTEGER,
        gadget_id INTEGER,
        max_count INTEGER,
        UNIQUE(operator_id, gadget_id),
        FOREIGN KEY(operator_id) REFERENCES operators(operator_id),
        FOREIGN KEY(gadget_id) REFERENCES gadgets(gadget_id)
    );
    """)
def run_migrations(db: DatabaseManager):
    current_version = db.get_schema_version()

    if current_version >= LATEST_SCHEMA_VERSION:
        return

    with db.get_connection() as conn:
        # --- Example migration block ---
        if current_version < 1:
            # Ensure metadata table exists
            conn.execute("""
                CREATE TABLE IF NOT EXISTS metadata (
                    key TEXT PRIMARY KEY,
                    value TEXT
                )
            """)

        conn.commit()

    db.set_schema_version(LATEST_SCHEMA_VERSION)