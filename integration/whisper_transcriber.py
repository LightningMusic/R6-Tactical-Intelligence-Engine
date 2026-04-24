import warnings
import sys
import io
import os
import json
import subprocess
import tempfile
from pathlib import Path
from typing import Any, Optional, Callable

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
    # Also check next to main script in dev mode
    dev_path = Path(__file__).parent.parent / "ffmpeg.exe"
    if dev_path.exists():
        return dev_path
    return None


def _get_audio_duration(ffmpeg_path: Path, input_path: Path) -> float:
    """Returns duration in seconds using ffprobe."""
    ffprobe = Path(str(ffmpeg_path).replace("ffmpeg.exe", "ffprobe.exe"))
    if not ffprobe.exists():
        ffprobe = Path(str(ffmpeg_path).replace("ffmpeg", "ffprobe"))

    try:
        result = subprocess.run(
            [
                str(ffprobe) if ffprobe.exists() else "ffprobe",
                "-v", "error",
                "-show_entries", "format=duration",
                "-of", "default=noprint_wrappers=1:nokey=1",
                str(input_path),
            ],
            capture_output=True, text=True, timeout=30,
            creationflags=0x08000000 if sys.platform == "win32" else 0,
        )
        return float(result.stdout.strip())
    except Exception:
        # Fallback estimate: ~1.5 MB/min for MP4
        mb = input_path.stat().st_size / (1024 * 1024)
        return (mb / 1.5) * 60.0


def _extract_audio_chunk(
    ffmpeg_path: Path,
    input_path: Path,
    output_path: Path,
    start_sec: float,
    duration_sec: float,
) -> bool:
    try:
        result = subprocess.run(
            [
                str(ffmpeg_path), "-y",
                "-ss", f"{start_sec:.3f}",
                "-t",  f"{duration_sec:.3f}",
                "-i",  str(input_path),
                "-vn",          # drop video
                "-ac", "1",     # mono
                "-ar", "16000", # 16 kHz — Whisper native
                "-acodec", "pcm_s16le",
                str(output_path),
            ],
            capture_output=True,
            timeout=120,
            creationflags=0x08000000 if sys.platform == "win32" else 0,
        )
        return result.returncode == 0 and output_path.exists()
    except Exception as e:
        print(f"[Whisper] ffmpeg extraction failed: {e}")
        return False


