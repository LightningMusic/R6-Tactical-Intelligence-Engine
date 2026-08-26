import os
import hashlib
from pathlib import Path
from typing import Optional


class ServerSettings:
    """
    Headless server settings loaded strictly from environment variables.
    NO default API tokens in source code. Fails closed if token unconfigured.
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

        # Token precedence & unambiguous check
        raw_token = os.getenv("R6_SERVER_API_TOKEN", "").strip()
        env_token_hash = os.getenv("R6_SERVER_API_TOKEN_HASH", "").strip().lower()

        self.AMBIGUOUS_TOKEN_CONFIG = bool(raw_token and env_token_hash)
        if self.AMBIGUOUS_TOKEN_CONFIG:
            print("[ServerSettings] Warning: Both R6_SERVER_API_TOKEN and R6_SERVER_API_TOKEN_HASH are set. R6_SERVER_API_TOKEN_HASH takes precedence.")

        if env_token_hash:
            self.API_TOKEN_HASH: Optional[str] = env_token_hash
        elif raw_token:
            self.API_TOKEN_HASH = hashlib.sha256(raw_token.encode("utf-8")).hexdigest().lower()
        else:
            self.API_TOKEN_HASH = None  # Unconfigured — fails closed!

        # Limits and parameters
        self.MAX_UPLOAD_BYTES = int(os.getenv("R6_SERVER_MAX_UPLOAD_BYTES", 524288000))  # 500 MB
        self.ALLOWED_PACKAGE_VERSION = os.getenv("R6_SERVER_ALLOWED_PACKAGE_VERSION", "1.0").strip()
        self.WORKER_POLL_INTERVAL = float(os.getenv("R6_SERVER_WORKER_POLL_INTERVAL", "2.0"))

    def ensure_directories(self) -> None:
        for d in (self.DATA_DIR, self.UPLOADS_DIR, self.WORK_DIR, self.REPORTS_DIR, self.LOGS_DIR):
            d.mkdir(parents=True, exist_ok=True)


server_settings = ServerSettings()
