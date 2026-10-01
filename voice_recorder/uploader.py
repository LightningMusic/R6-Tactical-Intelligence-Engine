"""
Sends finished chunks to the team's R6Analyzer server, and keeps trying.

A chunk is written to disk with a small .json sidecar before anything is
sent, and both are deleted only after the server confirms it has them -- so
a dropped connection, a closed laptop or quitting mid-upload never loses
audio; whatever is still waiting goes up next time the app runs.
"""
from __future__ import annotations

import json
import threading
import time
from pathlib import Path
from typing import Callable, Optional

import requests

SAMPLE_RATE = 16000     # what both R6Voice and R6Companion upload


class Uploader:
    def __init__(self, pending_dir: Path, get_config: Callable[[], dict],
                 on_change: Optional[Callable[[], None]] = None) -> None:
        self.pending_dir = pending_dir
        self.get_config = get_config
        self.on_change = on_change or (lambda: None)
        self.sent = 0
        self.last_error = ""
        self.connected: Optional[bool] = None
        self.clock_offset: Optional[float] = None    # server clock - this PC's clock
        self._wake = threading.Event()
        self._stop = False
        pending_dir.mkdir(parents=True, exist_ok=True)
        threading.Thread(target=self._loop, daemon=True, name="R6VoiceUpload").start()

    # ── queue ───────────────────────────────────────────────────────

    def enqueue(self, chunk, username: str) -> None:
        """A recorder.Chunk (R6Voice)."""
        self.enqueue_file(chunk.path, recording_id=chunk.recording_id, chunk_index=chunk.index,
                          username=username, start_epoch=chunk.start_epoch,
                          duration_sec=chunk.duration_sec, is_final=chunk.is_final)

    def enqueue_file(self, path: Path, *, recording_id: str, chunk_index: int, username: str,
                     start_epoch: float, duration_sec: float, is_final: bool) -> None:
        """Any finished audio chunk sitting in pending_dir (R6Companion)."""
        meta = {
            "recording_id": recording_id, "chunk_index": chunk_index,
            "username": username, "start_epoch": start_epoch,
            "duration_sec": duration_sec, "sample_rate": SAMPLE_RATE,
            "is_final": is_final, "file": path.name,
        }
        path.with_suffix(".json").write_text(json.dumps(meta), encoding="utf-8")
        self._wake.set()
        self.on_change()

    def waiting(self) -> int:
        return len(list(self.pending_dir.glob("*.json")))

    def stop(self) -> None:
        self._stop = True
        self._wake.set()

    # ── network ─────────────────────────────────────────────────────

    def _headers(self, cfg: dict) -> dict:
        return {"Authorization": f"Bearer {cfg.get('voice_token', '')}"}

    def ping(self) -> bool:
        cfg = self.get_config()
        if not cfg.get("server_url"):
            self.connected, self.last_error = False, "No server address set."
            return False
        try:
            t0 = time.time()
            r = requests.get(f"{cfg['server_url']}/api/v1/voice/ping", headers=self._headers(cfg), timeout=10)
            t1 = time.time()
            if r.status_code == 401:
                self.connected, self.last_error = False, "The server didn't accept this app's key."
                return False
            r.raise_for_status()
            self.clock_offset = float(r.json()["server_time"]) - (t0 + t1) / 2
            self.connected, self.last_error = True, ""
            return True
        except Exception as e:
            self.connected, self.last_error = False, f"Can't reach the server ({type(e).__name__})."
            return False
        finally:
            self.on_change()

    def _send(self, meta_path: Path, cfg: dict) -> bool:
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
        audio = meta_path.with_name(meta["file"])
        if not audio.exists():
            meta_path.unlink(missing_ok=True)
            return True
        with audio.open("rb") as fh:
            r = requests.post(
                f"{cfg['server_url']}/api/v1/voice/chunks",
                headers=self._headers(cfg),
                files={"file": (audio.name, fh, "audio/ogg")},
                data={k: str(meta[k]) for k in ("recording_id", "chunk_index", "username", "start_epoch",
                                                  "duration_sec", "sample_rate", "is_final")},
                timeout=(10, 120),
            )
        if r.status_code in (400, 413):
            # The server will never take this one (bad name, corrupt file):
            # keep it aside rather than retrying forever.
            bad = self.pending_dir / "rejected"
            bad.mkdir(exist_ok=True)
            audio.replace(bad / audio.name)
            meta_path.replace(bad / meta_path.name)
            self.last_error = f"Server rejected a chunk: {r.text[:120]}"
            return True
        r.raise_for_status()
        audio.unlink(missing_ok=True)
        meta_path.unlink(missing_ok=True)
        self.sent += 1
        return True

    def _loop(self) -> None:
        backoff = 5.0
        while not self._stop:
            items = sorted(self.pending_dir.glob("*.json"))
            if not items:
                self._wake.wait(timeout=60)
                self._wake.clear()
                continue
            cfg = self.get_config()
            try:
                self._send(items[0], cfg)
                self.connected, backoff = True, 5.0
                if not self.last_error.startswith("Server rejected"):
                    self.last_error = ""
            except Exception as e:
                self.connected = False
                self.last_error = f"Upload will retry ({type(e).__name__})."
                self.on_change()
                self._wake.wait(timeout=backoff)
                self._wake.clear()
                backoff = min(backoff * 2, 300.0)
                continue
            self.on_change()
