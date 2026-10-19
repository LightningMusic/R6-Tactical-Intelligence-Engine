"""
Room (callout) labels per map, for the main team analyzer.

The replay has coordinates but no room names. `analysis/places.py` already names floors (B/1F/2F) and each bomb
site's area from where defenders set up, so we know real callouts for rooms that have been objectives
("Armory Lockers", "Bathroom"). The rest of a map has no name until someone gives it one.

This is the one-time labelling the host does on `/rooms`: a top-down plot of everywhere players have actually
been (so the point density draws the map), with the known site areas pre-dropped as starting pins. The host
clicks each room and names it. A room is stored as one named point on a floor; a position is then named by the
nearest room on its own floor (`room_of`), the same nearest-anchor idea places.py already uses for sites.

Stored in the server's match database (`map_rooms`): server-side reference data, keyed by the map name exactly
as the matches table spells it.
"""
from __future__ import annotations

import math
from typing import Optional

from server.match_db import get_match_repo

MAX_SCATTER = 3500           # points sent to the labelling page (downsampled)
ROOM_MAX_M = 8.0             # a position farther than this from every room on its floor is left unnamed
FLOOR_GAP = 1.6


def _conn():
    return get_match_repo().db.get_connection()


def _ensure_table(conn) -> None:
    conn.execute("""CREATE TABLE IF NOT EXISTS map_rooms (
        map   TEXT NOT NULL,
        name  TEXT NOT NULL,
        floor TEXT NOT NULL DEFAULT '',
        x REAL NOT NULL, y REAL NOT NULL, z REAL NOT NULL,
        PRIMARY KEY (map, name))""")


def maps_with_positions() -> list[dict]:
    """Every map that has stored round positions, with how many rounds and how many rooms are labelled so far."""
    with _conn() as conn:
        _ensure_table(conn)
        rows = conn.execute("""
            SELECT m.map AS map, COUNT(DISTINCT d.match_id || '_' || d.metric_name) AS rounds
            FROM matches m JOIN derived_metrics d ON d.match_id = m.match_id
            WHERE d.metric_name LIKE 'round_%_positions' AND m.map NOT LIKE 'Map(%'
            GROUP BY m.map ORDER BY rounds DESC""").fetchall()
        labelled = {r[0]: r[1] for r in conn.execute("SELECT map, COUNT(*) FROM map_rooms GROUP BY map")}
    return [{"map": r["map"], "rounds": r["rounds"], "rooms": labelled.get(r["map"], 0)} for r in rows]


def _map_rounds(conn, map_name: str) -> list[dict]:
    """[{"site", "positions"}] for every stored round on this map (what places.py wants)."""
    from integration.positions import decode_blob
    out = []
    mids = [r[0] for r in conn.execute("SELECT match_id FROM matches WHERE map = ?", (map_name,))]
    for mid in mids:
        sites = {rn: (site or "") for rn, site in
                 conn.execute("SELECT round_number, site FROM rounds WHERE match_id = ?", (mid,))}
        for name, text in conn.execute("""SELECT metric_name, metric_text FROM derived_metrics
                                          WHERE match_id = ? AND metric_name LIKE 'round_%_positions'""", (mid,)):
            try:
                rn = int(name.split("_")[1])
            except ValueError:
                continue
            pos = decode_blob(text)
            if pos:
                out.append({"site": sites.get(rn, ""), "positions": pos})
    return out


def _floor_of(z: float, floors: list[tuple[float, str]]) -> str:
    return min(floors, key=lambda f: abs(f[0] - z))[1] if floors else ""


