"""
Self-updating game catalog: learns operators and maps from imported replays.

Replays identify operators and maps by numeric in-game IDs. r6-dissect only
knows the IDs compiled into it, so every new operator or map rework used to
surface as "Operator(<id>)" / "Map(<id>)" until someone hand-edited a lookup
table and the seed list. This module closes that loop from the data itself:

  Operators -- each replay carries the operator's game ID, the game's own
    name for it (roleName, e.g. "NOOR"; recorded for one team only) and the
    side it was played on. Known IDs resolve directly; an unknown ID is
    linked to an existing operator by name, or a new operator is created.
    An ID seen only on the enemy team (so no name) becomes a placeholder
    that is renamed -- or merged into the real operator, stats and all --
    as soon as a name shows up.

  Maps -- replays carry only a numeric map ID, never a name. An unknown ID
    is linked by r6-dissect's name for it when it has one, otherwise by its
    bomb-site names (reworks keep them), and otherwise flagged in
    unknown_maps for a human to name once via name_unknown_map(), which
    also backfills every match already played on it.

Gadgets and abilities never appear in replays; integration/ubisoft_catalog.py
fills those from Ubisoft's official operator pages.
"""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Callable, Iterable, Optional

RECRUIT_GAME_ID = 359656345734
PLACEHOLDER_OPERATOR_PREFIX = "Unknown operator ("
UNKNOWN_MAP_NAME = "Unknown"

_DISSECT_PLACEHOLDER = re.compile(r"^(Operator|Map)\(\d+\)$")

# r6-dissect enum names whose base form differs from the map pool's name.
_MAP_ALIASES = {"presidentialplane": "Plane"}


def fold(name: str) -> str:
    """Comparison key: case-, accent- and punctuation-insensitive.
    "Nøkk" / "NOKK" / "Nokk" and "Solid Snake" / "SolidSnake" all collide.
    ø has no Unicode decomposition, so it is mapped explicitly."""
    s = (name or "").replace("ø", "o").replace("Ø", "O")
    s = "".join(c for c in unicodedata.normalize("NFD", s) if unicodedata.category(c) != "Mn")
    return "".join(c for c in s.lower() if c.isalnum())


def _is_placeholder(name: Optional[str]) -> bool:
    return not name or bool(_DISSECT_PLACEHOLDER.match(name.strip()))


def _split_camel(name: str) -> str:
    """ "KafeDostoyevsky" -> "Kafe Dostoyevsky" """
    return re.sub(r"(?<=[a-z])(?=[A-Z])", " ", name).strip()


def _display_from_role_name(role_name: str) -> str:
    """ "NOOR" -> "Noor", "SOLID SNAKE" -> "Solid Snake". Short all-caps
    names like "IQ" come out as "Iq" here; the Ubisoft sync corrects the
    display name of replay-learned operators afterwards."""
    return " ".join(w.capitalize() for w in role_name.split())


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


@dataclass
class CatalogOutcome:
    map_name: str
    map_game_id: Optional[int]
    map_needs_name: bool = False
    log_lines: list[str] = field(default_factory=list)


# =====================================================================
# Operators
# =====================================================================

def _operator_by_fold(conn, names: Iterable[str]) -> Optional[tuple[int, str]]:
    wanted = {fold(n) for n in names if n and not _is_placeholder(n)}
    wanted.discard("")
    if not wanted:
        return None
    for row in conn.execute("SELECT operator_id, name FROM operators"):
        if not row[1].startswith(PLACEHOLDER_OPERATOR_PREFIX) and fold(row[1]) in wanted:
            return row[0], row[1]
    return None


def _merge_operator(conn, from_id: int, into_id: int) -> None:
    """Moves everything that references a placeholder onto the real
    operator, then deletes the placeholder."""
    conn.execute("UPDATE player_round_stats SET operator_id = ? WHERE operator_id = ?", (into_id, from_id))
    conn.execute("UPDATE operator_game_ids SET operator_id = ? WHERE operator_id = ?", (into_id, from_id))
    conn.execute("DELETE FROM operator_gadget_options WHERE operator_id = ?", (from_id,))
    conn.execute("DELETE FROM operators WHERE operator_id = ?", (from_id,))


