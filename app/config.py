import sys
import json
from pathlib import Path


def _resolve_base_dir() -> Path:
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
SETTINGS_PATH   = DATA_DIR / "settings.json"   # ← persistent settings file

# ── Source Paths ──────────────────────────────────────────────
DATABASE_DIR    = BASE_DIR / "database"
SCHEMA_PATH     = DATABASE_DIR / "schema.sql"

# ── Integration Paths ─────────────────────────────────────────
INTEGRATION_DIR = BASE_DIR / "integration"
R6_DISSECT_PATH = INTEGRATION_DIR / "bin" / "r6-dissect.exe"

# ── Exports ───────────────────────────────────────────────────
EXPORTS_DIR     = BASE_DIR / "exports"

# ── AI Model Paths ────────────────────────────────────────────
MODEL_DIR       = DATA_DIR / "models"
MODEL_PATH      = MODEL_DIR / "model.gguf"
WHISPER_MODEL_PATH = DATA_DIR / "models" / "whisper-base.pt"

# ── Runtime-mutable settings (loaded from settings.json) ──────
OBS_HOST        = "localhost"
OBS_PORT        = 4455
OBS_PASSWORD    = ""
OBS_SCENE_NAME  = "R6_Intelligence"

WHISPER_MODEL_SIZE = "base"
LLM_GPU_LAYERS  = 0
LLM_N_CTX       = 4096
LLM_N_THREADS   = 6

STABILITY_WAIT   = 5
STABILITY_CHECKS = 4
TRANSCRIBE_AUTO  = True
R6_REPLAY_FOLDER_OVERRIDE: str | None = None   # set from settings UI

# ── Settings I/O ──────────────────────────────────────────────

def load_settings() -> None:
    """
    Reads settings.json and applies values to this module's globals.
    Safe to call even if the file doesn't exist yet.
    """
    import app.config as cfg

    if not SETTINGS_PATH.exists():
        return

    try:
        data = json.loads(SETTINGS_PATH.read_text(encoding="utf-8"))
    except Exception as e:
        print(f"[Config] Failed to load settings.json: {e}")
        return

    cfg.OBS_HOST        = data.get("obs_host",        cfg.OBS_HOST)
    cfg.OBS_PORT        = data.get("obs_port",        cfg.OBS_PORT)
    cfg.OBS_PASSWORD    = data.get("obs_password",    cfg.OBS_PASSWORD)
    cfg.OBS_SCENE_NAME  = data.get("obs_scene_name",  cfg.OBS_SCENE_NAME)

    cfg.WHISPER_MODEL_SIZE = data.get("whisper_model_size", cfg.WHISPER_MODEL_SIZE)
    cfg.LLM_GPU_LAYERS  = data.get("llm_gpu_layers",  cfg.LLM_GPU_LAYERS)
    cfg.LLM_N_CTX       = data.get("llm_n_ctx",       cfg.LLM_N_CTX)
    cfg.LLM_N_THREADS   = data.get("llm_n_threads",   cfg.LLM_N_THREADS)

    cfg.STABILITY_WAIT   = data.get("stability_wait",   cfg.STABILITY_WAIT)
    cfg.STABILITY_CHECKS = data.get("stability_checks", cfg.STABILITY_CHECKS)
    cfg.TRANSCRIBE_AUTO  = data.get("transcribe_auto",  cfg.TRANSCRIBE_AUTO)
    cfg.R6_REPLAY_FOLDER_OVERRIDE = data.get("r6_replay_folder", None)


def save_settings() -> None:
    """
    Writes all runtime-mutable settings to settings.json.
    """
    import app.config as cfg

    DATA_DIR.mkdir(parents=True, exist_ok=True)

    data = {
        "obs_host":          cfg.OBS_HOST,
        "obs_port":          cfg.OBS_PORT,
        "obs_password":      cfg.OBS_PASSWORD,
        "obs_scene_name":    cfg.OBS_SCENE_NAME,
        "whisper_model_size": cfg.WHISPER_MODEL_SIZE,
        "llm_gpu_layers":    cfg.LLM_GPU_LAYERS,
        "llm_n_ctx":         cfg.LLM_N_CTX,
        "llm_n_threads":     cfg.LLM_N_THREADS,
        "stability_wait":    cfg.STABILITY_WAIT,
        "stability_checks":  cfg.STABILITY_CHECKS,
        "transcribe_auto":   cfg.TRANSCRIBE_AUTO,
        "r6_replay_folder":  cfg.R6_REPLAY_FOLDER_OVERRIDE,
    }

    SETTINGS_PATH.write_text(
        json.dumps(data, indent=2), encoding="utf-8"
    )


# ── Replay folder resolution ──────────────────────────────────

def _find_replay_folder() -> Path | None:
    default = Path("C:/Program Files (x86)/Steam/steamapps/common/"
                   "Tom Clancy's Rainbow Six Siege/MatchReplay")
    if default.exists():
        return default

    suffix = ("SteamLibrary/steamapps/common/"
              "Tom Clancy's Rainbow Six Siege/MatchReplay")
    for drive in "DEFGHIJKLMNOPQRSTUVWXYZ":
        p = Path(f"{drive}:/{suffix}")
        if p.exists():
            return p
    return None


def get_replay_folder() -> Path | None:
    """Returns the user-overridden path if set, otherwise auto-detects."""
    import app.config as cfg
    if cfg.R6_REPLAY_FOLDER_OVERRIDE:
        p = Path(cfg.R6_REPLAY_FOLDER_OVERRIDE)
        if p.exists():
            return p
    return _find_replay_folder()


def ensure_data_dirs() -> None:
    for d in (DATA_DIR, RECORDINGS_DIR, TRANSCRIPTS_DIR, REPORTS_DIR, EXPORTS_DIR, MODEL_DIR):
        d.mkdir(parents=True, exist_ok=True)