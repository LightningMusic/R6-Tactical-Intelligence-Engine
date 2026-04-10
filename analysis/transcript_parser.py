import re
from dataclasses import dataclass, field
from typing import Optional


# =====================================================
# R6 CALLOUT VOCABULARY
# =====================================================
# These are common callouts used in R6 comms.
# Organized by category for pattern matching.

LOCATION_CALLOUTS: set[str] = {
    # Generic positions
    "window", "door", "stairs", "roof", "hatch", "wall",
    "garage", "basement", "attic", "balcony", "hallway",
    "kitchen", "office", "bedroom", "lobby", "armory",
    "server", "bathroom", "vault", "laundry", "pool",
    # Directional
    "north", "south", "east", "west",
    "left", "right", "above", "below",
    "outside", "inside", "upstairs", "downstairs",
}

ACTION_CALLOUTS: set[str] = {
    # Enemy status
    "down", "dead", "spotted", "pushing", "rotating",
    "flanking", "rushing", "camping", "anchoring", "roaming",
    # Team actions
    "planting", "defusing", "holding", "watching", "covering",
    "breaching", "droning", "fragging", "flashing", "smoking",
    "reinforcing", "barricading", "trapping",
    # Utility
    "drone", "gadget", "ability", "grenade", "smoke",
    "flash", "breach", "nitro", "claymore", "barbed",
}

CALLOUT_PATTERNS: list[str] = [
    # "X on/at/in/from Y"
    r"\b(\w+)\s+(?:on|at|in|from)\s+(\w+)\b",
    # "X is Y" (enemy is pushing)
    r"\b(\w+)\s+is\s+(\w+ing)\b",
    # "watch X" / "cover X"
    r"\b(?:watch|cover|check|clear)\s+(\w+)\b",
    # Numbers like "2 on site" / "1 left"
    r"\b(\d+)\s+(?:on\s+site|remaining|left|alive|down)\b",
]


# =====================================================
# DATA STRUCTURES
# =====================================================

@dataclass
class Callout:
    timestamp: float          # seconds from session start
    raw_text:  str            # original whisper text
    category:  str            # "location" | "action" | "count" | "unknown"
    keywords:  list[str] = field(default_factory=list)
    confidence: float = 1.0   # 0.0–1.0


@dataclass
class ParsedTranscript:
    match_id:         Optional[int]
    raw_text:         str
    callouts:         list[Callout]       = field(default_factory=list)
    location_freq:    dict[str, int]      = field(default_factory=dict)
    action_freq:      dict[str, int]      = field(default_factory=dict)
    coordination_gaps: list[float]        = field(default_factory=list)
    silence_periods:  list[tuple[float, float]] = field(default_factory=list)
    word_count:       int = 0
    duration_sec:     float = 0.0


# =====================================================
# PARSER
# =====================================================

