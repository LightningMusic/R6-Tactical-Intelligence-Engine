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


class SignalWatch:
    """Decides when the host's 'recording' signal failing is worth interrupting the host about.

    Until the signal works, teammates' recorders that follow the host are never told to start and record
    nothing. That is serious at the START of a session (nothing has told them yet) but not for one missed
    minute in the middle: the signal is re-sent every minute and the server only lets go after 20 silent
    minutes. On 2026-10-06 a single 10 s timeout during a background upload raised an alarm that had fixed
    itself a minute later. So: warn at once when it has never worked this session, otherwise after two
    failures in a row; say so once; and say when it recovers (only if a warning was shown)."""

    def __init__(self, fails_before_warning: int = 2) -> None:
        self.fails_before_warning = fails_before_warning
        self.ever_worked = False
        self.fails = 0
        self.warned = False

    def record(self, ok: bool, last_chance: bool = False) -> Optional[str]:
        """Feed each attempt's result; returns "warn", "recovered" or None. `last_chance` is for a signal that
        will not be tried again (the stop at the end of a session): it warns at once."""
        if ok:
            self.ever_worked = True
            self.fails = 0
            if self.warned:
                self.warned = False
                return "recovered"
            return None
        self.fails += 1
        if not self.warned and (last_chance or not self.ever_worked or self.fails >= self.fails_before_warning):
            self.warned = True
            return "warn"
        return None


class CompanionLink:
    def __init__(self, http=None) -> None:
        self.http = http or requests
        self._last_lines: dict[str, str] = {}
        self.last_error = ""            # why the last set_recording() failed, in plain words ("" when it worked)

    def _base(self) -> Optional[tuple[str, dict]]:
        from app.uploader import resolve_api_key
        url = (settings.SERVER_URL or "").rstrip("/")
        key = resolve_api_key(self.http if self.http is not requests else None)
        if not url or not key:
            return None
        return url, {"Authorization": f"Bearer {key}"}

    def set_recording(self, on: bool) -> bool:
        """Tells the server this session is (not) recording, which is the only thing that makes teammates'
        recorders in "follow the host" mode start and stop. The reason for a failure is kept in
        `last_error`: this used to fail silently, and a refused key meant nobody's recorder ever started
        (2026-10-05) without a word on the host's screen."""
        self.last_error = ""
        base = self._base()
        if base is None:
            self.last_error = "no server address or API key is set up in this app"
            return False
        try:
            r = self.http.put(f"{base[0]}/api/v1/companion/control", headers=base[1],
                              json={"recording": bool(on)}, timeout=10)
        except Exception:
            self.last_error = "the server could not be reached"
            return False
        code = getattr(r, "status_code", None)
        if code == 200:
            return True
        self.last_error = ("the server refused this app's API key" if code in (401, 403)
                           else f"the server answered HTTP {code}")
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
        live = s.get("started") if browser else s.get("recording")          # a browser reports it while waiting too
        silent = " -- mic looks silent, check headset" if live and s.get("mic_ok") is False else ""
        # A browser PC that delivers microphone audio slower or faster than real time (2026-10-06: two thirds
        # speed all night) records something that cannot line up with the match.
        ratio = s.get("audio_ratio")
        if browser and s.get("recording") and isinstance(ratio, (int, float)) and not 0.93 <= ratio <= 1.07:
            silent += f" -- its audio is arriving at {ratio * 100:.0f}% of real time, so it won't line up with the match"
        # A companion can't see its own microphone, but the server can hear what it uploaded last time.
        last = c.get("last_recording") or {}
        if not silent and last.get("silent") and float(last.get("seconds") or 0) >= 60:
            when = time.strftime("%a %H:%M", time.localtime(float(last.get("start_epoch") or now)))
            mins = int(float(last["seconds"]) // 60)
            # Their recorder now looks for a working microphone by itself, so the note is for the host:
            # what happened, and what to look at if it happens again.
            if last.get("finished"):
                silent = (f" -- their last recording ({when}, {mins} min) was SILENT; the recorder now switches to a "
                          f"working mic by itself, so watch for a 'mic looks silent' line")
            else:
                silent = (f" -- the audio uploaded so far ({mins} min) is SILENT: no mic on that PC is picking up "
                          f"anything (headset unplugged or muted?)")
        healed = s.get("mic_healed")
        if healed and s.get("mic_ok") is not False:
            silent += f" -- switched to a working mic by itself: {healed}"
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
                return f"{label}: browser page is open but their mic isn't started{silent}"
            return f"{label}: browser ready, waiting for the session{silent}"
        return f"{label}: not recording -- {s.get('obs', 'unknown')}{silent}"

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
            # Durations and "last seen N min ago" don't count as a change (2026-10-07: a stick left at home
            # was reported 75 times in one practice, once a minute, because its minute count kept changing).
            gist = line.split(" ✓")[0].split(" (last seen")[0]
            if self._last_lines.get(key) != gist:
                self._last_lines[key] = gist
                out.append(line)
        return out
