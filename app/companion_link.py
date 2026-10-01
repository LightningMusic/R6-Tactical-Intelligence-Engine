"""
The host side of R6Companion: tells the team server when this app's session
starts and stops (companions on teammates' PCs follow it), and reads back how
each companion is doing so the recording log can say so.

Goes through the server rather than to the teammates' PCs directly because
school-managed PCs accept no incoming connections. While recording, the
"recording" state is re-sent every minute: that's how companions know this
app is still there (the server tells them to stop after 20 minutes of
silence, e.g. if this PC crashed).
"""
from __future__ import annotations

import time
from typing import Optional

import requests

from app.config import settings


class CompanionLink:
    def __init__(self, http=None) -> None:
        self.http = http or requests
        self._last_lines: dict[str, str] = {}

    def _base(self) -> Optional[tuple[str, dict]]:
        url, key = (settings.SERVER_URL or "").rstrip("/"), settings.API_KEY
        if not url or not key:
            return None
        return url, {"Authorization": f"Bearer {key}"}

    def set_recording(self, on: bool) -> bool:
        base = self._base()
        if base is None:
            return False
        try:
            r = self.http.put(f"{base[0]}/api/v1/companion/control", headers=base[1],
                              json={"recording": bool(on)}, timeout=10)
            return r.status_code == 200
        except Exception:
            return False

    def status(self) -> Optional[dict]:
        base = self._base()
        if base is None:
            return None
        try:
            r = self.http.get(f"{base[0]}/api/v1/companion/status", headers=base[1], timeout=10)
            return r.json() if r.status_code == 200 else None
        except Exception:
            return None

    @staticmethod
    def describe(c: dict, names: Optional[dict] = None, now: Optional[float] = None) -> str:
        """One line per companion for the recording log."""
        now = now or time.time()
        user = c.get("username", "?")
        who = (names or {}).get(user.lower(), user)
        label = who if who == user else f"{who} ({user})"
        s = c.get("status") or {}
        browser = s.get("kind") == "browser"
        if c.get("seconds_since_seen", 1e9) > 30:
            what = "browser recorder" if browser else "companion"
            return f"{label}: {what} not checking in (last seen {int(c['seconds_since_seen'] // 60)} min ago)"
        silent = " -- mic looks silent, check headset" if browser and s.get("started") and s.get("mic_ok") is False else ""
        if s.get("recording"):
            secs = int(now - float(s.get("since") or now))
            extra = f", {s['free_gb']} GB free" if s.get("free_gb") is not None and s["free_gb"] < 5 else ""
            return f"{label}: recording ✓ {secs // 60} min{extra}{silent}"
        if s.get("exporting") or s.get("uploads_waiting"):
            return f"{label}: uploading ({s.get('uploads_waiting', 0)} chunk(s) waiting)"
        if browser:
            if s.get("paused"):
                return f"{label}: mic paused in the browser"
            if s.get("started") is False:
                return f"{label}: browser page is open but their mic isn't started"
            return f"{label}: browser ready, waiting for the session{silent}"
        return f"{label}: not recording -- {s.get('obs', 'unknown')}"

    def changed_lines(self, names: Optional[dict] = None) -> list[str]:
        """Companion status lines that changed since the last call (so the log
        isn't flooded every minute with the same thing). Recording durations
        don't count as a change."""
        data = self.status()
        if not data:
            return []
        out = []
        for c in data.get("companions") or []:
            line = self.describe(c, names)
            key = c.get("device_id", "")
            gist = line.split(" ✓")[0]
            if self._last_lines.get(key) != gist:
                self._last_lines[key] = gist
                out.append(line)
        return out
