"""Is a replay one of our competition games?

r6-dissect reports a ``matchType`` per replay, e.g. ``{"name": "CustomGameOnline", "id": 4}``,
telling a user-made custom lobby apart from Ranked / Standard / Quick matchmaking. The team
only ever plays customs for its competitions (scrims and matches), so a custom lobby is exactly
the signal that an upload is worth the full, expensive debrief; a ranked/standard pug is not.

This keeps that one judgement in a single tested place, used by both the client (to tag a
package it uploads) and the server (to gate the heavy transcription + AI analysis on it).
"""
from __future__ import annotations

from typing import Any, Optional

# r6-dissect matchType.name values for a user-made lobby. Compared case-insensitively, and the
# plain "custom" substring is accepted too, so a future "CustomGame…" variant still counts.
CUSTOM_MATCH_TYPES = {"customgameonline", "customgamelocal"}


def match_type_name(match_type: Any) -> Optional[str]:
    """The matchType name out of a replay/metadata value, which may be a dict
    ({"name", "id"}), a bare string, or None."""
    if isinstance(match_type, dict):
        name = match_type.get("name")
        return str(name).strip() or None if name is not None else None
    if isinstance(match_type, str):
        return match_type.strip() or None
    return None


def is_custom(match_type: Any) -> Optional[bool]:
    """True/False when the match type is known; None when it can't be read — an old replay
    with no matchType, or a package made before this field existed. The caller decides what
    'unknown' should mean."""
    name = match_type_name(match_type)
    if name is None:
        return None
    n = name.lower()
    return n in CUSTOM_MATCH_TYPES or "custom" in n


def is_competition(match_type: Any, *, unknown: bool = True) -> bool:
    """Whether to treat this upload as one of our competition games. An unknown match type
    falls back to ``unknown`` (default True: never silently drop a real debrief just because
    a replay failed to label itself — current r6-dissect always does)."""
    known = is_custom(match_type)
    return unknown if known is None else known


def label(match_type: Any) -> str:
    """A short human label for logs and the dashboard, e.g. 'CustomGameOnline' or 'Ranked'."""
    return match_type_name(match_type) or "unknown"
