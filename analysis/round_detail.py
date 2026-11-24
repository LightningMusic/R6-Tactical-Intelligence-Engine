"""
Round-level detail for the debrief, from data we already store (round events +
decoded positions + the map's place model). All enemy-agnostic and name-based
for us, per the naming convention.

  * opening_place_* -- WHERE the opening duel happened (the single biggest lever):
    the spot the first death occurred, bucketed into "we got opened here" vs
    "our opening picks came here".
  * plant_timing_*  -- plant QUALITY when we do plant: how much time was on the
    clock, whether it was a scramble (planted already down bodies), and the
    post-plant result.
"""
from __future__ import annotations

import collections
import re
from typing import Any, Optional

ACTION_SECONDS = 180.0          # the bomb phase clock starts at 3:00; prep is the first 45 s
PREP_SECONDS = 45.0


def norm(name: Any) -> str:
    return re.sub(r"[^a-z0-9]", "", str(name or "").lower())


def clock_to_secs(clock: Any) -> Optional[int]:
    """'1:57' -> 117. None when it isn't an M:SS clock."""
    m = re.fullmatch(r"\s*(\d+):(\d{2})\s*", str(clock or ""))
    return int(m.group(1)) * 60 + int(m.group(2)) if m else None


# ── #6: where the opening happened ────────────────────────────────────────

def opening_places(match: Any, ours: Optional[set[str]], events: dict[int, dict],
                   positions: dict[int, dict], model: dict, fl: list,
                   rooms: Optional[list] = None) -> tuple[collections.Counter, collections.Counter]:
    """(opened_on_us, our_opening_picks): Counters of the place where the round's first
    death happened, split by whether the victim was ours. The first death's spot is where
    first contact was lost/won."""
    from analysis.places import area
    from integration.positions import at
    lost: collections.Counter = collections.Counter()
    won: collections.Counter = collections.Counter()
    oset = ours or set()
    for r in match.rounds:
        e = events.get(int(r.round_number), {})
        t = e.get("first_blood_time")
        victim = e.get("first_blood_victim")
        killer = e.get("first_blood_killer")
        if t is None or not victim:
            continue
        players = (positions.get(int(r.round_number)) or {}).get("players") or {}
        p = at((players.get(victim) or {}).get("pts") or [], float(t))
        if p is None:
            continue
        place = area(model, fl, p[1], p[2], p[3], rooms)
        if norm(victim) in oset:
            lost[place] += 1
        elif killer and norm(killer) in oset:
            won[place] += 1
    return lost, won


def opening_place_lines(lost: collections.Counter, won: collections.Counter) -> list[str]:
    out = []
    if lost:
        out.append("- Where we got opened (first blood against us): "
                   + ", ".join(f"{pl} x{n}" for pl, n in lost.most_common(3)) + ".")
    if won:
        out.append("- Where our opening picks came: "
                   + ", ".join(f"{pl} x{n}" for pl, n in won.most_common(3)) + ".")
    return out


# ── #8: plant quality / timing ────────────────────────────────────────────

def plant_timing(match: Any, ours: Optional[set[str]], events: dict[int, dict]) -> list[dict[str, Any]]:
    """One row per round we planted: clock left at the plant, whether it was a scramble
    (we were already down 2+ when it went down), and whether we won the round."""
    oset = ours or set()
    rows = []
    for r in match.rounds:
        e = events.get(int(r.round_number), {})
        if not e.get("planted_by_us"):
            continue
        secs = clock_to_secs(e.get("plant_clock"))
        t_plant = (PREP_SECONDS + ACTION_SECONDS - secs) if secs is not None else None
        down = None
        if t_plant is not None:
            down = sum(1 for k in (e.get("kills") or [])
                       if norm(k.get("victim")) in oset and k.get("t") is not None and k["t"] < t_plant)
        rows.append({"round": int(r.round_number), "secs_left": secs, "down": down,
                     "won": r.outcome == "win"})
    return rows


