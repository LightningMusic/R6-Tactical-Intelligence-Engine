"""
Drones from a round's .rec replay: the movement packets of drones (flags' low byte 0xC0 / 0xE0, see positions.py)
timed with the position decoder's clock.

Who threw a drone during the action phase: it first appears right next to the thrower. Over 141 such drones
(2026-10-07/08) 128 started within 3 m of exactly one living player (median 0.37 m). Prep drones can't be tied to a
player (they start outside, ~30 m from anyone, and nothing in the replay links them), so they are only counted.
Experiments: R6PosDecode-experiment/p70-p75, FINDINGS.md "Drones".
"""
from __future__ import annotations

import collections
import math
import struct
from typing import Any, Callable, Optional

from integration.positions import FC, PREP_SEC, _ref_at, at

DRONE = (0xC0, 0xE0)
MIN_SAMPLES = 20
OWNER_M = 3.0            # a drone thrown in the action phase starts this close to its thrower
CLEAR_RATIO = 2.0        # ...and the next-nearest player must be this much further away
FLOOR_M = 2.5


def drone_tracks(data: bytes, t_at: Callable[[int], float]) -> dict[int, list[tuple[float, float, float, float]]]:
    tracks: dict[int, list] = collections.defaultdict(list)
    i = data.find(FC)
    while i != -1:
        if i + 20 <= len(data):
            f = struct.unpack_from("<H", data, i + 4)[0]
            if (f & 0xFF) in DRONE and f & 0x0100:
                ref = _ref_at(data, i)
                if ref is not None:
                    x, y, z = struct.unpack_from("<3f", data, i + 6)
                    if all(math.isfinite(v) and abs(v) < 500 for v in (x, y, z)) and z > -50:
                        tracks[ref].append((t_at(i), x, y, z))
        i = data.find(FC, i + 1)
    return {r: p for r, p in tracks.items() if len(p) >= MIN_SAMPLES}


def thrower(positions: dict, t: float, x: float, y: float, z: float) -> Optional[str]:
    """The living player the drone appeared next to, when that is clear."""
    died = positions.get("died") or {}
    cands = []
    for name, pl in (positions.get("players") or {}).items():
        if died.get(name, 1e9) <= t:
            continue
        q = at(pl.get("pts") or [], t)
        if q is not None:
            cands.append((math.dist((x, y), q[1:3]) + (50.0 if abs(q[3] - z) > FLOOR_M else 0.0), name))
    cands.sort()
    if not cands or cands[0][0] > OWNER_M:
        return None
    if len(cands) > 1 and cands[1][0] < CLEAR_RATIO * max(cands[0][0], 1.0):
        return None
    return cands[0][1]


def decode_drones(data: bytes, positions: Optional[dict], t_at: Optional[Callable[[int], float]]) -> Optional[list[dict[str, Any]]]:
    """[{"t", "prep", "who" (or None), "path_m"}] one per drone, in order of appearance."""
    if t_at is None or not positions:
        return None
    out = []
    for p in drone_tracks(data, t_at).values():
        t0, x, y, z = p[0]
        path = sum(math.dist(a[1:3], b[1:3]) for a, b in zip(p, p[1:]))
        prep = t0 < PREP_SEC - 5
        out.append({"t": round(t0, 1), "prep": prep, "path_m": round(path, 1),
                    "who": None if prep else thrower(positions, t0, x, y, z)})
    return sorted(out, key=lambda d: d["t"])
