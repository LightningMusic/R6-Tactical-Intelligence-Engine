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
from pathlib import Path
from typing import Optional

from server.config import server_settings
from server.match_db import get_match_repo

MAX_SCATTER = 9000           # points sent to the labelling page (downsampled) — enough for a readable heatmap
ROOM_MAX_M = 8.0             # a position farther than this from every room on its floor is left unnamed
FLOOR_GAP = 1.6


def _conn():
    return get_match_repo().db.get_connection()


PIN_KINDS = ("room", "site", "stairs", "spawn", "hatch")


def _ensure_table(conn) -> None:
    conn.execute("""CREATE TABLE IF NOT EXISTS map_rooms (
        map   TEXT NOT NULL,
        name  TEXT NOT NULL,
        floor TEXT NOT NULL DEFAULT '',
        kind  TEXT NOT NULL DEFAULT 'room',
        x REAL NOT NULL, y REAL NOT NULL, z REAL NOT NULL,
        PRIMARY KEY (map, name))""")
    try:
        conn.execute("ALTER TABLE map_rooms ADD COLUMN kind TEXT NOT NULL DEFAULT 'room'")
    except Exception:
        pass        # column already there
    conn.execute("""CREATE TABLE IF NOT EXISTS map_structures (
        map TEXT NOT NULL, floor TEXT NOT NULL DEFAULT '', reinforced INTEGER NOT NULL DEFAULT 0,
        x REAL NOT NULL, y REAL NOT NULL, z REAL NOT NULL)""")
    # A floor-plan image (the host uploads one per map+floor, e.g. an r6maps screenshot) and how it's placed in
    # our world coordinates (transform set by the host dragging/scaling it to line up with the heatmap).
    conn.execute("""CREATE TABLE IF NOT EXISTS map_backdrops (
        map TEXT NOT NULL, floor TEXT NOT NULL, filename TEXT NOT NULL, transform_json TEXT,
        updated_at TEXT NOT NULL, PRIMARY KEY (map, floor))""")


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
            for p in pl["pts"][::2]:
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
                suggested.append({"name": _clean_site(site), "kind": "site", "x": round(c["x"], 1),
                                  "y": round(c["y"], 1), "z": round(c["z"], 1), "floor": _floor_of(c["z"], floors)})
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
        rows = conn.execute("SELECT name, floor, kind, x, y, z FROM map_rooms WHERE map = ? ORDER BY floor, name",
                            (map_name,)).fetchall()
    return [{"name": r["name"], "floor": r["floor"], "kind": r["kind"] or "room",
             "x": r["x"], "y": r["y"], "z": r["z"]} for r in rows]


def save_rooms(map_name: str, rooms: list[dict]) -> int:
    """Replace this map's rooms with the given list. Each room: {name, floor, kind, x, y, z}."""
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
        kind = str(r.get("kind") or "room").lower()
        seen.add(name.lower())
        clean.append((map_name, name, str(r.get("floor") or "")[:4], kind if kind in PIN_KINDS else "room", x, y, z))
        if len(clean) >= 200:
            break
    with _conn() as conn:
        _ensure_table(conn)
        conn.execute("DELETE FROM map_rooms WHERE map = ?", (map_name,))
        conn.executemany("INSERT INTO map_rooms (map, name, floor, kind, x, y, z) VALUES (?, ?, ?, ?, ?, ?, ?)", clean)
        conn.commit()
    return len(clean)


# ── Walls: every breakable surface, read from the stored replays (cached) ─────

def structures(map_name: str, rebuild: bool = False) -> dict:
    """The map's destructible surfaces (breakable walls, hatches, barricades) with a floor, and whether each one
    gets reinforced — read once from the stored replay packages and cached. Hard (concrete) walls aren't
    objects in the replay, so they still only show as the heatmap's empty gaps."""
    with _conn() as conn:
        _ensure_table(conn)
        have = conn.execute("SELECT COUNT(*) FROM map_structures WHERE map = ?", (map_name,)).fetchone()[0]
    if rebuild or not have:
        _build_structures(map_name)
    with _conn() as conn:
        rows = conn.execute("SELECT floor, reinforced, x, y, z FROM map_structures WHERE map = ?", (map_name,)).fetchall()
    return {"map": map_name, "walls": [{"floor": r["floor"], "reinforced": bool(r["reinforced"]),
                                        "x": r["x"], "y": r["y"], "z": r["z"]} for r in rows]}