def plant_timing_lines(rows: list[dict[str, Any]]) -> list[str]:
    if not rows:
        return []
    won = sum(1 for r in rows if r["won"])
    out = [f"- Plant quality: we planted in {len(rows)} round(s), post-plant record {won}-{len(rows) - won}."]
    timed = [r for r in rows if r["secs_left"] is not None]
    if timed:
        timed.sort(key=lambda r: r["secs_left"])
        med = timed[len(timed) // 2]["secs_left"]
        out.append(f"  Clock left at the plant: median {med // 60}:{med % 60:02d} "
                   f"(earliest {timed[-1]['secs_left'] // 60}:{timed[-1]['secs_left'] % 60:02d}, "
                   f"latest {timed[0]['secs_left'] // 60}:{timed[0]['secs_left'] % 60:02d}).")
    scramble = [r for r in rows if r["down"] is not None and r["down"] >= 2]
    if scramble:
        rs = ", ".join(f"R{r['round']:02d} (down {r['down']})" for r in scramble)
        out.append(f"  Scramble plants (already down 2+ when it went down): {rs} -- "
                   "a planned plant off the clear beats a last-body scramble.")
    return out


# ── #7: did a callout turn into a pick, or come right before a death? ──────

CALLOUT_LOOKAHEAD = 8.0          # a call "led to" a pick if one of ours lands within this many seconds


def callout_outcomes(timeline: Optional[dict]) -> dict[str, int]:
    """From the comms timeline: how many alert callouts were followed by one of OUR kills
    (good info) vs how many were made right before the caller died (info too late -- the
    timeline already tags those as 'callout_then_death')."""
    out = {"alerts": 0, "productive": 0, "late": 0}
    for rnd in (timeline or {}).get("rounds", []):
        items = rnd.get("items", [])
        our_kill_t = [it.get("t") for it in items if it.get("kind") == "Kill" and it.get("killer_ours") and it.get("t") is not None]
        for it in items:
            if it.get("kind") == "speech" and it.get("alert"):
                out["alerts"] += 1
                t = it.get("t")
                if t is not None and any(t < kt <= t + CALLOUT_LOOKAHEAD for kt in our_kill_t):
                    out["productive"] += 1
            elif it.get("kind") == "callout_then_death":
                out["late"] += 1
    return out


def callout_outcome_lines(o: dict[str, int]) -> list[str]:
    if not o.get("alerts") and not o.get("late"):
        return []
    out = []
    if o.get("alerts"):
        out.append(f"- Callouts that turned into one of our picks within {int(CALLOUT_LOOKAHEAD)}s: "
                   f"{o['productive']} of {o['alerts']} alert calls.")
    if o.get("late"):
        out.append(f"- Callouts made in the few seconds before the caller died (info too late): {o['late']}.")
    return out


# ── #5: did we drone the pick? ────────────────────────────────────────────

DRONE_PICK_WINDOW = 12.0         # a drone "was out for" a pick if thrown within this long before the kill


def droned_picks(match: Any, ours: Optional[set[str]], events: dict[int, dict],
                 positions: dict[int, dict]) -> dict[str, int]:
    """Our kills that had one of our ACTION-phase drones out in the seconds just before -- a soft
    proxy for intel-led picks (a drone being out, not proven line of sight)."""
    oset = ours or set()
    our_kills = droned = 0
    for r in match.rounds:
        rn = int(r.round_number)
        drones_t = [d.get("t") for d in ((positions.get(rn) or {}).get("drones") or [])
                    if not d.get("prep") and d.get("t") is not None]
        for k in events.get(rn, {}).get("kills") or []:
            if norm(k.get("killer")) in oset and k.get("t") is not None:
                our_kills += 1
                if any(k["t"] - DRONE_PICK_WINDOW <= dt <= k["t"] for dt in drones_t):
                    droned += 1
    return {"our_kills": our_kills, "droned": droned}


def droned_pick_lines(d: dict[str, int]) -> list[str]:
    if not d.get("our_kills"):
        return []
    return [f"- Picks with one of our drones out in the {int(DRONE_PICK_WINDOW)}s before: "
            f"{d['droned']} of {d['our_kills']} of our kills (a drone out, not proven sight)."]


# ── old #4: did we bring the right things for the objective? ───────────────

HARD_BREACH = {"thermite", "hibana", "ace", "maverick"}
UTIL_CLEAR = {"thatcher", "twitch", "iq", "kali", "zero", "flores", "brava", "fuze"}   # anti-gadget / util-clear


def objective_fit(match: Any, ours: Optional[set[str]], events: dict[int, dict]) -> dict[str, Any]:
    """Per attack round (side from our_role), whether we brought a hard breacher (to open a
    reinforced wall) and a util-clear operator (to clear the defenders' gadgets)."""
    oset = ours or set()
    no_hb: list[int] = []
    no_clear: list[int] = []
    atk = 0
    for r in match.rounds:
        rn = int(r.round_number)
        if events.get(rn, {}).get("our_role") != "attack":
            continue
        ops = {norm(getattr(getattr(s, "operator", None), "name", "") or "")
               for s in r.player_stats if ours is None or norm(s.player.name) in oset}
        ops.discard("")
        if not ops:
            continue
        atk += 1
        if not (ops & HARD_BREACH):
            no_hb.append(rn)
        if not (ops & UTIL_CLEAR):
            no_clear.append(rn)
    return {"attack_rounds": atk, "no_hard_breach": no_hb, "no_util_clear": no_clear}


def objective_fit_lines(f: dict[str, Any]) -> list[str]:
    if not f.get("attack_rounds"):
        return []
    out = []
    if f["no_hard_breach"]:
        rs = ", ".join(f"R{n:02d}" for n in f["no_hard_breach"])
        out.append(f"- Attacked with no hard breacher in {len(f['no_hard_breach'])} of {f['attack_rounds']} "
                   f"round(s): {rs} -- no way to open a reinforced wall.")
    if f["no_util_clear"]:
        rs = ", ".join(f"R{n:02d}" for n in f["no_util_clear"])
        out.append(f"- No util-clear operator (Thatcher/Twitch/IQ/Kali/Flores/Fuze) in "
                   f"{len(f['no_util_clear'])} of {f['attack_rounds']} attack round(s): {rs}.")
    return out
