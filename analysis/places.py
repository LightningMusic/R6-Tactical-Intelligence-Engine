"""
Places on a map, learned from where players actually were (the replay has coordinates but no room names).

Each round given here is {"site": "2F Armory Lockers, 2F Archives", "positions": <integration.positions output>}.
  floors(rounds)                 -> [(height, "2F"), ...]: peaks of the height histogram; a site's prefix ("2F")
                                    names the level most of its defenders stand on at the end of prep
  site_model(rounds, floors)     -> {site: {"x", "y", "z", "r"}}: where defenders set up each site
  where(model, floors, x, y, z)  -> "2F, at Armory Lockers / Archives" | "1F, 14 m from Bathroom / Tellers"

Checked leave-one-round-out on 53 rounds (2026-10-06..08): the site of 15/15 plants and of 48/49 defender
setups came out right; floors were named correctly on all four maps.
"""
from __future__ import annotations

import collections
import heapq
import math
import statistics
from typing import Optional

from integration.positions import at

SETUP_TIMES = (38.0, 42.0, 46.0)     # end of prep: defenders are set up on the site
FLOOR_GAP = 1.6
ORDER = ["B", "1F", "2F", "3F", "4F"]


def _defenders(pos: dict) -> list[str]:
    return [n for n, p in (pos.get("players") or {}).items() if p.get("team") == pos.get("def_team")]


def _levels(zs: list[float]) -> list[float]:
    c = collections.Counter(round(z * 2) / 2 for z in zs)
    peaks: list[float] = []
    for z, n in sorted(c.items(), key=lambda kv: -kv[1]):
        if n < 0.01 * len(zs):
            break
        if all(abs(z - p) >= FLOOR_GAP for p in peaks):
            peaks.append(z)
    return sorted(peaks)


def floors(rounds: list[dict]) -> list[tuple[float, str]]:
    zs = [p[3] for r in rounds for pl in (r["positions"].get("players") or {}).values() for p in pl["pts"][::2]]
    if not zs:
        return []
    lv = _levels(zs)
    by_prefix: dict[str, collections.Counter] = collections.defaultdict(collections.Counter)
    for r in rounds:
        prefix = (r.get("site") or "").split(" ")[0]
        if prefix not in ORDER:
            continue
        for n in _defenders(r["positions"]):
            p = at(r["positions"]["players"][n]["pts"], 42.0)
            if p:
                by_prefix[prefix][min(lv, key=lambda l: abs(l - p[3]))] += 1
    assigned: dict[float, str] = {}
    for prefix, votes in sorted(by_prefix.items(), key=lambda kv: -max(kv[1].values())):
        for z, n in votes.most_common():
            if z not in assigned and n >= 3:
                assigned[z] = prefix
                break
    out = [(z, assigned.get(z)) for z in lv]
    for i, (z, name) in enumerate(out):
        if name:
            continue
        below = next((nn for _, nn in reversed(out[:i]) if nn in ORDER), None)
        above = next((nn for _, nn in out[i + 1:] if nn in ORDER), None)
        if below and ORDER.index(below) + 1 < len(ORDER) and (not above or ORDER.index(above) > ORDER.index(below) + 1):
            out[i] = (z, ORDER[ORDER.index(below) + 1] + "?")
        elif above and ORDER.index(above) > 0:
            out[i] = (z, ORDER[ORDER.index(above) - 1] + "?")
        else:
            out[i] = (z, f"level {z:g}")
    return out


def floor_of(fl: list[tuple[float, str]], z: float) -> tuple[float, str]:
    return min(fl, key=lambda f: abs(f[0] - z)) if fl else (z, "?")


def _setup_points(r: dict, fl) -> list[tuple[float, float, float]]:
    prefix = (r.get("site") or "").split(" ")[0]
    pts = []
    for n in _defenders(r["positions"]):
        track = r["positions"]["players"][n]["pts"]
        for t in SETUP_TIMES:
            p = at(track, t)
            if p and floor_of(fl, p[3])[1].rstrip("?") == prefix:
                pts.append((p[1], p[2], p[3]))
    return pts


