import sys
import json
from pathlib import Path


def _resolve_base_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).parent
    return Path(__file__).parent.parent

BASE_DIR = _resolve_base_dir()

# ── Bundle vs Data Logic ──────────────────────────────────────
if getattr(sys, "frozen", False):
    # Files inside the PyInstaller bundle
    BUNDLE_DIR = BASE_DIR / "_internal"
else:
    # Files in your dev environment
    BUNDLE_DIR = BASE_DIR

# DATA_DIR stays at the root of the USB so models aren't deleted on update
DATA_DIR = BASE_DIR / "data"

# ── Static paths (Merged & Fixed) ─────────────────────────────
DB_PATH            = DATA_DIR / "matches.db"
RECORDINGS_DIR     = DATA_DIR / "recordings"
TRANSCRIPTS_DIR    = DATA_DIR / "transcripts"
REPORTS_DIR        = DATA_DIR / "reports"
EXPORTS_DIR        = BASE_DIR / "exports"
MODEL_DIR          = DATA_DIR / "models"
MODEL_PATH         = MODEL_DIR / "model.gguf"
WHISPER_MODEL_PATH = MODEL_DIR / "whisper-base.pt"
SETTINGS_PATH      = DATA_DIR / "settings.json"

# These must use BUNDLE_DIR to find files inside _internal
SCHEMA_PATH        = BUNDLE_DIR / "database" / "schema.sql"
INTEGRATION_DIR    = BUNDLE_DIR / "integration"
R6_DISSECT_PATH    = INTEGRATION_DIR / "bin" / "r6-dissect.exe"

# OBS sits outside the R6Analyzer folder on the USB root
OBS_DIR            = BASE_DIR.parent / "OBS-Studio"
OBS_EXE_PATH       = OBS_DIR / "bin" / "64bit" / "obs64.exe"

# Ollama portable — sits next to R6Analyzer on the USB
OLLAMA_DIR     = BASE_DIR.parent / "ollama"
OLLAMA_EXE     = OLLAMA_DIR / "ollama.exe"
OLLAMA_MODELS  = BASE_DIR / "data" / "ollama_models"  # store models on USB too


# ── Settings singleton ────────────────────────────────────────