def _build_structures(map_name: str, max_packages: int = 12) -> int:
    """Extract destructible surfaces from the map's stored replays and cache them. Dedups near-identical
    surfaces, assigns a floor, and marks the ones near a reinforcement spot as reinforceable."""
    import tempfile
    import zipfile
    from integration import walls as W
    from integration.replay_utility import read_rec
    from server.database import server_db
    from analysis import places

    with _conn() as conn:
        mids = [r[0] for r in conn.execute("SELECT match_id FROM matches WHERE map = ?", (map_name,))]
        rounds = _map_rounds(conn, map_name)
    floors = places.floors(rounds) if rounds else []

    # reinforcement spots already decoded and stored with the positions
    reinf = [(w["x"], w["y"], w["z"]) for r in rounds
             for w in ((r["positions"].get("walls") or {}).get("reinforcements") or [])
             if w.get("x") is not None]

    # match -> session -> package hash (the session/package bookkeeping is in the other DB)
    hashes: list[str] = []
    with server_db.get_connection() as sconn:
        for mid in mids:
            row = sconn.execute("SELECT session_id FROM server_parsed_matches WHERE match_id = ?", (mid,)).fetchone()
            if not row:
                continue
            pk = sconn.execute("SELECT package_hash FROM server_packages WHERE session_id = ?", (row[0],)).fetchone()
            if pk and pk[0] not in hashes:
                hashes.append(pk[0])

    surfaces: dict[tuple, list] = {}      # rounded (x,y,z) -> [x,y,z]
    for h in hashes[:max_packages]:
        pkg = server_settings.UPLOADS_DIR / f"{h}.r6session"
        if not pkg.exists():
            continue
        try:
            with tempfile.TemporaryDirectory() as tmp:
                with zipfile.ZipFile(pkg) as z:
                    recs = [n for n in z.namelist() if n.lower().endswith(".rec")]
                    for n in recs:
                        z.extract(n, tmp)
                        try:
                            for (x, y, zz) in W.map_objects(read_rec(Path(tmp) / n)).values():
                                key = (round(x * 1.5) / 1.5, round(y * 1.5) / 1.5, round(zz))
                                surfaces.setdefault(key, [x, y, zz])
                        except Exception:
                            continue
        except Exception as e:                                   # noqa: BLE001 - a bad package must not stop the rest
            print(f"[Rooms] structures: could not read {h[:12]} for {map_name}: {e}")

    rows = []
    for (x, y, z) in surfaces.values():
        floor = _floor_of(z, floors)
        reinforced = any(abs(rz - z) < FLOOR_GAP and math.dist((rx, ry), (x, y)) < 1.5 for rx, ry, rz in reinf)
        rows.append((map_name, floor, 1 if reinforced else 0, round(x, 2), round(y, 2), round(z, 2)))
    with _conn() as conn:
        _ensure_table(conn)
        conn.execute("DELETE FROM map_structures WHERE map = ?", (map_name,))
        conn.executemany("INSERT INTO map_structures (map, floor, reinforced, x, y, z) VALUES (?, ?, ?, ?, ?, ?)", rows)
        conn.commit()
    print(f"[Rooms] structures for {map_name}: {len(rows)} surfaces from {len(hashes[:max_packages])} packages.")
    return len(rows)


def rooms_by_map() -> dict[str, list[dict]]:
    with _conn() as conn:
        _ensure_table(conn)
        rows = conn.execute("SELECT map, name, floor, x, y, z FROM map_rooms").fetchall()
    out: dict[str, list[dict]] = {}
    for r in rows:
        out.setdefault(r["map"], []).append({"name": r["name"], "floor": r["floor"],
                                             "x": r["x"], "y": r["y"], "z": r["z"]})
    return out


# ── Floor-plan backdrop image per map+floor ──────────────────────────────────

import hashlib
import json
import re as _re
from datetime import datetime, timezone

BACKDROP_EXT = {"png": "png", "jpg": "jpg", "jpeg": "jpg", "webp": "webp"}
MAX_BACKDROP_BYTES = 10 * 1024 * 1024
_FLOOR_RE = _re.compile(r"^[A-Za-z0-9]{0,4}$")