def site_model(rounds: list[dict], fl) -> dict[str, dict]:
    by_site: dict[str, list] = collections.defaultdict(list)
    for r in rounds:
        if r.get("site"):
            by_site[r["site"]].extend(_setup_points(r, fl))
    model = {}
    for site, pts in by_site.items():
        if not pts:
            continue
        best = max(pts, key=lambda a: sum(math.dist(a[:2], b[:2]) < 6 for b in pts))
        near = [b for b in pts if math.dist(best[:2], b[:2]) < 9]
        c = tuple(statistics.median(b[k] for b in near) for k in range(3))
        d = sorted(math.dist(c[:2], p[:2]) for p in pts)
        model[site] = {"x": c[0], "y": c[1], "z": c[2], "r": d[int(len(d) * 0.75)], "n": len(pts)}
    return model


def nearest_site(model, fl, x, y, z) -> tuple[Optional[float], Optional[str]]:
    fz = floor_of(fl, z)[0]
    cands = [(math.dist((x, y), (s["x"], s["y"])), name) for name, s in model.items()
             if abs(floor_of(fl, s["z"])[0] - fz) < 0.1]
    return min(cands) if cands else (None, None)


def short_site(site: str) -> str:
    return " / ".join(part.split(" ", 1)[1] if " " in part else part for part in site.split(", "))


def where(model, fl, x, y, z) -> str:
    name = floor_of(fl, z)[1].rstrip("?")
    d, site = nearest_site(model, fl, x, y, z)
    if site is None:
        return name
    if d <= model[site]["r"]:
        return f"{name}, at {short_site(site)}"
    return f"{name}, {d:.0f} m from {short_site(site)}"


class WalkMap:
    """Walking distance, from a walkable map learned from every path walked on the map.

    Nobody walks through an unbreakable or reinforced wall, so every spot anyone stood on is floor and every
    opening anyone passed through (door, stairs, hatch, an opened soft wall) is a connection: two players 1 m
    apart on either side of a solid wall are a long walk apart. Limits: a soft wall opened in any stored round
    counts as open in all of them, and this is walking, not line of sight (a trade needs a line of sight,
    which can run through a doorway or murder hole; straight-line distance on the same floor matched real
    trades better, 16% vs 4%, than walking distance did, 13% vs 5%, over 310 deaths)."""

    CELL = 0.25            # finer than a wall is thick: a ~0.3 m wall must stay a gap
    STEP_MAX = 3.0         # samples further apart than this weren't walked (vault, rappel, glitch)

    def __init__(self, fl: list[tuple[float, str]]):
        self.levels = [z for z, _ in fl]
        self.cells: set[tuple[int, int, int]] = set()
        self.links: dict[tuple, set] = collections.defaultdict(set)

    def level(self, z: float) -> int:
        return min(range(len(self.levels)), key=lambda i: abs(self.levels[i] - z)) if self.levels else 0

    def _key(self, x, y, z):
        return (self.level(z), int(round(x / self.CELL)), int(round(y / self.CELL)))

    def add_path(self, pts: list) -> None:
        """pts: stored samples [t, x, y, z, yaw] in time order."""
        for a, b in zip(pts, pts[1:]):
            if b[0] - a[0] > 0.8:
                continue
            d = math.dist(a[1:3], b[1:3])
            if d > self.STEP_MAX:
                continue
            la, lb = self.level(a[3]), self.level(b[3])
            if la == lb:
                n = max(1, int(d / (self.CELL / 2)))
                for k in range(n + 1):
                    self.cells.add((la, int(round((a[1] + (b[1] - a[1]) * k / n) / self.CELL)),
                                    int(round((a[2] + (b[2] - a[2]) * k / n) / self.CELL))))
            else:
                ka, kb = self._key(*a[1:4]), self._key(*b[1:4])
                self.cells.update((ka, kb))
                self.links[ka].add(kb)
                self.links[kb].add(ka)

    def _nearest(self, x, y, z, reach=6):
        l, i, j = self._key(x, y, z)
        best = None
        for di in range(-reach, reach + 1):
            for dj in range(-reach, reach + 1):
                if (l, i + di, j + dj) in self.cells and (best is None or di * di + dj * dj < best[0]):
                    best = (di * di + dj * dj, (l, i + di, j + dj))
        return best[1] if best else None

    def distance(self, a, b, limit: float = 60.0) -> float:
        """Metres to walk from a to b, each (x, y, z); inf when not connected within `limit`."""
        s, g = self._nearest(*a), self._nearest(*b)
        if s is None or g is None:
            return math.inf
        lim, diag = limit / self.CELL, math.sqrt(2)
        dist = {s: 0.0}
        heap = [(0.0, s)]
        while heap:
            d, c = heapq.heappop(heap)
            if c == g:
                return d * self.CELL
            if d > dist.get(c, math.inf) or d > lim:
                continue
            l, i, j = c
            steps = [((l, i + di, j + dj), diag if di and dj else 1.0) for di in (-1, 0, 1) for dj in (-1, 0, 1) if di or dj]
            steps += [(n, 3.0 / self.CELL) for n in self.links.get(c, ())]
            for n, w in steps:
                if n in self.cells and d + w < dist.get(n, math.inf):
                    dist[n] = d + w
                    heapq.heappush(heap, (d + w, n))
        return math.inf


