import sys
from pathlib import Path


def _resolve_base_dir() -> Path:
    """
    Resolves the root directory of the application.
    - Frozen (.exe): directory containing the executable (USB root).
    - Dev: two levels up from this file (R6Analyzer/).
    """
    if getattr(sys, "frozen", False):
        return Path(sys.executable).parent
    else:
        return Path(__file__).parent.parent


BASE_DIR = _resolve_base_dir()

# ── Data Paths ────────────────────────────────────────────────
DATA_DIR        = BASE_DIR / "data"
DB_PATH         = DATA_DIR / "matches.db"
RECORDINGS_DIR  = DATA_DIR / "recordings"
TRANSCRIPTS_DIR = DATA_DIR / "transcripts"
REPORTS_DIR     = DATA_DIR / "reports"

# ── Source Paths ──────────────────────────────────────────────
DATABASE_DIR    = BASE_DIR / "database"
SCHEMA_PATH     = DATABASE_DIR / "schema.sql"

# ── Integration Paths ─────────────────────────────────────────
INTEGRATION_DIR     = BASE_DIR / "integration"
R6_DISSECT_PATH     = INTEGRATION_DIR / "bin" / "r6-dissect.exe"

# ── Exports ───────────────────────────────────────────────────
EXPORTS_DIR     = BASE_DIR / "exports"

# ── Replay Detection Config ───────────────────────────────────────
R6_REPLAY_FOLDER = "C:/Program Files (x86)/Steam/steamapps/common/Tom Clancy's Rainbow Six Siege/MatchReplay"


def ensure_data_dirs() -> None:
    """
    Creates all required runtime directories if they don't exist.
    Call once at startup from main.py.
    """
    for directory in (DATA_DIR, RECORDINGS_DIR, TRANSCRIPTS_DIR, REPORTS_DIR, EXPORTS_DIR):
        directory.mkdir(parents=True, exist_ok=True)