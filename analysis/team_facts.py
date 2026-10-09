"""
Facts the AI debrief is given, worked out in plain code so the model never has
to do arithmetic or judge a number: team-only player tables, round patterns,
first-kill conversion, and each player's standing against the team and against
their own history.

Everything here is pure (no database, no model) so it can be tested directly.
"""
from __future__ import annotations

import collections
import math
import re
import statistics
from typing import Any, Iterable, Optional

MIN_BASELINE_MATCHES = 3
MIN_DUELS_FOR_RATE = 6


def norm(name: Any) -> str:
    return str(name or "").strip().lower()


def ours_set(names: Optional[Iterable[str]]) -> Optional[set[str]]:
    cleaned = {norm(n) for n in (names or []) if norm(n)}
    return cleaned or None


def _kd(k: int, d: int) -> float:
    return k / d if d else float(k)


def band_ratio(value: float, reference: float) -> str:
    """Words for a ratio such as K/D: 1.00 against 1.00 is 'in line', never 'high'."""
    if reference <= 0:
        return "IN LINE WITH"
    r = value / reference
    if r >= 1.30:
        return "WELL ABOVE"
    if r >= 1.10:
        return "ABOVE"
    if r > 0.90:
        return "IN LINE WITH"
    if r > 0.70:
        return "BELOW"
    return "WELL BELOW"


def band_points(value: float, reference: float) -> str:
    """Words for a rate (survival, duel win rate), judged in percentage points."""
    diff = (value - reference) * 100.0
    if diff >= 30:
        return "WELL ABOVE"
    if diff >= 15:
        return "ABOVE"
    if diff > -15:
        return "IN LINE WITH"
    if diff > -30:
        return "BELOW"
    return "WELL BELOW"


def _mark(band: str) -> str:
    return "+" if band in ("ABOVE", "WELL ABOVE") else "-" if band in ("BELOW", "WELL BELOW") else "="


# ── tables ────────────────────────────────────────────────────────────────

def player_table(match: Any, ours: Optional[set[str]]) -> dict[str, dict[str, Any]]:
    """Per-player totals for this match. With `ours`, only that team's players."""
    table: dict[str, dict[str, Any]] = {}
    for r in match.rounds:
        for s in r.player_stats:
            name = str(s.player.name)
            if ours is not None and norm(name) not in ours:
                continue
            row = table.setdefault(name, {"name": name, "k": 0, "d": 0, "a": 0, "rounds": 0,
                                          "survived": 0, "ew": 0, "et": 0, "detail": []})
            row["k"] += int(s.kills)
            row["d"] += int(s.deaths)
            row["a"] += int(s.assists)
            row["ew"] += int(s.engagements_won)
            row["et"] += int(s.engagements_taken)
            row["rounds"] += 1
            row["detail"].append({
                "round": int(r.round_number), "side": r.side,
                "op": str(getattr(getattr(s, "operator", None), "name", "") or ""),
                "start": int(getattr(s, "ability_start", 0) or 0),
                "used": int(getattr(s, "ability_used", 0) or 0),
            })
            if int(s.deaths) == 0:
                row["survived"] += 1
    for row in table.values():
        row["kd"] = _kd(row["k"], row["d"])
        row["survival"] = row["survived"] / row["rounds"] if row["rounds"] else 0.0
        row["ewr"] = row["ew"] / row["et"] if row["et"] else None
    return table


def team_totals(table: dict[str, dict[str, Any]]) -> dict[str, Any]:
    k = sum(r["k"] for r in table.values())
    d = sum(r["d"] for r in table.values())
    rounds = sum(r["rounds"] for r in table.values())
    survived = sum(r["survived"] for r in table.values())
    ew = sum(r["ew"] for r in table.values())
    et = sum(r["et"] for r in table.values())
    return {"k": k, "d": d, "kd": _kd(k, d), "survival": survived / rounds if rounds else 0.0,
            "ewr": ew / et if et else None, "players": len(table)}


# ── rounds ────────────────────────────────────────────────────────────────

def _label(r: Any) -> str:
    return f"R{int(r.round_number):02d}"


def _streaks(rounds: list[Any], want_win: bool) -> tuple[int, str]:
    best, best_range, cur, cur_start = 0, "", 0, None
    for r in rounds:
        if (r.outcome == "win") == want_win:
            cur += 1
            cur_start = cur_start or _label(r)
            if cur > best:
                best = cur
                best_range = _label(r) if cur == 1 else f"{cur_start}-{_label(r)}"
        else:
            cur, cur_start = 0, None
    return best, best_range


def round_patterns(match: Any) -> str:
    rs = sorted(match.rounds, key=lambda r: r.round_number)
    lines: list[str] = []
    for side, title in (("attack", "Attack"), ("defense", "Defense")):
        sr = [r for r in rs if r.side == side]
        if not sr:
            continue
        won = [_label(r) for r in sr if r.outcome == "win"]
        lost = [_label(r) for r in sr if r.outcome != "win"]
        lines.append(f"- {title}: {len(won)}-{len(lost)} (won {', '.join(won) or 'none'}; "
                     f"lost {', '.join(lost) or 'none'})")
    if rs:
        lines.append("- Round by round: " + " ".join(f"{_label(r)} {'W' if r.outcome == 'win' else 'L'}" for r in rs))
        n, span = _streaks(rs, True)
        if n >= 2:
            lines.append(f"- Longest win streak: {n} ({span})")
        n, span = _streaks(rs, False)
        if n >= 2:
            lines.append(f"- Longest losing streak: {n} ({span})")
        switch = next((b for a, b in zip(rs, rs[1:]) if a.side != b.side), None)
        if switch is not None:
            lines.append(f"- Sides switched at {_label(switch)}")
    return "\n".join(lines)


def round_kda(match: Any, ours: Optional[set[str]]) -> dict[int, tuple[int, int, int]]:
    out: dict[int, tuple[int, int, int]] = {}
    for r in match.rounds:
        k = d = a = 0
        for s in r.player_stats:
            if ours is not None and norm(s.player.name) not in ours:
                continue
            k, d, a = k + int(s.kills), d + int(s.deaths), a + int(s.assists)
        out[int(r.round_number)] = (k, d, a)
    return out


# ── first kills and clutches (from the replay's kill feed) ────────────────

def ordered(events: dict[int, dict]) -> dict[int, dict]:
    """Only rounds whose kill feed is in the order things happened. Before 2026-10-06 it was sorted by the
    on-screen clock (time left), so first kills, trades and clutches stored then are wrong until re-read."""
    return {n: e for n, e in events.items() if e.get("kill_order")}


def opening_summary(match: Any, events: dict[int, dict]) -> dict[str, Any]:
    events = ordered(events)
    won_by_round = {int(r.round_number): r.outcome == "win" for r in match.rounds}
    up = [n for n, e in events.items() if e.get("opening_duel_won") is True and n in won_by_round]
    down = [n for n, e in events.items() if e.get("opening_duel_won") is False and n in won_by_round]
    return {
        "first_kill_rounds": len(up), "first_kill_wins": sum(won_by_round[n] for n in up),
        "conceded_rounds": len(down), "conceded_wins": sum(won_by_round[n] for n in down),
    }


def opening_lines(summary: dict[str, Any]) -> list[str]:
    out = []
    if summary["first_kill_rounds"]:
        out.append(f"We got the first kill in {summary['first_kill_rounds']} rounds and won "
                   f"{summary['first_kill_wins']} of them.")
    if summary["conceded_rounds"]:
        out.append(f"They got the first kill in {summary['conceded_rounds']} rounds; we won "
                   f"{summary['conceded_wins']} of those.")
    return out


