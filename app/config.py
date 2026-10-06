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

# Under DATA_DIR (not BASE_DIR) for the same reason as everything else here:
# it lives on the USB, excluded from the deploy sync's /PURGE right along
# with settings.json and matches.db, so it survives updates and never gets
# deleted out from under a running app. See app/logging_setup.py -- this is
# where every print()/error the app produces actually ends up, since the
# client exe is built with console=False (no console window at all) and
# would otherwise have nowhere for that output to go.
LOGS_DIR           = DATA_DIR / "logs"

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


def _embedded_server_url() -> str:
    """Lazily reads app/server_credentials.py's build-time-embedded server
    URL. Lazy (not a top-level import) so a from-source dev checkout works
    identically whether or not that generated file happens to exist, and so
    nothing here ever hard-crashes app startup over a build-tooling detail."""
    try:
        from app.server_credentials import EMBEDDED_SERVER_URL
        return str(EMBEDDED_SERVER_URL or "").strip()
    except ImportError:
        return ""


def _embedded_api_key() -> str:
    """Lazily reads app/server_credentials.py's build-time-embedded API key.
    See _embedded_server_url for why this is a lazy import."""
    try:
        from app.server_credentials import EMBEDDED_API_KEY
        return str(EMBEDDED_API_KEY or "").strip()
    except ImportError:
        return ""


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
        # Discord per-user audio capture (optional feature)
        "discord_bot_token":   "",
        "discord_channel_id":  "",
        "discord_channel_ids": [],
        # Client/server distributed-analysis settings (Milestone 1/2).
        # Defaults to "automatic": once a server_url is configured in
        # Settings, every session is uploaded automatically after local
        # analysis finishes (local analysis always runs regardless — this
        # only controls the optional upload leg). With no server_url set,
        # "automatic" behaves exactly like "local" (SyncCoordinator never
        # attempts an upload without a configured server), so a fresh USB
        # stick with no server yet is unaffected. Still fully overridable
        # in Settings -> Remote Sync for anyone who wants local-only.
        "analysis_mode":             "automatic",
        "server_url":                "",
        "api_key":                   "",
        "upload_replays":            True,
        "upload_voice":              False,
        "upload_automatically":      True,
        "upload_later_when_offline": True,
        "fallback_to_local_analysis": True,
        "request_timeout_seconds":   30,
        "max_upload_retries":        5,
        "client_name":               "USB_Client",
    }

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

    # ── OBS multi-profile helpers ──────────────────────────────
    # (gui/settings_view.py manages a list of named OBS connection
    # profiles — one per PC the USB stick is used on — on top of the
    # single "obs_host"/"obs_port"/... legacy keys kept for migration.)

    def get_obs_profiles(self) -> list:
        profiles = self._data.get("obs_profiles")
        if not isinstance(profiles, list) or not profiles:
            # Fall back to a fresh copy of the single default profile
            # rather than a shared mutable reference to DEFAULTS.
            return [dict(p) for p in self.DEFAULTS["obs_profiles"]]
        return profiles

    def set_obs_profiles(self, profiles: list, active_idx: int = 0) -> None:
        """Mutates in-memory settings only — callers persist with .save()
        explicitly, matching the existing set()/set_many() convention."""
        self._data["obs_profiles"] = list(profiles)
        self._data["obs_active_profile"] = int(active_idx)

    # ── Discord channel helpers ────────────────────────────────

    def get_discord_channels(self) -> list:
        channels = self._data.get("discord_channel_ids")
        return channels if isinstance(channels, list) else []

    # ── Typed properties ──────────────────────────────────────

    @property
    def OBS_HOST(self) -> str:
        return str(self._data.get("obs_host", "localhost"))

    @property
    def OBS_PORT(self) -> int:
        return int(self._data.get("obs_port", 4455))

    @property
    def OBS_PASSWORD(self) -> str:
        return str(self._data.get("obs_password", ""))

    @property
    def OBS_SCENE_NAME(self) -> str:
        return str(self._data.get("obs_scene_name", "R6_Intelligence"))

    @property
    def WHISPER_MODEL_SIZE(self) -> str:
        return str(self._data.get("whisper_model_size", "base"))

    @property
    def LLM_GPU_LAYERS(self) -> int:
        return int(self._data.get("llm_gpu_layers", 0))

    @property
    def LLM_MODEL_FILENAME(self) -> str:
        return str(self._data.get("llm_model_filename", "model.gguf"))

    @property
    def LLM_N_CTX(self) -> int:
        return int(self._data.get("llm_n_ctx", 4096))

    @property
    def LLM_N_THREADS(self) -> int:
        return int(self._data.get("llm_n_threads", 6))

    @property
    def STABILITY_WAIT(self) -> float:
        return float(self._data.get("stability_wait", 5))

    @property
    def STABILITY_CHECKS(self) -> int:
        return int(self._data.get("stability_checks", 4))

    @property
    def TRANSCRIBE_AUTO(self) -> bool:
        return bool(self._data.get("transcribe_auto", True))

    @property
    def ANALYSIS_MODE(self) -> str:
        mode = str(self._data.get("analysis_mode", "local")).lower().strip()
        return mode if mode in ("local", "remote", "automatic") else "local"

    @property
    def SERVER_URL(self) -> str:
        manual = str(self._data.get("server_url", "")).strip().rstrip("/")
        if manual:
            return manual
        # Milestone 5: fall back to whatever build_and_deploy.bat baked into
        # app/server_credentials.py at build time (typically a Tailscale
        # Funnel HTTPS address), so a client built against a configured
        # server needs zero manual setup and works from any network. A
        # manually-entered Settings value above always wins, e.g. to point
        # a build at a different/local server for testing.
        return _embedded_server_url().rstrip("/")

    @property
    def API_KEY(self) -> str:
        manual = str(self._data.get("api_key", "")).strip()
        if manual:
            return manual
        return _embedded_api_key()

    def api_key_candidates(self) -> list[str]:
        """Every key worth trying against the server, best guess first: the one typed into
        Settings, then the one built into this exe. They differ after a key rotation, when the
        saved one goes stale and (being preferred) would otherwise lock the app out of its own
        server (2026-10-05: a whole practice night of uploads refused)."""
        manual = str(self._data.get("api_key", "")).strip()
        embedded = _embedded_api_key()
        return [k for i, k in enumerate((manual, embedded)) if k and k not in (manual, embedded)[:i]]

    def forget_manual_api_key(self) -> None:
        """Drops a saved key the server has rejected, so the built-in one is used from now on."""
        if str(self._data.get("api_key", "")).strip():
            self._data["api_key"] = ""
            self.save()

    @property
    def UPLOAD_REPLAYS(self) -> bool:
        return bool(self._data.get("upload_replays", True))

    @property
    def UPLOAD_VOICE(self) -> bool:
        return bool(self._data.get("upload_voice", False))

    @property
    def UPLOAD_AUTOMATICALLY(self) -> bool:
        return bool(self._data.get("upload_automatically", True))

    @property
    def UPLOAD_LATER_WHEN_OFFLINE(self) -> bool:
        return bool(self._data.get("upload_later_when_offline", True))

    @property
    def FALLBACK_TO_LOCAL_ANALYSIS(self) -> bool:
        return bool(self._data.get("fallback_to_local_analysis", True))

    @property
    def REQUEST_TIMEOUT_SECONDS(self) -> int:
        return max(1, int(self._data.get("request_timeout_seconds", 30)))

    @property
    def MAX_UPLOAD_RETRIES(self) -> int:
        return max(0, int(self._data.get("max_upload_retries", 5)))

    @property
    def CLIENT_NAME(self) -> str:
        return str(self._data.get("client_name", "USB_Client")).strip()

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
              REPORTS_DIR, EXPORTS_DIR, MODEL_DIR, LOGS_DIR):
        d.mkdir(parents=True, exist_ok=True)
