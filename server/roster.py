"""
The host's saved team list: in-game names (and optional nicknames) offered as a
pick-list on the dashboard when making invite links. Stored in the server's own
database, never in the repo.
"""
from __future__ import annotations

from server.database import server_db
from server.invites import USERNAME_RE

MAX_ENTRIES = 50


def get_all() -> list[dict]:
    with server_db.get_connection() as conn:
        rows = conn.execute("SELECT username, label FROM team_roster ORDER BY position, username").fetchall()
    return [{"username": r["username"], "label": r["label"]} for r in rows]


def replace_all(entries: list) -> list[dict]:
    """Replaces the whole list. Raises ValueError (nothing is changed) if any
    name isn't a valid in-game name or the list is too long."""
    cleaned: list[tuple[str, str]] = []
    seen: set[str] = set()
    for e in entries or []:
        username = str((e or {}).get("username", "")).strip()
        label = str((e or {}).get("label", "")).strip()[:60]
        if not USERNAME_RE.match(username):
            raise ValueError(f"'{username}' isn't a valid in-game name (2-32 letters, digits, . _ -).")
        if username.lower() in seen:
            continue
        seen.add(username.lower())
        cleaned.append((username, label))
    if len(cleaned) > MAX_ENTRIES:
        raise ValueError(f"At most {MAX_ENTRIES} names.")
    with server_db.get_connection() as conn:
        conn.execute("DELETE FROM team_roster")
        conn.executemany("INSERT INTO team_roster (username, label, position) VALUES (?, ?, ?)",
                         [(u, lbl, i) for i, (u, lbl) in enumerate(cleaned)])
        conn.commit()
    return get_all()
