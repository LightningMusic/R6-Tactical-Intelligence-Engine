"""
analysis/event_parser.py

Parses r6-dissect matchFeedback arrays into structured RoundEvents objects.
Derives first-blood, trade kills, clutch detection, and per-player event stats
from the raw kill feed, then stores results as JSON in derived_metrics.

Designed to be called once per round during the import pipeline.
"""
from __future__ import annotations

import json
from typing import Optional

from models.round_events import (
    KillEvent, PlantEvent, RoundEvents,
    KILL_TYPES, DEFUSER_TYPES,
)


# A kill within this many seconds of the killer dying is a trade
TRADE_WINDOW_SEC = 4.0

# A defuse completing sooner than this after the plant is not a real defuse
MIN_DEFUSE_GAP_SEC = 5.0


def _parse_time(time_str: str) -> float:
    """
    Convert "HH:MM:SS" or "MM:SS" time string to seconds.
    Returns 0.0 on any parse failure.
    """
    try:
        parts = str(time_str or "").strip().split(":")
        if len(parts) == 3:
            return int(parts[0]) * 3600 + int(parts[1]) * 60 + float(parts[2])
        if len(parts) == 2:
            return int(parts[0]) * 60 + float(parts[1])
        return float(parts[0])
    except Exception:
        return 0.0


