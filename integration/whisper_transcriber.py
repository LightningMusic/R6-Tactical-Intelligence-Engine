import warnings
import sys
import io
import os
import json
import subprocess
import tempfile
from pathlib import Path
from typing import Optional, Callable

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
        print(f"[Whisper] WARNING: assets not at {assets}")
        return
    try:
        import whisper as _w
        _w.__file__ = str(internal / "whisper" / "__init__.py")
        print(f"[Whisper] Assets patched → {assets}")
    except Exception as e:
        print(f"[Whisper] Asset patch failed: {e}")


def _find_ffmpeg() -> Optional[Path]:
    """Returns path to ffmpeg executable or None."""
    import shutil
    found = shutil.which("ffmpeg")
    if found:
        return Path(found)
    if getattr(sys, "frozen", False):
        exe_dir = Path(sys.executable).parent
        for candidate in [
            exe_dir / "ffmpeg.exe",
            exe_dir / "_internal" / "ffmpeg.exe",
        ]:
            if candidate.exists():
                os.environ["PATH"] = (
                    str(candidate.parent) + os.pathsep
                    + os.environ.get("PATH", "")
                )
                return candidate
    return None


def _extract_audio_chunk(
    ffmpeg_path: Path,
    input_path: Path,
    output_path: Path,
    start_sec: float,
    duration_sec: float,
) -> bool:
    """Extracts a mono 16kHz audio chunk from video using ffmpeg."""
    try:
        result = subprocess.run(
            [
                str(ffmpeg_path),
                "-y",                          # overwrite
                "-ss", str(start_sec),
                "-t",  str(duration_sec),
                "-i",  str(input_path),
                "-vn",                         # no video
                "-ac", "1",                    # mono
                "-ar", "16000",                # 16kHz (Whisper native)
                "-acodec", "pcm_s16le",        # uncompressed WAV
                str(output_path),
            ],
            capture_output=True,
            timeout=120,
            creationflags=0x08000000 if sys.platform == "win32" else 0,
        )
        return result.returncode == 0
    except Exception as e:
        print(f"[Whisper] ffmpeg chunk extraction failed: {e}")
        return False


def _get_audio_duration(ffmpeg_path: Path, input_path: Path) -> float:
    """Returns duration of audio/video file in seconds."""
    try:
        result = subprocess.run(
            [
                str(ffmpeg_path).replace("ffmpeg", "ffprobe")
                if "ffmpeg" in str(ffmpeg_path)
                else "ffprobe",
                "-v", "error",
                "-show_entries", "format=duration",
                "-of", "default=noprint_wrappers=1:nokey=1",
                str(input_path),
            ],
            capture_output=True, text=True, timeout=30,
        )
        return float(result.stdout.strip())
    except Exception:
        # Fallback: estimate from file size (1.5MB per minute for MP4)
        mb = input_path.stat().st_size / (1024 * 1024)
        return mb / 1.5 * 60


