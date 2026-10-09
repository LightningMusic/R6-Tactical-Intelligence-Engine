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
