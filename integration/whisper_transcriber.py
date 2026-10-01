import warnings
import sys
import io
import os
import json
import re
import subprocess
import tempfile
import wave
from collections import deque
from pathlib import Path
from typing import Any, Optional, Callable

from app.config import TRANSCRIPTS_DIR, WHISPER_MODEL_PATH

# At top of file, define once:
_CREATE_NO_WINDOW = 0x08000000 if sys.platform == "win32" else 0

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
    """
    Returns duration in seconds, asking ffmpeg itself -- not ffprobe.

    2026-09-15 fix: this used to shell out to "ffprobe.exe" (falling back
    to a bare "ffprobe" on PATH), but this project has only ever bundled
    ffmpeg.exe -- ffprobe.exe was never bundled, built, or downloaded
    anywhere in this codebase or its build pipeline. On any machine
    without a separate, full, system-wide ffmpeg install already on
    PATH -- which is the exact "no admin rights, single portable exe off
    a USB stick" case this whole project is built for -- that ffprobe
    call always failed, silently, and fell through to the crude
    size-based estimate below.

    That estimate assumes ~1.5 MB/min, which is wildly wrong for a real
    OBS recording. Confirmed against a real session: a real ~167-minute
    recording (5:00pm-7:47pm) at 3787MB (~22.7 MB/min) was misjudged as
    ~2524 minutes -- a ~15x overestimate matching this exact fallback
    formula almost to the decimal. That drove transcribe_full() to plan
    for 253 ten-minute chunks instead of the real ~17, spending most of
    that time transcribing silence past the actual end of the file.

    Fix: ask ffmpeg (which IS always present -- it's what does the actual
    decoding everywhere else in this file) for the duration directly.
    Running it with an input but no output makes it print the file's own
    info, including a "Duration: HH:MM:SS.ss" line, to stderr before
    exiting non-zero -- expected and fine, since only that text is
    needed, not a real conversion. This is standard, long-stable ffmpeg
    behavior, not a version-specific guess.
    """
    try:
        result = subprocess.run(
            [str(ffmpeg_path), "-i", str(input_path)],
            capture_output=True, text=True, timeout=30,
            creationflags=_CREATE_NO_WINDOW,
        )
        stderr = result.stderr or ""
        m = re.search(r"Duration:\s*(\d+):(\d{2}):(\d{2})\.(\d+)", stderr)
        if m:
            hours, minutes, seconds, frac = m.groups()
            return (
                int(hours) * 3600
                + int(minutes) * 60
                + int(seconds)
                + int(frac) / (10 ** len(frac))
            )
    except Exception as e:
        print(f"[Whisper] ffmpeg duration probe failed: {e}")

    # Last-resort fallback, only reached if ffmpeg itself couldn't be run
    # at all (missing/corrupt binary) -- should be rare now that the
    # primary path above no longer depends on a companion binary
    # (ffprobe) this project never actually ships.
    try:
        mb = input_path.stat().st_size / (1024 * 1024)
        print(f"[Whisper] Falling back to size-based duration estimate "
              f"for {input_path.name} -- this is known to be inaccurate.")
        return (mb / 1.5) * 60.0
    except Exception:
        return 0.0


def _extract_audio_chunk(
    ffmpeg_path: Path,
    input_path: Path,
    output_path: Path,
    start_sec: float,
    duration_sec: float,
    stream_index: Optional[int] = None,
) -> bool:
    # stream_index picks one audio track (0 = OBS track 1, ...). Left as None,
    # ffmpeg takes its default -- the first audio stream.
    track = ["-map", f"0:a:{stream_index}"] if stream_index is not None else []
    try:
        result = subprocess.run(
            [
                str(ffmpeg_path), "-y",
                "-ss", f"{start_sec:.3f}",
                "-t",  f"{duration_sec:.3f}",
                "-i",  str(input_path),
                *track,
                "-vn",          # drop video
                "-ac", "1",     # mono
                "-ar", "16000", # 16 kHz — Whisper native
                "-acodec", "pcm_s16le",
                str(output_path),
            ],
            capture_output=True,
            timeout=120,
            creationflags=_CREATE_NO_WINDOW,
        )
        return result.returncode == 0 and output_path.exists()
    except Exception as e:
        print(f"[Whisper] ffmpeg extraction failed: {e}")
        return False


