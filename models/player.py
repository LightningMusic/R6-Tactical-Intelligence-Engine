from dataclasses import dataclass
from typing import Optional


@dataclass
class Player:
    """
    Represents a player identity in the system.

    Team members are persistent.
    Opponents may be reused or match-scoped.
    """

    player_id: Optional[int]
    name: str
    is_team_member: bool

    def validate(self) -> None:
        if not self.name or not self.name.strip():
            raise ValueError("Player name cannot be empty.")