def resolve_operator(
    conn,
    game_id: Optional[int],
    dissect_name: str,
    role_name: str,
    side: Optional[str],
    hint_name: Optional[str] = None,
    log: Optional[Callable[[str], None]] = None,
) -> Optional[tuple[int, str]]:
    """Returns (operator_id, name) for this replay operator, learning and
    linking as needed. None only for Recruit / unusable data."""
    log = log or (lambda _m: None)
    names = [hint_name or "", dissect_name or "", role_name or ""]

    if not game_id:
        return _operator_by_fold(conn, names)
    if game_id == RECRUIT_GAME_ID or "recruit" in {fold(n) for n in names}:
        return None  # plays on both sides; never learnable as one operator

    row = conn.execute(
        """SELECT o.operator_id, o.name FROM operator_game_ids g
           JOIN operators o ON o.operator_id = g.operator_id WHERE g.game_id = ?""",
        (game_id,),
    ).fetchone()
    if row:
        op_id, op_name = row[0], row[1]
        if op_name.startswith(PLACEHOLDER_OPERATOR_PREFIX):
            return _upgrade_placeholder(conn, op_id, op_name, names, log) or (op_id, op_name)
        return op_id, op_name

    existing = _operator_by_fold(conn, names)
    if existing:
        conn.execute(
            "INSERT OR IGNORE INTO operator_game_ids (game_id, operator_id, source) VALUES (?, ?, 'replay')",
            (game_id, existing[0]),
        )
        log(f"  [catalog] Linked operator ID {game_id} -> {existing[1]}")
        return existing

    display = _best_operator_display(hint_name, role_name, dissect_name)
    if side not in ("attack", "defense"):
        return None  # can't create a row without a side; next sighting will
    name = display or f"{PLACEHOLDER_OPERATOR_PREFIX}{game_id})"
    cur = conn.execute(
        """INSERT INTO operators (name, side, ability_name, ability_max_count, source)
           VALUES (?, ?, '', 0, 'replay')""",
        (name, side),
    )
    op_id = cur.lastrowid
    conn.execute(
        "INSERT OR IGNORE INTO operator_game_ids (game_id, operator_id, source) VALUES (?, ?, 'replay')",
        (game_id, op_id),
    )
    if display:
        log(f"  [catalog] NEW operator learned from replay: {display} ({side}, ID {game_id})")
    else:
        log(f"  [catalog] Unnamed operator seen on the other team (ID {game_id}, {side}) -- "
            f"stored as a placeholder; it gets its real name the first time your side plays it.")
    return op_id, name


def _best_operator_display(hint_name, role_name, dissect_name) -> str:
    if hint_name and not _is_placeholder(hint_name) and not hint_name.startswith(PLACEHOLDER_OPERATOR_PREFIX):
        return hint_name.strip()
    if role_name:
        return _display_from_role_name(role_name)
    if dissect_name and not _is_placeholder(dissect_name):
        return _split_camel(dissect_name)
    return ""


def _upgrade_placeholder(conn, op_id, op_name, names, log) -> Optional[tuple[int, str]]:
    real = _operator_by_fold(conn, names)
    if real:
        _merge_operator(conn, op_id, real[0])
        log(f"  [catalog] {op_name} identified as {real[1]} -- merged its stats into {real[1]}")
        return real
    display = _best_operator_display(names[0], names[2], names[1])
    if display:
        conn.execute("UPDATE operators SET name = ? WHERE operator_id = ?", (display, op_id))
        log(f"  [catalog] {op_name} identified as {display}")
        return op_id, display
    return None


# =====================================================================
# Maps
# =====================================================================

def _map_by_fold(conn, names: Iterable[str]) -> Optional[tuple[int, str]]:
    wanted = set()
    for n in names:
        if not n or _is_placeholder(n) or n == UNKNOWN_MAP_NAME:
            continue
        base = re.sub(r"Y\d+$", "", n.strip())
        wanted.add(fold(base))
        alias = _MAP_ALIASES.get(fold(base))
        if alias:
            wanted.add(fold(alias))
    wanted.discard("")
    if not wanted:
        return None
    for row in conn.execute("SELECT map_id, name FROM maps"):
        if fold(row[1]) in wanted:
            return row[0], row[1]
    return None


def _maps_matching_sites(conn, sites: list[str]) -> list[tuple[int, str]]:
    if not sites:
        return []
    qmarks = ",".join("?" * len(sites))
    return [
        (r[0], r[1]) for r in conn.execute(
            f"""SELECT m.map_id, m.name FROM map_sites s JOIN maps m ON m.map_id = s.map_id
                WHERE s.site IN ({qmarks}) GROUP BY m.map_id""",
            sites,
        )
    ]


