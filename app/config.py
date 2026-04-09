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

# ── OBS Integration ───────────────────────────────────────────
OBS_HOST       = "localhost"
OBS_PORT       = 4455
OBS_PASSWORD   = "gr17aGAe8WkxZO6i"   # move to a local secrets file later
OBS_SCENE_NAME = "R6_Intelligence"     # must match your OBS scene name exactly


# ── Exports ───────────────────────────────────────────────────
EXPORTS_DIR     = BASE_DIR / "exports"

# ── AI Model Paths ────────────────────────────────────────────
MODEL_DIR  = DATA_DIR / "models"
MODEL_PATH = MODEL_DIR / "model.gguf"

# ── Whisper Model ─────────────────────────────────────────────
WHISPER_MODEL_PATH = DATA_DIR / "models" / "whisper-base.pt"
WHISPER_MODEL_SIZE = "base"

# ── LLM Runtime Settings (overridable from Settings UI) ──────
LLM_GPU_LAYERS = 0
LLM_N_CTX      = 4096
LLM_N_THREADS  = 6

# ── Replay Detection Logic ───────────────────────────────────────────

def _find_replay_folder() -> Path | None:
    """
    Attempts to auto-locate the R6 MatchReplay folder.
    1. Checks the default Steam path.
    2. Checks common Steam library locations on other drives.
    """
    # Default Steam path
    default_path = Path("C:/Program Files (x86)/Steam/steamapps/common/Tom Clancy's Rainbow Six Siege/MatchReplay")
    if default_path.exists():
        return default_path

    # Secondary Search: Look for common SteamLibrary folders on D:, E:, F:
    # We look for the specific folder structure of R6
    r6_suffix = "SteamLibrary/steamapps/common/Tom Clancy's Rainbow Six Siege/MatchReplay"
    for drive in "DEFGHIJKLMNOPQRSTUVWXYZ":
        drive_path = Path(f"{drive}:/{r6_suffix}")
        if drive_path.exists():
            return drive_path

    return None

# Auto-detected path (defaults to None if not found)
R6_REPLAY_FOLDER = _find_replay_folder()

def ensure_data_dirs() -> None:
    """
    Creates all required runtime directories if they don't exist.
    Call once at startup from main.py.
    """
    for directory in (DATA_DIR, RECORDINGS_DIR, TRANSCRIPTS_DIR, REPORTS_DIR, EXPORTS_DIR):
        directory.mkdir(parents=True, exist_ok=True)