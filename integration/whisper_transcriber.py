import warnings
import sys
import io
from pathlib import Path

from app.config import TRANSCRIPTS_DIR, WHISPER_MODEL_PATH


def _ensure_console() -> None:
    """Prevent None stdout/stderr crashes in windowed exe mode."""
    if sys.stdout is None:
        sys.stdout = io.StringIO()
    if sys.stderr is None:
        sys.stderr = io.StringIO()


def _find_ffmpeg() -> bool:
    """Returns True if ffmpeg is accessible."""
    import shutil
    import subprocess

    # Check PATH first
    if shutil.which("ffmpeg"):
        return True

    # Check next to the exe (for USB deployment)
    if getattr(sys, "frozen", False):
        exe_dir = Path(sys.executable).parent
        for candidate in [
            exe_dir / "ffmpeg.exe",
            exe_dir / "_internal" / "ffmpeg.exe",
        ]:
            if candidate.exists():
                # Add its directory to PATH
                import os
                os.environ["PATH"] = (
                    str(candidate.parent)
                    + os.pathsep
                    + os.environ.get("PATH", "")
                )
                return True
    return False


class WhisperTranscriber:
    """
    Local audio transcription using OpenAI Whisper.
    Lazy-loaded. Requires ffmpeg to decode audio files.
    """

    def __init__(self) -> None:
        self._model = None

    def _load_model(self) -> None:
        if self._model is not None:
            return

        _ensure_console()

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
                "Copy whisper-base.pt to data/models/"
            )

        if not _find_ffmpeg():
            raise RuntimeError(
                "ffmpeg not found. Whisper requires ffmpeg to decode audio.\n"
                "Download ffmpeg.exe and place it next to R6Analyzer.exe on the USB.\n"
                "Get it from: https://www.gyan.dev/ffmpeg/builds/ "
                "(ffmpeg-release-essentials.zip — just ffmpeg.exe is enough)"
            )

        size = settings.WHISPER_MODEL_SIZE
        print(f"[Whisper] Loading '{size}' model...")

        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            self._model = whisper.load_model(
                size,
                download_root=str(WHISPER_MODEL_PATH.parent),
            )

        print("[Whisper] Model ready.")

    def transcribe(self, audio_path: Path, language: str = "en") -> dict:
        _ensure_console()
        self._load_model()

        if not audio_path.exists():
            raise FileNotFoundError(f"Audio file not found: {audio_path}")

        mb = audio_path.stat().st_size / (1024 * 1024)
        print(f"[Whisper] Transcribing {audio_path.name} ({mb:.1f} MB)...")

        import whisper
        result: dict = self._model.transcribe(  # type: ignore[union-attr]
            str(audio_path),
            language=language,
            verbose=False,
            fp16=False,
        )

        print(f"[Whisper] Done. {len(result.get('segments', []))} segments.")
        return result

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

    def save_transcript(self, result: dict, match_id: int) -> Path:
        import json
        TRANSCRIPTS_DIR.mkdir(parents=True, exist_ok=True)
        out_path = TRANSCRIPTS_DIR / f"match_{match_id}_transcript.json"
        with open(out_path, "w", encoding="utf-8") as f:
            json.dump(result, f, indent=2, ensure_ascii=False)
        print(f"[Whisper] Transcript saved → {out_path}")
        return out_path