def walk_map(rounds: list[dict], fl) -> WalkMap:
    wm = WalkMap(fl)
    for r in rounds:
        for pl in (r["positions"].get("players") or {}).values():
            wm.add_path(pl["pts"])
    return wm


WALL_SAME_M = 1.2            # reinforcements this close (same floor) are on the same wall
USUAL_WALL_SHARE = 0.5       # reinforced in at least this share of a site's rounds: one of its usual walls
USUAL_WALL_ROUNDS = 3        # rounds on a site needed before anything is "usual" there


def wall_spots(rounds: list[dict]) -> dict[str, dict]:
    """site -> {"rounds": n, "walls": [{"x", "y", "z", "rounds": k}]}: the walls defenders reinforce on each
    site and in how many of its n stored rounds, from the reinforcement spots in the positions' "walls" (any
    team: a site's standard walls are the same whoever defends it)."""
    by_site: dict[str, list[list[tuple]]] = collections.defaultdict(list)
    for r in rounds:
        w = (r.get("positions") or {}).get("walls")
        spots = [(s["x"], s["y"], s["z"]) for s in (w or {}).get("reinforcements") or [] if s.get("x") is not None]
        if r.get("site") and spots:
            by_site[r["site"]].append(spots)
    out = {}
    for site, per_round in by_site.items():
        walls: list[dict] = []
        for k, spots in enumerate(per_round):
            for x, y, z in spots:
                c = next((c for c in walls if math.dist((c["x"], c["y"]), (x, y)) < WALL_SAME_M
                          and abs(c["z"] - z) < FLOOR_GAP), None)
                if c is None:
                    c = {"x": x, "y": y, "z": z, "n": 0, "seen": set()}
                    walls.append(c)
                c["x"] = (c["x"] * c["n"] + x) / (c["n"] + 1)
                c["y"] = (c["y"] * c["n"] + y) / (c["n"] + 1)
                c["n"] += 1
                c["seen"].add(k)
        out[site] = {"rounds": len(per_round),
                     "walls": sorted(({"x": round(c["x"], 2), "y": round(c["y"], 2), "z": c["z"], "rounds": len(c["seen"])}
                                      for c in walls), key=lambda c: -c["rounds"])}
    return out


def usual_walls(spots: dict[str, dict], site: str) -> list[dict]:
    """The walls reinforced in at least USUAL_WALL_SHARE of the site's stored rounds ([] with too few rounds)."""
    s = spots.get(site)
    if not s or s["rounds"] < USUAL_WALL_ROUNDS:
        return []
    return [w for w in s["walls"] if w["rounds"] / s["rounds"] >= USUAL_WALL_SHARE]


def area(model, fl, x, y, z) -> str:
    """A coarse place for grouping: on a site, near one (within 20 m), or just the floor."""
    name = floor_of(fl, z)[1].rstrip("?")
    d, site = nearest_site(model, fl, x, y, z)
    if site is None:
        return name
    if d <= model[site]["r"]:
        return f"{name} at {short_site(site)}"
    if d <= 20:
        return f"{name} near {short_site(site)}"
    return f"{name}, away from the sites"