class EventParser:
    """
    Parses the matchFeedback list from one r6-dissect round JSON
    into a structured RoundEvents object.

    Usage:
        parser = EventParser(our_team_index=0)
        events = parser.parse(round_data["matchFeedback"], player_team_map)
    """

    def __init__(self, our_team_index: int, our_role: Optional[str] = None) -> None:
        self.our_team_index = our_team_index
        self.our_role = our_role           # "attack" | "defense" | None

    def parse(
        self,
        feedback: list[dict],
        player_team_map: dict[str, int],   # username → teamIndex
        round_outcome: str = "loss",        # "win" or "loss"
    ) -> RoundEvents:
        """
        Main entry point.

        feedback: the matchFeedback list from r6-dissect JSON
        player_team_map: {username: teamIndex} for all players in round
        round_outcome: "win" or "loss" from our team's perspective
        """
        events = RoundEvents()
        kills:        list[KillEvent]  = []
        plant_events: list[PlantEvent] = []

        for item in (feedback or []):
            # 2026-09-16 fix: on this build's r6-dissect version, matchFeedback
            # items carry "type" as a nested {"name": "Kill", "id": 0} object,
            # not a bare string. The old `str(item.get("type") or ...)` call
            # stringified that whole dict -- "{'name': 'Kill', 'id': 0}" --
            # which never matched KILL_TYPES/DEFUSER_TYPES (plain strings
            # like "Kill"), so every round's kill feed silently came back
            # empty even though real "Kill"/"DefuserPlantComplete" events
            # were present in the data the whole time. Confirmed straight
            # from a real device log via the diagnostic logging added
            # 2026-09-15 (types_seen showed literal dict reprs). Unwrap the
            # dict's "name" field; still falls back to a bare string for any
            # r6-dissect version/build that serializes type as a plain string.
            type_field = item.get("type")
            if type_field is None:
                type_field = item.get("feedbackType")
            if isinstance(type_field, dict):
                event_type = str(type_field.get("name") or type_field.get("Name") or "")
            else:
                event_type = str(type_field or "")

            time_str = str(item.get("time") or item.get("timeInSeconds") or "0")
            time_sec = _parse_time(time_str)

            if event_type in KILL_TYPES:
                killer   = str(item.get("username") or item.get("attacker") or "").strip()
                victim   = str(item.get("target")   or item.get("victim")   or "").strip()
                headshot = bool(item.get("headshot") or item.get("isHeadshot") or False)

                k_team = player_team_map.get(killer, -1)
                v_team = player_team_map.get(victim, -1)

                kills.append(KillEvent(
                    time_str=time_str,
                    time_sec=time_sec,
                    killer=killer,
                    victim=victim,
                    headshot=headshot,
                    killer_team_index=k_team,
                    victim_team_index=v_team,
                ))

            elif event_type in DEFUSER_TYPES:
                username = str(item.get("username") or "").strip()
                p_team   = player_team_map.get(username, -1)
                plant_events.append(PlantEvent(
                    time_str=time_str,
                    time_sec=time_sec,
                    username=username,
                    event_type=event_type,
                    team_index=p_team,
                    elapsed=float(item.get("elapsedSeconds") or 0.0),
                ))

        # Sort kills chronologically
        kills.sort(key=lambda k: k.time_sec)
        events.kills        = kills
        events.plant_events = plant_events

        self._compute_first_blood(events)
        self._compute_trades(events)
        self._compute_plant_summary(events, player_team_map)
        self._compute_clutch(events, player_team_map, round_outcome)
        self._compute_per_player(events)

        return events

    # ─────────────────────────────────────────────────────────────────────────

    def _compute_first_blood(self, events: RoundEvents) -> None:
        """First kill of the round — mark it and determine opening duel outcome."""
        if not events.kills:
            return

        first = events.kills[0]
        first.is_opening = True

        events.first_blood_killer = first.killer
        events.first_blood_victim = first.victim
        events.first_blood_time   = first.time_sec

        # Opening duel won if our team got first blood
        events.opening_duel_won = (first.killer_team_index == self.our_team_index)

    def _compute_trades(self, events: RoundEvents) -> None:
        """
        A kill is a trade if the killer dies within TRADE_WINDOW_SEC after their kill.
        Mark both the original kill (is_trade=True for the victim who got traded)
        and the trade kill.
        """
        for i, kill in enumerate(events.kills):
            for j in range(i + 1, len(events.kills)):
                later = events.kills[j]
                if later.time_sec - kill.time_sec > TRADE_WINDOW_SEC:
                    break
                # later.victim == kill.killer means the killer got killed shortly after
                if later.victim == kill.killer:
                    kill.is_trade = True   # this kill was traded
                    later.is_trade = True  # this kill was the trade
                    break

    def _compute_plant_summary(
        self, events: RoundEvents, player_team_map: dict[str, int]
    ) -> None:
        """Derive plant_attempted, plant_completed, defuse_attempted, defuse_completed."""
        # r6-dissect often credits the wrong player on these events, so the
        # team-level facts come from our side instead: attackers plant,
        # defenders defuse. A "disable complete" within MIN_DEFUSE_GAP_SEC of
        # the plant is a false positive (a real defuse takes longer), as seen
        # on a round that was then won by the planting team.
        events.our_role = self.our_role
        completes = [pe for pe in events.plant_events if pe.event_type == "DefuserPlantComplete"]
        if completes:
            events.bomb_planted = True
            events.plant_clock = completes[0].time_str
            if self.our_role in ("attack", "defense"):
                events.planted_by_us = (self.our_role == "attack")
            plant_at = completes[0].elapsed
            events.bomb_defused = any(
                pe.event_type == "DefuserDisableComplete"
                and (not plant_at or not pe.elapsed or pe.elapsed - plant_at >= MIN_DEFUSE_GAP_SEC)
                for pe in events.plant_events
            )
        for pe in events.plant_events:
            is_our_team = (pe.team_index == self.our_team_index)
            et = pe.event_type

            if et == "DefuserPlantStart":
                if is_our_team:
                    events.plant_attempted = True
                    events.planter_username = pe.username
            elif et == "DefuserPlantComplete":
                if is_our_team:
                    events.plant_completed = True
                    events.planter_username = pe.username
            elif et == "DefuserDisableStart":
                if is_our_team:
                    events.defuse_attempted = True
                    events.defuser_username = pe.username
            elif et == "DefuserDisableComplete":
                if is_our_team:
                    events.defuse_completed = True
                    events.defuser_username = pe.username

        if self.our_role in ("attack", "defense"):
            # Side beats the (unreliable) usernames: we planted if we attacked, we defused if we defended.
            we_attack = self.our_role == "attack"
            starts = {pe.event_type for pe in events.plant_events}
            events.plant_completed = bool(events.bomb_planted and we_attack)
            events.plant_attempted = events.plant_completed or (we_attack and "DefuserPlantStart" in starts)
            events.defuse_completed = bool(events.bomb_defused and not we_attack)
            events.defuse_attempted = events.defuse_completed or (
                not we_attack and bool({"DefuserDisableStart", "DefuserDisableComplete"} & starts))
            events.planter_username = None
            events.defuser_username = None

    @staticmethod
    def apply_objective_attempts(events: RoundEvents, attempts: list[dict], our_role: Optional[str],
                                 outcome: Optional[str] = None) -> None:
        """Folds the replay's own plant/defuse countdowns (integration.replay_utility) into a round's
        events. They are more reliable than r6-dissect's defuser events: they name the right player,
        they include attempts that were let go or cut short, and a real defuse is a completed countdown
        (no timing guess). `attempts`: [{kind, completed, username, operator, confidence}].
        `outcome` ("win"/"loss" for us, from the score) settles one case the countdown cannot: a defuse
        that finishes means the defenders won the round, so a countdown that reached zero in a round the
        attackers won was a defuser killed in the last instant, not a defuse."""
        we_attack = (our_role == "attack") if our_role in ("attack", "defense") else None
        attackers_won = None
        if we_attack is not None and outcome in ("win", "loss"):
            attackers_won = (outcome == "win") == we_attack
        marked = []
        for a in attempts:
            ours = None if we_attack is None else ((a["kind"] == "plant") == we_attack)
            a = dict(a)
            if a["kind"] == "defuse" and a.get("completed") and attackers_won:
                a["completed"] = False
            marked.append({**a, "ours": ours})
        events.objective_tracked = True
        events.objective_attempts = marked

        plants = [a for a in marked if a["kind"] == "plant"]
        defuses = [a for a in marked if a["kind"] == "defuse"]
        done_plant = next((a for a in plants if a["completed"]), None)
        if done_plant:
            events.bomb_planted = True
            if we_attack is not None:
                events.planted_by_us = we_attack
        events.bomb_defused = any(a["completed"] for a in defuses)
        if we_attack is None:
            return
        mine = plants if we_attack else defuses
        done = next((a for a in mine if a["completed"]), None)
        events.plant_attempted = events.plant_completed = False
        events.defuse_attempted = events.defuse_completed = False
        events.planter_username = events.defuser_username = None
        if we_attack:
            events.plant_attempted, events.plant_completed = bool(mine), bool(done)
            events.planter_username = done["username"] if done else None
        else:
            events.defuse_attempted, events.defuse_completed = bool(mine), bool(done)
            events.defuser_username = done["username"] if done else None

    def _compute_clutch(
        self,
        events: RoundEvents,
        player_team_map: dict[str, int],
        round_outcome: str,
    ) -> None:
        """
        Clutch detection using kill order:
        After each kill, simulate alive counts. If at any point exactly 1 of our
        players remains alive against ≥1 enemy, that player is the clutch candidate.
        If round was won after that point, they clutched.

        We only consider 'our team' players (teamIndex == our_team_index).
        """
        if round_outcome != "win" or not events.kills:
            return

        # Build alive sets from player_team_map
        our_players: set[str]   = {u for u, t in player_team_map.items() if t == self.our_team_index}
        their_players: set[str] = {u for u, t in player_team_map.items() if t != self.our_team_index}

        if not our_players or not their_players:
            return

        alive_ours   = set(our_players)
        alive_theirs = set(their_players)

        clutch_candidate: Optional[str] = None
        clutch_kills = 0

        for kill in events.kills:
            # Remove victim from alive set
            alive_ours.discard(kill.victim)
            alive_theirs.discard(kill.victim)

            # Check for clutch scenario: exactly 1 of ours alive, ≥1 of theirs
            if len(alive_ours) == 1 and len(alive_theirs) >= 1:
                candidate = next(iter(alive_ours))
                if clutch_candidate is None:
                    clutch_candidate = candidate
                    clutch_kills = 0
                # Count kills by this candidate from this point
                if kill.killer == clutch_candidate:
                    clutch_kills += 1

        # We won and had a clutch scenario — record it
        if clutch_candidate and clutch_kills >= 1:
            events.clutch_player     = clutch_candidate
            events.clutch_kill_count = clutch_kills

    def _compute_per_player(self, events: RoundEvents) -> None:
        """
        Build per-player derived stats dict from the kill events.
        Keys: kills, deaths, headshots, headshot_rate, trades, traded,
              first_blood_kill, first_blood_death, is_clutch
        """
        stats: dict[str, dict] = {}

        def _get(username: str) -> dict:
            if username not in stats:
                stats[username] = {
                    "kills": 0, "deaths": 0,
                    "headshots": 0, "headshot_rate": 0.0,
                    "trades": 0, "traded": 0,
                    "first_blood_kill": False,
                    "first_blood_death": False,
                    "is_clutch": False,
                }
            return stats[username]

        for kill in events.kills:
            if kill.killer:
                kd = _get(kill.killer)
                kd["kills"] += 1
                if kill.headshot:
                    kd["headshots"] += 1
                if kill.is_opening:
                    kd["first_blood_kill"] = True
                if kill.is_trade:
                    kd["trades"] += 1

            if kill.victim:
                vd = _get(kill.victim)
                vd["deaths"] += 1
                if kill.is_opening:
                    vd["first_blood_death"] = True
                if kill.is_trade:
                    vd["traded"] += 1

        # Compute headshot rates
        for username, data in stats.items():
            if data["kills"] > 0:
                data["headshot_rate"] = round(data["headshots"] / data["kills"], 3)

        # Mark clutch player
        if events.clutch_player and events.clutch_player in stats:
            stats[events.clutch_player]["is_clutch"] = True

        events.player_derived = stats


