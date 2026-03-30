from dataclasses import dataclass


@dataclass(frozen=True)
class Gadget:
    """
    Represents a secondary gadget in Rainbow Six Siege.

    Mirrors the `gadgets` table structure.
    """
    gadget_id: int
    name: str
    category: str

    def is_attack_gadget(self) -> bool:
        return self.category.lower() == "attack"

    def is_defense_gadget(self) -> bool:
        return self.category.lower() == "defense"