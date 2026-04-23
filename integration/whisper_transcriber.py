import warnings
import sys
import io
import os
import json
from pathlib import Path
from typing import Optional, Callable
import whisper
from typing import Optional
from app.config import TRANSCRIPTS_DIR, WHISPER_MODEL_PATH


def _ensure_console() -> None:
    if sys.stdout is None:
        sys.stdout = io.StringIO()
    if sys.stderr is None:
        sys.stderr = io.StringIO()


def _fix_whisper_assets() -> None:
    if not getattr(sys, "frozen", False):
        return
    internal = Path(sys.executable).parent / "_internal"
    assets   = internal / "whisper" / "assets"
    if not assets.exists():
        print(f"[Whisper] WARNING: assets not found at {assets}")
        return
    try:
        import whisper as _w
        _w.__file__ = str(internal / "whisper" / "__init__.py")
        print(f"[Whisper] Asset path patched → {assets}")
    except Exception as e:
        print(f"[Whisper] Asset patch failed: {e}")


def _find_ffmpeg() -> bool:
    import shutil
    if shutil.which("ffmpeg"):
        return True
    if getattr(sys, "frozen", False):
        exe_dir = Path(sys.executable).parent
        for candidate in [exe_dir / "ffmpeg.exe", exe_dir / "_internal" / "ffmpeg.exe"]:
            if candidate.exists():
                os.environ["PATH"] = str(candidate.parent) + os.pathsep + os.environ.get("PATH", "")
                return True
    return False