def _link_map(conn, game_id: int, map_id: int, map_name: str, source: str) -> None:
    conn.execute(
        "INSERT OR IGNORE INTO map_game_ids (game_id, map_id, source) VALUES (?, ?, ?)",
        (game_id, map_id, source),
    )
    # Anything flagged or imported earlier under this ID gets its name now.
    conn.execute(
        "UPDATE matches SET map = ?, map_id = ? WHERE map_game_id = ?",
        (map_name, map_id, game_id),
    )
    conn.execute("DELETE FROM unknown_maps WHERE game_id = ?", (game_id,))


def _remember_sites(conn, map_id: int, sites: list[str]) -> None:
    for s in sites:
        conn.execute("INSERT OR IGNORE INTO map_sites (map_id, site) VALUES (?, ?)", (map_id, s))


def resolve_map(
    conn,
    game_id: Optional[int],
    dissect_name: Optional[str],
    sites: list[str],
    hint_name: Optional[str] = None,
    log: Optional[Callable[[str], None]] = None,
) -> tuple[Optional[int], str, bool]:
    """Returns (map_id, map_name, needs_name)."""
    log = log or (lambda _m: None)
    sites = sorted({s.strip() for s in sites if s and s.strip()})

    if game_id:
        row = conn.execute(
            """SELECT m.map_id, m.name FROM map_game_ids g
               JOIN maps m ON m.map_id = g.map_id WHERE g.game_id = ?""",
            (game_id,),
        ).fetchone()
        if row:
            _remember_sites(conn, row[0], sites)
            return row[0], row[1], False

    named = _map_by_fold(conn, [hint_name or "", dissect_name or ""])
    by_sites = _maps_matching_sites(conn, sites)

    if named:
        # r6-dissect's name disagreeing with where these exact sites were
        # seen before means the sources conflict: ask instead of guessing.
        contradicted = by_sites and named[0] not in {m for m, _ in by_sites}
        if not contradicted:
            if game_id:
                _link_map(conn, game_id, named[0], named[1], "replay")
                log(f"  [catalog] Linked map ID {game_id} -> {named[1]}")
            _remember_sites(conn, named[0], sites)
            return named[0], named[1], False
        log(f"  [catalog] Map ID {game_id}: replay says {named[1]!r} but its sites match "
            f"{', '.join(n for _, n in by_sites)} -- flagging instead of guessing")
    elif len(by_sites) == 1 and game_id:
        map_id, map_name = by_sites[0]
        _link_map(conn, game_id, map_id, map_name, "sites")
        _remember_sites(conn, map_id, sites)
        log(f"  [catalog] New map ID {game_id} recognized as {map_name} by its site names")
        return map_id, map_name, False
    elif (not named) and dissect_name and not _is_placeholder(dissect_name) and game_id and not by_sites:
        # r6-dissect knows a name the map pool doesn't have yet: a map that
        # predates or postdates the seeded pool. Its name is trustworthy.
        new_name = _split_camel(re.sub(r"Y\d+$", "", dissect_name))
        cur = conn.execute("INSERT INTO maps (name, is_active_pool) VALUES (?, 1)", (new_name,))
        _link_map(conn, game_id, cur.lastrowid, new_name, "replay")
        _remember_sites(conn, cur.lastrowid, sites)
        log(f"  [catalog] NEW map learned from replay: {new_name} (ID {game_id})")
        return cur.lastrowid, new_name, False

    if not game_id:
        return None, UNKNOWN_MAP_NAME, False

    conn.execute(
        """INSERT INTO unknown_maps (game_id, dissect_name, sites, first_seen, times_seen)
           VALUES (?, ?, ?, ?, 1)
           ON CONFLICT(game_id) DO UPDATE SET times_seen = times_seen + 1""",
        (game_id, dissect_name, " | ".join(sites), _now()),
    )
    if sites:
        row = conn.execute("SELECT sites FROM unknown_maps WHERE game_id = ?", (game_id,)).fetchone()
        merged = sorted({*filter(None, (row[0] or "").split(" | ")), *sites})
        conn.execute("UPDATE unknown_maps SET sites = ? WHERE game_id = ?", (" | ".join(merged), game_id))
    log(f"  [catalog] ⚑ NEW MAP (ID {game_id}) -- not recognized. Sites: "
        f"{', '.join(sites) or 'none recorded'}. It needs a name.")
    return None, UNKNOWN_MAP_NAME, True


# =====================================================================
# Import entry point
# =====================================================================

