from dataclasses import dataclass
from typing import Optional


@dataclass
class RoundResources:
    """
    Represents team-level resources for a single round.

    Enforces drone and reinforcement constraints according to FOUNDATION V2.1.
    """

    resource_id: Optional[int]
    round_id: int
    side: str  # "attack" or "defense"

    # Attack resources
    team_drones_start: int = 10
    team_drones_lost: int = 0

    # Defense resources
    team_reinforcements_start: int = 10
    team_reinforcements_used: int = 0

    # ----------------------------------
    # Validation Layer
    # ----------------------------------

    def validate(self) -> None:
        if self.side not in ("attack", "defense"):
            raise ValueError("Side must be 'attack' or 'defense'.")

        if self.team_drones_start != 10:
            raise ValueError("Must start with 10 drones.")
        if self.team_drones_lost < 0:
            raise ValueError("Drone loss cannot be negative.")
        if self.team_drones_lost > self.team_drones_start:
            raise ValueError("Drone loss cannot exceed starting drones.")

        if self.team_reinforcements_start != 10:
            raise ValueError("Must start with 10 reinforcements.")
        if self.team_reinforcements_used < 0:
            raise ValueError("Reinforcements used cannot be negative.")
        if self.team_reinforcements_used > self.team_reinforcements_start:
            raise ValueError("Reinforcements used cannot exceed starting amount.")

        # Side-specific: only the active resource should be non-zero
        if self.side == "attack" and self.team_reinforcements_used > 0:
            raise ValueError("Reinforcements cannot be used on attack.")
        if self.side == "defense" and self.team_drones_lost > 0:
            raise ValueError("Drones cannot be lost on defense.")

    # ----------------------------------
    # Derived Helpers
    # ----------------------------------

    def drones_remaining(self) -> int:
        if self.side != "attack":
            return 0
        return self.team_drones_start - self.team_drones_lost

    def reinforcements_remaining(self) -> int:
        if self.side != "defense":
            return 0
        return self.team_reinforcements_start - self.team_reinforcements_used

    def drone_loss_rate(self) -> float:
        if self.side != "attack":
            return 0.0
        return self.team_drones_lost / self.team_drones_start

    def reinforcement_usage_rate(self) -> float:
        if self.side != "defense":
            return 0.0
        return self.team_reinforcements_used / self.team_reinforcements_start