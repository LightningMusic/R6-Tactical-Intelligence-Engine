from dataclasses import dataclass, field
from enum import Enum
from typing import Optional

from models.round import Round


class ImportStatus(Enum):
    SUCCESS          = "success"
    PARTIAL_FAILURE  = "partial_failure"
    CRITICAL_FAILURE = "critical_failure"


@dataclass
class ImportResult:
    status: ImportStatus

    match_id:     Optional[int]  = None
    map_id:       Optional[int]  = None
    map_name: Optional[str]  = None   # ADD THIS — raw from r6-dissect

    score_us:     Optional[int]  = None
    score_them:   Optional[int]  = None

    rounds: list[Round] = field(default_factory=list)
    error_message: Optional[str] = None

    @property
    def is_success(self) -> bool:
        return self.status == ImportStatus.SUCCESS

    @property
    def has_partial_data(self) -> bool:
        return self.status == ImportStatus.PARTIAL_FAILURE and (
            self.map_id is not None
            or self.map_name is not None
            or self.score_us is not None
            or len(self.rounds) > 0
        )