class WhisperTranscriber:

    def __init__(self) -> None:
        self._model: Optional[whisper.Whisper] = None

    def _load_model(self) -> None:
        if self._model is not None:
            return
        _ensure_console()
        _fix_whisper_assets()

        try:
            import whisper
        except ImportError:
            raise ImportError("openai-whisper not installed. Run: pip install openai-whisper")

        from app.config import settings

        if not WHISPER_MODEL_PATH.exists():
            raise FileNotFoundError(
                f"Whisper model not found at {WHISPER_MODEL_PATH}\n"
                "Place whisper-small.pt in data/models/"
            )

        if not _find_ffmpeg():
            raise RuntimeError(
                "ffmpeg not found. Place ffmpeg.exe next to R6Analyzer.exe.\n"
                "Get it from: https://www.gyan.dev/ffmpeg/builds/"
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

    def transcribe_full(
        self,
        audio_path: Path,
        language: str = "en",
        progress_callback: Optional[Callable[[str], None]] = None,
    ) -> dict:
        """
        Transcribes the ENTIRE recording with word-level timestamps.
        Returns the full Whisper result dict.
        """
        _ensure_console()
        self._load_model()

        if not audio_path.exists():
            raise FileNotFoundError(f"Audio not found: {audio_path}")

        mb = audio_path.stat().st_size / (1024 * 1024)
        print(f"[Whisper] Transcribing full recording: {audio_path.name} ({mb:.0f} MB)")
        if progress_callback:
            progress_callback(f"Transcribing full recording ({mb:.0f} MB)...")

        self._load_model()

        assert self._model is not None

        result: dict = self._model.transcribe(
            str(audio_path),
            language=language,
            verbose=False,
            fp16=False,
            word_timestamps=True,       # word-level timing for better clip alignment
            beam_size=5,
            best_of=5,
            temperature=0.0,
            condition_on_previous_text=True,
            no_speech_threshold=0.5,
            logprob_threshold=-1.0,
            compression_ratio_threshold=2.4,
        )

        seg_count = len(result.get("segments", []))
        print(f"[Whisper] Full transcription done: {seg_count} segments.")
        if progress_callback:
            progress_callback(f"Full transcription done: {seg_count} segments.")
        return result

    def transcribe(self, audio_path: Path, language: str = "en") -> dict:
        """Backwards-compatible wrapper."""
        return self.transcribe_full(audio_path, language)

    def clip_to_match(
        self,
        full_result: dict,
        match_start_sec: float,
        match_end_sec: float,
    ) -> dict:
        """
        Clips a full transcription to a match time window.
        Uses word-level timestamps when available for tighter alignment.
        """
        segments = full_result.get("segments", [])
        clipped  = []

        for seg in segments:
            seg_start = seg.get("start", 0.0)
            seg_end   = seg.get("end",   0.0)

            # Segment overlaps the match window
            if seg_end < match_start_sec or seg_start > match_end_sec:
                continue

            # If word timestamps available, filter words to window too
            if "words" in seg:
                words_in_window = [
                    w for w in seg["words"]
                    if w.get("start", 0) >= match_start_sec
                    and w.get("end", 0)   <= match_end_sec
                ]
                if words_in_window:
                    clipped_seg = dict(seg)
                    clipped_seg["words"] = words_in_window
                    clipped_seg["text"]  = " ".join(w["word"] for w in words_in_window)
                    clipped_seg["start"] = words_in_window[0]["start"]
                    clipped_seg["end"]   = words_in_window[-1]["end"]
                    clipped.append(clipped_seg)
            else:
                clipped.append(seg)

        return {
            "text":     " ".join(s["text"].strip() for s in clipped),
            "segments": clipped,
        }

    def diarize_speakers(
        self,
        segments: list[dict],
        n_speakers: int = 5,
    ) -> dict[str, dict]:
        """
        Lightweight heuristic speaker diarization using silence gaps and
        volume/timing patterns. Returns a dict mapping speaker labels to stats.

        Note: This is a heuristic approach — true diarization requires pyannote.audio
        which needs HuggingFace auth. This gives useful approximations for team comms.
        """
        if not segments:
            return {}

        speakers: dict[str, dict] = {}
        current_speaker = "Speaker_1"
        speaker_num     = 1
        last_end        = 0.0
        SILENCE_THRESHOLD = 1.5   # seconds — new speaker if gap > this

        for seg in segments:
            start = seg.get("start", 0.0)
            text  = seg.get("text", "").strip()
            if not text:
                continue

            gap = start - last_end

            # Heuristic: significant gap → likely a new speaker
            # In team comms, people rarely talk over each other
            if gap > SILENCE_THRESHOLD and len(speakers) < n_speakers:
                speaker_num    = (speaker_num % n_speakers) + 1
                current_speaker = f"Speaker_{speaker_num}"

            if current_speaker not in speakers:
                speakers[current_speaker] = {
                    "segments":   [],
                    "word_count": 0,
                    "top_words":  [],
                    "talk_time":  0.0,
                }

            words     = text.split()
            duration  = seg.get("end", start) - start
            speakers[current_speaker]["segments"].append({
                "start": start,
                "end":   seg.get("end", start),
                "text":  text,
            })
            speakers[current_speaker]["word_count"] += len(words)
            speakers[current_speaker]["talk_time"]  += duration

            last_end = seg.get("end", start)

        # Compute top words per speaker (excluding common filler)
        STOP_WORDS = {"the","a","an","is","it","in","on","to","i","we","and",
                      "of","for","at","be","was","are","do","get","go","got"}
        for spk, data in speakers.items():
            all_words: dict[str, int] = {}
            for seg_data in data["segments"]:
                for w in seg_data["text"].lower().split():
                    w = w.strip(".,!?")
                    if w and w not in STOP_WORDS and len(w) > 2:
                        all_words[w] = all_words.get(w, 0) + 1
            data["top_words"] = sorted(
                all_words,
                key=lambda w: all_words[w],
                reverse=True
            )[:10]

        return speakers

    def export_full_transcript(
        self,
        full_result: dict,
        match_clips: list[dict],
        output_path: Path,
        speakers: Optional[dict] = None,
    ) -> Path:
        """
        Exports the complete transcript as a formatted TXT file.
        Includes full recording + per-match sections + speaker breakdown.
        """
        lines = [
            "=" * 70,
            "  R6 TACTICAL INTELLIGENCE — FULL SESSION TRANSCRIPT",
            "=" * 70,
            "",
        ]

        # ── Full raw transcript ───────────────────────────────
        lines.append("FULL RECORDING TRANSCRIPT")
        lines.append("─" * 40)
        full_text = full_result.get("text", "").strip()
        if full_text:
            # Wrap at 80 chars for readability
            import textwrap
            lines.extend(textwrap.wrap(full_text, width=80))
        else:
            lines.append("(no speech detected)")
        lines.append("")

        # ── Per-match clips ───────────────────────────────────
        for i, clip in enumerate(match_clips):
            match_id  = clip.get("match_id", i + 1)
            start_sec = clip.get("start_sec", 0)
            end_sec   = clip.get("end_sec",   0)
            text      = clip.get("text", "").strip()

            lines.append(f"MATCH {match_id} TRANSCRIPT  [{start_sec:.0f}s – {end_sec:.0f}s]")
            lines.append("─" * 40)
            if text:
                import textwrap
                lines.extend(textwrap.wrap(text, width=80))
            else:
                lines.append("(no speech in this window)")
            lines.append("")

        # ── Speaker breakdown ─────────────────────────────────
        if speakers:
            lines.append("SPEAKER BREAKDOWN")
            lines.append("─" * 40)
            for spk, data in sorted(speakers.items()):
                lines.append(
                    f"{spk}:  {data['word_count']} words | "
                    f"{data['talk_time']:.0f}s talk time"
                )
                if data["top_words"]:
                    lines.append(f"  Top callouts: {', '.join(data['top_words'][:8])}")
                lines.append("")

        lines.append("=" * 70)

        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text("\n".join(lines), encoding="utf-8")
        print(f"[Whisper] Full transcript saved → {output_path}")
        return output_path

    def save_transcript(self, result: dict, match_id: int) -> Path:
        import json
        TRANSCRIPTS_DIR.mkdir(parents=True, exist_ok=True)
        out_path = TRANSCRIPTS_DIR / f"match_{match_id}_transcript.json"
        with open(out_path, "w", encoding="utf-8") as f:
            json.dump(result, f, indent=2, ensure_ascii=False)
        print(f"[Whisper] Saved → {out_path}")
        return out_path