def scatter(map_name: str) -> dict:
    """Everything the labelling page draws: downsampled points (x, y, floor), the floor list, the map's
    extent, the known site areas as starting pins, and any rooms already saved."""
    from analysis import places
    with _conn() as conn:
        _ensure_table(conn)
        rounds = _map_rounds(conn, map_name)
        saved = get_rooms(map_name)
    if not rounds:
        return {"map": map_name, "floors": [], "points": [], "bounds": None, "suggested": [], "rooms": saved}
    floors = places.floors(rounds)
    floor_list = [{"name": name, "z": round(z, 1)} for z, name in floors] or [{"name": "", "z": 0.0}]
    fi = {f["name"]: i for i, f in enumerate(floor_list)}

    pts: list[list] = []
    for r in rounds:
        for pl in (r["positions"].get("players") or {}).values():
            for p in pl["pts"][::3]:                       # every 3rd sample is plenty for a shape
                pts.append((p[1], p[2], _floor_of(p[3], floors)))
    xs = [p[0] for p in pts]
    ys = [p[1] for p in pts]
    bounds = {"minx": min(xs), "maxx": max(xs), "miny": min(ys), "maxy": max(ys)} if pts else None
    step = max(1, len(pts) // MAX_SCATTER)
    points = [[round(p[0], 1), round(p[1], 1), fi.get(p[2], 0)] for p in pts[::step]]

    suggested = []
    if not saved:
        try:
            model = places.site_model(rounds, floors)
            for site, c in model.items():
                suggested.append({"name": _clean_site(site), "x": round(c["x"], 1), "y": round(c["y"], 1),
                                  "z": round(c["z"], 1), "floor": _floor_of(c["z"], floors)})
        except Exception:
            pass
    return {"map": map_name, "floors": floor_list, "points": points, "bounds": bounds,
            "suggested": suggested, "rooms": saved}


def _clean_site(site: str) -> str:
    """"2F Armory Lockers, 2F Archives" -> "Armory Lockers": the first area, floor prefix dropped."""
    first = (site or "").split(",")[0].strip()
    parts = first.split(" ", 1)
    return parts[1] if len(parts) == 2 and parts[0] in ("B", "1F", "2F", "3F", "4F") else first


def get_rooms(map_name: str) -> list[dict]:
    with _conn() as conn:
        _ensure_table(conn)
        rows = conn.execute("SELECT name, floor, x, y, z FROM map_rooms WHERE map = ? ORDER BY floor, name",
                            (map_name,)).fetchall()
    return [{"name": r["name"], "floor": r["floor"], "x": r["x"], "y": r["y"], "z": r["z"]} for r in rows]


def save_rooms(map_name: str, rooms: list[dict]) -> int:
    """Replace this map's rooms with the given list. Each room: {name, floor, x, y, z}."""
    clean = []
    seen = set()
    for r in rooms or []:
        name = str(r.get("name") or "").strip()[:40]
        if not name or name.lower() in seen:
            continue
        try:
            x, y, z = float(r["x"]), float(r["y"]), float(r["z"])
        except (KeyError, TypeError, ValueError):
            continue
        seen.add(name.lower())
        clean.append((map_name, name, str(r.get("floor") or "")[:4], x, y, z))
        if len(clean) >= 200:
            break
    with _conn() as conn:
        _ensure_table(conn)
        conn.execute("DELETE FROM map_rooms WHERE map = ?", (map_name,))
        conn.executemany("INSERT INTO map_rooms (map, name, floor, x, y, z) VALUES (?, ?, ?, ?, ?, ?)", clean)
        conn.commit()
    return len(clean)


def rooms_by_map() -> dict[str, list[dict]]:
    with _conn() as conn:
        _ensure_table(conn)
        rows = conn.execute("SELECT map, name, floor, x, y, z FROM map_rooms").fetchall()
    out: dict[str, list[dict]] = {}
    for r in rows:
        out.setdefault(r["map"], []).append({"name": r["name"], "floor": r["floor"],
                                             "x": r["x"], "y": r["y"], "z": r["z"]})
    return out


def room_of(rooms: list[dict], x: float, y: float, z: float, max_m: float = ROOM_MAX_M) -> Optional[str]:
    """The nearest labelled room on the same floor (within max_m), or None. `rooms` is one map's list."""
    best, best_d = None, max_m
    for r in rooms:
        if abs(r["z"] - z) > FLOOR_GAP:
            continue
        d = math.dist((r["x"], r["y"]), (x, y))
        if d < best_d:
            best, best_d = r["name"], d
    return best