class WhisperTranscriber:

    CHUNK_DURATION_SEC = 600  # 10-minute chunks

    def __init__(self) -> None:
        self._model: Any    = None   # whisper.Whisper — typed as Any to avoid import-time issues
        self._ffmpeg: Optional[Path] = None

    def _get_ffmpeg(self) -> Path:
        if self._ffmpeg is None:
            self._ffmpeg = _find_ffmpeg()
        if self._ffmpeg is None:
            raise RuntimeError(
                "ffmpeg not found.\n"
                "Place ffmpeg.exe next to R6Analyzer.exe on the USB.\n"
                "Download: https://www.gyan.dev/ffmpeg/builds/ "
                "(ffmpeg-release-essentials.zip → bin/ffmpeg.exe)"
            )
        return self._ffmpeg

    def _load_model(self) -> None:
        if self._model is not None:
            return
        _ensure_console()
        _fix_whisper_assets()

        try:
            import whisper  # type: ignore[import-untyped]
        except ImportError:
            raise ImportError(
                "openai-whisper not installed.\n"
                "Run: pip install openai-whisper"
            )

        from app.config import settings

        if not WHISPER_MODEL_PATH.exists():
            raise FileNotFoundError(
                f"Whisper model not found at {WHISPER_MODEL_PATH}\n"
                "Download it by running once in your venv:\n"
                "  python -c \"import whisper; "
                "whisper.load_model('small', download_root='data/models/')\""
            )

        # Confirm ffmpeg before loading the model
        self._get_ffmpeg()

        size = settings.WHISPER_MODEL_SIZE
        print(f"[Whisper] Loading '{size}' model from {WHISPER_MODEL_PATH.parent} ...")

        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            self._model = whisper.load_model(   # type: ignore[attr-defined]
                size,
                download_root=str(WHISPER_MODEL_PATH.parent),
            )
        print("[Whisper] Model ready.")

    # =====================================================
    # FULL CHUNKED TRANSCRIPTION
    # =====================================================

    def transcribe_full(
        self,
        audio_path: Path,
        language: str = "en",
        progress_callback: Optional[Callable[[str], None]] = None,
    ) -> dict[str, Any]:
        """
        Transcribes the full recording in 10-minute chunks.
        Returns a combined dict with 'text' and 'segments'.
        """
        _ensure_console()
        self._load_model()

        if not audio_path.exists():
            raise FileNotFoundError(f"Recording not found: {audio_path}")

        ffmpeg       = self._get_ffmpeg()
        mb           = audio_path.stat().st_size / (1024 * 1024)
        duration_sec = _get_audio_duration(ffmpeg, audio_path)

        n_chunks = max(1, int(duration_sec / self.CHUNK_DURATION_SEC) + 1)
        msg = (
            f"Recording: {duration_sec / 60:.1f} min ({mb:.0f} MB) — "
            f"processing in {n_chunks} chunk(s)..."
        )
        print(f"[Whisper] {msg}")
        if progress_callback:
            progress_callback(msg)

        all_segments: list[dict[str, Any]] = []
        full_text_parts: list[str]         = []
        chunk_start = 0.0
        chunk_num   = 0

        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp_path = Path(tmp_dir)

            while chunk_start < duration_sec:
                chunk_num  += 1
                chunk_end   = min(chunk_start + self.CHUNK_DURATION_SEC, duration_sec)
                chunk_dur   = chunk_end - chunk_start
                chunk_file  = tmp_path / f"chunk_{chunk_num:03d}.wav"

                pct = int(chunk_start / duration_sec * 100) if duration_sec > 0 else 0
                progress_msg = (
                    f"Transcribing chunk {chunk_num}/{n_chunks} "
                    f"({chunk_start/60:.0f}–{chunk_end/60:.0f} min) [{pct}%]..."
                )
                print(f"[Whisper] {progress_msg}")
                if progress_callback:
                    progress_callback(progress_msg)

                ok = _extract_audio_chunk(
                    ffmpeg, audio_path, chunk_file,
                    chunk_start, chunk_dur,
                )

                if not ok:
                    print(f"[Whisper] Chunk {chunk_num} extraction failed — skipping")
                    chunk_start += self.CHUNK_DURATION_SEC
                    continue

                try:
                    # self._model.transcribe is typed as Any so Pylance won't complain
                    raw: dict[str, Any] = self._model.transcribe(   # type: ignore[union-attr]
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
                    for seg in raw.get("segments", []):
                        seg_dict: dict[str, Any] = dict(seg)
                        seg_dict["start"] = float(seg_dict.get("start", 0.0)) + chunk_start
                        seg_dict["end"]   = float(seg_dict.get("end",   0.0)) + chunk_start

                        words_raw = seg_dict.get("words")
                        if isinstance(words_raw, list):
                            offset_words: list[dict[str, Any]] = []
                            for w in words_raw:
                                w2: dict[str, Any] = dict(w)
                                w2["start"] = float(w2.get("start", 0.0)) + chunk_start
                                w2["end"]   = float(w2.get("end",   0.0)) + chunk_start
                                offset_words.append(w2)
                            seg_dict["words"] = offset_words

                        all_segments.append(seg_dict)

                    chunk_text: str = raw.get("text", "") or ""
                    chunk_text = chunk_text.strip()
                    if chunk_text:
                        full_text_parts.append(chunk_text)

                    print(
                        f"[Whisper] Chunk {chunk_num}: "
                        f"{len(raw.get('segments', []))} segs, "
                        f"{len(chunk_text.split())} words"
                    )

                except Exception as e:
                    print(f"[Whisper] Chunk {chunk_num} transcription error: {e}")

                finally:
                    try:
                        chunk_file.unlink(missing_ok=True)
                    except Exception:
                        pass

                chunk_start += self.CHUNK_DURATION_SEC

        full_text  = " ".join(full_text_parts)
        total_words = len(full_text.split())
        done_msg    = (
            f"Transcription complete: {total_words} words, "
            f"{len(all_segments)} segments."
        )
        print(f"[Whisper] {done_msg}")
        if progress_callback:
            progress_callback(done_msg)

        return {"text": full_text, "segments": all_segments, "language": language}

    def transcribe(self, audio_path: Path, language: str = "en") -> dict[str, Any]:
        """Backwards-compatible wrapper."""
        return self.transcribe_full(audio_path, language)

    # =====================================================
    # CLIP TO MATCH WINDOW
    # =====================================================

    def clip_to_match(
        self,
        full_result: dict[str, Any],
        match_start_sec: float,
        match_end_sec: float,
    ) -> dict[str, Any]:
        segments: list[Any] = full_result.get("segments") or []
        clipped: list[dict[str, Any]] = []

        for seg in segments:
            if not isinstance(seg, dict):
                continue

            seg_start = float(seg.get("start") or 0.0)
            seg_end   = float(seg.get("end")   or 0.0)

            if seg_end < match_start_sec or seg_start > match_end_sec:
                continue

            words_raw = seg.get("words")
            if isinstance(words_raw, list):
                words_in: list[dict[str, Any]] = []
                for w in words_raw:
                    if not isinstance(w, dict):
                        continue
                    w_start = float(w.get("start") or 0.0)
                    w_end   = float(w.get("end")   or 0.0)
                    if w_start >= match_start_sec and w_end <= match_end_sec:
                        words_in.append(w)

                if words_in:
                    new_seg: dict[str, Any] = dict(seg)
                    new_seg["words"] = words_in
                    new_seg["text"]  = " ".join(
                        str(w.get("word") or "") for w in words_in
                    )
                    new_seg["start"] = float(words_in[0].get("start") or 0.0)
                    new_seg["end"]   = float(words_in[-1].get("end")  or 0.0)
                    clipped.append(new_seg)
            else:
                clipped.append(dict(seg))

        return {
            "text":     " ".join(
                str(s.get("text") or "").strip() for s in clipped
            ),
            "segments": clipped,
        }

    # =====================================================
    # SPEAKER DIARIZATION
    # =====================================================

    def diarize_speakers(
        self,
        segments: list[Any],
        n_speakers: int = 5,
    ) -> dict[str, dict[str, Any]]:
        if not segments:
            return {}

        SILENCE_THRESHOLD = 1.5
        STOP_WORDS = {
            "the","a","an","is","it","in","on","to","i","we","and",
            "of","for","at","be","was","are","do","get","go","got",
            "im","its","uh","um","ok","okay","yeah","yep","yes","no",
        }

        speakers: dict[str, dict[str, Any]] = {}
        current_speaker = "Speaker_1"
        speaker_num     = 1
        last_end        = 0.0

        for seg in segments:
            if not isinstance(seg, dict):
                continue

            start = float(seg.get("start") or 0.0)
            text  = str(seg.get("text") or "").strip()
            if not text:
                continue

            gap = start - last_end
            if gap > SILENCE_THRESHOLD:
                speaker_num     = (speaker_num % n_speakers) + 1
                current_speaker = f"Speaker_{speaker_num}"

            if current_speaker not in speakers:
                speakers[current_speaker] = {
                    "segments":  [],
                    "word_count": 0,
                    "top_words": [],
                    "talk_time": 0.0,
                }

            duration = float(seg.get("end") or start) - start
            speakers[current_speaker]["segments"].append({
                "start": start,
                "end":   float(seg.get("end") or start),
                "text":  text,
            })
            speakers[current_speaker]["word_count"] += len(text.split())
            speakers[current_speaker]["talk_time"]  += duration
            last_end = float(seg.get("end") or start)

        # Compute top words per speaker
        for spk_data in speakers.values():
            freq: dict[str, int] = {}
            for seg_item in spk_data["segments"]:
                for w in str(seg_item.get("text", "")).lower().split():
                    w = w.strip(".,!?-")
                    if w and w not in STOP_WORDS and len(w) > 2:
                        freq[w] = freq.get(w, 0) + 1
            spk_data["top_words"] = sorted(
                freq.keys(), key=lambda k: freq[k], reverse=True
            )[:10]

        return speakers

    # =====================================================
    # EXPORT FULL TRANSCRIPT
    # =====================================================

    def export_full_transcript(
        self,
        full_result: dict[str, Any],
        match_clips: list[dict[str, Any]],
        output_path: Path,
        speakers: Optional[dict[str, dict[str, Any]]] = None,
    ) -> Path:
        import textwrap

        lines: list[str] = [
            "=" * 70,
            "  R6 TACTICAL INTELLIGENCE — FULL SESSION TRANSCRIPT",
            "=" * 70,
            "",
            "FULL RECORDING TRANSCRIPT",
            "─" * 40,
        ]

        full_text = str(full_result.get("text") or "").strip()
        if full_text:
            lines.extend(textwrap.wrap(full_text, width=80))
        else:
            lines.append("(no speech detected)")
        lines.append("")

        for i, clip in enumerate(match_clips):
            match_id  = clip.get("match_id", i + 1)
            start_sec = float(clip.get("start_sec") or 0.0)
            end_sec   = float(clip.get("end_sec")   or 0.0)
            text      = str(clip.get("text") or "").strip()

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
            for spk, spk_data in sorted(speakers.items()):
                wc   = int(spk_data.get("word_count") or 0)
                tt   = float(spk_data.get("talk_time") or 0.0)
                top  = list(spk_data.get("top_words") or [])[:8]
                lines.append(f"{spk}:  {wc} words | {tt:.0f}s talk time")
                if top:
                    lines.append(f"  Top callouts: {', '.join(top)}")
            lines.append("")

        lines.append("=" * 70)

        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text("\n".join(lines), encoding="utf-8")
        print(f"[Whisper] Full transcript → {output_path}")
        return output_path

    def save_transcript(self, result: dict[str, Any], match_id: int) -> Path:
        TRANSCRIPTS_DIR.mkdir(parents=True, exist_ok=True)
        out_path = TRANSCRIPTS_DIR / f"match_{match_id}_transcript.json"
        with open(out_path, "w", encoding="utf-8") as f:
            json.dump(result, f, indent=2, ensure_ascii=False)
        print(f"[Whisper] Saved → {out_path}")
        return out_path