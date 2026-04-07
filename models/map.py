from dataclasses import dataclass


@dataclass
class Map:
    map_id: int
    name: str
    is_active_pool: bool = True