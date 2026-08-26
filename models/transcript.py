from dataclasses import dataclass, field
from typing import Optional


@dataclass
class Transcript:
    """
    Represents a stored match transcript from Whisper transcription.

    Mirrors the `transcripts` table structure.
    processed_segments_json stores the full ParsedTranscript serialisation
    produced by TranscriptParser.to_storage_dict().
    """
    transcript_id: Optional[int]
    match_id: int
    raw_text: str
    processed_segments_json: Optional[str] = None

    def has_segments(self) -> bool:
        return bool(self.processed_segments_json)

    def word_count(self) -> int:
        return len(self.raw_text.split()) if self.raw_text else 0

    def preview(self, max_chars: int = 200) -> str:
        """Returns the first max_chars characters of the raw transcript."""
        if not self.raw_text:
            return ""
        text = self.raw_text.strip()
        if len(text) <= max_chars:
            return text
        return text[:max_chars].rsplit(" ", 1)[0] + "…"