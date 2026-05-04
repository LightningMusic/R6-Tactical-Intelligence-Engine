"""
models/round_events.py

Structured kill feed and event data extracted from r6-dissect matchFeedback.
These are ephemeral — computed at import time, stored as JSON in derived_metrics,
and consumed by the AI engine and analysis pipeline.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional


# ─────────────────────────────────────────────────────────────────────────────
# RAW EVENT TYPES (from matchFeedback)
# ─────────────────────────────────────────────────────────────────────────────

# Known r6-dissect matchFeedback type strings
KILL_TYPES = {"Kill"}
DEFUSER_TYPES = {
    "DefuserPlantStart",
    "DefuserPlantComplete",
    "DefuserDisableStart",
    "DefuserDisableComplete",
}
OPERATOR_ABILITY_TYPES = {
    "GadgetDestroyEvent",
    "OperatorActivatedAbility",
}
OTHER_TYPES = {
    "Other",
    "Unknown",
}


@dataclass
class KillEvent:
    """Represents a single kill in the round kill feed."""
    time_str:   str          # "00:02:34" — round time at moment of kill
    time_sec:   float        # seconds into the round
    killer:     str          # username of killer
    victim:     str          # username of victim
    headshot:   bool
    killer_team_index: int   # 0 or 1
    victim_team_index: int   # 0 or 1
    is_trade:   bool = False # True if killer was killed within TRADE_WINDOW_SEC after this kill
    is_opening: bool = False # True if this is the first kill of the round


@dataclass
class PlantEvent:
    """Represents a defuser plant or disable event."""
    time_str:  str
    time_sec:  float
    username:  str
    event_type: str   # "DefuserPlantStart"|"DefuserPlantComplete"|"DefuserDisableStart"|...
    team_index: int


@dataclass
class RoundEvents:
    """
    All structured events for a single round, derived from matchFeedback.
    Computed by EventParser and stored on Round for pipeline consumption.
    """
    kills:        list[KillEvent]  = field(default_factory=list)
    plant_events: list[PlantEvent] = field(default_factory=list)

    # ── Derived fields (computed by EventParser.compute_derived) ──
    first_blood_killer:  Optional[str]  = None   # username who got first kill
    first_blood_victim:  Optional[str]  = None   # username who was killed first
    first_blood_time:    Optional[float] = None  # seconds into round
    opening_duel_won:    Optional[bool] = None   # from our team's perspective

    # Per-player derived stats keyed by username
    # Each entry: {kills, deaths, headshots, trades, traded, first_blood}
    player_derived: dict[str, dict] = field(default_factory=dict)

    # Plant/defuse summary
    plant_attempted:   bool = False
    plant_completed:   bool = False
    defuse_attempted:  bool = False
    defuse_completed:  bool = False
    planter_username:  Optional[str] = None
    defuser_username:  Optional[str] = None

    # Clutch detection
    # A clutch = one player from our team alive, ≥1 enemy alive, round won
    clutch_player:     Optional[str]  = None
    clutch_kill_count: int            = 0

    def to_dict(self) -> dict:
        """Serialise to a JSON-safe dict for storage in derived_metrics."""
        return {
            "first_blood_killer":  self.first_blood_killer,
            "first_blood_victim":  self.first_blood_victim,
            "first_blood_time":    self.first_blood_time,
            "opening_duel_won":    self.opening_duel_won,
            "plant_attempted":     self.plant_attempted,
            "plant_completed":     self.plant_completed,
            "defuse_attempted":    self.defuse_attempted,
            "defuse_completed":    self.defuse_completed,
            "planter":             self.planter_username,
            "defuser":             self.defuser_username,
            "clutch_player":       self.clutch_player,
            "clutch_kills":        self.clutch_kill_count,
            "kills": [
                {
                    "time":       k.time_str,
                    "killer":     k.killer,
                    "victim":     k.victim,
                    "headshot":   k.headshot,
                    "trade":      k.is_trade,
                    "opening":    k.is_opening,
                }
                for k in self.kills
            ],
            "player_derived": self.player_derived,
        }