def extract_match_audio(
    ffmpeg_path: Path,
    pieces: list[tuple[Path, float, float]],
    output_path: Path,
    bitrate: str = "48k",
    stream_index: Optional[int] = None,
) -> bool:
    """
    Builds one compressed (mono, 16 kHz AAC) audio file out of one or more
    (recording_file, start_sec, duration_sec) pieces -- more than one when a
    match straddles an OBS file split.

    Compressed on purpose: the old 16-bit WAV clip was ~1.9 MB per minute, so
    a 30-minute match meant a ~57 MB upload that kept timing out through the
    Tailscale Funnel. Speech at 48 kbps AAC is ~0.36 MB per minute and
    Whisper transcribes it just as well.
    """
    if not pieces:
        return False
    try:
        with tempfile.TemporaryDirectory() as tmp:
            wavs: list[Path] = []
            for i, (path, start_sec, duration_sec) in enumerate(pieces):
                wav = Path(tmp) / f"piece_{i}.wav"
                if _extract_audio_chunk(ffmpeg_path, path, wav, start_sec, duration_sec, stream_index) \
                        and wav.exists() and wav.stat().st_size > 1024:
                    wavs.append(wav)
            if not wavs:
                return False

            cmd = [str(ffmpeg_path), "-y"]
            for wav in wavs:
                cmd += ["-i", str(wav)]
            if len(wavs) > 1:
                inputs = "".join(f"[{i}:a]" for i in range(len(wavs)))
                cmd += ["-filter_complex", f"{inputs}concat=n={len(wavs)}:v=0:a=1[out]",
                        "-map", "[out]"]
            cmd += ["-ac", "1", "-ar", "16000", "-c:a", "aac", "-b:a", bitrate,
                    str(output_path)]
            result = subprocess.run(
                cmd, capture_output=True, timeout=300, creationflags=_CREATE_NO_WINDOW,
            )
            return result.returncode == 0 and output_path.exists()
    except Exception as e:
        print(f"[Whisper] match audio extraction failed: {e}")
        return False


# Whisper's classic failure on long stretches of silence or game noise is a
# repetition loop -- the same short phrase emitted hundreds of times ("I don't
# know," x649 in one real 20-minute clip). The decoder's own defence (retry at
# a higher temperature when a segment's compression ratio is too high) only
# works if it's given a temperature *list* to fall back through, and feeding
# each segment's text into the next one's prompt is what keeps a loop going.
_DECODE_TEMPERATURES = (0.0, 0.2, 0.4, 0.6, 0.8, 1.0)
_LOOP_WINDOW = 16          # how many recent segments to compare against
_LOOP_MAX_IN_WINDOW = 3    # a phrase may appear this often within the window


def _normalize_segment_text(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", (text or "").lower()).strip()


def collapse_repetitions(segments: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], int]:
    """
    Drops looped segments: empty ones, and any phrase that already appeared
    _LOOP_MAX_IN_WINDOW times among the last _LOOP_WINDOW segments (which also
    catches two-phrase alternations, not just straight repeats). Returns
    (kept_segments, dropped_count).
    """
    kept: list[dict[str, Any]] = []
    recent: deque[str] = deque(maxlen=_LOOP_WINDOW)
    dropped = 0
    for seg in segments:
        key = _normalize_segment_text(str(seg.get("text", "")))
        if not key:
            dropped += 1
            continue
        looped = recent.count(key) >= _LOOP_MAX_IN_WINDOW
        recent.append(key)
        if looped:
            dropped += 1
            continue
        kept.append(seg)
    return kept, dropped


