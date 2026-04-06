from dataclasses import dataclass, field
from typing import List, Optional
from datetime import datetime
from dataclasses import dataclass, field
from datetime import datetime
from typing import List

from models.round import Round
from models.round import Round


@dataclass
class Match:
    match_id: Optional[int]
    """
    Represents a full match.

    Aggregates:
    - Match metadata
    - All rounds
    - Derived score logic

    Enforces FOUNDATION V2.1 integrity rules.
    """

    match_id: Optional[int]
    datetime_played: datetime
    opponent_name: str
    map: str
    result: str  # "win" or "loss"
    recording_path: Optional[str]

    rounds: List[Round] = field(default_factory=list)

    # ----------------------------------
    # Validation Layer
    # ----------------------------------

    def validate(self) -> None:
        """
        Validates match integrity.
        Raises ValueError on invalid state.
        """

        if self.result not in ("win", "loss"):
            raise ValueError("Match result must be 'win' or 'loss'.")

        if len(self.rounds) == 0:
            raise ValueError("Match must contain at least one round.")

        # Validate each round
        for r in self.rounds:
            r.validate()

        # Validate round sequencing
        self._validate_round_sequence()

        # Validate score consistency
        self._validate_score_consistency()

    # ----------------------------------
    # Internal Consistency Rules
    # ----------------------------------

    def _validate_round_sequence(self) -> None:
        """
        Ensures round numbers are sequential starting at 1.
        """

        expected = 1
        for r in sorted(self.rounds, key=lambda x: x.round_number):
            if r.round_number != expected:
                raise ValueError("Round numbers must be sequential starting at 1.")
            expected += 1

    def _validate_score_consistency(self) -> None:
        """
        Ensures match result aligns with round outcomes.
        """

        wins = sum(1 for r in self.rounds if r.outcome == "win")
        losses = sum(1 for r in self.rounds if r.outcome == "loss")

        if wins == losses:
            raise ValueError("Match cannot end in a tie.")

        calculated_result = "win" if wins > losses else "loss"

        if calculated_result != self.result:
            raise ValueError("Match result does not match round outcomes.")

    # ----------------------------------
    # Derived Helpers
    # ----------------------------------

    def total_rounds(self) -> int:
        return len(self.rounds)

    def rounds_won(self) -> int:
        return sum(1 for r in self.rounds if r.outcome == "win")

    def rounds_lost(self) -> int:
        return sum(1 for r in self.rounds if r.outcome == "loss")

    def match_score(self) -> str:
        return f"{self.rounds_won()} - {self.rounds_lost()}"

    def overall_engagement_win_rate(self) -> float:
        total_taken = sum(
            r.team_engagement_win_rate() * 
            sum(p.engagements_taken for p in r.player_stats)
            for r in self.rounds
        )

        total_engagements = sum(
            sum(p.engagements_taken for p in r.player_stats)
            for r in self.rounds
        )

        if total_engagements == 0:
            return 0.0

        return total_taken / total_engagements