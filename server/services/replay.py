"""
Round playback data for the /replay viewer.

Where /film places our deaths, this hands the page the full movement for a round -- every
player's (x, y, z, yaw) trace at the decoded tick rate -- so the client can play the round back
like an in-game replay. Same stores as /film (round_<n>_positions + round_<n>_events + the roster),
and the same naming rule: our side carries names, the enemy side is blanked (shown by operator on
the page), and drone owners are dropped (we only keep the count), since enemy handles never leave here.
"""
from __future__ import annotations

import re
from typing import Optional

from analysis.match_builder import load_round_positions
from server.match_db import get_match_repo
from server.services import film


def _norm(s) -> str:
    return re.sub(r"[^a-z0-9]", "", str(s or "").lower())


def _round(match, rn: int, pos_round: dict, ours: set, ev: dict) -> dict:
    players = pos_round.get("players") or {}
    died = {_norm(k): v for k, v in (pos_round.get("died") or {}).items()}
    op_by = {_norm(s.player.name): (getattr(getattr(s, "operator", None), "name", "") or "")
             for r in match.rounds if int(r.round_number) == rn for s in r.player_stats}

    tally: dict = {}
    for name, v in players.items():
        if _norm(name) in ours:
            tally[v.get("team")] = tally.get(v.get("team"), 0) + 1
    our_team = max(tally, key=tally.get) if tally else pos_round.get("def_team")

    out_players = []
    for name, v in players.items():
        team = v.get("team")
        enemy = team != our_team
        pts = [[round(t, 1), round(x, 1), round(y, 1), round(z, 1), (round(yaw) if yaw is not None else None)]
               for (t, x, y, z, yaw) in (v.get("pts") or [])]
        out_players.append({
            "name": "" if enemy else name,                 # enemies are shown by operator only
            "team": team, "ours": _norm(name) in ours,
            "op": op_by.get(_norm(name), ""),
            "died": died.get(_norm(name)), "pts": pts,
        })
    drones = [{"t": round(d["t"], 1), "prep": bool(d.get("prep"))} for d in (pos_round.get("drones") or [])]
    linked = sum(1 for d in (pos_round.get("drones") or []) if d.get("who"))
    site = next((r.site for r in match.rounds if int(r.round_number) == rn), "")
    return {"map": match.map, "round": rn, "site": site, "def_team": pos_round.get("def_team"),
            "our_team": our_team, "players": out_players, "drones": drones, "drones_linked": linked,
            "plant_clock": ev.get("plant_clock"), "planted_by_us": ev.get("planted_by_us")}


def replay_round(match_id: int, round_number: Optional[int] = None) -> dict:
    """One round's full playback. round_number None -> the first round that has traces. The result
    always carries `rounds` (every round with traces) so the page can offer a round picker."""
    repo = get_match_repo()
    match = repo.get_match_full(match_id)
    if match is None:
        return {"error": f"Match {match_id} not found."}
    pos = load_round_positions(repo.db, match_id)
    if not pos:
        return {"error": "This match has no position traces (imported before positions, or none decoded)."}
    rounds = sorted(int(r) for r in pos.keys())
    rn = int(round_number) if round_number else rounds[0]
    if rn not in pos:
        return {"error": f"Round {rn} has no traces.", "rounds": rounds}
    ours = film._ours(repo, match_id) or set()
    data = _round(match, rn, pos[rn], ours, film._events(repo, match_id).get(rn, {}))
    data["rounds"] = rounds
    data["match"] = {"match_id": match_id, "map": match.map, "opponent": match.opponent_name,
                     "result": match.result, "score": match.match_score()}
    return data