# =====================================================
# 2026-09-10 fix: load audio ourselves so Whisper never gets the chance
# to flash a console window mid-match.
#
# openai-whisper's own model.transcribe() accepts either a file path OR
# an already-loaded float32 numpy array. When given a path, it calls its
# own internal whisper.audio.load_audio(), which shells out to ffmpeg
# via a plain subprocess.run() call that Whisper never suppresses the
# console window for -- unlike every ffmpeg/r6-dissect call we make
# ourselves in this codebase (see _CREATE_NO_WINDOW above, and the same
# pattern in integration/rec_importer.py). Since this build runs as a
# windowed app with no console of its own, every one of Whisper's own
# internal ffmpeg calls had nowhere to send output except a brand-new
# console window -- which is exactly the window that flashed and stole
# focus every single chunk (and every per-user file) during local
# transcription, pulling the user out of the game mid-match.
#
# The fix is to always hand model.transcribe() an ndarray we loaded
# ourselves instead of a path, so its internal loader is never invoked
# at all. _load_whisper_audio() below picks the cheapest correct way to
# get there for whatever file it's given.
# =====================================================

def _read_pcm16_wav_mono(path: Path, expected_sr: int) -> Any:
    """
    Reads a WAV file we already know is mono/16-bit/expected_sr straight
    into a Whisper-ready float32 array using nothing but the standard
    library's `wave` module -- no subprocess at all. Our own
    _extract_audio_chunk() always produces exactly this format, so this
    is the fast, no-subprocess path for every chunk during full-recording
    transcription.

    Raises ValueError/wave.Error if the file isn't in the exact format
    expected, so the caller can fall back to the general-purpose
    ffmpeg-based loader below instead of silently mis-decoding audio.
    """
    import numpy as np
    with wave.open(str(path), "rb") as wf:
        if wf.getnchannels() != 1 or wf.getsampwidth() != 2 or wf.getframerate() != expected_sr:
            raise ValueError(
                f"{path} is not mono/16-bit/{expected_sr}Hz "
                f"(got {wf.getnchannels()}ch, {wf.getsampwidth() * 8}-bit, {wf.getframerate()}Hz)"
            )
        raw = wf.readframes(wf.getnframes())
    return np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0


def _load_audio_via_ffmpeg(ffmpeg_path: Path, path: Path, sr: int = 16000) -> Any:
    """
    General-purpose loader for any input format (e.g. the 48kHz stereo
    WAVs voice_buffers.py exports per Discord user): re-implements exactly
    what whisper.audio.load_audio() does internally -- decode/resample/
    downmix via ffmpeg to raw s16le PCM on stdout, then normalize to
    float32 -- except WITH _CREATE_NO_WINDOW set, unlike Whisper's own
    copy of this same code.
    """
    import numpy as np
    cmd = [
        str(ffmpeg_path), "-nostdin", "-threads", "0",
        "-i", str(path),
        "-f", "s16le", "-ac", "1", "-acodec", "pcm_s16le", "-ar", str(sr),
        "-",
    ]
    result = subprocess.run(
        cmd, capture_output=True, timeout=120, check=True,
        creationflags=_CREATE_NO_WINDOW,
    )
    return np.frombuffer(result.stdout, dtype=np.int16).astype(np.float32) / 32768.0


def _load_whisper_audio(ffmpeg_path: Path, path: Path, sr: int = 16000) -> Any:
    """Fast, no-subprocess path for our own already-correctly-formatted
    chunk WAVs, with an automatic fallback to the general-purpose
    ffmpeg loader for anything that isn't exactly mono/16-bit/sr."""
    try:
        return _read_pcm16_wav_mono(path, sr)
    except (ValueError, wave.Error):
        return _load_audio_via_ffmpeg(ffmpeg_path, path, sr)