class WhisperTranscriber:

    CHUNK_DURATION_SEC = 600   # 10-minute chunks — manageable memory per chunk
    MAX_CHUNK_MB       = 500   # don't bother chunking below this file size

    def __init__(self) -> None:
        self._model    = None
        self._ffmpeg   = None

    def _get_ffmpeg(self) -> Path:
        if self._ffmpeg is None:
            self._ffmpeg = _find_ffmpeg()
        if self._ffmpeg is None:
            raise RuntimeError(
                "ffmpeg not found. Place ffmpeg.exe next to R6Analyzer.exe.\n"
                "Download from: https://www.gyan.dev/ffmpeg/builds/ "
                "(ffmpeg-release-essentials.zip)"
            )
        return self._ffmpeg

    def _load_model(self) -> None:
        if self._model is not None:
            return
        _ensure_console()
        _fix_whisper_assets()

        try:
            import whisper
        except ImportError:
            raise ImportError(
                "openai-whisper not installed.\n"
                "Run: pip install openai-whisper"
            )

        from app.config import settings

        if not WHISPER_MODEL_PATH.exists():
            raise FileNotFoundError(
                f"Whisper model not found at {WHISPER_MODEL_PATH}\n"
                "Download with: python -c \"import whisper; "
                "whisper.load_model('small', download_root='data/models/')\""
            )

        # Confirm ffmpeg exists before loading model
        self._get_ffmpeg()

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
        Transcribes the full recording using chunked processing.
        Handles large MP4 files by extracting 10-minute audio chunks.
        """
        _ensure_console()
        self._load_model()

        if not audio_path.exists():
            raise FileNotFoundError(f"Recording not found: {audio_path}")

        ffmpeg = self._get_ffmpeg()
        mb     = audio_path.stat().st_size / (1024 * 1024)

        if progress_callback:
            progress_callback(
                f"Preparing transcription: {audio_path.name} ({mb:.0f} MB)..."
            )

        # Get total duration
        duration_sec = _get_audio_duration(ffmpeg, audio_path)
        print(f"[Whisper] Recording duration: {duration_sec/60:.1f} minutes")

        if progress_callback:
            progress_callback(
                f"Recording: {duration_sec/60:.1f} min — "
                f"processing in {int(duration_sec/self.CHUNK_DURATION_SEC)+1} chunks..."
            )

        # Process in chunks
        all_segments: list[dict] = []
        full_text_parts: list[str] = []
        chunk_start = 0.0
        chunk_num   = 0

        import whisper

        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp_path = Path(tmp_dir)

            while chunk_start < duration_sec:
                chunk_num   += 1
                chunk_end    = min(chunk_start + self.CHUNK_DURATION_SEC, duration_sec)
                chunk_dur    = chunk_end - chunk_start
                chunk_file   = tmp_path / f"chunk_{chunk_num:03d}.wav"

                pct = int(chunk_start / duration_sec * 100)
                msg = (
                    f"Transcribing chunk {chunk_num} "
                    f"({chunk_start/60:.0f}–{chunk_end/60:.0f} min) [{pct}%]..."
                )
                print(f"[Whisper] {msg}")
                if progress_callback:
                    progress_callback(msg)

                # Extract audio chunk
                ok = _extract_audio_chunk(
                    ffmpeg, audio_path, chunk_file,
                    chunk_start, chunk_dur
                )

                if not ok or not chunk_file.exists():
                    print(f"[Whisper] Chunk {chunk_num} extraction failed — skipping")
                    chunk_start += self.CHUNK_DURATION_SEC
                    continue

                # Transcribe chunk
                try:
                    chunk_result = self._model.transcribe(
                        str(chunk_file),
                        language=language,
                        verbose=False,
                        fp16=False,
                        word_timestamps=True,
                        beam_size=5,
                        temperature=0.0,
                        condition_on_previous_text=True,
                        no_speech_threshold=0.5,
                        logprob_threshold=-1.0,
                        compression_ratio_threshold=2.4,
                    )

                    # Offset timestamps by chunk start
                    for seg in chunk_result.get("segments", []):
                        seg["start"] += chunk_start
                        seg["end"]   += chunk_start
                        if "words" in seg:
                            for w in seg["words"]:
                                w["start"] += chunk_start
                                w["end"]   += chunk_start
                        all_segments.append(seg)

                    chunk_text = chunk_result.get("text", "").strip()
                    if chunk_text:
                        full_text_parts.append(chunk_text)

                    print(
                        f"[Whisper] Chunk {chunk_num}: "
                        f"{len(chunk_result.get('segments',[]))} segments, "
                        f"{len(chunk_text.split())} words"
                    )

                except Exception as e:
                    print(f"[Whisper] Chunk {chunk_num} transcription error: {e}")

                finally:
                    # Clean up chunk immediately to free disk space
                    try:
                        chunk_file.unlink()
                    except Exception:
                        pass

                chunk_start += self.CHUNK_DURATION_SEC

        full_text = " ".join(full_text_parts)
        total_words = len(full_text.split())

        print(
            f"[Whisper] Full transcription complete: "
            f"{len(all_segments)} segments, {total_words} words"
        )
        if progress_callback:
            progress_callback(
                f"Transcription complete: {total_words} words across "
                f"{len(all_segments)} segments."
            )

        return {
            "text":     full_text,
            "segments": all_segments,
            "language": language,
        }

    def transcribe(self, audio_path: Path, language: str = "en") -> dict:
        """Backwards-compatible wrapper."""
        return self.transcribe_full(audio_path, language)

    def clip_to_match(
        self,
        full_result: dict,
        match_start_sec: float,
        match_end_sec: float,
    ) -> dict:
        segments = full_result.get("segments", [])
        clipped  = []

        for seg in segments:
            seg_start = seg.get("start", 0.0)
            seg_end   = seg.get("end",   0.0)

            if seg_end < match_start_sec or seg_start > match_end_sec:
                continue

            if "words" in seg:
                words_in_window = [
                    w for w in seg["words"]
                    if w.get("start", 0) >= match_start_sec
                    and w.get("end", 0)   <= match_end_sec
                ]
                if words_in_window:
                    seg2 = dict(seg)
                    seg2["words"] = words_in_window
                    seg2["text"]  = " ".join(w.get("word","") for w in words_in_window)
                    seg2["start"] = words_in_window[0]["start"]
                    seg2["end"]   = words_in_window[-1]["end"]
                    clipped.append(seg2)
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
    ) -> dict:
        """
        Heuristic speaker separation by silence gaps.
        Returns speaker → stats dict.
        """
        if not segments:
            return {}

        SILENCE_THRESHOLD = 1.5
        speakers: dict[str, dict] = {}
        current_speaker = "Speaker_1"
        speaker_num     = 1
        last_end        = 0.0
        STOP_WORDS = {
            "the","a","an","is","it","in","on","to","i","we","and",
            "of","for","at","be","was","are","do","get","go","got",
            "im","its","uh","um","ok","okay","yeah","yep","yes","no",
        }

        for seg in segments:
            start = seg.get("start", 0.0)
            text  = seg.get("text", "").strip()
            if not text:
                continue

            gap = start - last_end
            if gap > SILENCE_THRESHOLD:
                speaker_num    = (speaker_num % n_speakers) + 1
                current_speaker = f"Speaker_{speaker_num}"

            if current_speaker not in speakers:
                speakers[current_speaker] = {
                    "segments":   [],
                    "word_count": 0,
                    "top_words":  [],
                    "talk_time":  0.0,
                }

            words    = text.split()
            duration = seg.get("end", start) - start
            speakers[current_speaker]["segments"].append({
                "start": start,
                "end":   seg.get("end", start),
                "text":  text,
            })
            speakers[current_speaker]["word_count"] += len(words)
            speakers[current_speaker]["talk_time"]  += duration
            last_end = seg.get("end", start)

        for spk, data in speakers.items():
            freq: dict[str, int] = {}
            for seg_data in data["segments"]:
                for w in seg_data["text"].lower().split():
                    w = w.strip(".,!?-")
                    if w and w not in STOP_WORDS and len(w) > 2:
                        freq[w] = freq.get(w, 0) + 1
            data["top_words"] = sorted(freq, key=freq.get, reverse=True)[:10]

        return speakers

    def export_full_transcript(
        self,
        full_result: dict,
        match_clips: list[dict],
        output_path: Path,
        speakers: Optional[dict] = None,
    ) -> Path:
        import textwrap

        lines = [
            "=" * 70,
            "  R6 TACTICAL INTELLIGENCE — FULL SESSION TRANSCRIPT",
            "=" * 70,
            "",
            "FULL RECORDING TRANSCRIPT",
            "─" * 40,
        ]

        full_text = full_result.get("text", "").strip()
        if full_text:
            lines.extend(textwrap.wrap(full_text, width=80))
        else:
            lines.append("(no speech detected)")
        lines.append("")

        for i, clip in enumerate(match_clips):
            match_id  = clip.get("match_id", i + 1)
            start_sec = clip.get("start_sec", 0)
            end_sec   = clip.get("end_sec",   0)
            text      = clip.get("text", "").strip()

            lines += [
                f"MATCH {match_id}  [{start_sec:.0f}s – {end_sec:.0f}s]",
                "─" * 40,
            ]
            if text:
                lines.extend(textwrap.wrap(text, width=80))
            else:
                lines.append("(no speech in this match window)")
            lines.append("")

        if speakers:
            lines += ["SPEAKER BREAKDOWN", "─" * 40]
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
        print(f"[Whisper] Full transcript → {output_path}")
        return output_path

    def save_transcript(self, result: dict, match_id: int) -> Path:
        TRANSCRIPTS_DIR.mkdir(parents=True, exist_ok=True)
        out_path = TRANSCRIPTS_DIR / f"match_{match_id}_transcript.json"
        with open(out_path, "w", encoding="utf-8") as f:
            json.dump(result, f, indent=2, ensure_ascii=False)
        print(f"[Whisper] Saved → {out_path}")
        return out_path