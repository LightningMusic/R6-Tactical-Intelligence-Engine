from dataclasses import dataclass
from typing import Optional

from models.operator import Operator
from models.gadget import Gadget
from models.player import Player


@dataclass
class PlayerRoundStats:
    def __init__(
        self,
        round_id: int,
        player_id: int,
        player: Player,  # ← ADD THIS
        operator: Operator,
        kills: int,
        deaths: int,
        assists: int,
        engagements_taken: int,
        engagements_won: int,
        ability_start: int,
        ability_used: int,
        secondary_gadget: Optional[Gadget],
        secondary_start: int,
        secondary_used: int,
        plant_attempted: bool,
        plant_successful: bool,
        stat_id: Optional[int] = None
    ):
        self.stat_id = stat_id
        self.round_id = round_id
        self.player_id = player_id
        self.player = player  # ← STORE IT
        self.operator = operator
        self.kills = kills
        self.deaths = deaths
        self.assists = assists
        self.engagements_taken = engagements_taken
        self.engagements_won = engagements_won
        self.ability_start = ability_start
        self.ability_used = ability_used
        self.secondary_gadget = secondary_gadget
        self.secondary_start = secondary_start
        self.secondary_used = secondary_used
        self.plant_attempted = plant_attempted
        self.plant_successful = plant_successful

    def validate(self) -> None:
        """
        Validates integrity of a single player's round statistics.
        Raises ValueError if invalid.
        """

        if self.kills < 0:
            raise ValueError("Kills cannot be negative.")

        if self.deaths < 0:
            raise ValueError("Deaths cannot be negative.")

        if self.assists < 0:
            raise ValueError("Assists cannot be negative.")

        if self.engagements_taken < 0:
            raise ValueError("Engagements taken cannot be negative.")

        if self.engagements_won < 0:
            raise ValueError("Engagements won cannot be negative.")

        if self.engagements_won > self.engagements_taken:
            raise ValueError("Engagements won cannot exceed engagements taken.")

        if self.ability_used > self.ability_start:
            raise ValueError("Ability used cannot exceed ability start count.")

        if self.secondary_used > self.secondary_start:
            raise ValueError("Secondary gadget used cannot exceed starting amount.")

        if self.plant_successful and not self.plant_attempted:
            raise ValueError("Cannot have successful plant without attempting plant.")