def _images_dir() -> Path:
    d = server_settings.DATA_DIR / "map_images"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def save_backdrop(map_name: str, floor: str, data: bytes, ext: str) -> dict:
    """Store a floor image for this map+floor (replacing any existing one). Returns the saved record."""
    if not _FLOOR_RE.match(floor or ""):
        raise ValueError("Bad floor.")
    ext = BACKDROP_EXT.get((ext or "").lower().lstrip("."))
    if not ext:
        raise ValueError("Image must be a PNG, JPG or WEBP.")
    if not data or len(data) > MAX_BACKDROP_BYTES:
        raise ValueError(f"Image must be between 1 byte and {MAX_BACKDROP_BYTES // (1024 * 1024)} MB.")
    name = f"{hashlib.sha256(data).hexdigest()[:20]}.{ext}"
    (_images_dir() / name).write_bytes(data)
    with _conn() as conn:
        _ensure_table(conn)
        old = conn.execute("SELECT filename FROM map_backdrops WHERE map = ? AND floor = ?", (map_name, floor)).fetchone()
        conn.execute("""INSERT INTO map_backdrops (map, floor, filename, transform_json, updated_at)
                        VALUES (?, ?, ?, NULL, ?)
                        ON CONFLICT(map, floor) DO UPDATE SET filename = excluded.filename, transform_json = NULL,
                        updated_at = excluded.updated_at""", (map_name, floor, name, _now_iso()))
        conn.commit()
    if old and old[0] and old[0] != name:
        _maybe_remove_image(old[0])
    return {"floor": floor, "has_image": True, "transform": None}


def get_backdrop(map_name: str, floor: str) -> Optional[dict]:
    with _conn() as conn:
        _ensure_table(conn)
        r = conn.execute("SELECT filename, transform_json FROM map_backdrops WHERE map = ? AND floor = ?",
                         (map_name, floor)).fetchone()
    if not r or not (_images_dir() / r["filename"]).exists():
        return None
    return {"floor": floor, "has_image": True,
            "transform": json.loads(r["transform_json"]) if r["transform_json"] else None}


def backdrop_floors(map_name: str) -> list[str]:
    with _conn() as conn:
        _ensure_table(conn)
        return [r[0] for r in conn.execute("SELECT floor FROM map_backdrops WHERE map = ?", (map_name,))]


def backdrop_bytes(map_name: str, floor: str) -> Optional[tuple[bytes, str]]:
    with _conn() as conn:
        _ensure_table(conn)
        r = conn.execute("SELECT filename FROM map_backdrops WHERE map = ? AND floor = ?", (map_name, floor)).fetchone()
    if not r:
        return None
    path = _images_dir() / r["filename"]
    if not path.exists():
        return None
    media = {"png": "image/png", "jpg": "image/jpeg", "webp": "image/webp"}.get(path.suffix.lstrip("."), "image/png")
    return path.read_bytes(), media


def set_backdrop_transform(map_name: str, floor: str, transform: dict) -> bool:
    if not isinstance(transform, dict):
        raise ValueError("transform must be an object.")
    keep = {}
    for k in ("scale", "rot", "tx", "ty", "opacity"):
        try:
            keep[k] = float(transform.get(k))
        except (TypeError, ValueError):
            raise ValueError(f"transform.{k} must be a number.")
    keep["flip"] = bool(transform.get("flip"))
    with _conn() as conn:
        _ensure_table(conn)
        cur = conn.execute("UPDATE map_backdrops SET transform_json = ?, updated_at = ? WHERE map = ? AND floor = ?",
                           (json.dumps(keep), _now_iso(), map_name, floor))
        conn.commit()
    return cur.rowcount > 0


def delete_backdrop(map_name: str, floor: str) -> bool:
    with _conn() as conn:
        _ensure_table(conn)
        r = conn.execute("SELECT filename FROM map_backdrops WHERE map = ? AND floor = ?", (map_name, floor)).fetchone()
        cur = conn.execute("DELETE FROM map_backdrops WHERE map = ? AND floor = ?", (map_name, floor))
        conn.commit()
    if r and r[0]:
        _maybe_remove_image(r[0])
    return cur.rowcount > 0


def _maybe_remove_image(filename: str) -> None:
    """Delete an image file only when no backdrop row still references it (same image can be shared)."""
    with _conn() as conn:
        still = conn.execute("SELECT 1 FROM map_backdrops WHERE filename = ? LIMIT 1", (filename,)).fetchone()
    if not still:
        try:
            (_images_dir() / filename).unlink(missing_ok=True)
        except Exception:
            pass


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
