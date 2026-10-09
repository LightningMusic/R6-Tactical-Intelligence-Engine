"""
Wall state from a round's .rec replay: which walls the defenders reinforced (who, when, where) and what the
attackers' breaching gadgets opened.

Three things in the replay (decompressed with replay_utility.read_rec):

  * The reinforcement pool. The defending team's entity carries property 0xF820DE67: 10 at the start of the
    round, one less each time a reinforcement is started, one more when one is cancelled. It sits in the event
    stream, which the round clock (one tick a second, the first 45 are prep) times.
  * Who: putting up a reinforcement takes about 5 s with the weapon away, so it is the defender whose "weapon
    ready" flag (the one plants are read from) drops just after the pool does and stays down 4 to 7.5 s.
    Where: that defender's position from the movement stream plus REACH_M along their facing.
  * The destruction stream. `62 73 85 FE` packets introduce fixed level objects (8-byte level id, position,
    rotation, type); each later change to one is a `60 73 85 FE` packet keyed by that id, carrying the entity that
    caused it and a resource id for the weapon or gadget. Resource ids are the same in every replay, so they
    name the gadget (GADGETS). A charge leaves these records only when it goes off.

Checked on 37 rounds over four maps (2026-10-07/08): the pool started at 10 in every round; 88% of
reinforcements had a clear owner; the same walls came out round after round (within ~0.5 m); 8 of 11 Thermite
charges hit a wall decoded as reinforced in that round. Notes: R6PosDecode-experiment/FINDINGS.md.
"""
from __future__ import annotations

import bisect
import collections
import math
import struct
from typing import Any, Callable, Optional

from integration import replay_utility as RU
from integration.positions import CLOCK, FC, PREP_SEC

POOL = struct.pack("<I", 0xF820DE67) + b"\x04"
POOL_START = 10
OBJECT = bytes([0x62, 0x73, 0x85, 0xFE])
SURFACE = 0x351CA56E                 # object type of destructible walls, floors and barricades
EXPLOSIVE = 0xFFFFFFFF               # an update's count field for explosions
REACH_M = 0.8                        # the wall is this far in front of whoever reinforced it
DOWN_S = (4.0, 7.5)                  # how long the weapon stays away while reinforcing
AFTER_BYTES = (-300, 1500)           # where the weapon goes down, relative to the pool's drop
OWNER_SEARCH = 512
FORMAT_VERSION = 1

# resource id -> (operator, kind). Matched over 35 rounds: each appears only in rounds where that operator played.
GADGETS: dict[int, tuple[str, str]] = {
    0x094688197C: ("Thermite", "hard"),
    0x38FD1CF6EE: ("Ace", "hard"),
    0x4186FA2EF1: ("Ace", "hard"),
    0x5C9F2F26CD: ("Ram", "soft"),
    0x07F1A5C260: ("Ash", "soft"),
    0x07F1A5C286: ("Sledge", "soft"),
    0x07F1A5C267: ("Fuze", "soft"),
    0x08D34EBCBE: ("Fuze", "soft"),
    0x108F84DCB1: ("Zofia", "soft"),
}


def ticks(data: bytes) -> list[int]:
    """Offsets of the round clock's ticks, from the first non-zero one (prep start)."""
    out: list[int] = []
    started = False
    i = data.find(CLOCK)
    while i != -1:
        if i + 9 <= len(data) and (started or struct.unpack_from("<I", data, i + 5)[0]):
            started = True
            out.append(i)
        i = data.find(CLOCK, i + 1)
    return out


def event_seconds(tk: list[int], off: int) -> float:
    """Seconds since prep start of an event-stream offset (between ticks: by bytes)."""
    k = bisect.bisect_right(tk, off) - 1
    if k < 0:
        return 0.0
    if k + 1 < len(tk) and tk[k + 1] > tk[k]:
        return k + (off - tk[k]) / (tk[k + 1] - tk[k])
    return float(k)


