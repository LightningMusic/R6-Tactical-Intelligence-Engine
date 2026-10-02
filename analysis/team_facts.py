"""
Facts the AI debrief is given, worked out in plain code so the model never has
to do arithmetic or judge a number: team-only player tables, round patterns,
first-kill conversion, and each player's standing against the team and against
their own history.

Everything here is pure (no database, no model) so it can be tested directly.
"""
from __future__ import annotations

import re
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
                                          "survived": 0, "ew": 0, "et": 0})
            row["k"] += int(s.kills)
            row["d"] += int(s.deaths)
            row["a"] += int(s.assists)
            row["ew"] += int(s.engagements_won)
            row["et"] += int(s.engagements_taken)
            row["rounds"] += 1
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

def opening_summary(match: Any, events: dict[int, dict]) -> dict[str, Any]:
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
    for n in sorted(events):
        who = events[n].get("clutch_player") or ""
        if who and (ours is None or norm(who) in ours):
            out.append(f"R{n:02d}: {display.get(norm(who), who)} won the round alone "
                       f"({int(events[n].get('clutch_kills') or 0)} kill(s))")
    return out


def opening_counts(events: dict[int, dict]) -> dict[str, dict[str, int]]:
    """{player: {'first_kills': n, 'first_deaths': n}} from the kill feed."""
    out: dict[str, dict[str, int]] = {}
    for e in events.values():
        killer, victim = e.get("first_blood_killer") or "", e.get("first_blood_victim") or ""
        if killer:
            out.setdefault(norm(killer), {"first_kills": 0, "first_deaths": 0})["first_kills"] += 1
        if victim:
            out.setdefault(norm(victim), {"first_kills": 0, "first_deaths": 0})["first_deaths"] += 1
    return out


# ── one player's standing ────────────────────────────────────────────────

def player_facts(row: dict[str, Any], team: dict[str, Any], baseline: Optional[dict[str, Any]],
                 opening: Optional[dict[str, int]] = None) -> list[tuple[str, str]]:
    """[(mark, sentence)] where mark is '+' (a strength), '-' (a weakness) or '='."""
    facts: list[tuple[str, str]] = []

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
    w = 2.0 if "WELL " in text else 1.0
    if "their usual" in text:
        w += 0.5            # a player's own history says more than tonight's team average
    return w if mark != "=" else 0.0


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
    """The 'what to focus on next' list, from measured numbers only."""
    pts: list[str] = []
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
    return "\n".join(f"{i}. {p}" for i, p in enumerate(pts[:3], 1))


# ── bundle for the match prompt ───────────────────────────────────────────

def build_match_facts(match: Any, ours: Optional[set[str]], events: dict[int, dict],
                      display: dict[str, str]) -> dict[str, Any]:
    table = player_table(match, ours)
    team = team_totals(table)
    opening = opening_summary(match, events)
    return {
        "ours": ours,
        "table": table,
        "team": team,
        "round_patterns": round_patterns(match),
        "round_kda": round_kda(match, ours),
        "opening": opening,
        "opening_lines": opening_lines(opening),
        "clutches": clutch_lines(events, ours, display),
    }


def replace_section(text: str, header: str, body: str) -> str:
    """Swaps the body of a '## HEADER' section for `body` (adds the section if missing)."""
    pat = re.compile(rf"(^##\s*{re.escape(header)}\s*$)(.*?)(?=^##\s|\Z)", re.S | re.M | re.I)
    if pat.search(text):
        return pat.sub(lambda m: f"{m.group(1)}\n{body}\n\n", text, count=1).rstrip() + "\n"
    return text.rstrip() + f"\n\n## {header}\n{body}\n"