class TranscriptParser:
    """
    Parses Whisper transcript output into structured tactical data.

    Input:  Whisper result dict with 'text' and 'segments' keys.
    Output: ParsedTranscript with callouts, frequencies, and gaps.
    """

    # Minimum gap in seconds to count as a "coordination gap"
    SILENCE_THRESHOLD_SEC: float = 8.0

    def __init__(self) -> None:
        self._compiled_patterns = [
            re.compile(p, re.IGNORECASE) for p in CALLOUT_PATTERNS
        ]

    # =====================================================
    # PUBLIC
    # =====================================================

    def parse(
        self,
        whisper_result: dict,
        match_id: Optional[int] = None,
    ) -> ParsedTranscript:
        """
        Main entry point. Accepts a Whisper result dict.
        Returns a fully populated ParsedTranscript.
        """
        raw_text = whisper_result.get("text", "").strip()
        segments: list[dict] = whisper_result.get("segments", [])

        result = ParsedTranscript(
            match_id=match_id,
            raw_text=raw_text,
            word_count=len(raw_text.split()),
            duration_sec=segments[-1]["end"] if segments else 0.0,
        )

        for seg in segments:
            callout = self._parse_segment(seg)
            if callout:
                result.callouts.append(callout)

        result.location_freq = self._build_frequency(
            result.callouts, "location"
        )
        result.action_freq = self._build_frequency(
            result.callouts, "action"
        )
        result.silence_periods = self._detect_silence(segments)
        result.coordination_gaps = [
            start for start, end in result.silence_periods
            if (end - start) >= self.SILENCE_THRESHOLD_SEC
        ]

        return result

    def parse_segments_list(
        self,
        segments: list[dict],
        match_id: Optional[int] = None,
    ) -> ParsedTranscript:
        """
        Convenience method when you already have the segments list
        (e.g. after clip_to_match in WhisperTranscriber).
        """
        full_text = " ".join(s.get("text", "").strip() for s in segments)
        return self.parse(
            {"text": full_text, "segments": segments},
            match_id=match_id,
        )

    # =====================================================
    # SEGMENT PARSING
    # =====================================================

    def _parse_segment(self, segment: dict) -> Optional[Callout]:
        text      = segment.get("text", "").strip()
        start_sec = segment.get("start", 0.0)

        if not text:
            return None

        words    = re.findall(r"\b\w+\b", text.lower())
        keywords: list[str] = []
        category = "unknown"

        # Check against vocabulary sets
        loc_hits    = [w for w in words if w in LOCATION_CALLOUTS]
        action_hits = [w for w in words if w in ACTION_CALLOUTS]

        # Check count patterns ("2 on site", "1 left")
        count_hits = re.findall(
            r"\b(\d+)\s+(?:on\s+site|remaining|left|alive|down)\b",
            text, re.IGNORECASE
        )

        if loc_hits:
            keywords += loc_hits
            category  = "location"
        if action_hits:
            keywords += action_hits
            category  = "action" if category == "unknown" else category
        if count_hits:
            keywords += [f"count:{n}" for n in count_hits]
            category  = "count" if category == "unknown" else category

        # Pattern matching for compound callouts
        for pattern in self._compiled_patterns:
            for match in pattern.finditer(text):
                for group in match.groups():
                    if group and group.lower() not in keywords:
                        keywords.append(group.lower())

        if not keywords and category == "unknown":
            return None   # Skip segments with no tactical content

        return Callout(
            timestamp=start_sec,
            raw_text=text,
            category=category,
            keywords=keywords,
            confidence=self._score_confidence(keywords),
        )

    # =====================================================
    # FREQUENCY ANALYSIS
    # =====================================================

    def _build_frequency(
        self,
        callouts: list[Callout],
        category: str,
    ) -> dict[str, int]:
        freq: dict[str, int] = {}
        for c in callouts:
            if c.category != category and category != "all":
                continue
            for kw in c.keywords:
                if kw.startswith("count:"):
                    continue
                freq[kw] = freq.get(kw, 0) + 1
        return dict(sorted(freq.items(), key=lambda x: x[1], reverse=True))

    # =====================================================
    # SILENCE / GAP DETECTION
    # =====================================================

    def _detect_silence(
        self,
        segments: list[dict],
    ) -> list[tuple[float, float]]:
        """
        Returns (start, end) pairs of silence periods between segments.
        Whisper only produces segments where speech was detected, so
        gaps between segment end/start timestamps = silence.
        """
        gaps: list[tuple[float, float]] = []

        for i in range(1, len(segments)):
            prev_end   = segments[i - 1].get("end",   0.0)
            curr_start = segments[i].get("start", 0.0)
            gap        = curr_start - prev_end

            if gap >= 2.0:   # ignore sub-2s gaps (breathing, pauses)
                gaps.append((prev_end, curr_start))

        return gaps

    # =====================================================
    # CONFIDENCE SCORING
    # =====================================================

    def _score_confidence(self, keywords: list[str]) -> float:
        """
        Simple heuristic — more recognized keywords = higher confidence.
        Max out at 1.0 with 3+ known keywords.
        """
        known = sum(
            1 for k in keywords
            if k in LOCATION_CALLOUTS or k in ACTION_CALLOUTS
        )
        return min(1.0, known * 0.35)

    # =====================================================
    # SUMMARY FOR INTEL ENGINE
    # =====================================================

    def summarize(self, parsed: ParsedTranscript) -> dict:
        """
        Produces a flat summary dict suitable for the IntelEngine prompt
        and for storage in derived_metrics.
        """
        top_locations = list(parsed.location_freq.keys())[:5]
        top_actions   = list(parsed.action_freq.keys())[:5]

        gap_count    = len(parsed.coordination_gaps)
        avg_gap      = (
            sum(parsed.coordination_gaps) / gap_count
            if gap_count > 0 else 0.0
        )

        return {
            "word_count":        parsed.word_count,
            "duration_sec":      round(parsed.duration_sec, 1),
            "callout_count":     len(parsed.callouts),
            "top_locations":     top_locations,
            "top_actions":       top_actions,
            "coordination_gaps": gap_count,
            "avg_gap_sec":       round(avg_gap, 1),
            "silence_periods":   len(parsed.silence_periods),
        }

    def to_storage_dict(self, parsed: ParsedTranscript) -> dict:
        """
        Serializes the full ParsedTranscript to a JSON-safe dict
        for storage in transcripts.processed_segments_json.
        """
        return {
            "word_count":      parsed.word_count,
            "duration_sec":    parsed.duration_sec,
            "callouts": [
                {
                    "timestamp":  c.timestamp,
                    "text":       c.raw_text,
                    "category":   c.category,
                    "keywords":   c.keywords,
                    "confidence": c.confidence,
                }
                for c in parsed.callouts
            ],
            "location_freq":    parsed.location_freq,
            "action_freq":      parsed.action_freq,
            "coordination_gaps": parsed.coordination_gaps,
            "silence_periods":  [
                {"start": s, "end": e}
                for s, e in parsed.silence_periods
            ],
        }