def _owner(data: bytes, i: int) -> Optional[int]:
    """The entity whose record holds the property at i: [marker][entity][0 0 0 0] before it (the snapshot
    writes the team with a creation marker, the event stream with 0x23)."""
    for j in range(i - 1, max(0, i - OWNER_SEARCH), -1):
        if (data[j] in RU.RECORD_MARKERS and j + 9 <= len(data) and data[j + 4] >= 0xF0
                and data[j + 5:j + 9] == b"\0\0\0\0"):
            return struct.unpack_from("<I", data, j + 1)[0]
    return None


def pool_series(data: bytes) -> list[tuple[int, int]]:
    """[(offset, value)] of the defending team's reinforcement pool, in stream order."""
    by_owner: dict[Optional[int], list[tuple[int, int]]] = collections.defaultdict(list)
    i = data.find(POOL)
    while i != -1:
        if i + 9 <= len(data):
            v = struct.unpack_from("<I", data, i + 5)[0]
            if v <= POOL_START:
                by_owner[_owner(data, i)].append((i, v))
        i = data.find(POOL, i + 1)
    full = [s for s in by_owner.values() if s and s[0][1] == POOL_START]
    return max(full, key=len) if full else []


def _defenders(dissect: dict) -> set[str]:
    teams = dissect.get("teams") or []
    out = set()
    for p in dissect.get("players") or []:
        ti = p.get("teamIndex")
        if isinstance(ti, int) and 0 <= ti < len(teams) and teams[ti].get("role") == "Defense" and p.get("username"):
            out.add(p["username"])
    return out


def reinforcers(data: bytes, dissect: dict, tk: list[int], series: list[tuple[int, int]]) -> list[dict[str, Any]]:
    """[{"t", "who" or None}] for every drop of the pool."""
    defs = _defenders(dissect)
    names = [p.get("username") for p in dissect.get("players") or [] if p.get("username")]
    ctrl = {c: u for c, u in RU.find_controllers(data, names).items() if u in defs}
    downs = []
    for user, states in RU.ready_states(data, ctrl).items():
        for k, (off, ready) in enumerate(states):
            if ready:
                continue
            up = next((o for o, r in states[k + 1:] if r), None)
            if up is not None:
                downs.append((off, user, event_seconds(tk, up) - event_seconds(tk, off)))
    out = []
    for (_, before), (off, after) in zip(series, series[1:]):
        if after != before - 1:
            continue
        cands = sorted(((d - off, u) for d, u, dur in downs
                        if AFTER_BYTES[0] <= d - off <= AFTER_BYTES[1] and DOWN_S[0] <= dur <= DOWN_S[1]),
                       key=lambda c: abs(c[0]))
        who = None
        if cands and (len(cands) == 1 or cands[1][1] == cands[0][1] or abs(cands[1][0]) > 2 * abs(cands[0][0])):
            who = cands[0][1]
        out.append({"t": round(event_seconds(tk, off), 1), "who": who})
    return out


def wall_spot(pts: list, t: float) -> Optional[tuple[float, float, float]]:
    """Where the wall was: the reinforcer's last known position by t + 2 s (they stand still while doing it)
    plus REACH_M along their facing."""
    if not pts:
        return None
    k = bisect.bisect_right([p[0] for p in pts], t + 2.0)
    if k == 0:
        return None
    p = pts[k - 1]
    yaw = next((q[4] for q in reversed(pts[:k]) if q[4] is not None), None)
    if yaw is None:
        return None
    a = math.radians(yaw)
    return (round(p[1] + REACH_M * math.cos(a), 2), round(p[2] + REACH_M * math.sin(a), 2), round(p[3], 2))


def map_objects(data: bytes) -> dict[int, tuple[float, float, float]]:
    """level id -> position of every destructible surface the replay introduces."""
    out: dict[int, tuple[float, float, float]] = {}
    i = data.find(OBJECT)
    while i != -1:
        if i + 61 <= len(data) and struct.unpack_from("<I", data, i + 57)[0] == SURFACE:
            x, y, z = struct.unpack_from("<3f", data, i + 16)
            if all(math.isfinite(v) and abs(v) < 1000 for v in (x, y, z)):
                out.setdefault(struct.unpack_from("<Q", data, i + 4)[0], (x, y, z))
        i = data.find(OBJECT, i + 1)
    return out


