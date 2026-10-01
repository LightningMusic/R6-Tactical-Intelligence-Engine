"""
analysis/match_builder.py

Turns RecImporter's ImportResult objects (rounds parsed straight from
.rec replay files — no manual entry involved) into match/round/player-stat
rows in a Repository's database.

This is the exact logic app/session_manager.py's SessionManager has always
run locally, right after r6-dissect finishes, fully automatically (there is
no manual-entry step in this path — see SessionManager for the local flow's
own docstring). It's factored out here, standalone and Qt-free, so the
server's Milestone 4 phase 2 pipeline (server/services/session_processing.py)
can build an identical match record from a session's bundled .rec files
without duplicating — and risking drifting from — the client's own logic.
SessionManager delegates to this module rather than keeping its own copy.
"""
from __future__ import annotations

import json
from typing import TYPE_CHECKING, Callable, List, Optional

from models.round_resources import RoundResources

if TYPE_CHECKING:
    from database.repositories import Repository
    from models.import_result import ImportResult
    from models.round import Round


def _noop_log(msg: str) -> None:
    pass


def build_matches_from_import_results(
    repo: "Repository",
    results: List["ImportResult"],
    recording_path: Optional[str] = None,
    log: Optional[Callable[[str], None]] = None,
    catalog_hints: Optional[dict] = None,
) -> None:
    """
    Creates match records, saves rounds, player stats, and round events for
    every ImportResult that has parsed rounds and doesn't already have a
    match_id. Mutates each ImportResult in place, setting .match_id/.map_id
    once created — callers (e.g. to attach a transcript afterwards) can read
    those back off the same `results` list.

    Every result first goes through the game catalog, which learns any new
    operator or map the replay introduces (database/game_catalog.py).
    catalog_hints carries names the uploading client already knew, when
    this runs on the server.
    """
    from database.game_catalog import learn_from_import

    log = log or _noop_log

    for result in results:
        if not result.rounds:
            log("  Skipping match creation — no rounds parsed.")
            continue
        if result.match_id is not None:
            log(f"  Match {result.match_id} already exists.")
            continue

        try:
            learn_from_import(repo.db, result, hints=catalog_hints, log=log)
        except Exception as cat_err:
            log(f"  [catalog] Could not update the game catalog (non-fatal): {cat_err}")

        map_name = result.map_name or "Unknown"
        if map_name.startswith("Map("):
            map_name = "Unknown"

        existing = _existing_match_id(repo, result)
        if existing is not None:
            result.match_id = existing
            log(f"  Match already recorded as match {existing} (same replay start time and map) — not importing it twice.")
            continue

        try:
            from models.match import Match

            map_id = result.map_id or repo.get_map_id_by_name(map_name)

            match = Match(
                match_id=None,
                datetime_played=_played_at(result.played_at),
                opponent_name="Imported",
                map=map_name,
                result=_match_result(result.score_us, result.score_them),
                recording_path=recording_path,
                rounds=[],
            )
            match_id = repo.insert_match(match)
            result.match_id = match_id
            result.map_id = map_id
            # map_game_id is what lets a match on a not-yet-named map be
            # renamed later, once someone names it (see game_catalog).
            # Never allowed to cost the match itself: on a database that
            # somehow skipped migration v5 the column won't exist yet, and
            # that must not stop the rounds and stats below being saved.
            try:
                with repo.db.get_connection() as conn:
                    conn.execute(
                        "UPDATE matches SET map_id = ?, map_game_id = ? WHERE match_id = ?",
                        (map_id, result.map_game_id, match_id),
                    )
                    conn.commit()
            except Exception as col_err:
                log(f"    (map ID not recorded on match {match_id}: {col_err} -- run migrations)")

            total_stats_saved = 0
            total_events_saved = 0

            for round_obj in result.rounds:
                round_obj.match_id = match_id

                resources = RoundResources(
                    resource_id=None,
                    round_id=0,
                    side=round_obj.side,
                    team_drones_start=10,
                    team_drones_lost=0,
                    team_reinforcements_start=10,
                    team_reinforcements_used=0,
                )
                round_id = repo.insert_round(round_obj, match_id)
                repo.insert_round_resources(resources, round_id)

                stats_saved = save_raw_player_stats(repo, round_id, round_obj, log)
                total_stats_saved += stats_saved

                if round_obj.round_events is not None:
                    try:
                        events_json = json.dumps(round_obj.round_events.to_dict())
                        metric_name = f"round_{round_obj.round_number}_events"
                        with repo.db.get_connection() as conn:
                            conn.execute(
                                """INSERT OR REPLACE INTO derived_metrics
                                   (match_id, metric_name, metric_value, is_ai_generated)
                                   VALUES (?, ?, 0, 0)""",
                                (match_id, metric_name),
                            )
                            conn.execute(
                                """UPDATE derived_metrics
                                   SET metric_text = ?
                                   WHERE match_id = ? AND metric_name = ?""",
                                (events_json, match_id, metric_name),
                            )
                            conn.commit()
                        total_events_saved += 1
                    except Exception as ev_err:
                        log(f"    Could not save events for R{round_obj.round_number}: {ev_err}")

            log(
                f"  ✓ Created match {match_id}: {map_name} "
                f"({len(result.rounds)} rounds, "
                f"{total_stats_saved} player stat rows, "
                f"{total_events_saved} kill feed sets)"
            )

        except Exception as e:
            log(f"  ✗ Failed to create match record: {e}")


