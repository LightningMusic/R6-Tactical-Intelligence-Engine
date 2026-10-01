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
    map_name: Optional[str]  = None   # raw from r6-dissect until the catalog resolves it
    # The replay's own map ID and r6-dissect's name for it (which is
    # "Map(<id>)" when r6-dissect doesn't know the map). Kept separately
    # from map_name so database/game_catalog.py can identify the map from
    # the ID rather than trusting whatever name happened to be printed.
    map_game_id:  Optional[int]  = None
    dissect_map_name: Optional[str] = None
    # Set by the catalog when this match's map could not be identified and
    # was flagged for someone to name.
    map_needs_name: bool = False
    catalog_resolved: bool = False
    # When the match was actually played (UTC ISO string from the replay
    # header), rather than when it happened to be imported.
    played_at: Optional[str] = None
    # Every parsed round's replay timestamp, so the audio clip window can be
    # worked out without running r6-dissect over the files a second time.
    round_timestamps: list[str] = field(default_factory=list)
    # Per-round replay facts for the comms timeline: start time, our team's
    # usernames, and kill-feed events with seconds-since-prep. See
    # RecImporter.timeline_round().
    timeline_rounds: list[dict] = field(default_factory=list)

    score_us:     Optional[int]  = None
    score_them:   Optional[int]  = None

    rounds: list[Round] = field(default_factory=list)
    error_message: Optional[str] = None

    transcript_text:     Optional[str]  = None
    transcript_segments: list           = field(default_factory=list)
    
    # In models/import_result.py — add this field:
    recording_path: Optional[str] = None

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