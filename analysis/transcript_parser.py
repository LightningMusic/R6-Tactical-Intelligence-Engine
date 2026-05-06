import re
from dataclasses import dataclass, field
from typing import Optional


# =====================================================
# R6 CALLOUT VOCABULARY
# =====================================================

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
    # Tactical positions
    "flank", "rotate", "plant", "bomb", "defuser",
    "breach", "roam", "anchor", "peek", "angle", "corner",
    "site", "spawn", "obj", "objective",
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
    # Engagement callouts
    "contact", "engaged", "shooting", "traded", "clutch",
    "repositioning", "falling",
}

# Words that strongly indicate an active fight/engagement
ENGAGEMENT_WORDS: set[str] = {
    "down", "dead", "contact", "engaged", "shooting", "traded",
    "clutch", "kill", "died", "shot", "push", "pushing",
    "last", "one left", "two left", "three left",
}

CALLOUT_PATTERNS: list[str] = [
    r"\b(\w+)\s+(?:on|at|in|from)\s+(\w+)\b",
    r"\b(\w+)\s+is\s+(\w+ing)\b",
    r"\b(?:watch|cover|check|clear)\s+(\w+)\b",
    r"\b(\d+)\s+(?:on\s+site|remaining|left|alive|down)\b",
]


# =====================================================
# DATA STRUCTURES
# =====================================================

@dataclass
class Callout:
    timestamp: float
    raw_text:  str
    category:  str
    keywords:  list[str] = field(default_factory=list)
    confidence: float = 1.0


@dataclass
class FightSilence:
    """Represents a detected window where a fight occurred with no intel given."""
    start_sec:     float
    end_sec:       float
    gap_sec:       float
    prior_callout: str
    next_callout:  str


@dataclass
class ParsedTranscript:
    match_id:         Optional[int]
    raw_text:         str
    callouts:         list[Callout]       = field(default_factory=list)
    location_freq:    dict[str, int]      = field(default_factory=dict)
    action_freq:      dict[str, int]      = field(default_factory=dict)
    coordination_gaps: list[float]        = field(default_factory=list)
    silence_periods:  list[tuple[float, float]] = field(default_factory=list)
    fight_silences:   list[FightSilence]  = field(default_factory=list)
    word_count:       int = 0
    duration_sec:     float = 0.0


# =====================================================
# PARSER
# =====================================================

class TranscriptParser:
    """
    Parses Whisper transcript output into structured tactical data.

    Input:  Whisper result dict with 'text' and 'segments' keys.
    Output: ParsedTranscript with callouts, frequencies, gaps,
            and fight_silences (prolonged fights without intel).
    """

    SILENCE_THRESHOLD_SEC: float = 8.0
    FIGHT_SILENCE_MIN_SEC: float = 15.0   # gap between engagement callouts = no intel

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

        result.location_freq = self._build_frequency(result.callouts, "location")
        result.action_freq   = self._build_frequency(result.callouts, "action")
        result.silence_periods = self._detect_silence(segments)
        result.coordination_gaps = [
            start for start, end in result.silence_periods
            if (end - start) >= self.SILENCE_THRESHOLD_SEC
        ]
        result.fight_silences = self._detect_fight_silences(segments)

        return result

    def parse_segments_list(
        self,
        segments: list[dict],
        match_id: Optional[int] = None,
    ) -> ParsedTranscript:
        full_text = " ".join(s.get("text", "").strip() for s in segments)
        return self.parse(
            {"text": full_text, "segments": segments},
            match_id=match_id,
        )

    # =====================================================
    # FIGHT SILENCE DETECTION
    # =====================================================

    def _has_engagement_content(self, text: str) -> bool:
        """Returns True if text contains words indicating an active fight."""
        lower = text.lower()
        return any(w in lower for w in ENGAGEMENT_WORDS)

    def _detect_fight_silences(self, segments: list[dict]) -> list[FightSilence]:
        """
        Finds gaps ≥ FIGHT_SILENCE_MIN_SEC between engagement-related callouts.

        These represent moments where someone was likely in a fight but said
        nothing — no intel to the team. The longer the gap, the worse the
        communication breakdown.
        """
        # Collect segments containing engagement language
        fight_segs = [
            s for s in segments
            if isinstance(s, dict) and self._has_engagement_content(
                str(s.get("text", ""))
            )
        ]

        silences: list[FightSilence] = []

        for i in range(1, len(fight_segs)):
            prev = fight_segs[i - 1]
            curr = fight_segs[i]
            prev_end   = float(prev.get("end",   0.0))
            curr_start = float(curr.get("start", 0.0))
            gap = curr_start - prev_end

            if gap >= self.FIGHT_SILENCE_MIN_SEC:
                silences.append(FightSilence(
                    start_sec=round(prev_end, 1),
                    end_sec=round(curr_start, 1),
                    gap_sec=round(gap, 1),
                    prior_callout=str(prev.get("text", "")).strip()[:100],
                    next_callout=str(curr.get("text", "")).strip()[:100],
                ))

        # Sort by longest gap first
        silences.sort(key=lambda s: s.gap_sec, reverse=True)
        return silences

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

        loc_hits    = [w for w in words if w in LOCATION_CALLOUTS]
        action_hits = [w for w in words if w in ACTION_CALLOUTS]

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

        for pattern in self._compiled_patterns:
            for match in pattern.finditer(text):
                for group in match.groups():
                    if group and group.lower() not in keywords:
                        keywords.append(group.lower())

        if not keywords and category == "unknown":
            return None

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
        gaps: list[tuple[float, float]] = []
        for i in range(1, len(segments)):
            prev_end   = segments[i - 1].get("end",   0.0)
            curr_start = segments[i].get("start", 0.0)
            gap        = curr_start - prev_end
            if gap >= 2.0:
                gaps.append((prev_end, curr_start))
        return gaps

    # =====================================================
    # CONFIDENCE SCORING
    # =====================================================

    def _score_confidence(self, keywords: list[str]) -> float:
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
        Includes fight_silence data for intel gap detection.
        """
        top_locations = list(parsed.location_freq.keys())[:5]
        top_actions   = list(parsed.action_freq.keys())[:5]

        gap_count    = len(parsed.coordination_gaps)
        avg_gap      = (
            sum(parsed.coordination_gaps) / gap_count
            if gap_count > 0 else 0.0
        )

        # Fight silence summary
        fight_silence_count = len(parsed.fight_silences)
        worst_silences = [
            {
                "gap_sec":       fs.gap_sec,
                "start_sec":     fs.start_sec,
                "prior_callout": fs.prior_callout,
                "next_callout":  fs.next_callout,
            }
            for fs in parsed.fight_silences[:3]  # top 3 worst
        ]

        return {
            "word_count":             parsed.word_count,
            "duration_sec":           round(parsed.duration_sec, 1),
            "callout_count":          len(parsed.callouts),
            "top_locations":          top_locations,
            "top_actions":            top_actions,
            "coordination_gaps":      gap_count,
            "avg_gap_sec":            round(avg_gap, 1),
            "silence_periods":        len(parsed.silence_periods),
            "fight_silence_count":    fight_silence_count,
            "worst_fight_silences":   worst_silences,
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
            "fight_silence_count":  len(parsed.fight_silences),
            "fight_silences": [
                {
                    "start_sec":     fs.start_sec,
                    "end_sec":       fs.end_sec,
                    "gap_sec":       fs.gap_sec,
                    "prior_callout": fs.prior_callout,
                    "next_callout":  fs.next_callout,
                }
                for fs in parsed.fight_silences
            ],
        }