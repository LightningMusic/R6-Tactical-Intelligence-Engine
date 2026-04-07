from dataclasses import dataclass
from typing import List, Optional
from datetime import datetime


@dataclass
class MatchTimeWindow:
    """
    Represents a single match time window derived from .rec data.
    """
    match_id: Optional[int]
    start_time: datetime
    end_time: datetime


@dataclass
class TranscriptSegment:
    """
    Represents a piece of transcript with timing.
    """
    start_time: float  # seconds from start of recording
    end_time: float
    text: str


@dataclass
class AlignedTranscript:
    """
    Transcript mapped to a specific match.
    """
    match_id: Optional[int]
    segments: List[TranscriptSegment]
    full_text: str


class TimelineAligner:
    """
    Responsible for aligning a continuous recording transcript
    to multiple matches using time windows from .rec data.
    """

    def __init__(self, recording_start_time: datetime):
        self.recording_start_time = recording_start_time

    # -----------------------------------------------------
    # Public API
    # -----------------------------------------------------

    def align(
        self,
        transcript_segments: List[TranscriptSegment],
        match_windows: List[MatchTimeWindow],
    ) -> List[AlignedTranscript]:
        """
        Splits transcript into match-aligned chunks.

        Args:
            transcript_segments: Whisper segments (continuous session)
            match_windows: Time windows from replay files

        Returns:
            List of aligned transcripts per match
        """

        aligned_results: List[AlignedTranscript] = []

        for window in match_windows:
            matched_segments = self._get_segments_for_window(
                transcript_segments,
                window
            )

            full_text = " ".join(seg.text for seg in matched_segments)

            aligned_results.append(
                AlignedTranscript(
                    match_id=window.match_id,
                    segments=matched_segments,
                    full_text=full_text.strip(),
                )
            )

        return aligned_results

    # -----------------------------------------------------
    # Core Logic
    # -----------------------------------------------------

    def _get_segments_for_window(
        self,
        segments: List[TranscriptSegment],
        window: MatchTimeWindow,
    ) -> List[TranscriptSegment]:
        """
        Filters transcript segments that fall within a match window.
        """

        window_start_sec = self._to_seconds(window.start_time)
        window_end_sec = self._to_seconds(window.end_time)

        matched = []

        for seg in segments:
            if self._overlaps(seg.start_time, seg.end_time, window_start_sec, window_end_sec):
                matched.append(seg)

        return matched

    # -----------------------------------------------------
    # Helpers
    # -----------------------------------------------------

    def _to_seconds(self, absolute_time: datetime) -> float:
        """
        Converts absolute time to seconds from recording start.
        """
        delta = absolute_time - self.recording_start_time
        return delta.total_seconds()

    @staticmethod
    def _overlaps(seg_start: float, seg_end: float, win_start: float, win_end: float) -> bool:
        """
        Checks if two time ranges overlap.
        """
        return not (seg_end < win_start or seg_start > win_end)