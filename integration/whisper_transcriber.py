import warnings
from pathlib import Path
from typing import Optional

from app.config import TRANSCRIPTS_DIR


class WhisperTranscriber:
    """
    Local audio transcription using OpenAI Whisper.
    Runs entirely offline — no API key needed.

    Model sizes (tradeoff: speed vs accuracy):
      tiny   — fastest, lowest accuracy (~1GB RAM)
      base   — good balance for comms (~1GB RAM)
      small  — better accuracy (~2GB RAM)
      medium — high accuracy, slower (~5GB RAM)
    """

    def __init__(self, model_size: str = "base") -> None:
        self.model_size = model_size
        self._model = None   # lazy-loaded

    # =====================================================
    # LAZY LOAD
    # =====================================================


    def _load_model(self) -> None:
        if self._model is not None:
            return
        try:
            import whisper
        except ImportError:
            raise ImportError(
                "openai-whisper is not installed.\n"
                "Run: pip install openai-whisper"
            )

        from app.config import WHISPER_MODEL_PATH, WHISPER_MODEL_SIZE

        if WHISPER_MODEL_PATH.exists():
            print(f"[Whisper] Loading from local file: {WHISPER_MODEL_PATH}")
            import warnings
            with warnings.catch_warnings():
                warnings.simplefilter("ignore")
                self._model = whisper.load_model(
                    WHISPER_MODEL_SIZE,
                    download_root=str(WHISPER_MODEL_PATH.parent),
                )
        else:
            raise FileNotFoundError(
                f"Whisper model not found at {WHISPER_MODEL_PATH}\n"
                f"See setup instructions to download it manually."
            )
        print("[Whisper] Model ready.")

    # =====================================================
    # TRANSCRIBE
    # =====================================================

    def transcribe(
        self,
        audio_path: Path,
        language: str = "en",
    ) -> dict:
        """
        Transcribes an audio file.
        Returns Whisper's full result dict:
          {
            "text": "full transcript...",
            "segments": [{"start": 0.0, "end": 2.5, "text": "..."}, ...]
          }
        """
        self._load_model()

        if not audio_path.exists():
            raise FileNotFoundError(f"Audio file not found: {audio_path}")

        print(f"[Whisper] Transcribing {audio_path.name}...")

        import whisper
        result: dict = self._model.transcribe(   # type: ignore[union-attr]
            str(audio_path),
            language=language,
            verbose=False,
            fp16=False,          # CPU-safe — no half-precision errors
        )

        print(f"[Whisper] Done. {len(result.get('segments', []))} segments.")
        return result

    # =====================================================
    # CLIP TO MATCH WINDOW
    # =====================================================

    def clip_to_match(
        self,
        full_result: dict,
        match_start_sec: float,
        match_end_sec: float,
    ) -> dict:
        """
        Filters segments to only those within a match's time window.
        Used to extract per-match transcript from a full session recording.

        Returns a trimmed result dict with the same structure.
        """
        segments = full_result.get("segments", [])

        clipped = [
            seg for seg in segments
            if seg["start"] >= match_start_sec
            and seg["end"] <= match_end_sec
        ]

        clipped_text = " ".join(s["text"].strip() for s in clipped)

        return {
            "text": clipped_text,
            "segments": clipped,
        }

    # =====================================================
    # SAVE TRANSCRIPT
    # =====================================================

    def save_transcript(
        self,
        result: dict,
        match_id: int,
    ) -> Path:
        """
        Saves transcript text to the transcripts directory.
        Returns the saved file path.
        """
        import json

        TRANSCRIPTS_DIR.mkdir(parents=True, exist_ok=True)
        out_path = TRANSCRIPTS_DIR / f"match_{match_id}_transcript.json"

        with open(out_path, "w", encoding="utf-8") as f:
            json.dump(result, f, indent=2, ensure_ascii=False)

        print(f"[Whisper] Transcript saved → {out_path}")
        return out_path