def clutch_lines(events: dict[int, dict], ours: Optional[set[str]], display: dict[str, str]) -> list[str]:
    out = []
    events = ordered(events)
    for n in sorted(events):
        who = events[n].get("clutch_player") or ""
        if who and (ours is None or norm(who) in ours):
            kills = int(events[n].get("clutch_kills") or 0)
            out.append(f"R{n:02d}: {display.get(norm(who), who)} won the round alone "
                       + (f"({kills} kill(s))" if kills else "(held on with no kill until the round was won)"))
    return out


def opening_counts(events: dict[int, dict]) -> dict[str, dict[str, int]]:
    """{player: {'first_kills': n, 'first_deaths': n}} from the kill feed."""
    out: dict[str, dict[str, int]] = {}
    for e in ordered(events).values():
        killer, victim = e.get("first_blood_killer") or "", e.get("first_blood_victim") or ""
        if killer:
            out.setdefault(norm(killer), {"first_kills": 0, "first_deaths": 0})["first_kills"] += 1
        if victim:
            out.setdefault(norm(victim), {"first_kills": 0, "first_deaths": 0})["first_deaths"] += 1
    return out


# ── this match against the team's own usual ───────────────────────────────

USUAL_MIN_MATCHES = 3        # earlier matches needed before "your usual" means anything
USUAL_MIN_ROUNDS = 20
USUAL_MIN_THIS_ROUNDS = 4    # a one- or two-round package says nothing about how a night went


def match_record(match: Any, ours: Optional[set[str]], events: dict[int, dict],
                 positions: Optional[dict[int, dict]] = None) -> dict[str, int]:
    """The counts one match adds to (or is compared with) the team's usual."""
    pos = positioning(match, ours, positions) if positions else {"deaths": 0, "isolated": 0}
    table = player_table(match, ours)
    team = team_totals(table)
    op = opening_summary(match, events)
    deaths = traded = 0
    for e in ordered(events).values():
        for k in e.get("kills") or []:
            if ours is not None and norm(k.get("victim")) in ours:
                deaths += 1
                traded += bool(k.get("trade"))           # its killer was killed within a few seconds
    atk = [r for r in match.rounds if r.side == "attack"]
    planted = [r for r in atk if events.get(int(r.round_number), {}).get("bomb_planted")]
    sites: dict[str, list[int]] = {}
    for r in match.rounds:
        if getattr(r, "site", None):
            s = sites.setdefault(f"{r.side}|{r.site}", [0, 0])
            s[0] += 1
            s[1] += r.outcome == "win"
    return {
        "rounds": len(match.rounds), "won": sum(1 for r in match.rounds if r.outcome == "win"),
        "fk": op["first_kill_rounds"], "fk_won": op["first_kill_wins"],
        "conc": op["conceded_rounds"], "conc_won": op["conceded_wins"],
        "k": team["k"], "d": team["d"],
        "player_rounds": sum(r["rounds"] for r in table.values()),
        "survived": sum(r["survived"] for r in table.values()),
        "deaths": deaths, "traded": traded,
        "atk": len(atk), "planted": len(planted), "planted_won": sum(r.outcome == "win" for r in planted),
        "unplanted_won": sum(r.outcome == "win" for r in atk if r not in planted),
        "sites": sites,
        "pos_deaths": pos["deaths"], "pos_isolated": pos["isolated"],
    }


def usual_baseline(records: Iterable[Optional[dict[str, Any]]]) -> Optional[dict[str, Any]]:
    """Totals over the earlier matches, or None when there are too few to call anything usual."""
    recs = [r for r in records if r and r["rounds"] >= USUAL_MIN_THIS_ROUNDS]
    if len(recs) < USUAL_MIN_MATCHES or sum(r["rounds"] for r in recs) < USUAL_MIN_ROUNDS:
        return None
    out: dict[str, Any] = {k: sum(r.get(k, 0) for r in recs) for k in recs[0] if k != "sites"}
    sites: dict[str, list[int]] = {}
    for r in recs:
        for key, (n, w) in (r.get("sites") or {}).items():
            s = sites.setdefault(key, [0, 0])
            s[0] += n
            s[1] += w
    out["sites"] = sites
    out["matches"] = len(recs)
    return out


def usual_lines(this: Optional[dict[str, int]], base: Optional[dict[str, int]]) -> list[str]:
    """How this match compares with the team's earlier ones, as plain lines. Empty when there is no
    fair comparison (too few earlier matches, or this one is too short)."""
    if not this or not base or this["rounds"] < USUAL_MIN_THIS_ROUNDS:
        return []
    out: list[str] = []
    n, b = this["fk"] + this["conc"], base["fk"] + base["conc"]
    if n >= 3 and b >= 10:
        here, usual = this["fk"] / n, base["fk"] / b
        out.append(f"- First kill: we got it in {this['fk']} of {n} rounds ({here:.0%}), "
                   f"{band_points(here, usual).lower()} your usual {usual:.0%} over {base['matches']} earlier matches.")
        if base["fk"] >= 5 and base["conc"] >= 5:
            out.append(f"- What the first kill is worth to this team: in your earlier matches you won "
                       f"{base['fk_won'] / base['fk']:.0%} of the rounds where you got it and "
                       f"{base['conc_won'] / base['conc']:.0%} of the rounds where they did"
                       + (f". Here: {this['fk_won']} of {this['fk']} and {this['conc_won']} of {this['conc']}."
                          if this["fk"] and this["conc"] else
                          f". Here: {this['fk_won']} of {this['fk']} when we got it." if this["fk"] else
                          f". Here: {this['conc_won']} of {this['conc']} when they got it."))
    if this["player_rounds"] >= 10 and base["player_rounds"] >= 30:
        here, usual = this["survived"] / this["player_rounds"], base["survived"] / base["player_rounds"]
        out.append(f"- Staying alive: players lived through {here:.0%} of their rounds, "
                   f"{band_points(here, usual).lower()} your usual {usual:.0%}.")
    if this["d"] and base["d"]:
        here, usual = _kd(this["k"], this["d"]), _kd(base["k"], base["d"])
        out.append(f"- Team K/D {here:.2f}, {band_ratio(here, usual).lower()} your usual {usual:.2f}.")
    if this.get("deaths", 0) >= 8 and base.get("deaths", 0) >= 30:
        here, usual = this["traded"] / this["deaths"], base["traded"] / base["deaths"]
        out.append(f"- Trades: {this['traded']} of our {this['deaths']} deaths were avenged within seconds ({here:.0%}), "
                   f"{band_points(here, usual).lower()} your usual {usual:.0%}. An untraded death leaves the team a player down.")
    unpl = base.get("atk", 0) - base.get("planted", 0)
    if this.get("atk", 0) >= 3 and base.get("planted", 0) >= 5 and unpl >= 5:
        out.append(f"- Plants: we planted in {this['planted']} of {this['atk']} attack rounds and won {this['planted_won']} of those. "
                   f"In earlier matches you won {base['planted_won'] / base['planted']:.0%} of the attack rounds where you planted "
                   f"and {base['unplanted_won'] / unpl:.0%} of those where you didn't.")
    for key, (n, w) in sorted((this.get("sites") or {}).items()):
        before = (base.get("sites") or {}).get(key)
        if before and before[0] >= 4:
            side, site = key.split("|", 1)
            out.append(f"- {'Defending' if side == 'defense' else 'Attacking'} {site}: {w}-{n - w} here; "
                       f"{before[1]}-{before[0] - before[1]} in earlier matches.")
    return out