def object_updates(data: bytes, objects: dict[int, tuple]) -> list[tuple[int, int, int, int, int]]:
    """[(offset, level id, cause entity, resource id, count)] for every change to a known surface."""
    out = []
    i = data.find(FC)
    while i != -1:
        if 12 <= i and i + 170 <= len(data):
            oid = struct.unpack_from("<Q", data, i - 12)[0]
            if oid in objects:
                for j in range(i + 6, i + 140):
                    if data[j + 3] == 0xF0 and data[j + 4:j + 16] == b"\0" * 12:
                        cause = struct.unpack_from("<I", data, j)[0]
                        res = struct.unpack_from("<Q", data, j + 16)[0]
                        n = struct.unpack_from("<I", data, j + 24)[0]
                        out.append((i, oid, cause, res, n))
                        break
        i = data.find(FC, i + 1)
    return out


def breaches(data: bytes, t_at: Callable[[int], float]) -> tuple[list[dict], list[list]]:
    """(known gadgets: [{"t", "op", "kind", "x", "y", "z", "objects"}] one per charge/use,
    other explosions: [[resource hex, t, x, y, z, objects]] kept so new gadgets can be named later)."""
    objects = map_objects(data)
    groups: dict[tuple[int, int], list[tuple[int, int]]] = collections.defaultdict(list)
    for off, oid, cause, res, n in object_updates(data, objects):
        if res in GADGETS or n == EXPLOSIVE:
            groups[(res, cause)].append((off, oid))
    known, other = [], []
    for (res, _cause), hits in groups.items():
        oids = {oid for _, oid in hits}
        pts = [objects[o] for o in oids]
        t = round(t_at(min(off for off, _ in hits)), 1)
        x = round(sum(p[0] for p in pts) / len(pts), 2)
        y = round(sum(p[1] for p in pts) / len(pts), 2)
        z = round(min(p[2] for p in pts), 2)
        if res in GADGETS:
            op, kind = GADGETS[res]
            known.append({"t": t, "op": op, "kind": kind, "x": x, "y": y, "z": z, "objects": len(oids)})
        else:
            other.append([f"{res:X}", t, x, y, z, len(oids)])
    known.sort(key=lambda b: b["t"])
    other.sort(key=lambda b: b[1])
    return known, other


def decode_walls(data: bytes, dissect: dict, positions: Optional[dict],
                 t_at: Optional[Callable[[int], float]]) -> Optional[dict[str, Any]]:
    """{"v", "start", "end", "reinforcements": [{"t", "who", "x", "y", "z"}], "breaches": [...], "other": [...]}
    with t in seconds since prep began (prep is the first 45). None when the replay has no pool to read."""
    tk = ticks(data)
    series = pool_series(data)
    if not tk or not series:
        return None
    players = (positions or {}).get("players") or {}
    reinf = []
    for r in reinforcers(data, dissect, tk, series):
        spot = wall_spot(players.get(r["who"], {}).get("pts") or [], r["t"]) if r["who"] else None
        reinf.append({"t": r["t"], "who": r["who"], "x": spot[0] if spot else None,
                      "y": spot[1] if spot else None, "z": spot[2] if spot else None})
    known, other = breaches(data, t_at) if t_at is not None else ([], [])
    return {"v": FORMAT_VERSION, "start": series[0][1], "end": series[-1][1], "reinforcements": reinf,
            "breaches": known, "other": other}


def clock_left(t: float) -> str:
    """The on-screen clock at t seconds since prep began: m:ss left in the action phase."""
    if t < PREP_SEC:
        return f"{int(t)} s into prep"
    left = max(0, int(round(180 - (t - PREP_SEC))))
    return f"{left // 60}:{left % 60:02d} left"
