"""
When is it safe to delete a recording?

The OBS video can still be worth keeping long after a match is imported (reviewing a round, a
clip, re-running audio with better settings), and once deleted it is gone. The automatic cleanup
exists for a real reason (a 64 GB stick once filled completely and broke an import), so it still
runs, but it now only removes a recording when everything we can get out of it is safe on the
server: every match that came from it has been uploaded AND the server has finished analysing it.
A recording whose matches have not uploaded (a rejected key, no connection, a server that was off)
is kept, and the log says why.

A package leaves the stick as soon as it uploads, so a recording is tied to its matches by time:
a match's queue entry is created while that recording runs, or shortly after it stops.
"""
from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, Optional

BEFORE_START_SLACK = 300.0          # a match can be queued a moment before the file name's second
AFTER_END_SLACK = 1800.0            # ...or shortly after it stops (tonight's took 18 and 35 seconds)
MIN_AGE_SECONDS = 6 * 3600.0        # never touch a recording written to in the last six hours
_NAME = re.compile(r"^(\d{4})-(\d{2})-(\d{2})[ T_](\d{2})-(\d{2})-(\d{2})")


def recording_start(path: Path) -> Optional[float]:
    """Epoch seconds from a name like '2026-10-05 16-22-33.mp4' (OBS names files in local time)."""
    m = _NAME.match(path.stem)
    if not m:
        return None
    try:
        return datetime(*(int(g) for g in m.groups())).timestamp()
    except ValueError:
        return None


def load_queue(queue_file: Path) -> list[dict]:
    """The upload queue as plain dicts, read-only (the live queue object stays the app's to write)."""
    try:
        data = json.loads(Path(queue_file).read_text(encoding="utf-8-sig"))
    except (OSError, ValueError):
        return []
    return [v for v in data.values() if isinstance(v, dict)] if isinstance(data, dict) else []


def _epoch(iso: Optional[str]) -> Optional[float]:
    if not iso:
        return None
    try:
        dt = datetime.fromisoformat(str(iso))
    except ValueError:
        return None
    return (dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)).timestamp()


def sessions_from(path: Path, queue: Iterable[dict]) -> list[dict]:
    start = recording_start(path)
    if start is None:
        return []
    try:
        end = path.stat().st_mtime
    except OSError:
        return []
    out = []
    for item in queue:
        made = _epoch(item.get("created_at"))
        if made is not None and start - BEFORE_START_SLACK <= made <= end + AFTER_END_SLACK:
            out.append(item)
    return out


def why_keep(path: Path, queue: Iterable[dict], now: Optional[float] = None) -> Optional[str]:
    """None when the recording can go; otherwise the reason it must stay."""
    queue = list(queue)
    try:
        age = (now if now is not None else datetime.now().timestamp()) - path.stat().st_mtime
    except OSError:
        return "it can't be read"
    if age < MIN_AGE_SECONDS:
        return "it was written to within the last few hours"
    if recording_start(path) is None:
        return "its start time can't be read from the file name"
    matches = sessions_from(path, queue)
    if not matches:
        return "no match from it is on record yet, so nothing has been uploaded from it"
    for item in matches:
        sid = str(item.get("session_id", "?"))[:16]
        if item.get("package_status") != "uploaded":
            return f"match {sid} has not uploaded yet ({item.get('last_error') or item.get('package_status')})"
        if item.get("remote_analysis_status") != "completed":
            return f"the server has not finished analysing match {sid} ({item.get('remote_analysis_status')})"
    return None


def split_deletable(candidates: Iterable[Path], queue: Iterable[dict], now: Optional[float] = None
                    ) -> tuple[list[Path], list[tuple[Path, str]]]:
    """(recordings that may be deleted, [(recording, why it is kept)])."""
    queue = list(queue)
    go: list[Path] = []
    keep: list[tuple[Path, str]] = []
    for p in candidates:
        why = why_keep(p, queue, now)
        if why is None:
            go.append(p)
        else:
            keep.append((p, why))
    return go, keep
