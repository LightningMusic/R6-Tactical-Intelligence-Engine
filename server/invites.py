"""
Invite links for the browser recorder (/join).

A teammate opens a link, clicks once, and their browser records their own mic
for the host's session. The link carries an invite token that is deliberately
weaker than every other key on this server: it is tied to ONE in-game
username, it can only upload that person's voice chunks and check in (never
read a session, a transcript or another teammate's audio), it expires, and the
host can revoke it from the dashboard at any time.

Only a hash of the token is stored. A lost link is replaced with
"regenerate", which invalidates the old one.
"""
from __future__ import annotations

import hashlib
import hmac
import re
import secrets
import time
from typing import Optional

from server.database import server_db

TOKEN_PREFIX = "inv_"
DEFAULT_DAYS = 180
MAX_DAYS = 400
USERNAME_RE = re.compile(r"^[A-Za-z0-9_.\-]{2,32}$")
_LAST_SEEN_GRANULARITY_SEC = 30


def _hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest().lower()


def _new_token(invite_id: str) -> str:
    return f"{TOKEN_PREFIX}{invite_id}_{secrets.token_urlsafe(24)}"


def _split(token: str) -> Optional[str]:
    """The invite id inside a token, or None if it isn't shaped like one."""
    if not token.startswith(TOKEN_PREFIX):
        return None
    parts = token[len(TOKEN_PREFIX):].split("_", 1)
    if len(parts) != 2 or not re.fullmatch(r"[0-9a-f]{8}", parts[0]) or not parts[1]:
        return None
    return parts[0]


def _row(r, now: float) -> dict:
    return {
        "invite_id": r["invite_id"],
        "username": r["username"],
        "label": r["label"],
        "created_at": r["created_at"],
        "expires_at": r["expires_at"],
        "revoked": r["revoked_at"] is not None,
        "expired": r["expires_at"] <= now,
        "last_seen": r["last_seen"],
    }


def create(username: str, days: int = DEFAULT_DAYS, label: str = "") -> dict:
    username = (username or "").strip()
    if not USERNAME_RE.match(username):
        raise ValueError("Username must be the in-game name (2-32 letters, digits, . _ -).")
    days = max(1, min(int(days or DEFAULT_DAYS), MAX_DAYS))
    now = time.time()
    invite_id = secrets.token_hex(4)
    token = _new_token(invite_id)
    with server_db.get_connection() as conn:
        conn.execute(
            """INSERT INTO invites (invite_id, username, label, token_hash, created_at, expires_at)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (invite_id, username, (label or "").strip()[:60], _hash(token), now, now + days * 86400),
        )
        conn.commit()
    return {"invite_id": invite_id, "username": username, "token": token, "expires_at": now + days * 86400}


def list_all() -> list[dict]:
    now = time.time()
    with server_db.get_connection() as conn:
        rows = conn.execute("SELECT * FROM invites ORDER BY created_at DESC").fetchall()
    return [_row(r, now) for r in rows]


def revoke(invite_id: str) -> bool:
    with server_db.get_connection() as conn:
        cur = conn.execute("UPDATE invites SET revoked_at = ? WHERE invite_id = ? AND revoked_at IS NULL",
                           (time.time(), invite_id))
        conn.commit()
        return cur.rowcount > 0


def regenerate(invite_id: str, days: int = DEFAULT_DAYS) -> Optional[dict]:
    """A fresh token for an existing invite; the old link stops working."""
    days = max(1, min(int(days or DEFAULT_DAYS), MAX_DAYS))
    now = time.time()
    token = _new_token(invite_id)
    with server_db.get_connection() as conn:
        row = conn.execute("SELECT username FROM invites WHERE invite_id = ?", (invite_id,)).fetchone()
        if row is None:
            return None
        conn.execute("UPDATE invites SET token_hash = ?, expires_at = ?, revoked_at = NULL WHERE invite_id = ?",
                     (_hash(token), now + days * 86400, invite_id))
        conn.commit()
    return {"invite_id": invite_id, "username": row["username"], "token": token, "expires_at": now + days * 86400}


def authenticate(token: str) -> Optional[dict]:
    """The invite behind a token, or None if it is unknown, revoked, expired
    or simply wrong. Constant-time on the secret part."""
    invite_id = _split(token or "")
    if invite_id is None:
        return None
    now = time.time()
    with server_db.get_connection() as conn:
        r = conn.execute("SELECT * FROM invites WHERE invite_id = ?", (invite_id,)).fetchone()
        if r is None or not hmac.compare_digest(_hash(token), r["token_hash"]):
            return None
        if r["revoked_at"] is not None or r["expires_at"] <= now:
            return None
        if r["last_seen"] is None or now - float(r["last_seen"]) >= _LAST_SEEN_GRANULARITY_SEC:
            conn.execute("UPDATE invites SET last_seen = ? WHERE invite_id = ?", (now, invite_id))
            conn.commit()
    return {"invite_id": invite_id, "username": r["username"], "expires_at": r["expires_at"]}
