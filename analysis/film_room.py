"""
Spatial "film room": every one of a player's (or the team's) deaths placed in the round, read from the
replay's movement traces — where they died, the floor, the path they walked in the seconds before, the
enemy who killed them and the line that kill came along, and the nearest living teammate at that instant.

This is the data behind the /film page and the Chalet-style death maps. It is a pure transform over an
already-loaded match (the model object), its decoded positions ({round:int -> positions dict}, from
analysis.match_builder.load_round_positions) and its stored kill events ({round:int -> events dict}); the
server service that feeds it does the database work. Distances are metres in the engine's own space, so a
map is internally consistent but not aligned to any floor-plan image.
"""
from __future__ import annotations

import math
import statistics
from typing import Any, Optional

FLOOR_STEP = 2.0           # a height gap larger than this is another floor (matches team_facts positioning)
PATH_WINDOW = 12.0         # seconds of movement before the death to keep as the approach path
PATH_MAX_PTS = 24          # thin the path to at most this many points


def _norm(s: Any) -> str:
    return str(s or "").strip().lower()


def floor_labeler(floor_zs: list[float], floors: Optional[list] = None):
    """A function z -> floor label. If `floors` is given (the [(z, name), ...] shape analysis.places.floors
    returns) each z takes its nearest named level. Otherwise the levels are learned from `floor_zs` (the
    z of each death — deaths land on floor planes, so these cluster cleanly where whole-trace samples do not,
    because stairs fill the gaps): gap-clustered and named bottom-up, a lowest level below -2 being the
    Basement, then 1F, 2F, ... upward. Map-agnostic."""
    if floors:
        pairs = [(float(z), str(n)) for z, n in floors]
        return (lambda z: min(pairs, key=lambda p: abs(p[0] - z))[1]) if pairs else (lambda z: "?")
    zs = sorted(z for z in floor_zs if z is not None)
    if not zs:
        return lambda z: "?"
    clusters = [[zs[0]]]
    for z in zs[1:]:
        if z - clusters[-1][-1] <= FLOOR_STEP:
            clusters[-1].append(z)
        else:
            clusters.append([z])
    centres = sorted(statistics.median(c) for c in clusters)
    names: list[str] = []
    ground = 1 if centres[0] < -2.0 else 0
    if ground:
        names.append("Basement")
    names += [f"{i + 1}F" for i in range(len(centres) - ground)]
    pairs = list(zip(centres, names))
    return lambda z: min(pairs, key=lambda p: abs(p[0] - z))[1]


def _nearest(pts: list, t: float) -> Optional[list]:
    """The sample closest in time to t. Unlike integration.positions.at there is no 2 s cutoff: a player who
    stood still near death sends no movement packets, but we still want to place the death at their last
    known spot rather than drop it."""
    if not pts:
        return None
    return min(pts, key=lambda p: abs(p[0] - t))


def death_film(match: Any, ours: Optional[set[str]], positions: dict[int, dict], events: dict[int, dict],
               player: Optional[str] = None, display: Optional[dict[str, str]] = None,
               floors: Optional[list] = None) -> dict[str, Any]:
    """Every death of ours (or just `player`'s, normalized) as a placed film entry, ordered by round then
    time. `display` maps a normalized username to a shown name; `floors` is an optional [(z, name), ...] from
    analysis.places.floors to name floors by the map's real plan instead of learning them here."""
    display = display or {}
    want = _norm(player) if player else None
    # learn floor levels from where our players actually died (they land on floor planes, so these cluster
    # cleanly even when stair samples would blur a whole-trace histogram)
    floor_zs: list[float] = []
    for pos in positions.values():
        players0 = pos.get("players") or {}
        for n, t in (pos.get("died") or {}).items():
            if (ours is None or _norm(n) in ours) and n in players0:
                p = _nearest(players0[n]["pts"], t - 0.5)
                if p is not None:
                    floor_zs.append(p[3])
    label = floor_labeler(floor_zs, floors)

    round_meta: dict[int, dict] = {}
    op_by: dict[tuple[int, str], str] = {}
    for r in match.rounds:
        rn = int(r.round_number)
        round_meta[rn] = {"side": getattr(r, "side", None), "site": getattr(r, "site", "") or "",
                          "outcome": getattr(r, "outcome", None)}
        for ps in getattr(r, "player_stats", None) or []:
            nm = _norm(getattr(getattr(ps, "player", None), "name", None))
            op = getattr(getattr(ps, "operator", None), "name", None)
            if nm and op:
                op_by[(rn, nm)] = op

    def kill_of(rn: int, victim_norm: str) -> Optional[dict]:
        for k in (events.get(rn, {}) or {}).get("kills") or []:
            if _norm(k.get("victim")) == victim_norm:
                return k
        return None

    deaths: list[dict] = []
    for rn in sorted(positions):
        pos = positions[rn]
        players = pos.get("players") or {}
        mine = [n for n in players if ours is None or _norm(n) in ours]
        died = {n: t for n, t in (pos.get("died") or {}).items() if n in mine}
        meta = round_meta.get(int(rn), {})
        for n, t in sorted(died.items(), key=lambda kv: kv[1]):
            nn = _norm(n)
            if want and nn != want:
                continue
            me = _nearest(players[n]["pts"], t - 0.5)
            if me is None:
                continue
            alive = [(o, _nearest(players[o]["pts"], t - 0.5)) for o in mine
                     if o != n and died.get(o, 1e9) > t]
            alive = [(o, p) for o, p in alive if p is not None]
            near = None
            if alive:
                o, p = min(alive, key=lambda op_: math.dist(me[1:4], op_[1][1:4]))
                near = {"who": display.get(_norm(o), o),
                        "dist_m": round(math.dist(me[1:4], p[1:4]), 1),
                        "xy": [round(p[1], 2), round(p[2], 2)]}
            k = kill_of(int(rn), nn) or {}
            killer = k.get("killer")
            kpos = _nearest((players.get(killer) or {}).get("pts") or [], t) if killer else None
            path = [[round(q[1], 2), round(q[2], 2), round(q[3], 2)]
                    for q in players[n]["pts"] if t - PATH_WINDOW <= q[0] <= t]
            if len(path) > PATH_MAX_PTS:
                step = len(path) // PATH_MAX_PTS + 1
                path = path[::step]
            deaths.append({
                "round": int(rn), "side": meta.get("side"), "outcome": meta.get("outcome"),
                "site": meta.get("site"), "op": op_by.get((int(rn), nn)),
                "player": display.get(nn, n), "t": round(t, 1),
                "clock": f"{int(t) // 60}:{int(t) % 60:02d}",
                "floor": label(me[3]),
                "x": round(me[1], 2), "y": round(me[2], 2), "z": round(me[3], 2),
                "killer": killer, "hs": bool(k.get("headshot")), "opening": bool(k.get("opening")),
                "traded": (bool(k.get("trade")) if "trade" in k else None),
                "mates_alive": len(alive),
                "near_mate": near,
                "killer_xy": ([round(kpos[1], 2), round(kpos[2], 2), round(kpos[3], 2)] if kpos else None),
                "path": path,
            })

    return {"player": (display.get(want, player) if want else "Team"),
            "deaths": deaths,
            "floors": sorted({d["floor"] for d in deaths})}