class WhisperTranscriber:

    CHUNK_DURATION_SEC = 600  # 10-minute chunks

    # 2026-09-11, revised 2026-09-16: cosine-distance cutoff for automatic
    # speaker-count detection in _cluster_segments_by_voice() -- two
    # voiceprints closer than this are treated as the same speaker, farther
    # apart as different speakers. AgglomerativeClustering(distance_threshold=X)
    # merges any pair of clusters BELOW X, so a HIGHER threshold merges more
    # aggressively (fewer, bigger clusters) and a LOWER one merges less
    # (more, smaller clusters) -- the original comment here had this
    # backwards. Raised from 0.45 -> 0.7 after a real solo/near-solo session
    # (2026-09-16) got split into ~10 "speakers" out of what was really 1-2
    # real voices: 0.45, tuned only on clean synthetic embeddings, was far
    # too sensitive to real mic/Discord-codec noise on a single voice. Still
    # not validated against a real multi-person session -- if a future
    # session with several genuinely distinct teammates starts getting
    # merged into too few speakers, lower this back down a bit.
    DIARIZATION_DISTANCE_THRESHOLD = 0.7

    def __init__(
        self,
        model_path: Optional[Path] = None,
        model_size: Optional[str] = None,
    ) -> None:
        # Defaults preserve exact prior behavior for existing (client)
        # callers that pass nothing. The overrides exist so the server's
        # own transcription pipeline can point at its own bundled model
        # file instead of the client's app.config paths/settings.
        self._model: Any    = None   # whisper.Whisper — typed as Any to avoid import-time issues
        self._ffmpeg: Optional[Path] = None
        self._model_path: Path = model_path if model_path is not None else WHISPER_MODEL_PATH
        self._model_size_override: Optional[str] = model_size
        self._voice_encoder: Any = None  # resemblyzer.VoiceEncoder — lazy, optional dependency

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

        if not self._model_path.exists():
            raise FileNotFoundError(
                f"Whisper model not found at {self._model_path}\n"
                "Download it by running once in your venv:\n"
                "  python -c \"import whisper; "
                "whisper.load_model('small', download_root='data/models/')\""
            )

        # Confirm ffmpeg before loading the model
        self._get_ffmpeg()

        if self._model_size_override is not None:
            size = self._model_size_override
        else:
            from app.config import settings
            size = settings.WHISPER_MODEL_SIZE

        print(f"[Whisper] Loading '{size}' model from {self._model_path.parent} ...")

        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            self._model = whisper.load_model(   # type: ignore[attr-defined]
                size,
                download_root=str(self._model_path.parent),
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
                    # Load the chunk ourselves (no console-window flash) instead
                    # of handing Whisper a path -- see _load_whisper_audio() above.
                    chunk_audio = _load_whisper_audio(ffmpeg, chunk_file)

                    # self._model.transcribe is typed as Any so Pylance won't complain
                    raw: dict[str, Any] = self._model.transcribe(   # type: ignore[union-attr]
                        chunk_audio,
                        language=language,
                        verbose=None,   # False still draws a tqdm bar on stderr, which the server log files as [ERROR]
                        fp16=False,
                        word_timestamps=True,
                        beam_size=5,
                        temperature=_DECODE_TEMPERATURES,
                        condition_on_previous_text=False,
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

        all_segments, dropped = collapse_repetitions(all_segments)
        if dropped:
            print(f"[Whisper] Dropped {dropped} looped/empty segment(s) "
                  f"(repetition guard).")
        full_text  = " ".join(str(seg.get("text", "")).strip() for seg in all_segments)
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

    def transcribe_per_user(
        self,
        user_audio_files: dict[str, Path],
        language: str = "en",
        progress_callback: Optional[Callable[[str], None]] = None,
    ) -> dict[str, dict]:
        """
        Transcribes each user's individual audio file.
        Returns {display_name: whisper_result_dict}.
        """
        _ensure_console()
        self._load_model()

        results: dict[str, dict] = {}

        for i, (name, wav_path) in enumerate(user_audio_files.items()):
            if not wav_path.exists():
                continue

            mb  = wav_path.stat().st_size / (1024 * 1024)
            msg = f"Transcribing {name} ({mb:.1f} MB) [{i+1}/{len(user_audio_files)}]..."
            print(f"[Whisper] {msg}")
            if progress_callback:
                progress_callback(msg)

            try:
                # Load ourselves (no console-window flash) instead of handing
                # Whisper a path -- see _load_whisper_audio() above. These are
                # 48kHz stereo files, so this always takes the ffmpeg branch.
                user_audio = _load_whisper_audio(self._get_ffmpeg(), wav_path)

                result: dict = self._model.transcribe(  # type: ignore[union-attr]
                    user_audio,
                    language=language,
                    verbose=None,
                    fp16=False,
                    word_timestamps=True,
                    beam_size=5,
                    temperature=_DECODE_TEMPERATURES,
                    condition_on_previous_text=False,
                    no_speech_threshold=0.5,
                    logprob_threshold=-1.0,
                    compression_ratio_threshold=2.4,
                )
                kept, dropped = collapse_repetitions(list(result.get("segments") or []))
                if dropped:
                    print(f"[Whisper] {name}: dropped {dropped} looped/empty segment(s).")
                    result["segments"] = kept
                    result["text"] = " ".join(str(s.get("text", "")).strip() for s in kept)
                results[name] = result
                word_count = len((result.get("text") or "").split())
                print(f"[Whisper] {name}: {word_count} words")
            except Exception as e:
                print(f"[Whisper] Failed for {name}: {e}")

        return results


    def build_attributed_transcript(
        self,
        per_user_results: dict[str, dict],
    ) -> list[dict]:
        """
        Merges per-user transcripts into a single chronological list,
        each item tagged with the speaker's name.
        Format: [{speaker, start, end, text}, ...]
        """
        all_segments: list[dict] = []

        for speaker_name, result in per_user_results.items():
            for seg in (result.get("segments") or []):
                if not isinstance(seg, dict):
                    continue
                text = str(seg.get("text") or "").strip()
                if not text:
                    continue
                all_segments.append({
                    "speaker": speaker_name,
                    "start":   float(seg.get("start") or 0.0),
                    "end":     float(seg.get("end")   or 0.0),
                    "text":    text,
                })

        # Sort chronologically
        all_segments.sort(key=lambda s: s["start"])
        return all_segments


    def format_attributed_transcript(
        self,
        attributed: list[dict],
        match_start_sec: float = 0.0,
        match_end_sec: float   = float("inf"),
    ) -> str:
        """
        Formats an attributed transcript as readable text.
        Optionally clips to a match time window.
        """
        lines: list[str] = []

        for seg in attributed:
            start = seg["start"]
            end   = seg["end"]

            if end < match_start_sec or start > match_end_sec:
                continue

            mins = int(start // 60)
            secs = int(start % 60)
            lines.append(
                f"[{mins:02d}:{secs:02d}] {seg['speaker']}: {seg['text']}"
            )

        return "\n".join(lines)
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
        audio_path: Optional[Path] = None,
    ) -> dict[str, dict[str, Any]]:
        """
        Groups Whisper's transcript segments into "Speaker_N" turns.

        When `audio_path` is given (the full session recording — Whisper's
        segment start/end times are offsets into it), each segment gets a
        short voice "fingerprint" (a resemblyzer d-vector) and segments
        with similar fingerprints are grouped as the same speaker,
        regardless of how much silence sits between them. That's what
        actually lets one "Speaker_N" label mean the same person for the
        whole match instead of just until the next pause — see
        _cluster_segments_by_voice() below.

        Falls back to the original silence-gap heuristic (a new "speaker"
        every time a pause longer than SILENCE_THRESHOLD is seen, cycling
        round-robin through n_speakers labels) whenever no audio_path is
        given, resemblyzer/scikit-learn aren't installed, or clustering
        fails for any reason — that path needs nothing but the segment
        timestamps Whisper already produced, so transcription never breaks
        over an optional dependency.

        Either way, "Speaker_N" is per-recording only — it means "probably
        the same person," not which person. Turning that into a real
        player name is the manual tagging step (see
        gui/speaker_tagging_dialog.py and database repositories'
        transcript_speaker_labels table).
        """
        if not segments:
            return {}

        if audio_path is not None:
            try:
                clustered = self._cluster_segments_by_voice(segments, audio_path, n_speakers)
            except Exception as e:
                print(f"[Whisper] Voice clustering failed ({e}) — falling back to silence-gap grouping")
                clustered = None
            if clustered is not None:
                return clustered

        return self._diarize_by_silence_gaps(segments, n_speakers)

    def _diarize_by_silence_gaps(
        self,
        segments: list[Any],
        n_speakers: int = 5,
    ) -> dict[str, dict[str, Any]]:
        """Original heuristic: no audio analysis at all, just starts a
        "new speaker" (round-robin) after a long-enough pause. Cheap,
        dependency-free, and often wrong the moment the same person talks
        twice in a row after a pause — kept as the always-available
        fallback under the real voice-clustering path above."""
        SILENCE_THRESHOLD = 1.5

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

            self._add_segment_to_speaker(
                speakers, current_speaker,
                start, float(seg.get("end") or start), text,
            )
            last_end = float(seg.get("end") or start)

        self._fill_top_words(speakers)
        return speakers

    def _cluster_segments_by_voice(
        self,
        segments: list[Any],
        audio_path: Path,
        n_speakers: int,
    ) -> Optional[dict[str, dict[str, Any]]]:
        """
        Real speaker diarization: embeds each transcript segment's audio as
        a voiceprint (resemblyzer) and agglomeratively clusters those
        voiceprints into up to n_speakers groups. Returns None (never
        raises past this point) if the optional dependency isn't
        installed or there isn't enough usable audio to cluster — the
        caller falls back to the silence-gap heuristic either way.

        This is genuinely optional and CPU-only: `pip install
        resemblyzer` pulls in a small (~17MB) PyTorch d-vector model, no
        GPU, no cloud, no account/API key. It is NOT installed by default
        (see requirements.txt) since it does add real weight to the USB
        build for a team that may not want it.
        """
        try:
            import numpy as np
            from resemblyzer import VoiceEncoder, preprocess_wav
            from sklearn.cluster import AgglomerativeClustering
        except ImportError:
            return None  # optional dependency not installed

        if not Path(audio_path).exists():
            return None

        MIN_SEGMENT_SEC = 0.5  # shorter clips rarely carry a usable voiceprint

        usable: list[dict[str, Any]] = []
        for seg in segments:
            if not isinstance(seg, dict):
                continue
            text = str(seg.get("text") or "").strip()
            if not text:
                continue
            start = float(seg.get("start") or 0.0)
            end   = float(seg.get("end") or start)
            if end - start < MIN_SEGMENT_SEC:
                continue
            usable.append({"start": start, "end": end, "text": text})

        if len(usable) < 2:
            return None  # nothing meaningful to cluster

        wav = preprocess_wav(str(audio_path))
        sr  = 16000  # resemblyzer.audio.sampling_rate — fixed by the model

        if self._voice_encoder is None:
            self._voice_encoder = VoiceEncoder("cpu")
        encoder = self._voice_encoder

        embeddings: list[Any] = []
        kept: list[dict[str, Any]] = []
        for seg in usable:
            start_sample = max(0, int(seg["start"] * sr))
            end_sample   = min(len(wav), int(seg["end"] * sr))
            clip = wav[start_sample:end_sample]
            if len(clip) < int(sr * 0.3):
                continue
            try:
                embeddings.append(encoder.embed_utterance(clip))
                kept.append(seg)
            except Exception:
                continue

        if len(kept) < 2:
            return None

        embeddings_arr = np.stack(embeddings)

        # 2026-09-11 fix: this used to always force exactly n_speakers
        # clusters (AgglomerativeClustering(n_clusters=n_speakers, ...)) --
        # meaning a solo session with just one person talking still got
        # artificially split into n_speakers-many "Speaker_N" labels, since
        # forcing an exact cluster count means the algorithm has to find
        # *some* split even when every voiceprint genuinely belongs to the
        # same person. Reported directly: "it automatically tries to split
        # up the recording into five people... but it's normally just me."
        #
        # Switched to sklearn's distance-based auto-clustering
        # (n_clusters=None, distance_threshold=...): it keeps merging the
        # two most-similar remaining clusters until every remaining pair is
        # farther apart than the threshold, so it lands on however many
        # distinct voices the audio actually seems to contain -- 1, when
        # that's genuinely all there is, up to n_speakers.
        #
        # n_speakers is now a CEILING, not a target: if auto-detection
        # still comes back with more distinct voices than that (unlikely,
        # but possible on noisy/short clips), this falls back to the old
        # forced-count behavior as a safety net -- so this change can only
        # ever do as well as before in the worst case, never worse.
        #
        # DIARIZATION_DISTANCE_THRESHOLD is a starting point, not a
        # validated constant -- like the rest of this diarization pipeline,
        # it hasn't been tuned against real recorded voices yet (see
        # requirements.txt / Milestone 6 notes on that same gap). If
        # sessions start coming back consistently over- or under-split,
        # this is the first number to adjust.
        auto_labels = AgglomerativeClustering(
            n_clusters=None,
            distance_threshold=self.DIARIZATION_DISTANCE_THRESHOLD,
            metric="cosine", linkage="average",
        ).fit_predict(embeddings_arr)
        n_found = len(set(auto_labels.tolist()))

        if n_found <= max(1, n_speakers):
            labels = auto_labels
        else:
            n_clusters = max(1, min(n_speakers, len(kept)))
            labels = AgglomerativeClustering(
                n_clusters=n_clusters, metric="cosine", linkage="average",
            ).fit_predict(embeddings_arr)

        # Relabel clusters "Speaker_1..N" in order of first appearance —
        # keeps the convention that Speaker_1 is whoever talks first,
        # matching the old heuristic and keeping labels easy to skim.
        seen_order: list[int] = []
        for lbl in labels:
            if lbl not in seen_order:
                seen_order.append(int(lbl))
        label_to_name = {lbl: f"Speaker_{i+1}" for i, lbl in enumerate(seen_order)}

        speakers: dict[str, dict[str, Any]] = {}
        for seg, lbl in zip(kept, labels):
            self._add_segment_to_speaker(
                speakers, label_to_name[int(lbl)],
                seg["start"], seg["end"], seg["text"],
            )

        self._fill_top_words(speakers)
        return speakers

    @staticmethod
    def _add_segment_to_speaker(
        speakers: dict[str, dict[str, Any]],
        speaker: str,
        start: float,
        end: float,
        text: str,
    ) -> None:
        if speaker not in speakers:
            speakers[speaker] = {
                "segments":  [],
                "word_count": 0,
                "top_words": [],
                "talk_time": 0.0,
            }
        speakers[speaker]["segments"].append({"start": start, "end": end, "text": text})
        speakers[speaker]["word_count"] += len(text.split())
        speakers[speaker]["talk_time"]  += max(0.0, end - start)

    def clip_speakers_to_match(
        self,
        speakers: dict[str, dict[str, Any]],
        start_sec: float,
        end_sec: float,
    ) -> dict[str, dict[str, Any]]:
        """
        diarize_speakers() runs once over the FULL session recording (every
        match played that session). This slices that result down to just
        one match's window — same idea as clip_to_match() above, but for
        per-speaker data instead of the flat transcript — so a session
        with 3 matches doesn't store the exact same session-wide speaker
        stats on all 3 (which is what happened before this existed:
        every match's stored "speakers" data covered the whole session,
        not that match). Word counts/talk time/top words are recomputed
        from just the segments inside [start_sec, end_sec].
        """
        clipped: dict[str, dict[str, Any]] = {}
        for spk, data in speakers.items():
            for seg in data.get("segments", []):
                s = float(seg.get("start") or 0.0)
                e = float(seg.get("end") or s)
                if e < start_sec or s > end_sec:
                    continue
                self._add_segment_to_speaker(clipped, spk, s, e, str(seg.get("text") or ""))
        self._fill_top_words(clipped)
        return clipped

    @staticmethod
    def _fill_top_words(speakers: dict[str, dict[str, Any]]) -> None:
        STOP_WORDS = {
            "the","a","an","is","it","in","on","to","i","we","and",
            "of","for","at","be","was","are","do","get","go","got",
            "im","its","uh","um","ok","okay","yeah","yep","yes","no",
        }
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