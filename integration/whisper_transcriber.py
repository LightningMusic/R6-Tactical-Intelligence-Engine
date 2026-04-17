import warnings
from pathlib import Path

from app.config import TRANSCRIPTS_DIR, WHISPER_MODEL_PATH


class WhisperTranscriber:
    """
    Local audio transcription using OpenAI Whisper.
    Model is loaded lazily on first use.
    Model size is read from settings singleton at load time.
    """

    def __init__(self) -> None:
        self._model = None

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

        from app.config import settings

        if not WHISPER_MODEL_PATH.exists():
            raise FileNotFoundError(
                f"Whisper model not found at {WHISPER_MODEL_PATH}\n"
                "Place whisper-base.pt in data/models/ — "
                "see setup instructions."
            )

        size = settings.WHISPER_MODEL_SIZE
        print(f"[Whisper] Loading '{size}' model from {WHISPER_MODEL_PATH.parent}...")

        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            self._model = whisper.load_model(
                size,
                download_root=str(WHISPER_MODEL_PATH.parent),
            )

        print("[Whisper] Model ready.")

    # =====================================================
    # TRANSCRIBE
    # =====================================================

    def transcribe(self, audio_path: Path, language: str = "en") -> dict:
        self._load_model()

        file_size = audio_path.stat().st_size / (1024 * 1024)
        print(f"[Whisper] Processing {file_size:.1f} MB audio file...")
        
        if not audio_path.exists():
            raise FileNotFoundError(f"Audio file not found: {audio_path}")

        print(f"[Whisper] Transcribing {audio_path.name}...")

        import whisper
        import sys
        from contextlib import redirect_stdout
        import os

        # We use redirect_stdout to ensure that if Whisper tries to 'write' 
        # progress to a broken stream, it goes to devnull instead.
        with open(os.devnull, 'w') as fnull:
            with redirect_stdout(fnull):
                result: dict = self._model.transcribe(  # type: ignore[union-attr]
                    str(audio_path),
                    language=language,
                    verbose=None, # Changed from False to None to let it use default internal handling
                    fp16=False,
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
        segments = full_result.get("segments", [])
        clipped  = [
            seg for seg in segments
            if seg["start"] >= match_start_sec
            and seg["end"]   <= match_end_sec
        ]
        return {
            "text":     " ".join(s["text"].strip() for s in clipped),
            "segments": clipped,
        }

    # =====================================================
    # SAVE TRANSCRIPT
    # =====================================================

    def save_transcript(self, result: dict, match_id: int) -> Path:
        import json
        TRANSCRIPTS_DIR.mkdir(parents=True, exist_ok=True)
        out_path = TRANSCRIPTS_DIR / f"match_{match_id}_transcript.json"
        with open(out_path, "w", encoding="utf-8") as f:
            json.dump(result, f, indent=2, ensure_ascii=False)
        print(f"[Whisper] Transcript saved → {out_path}")
        return out_path