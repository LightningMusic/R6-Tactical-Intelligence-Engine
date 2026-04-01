from dataclasses import dataclass
from typing import Optional


@dataclass(frozen=True)
class Gadget:
    """
    Represents a secondary gadget in Rainbow Six Siege.

    Mirrors the `gadgets` table structure + optional max_count
    when loaded from operator_gadget_options.
    """
    gadget_id: int
    name: str
    category: str
    max_count: int = 0 

    def is_attack_gadget(self) -> bool:
        return self.category.lower() == "attack"

    def is_defense_gadget(self) -> bool:
        return self.category.lower() == "defense"