# ── positions (where everyone was, from the replay's movement stream) ──────

ISOLATION_M = 10.0          # a death with no teammate within this distance could not be traded
APART_M = 10.0              # the last two of us further apart than this were fighting separate fights
FLOOR_STEP = 2.0            # a height difference larger than this is another floor


def _gap(a, b) -> float:
    """Distance between two samples [t, x, y, z, yaw]; another floor counts as far away."""
    return math.dist(a[1:3], b[1:3]) + (100.0 if abs(a[3] - b[3]) > FLOOR_STEP else 0.0)


def positioning(match: Any, ours: Optional[set[str]], positions: dict[int, dict], walk: Any = None) -> dict[str, Any]:
    """From each round's player paths: our deaths with nobody close enough to trade them (straight line on the
    same floor: a trade needs a line of sight, and this matched real trades best), and the rounds that came
    down to two of us: how far apart they were (WALKING distance when `walk` is given, so a wall between them
    counts) and whether they went down together."""
    from integration.positions import at
    deaths = isolated = 0
    last_two: list[dict] = []
    outcome = {int(r.round_number): r.outcome for r in match.rounds}
    for rn, pos in sorted(positions.items()):
        players = pos.get("players") or {}
        mine = [n for n in players if ours is None or norm(n) in ours]
        if len(mine) < 2:
            continue
        died = {n: t for n, t in (pos.get("died") or {}).items() if n in mine}
        for n, t in died.items():
            me = at(players[n]["pts"], t - 0.5)
            if me is None:
                continue
            others = [p for p in (at(players[o]["pts"], t - 0.5) for o in mine if o != n and died.get(o, 1e9) > t)
                      if p is not None]
            if not others:
                continue                     # the last of us: nobody left to trade, says nothing about spacing
            near = min(_gap(me, o) for o in others)
            deaths += 1
            isolated += near > ISOLATION_M
        order = sorted(died.items(), key=lambda kv: kv[1])
        alive = list(mine)
        for n, t in order:
            alive.remove(n)
            if len(alive) == 2:
                a, b = alive
                end = min(died.get(a, 1e9), died.get(b, 1e9),
                          max(p[0] for x in (a, b) for p in players[x]["pts"][-1:]))
                pairs = [(pa, pb) for s in range(int(t), int(end) + 1)
                         if (pa := at(players[a]["pts"], s)) is not None and (pb := at(players[b]["pts"], s)) is not None]
                ds = [_gap(pa, pb) for pa, pb in pairs]
                if ds:
                    da, db = died.get(a), died.get(b)
                    entry = {"round": rn, "median_m": statistics.median(ds), "outcome": outcome.get(rn),
                             "died_apart_s": abs(da - db) if da is not None and db is not None else None}
                    if walk is not None:
                        # a few moments are enough (each is a path search): start, middle, end of the 2-alive spell
                        picks = [pairs[0], pairs[len(pairs) // 2], pairs[-1]] if len(pairs) > 2 else pairs
                        w = [walk.distance(pa[1:4], pb[1:4]) for pa, pb in picks]
                        entry["walk_m"] = statistics.median(w)
                    last_two.append(entry)
                break
    return {"deaths": deaths, "isolated": isolated, "last_two": last_two}


def positioning_lines(p: dict[str, Any], usual: Optional[dict[str, Any]] = None) -> list[str]:
    out = []
    if p.get("deaths"):
        line = (f"- Deaths with no teammate within {ISOLATION_M:.0f} m on the same floor (nobody close enough to trade them): "
                f"{p['isolated']} of {p['deaths']} ({p['isolated'] / p['deaths']:.0%}).")
        if usual and usual.get("pos_deaths", 0) >= 20:
            u = usual["pos_isolated"] / usual["pos_deaths"]
            line += f" Your usual: {u:.0%}."
        out.append(line)
    two = p.get("last_two") or []
    if two:
        walked = all("walk_m" in x for x in two)

        def sep(x):
            return x["walk_m"] if walked else x["median_m"]

        def describe(x):
            if walked:
                if x["walk_m"] == math.inf:
                    s = "no walkable way between them"
                else:
                    s = f"{x['walk_m']:.0f} m to walk between them"
                    if x["median_m"] < APART_M < x["walk_m"]:
                        s += f" (only {x['median_m']:.0f} m in a straight line: a wall between)"
                    elif x["median_m"] >= 100:
                        s += " (on different floors)"
            else:
                s = "on different floors" if x["median_m"] >= 100 else f"{x['median_m']:.0f} m apart"
            return (f"R{x['round']:02d} {s}" + (f", down {x['died_apart_s']:.0f} s apart" if x["died_apart_s"] is not None else "")
                    + (" (won)" if x["outcome"] == "win" else ""))

        apart = [x for x in two if sep(x) > APART_M]
        how = "a walk of more than" if walked else "more than"
        out.append(f"- It came down to two of us in {len(two)} round(s); in {len(apart)} of them the two were {how} "
                   f"{APART_M:.0f} m apart, fighting separate fights: {'; '.join(describe(x) for x in two)}.")
    return out


def death_places(match: Any, ours: Optional[set[str]], positions: dict[int, dict], model: dict, fl: list) -> list[str]:
    """Where our players died, as places ('2F at Armory Lockers / Archives'), most common first."""
    from analysis.places import area
    from integration.positions import at
    counts: collections.Counter = collections.Counter()
    for pos in positions.values():
        players = pos.get("players") or {}
        for n, t in (pos.get("died") or {}).items():
            if (ours is None or norm(n) in ours) and n in players:
                p = at(players[n]["pts"], t - 0.5)
                if p is not None:
                    counts[area(model, fl, p[1], p[2], p[3])] += 1
    if not counts:
        return []
    top = ", ".join(f"{place} x{n}" for place, n in counts.most_common(3))
    return [f"- Where our players died most: {top}."]


# ── site setup and breaches (integration/walls.py) ────────────────────────

ON_WALL_M = 2.0             # a breach this close to a reinforced spot (same floor) went through that wall
COVERED_M = 1.5             # a reinforcement this close to one of the site's usual walls covered it
HARD_READ = {"thermite", "ace"}          # hard breachers whose charges the replay names (walls.GADGETS)


def _near(spots: list[dict], x: float, y: float, z: float, dist: float) -> Optional[dict]:
    best = min(((math.dist((s["x"], s["y"]), (x, y)), s) for s in spots
                if s.get("x") is not None and abs(s["z"] - z) < FLOOR_STEP), default=None, key=lambda c: c[0])
    return best[1] if best and best[0] < dist else None


def _ops(r: Any, ours: Optional[set[str]], mine: bool) -> set[str]:
    return {norm(getattr(getattr(s, "operator", None), "name", "") or "") for s in r.player_stats
            if ours is not None and (norm(s.player.name) in ours) == mine} - {""}


def setup(match: Any, ours: Optional[set[str]], positions: dict[int, dict],
          usual: Optional[dict[str, dict]] = None) -> dict[str, list[dict]]:
    """Per round, from the replays' wall data: on defense, how many of the 10 reinforcements went up, how many
    after prep, who put them up, which of the site's usual walls stayed open, and which hard breach charges went
    off on a wall we reinforced; on attack, what our hard breach opened."""
    from analysis.places import usual_walls
    from integration.positions import PREP_SEC
    out: dict[str, list[dict]] = {"defense": [], "attack": []}
    for r in sorted(match.rounds, key=lambda x: x.round_number):
        rn = int(r.round_number)
        w = (positions.get(rn) or {}).get("walls")
        if not w or r.side not in ("attack", "defense"):
            continue
        reinf = w.get("reinforcements") or []
        hard = [b for b in w.get("breaches") or [] if b.get("kind") == "hard"]
        if r.side == "defense":
            missed = total = 0
            for u in usual_walls(usual or {}, r.site or ""):
                total += 1
                missed += _near(reinf, u["x"], u["y"], u["z"], COVERED_M) is None
            through, other = [], []
            for b in hard:
                s = _near(reinf, b["x"], b["y"], b["z"], ON_WALL_M)
                (through if s else other).append({**b, "wall_by": s.get("who") if s else None})
            out["defense"].append({
                "round": rn, "site": r.site or "", "used": w["start"] - w["end"], "start": w["start"],
                "late": sum(1 for s in reinf if s["t"] > PREP_SEC),
                "by": collections.Counter(s["who"] for s in reinf if s.get("who") and (ours is None or norm(s["who"]) in ours)),
                "unclear": sum(1 for s in reinf if not s.get("who")),
                "usual": total, "missed": missed, "through": through, "other": other,
                "their_hard": sorted(_ops(r, ours, False) & HARD_BREACH)})
        else:
            opened, other = [], []
            for b in hard:
                (opened if _near(reinf, b["x"], b["y"], b["z"], ON_WALL_M) else other).append(b)
            out["attack"].append({"round": rn, "opened": opened, "other": other,
                                  "our_hard": sorted(_ops(r, ours, True) & HARD_BREACH)})
    return out


def _charges(bs: list[dict]) -> str:
    """'Thermite at 2:11 left' / 'Thermite (2 charges) from 2:12 left' / 'Ace (5 blasts) from 1:42 left'
    (each S.E.L.M.A. blasts more than once; earliest first)."""
    from integration.walls import clock_left
    by: dict[str, list[float]] = collections.defaultdict(list)
    for b in bs:
        by[b["op"]].append(b["t"])
    return ", ".join(f"{op} at {clock_left(ts[0])}" if len(ts) == 1
                     else f"{op} ({len(ts)} {'blasts' if op == 'Ace' else 'charges'}) from {clock_left(min(ts))}"
                     for op, ts in sorted(by.items(), key=lambda kv: min(kv[1])))


def setup_lines(s: dict[str, list[dict]], display: Optional[dict[str, str]] = None) -> list[str]:
    from analysis.places import short_site
    display = display or {}
    name = lambda n: display.get(norm(n), n)
    out = []
    d = s.get("defense") or []
    if d:
        used = sum(x["used"] for x in d)
        start = sum(x["start"] for x in d)
        low = min(d, key=lambda x: x["used"])
        line = (f"- Reinforcements on defense: {used} of {start} used over {len(d)} round(s)"
                f" (fewest: R{low['round']:02d}, {low['used']} of {low['start']}).")
        late = [x for x in d if x["late"]]
        if late:
            line += (f" {sum(x['late'] for x in late)} went up after the action had started: "
                     + ", ".join(f"R{x['round']:02d}" + (f" x{x['late']}" if x["late"] > 1 else "") for x in late) + ".")
        out.append(line)
        by: collections.Counter = collections.Counter()
        for x in d:
            by.update(x["by"])
        if by:
            unclear = sum(x["unclear"] for x in d)
            out.append("- Who put them up: " + ", ".join(f"{name(n)} {k}" for n, k in by.most_common())
                       + (f" ({unclear} not clear from the replay)" if unclear else "") + ".")
        judged = [x for x in d if x["usual"]]
        if judged:
            missed = [x for x in judged if x["missed"]]
            if missed:
                out.append("- Walls usually reinforced on that site but left open: " + "; ".join(
                    f"R{x['round']:02d} ({short_site(x['site'])}) {x['missed']} of {x['usual']}" for x in missed) + ".")
            else:
                out.append(f"- Every usual wall of the site was reinforced in all {len(judged)} round(s) where that is known.")
        through = [x for x in d if x["through"]]
        if through:
            out.append(f"- Their hard breach went through walls we reinforced in {len(through)} of {len(d)} defense round(s): "
                       + "; ".join(f"R{x['round']:02d} {_charges(x['through'])}"
                                   + (f" ({', '.join(sorted({name(b['wall_by']) for b in x['through'] if b['wall_by']}))}'s wall)"
                                      if any(b["wall_by"] for b in x["through"]) else "")
                                   for x in through) + ".")
        other = [x for x in d if x["other"]]
        if other:
            out.append("- Their hard breach also opened walls we had not reinforced: "
                       + "; ".join(f"R{x['round']:02d} {_charges(x['other'])}" for x in other) + ".")
        faced = [x for x in d if set(x["their_hard"]) & HARD_READ]
        held = [x for x in faced if not x["through"] and not x["other"]]
        if held:
            out.append(f"- They had a hard breacher but no charge went off in {len(held)} of {len(faced)} such round(s): "
                       + ", ".join(f"R{x['round']:02d}" for x in held) + " (denied, never placed, or never reached).")
    a = s.get("attack") or []
    rows = []
    for x in a:
        if x["opened"]:
            rows.append(f"R{x['round']:02d} {_charges(x['opened'])} on reinforced walls")
        if x["other"]:
            rows.append(f"R{x['round']:02d} {_charges(x['other'])} on walls that were not reinforced")
        readable = set(x["our_hard"]) & HARD_READ
        if readable and not x["opened"] and not x["other"]:
            rows.append(f"R{x['round']:02d} {'/'.join(o.capitalize() for o in sorted(readable))} in the lineup but no charge went off")
    if rows:
        out.append("- Our hard breach: " + "; ".join(rows) + "."
                   + (" (No charge going off: denied, never placed, or the breacher died first.)"
                      if any("no charge went off" in r for r in rows) else ""))
    unread = sorted({o for x in a for o in x["our_hard"]} - HARD_READ)
    if unread:
        out.append(f"- ({', '.join(o.capitalize() for o in unread)}: charges are not read from the replay yet.)")
    return out


# ── objective play, utility and operators ─────────────────────────────────

HARD_BREACH = {"thermite", "hibana", "ace", "maverick"}

# Operators whose signature gadget is a passive ability, a shield or a tool rather than a limited
# stock of charges. Their counter (when there is one) says nothing about "used" or "unused".
NON_CONSUMABLE = {"sledge", "montagne", "blitz", "clash", "blackbeard", "warden", "caveira", "nøkk", "nokk",
                  "vigil", "jackal", "iq", "oryx"}
DEFENSE_FIRST_USE = 0.8          # a gadget used in this share of rounds is "used reliably"
LOW_USE = 1 / 3                  # ...and in this share or less, "left unused"


def utility_measured(match: Any, ours: Optional[set[str]], events: dict[int, dict]) -> bool:
    """True when gadget usage was actually read from the replays (not merely absent)."""
    if any(e.get("utility_tracked") for e in events.values()):
        return True
    return any(int(getattr(s, "ability_used", 0) or 0) > 0
               for r in match.rounds for s in r.player_stats
               if ours is None or norm(s.player.name) in ours)


def player_utility(row: dict[str, Any]) -> Optional[dict[str, Any]]:
    """One player's gadget use over the match, or None if no round gave them a countable gadget."""
    det = [d for d in row["detail"] if d["start"] > 0 and norm(d["op"]) not in NON_CONSUMABLE]
    if not det:
        return None
    out: dict[str, Any] = {"rounds": len(det), "used_rounds": sum(1 for d in det if d["used"] > 0),
                           "charges_total": sum(d["start"] for d in det),
                           "charges_used": sum(min(d["used"], d["start"]) for d in det)}
    for side in ("attack", "defense"):
        rs = [d for d in det if d["side"] == side]
        ops: dict[str, int] = {}
        for d in rs:
            ops[d["op"] or "?"] = ops.get(d["op"] or "?", 0) + 1
        out[side] = {"rounds": len(rs), "used_rounds": sum(1 for d in rs if d["used"] > 0),
                     "ops": [o for o, _ in sorted(ops.items(), key=lambda kv: -kv[1])]}
    return out


def player_secondary(events: dict[int, dict], ours: Optional[set[str]]) -> dict[str, dict[str, Any]]:
    """norm(username) -> use of their secondary gadget (frag, stun, claymore, wire...) over the match.
    Read from each round's events; a round only counts for a player whose loadout had one."""
    out: dict[str, dict[str, Any]] = {}
    for rn, e in sorted(events.items()):
        if not e.get("secondary_tracked"):
            continue
        side = e.get("our_role") if e.get("our_role") in ("attack", "defense") else "attack"
        for name, d in (e.get("player_derived") or {}).items():
            start = int(d.get("secondary_start") or 0)
            key = norm(name)
            if start <= 0 or (ours is not None and key not in ours):
                continue
            used = int(d.get("secondary_used") or 0)
            row = out.setdefault(key, {"name": name, "rounds": 0, "used_rounds": 0, "charges_total": 0,
                                       "charges_used": 0, "attack": {"rounds": 0, "used_rounds": 0},
                                       "defense": {"rounds": 0, "used_rounds": 0}})
            row["rounds"] += 1
            row["used_rounds"] += 1 if used > 0 else 0
            row["charges_total"] += start
            row["charges_used"] += min(used, start)
            row[side]["rounds"] += 1
            row[side]["used_rounds"] += 1 if used > 0 else 0
    return out


def secondary_summary(sec: dict[str, dict[str, Any]], named: Optional[set[str]] = None) -> dict[str, Any]:
    rows = [r for k, r in sec.items() if named is None or k in named]
    tot = {"measured": bool(rows), "rounds": sum(r["rounds"] for r in rows), "used": sum(r["used_rounds"] for r in rows)}
    for side in ("attack", "defense"):
        tot[side] = (sum(r[side]["used_rounds"] for r in rows), sum(r[side]["rounds"] for r in rows))
    return tot


def secondary_lines(summary: dict[str, Any]) -> list[str]:
    if not summary.get("measured") or not summary["rounds"]:
        return []
    (ua, na), (ud, nd) = summary["attack"], summary["defense"]
    bits = [f"{lab} {u}/{n}" for lab, u, n in (("attack", ua, na), ("defense", ud, nd)) if n]
    return [f"- Secondary gadgets (frags, stuns, claymores, wire and the like) were used in {summary['used']} of "
            f"{summary['rounds']} player-rounds that carried one ({summary['used'] / summary['rounds']:.0%}): "
            + ", ".join(bits) + "."]


def secondary_text(row: dict[str, Any]) -> str:
    bits = [f"{lab} {row[lab]['used_rounds']}/{row[lab]['rounds']}" for lab in ("attack", "defense") if row[lab]["rounds"]]
    return (f"secondary gadget used in {row['used_rounds']} of {row['rounds']} rounds that had one "
            f"({', '.join(bits)}); charges used {row['charges_used']} of {row['charges_total']}.")


def operator_text(row: dict[str, Any]) -> str:
    counts: dict[tuple[str, str], int] = {}
    for d in row["detail"]:
        if d["op"]:
            counts[(d["op"], d["side"])] = counts.get((d["op"], d["side"]), 0) + 1
    return ", ".join(f"{op} x{n} ({'att' if side == 'attack' else 'def'})"
                     for (op, side), n in sorted(counts.items(), key=lambda kv: (-kv[1], kv[0][0])))


def _attempts(events: dict[int, dict], r: Any) -> list[dict]:
    return events.get(int(r.round_number), {}).get("objective_attempts") or []


def objective_summary(match: Any, events: dict[int, dict]) -> dict[str, Any]:
    """Plants and defuses: the team-level outcome, and (when the replay's countdowns were read)
    who did them and which attempts were let go or cut short."""
    ev = lambda r: events.get(int(r.round_number), {})
    atk = [r for r in match.rounds if r.side == "attack"]
    dfn = [r for r in match.rounds if r.side == "defense"]
    planted = [r for r in atk if ev(r).get("bomb_planted")]
    enemy = [r for r in dfn if ev(r).get("bomb_planted")]
    attempts_known = any(e.get("objective_tracked") for e in events.values())

    plants_by: dict[str, list[tuple[int, str]]] = {}      # who planted for us: name -> [(round, operator)]
    defuses_by: dict[str, list[tuple[int, str]]] = {}
    cut_by: dict[str, int] = {}                           # our attempts that did not complete
    plant_cut = enemy_plant_cut = 0                       # plant attempts that did not complete: ours / theirs
    counter_rounds = counter_cut = their_defuse_rounds = their_defuse_cut = 0
    for r in match.rounds:
        mine = r.side == "attack"
        here = _attempts(events, r)
        for a in here:
            if a["kind"] == "plant":
                if mine:
                    plant_cut += 0 if a["completed"] else 1
                else:
                    enemy_plant_cut += 0 if a["completed"] else 1
            elif not mine:
                counter_cut += 0 if a["completed"] else 1
            else:
                their_defuse_cut += 0 if a["completed"] else 1
            if a.get("ours") and a.get("username"):
                bucket = plants_by if a["kind"] == "plant" else defuses_by
                key = norm(a["username"])
                if a["completed"]:
                    bucket.setdefault(key, []).append((int(r.round_number), a.get("operator") or ""))
                else:
                    cut_by[key] = cut_by.get(key, 0) + 1
        if not mine and any(a["kind"] == "defuse" for a in here):
            counter_rounds += 1
        if mine and any(a["kind"] == "defuse" for a in here):
            their_defuse_rounds += 1
    return {
        "tracked": any("bomb_planted" in e for e in events.values()),
        "attempts_known": attempts_known,
        "attack_rounds": len(atk), "planted": len(planted),
        "planted_won": sum(1 for r in planted if r.outcome == "win"),
        "planted_defused": sum(1 for r in planted if ev(r).get("bomb_defused")),
        "unplanted_won": sum(1 for r in atk if r not in planted and r.outcome == "win"),
        "defense_rounds": len(dfn), "enemy_planted": len(enemy),
        "enemy_planted_won": sum(1 for r in enemy if r.outcome == "win"),
        "we_defused": sum(1 for r in enemy if ev(r).get("bomb_defused")),
        "unplanted_defense_won": sum(1 for r in dfn if r not in enemy and r.outcome == "win"),
        "unplanted_defense": sum(1 for r in dfn if r not in enemy),
        # from the replay's countdowns (only meaningful when attempts_known)
        "plant_cut": plant_cut, "enemy_plant_cut": enemy_plant_cut,
        "counter_rounds": counter_rounds, "counter_cut": counter_cut,
        "their_defuse_rounds": their_defuse_rounds, "their_defuse_cut": their_defuse_cut,
        "plants_by": plants_by, "defuses_by": defuses_by, "cut_by": cut_by,
    }


def _credit(by: dict[str, list[tuple[int, str]]], display: dict[str, str], named: Optional[set[str]]) -> str:
    """'Zander (R4 Thermite, R8 Ace), a teammate (R2 Lion)': the saved team list by name, anyone else unnamed."""
    parts: list[str] = []
    others: list[tuple[int, str]] = []
    for key, rounds in sorted(by.items(), key=lambda kv: (-len(kv[1]), kv[0])):
        if named is not None and key not in named:
            others += rounds
            continue
        what = ", ".join(f"R{n}" + (f" {op}" if op else "") for n, op in sorted(rounds))
        parts.append(f"{display.get(key, key)} ({what})")
    if others:
        what = ", ".join(f"R{n}" + (f" {op}" if op else "") for n, op in sorted(others))
        parts.append(f"a teammate outside the saved team list ({what})")
    return ", ".join(parts)


def objective_lines(o: dict[str, Any], display: Optional[dict[str, str]] = None,
                    named: Optional[set[str]] = None) -> list[str]:
    if not o["tracked"] and not o.get("attempts_known"):
        return []
    display = display or {}
    known = bool(o.get("attempts_known"))
    plural = lambda n, w: f"{n} {w}{'' if n == 1 else 's'}"
    out = []
    if o["attack_rounds"]:
        line = f"- Attack: we planted in {o['planted']} of {o['attack_rounds']} rounds"
        if o["planted"]:
            line += f" and won {o['planted_won']} of those"
            if o["planted_defused"]:
                line += f" (the defuser was disabled in {o['planted_defused']})"
        rest = o["attack_rounds"] - o["planted"]
        if rest:
            line += f"; in the {rest} round{'s' if rest != 1 else ''} without a plant we won {o['unplanted_won']}"
        out.append(line + ".")
        if known and o["plants_by"]:
            out.append(f"- Planted by: {_credit(o['plants_by'], display, named)}.")
        if known and o["plant_cut"]:
            out.append(f"- {plural(o['plant_cut'], 'plant attempt')} of ours started but did not finish (the planter let go or was killed).")
    if o["defense_rounds"]:
        line = f"- Defense: they planted in {o['enemy_planted']} of {o['defense_rounds']} rounds"
        if o["enemy_planted"]:
            line += f"; we won {o['enemy_planted_won']} of those rounds"
            if not known:
                line += f" and disabled the defuser in {o['we_defused']}"
        if o["unplanted_defense"]:
            line += f"; we won {o['unplanted_defense_won']} of the {o['unplanted_defense']} where they never planted"
        out.append(line + ".")
        if known and o["enemy_planted"]:
            if o["counter_rounds"]:
                line = (f"- Counter-defuse: we started a defuse in {o['counter_rounds']} of those {o['enemy_planted']} "
                        f"plants and finished it in {o['we_defused']}")
                if o["counter_cut"]:
                    line += f" ({plural(o['counter_cut'], 'attempt')} cut short)"
                out.append(line + ".")
            else:
                out.append(f"- Counter-defuse: we never started a defuse in the {plural(o['enemy_planted'], 'round')} "
                           "they planted.")
            if o["defuses_by"]:
                out.append(f"- Defuser disabled by: {_credit(o['defuses_by'], display, named)}.")
        if known and o["enemy_plant_cut"]:
            out.append(f"- {plural(o['enemy_plant_cut'], 'enemy plant attempt')} did not finish "
                       "(we killed the planter or made them let go).")
    if known and o["planted"] and o["their_defuse_rounds"]:
        out.append(f"- They went for the defuse in {o['their_defuse_rounds']} of the {o['planted']} rounds we planted "
                   f"and finished it in {o['planted_defused']}.")
    if not known:
        out.append("- Who planted or defused, and attempts that did not finish, could not be read from this match's replays.")
    return out


def player_objective(events: dict[int, dict], ours: Optional[set[str]]) -> dict[str, dict[str, Any]]:
    """norm(username) -> {"plants": [(round, op)], "defuses": [...], "cut": n}: the plants and defuses
    each of our players made, from the replay's countdowns."""
    out: dict[str, dict[str, Any]] = {}
    for rn, e in sorted(events.items()):
        for a in e.get("objective_attempts") or []:
            who = norm(a.get("username"))
            if not who or not a.get("ours") or (ours is not None and who not in ours):
                continue
            row = out.setdefault(who, {"plants": [], "defuses": [], "cut": 0})
            if not a["completed"]:
                row["cut"] += 1
            else:
                row["plants" if a["kind"] == "plant" else "defuses"].append((int(rn), a.get("operator") or ""))
    return out


def composition_lines(match: Any, ours: Optional[set[str]]) -> list[str]:
    """Which operators we picked, and whether attack had a hard breacher."""
    atk: dict[str, int] = {}
    dfn: dict[str, int] = {}
    breach_rounds = 0
    atk_rounds = 0
    for r in sorted(match.rounds, key=lambda x: x.round_number):
        ops = [str(getattr(getattr(s, "operator", None), "name", "") or "")
               for s in r.player_stats if ours is None or norm(s.player.name) in ours]
        ops = [o for o in ops if o]
        if not ops:
            continue
        target = atk if r.side == "attack" else dfn
        for o in ops:
            target[o] = target.get(o, 0) + 1
        if r.side == "attack":
            atk_rounds += 1
            breach_rounds += any(norm(o) in HARD_BREACH for o in ops)
    out = []
    top = lambda d: ", ".join(f"{o} x{n}" if n > 1 else o for o, n in sorted(d.items(), key=lambda kv: (-kv[1], kv[0]))[:8])
    if atk:
        line = f"- Attack operators: {top(atk)}."
        if atk_rounds:
            line += f" A hard breacher (Thermite, Hibana, Ace or Maverick) was on the team in {breach_rounds} of {atk_rounds} attack rounds."
        out.append(line)
    if dfn:
        out.append(f"- Defense operators: {top(dfn)}.")
    return out


def utility_team_lines(table: dict[str, dict[str, Any]], display: dict[str, str]) -> list[str]:
    util = {n: player_utility(r) for n, r in table.items()}
    util = {n: u for n, u in util.items() if u}
    if not util:
        return []
    ua = sum(u["attack"]["used_rounds"] for u in util.values())
    na = sum(u["attack"]["rounds"] for u in util.values())
    ud = sum(u["defense"]["used_rounds"] for u in util.values())
    nd = sum(u["defense"]["rounds"] for u in util.values())
    tot_u, tot_n = ua + ud, na + nd
    out = [f"- Operator gadgets were used in {tot_u} of {tot_n} operator-rounds that had a countable gadget "
           f"({tot_u / tot_n:.0%}): attack {ua}/{na}, defense {ud}/{nd}."]
    for name, u in sorted(util.items(), key=lambda kv: kv[0].lower()):
        who = display.get(norm(name), name)
        bits = []
        for side, lab in (("defense", "defense"), ("attack", "attack")):
            s = u[side]
            if s["rounds"]:
                bits.append(f"{lab} {s['used_rounds']}/{s['rounds']} ({', '.join(s['ops'][:3])})")
        out.append(f"- {who}: gadget used in " + "; ".join(bits)
                   + f". Charges used {u['charges_used']} of {u['charges_total']}.")
    return out


# ── one player's standing ────────────────────────────────────────────────

def objective_text(rounds: list[tuple[int, str]]) -> str:
    return ", ".join(f"R{n}" + (f" {op}" if op else "") for n, op in sorted(rounds))


def player_facts(row: dict[str, Any], team: dict[str, Any], baseline: Optional[dict[str, Any]],
                 opening: Optional[dict[str, int]] = None, utility: Optional[dict[str, Any]] = None,
                 objective: Optional[dict[str, Any]] = None,
                 ) -> list[tuple[str, str]]:
    """[(mark, sentence)] where mark is '+' (a strength), '-' (a weakness) or '='."""
    facts: list[tuple[str, str]] = []

    if objective:
        # Getting the defuser down (or up) wins rounds; it is credited before any frag number.
        if objective["plants"]:
            n = len(objective["plants"])
            facts.append(("+", f"Planted the defuser in {n} round{'s' if n != 1 else ''} ({objective_text(objective['plants'])})"))
        if objective["defuses"]:
            n = len(objective["defuses"])
            facts.append(("+", f"Disabled the enemy defuser in {n} round{'s' if n != 1 else ''} ({objective_text(objective['defuses'])})"))

    if utility:
        # Objective play comes first: using the operator's gadget is what the pick is for.
        for side in ("defense", "attack"):
            s = utility[side]
            if s["rounds"] < 2:
                continue
            rate = s["used_rounds"] / s["rounds"]
            ops = ", ".join(s["ops"][:3])
            if rate >= DEFENSE_FIRST_USE:
                facts.append(("+", f"Used their gadget in {s['used_rounds']} of {s['rounds']} {side} rounds ({ops})"))
            elif rate <= LOW_USE:
                facts.append(("-", f"Used their gadget in only {s['used_rounds']} of {s['rounds']} {side} rounds ({ops})"))
            else:
                facts.append(("=", f"Used their gadget in {s['used_rounds']} of {s['rounds']} {side} rounds ({ops})"))

    if team["players"] > 1 and team["kd"] > 0:
        b = band_ratio(row["kd"], team["kd"])
        facts.append((_mark(b), f"K/D {row['kd']:.2f} is {b} the team's {team['kd']:.2f} tonight"))

    have_base = bool(baseline) and baseline["matches"] >= MIN_BASELINE_MATCHES
    if have_base:
        b = band_ratio(row["kd"], baseline["kd"])
        facts.append((_mark(b), f"K/D {row['kd']:.2f} is {b} their usual {baseline['kd']:.2f} "
                                f"(over {baseline['matches']} earlier matches)"))
        b = band_points(row["survival"], baseline["survival"])
        facts.append((_mark(b), f"Survival {row['survival']:.0%} is {b} their usual {baseline['survival']:.0%}"))
    elif team["players"] > 1:
        b = band_points(row["survival"], team["survival"])
        facts.append((_mark(b), f"Survival {row['survival']:.0%} is {b} the team's {team['survival']:.0%} tonight"))

    if row["ewr"] is not None and row["et"] >= MIN_DUELS_FOR_RATE:
        ref = baseline["ewr"] if have_base and baseline.get("ewr") is not None else team["ewr"]
        who = "their usual" if have_base and baseline.get("ewr") is not None else "the team's"
        if ref is not None:
            b = band_points(row["ewr"], ref)
            facts.append((_mark(b), f"Gunfights won {row['ew']}/{row['et']} ({row['ewr']:.0%}) is {b} {who} {ref:.0%}"))

    if opening:
        fk, fd = opening.get("first_kills", 0), opening.get("first_deaths", 0)
        if fk or fd:
            mark = "+" if fk >= 3 and fk > fd else "-" if fd >= 3 and fd > fk else "="
            facts.append((mark, f"Opening kills: got the first kill in {fk} round(s), died first in {fd}"))
    return facts


def _weight(mark: str, text: str) -> float:
    if mark == "=":
        return 0.0
    if text.startswith("Used their gadget"):
        return 4.0 if " only " in text else 3.0       # objective play outranks frags
    if text.startswith(("Planted the defuser", "Disabled the enemy defuser")):
        return 3.5
    w = 2.0 if "WELL " in text else 1.0
    if "their usual" in text:
        w += 0.5            # a player's own history says more than tonight's team average
    if text.startswith("K/D"):
        w *= 0.5            # kills are supporting evidence, not the headline
    return w


def pick_lines(facts: list[tuple[str, str]]) -> tuple[Optional[str], Optional[str]]:
    """(strength, weakness) chosen in code: the strongest (+) fact and the strongest (-) fact,
    or None. The model is never asked to judge which is which."""
    plus = [(_weight(m, t), t) for m, t in facts if m == "+"]
    minus = [(_weight(m, t), t) for m, t in facts if m == "-"]
    strength = max(plus)[1] if plus else None
    weakness = max(minus)[1] if minus else None
    return strength, weakness


def drill_for(weakness: str) -> str:
    """A fixed, sensible drill for the kind of weakness found (the model's drills were generic
    and sometimes backwards, e.g. 'take riskier plays' for a support player)."""
    w = weakness.lower()
    if w.startswith("used their gadget"):
        return ("Before each round decide where every charge goes (on defense, in prep; on attack, before the "
                "first push), and check in the replay when it stayed unused.")
    if w.startswith("survival"):
        return ("Replay the two rounds where you died earliest and ask whether you could have fallen back "
                "or held the angle longer instead of pushing out.")
    if w.startswith("gunfights"):
        return ("Ten minutes of crosshair-placement practice at head height before the next session, then "
                "check the gunfights you lost in the replay.")
    if w.startswith("opening kills"):
        return "Take first contact only with a teammate ready to trade, and gather info before committing to a peek."
    if w.startswith("k/d"):
        return ("Replay each of your deaths and note whether a teammate could have traded you; if not, hold "
                "the angle until one can.")
    return "Replay the rounds where this showed up and write down one thing you would do differently."


def focus_points(match: Any, facts: dict[str, Any]) -> str:
    """The 'what to focus on next' list, from measured numbers only. Objective play and
    utility come first; kills and duels are supporting evidence, not the headline."""
    pts: list[str] = []

    o = facts.get("objective")
    if o and o.get("tracked"):
        if o["attack_rounds"] >= 3 and o["planted"] / o["attack_rounds"] < 0.5:
            pts.append(f"Plants: we planted in only {o['planted']} of {o['attack_rounds']} attack rounds.")
        elif o["planted"] and o["planted_defused"] >= 2:
            pts.append(f"Post-plant: the defuser was disabled in {o['planted_defused']} of {o['planted']} rounds we planted.")
        if o["enemy_planted"] >= 2 and o["enemy_planted_won"] / o["enemy_planted"] <= 1 / 3:
            line = (f"Defense after a plant: they planted in {o['enemy_planted']} rounds and we won only "
                    f"{o['enemy_planted_won']} (defuser disabled in {o['we_defused']}")
            if o.get("attempts_known"):
                line += f"; we started a defuse in {o['counter_rounds']}"
            pts.append(line + ").")
        elif o.get("attempts_known") and o["enemy_planted"] >= 2 and o["counter_rounds"] / o["enemy_planted"] <= 1 / 3:
            pts.append(f"Counter-defuse: they planted in {o['enemy_planted']} rounds and we started a defuse in only "
                       f"{o['counter_rounds']}.")

    u = facts.get("utility")
    if u and u.get("measured") and u["total"] >= 6:
        if u["used"] / u["total"] < 0.6:
            pts.append(f"Utility: operator gadgets were used in only {u['used']} of {u['total']} operator-rounds "
                       f"({u['used'] / u['total']:.0%}).")
        if u["gaps"]:
            worst = ", ".join(f"{n} on {side} ({used}/{n_r})" for n, side, used, n_r in u["gaps"][:3])
            pts.append(f"Gadgets left unused: {worst}.")

    s = facts.get("secondary") or {}
    if s.get("measured") and s["rounds"] >= 12 and s["used"] / s["rounds"] < 0.35:
        pts.append(f"Secondary gadgets (frags, stuns, claymores, wire): used in only {s['used']} of {s['rounds']} "
                   f"player-rounds that carried one.")

    rs = list(match.rounds)
    rec = {}
    for side in ("attack", "defense"):
        sr = [r for r in rs if r.side == side]
        rec[side] = (sum(1 for r in sr if r.outcome == "win"), len(sr))
    (aw, an), (dw, dn) = rec["attack"], rec["defense"]
    if an >= 3 and dn >= 3:
        a_rate, d_rate = aw / an, dw / dn
        if abs(a_rate - d_rate) >= 0.25:
            weak, strong = ("Defense", "attack") if d_rate < a_rate else ("Attack", "defense")
            (w, n), (w2, n2) = (rec["defense"], rec["attack"]) if weak == "Defense" else (rec["attack"], rec["defense"])
            pts.append(f"{weak} was the weaker side: {w}-{n - w} against {w2}-{n2 - w2} on {strong}.")

    o = facts["opening"]
    if o["conceded_rounds"] >= 2 and o["conceded_wins"] / o["conceded_rounds"] <= 1 / 3:
        extra = ""
        if o["first_kill_rounds"]:
            extra = f" (we won {o['first_kill_wins']} of the {o['first_kill_rounds']} where we got it)"
        pts.append(f"Opening duels: they got the first kill in {o['conceded_rounds']} rounds and we won only "
                   f"{o['conceded_wins']} of them{extra}.")

    c = facts.get("comms_counts") or {}
    if c.get("talk_over", 0) >= 20:
        line = f"Comms: {c['talk_over']} moments of two people talking over each other mid-round"
        if c.get("callout_then_death", 0) >= 5:
            line += f", and {c['callout_then_death']} callouts followed by a teammate dying within seconds"
        pts.append(line + ".")
    elif c.get("callout_then_death", 0) >= 5:
        pts.append(f"Comms: {c['callout_then_death']} callouts were followed by a teammate dying within seconds.")

    if not pts:
        return "1. No clear weakness stands out in the numbers; keep doing what is working."
    return "\n".join(f"{i}. {p}" for i, p in enumerate(pts[:4], 1))


# ── bundle for the match prompt ───────────────────────────────────────────

def build_match_facts(match: Any, ours: Optional[set[str]], events: dict[int, dict],
                      display: dict[str, str], report_players: Optional[set[str]] = None,
                      usual: Optional[dict[str, int]] = None, positions: Optional[dict[int, dict]] = None,
                      places: Optional[tuple[dict, list]] = None) -> dict[str, Any]:
    """`ours`: our whole team (team numbers). `report_players`: who is listed by name (the saved team list).
    `usual`: totals over the team's earlier matches (usual_baseline), when there are enough.
    `positions`: {round: player paths} from the replays; `places`: (site model, floors[, walk map[, usual walls]])
    for this map."""
    table = player_table(match, ours)
    team = team_totals(table)
    opening = opening_summary(match, events)
    objective = objective_summary(match, events)
    measured = utility_measured(match, ours, events)
    named = {n: r for n, r in table.items() if report_players is None or norm(n) in report_players}
    secondary = secondary_summary(player_secondary(events, ours), report_players)

    util_used = util_total = 0
    gaps: list[tuple[str, str, int, int]] = []
    for name, r in table.items():
        if report_players is not None and norm(name) not in report_players:
            continue            # one set of players everywhere: the utility totals match the lines under them
        pu = player_utility(r)
        if not pu:
            continue
        util_used += pu["attack"]["used_rounds"] + pu["defense"]["used_rounds"]
        util_total += pu["attack"]["rounds"] + pu["defense"]["rounds"]
        for side in ("attack", "defense"):
            s = pu[side]
            if s["rounds"] >= 2 and s["used_rounds"] / s["rounds"] <= LOW_USE:
                gaps.append((display.get(norm(name), name), side, s["used_rounds"], s["rounds"]))
    gaps.sort(key=lambda g: (g[2] / g[3], -g[3]))

    pos_lines: list[str] = []
    if positions and ours:
        try:
            walk = places[2] if places and len(places) > 2 else None
            pos_lines = positioning_lines(positioning(match, ours, positions, walk), usual)
            if places and places[0]:
                pos_lines += death_places(match, ours, positions, places[0], places[1])
        except Exception:                                  # a nicety: never a reason to lose the debrief
            pos_lines = []
    wall_lines: list[str] = []
    if positions and ours:
        try:
            spots = places[3] if places and len(places) > 3 else None
            wall_lines = setup_lines(setup(match, ours, positions, spots), display)
        except Exception:
            wall_lines = []

    return {
        "positioning_lines": pos_lines,
        "setup_lines": wall_lines,
        "ours": ours,
        "table": table,
        "team": team,
        "round_patterns": round_patterns(match),
        "round_kda": round_kda(match, ours),
        "usual_lines": usual_lines(match_record(match, ours, events, positions), usual) if ours else [],
        "opening": opening,
        "opening_lines": opening_lines(opening),
        "clutches": clutch_lines(events, ours, display),
        "objective": objective,
        "objective_lines": objective_lines(objective, display, report_players),
        "composition_lines": composition_lines(match, ours),
        "utility": {"measured": measured, "used": util_used, "total": util_total, "gaps": gaps},
        "utility_lines": utility_team_lines(named, display) if measured else [],
        "secondary": secondary,
        "secondary_lines": secondary_lines(secondary),
    }


def section_body(text: str, header: str) -> str:
    m = re.search(rf"^##\s*{re.escape(header)}\s*$(.*?)(?=^##\s|\Z)", text or "", re.S | re.M | re.I)
    return m.group(1).strip() if m else ""


def assemble_report(model_text: str, facts: dict[str, Any], focus: str) -> str:
    """The final debrief: the model writes the summary and the comms paragraph; everything that
    is counted or judged comes from code, in a fixed order."""
    objective = "\n".join(facts.get("objective_lines") or [])
    utility = []
    if facts.get("utility", {}).get("measured"):
        utility += facts.get("utility_lines") or []
    else:
        utility.append("- Gadget use could not be read from this match's replays, so it is not judged here.")
    utility += facts.get("secondary_lines") or []
    utility += facts.get("composition_lines") or []
    sections = [
        ("MATCH SUMMARY", section_body(model_text, "MATCH SUMMARY")),
        ("ROUND PATTERNS", facts["round_patterns"]),
        ("COMPARED WITH YOUR USUAL", "\n".join(facts.get("usual_lines") or [])),
        ("OBJECTIVE PLAY", objective),
        ("POSITIONING", "\n".join(facts.get("positioning_lines") or [])),
        ("SITE SETUP & BREACHES", "\n".join(facts.get("setup_lines") or [])),
        ("UTILITY & OPERATORS", "\n".join(utility)),
        ("WHAT TO FOCUS ON NEXT", focus),
        ("COMMUNICATION", section_body(model_text, "COMMUNICATION") or "No comms data recorded."),
    ]
    return "\n\n".join(f"## {h}\n{b}" for h, b in sections if b).strip() + "\n"


def replace_section(text: str, header: str, body: str) -> str:
    """Swaps the body of a '## HEADER' section for `body` (adds the section if missing)."""
    pat = re.compile(rf"(^##\s*{re.escape(header)}\s*$)(.*?)(?=^##\s|\Z)", re.S | re.M | re.I)
    if pat.search(text):
        return pat.sub(lambda m: f"{m.group(1)}\n{body}\n\n", text, count=1).rstrip() + "\n"
    return text.rstrip() + f"\n\n## {header}\n{body}\n"
