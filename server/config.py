import os
import sys
import json
import secrets
import hashlib
from pathlib import Path
from datetime import datetime, timezone
from typing import Optional


def _int_or_none(raw: str) -> Optional[int]:
    raw = (raw or "").strip()
    return int(raw) if raw else None


class ServerSettings:
    """
    Headless server settings.

    Token precedence, highest first:
      1. R6_SERVER_API_TOKEN_HASH env var (explicit, pre-hashed)
      2. R6_SERVER_API_TOKEN env var (explicit, plaintext)
      3. server_config.json next to the data directory (self-provisioned)

    If neither environment variable is set, the server self-provisions on
    first run: it generates a random token, saves it to server_config.json,
    and reuses that same token on every later launch. This is what makes the
    packaged R6Server.exe usable with zero manual setup — double-click it,
    read the token off the console (also re-printed on every subsequent
    launch), paste it into the client's Settings -> Remote Sync tab. Only
    falls closed (API_TOKEN_HASH = None, every request rejected) if the
    config file can't be read or written at all, e.g. a read-only location.
    """

    def __init__(self) -> None:
        self.DATA_DIR = Path(os.getenv("R6_SERVER_DATA_DIR", "./server_data")).resolve()
        self.DATABASE_PATH = Path(
            os.getenv("R6_SERVER_DATABASE_PATH", str(self.DATA_DIR / "server_matches.db"))
        ).resolve()
        self.UPLOADS_DIR = self.DATA_DIR / "uploads"
        self.WORK_DIR = self.DATA_DIR / "work"
        self.REPORTS_DIR = self.DATA_DIR / "reports"
        self.LOGS_DIR = self.DATA_DIR / "logs"
        self.CONFIG_FILE = self.DATA_DIR / "server_config.json"
        # Teammates' own mic recordings (5-minute chunks from R6Voice) and
        # the per-session audio/comms working files built from them.
        self.VOICE_DIR = self.DATA_DIR / "voice"
        self.COMMS_DIR = self.DATA_DIR / "comms"

        # While this sentinel file exists, the worker holds queued jobs
        # instead of running them. Uploads are still accepted and queued as
        # normal -- that side costs nothing, and it's what keeps clients
        # from piling up unsendable packages. It's the PROCESSING stage
        # (Whisper transcription + Ollama analysis) that saturates the
        # machine, which is unwelcome when it doubles as a gaming PC.
        # A file rather than an in-memory flag or a setting: it can be
        # flipped from outside the process (toggle_analysis.bat) with the
        # server already running, it survives restarts, and server_data\ is
        # preserved across rebuilds, so the choice sticks.
        self.ANALYSIS_PAUSE_FLAG = self.DATA_DIR / "analysis_paused.flag"

        # Transcription (Milestone 4, phase 1). Model files live under
        # server_data/ rather than next to the exe, for the same reason
        # the client keeps its model in data/models/: build_and_deploy.bat's
        # USB robocopy already excludes server_data/ from /PURGE, so a model
        # placed here once survives every later rebuild. r6-dissect, by
        # contrast, ships bundled with the app itself (like the client's
        # copy), since it's an app asset, not user data.
        self.MODELS_DIR = self.DATA_DIR / "models"
        self.WHISPER_MODEL_PATH = Path(
            os.getenv("R6_SERVER_WHISPER_MODEL_PATH", str(self.MODELS_DIR / "whisper-base.pt"))
        ).resolve()
        self.WHISPER_MODEL_SIZE = os.getenv("R6_SERVER_WHISPER_MODEL_SIZE", "base").strip()

        if getattr(sys, "frozen", False):
            _bundle_dir = Path(sys.executable).parent / "_internal"
        else:
            _bundle_dir = Path(__file__).parent.parent
        _dissect_name = "r6-dissect.exe" if sys.platform == "win32" else "r6-dissect"
        self.R6_DISSECT_PATH = Path(
            os.getenv("R6_SERVER_DISSECT_PATH", str(_bundle_dir / "integration" / "bin" / _dissect_name))
        )

        # Milestone 4, phase 2: server-side match schema + AI analysis.
        # The server reconstructs the exact same match/round/player-stat
        # schema the client uses (database/schema.sql, bundled as an app
        # asset the same way r6-dissect is) in its OWN database, entirely
        # separate from server_matches.db (which only tracks upload/job
        # bookkeeping). This is what lets database.repositories.Repository
        # and analysis.intel_engine.IntelEngine run here unmodified.
        self.MATCHES_DB_PATH = Path(
            os.getenv("R6_SERVER_MATCHES_DB_PATH", str(self.DATA_DIR / "matches.db"))
        ).resolve()
        self.MATCHES_SCHEMA_PATH = _bundle_dir / "database" / "schema.sql"

        # Portable Ollama (Milestone 4, phase 2 AI backend). Lives under
        # server_data/ for the same reason the Whisper model does — excluded
        # from the USB robocopy /PURGE, so it survives a later rebuild.
        # Drop an extracted ollama-windows-amd64.zip's contents here once;
        # IntelEngine._OllamaBackend launches it and pulls the model itself
        # on first use, exactly like the client's own (client-side, sibling-
        # folder) portable Ollama setup.
        self.OLLAMA_DIR = self.DATA_DIR / "ollama"
        self.OLLAMA_EXE = self.OLLAMA_DIR / "ollama.exe"
        self.OLLAMA_MODELS_DIR = self.OLLAMA_DIR / "models"
        # Set when Ollama runs somewhere else (the Docker deployment runs it
        # as its own container, e.g. http://ollama:11434). The backend then
        # talks to it as-is and never tries to launch an ollama.exe itself.
        self.OLLAMA_URL = os.getenv("R6_SERVER_OLLAMA_URL", "").strip().rstrip("/")
        # Chosen by an end-to-end A/B on a real 2-round match (same package
        # through this exact pipeline, all 10 players' intel):
        #   llama3.2:3b  1.5 min  -- never used kill data; generic intel
        #   qwen2.5:7b   2.7 min  -- contradicted itself on round outcomes
        #   llama3.1:8b  3.5 min  -- only one to report KDs correctly and
        #                            name the actual top fraggers
        # The server runs unattended, so ~2 extra minutes per match buys
        # noticeably better analysis. The client deliberately stays on the
        # 3B (its own settings key, ollama_model): it runs off a 64 GB USB
        # stick, and the server now does the heavy analysis anyway.
        self.OLLAMA_MODEL = os.getenv("R6_SERVER_OLLAMA_MODEL", "llama3.1:8b").strip()

        # Runtime options passed on every generate call. Benchmarked on the
        # home desktop (i5-14600K, 64 GB RAM, Radeon RX 7600 8 GB):
        #   3B  CPU-only 15.4 tok/s  vs  GPU-only 5.1 tok/s
        #   7B  CPU-only  8.9 tok/s  vs  GPU-only 4.7 tok/s
        # ROCm on that card generates ~3x slower than the CPU. End to end
        # through the real pipeline the difference mostly washes out (a
        # full 3B match analysis: 1.5 min on the old hybrid placement vs
        # 1.7 min CPU-only), so the real win is that analysis never touches
        # the GPU -- a game running at the same time keeps all of it. Set
        # R6_SERVER_OLLAMA_NUM_GPU to a layer count (or
        # leave it empty for Ollama's auto-placement) on hardware where the
        # GPU actually wins -- e.g. most NVIDIA cards.
        self.OLLAMA_OPTIONS = {
            "num_ctx": _int_or_none(os.getenv("R6_SERVER_OLLAMA_NUM_CTX", "8192")),
            "num_gpu": _int_or_none(os.getenv("R6_SERVER_OLLAMA_NUM_GPU", "0")),
            "num_thread": _int_or_none(os.getenv("R6_SERVER_OLLAMA_NUM_THREAD", "")),
        }

        # Network
        self.HOST = os.getenv("R6_SERVER_HOST", "0.0.0.0").strip()
        self.PORT = int(os.getenv("R6_SERVER_PORT", "8000"))

        # Token precedence & unambiguous check
        raw_token = os.getenv("R6_SERVER_API_TOKEN", "").strip()
        env_token_hash = os.getenv("R6_SERVER_API_TOKEN_HASH", "").strip().lower()

        self.AMBIGUOUS_TOKEN_CONFIG = bool(raw_token and env_token_hash)
        if self.AMBIGUOUS_TOKEN_CONFIG:
            print("[ServerSettings] Warning: Both R6_SERVER_API_TOKEN and R6_SERVER_API_TOKEN_HASH are set. R6_SERVER_API_TOKEN_HASH takes precedence.", flush=True)

        # API_TOKEN_PLAINTEXT is only ever populated when we actually know
        # the plaintext (env var R6_SERVER_API_TOKEN, or self-provisioned) —
        # never when only a pre-hashed token was supplied, since a hash
        # can't be reversed and shouldn't be treated as if it could be.
        self.API_TOKEN_PLAINTEXT: Optional[str] = None
        self.TOKEN_SOURCE: str = "unconfigured"

        if env_token_hash:
            self.API_TOKEN_HASH: Optional[str] = env_token_hash
            self.TOKEN_SOURCE = "environment (R6_SERVER_API_TOKEN_HASH)"
        elif raw_token:
            self.API_TOKEN_HASH = hashlib.sha256(raw_token.encode("utf-8")).hexdigest().lower()
            self.API_TOKEN_PLAINTEXT = raw_token
            self.TOKEN_SOURCE = "environment (R6_SERVER_API_TOKEN)"
        else:
            self._load_or_provision_token()

        # A second, narrower token for teammates' R6Voice recorders: it can
        # upload voice chunks and nothing else, so handing it to four people
        # doesn't hand out the key to every match and report on the server.
        self.VOICE_TOKEN_PLAINTEXT: Optional[str] = None
        self.VOICE_TOKEN_HASH: Optional[str] = None
        self._load_or_provision_voice_token()

        # Milestone 5: permanent public address (e.g. a Tailscale Funnel
        # HTTPS URL) this server is reachable at from any network, not just
        # the tailnet/LAN. Set once via setup_tailscale_funnel.bat, which
        # writes it into server_config.json alongside the API token (see
        # _load_public_url below). Purely informational to the server
        # itself — it still just binds HOST:PORT as before, same as it
        # always has; this is read back out by
        # scripts/embed_client_credentials.py at client build time so the
        # client never needs a server address typed in manually. An env
        # var override is supported for the same reason the token has
        # one: a scripted/CI deployment that never touches
        # server_config.json directly.
        self.PUBLIC_URL: str = os.getenv("R6_SERVER_PUBLIC_URL", "").strip() or self._load_public_url()

        # Limits and parameters
        self.MAX_UPLOAD_BYTES = int(os.getenv("R6_SERVER_MAX_UPLOAD_BYTES", 524288000))  # 500 MB
        self.ALLOWED_PACKAGE_VERSION = os.getenv("R6_SERVER_ALLOWED_PACKAGE_VERSION", "1.0").strip()
        self.WORKER_POLL_INTERVAL = float(os.getenv("R6_SERVER_WORKER_POLL_INTERVAL", "2.0"))

    def _load_or_provision_token(self) -> None:
        """No token supplied via environment variables — reuse (or create)
        the self-provisioned one in server_config.json."""
        try:
            self.DATA_DIR.mkdir(parents=True, exist_ok=True)

            if self.CONFIG_FILE.exists():
                data = json.loads(self.CONFIG_FILE.read_text(encoding="utf-8"))
                token = str(data.get("api_token", "")).strip()
                if token:
                    self.API_TOKEN_PLAINTEXT = token
                    self.API_TOKEN_HASH = hashlib.sha256(token.encode("utf-8")).hexdigest().lower()
                    self.TOKEN_SOURCE = "server_config.json"
                    return
                # File exists but has no usable token — fall through and
                # regenerate rather than leaving the server unconfigured.

            token = secrets.token_urlsafe(32)
            self.CONFIG_FILE.write_text(
                json.dumps(
                    {
                        "api_token": token,
                        "generated_at": datetime.now(timezone.utc).isoformat(),
                        "note": (
                            "Auto-generated on first run. This is the Bearer token "
                            "the R6Analyzer client needs in Settings -> Remote Sync "
                            "-> API Key to talk to this server. Delete this file "
                            "and restart the server to rotate it (every client "
                            "using the old token will need updating too)."
                        ),
                    },
                    indent=2,
                ),
                encoding="utf-8",
            )
            self.API_TOKEN_PLAINTEXT = token
            self.API_TOKEN_HASH = hashlib.sha256(token.encode("utf-8")).hexdigest().lower()
            self.TOKEN_SOURCE = "server_config.json (newly generated)"

        except Exception as e:
            print(
                f"[ServerSettings] Warning: could not read/write {self.CONFIG_FILE}: {e}. "
                f"No API token is configured — every request will be rejected with "
                f"HTTP 500 until R6_SERVER_API_TOKEN is set or this location becomes writable.",
                flush=True,
            )
            self.API_TOKEN_HASH = None
            self.TOKEN_SOURCE = "unconfigured (config file error)"

    def _load_or_provision_voice_token(self) -> None:
        env = os.getenv("R6_SERVER_VOICE_TOKEN", "").strip()
        token = env
        try:
            data = {}
            if self.CONFIG_FILE.exists():
                data = json.loads(self.CONFIG_FILE.read_text(encoding="utf-8"))
            if not token:
                token = str(data.get("voice_token", "")).strip()
            # Only a server that already manages its own server_config.json
            # gets one written for it; one configured by environment
            # variables never has the file touched (R6_SERVER_VOICE_TOKEN
            # sets its voice key instead).
            if not token and self.TOKEN_SOURCE.startswith("server_config.json"):
                token = secrets.token_urlsafe(24)
                data["voice_token"] = token
                self.DATA_DIR.mkdir(parents=True, exist_ok=True)
                self.CONFIG_FILE.write_text(json.dumps(data, indent=2), encoding="utf-8")
        except Exception as e:
            print(f"[ServerSettings] Warning: no voice token ({e}); voice uploads will need the main API token.",
                  flush=True)
        if not token:
            return
        self.VOICE_TOKEN_PLAINTEXT = token
        self.VOICE_TOKEN_HASH = hashlib.sha256(token.encode("utf-8")).hexdigest().lower()

    def _load_public_url(self) -> str:
        """Reads the optional `public_url` key from server_config.json, if
        that file exists — set once by setup_tailscale_funnel.bat, never by
        this class. Independent of where the API token came from (even a
        server started with R6_SERVER_API_TOKEN set will still pick up a
        public_url someone saved into the file previously); returns "" if
        the file or key is missing, or can't be read for any reason, since
        an unset public address just means the client build won't have one
        baked in — not a fatal error."""
        try:
            if self.CONFIG_FILE.exists():
                data = json.loads(self.CONFIG_FILE.read_text(encoding="utf-8"))
                return str(data.get("public_url", "")).strip()
        except Exception:
            pass
        return ""

    def save_public_url(self, url: str) -> None:
        """Writes/updates the `public_url` key in server_config.json without
        disturbing whatever else is already in there (api_token,
        generated_at, ...). Used by setup_tailscale_funnel.bat's helper
        script, not by the running server itself. Creates the file (with
        just this one key) if it doesn't exist yet — e.g. someone ran the
        Funnel setup before ever starting the server once — though the API
        token still won't exist until the server actually runs and
        self-provisions it."""
        self.DATA_DIR.mkdir(parents=True, exist_ok=True)
        data = {}
        if self.CONFIG_FILE.exists():
            try:
                data = json.loads(self.CONFIG_FILE.read_text(encoding="utf-8"))
            except Exception:
                data = {}
        data["public_url"] = url.strip().rstrip("/")
        self.CONFIG_FILE.write_text(json.dumps(data, indent=2), encoding="utf-8")
        self.PUBLIC_URL = data["public_url"]

    def analysis_paused(self) -> bool:
        """Checked fresh on every worker poll, never cached, so toggling the
        flag takes effect without restarting the server."""
        return self.ANALYSIS_PAUSE_FLAG.exists()

    def ensure_directories(self) -> None:
        for d in (
            self.DATA_DIR, self.UPLOADS_DIR, self.WORK_DIR, self.REPORTS_DIR,
            self.LOGS_DIR, self.MODELS_DIR, self.OLLAMA_DIR, self.OLLAMA_MODELS_DIR,
            self.VOICE_DIR, self.COMMS_DIR,
        ):
            d.mkdir(parents=True, exist_ok=True)


server_settings = ServerSettings()