def learn_from_import(db, result, hints: Optional[dict] = None, log=None) -> CatalogOutcome:
    """
    Resolves and learns everything in one ImportResult, in place:
    result.map_name / map_id / map_needs_name are set, and each raw player's
    "operator" becomes the catalog's canonical name, with "operator_db_id".
    Idempotent -- safe to call again on an already-resolved result.

    hints: optional {"maps": {game_id: name}, "operators": {game_id: name}},
    names the uploading client had already learned. This is how a map a
    person named on the client reaches the server's database.
    """
    if getattr(result, "catalog_resolved", False):
        return CatalogOutcome(result.map_name or UNKNOWN_MAP_NAME, result.map_game_id,
                              result.map_needs_name, [])

    lines: list[str] = []
    emit = (lambda m: (lines.append(m), log(m))) if log else lines.append
    hints = hints or {}
    op_hints = {int(k): v for k, v in (hints.get("operators") or {}).items()}
    map_hints = {int(k): v for k, v in (hints.get("maps") or {}).items()}

    with db.get_connection() as conn:
        for round_obj in result.rounds:
            for raw in round_obj.raw_player_stats:
                gid = raw.get("operator_game_id")
                resolved = resolve_operator(
                    conn, gid, raw.get("operator", ""), raw.get("role_name", ""),
                    raw.get("side"), op_hints.get(gid) if gid else None, emit,
                )
                if resolved:
                    raw["operator_db_id"], raw["operator"] = resolved

        sites = [r.site for r in result.rounds if r.site]
        game_id = result.map_game_id
        map_id, map_name, needs_name = resolve_map(
            conn, game_id, result.dissect_map_name or result.map_name, sites,
            map_hints.get(game_id) if game_id else None, emit,
        )
        conn.commit()

    result.map_name = map_name
    result.map_id = map_id
    result.map_needs_name = needs_name
    result.catalog_resolved = True
    return CatalogOutcome(map_name, game_id, needs_name, lines)


def client_hints_for(db, result) -> dict:
    """The names this database already has for the IDs in one match --
    attached to the upload package so the server can learn from them."""
    op_ids = {
        raw.get("operator_game_id")
        for r in result.rounds for raw in r.raw_player_stats if raw.get("operator_game_id")
    }
    out: dict = {"operators": {}, "maps": {}}
    with db.get_connection() as conn:
        for gid in op_ids:
            row = conn.execute(
                """SELECT o.name FROM operator_game_ids g JOIN operators o
                   ON o.operator_id = g.operator_id WHERE g.game_id = ?""", (gid,)).fetchone()
            if row and not row[0].startswith(PLACEHOLDER_OPERATOR_PREFIX):
                out["operators"][str(gid)] = row[0]
        if result.map_game_id:
            row = conn.execute(
                """SELECT m.name FROM map_game_ids g JOIN maps m
                   ON m.map_id = g.map_id WHERE g.game_id = ?""", (result.map_game_id,)).fetchone()
            if row:
                out["maps"][str(result.map_game_id)] = row[0]
    return out


# =====================================================================
# Flagged maps
# =====================================================================

def pending_unknown_maps(db) -> list[dict]:
    with db.get_connection() as conn:
        rows = conn.execute(
            """SELECT u.game_id, u.dissect_name, u.sites, u.first_seen, u.times_seen,
                      (SELECT COUNT(*) FROM matches m WHERE m.map_game_id = u.game_id) AS matches
               FROM unknown_maps u ORDER BY u.first_seen"""
        ).fetchall()
    return [dict(r) for r in rows]


def name_unknown_map(db, game_id: int, name: str) -> tuple[str, int]:
    """Names a flagged map. If the name matches an existing map (a rework
    under a new ID) it's linked to that map; otherwise a new map is added.
    Every match already recorded under this ID is updated. Returns
    (final map name, number of matches backfilled)."""
    name = name.strip()
    if not name:
        raise ValueError("Map name can't be empty.")
    with db.get_connection() as conn:
        existing = _map_by_fold(conn, [name])
        if existing:
            map_id, final = existing
        else:
            map_id = conn.execute(
                "INSERT INTO maps (name, is_active_pool) VALUES (?, 1)", (name,)
            ).lastrowid
            final = name
        row = conn.execute("SELECT sites FROM unknown_maps WHERE game_id = ?", (game_id,)).fetchone()
        sites = [s for s in ((row[0] if row else "") or "").split(" | ") if s]
        _link_map(conn, game_id, map_id, final, "user")
        _remember_sites(conn, map_id, sites)
        backfilled = conn.execute(
            "SELECT COUNT(*) FROM matches WHERE map_game_id = ?", (game_id,)
        ).fetchone()[0]
        conn.commit()
    return final, backfilled
