"""
Placed gadgets from a round's .rec, by spawn-time proximity.

A deployed gadget entity shows up right where its owner is standing the instant they place it --
the same reason a thrown drone appears next to its thrower. So instead of decoding every gadget's
own entity signature one by one, we find the static placed entities and ask: who was within ~2.5 m
on the same floor the moment this one appeared? That player placed it, and their OPERATOR is the
gadget type (Mute -> jammer, Valkyrie -> camera, Lesion -> Gu mine, ...), read off the roster.

Validated across real rounds (2026-11-27): the attributions land on the right operators, on-site,
spread through the round, at 0.2-2 m. It covers what a player sets down or sticks nearby; a gadget
thrown far from its owner (some grenades) won't match and is left out rather than guessed. A None
owner means "couldn't place it", never "nobody's".
"""
from __future__ import annotations

import collections
import math
import re
import struct
from typing import Any, Callable, Optional

from integration.positions import FC, _ref_at, at

OWNER_M = 2.5          # a placed gadget starts this close to whoever set it
FLOOR_M = 2.5          # ...and on the same floor
MIN_SAMPLES = 3        # fewer packets than this is noise, not a settled placement
MAX_SAMPLES = 400
SPREAD_M = 2.0         # it has to stay put (a placement, not a roaming entity)

# operator (lowercase) -> what they place. Operators that don't set a gadget down near themselves
# (pure throwers, scanners, shields they carry) are absent and fall back to the operator's name.
OP_GADGET = {
    "mute": "Jammer", "bandit": "Battery", "kapkan": "EDD trap", "frost": "Welcome Mat",
    "lesion": "Gu mine", "ela": "Grzmot mine", "fenrir": "Dread mine", "valkyrie": "Black Eye cam",
    "maestro": "Evil Eye", "echo": "Yokai drone", "castle": "Barricade", "mira": "Black Mirror",
    "goyo": "Volcan shield", "aruni": "Surya gate", "melusi": "Banshee", "thorn": "Razorbloom shell",
    "azami": "Kiba barrier", "thunderbird": "Kona station", "wamai": "Mag-NET", "jager": "ADS",
    "jäger": "ADS", "kaid": "Rtila claw", "tachanka": "shield / fire", "osa": "Talon shield",
    "solis": "scan",
    # attackers who leave something placed
    "thermite": "Exothermic charge", "hibana": "X-KAIROS pellet", "ace": "S.E.L.M.A. charge",
    "maverick": "breach torch", "thatcher": "EMP", "twitch": "Shock Drone", "fuze": "Cluster charge",
    "flores": "RCE drone", "nomad": "Airjab", "gridlock": "Trax Stinger", "kali": "LV explosive",
    "ying": "Candela", "zofia": "impact / concussion", "sledge": "breach",
}


def _norm(s: Any) -> str:
    return re.sub(r"[^a-z0-9]", "", str(s or "").lower())


def placed_entities(data: bytes, t_at: Callable[[int], float]) -> list[tuple[int, Optional[float], float, float, float]]:
    """(entity ref, spawn time, x, y, z) for every entity that appears at a fixed spot and stays."""
    first: dict[int, tuple[int, float, float, float]] = {}
    pts: dict[int, list[tuple[float, float, float]]] = collections.defaultdict(list)
    i = data.find(FC)
    while i != -1:
        if i + 20 <= len(data):
            f = struct.unpack_from("<H", data, i + 4)[0]
            if f & 0x0100:
                ref = _ref_at(data, i)
                if ref is not None:
                    x, y, z = struct.unpack_from("<3f", data, i + 6)
                    if all(math.isfinite(v) and abs(v) < 500 for v in (x, y, z)) and z > -50:
                        pts[ref].append((x, y, z))
                        if ref not in first:
                            first[ref] = (i, x, y, z)
        i = data.find(FC, i + 1)
    out = []
    for ref, ps in pts.items():
        if not (MIN_SAMPLES <= len(ps) <= MAX_SAMPLES):
            continue
        xs = [p[0] for p in ps]
        ys = [p[1] for p in ps]
        if (max(xs) - min(xs)) >= SPREAD_M or (max(ys) - min(ys)) >= SPREAD_M:
            continue
        off, x, y, z = first[ref]
        out.append((ref, t_at(off), x, y, z))
    return out


def decode_gadgets(data: bytes, positions: Optional[dict], t_at: Optional[Callable[[int], float]],
                   op_of: Optional[dict] = None) -> Optional[list[dict[str, Any]]]:
    """[{"t","x","y","z","who" (or None),"op","type"}] one per placed gadget, by spawn proximity.
    op_of maps norm(username) -> operator name (from the round's roster) so `type` can be named."""
    if t_at is None or not positions:
        return None
    players = positions.get("players") or {}
    op_of = op_of or {}
    out = []
    for ref, t, x, y, z in placed_entities(data, t_at):
        if t is None or t < 1:
            continue
        best = None
        for name, pl in players.items():
            q = at(pl.get("pts") or [], t)
            if q is None:
                continue
            if abs(q[3] - z) > FLOOR_M:
                continue
            d = math.hypot(q[1] - x, q[2] - y)
            if best is None or d < best[0]:
                best = (d, name)
        if not best or best[0] > OWNER_M:
            continue
        who = best[1]
        op = op_of.get(_norm(who)) or ""
        out.append({"t": round(t, 1), "x": round(x, 1), "y": round(y, 1), "z": round(z, 1),
                    "who": who, "op": op, "type": OP_GADGET.get(op.lower(), op or "gadget")})
    return sorted(out, key=lambda g: g["t"])