# ─────────────────────────────────────────────────────────────────────────────
# CONVENIENCE: parse a full round JSON dict
# ─────────────────────────────────────────────────────────────────────────────

def parse_round_events(
    round_data: dict,
    our_team_index: int,
    round_outcome: str = "loss",
) -> RoundEvents:
    """
    Top-level convenience function used by rec_importer.
    Accepts the full round_data dict from r6-dissect and returns RoundEvents.
    """
    feedback = round_data.get("matchFeedback") or []

    # Build player → teamIndex map
    player_team_map: dict[str, int] = {}
    for player in round_data.get("players", []):
        username  = str(player.get("username") or "").strip()
        team_idx  = int(player.get("teamIndex") or 0)
        if username:
            player_team_map[username] = team_idx

    our_role = None
    teams = round_data.get("teams") or []
    if 0 <= our_team_index < len(teams):
        role = str((teams[our_team_index] or {}).get("role") or "").lower()
        our_role = role if role in ("attack", "defense") else None

    parser = EventParser(our_team_index=our_team_index, our_role=our_role)
    return parser.parse(feedback, player_team_map, round_outcome)


# ─────────────────────────────────────────────────────────────────────────────
# PROMPT FORMATTER
# ─────────────────────────────────────────────────────────────────────────────

def format_events_for_prompt(events_by_round: dict[int, RoundEvents]) -> str:
    """
    Formats RoundEvents across all rounds into a concise text block
    for inclusion in the AI analysis prompt.

    round_number (1-based) → RoundEvents
    """
    if not events_by_round:
        return "  No kill feed data available.\n"

    lines: list[str] = []

    for rnum in sorted(events_by_round.keys()):
        ev = events_by_round[rnum]
        lines.append(f"  R{rnum:02d}:")

        if ev.first_blood_killer:
            fb_note = " (opening duel WON)" if ev.opening_duel_won else " (opening duel LOST)"
            lines.append(
                f"    First blood: {ev.first_blood_killer} → {ev.first_blood_victim}"
                f" @ {ev.first_blood_time:.0f}s{fb_note}"
            )

        if ev.kills:
            kf_parts = []
            for k in ev.kills:
                hs  = "HS" if k.headshot  else ""
                tr  = "TR" if k.is_trade  else ""
                tag = " ".join(filter(None, [hs, tr]))
                kf_parts.append(
                    f"{k.killer}→{k.victim}@{k.time_sec:.0f}s"
                    + (f"[{tag}]" if tag else "")
                )
            lines.append(f"    Kill feed: {', '.join(kf_parts)}")

        if ev.plant_completed:
            who = f" by {ev.planter_username}" if ev.planter_username else ""
            lines.append(f"    Bomb planted{who}")
        elif ev.plant_attempted:
            lines.append(f"    Plant attempted but not completed")

        if ev.defuse_completed:
            who = f" by {ev.defuser_username}" if ev.defuser_username else ""
            lines.append(f"    Bomb defused{who}")

        if ev.clutch_player:
            lines.append(
                f"    Clutch: {ev.clutch_player} "
                f"({ev.clutch_kill_count}K to close out round)"
            )

        # Interesting per-player notes (headshot rate, trades)
        notable = []
        for username, data in sorted(ev.player_derived.items()):
            notes = []
            if data.get("headshot_rate", 0) >= 0.5 and data["kills"] >= 2:
                notes.append(f"{data['headshot_rate']:.0%} HS rate")
            if data.get("trades", 0) >= 1:
                notes.append(f"{data['trades']} trade kill(s)")
            if notes:
                notable.append(f"{username}: {', '.join(notes)}")
        if notable:
            lines.append(f"    Notable: {'; '.join(notable)}")

    return "\n".join(lines)