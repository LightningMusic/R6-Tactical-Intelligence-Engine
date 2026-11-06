"""
Spatial film-room data for the /film page.

The replay's movement traces (stored per round as `round_N_positions`) let us place every one of our deaths
in the round: the floor and spot a player died on, the path they walked in the seconds before, the enemy who
killed them and the line it came along, and the nearest living teammate at that instant. The heavy lifting is
a pure transform in analysis.film_room; this service only loads a match's traces, kill events and roster from
the server's match database and hands them over, then adds the floor plan analysis.places already learns for
the map so floors are named the same way the /rooms heatmap names them.
"""
from __future__ import annotations

import json
import re
from typing import Optional

from analysis import film_room
from analysis.match_builder import load_round_positions
from server.match_db import get_match_repo


def _ours(repo, match_id: int) -> Optional[set[str]]:
    with repo.db.get_connection() as conn:
        row = conn.execute(
            "SELECT metric_text FROM derived_metrics WHERE match_id = ? AND metric_name = 'our_players'",
            (match_id,),
        ).fetchone()
    try:
        return {str(s).strip().lower() for s in json.loads(row[0])} if row and row[0] else None
    except Exception:
        return None


def _events(repo, match_id: int) -> dict[int, dict]:
    out: dict[int, dict] = {}
    with repo.db.get_connection() as conn:
        rows = conn.execute(
            "SELECT metric_name, metric_text FROM derived_metrics "
            "WHERE match_id = ? AND metric_name LIKE 'round_%_events' AND metric_text IS NOT NULL",
            (match_id,),
        ).fetchall()
    for r in rows:
        m = re.match(r"round_(\d+)_events", r["metric_name"])
        if m:
            try:
                out[int(m.group(1))] = json.loads(r["metric_text"])
            except Exception:
                pass
    return out


def _floor_plan(repo, match) -> Optional[list]:
    """The map's named floor levels from analysis.places.floors (same source as /rooms), learned from every
    stored round on this map; None when too few, in which case film_room learns floors from the death heights."""
    try:
        from analysis import places
        with repo.db.get_connection() as conn:
            ids = [r[0] for r in conn.execute("SELECT match_id FROM matches WHERE map = ?", (match.map,))]
            sites: dict = {}
            if ids:
                q = f"SELECT match_id, round_number, site FROM rounds WHERE match_id IN ({','.join('?' * len(ids))})"
                sites = {(r[0], int(r[1])): r[2] for r in conn.execute(q, ids)}
        rounds = []
        for mid in ids:
            for rn, pos in load_round_positions(repo.db, mid).items():
                rounds.append({"site": sites.get((mid, rn)) or "", "positions": pos})
        if sum(1 for r in rounds if r["site"]) < 3:
            return None
        return places.floors(rounds)
    except Exception:
        return None


def matches_with_film() -> list[dict]:
    """Every match that has stored position traces, newest first, with its roster usernames (the pickers)."""
    repo = get_match_repo()
    out: list[dict] = []
    with repo.db.get_connection() as conn:
        mids = [r[0] for r in conn.execute(
            "SELECT DISTINCT match_id FROM derived_metrics WHERE metric_name LIKE 'round_%_positions'")]
        for mid in mids:
            m = conn.execute(
                "SELECT match_id, datetime, map, opponent_name, result FROM matches WHERE match_id = ?",
                (mid,)).fetchone()
            if not m:
                continue
            orow = conn.execute(
                "SELECT metric_text FROM derived_metrics WHERE match_id = ? AND metric_name = 'our_players'",
                (mid,)).fetchone()
            try:
                players = sorted(json.loads(orow[0])) if orow and orow[0] else []
            except Exception:
                players = []
            out.append({"match_id": m["match_id"], "datetime": m["datetime"], "map": m["map"],
                        "opponent": m["opponent_name"], "result": m["result"], "players": players})
    out.sort(key=lambda r: (r["datetime"] or ""), reverse=True)
    return out


def film(match_id: int, player: Optional[str] = None) -> dict:
    """The placed deaths for one match (whole team, or just `player`)."""
    repo = get_match_repo()
    match = repo.get_match_full(match_id)
    if match is None:
        return {"error": f"Match {match_id} not found."}
    positions = load_round_positions(repo.db, match_id)
    if not positions:
        return {"error": "This match has no position traces (imported before positions, or none decoded)."}
    data = film_room.death_film(match, _ours(repo, match_id), positions, _events(repo, match_id),
                                player=player, floors=_floor_plan(repo, match))
    data["match"] = {"match_id": match_id, "map": match.map, "opponent": match.opponent_name,
                     "result": match.result, "score": match.match_score()}
    return data