class _Settings:
    """
    Single source of truth for all user-configurable settings.
    Loaded from data/settings.json at startup.
    Saved back with settings.save().
    """

    DEFAULTS: dict = {
        "obs_profiles": [
            {
                "name": "Default",
                "host": "localhost",
                "port": 4455,
                "password": "",
                "scene_name": "R6_Comms",
            }
        ],
        "obs_active_profile": 0,
        # Legacy single-value keys kept for migration
        "obs_host":           "localhost",
        "obs_port":           4455,
        "obs_password":       "",
        "obs_scene_name":     "R6_Comms",
        "whisper_model_size": "base",
        "llm_model_filename": "model.gguf",
        "llm_gpu_layers":     0,
        "llm_n_ctx":          4096,
        "llm_n_threads":      6,
        "stability_wait":     5,
        "stability_checks":   4,
        "transcribe_auto":    True,
        "r6_replay_folder":   None,
        "twitch_channel":     "",
        "twitch_title":       "",
        "twitch_auto_start":  False,
        "twitch_auto_stop":   False,
        "discord_bot_token":  "",
        "discord_channel_ids": [],   # list of {name, id} dicts
        "discord_channel_id": "",    # legacy single value
        "ollama_model":       "llama3.2:3b",
    }

    # ── Active OBS profile helpers ────────────────────────────



    def _active_obs_profile(self) -> dict:
        profiles = self._data.get("obs_profiles") or []
        idx = int(self._data.get("obs_active_profile", 0))
        if profiles and 0 <= idx < len(profiles):
            return profiles[idx]
        # Fall back to legacy flat keys
        return {
            "host":       self._data.get("obs_host", "localhost"),
            "port":       self._data.get("obs_port", 4455),
            "password":   self._data.get("obs_password", ""),
            "scene_name": self._data.get("obs_scene_name", "R6_Comms"),
        }

    def get_obs_profiles(self) -> list[dict]:
        return list(self._data.get("obs_profiles") or [])

    def set_obs_profiles(self, profiles: list[dict], active_idx: int = 0) -> None:
        self._data["obs_profiles"]       = profiles
        self._data["obs_active_profile"] = active_idx

    def get_discord_channels(self) -> list[dict]:
        """Returns list of {name, id} dicts."""
        channels = self._data.get("discord_channel_ids") or []
        if not channels:
            # Migrate legacy single channel
            legacy = self._data.get("discord_channel_id", "")
            if legacy:
                channels = [{"name": "Main", "id": str(legacy)}]
        return channels

    def set_discord_channels(self, channels: list[dict]) -> None:
        self._data["discord_channel_ids"] = channels

    def __init__(self) -> None:
        self._data: dict = dict(self.DEFAULTS)
        self.load()

    def load(self) -> None:
        if not SETTINGS_PATH.exists():
            return
        try:
            saved = json.loads(SETTINGS_PATH.read_text(encoding="utf-8"))
            for key in self.DEFAULTS:
                if key in saved:
                    self._data[key] = saved[key]
        except Exception as e:
            print(f"[Settings] Failed to load settings.json: {e}")

    def save(self) -> None:
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        try:
            SETTINGS_PATH.write_text(
                json.dumps(self._data, indent=2), encoding="utf-8"
            )
        except Exception as e:
            print(f"[Settings] Failed to save settings.json: {e}")

    def get(self, key: str):
        return self._data.get(key, self.DEFAULTS.get(key))

    def set(self, key: str, value) -> None:
        self._data[key] = value

    def set_many(self, updates: dict) -> None:
        self._data.update(updates)

    # ── Typed properties ──────────────────────────────────────

    @property
    def OBS_HOST(self) -> str:
        return str(self._data["obs_host"])

    @property
    def OBS_PORT(self) -> int:
        return int(self._data["obs_port"])

    @property
    def OBS_PASSWORD(self) -> str:
        return str(self._data["obs_password"])

    @property
    def OBS_SCENE_NAME(self) -> str:
        return str(self._data["obs_scene_name"])

    @property
    def WHISPER_MODEL_SIZE(self) -> str:
        return str(self._data["whisper_model_size"])

    @property
    def LLM_GPU_LAYERS(self) -> int:
        return int(self._data["llm_gpu_layers"])

    @property
    def LLM_MODEL_FILENAME(self) -> str:
        return str(self._data.get("llm_model_filename", "model.gguf"))

    @property
    def LLM_N_CTX(self) -> int:
        return int(self._data["llm_n_ctx"])

    @property
    def LLM_N_THREADS(self) -> int:
        return int(self._data["llm_n_threads"])

    @property
    def STABILITY_WAIT(self) -> float:
        return float(self._data["stability_wait"])

    @property
    def STABILITY_CHECKS(self) -> int:
        return int(self._data["stability_checks"])

    @property
    def TRANSCRIBE_AUTO(self) -> bool:
        return bool(self._data["transcribe_auto"])

    @property
    def R6_REPLAY_FOLDER(self) -> Path | None:
        val = self._data.get("r6_replay_folder")
        if val:
            p = Path(val)
            return p if p.exists() else None
        return _find_replay_folder()


# ── Module-level singleton (imported everywhere) ──────────────
settings = _Settings()


# ── Replay folder auto-detection ──────────────────────────────

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
    return settings.R6_REPLAY_FOLDER


def get_llm_model_path() -> Path:
    configured = settings.LLM_MODEL_FILENAME.strip()
    if configured:
        configured_path = Path(configured)
        if configured_path.is_absolute():
            return configured_path
        candidate = MODEL_DIR / configured
        if candidate.exists():
            return candidate

    if MODEL_PATH.exists():
        return MODEL_PATH

    gguf_files = sorted(
        MODEL_DIR.glob("*.gguf"),
        key=lambda p: p.stat().st_size,
        reverse=True,
    )
    if gguf_files:
        return gguf_files[0]

    return MODEL_PATH


def get_whisper_model_path() -> Path:
    size = settings.WHISPER_MODEL_SIZE.strip().lower() or "base"
    candidates = [
        MODEL_DIR / f"{size}.pt",
        MODEL_DIR / f"whisper-{size}.pt",
    ]

    if size == "base":
        candidates.extend([
            MODEL_DIR / "base.pt",
            WHISPER_MODEL_PATH,
        ])

    for candidate in candidates:
        if candidate.exists():
            return candidate

    pt_files = sorted(
        MODEL_DIR.glob("*.pt"),
        key=lambda p: p.stat().st_size,
        reverse=True,
    )
    if pt_files:
        return pt_files[0]

    return candidates[0]


def ensure_data_dirs() -> None:
    for d in (DATA_DIR, RECORDINGS_DIR, TRANSCRIPTS_DIR,
              REPORTS_DIR, EXPORTS_DIR, MODEL_DIR):
        d.mkdir(parents=True, exist_ok=True)
