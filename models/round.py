from dataclasses import dataclass, field
from typing import List, Optional

from models.round_resources import RoundResources
from models.player_round_stats import PlayerRoundStats

@dataclass
class Round:
    """
    Represents a single round within a match.

    Aggregates:
    - Round metadata
    - Team resource state
    - Player stat objects

    Enforces FOUNDATION V2.1 integrity rules.
    """

    round_id: Optional[int]
    match_id: Optional[int]
    round_number: int
    side: str  # "attack" or "defense"
    site: str
    outcome: str  # "win" or "loss"

    resources: Optional[RoundResources]
    player_stats: List[PlayerRoundStats] = field(default_factory=list)

    # ----------------------------------
    # Validation Layer
    # ----------------------------------

    def validate(self) -> None:
        if self.side not in ("attack", "defense"):
            raise ValueError("Round side must be 'attack' or 'defense'.")

        if self.outcome not in ("win", "loss"):
            raise ValueError("Outcome must be 'win' or 'loss'.")

        # ✅ SAFE GUARD
        if self.resources is None:
            raise ValueError("Round resources must be set before validation.")

        if self.resources.side != self.side:
            raise ValueError("Resource side must match round side.")

        self.resources.validate()

        # Validate player stats
        if len(self.player_stats) == 0:
            raise ValueError("Round must contain player stats.")

        for stats in self.player_stats:
            stats.validate()

        self._validate_kill_consistency()

    # ----------------------------------
    # Internal Consistency Rules
    # ----------------------------------

    def _validate_kill_consistency(self) -> None:
        """
        Optional logical sanity:
        Total team kills should not exceed 5.
        (Assumes standard 5v5 round format.)
        """

        total_kills = sum(p.kills for p in self.player_stats)

        if total_kills > 5:
            raise ValueError("Total team kills cannot exceed 5 in a round.")

    # ----------------------------------
    # Derived Helpers
    # ----------------------------------

    def total_kills(self) -> int:
        return sum(p.kills for p in self.player_stats)

    def total_deaths(self) -> int:
        return sum(p.deaths for p in self.player_stats)

    def team_engagement_win_rate(self) -> float:
        total_taken = sum(p.engagements_taken for p in self.player_stats)
        total_won = sum(p.engagements_won for p in self.player_stats)

        if total_taken == 0:
            return 0.0

        return total_won / total_taken

    def plant_attempted(self) -> bool:
        return any(p.plant_attempted for p in self.player_stats)

    def plant_successful(self) -> bool:
        return any(p.plant_successful for p in self.player_stats)