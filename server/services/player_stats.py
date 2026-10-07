"""
"My stats": one player's own numbers from the team's stored matches, for their invite link.

Read-only and cheap on purpose: the server runs on the host's own gaming PC, so this never starts an
analysis, never calls the AI, and never reads a replay. It only adds up what the normal pipeline already
stored (rounds, player stats, the replay's round events and the debrief's per-player intel).

A player sees only matches where they played on our team, and only their own lines.
"""
from __future__ import annotations

import json
from collections import Counter
from typing import Any, Optional

MAX_MATCHES = 12
MIN_USUAL_MATCHES = 3


def _norm(s: Any) -> str:
    return str(s or "").strip().lower()


def _kd(k: int, d: int) -> float:
    return round(k / d, 2) if d else float(k)


def _events_and_meta(conn, ids: list[int]) -> tuple[dict, dict, dict]:
    events: dict[int, dict[int, dict]] = {}
    teams: dict[int, set[str]] = {}
    intel: dict[int, dict[str, str]] = {}
    if not ids:
        return events, teams, intel
    marks = ",".join("?" * len(ids))
    for r in conn.execute(f"SELECT match_id, metric_name, metric_text FROM derived_metrics "
                          f"WHERE match_id IN ({marks}) AND metric_text IS NOT NULL AND "
                          f"(metric_name LIKE 'round_%_events' OR metric_name = 'our_players' "
                          f"OR metric_name LIKE 'ai_player_intel::%')", ids):
        mid, name, text = r[0], r[1], r[2]
        try:
            if name == "our_players":
                teams[mid] = {_norm(n) for n in json.loads(text)}
            elif name.startswith("ai_player_intel::"):
                intel.setdefault(mid, {})[_norm(name.split("::", 1)[1])] = text
            else:
                events.setdefault(mid, {})[int(name.split("_")[1])] = json.loads(text)
        except Exception:
            continue
    return events, teams, intel


def match_line(match: Any, me: str, events: dict[int, dict], intel: Optional[str]) -> Optional[dict]:
    """This player's own line for one match, or None if they did not play in it."""
    rows = []
    for r in sorted(match.rounds, key=lambda r: r.round_number):
        for s in r.player_stats:
            if _norm(s.player.name) == me:
                rows.append((r, s))
    if not rows:
        return None
    k = sum(int(s.kills) for _, s in rows)
    d = sum(int(s.deaths) for _, s in rows)
    a = sum(int(s.assists) for _, s in rows)
    survived = sum(1 for _, s in rows if int(s.deaths) == 0)
    ops = Counter((str(getattr(getattr(s, "operator", None), "name", "") or "?"), r.side) for r, s in rows)
    won = sum(1 for r in match.rounds if r.outcome == "win")
    fk = fd = known = 0
    plants, defuses, clutches = [], [], []
    for r, _ in rows:
        e = events.get(int(r.round_number)) or {}
        if e.get("kill_order"):                  # first kills are only trusted from correctly ordered feeds
            known += 1
            fk += _norm(e.get("first_blood_killer")) == me
            fd += _norm(e.get("first_blood_victim")) == me
            if _norm(e.get("clutch_player")) == me:
                clutches.append({"round": int(r.round_number), "kills": int(e.get("clutch_kills") or 0)})
        for at in e.get("objective_attempts") or []:
            if at.get("ours") and at.get("completed") and _norm(at.get("username")) == me:
                (plants if at.get("kind") == "plant" else defuses).append(int(r.round_number))
    dt = getattr(match, "datetime_played", None)
    return {
        "match_id": match.match_id, "date": dt.isoformat()[:10] if hasattr(dt, "isoformat") else str(dt or "")[:10],
        "map": match.map, "score_us": won, "score_them": len(match.rounds) - won,
        "rounds": len(rows), "k": k, "d": d, "a": a, "kd": _kd(k, d),
        "survival": round(survived / len(rows), 3),
        "operators": [{"op": op, "side": side, "rounds": n} for (op, side), n in ops.most_common()],
        "first_kills": fk, "first_deaths": fd, "opening_rounds_known": known,
        "plants": plants, "defuses": defuses, "clutches": clutches,
        "intel": intel,
    }


def summarize(lines: list[dict]) -> dict:
    rounds = sum(m["rounds"] for m in lines)
    k, d = sum(m["k"] for m in lines), sum(m["d"] for m in lines)
    surv = sum(round(m["survival"] * m["rounds"]) for m in lines)
    ops = Counter()
    for m in lines:
        for o in m["operators"]:
            ops[(o["op"], o["side"])] += o["rounds"]
    return {
        "matches": len(lines), "rounds": rounds, "k": k, "d": d, "kd": _kd(k, d),
        "survival": round(surv / rounds, 3) if rounds else 0.0,
        "first_kills": sum(m["first_kills"] for m in lines), "first_deaths": sum(m["first_deaths"] for m in lines),
        "opening_rounds_known": sum(m["opening_rounds_known"] for m in lines),
        "plants": sum(len(m["plants"]) for m in lines), "clutches": sum(len(m["clutches"]) for m in lines),
        "top_operators": [{"op": op, "side": side, "rounds": n} for (op, side), n in ops.most_common(5)],
    }


def player_stats(repo, username: str, limit: int = MAX_MATCHES) -> dict:
    """Everything for one player's page. `repo` is the server's match Repository."""
    me = _norm(username)
    matches = repo.get_all_matches_full()                       # oldest first
    with repo.db.get_connection() as conn:
        events, teams, intel = _events_and_meta(conn, [m.match_id for m in matches])
    lines = []
    for m in matches:
        team = teams.get(m.match_id)
        if team is not None and me not in team:                 # played against us (or not at all)
            continue
        line = match_line(m, me, events.get(m.match_id) or {}, (intel.get(m.match_id) or {}).get(me))
        if line:
            lines.append(line)
    out: dict[str, Any] = {"username": username, "totals": summarize(lines),
                           "matches": list(reversed(lines))[:limit], "latest_vs_usual": None}
    real = [m for m in lines if m["rounds"] >= 4]
    if len(real) > MIN_USUAL_MATCHES:
        latest, before = real[-1], summarize(real[:-1])
        out["latest_vs_usual"] = {"match_id": latest["match_id"], "matches_before": before["matches"],
                                  "kd": [latest["kd"], before["kd"]],
                                  "survival": [latest["survival"], before["survival"]]}
    return out
