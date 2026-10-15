"""
Player keys for a teammate's own analyzer (/solo) and their sensitivity tool's
plug-in calls (2026-10-14, for Hector).

A player key is tied to ONE in-game username and can only reach the /solo and
/plugin routes, and only that player's own data there: their uploaded replays,
their settings snapshots, and their own lines from the team's matches. It can
never open the dashboard, comms, voice, transcripts, invites or another
player's numbers. The host's main key can do everything a player key can, for
any player.

Only a hash of the token is stored; a lost key is replaced with "regenerate".
"""
from __future__ import annotations

import hashlib
import hmac
import re
import secrets
import time
from typing import Optional

from server.database import server_db

TOKEN_PREFIX = "ply_"
DEFAULT_DAYS = 365
MAX_DAYS = 730
USERNAME_RE = re.compile(r"^[A-Za-z0-9_.\-]{2,32}$")
_LAST_SEEN_GRANULARITY_SEC = 30


def _hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest().lower()


def _new_token(key_id: str) -> str:
    return f"{TOKEN_PREFIX}{key_id}_{secrets.token_urlsafe(24)}"


def _split(token: str) -> Optional[str]:
    if not token.startswith(TOKEN_PREFIX):
        return None
    parts = token[len(TOKEN_PREFIX):].split("_", 1)
    if len(parts) != 2 or not re.fullmatch(r"[0-9a-f]{8}", parts[0]) or not parts[1]:
        return None
    return parts[0]


def _row(r, now: float) -> dict:
    return {"key_id": r["key_id"], "username": r["username"], "label": r["label"], "created_at": r["created_at"],
            "expires_at": r["expires_at"], "revoked": r["revoked_at"] is not None, "expired": r["expires_at"] <= now,
            "last_seen": r["last_seen"]}


def create(username: str, days: int = DEFAULT_DAYS, label: str = "") -> dict:
    username = (username or "").strip()
    if not USERNAME_RE.match(username):
        raise ValueError("Username must be the in-game name (2-32 letters, digits, . _ -).")
    days = max(1, min(int(days or DEFAULT_DAYS), MAX_DAYS))
    now = time.time()
    key_id = secrets.token_hex(4)
    token = _new_token(key_id)
    with server_db.get_connection() as conn:
        conn.execute("""INSERT INTO player_keys (key_id, username, label, token_hash, created_at, expires_at)
                        VALUES (?, ?, ?, ?, ?, ?)""",
                     (key_id, username, (label or "").strip()[:60], _hash(token), now, now + days * 86400))
        conn.commit()
    return {"key_id": key_id, "username": username, "token": token, "expires_at": now + days * 86400}


def list_all() -> list[dict]:
    now = time.time()
    with server_db.get_connection() as conn:
        rows = conn.execute("SELECT * FROM player_keys ORDER BY created_at DESC").fetchall()
    return [_row(r, now) for r in rows]


def revoke(key_id: str) -> bool:
    with server_db.get_connection() as conn:
        cur = conn.execute("UPDATE player_keys SET revoked_at = ? WHERE key_id = ? AND revoked_at IS NULL",
                           (time.time(), key_id))
        conn.commit()
        return cur.rowcount > 0


def purge(key_id: str) -> bool:
    """Removes a key from the list for good (the player's uploaded matches are kept)."""
    with server_db.get_connection() as conn:
        cur = conn.execute("DELETE FROM player_keys WHERE key_id = ?", (key_id,))
        conn.commit()
        return cur.rowcount > 0


def purge_dead() -> int:
    with server_db.get_connection() as conn:
        cur = conn.execute("DELETE FROM player_keys WHERE revoked_at IS NOT NULL OR expires_at <= ?", (time.time(),))
        conn.commit()
        return cur.rowcount


def regenerate(key_id: str, days: int = DEFAULT_DAYS) -> Optional[dict]:
    days = max(1, min(int(days or DEFAULT_DAYS), MAX_DAYS))
    now = time.time()
    token = _new_token(key_id)
    with server_db.get_connection() as conn:
        row = conn.execute("SELECT username FROM player_keys WHERE key_id = ?", (key_id,)).fetchone()
        if row is None:
            return None
        conn.execute("UPDATE player_keys SET token_hash = ?, expires_at = ?, revoked_at = NULL WHERE key_id = ?",
                     (_hash(token), now + days * 86400, key_id))
        conn.commit()
    return {"key_id": key_id, "username": row["username"], "token": token, "expires_at": now + days * 86400}


def authenticate(token: str) -> Optional[dict]:
    """The key behind a token, or None if unknown, revoked, expired or wrong."""
    key_id = _split(token or "")
    if key_id is None:
        return None
    now = time.time()
    with server_db.get_connection() as conn:
        r = conn.execute("SELECT * FROM player_keys WHERE key_id = ?", (key_id,)).fetchone()
        if r is None or not hmac.compare_digest(_hash(token), r["token_hash"]):
            return None
        if r["revoked_at"] is not None or r["expires_at"] <= now:
            return None
        if r["last_seen"] is None or now - float(r["last_seen"]) >= _LAST_SEEN_GRANULARITY_SEC:
            conn.execute("UPDATE player_keys SET last_seen = ? WHERE key_id = ?", (now, key_id))
            conn.commit()
    return {"key_id": key_id, "username": r["username"]}


def known_usernames() -> list[str]:
    """Every username that has (or had) a player key, for the host's pick-list."""
    with server_db.get_connection() as conn:
        return [r[0] for r in conn.execute("SELECT DISTINCT username FROM player_keys ORDER BY username")]