def save_raw_player_stats(
    repo: "Repository",
    round_id: int,
    round_obj: "Round",
    log: Optional[Callable[[str], None]] = None,
) -> int:
    """
    Converts raw_player_stats dicts from the replay into PlayerRoundStats
    records in the database. Returns the number of rows saved.

    For players on our team: match against team_players by Ubisoft username
    OR any known alias (case-insensitive — see database/repositories.py
    resolve_player_by_username() and the Settings > Players "aliases" field,
    Milestone 6). If no match found, still save using username as name,
    creating a new (non-team) "ghost" player — use Settings > Players to
    merge that ghost into the correct team player once you notice it, which
    also remembers the username as an alias so it matches automatically
    next time.
    For opponent players: always saved as non-team-member guests.
    Stats available from replay: kills, deaths, assists, operator name.
    Everything else (engagements, gadget, ability) defaults to 0/None —
    identically on the client and the server, since neither has a manual
    "Match View" entry step to fill those in from replay data alone.
    """
    log = log or _noop_log

    if not round_obj.raw_player_stats:
        return 0

    saved = 0

    for raw in round_obj.raw_player_stats:
        username = raw.get("username", "")
        op_name = raw.get("operator", "")
        kills = int(raw.get("kills", 0))
        deaths = int(raw.get("deaths", 0))
        assists = int(raw.get("assists", 0))
        is_our_team = bool(raw.get("is_our_team", False))

        player = repo.resolve_player_by_username(username) if username else None

        if player is None and username:
            from models.player import Player

            new_player = Player(player_id=None, name=username, is_team_member=False)
            try:
                player_id = repo.insert_player(new_player)
                player = Player(player_id=player_id, name=username, is_team_member=False)
            except Exception as e:
                log(f"    Could not create player '{username}': {e}")
                continue

        if player is None or player.player_id is None:
            log("    Skipping stat row — no username in replay data")
            continue

        operator = None
        if raw.get("operator_db_id"):
            operator = repo.get_operator_by_id(int(raw["operator_db_id"]))
        if operator is None and op_name:
            operator = repo.get_operator_by_name(op_name)
            if operator is None:
                operator = repo.get_operator_by_name_fuzzy(op_name)

        if operator is None:
            log(f"    Operator '{op_name}' not found in DB — skipping player '{username}'")
            continue

        from models.player_round_stats import PlayerRoundStats

        stat = PlayerRoundStats(
            stat_id=None,
            round_id=round_id,
            player_id=player.player_id,
            player=player,
            operator=operator,
            kills=kills,
            deaths=deaths,
            assists=assists,
            # Replays don't record gunfights directly. Approximated from what
            # they do record -- each kill a gunfight won, each death one
            # lost -- instead of the 0/0 this used to hardcode, which every
            # AI report then read as "0% engagement win rate" for everyone.
            engagements_taken=kills + deaths,
            engagements_won=kills,
            ability_start=operator.ability_max_count,
            ability_used=0,
            secondary_gadget=None,
            secondary_start=0,
            secondary_used=0,
            plant_attempted=False,
            plant_successful=False,
        )

        try:
            repo.insert_player_round_stats(stat, round_id, player.player_id)
            saved += 1
        except Exception as e:
            log(f"    Could not save stats for '{username}': {e}")

    return saved


def _existing_match_id(repo: "Repository", result: "ImportResult") -> Optional[int]:
    """A match already stored for the same replay: same first-round start
    time (to the second) and, when both sides know it, the same map ID. The
    start time comes from the replay itself, so a re-import, a re-upload, or
    a restarted session produce the identical value."""
    if not result.played_at:
        return None
    try:
        stamp = _played_at(result.played_at).isoformat()
        with repo.db.get_connection() as conn:
            row = conn.execute(
                """SELECT match_id FROM matches
                   WHERE datetime = ?
                     AND (? IS NULL OR map_game_id IS NULL OR map_game_id = ?)
                   ORDER BY match_id LIMIT 1""",
                (stamp, result.map_game_id, result.map_game_id),
            ).fetchone()
        return int(row[0]) if row else None
    except Exception:
        return None


def _now():
    from datetime import datetime

    return datetime.now()


def _played_at(stamp: Optional[str]):
    """The replay's own timestamp as local time (matching what _now() has
    always produced), falling back to import time.

    The replay's JSON timestamp carries a "Z" (UTC) suffix it hasn't
    earned -- r6-dissect's own parser (header.go) reads the game's local
    clock with a timezone-less layout, which Go labels UTC by default
    without converting anything. The digits themselves are already local
    wall-clock time, so they're taken as-is; treating them as real UTC
    (converting to local a second time, as an earlier version of this
    function did) shifted every match's recorded time by this machine's
    UTC offset. See analysis/timeline_aligner.py's _extract_timestamps for
    the same fix, needed there for voice alignment."""
    from datetime import datetime

    if stamp:
        try:
            return datetime.fromisoformat(stamp.rstrip("Z"))
        except ValueError:
            pass
    return _now()


def _match_result(score_us: Optional[int], score_them: Optional[int]) -> Optional[str]:
    """Win/loss from the last round's score; None when unknown or level.
    A match abandoned partway still gets whichever side was ahead at the
    time -- the replays alone can't tell a finished match from one cut
    short."""
    if score_us is None or score_them is None or score_us == score_them:
        return None
    return "win" if score_us > score_them else "loss"
