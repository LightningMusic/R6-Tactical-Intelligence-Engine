"""
Everything the dashboard shows, computed from full Match objects with no Qt
involved, so it can run on a worker thread and be tested directly.
"""
from __future__ import annotations

import statistics
from typing import Optional

from analysis.metrics_engine import MetricsEngine


def effective_result(match) -> tuple[Optional[str], bool]:
    """(result, derived). Older imports never stored a win/loss; for those
    the result is taken from the round tally, and flagged as derived."""
    if match.result in ("win", "loss"):
        return match.result, False
    wins = sum(1 for r in match.rounds if r.outcome == "win")
    losses = sum(1 for r in match.rounds if r.outcome == "loss")
    if wins == losses:
        return None, False
    return ("win" if wins > losses else "loss"), True


def _rate(won: int, total: int) -> Optional[float]:
    return won / total if total else None


def build_dashboard(matches: list, team_ids: set[int], roster: list[tuple[int, str]]) -> dict:
    decided = []
    for m in matches:
        res, derived = effective_result(m)
        if res:
            decided.append((m, res, derived))

    wins = sum(1 for _, r, _ in decided if r == "win")
    losses = len(decided) - wins

    side = {"attack": [0, 0], "defense": [0, 0]}
    for m, _, _ in decided:
        for r in m.rounds:
            if r.side in side and r.outcome in ("win", "loss"):
                side[r.side][0] += r.outcome == "win"
                side[r.side][1] += 1

    streak_kind, streak = None, 0
    for _, res, _ in reversed(decided):
        if streak_kind is None:
            streak_kind, streak = res, 1
        elif res == streak_kind:
            streak += 1
        else:
            break

    return {
        "matches": len(matches),
        "decided": len(decided),
        "wins": wins,
        "losses": losses,
        "win_rate": _rate(wins, len(decided)),
        "atk": (side["attack"][0], side["attack"][1]),
        "def": (side["defense"][0], side["defense"][1]),
        "rounds_won": side["attack"][0] + side["defense"][0],
        "rounds_total": side["attack"][1] + side["defense"][1],
        "streak": (streak_kind, streak),
        "form": [res for _, res, _ in decided[-10:]],
        "last_played": matches[-1].datetime_played if matches else None,
        "recent": _recent(matches[-12:][::-1]),
        "maps": _maps(decided),
        "operators": _operators(decided),
        **_players(decided, team_ids, roster),
    }


def _recent(matches: list) -> list[dict]:
    out = []
    for m in matches:
        res, derived = effective_result(m)
        atk = [r for r in m.rounds if r.side == "attack"]
        dfn = [r for r in m.rounds if r.side == "defense"]
        out.append({
            "id": m.match_id,
            "date": m.datetime_played,
            "map": m.map or "Unknown",
            "opponent": m.opponent_name or "—",
            "rounds_won": sum(1 for r in m.rounds if r.outcome == "win"),
            "rounds_lost": sum(1 for r in m.rounds if r.outcome == "loss"),
            "atk": (sum(1 for r in atk if r.outcome == "win"), len(atk)),
            "def": (sum(1 for r in dfn if r.outcome == "win"), len(dfn)),
            "result": res,
            "derived": derived,
        })
    return out


def _maps(decided: list) -> list[dict]:
    data: dict[str, dict] = {}
    for m, res, _ in decided:
        d = data.setdefault(m.map or "Unknown", {"played": 0, "wins": 0, "atk": [0, 0], "def": [0, 0]})
        d["played"] += 1
        d["wins"] += res == "win"
        for r in m.rounds:
            key = "atk" if r.side == "attack" else "def"
            if r.outcome in ("win", "loss"):
                d[key][0] += r.outcome == "win"
                d[key][1] += 1
    return sorted(
        ({
            "map": name, "played": d["played"], "wins": d["wins"], "losses": d["played"] - d["wins"],
            "win_rate": _rate(d["wins"], d["played"]),
            "atk_rate": _rate(*d["atk"]), "def_rate": _rate(*d["def"]),
        } for name, d in data.items()),
        key=lambda x: (-x["played"], x["map"]),
    )


def _operators(decided: list) -> dict[str, list[dict]]:
    """Your team's picks only. Operators are locked to one side, so a pick
    whose side matches the side you played that round is yours; the other
    team's picks would otherwise be credited with your round results."""
    data: dict[tuple[str, str], list[int]] = {}
    for m, _, _ in decided:
        for r in m.rounds:
            if r.outcome not in ("win", "loss"):
                continue
            for ps in r.player_stats:
                if ps.operator.side != r.side:
                    continue
                d = data.setdefault((ps.operator.name, r.side), [0, 0])
                d[0] += r.outcome == "win"
                d[1] += 1
    out: dict[str, list[dict]] = {"attack": [], "defense": []}
    for (name, sd), (w, n) in data.items():
        out[sd].append({"operator": name, "rounds": n, "wins": w, "win_rate": _rate(w, n)})
    for sd in out:
        out[sd].sort(key=lambda x: (-x["rounds"], x["operator"]))
    return out


def _players(decided: list, team_ids: set[int], roster: list[tuple[int, str]]) -> dict:
    totals: dict[int, dict] = {}
    series: dict[int, list[dict]] = {}
    for m, res, _ in decided:
        if not m.rounds:
            continue
        engine = MetricsEngine(m)
        summary = engine.player_summary()
        tps = engine.tactical_performance_score()
        for pid, d in summary.items():
            if pid not in team_ids:
                continue
            t = totals.setdefault(pid, {
                "name": d["player"].name, "matches": 0, "kills": 0, "deaths": 0, "assists": 0,
                "rounds": 0, "survived": 0, "tps": [],
            })
            t["matches"] += 1
            t["kills"] += d["kills"]
            t["deaths"] += d["deaths"]
            t["assists"] += d["assists"]
            t["rounds"] += d["rounds_played"]
            t["survived"] += d["rounds_survived"]
            t["tps"].append(tps.get(pid, 0.0))
            series.setdefault(pid, []).append({
                "date": m.datetime_played, "map": m.map or "Unknown", "result": res,
                "k": d["kills"], "d": d["deaths"], "a": d["assists"],
                "kd": d["kd_ratio"], "survival": d["survival_rate"], "tps": tps.get(pid, 0.0),
            })

    board = []
    for pid, t in totals.items():
        board.append({
            "id": pid, "name": t["name"], "matches": t["matches"],
            "kills": t["kills"], "deaths": t["deaths"], "assists": t["assists"],
            "kd": t["kills"] / t["deaths"] if t["deaths"] else float(t["kills"]),
            "survival": _rate(t["survived"], t["rounds"]),
            "tps": sum(t["tps"]) / len(t["tps"]),
        })
    board.sort(key=lambda x: -x["tps"])

    detail = {}
    for pid, _name in roster:
        rows = series.get(pid, [])
        vals = [r["tps"] for r in rows]
        trend = None
        if len(vals) >= 4:
            split = max(1, len(vals) // 3)
            trend = sum(vals[-split:]) / split - sum(vals[:-split]) / (len(vals) - split)
        detail[pid] = {
            "rows": rows,
            "avg_tps": sum(vals) / len(vals) if vals else None,
            "trend": trend,
            "consistency": statistics.stdev(vals) if len(vals) > 1 else None,
        }
    return {"leaderboard": board, "player_detail": detail}
