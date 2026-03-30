from dataclasses import dataclass


@dataclass(frozen=True)
class Operator:
    """
    Represents a Rainbow Six Siege operator.

    Mirrors the `operators` table structure.
    """
    operator_id: int
    name: str
    side: str  # "attack" or "defense"
    ability_name: str
    ability_max_count: int

    def is_attack(self) -> bool:
        return self.side.lower() == "attack"

    def is_defense(self) -> bool:
        return self.side.lower() == "defense"

    def validate_ability_usage(self, used_count: int) -> bool:
        """
        Ensures ability usage does not exceed allowed maximum.
        """
        return 0 <= used_count <= self.ability_max_count