import re
import sys
import json
import subprocess
from pathlib import Path
from typing import Optional

from app.config import R6_DISSECT_PATH

# 2026-09-10: matches the _CREATE_NO_WINDOW pattern already used everywhere
# else in this codebase this call runs a console executable from a windowed
# app (integration/rec_importer.py's own r6-dissect call, integration/
# whisper_transcriber.py's ffmpeg calls) -- this one was missed, so every
# .rec file processed here flashed a console window with nowhere to send
# its output.
_CREATE_NO_WINDOW = 0x08000000 if sys.platform == "win32" else 0


class TimelineAligner:
    """
    Extracts match start/end timestamps from .rec files so the
    session audio can be clipped to per-match segments.

    R6 replay files contain a 'timestamp' field (UTC ISO string)
    which marks when that round was played. We use the earliest
    round timestamp as match start and the latest as match end,
    then add a buffer for pre/post-round audio.
    """

    ROUND_DURATION_ESTIMATE_SEC = 210   # ~3.5 min max per round
    PRE_BUFFER_SEC  = 30                # capture lobby comms before match
    POST_BUFFER_SEC = 60                # capture post-match discussion

    def __init__(self, dissect_path: Path = R6_DISSECT_PATH) -> None:
        self.dissect_path = dissect_path

    # =====================================================
    # PUBLIC
    # =====================================================

    @staticmethod
    def parse_session_start_epoch(recording_path: Path) -> Optional[float]:
        """
        Determines the epoch time a recording started, for use as
        session_start_epoch in get_match_window().

        Strategy 1: parse the timestamp out of an OBS-style filename,
        e.g. "2026-04-27 16-38-48.mp4". This is preferred because it
        survives the file being copied, zipped, or re-extracted (a
        packaged/uploaded recording's mtime/ctime reflects when it was
        extracted, not when it was recorded, but its filename does not
        change) — so this is the strategy both the client (recording
        straight off disk) and the server (recording extracted from an
        uploaded .r6session archive) can both rely on identically.

        Strategy 2 (fallback): the file's own modification time, for a
        recording whose filename doesn't match the OBS pattern.
        """
        import re
        from datetime import datetime

        if recording_path is None or not recording_path.exists():
            return None

        stem = recording_path.stem  # e.g. "2026-04-27 16-38-48"
        m = re.match(r"(\d{4}-\d{2}-\d{2})\s+(\d{2}-\d{2}-\d{2})", stem)
        if m:
            try:
                date_str = m.group(1)
                time_str = m.group(2).replace("-", ":")
                dt = datetime.strptime(f"{date_str} {time_str}", "%Y-%m-%d %H:%M:%S")
                return dt.timestamp()
            except Exception:
                pass

        try:
            return recording_path.stat().st_mtime
        except Exception:
            return None

    # OBS names every file after the moment it was opened, and a recording
    # that hits the split size continues in a new file named for the split
    # moment -- so one session can be several files, and any one match can sit
    # in any of them (or straddle two).
    _OBS_STEM = re.compile(r"(\d{4}-\d{2}-\d{2})\s+(\d{2}-\d{2}-\d{2})")
    _VIDEO_SUFFIXES = {".mp4", ".mkv", ".mov", ".flv", ".ts", ".m4v"}
    _SEAMLESS_GAP_SEC = 180     # a file starting this soon after another ended continues it
    _STILL_WRITING_SEC = 600    # a file touched this recently is treated as still recording

    @classmethod
    def _obs_start_epoch(cls, path: Path) -> Optional[float]:
        from datetime import datetime

        m = cls._OBS_STEM.match(path.stem)
        if not m:
            return None
        try:
            return datetime.strptime(
                f"{m.group(1)} {m.group(2).replace('-', ':')}", "%Y-%m-%d %H:%M:%S"
            ).timestamp()
        except ValueError:
            return None

    @staticmethod
    def _open_elsewhere(path: Path) -> bool:
        """True when another process (OBS) still holds the file open. FAT32
        and exFAT don't move a file's modified time while it's being written
        -- only when it's closed -- so on a USB stick a recording in progress
        looks like it stopped the moment it started. Windows only; elsewhere
        this reports False and the mtime rule stands."""
        if sys.platform != "win32":
            return False
        import ctypes
        from ctypes import wintypes

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.CreateFileW.restype = wintypes.HANDLE
        kernel32.CreateFileW.argtypes = [
            wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, wintypes.LPVOID,
            wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE,
        ]
        GENERIC_READ, OPEN_EXISTING, ERROR_SHARING_VIOLATION = 0x80000000, 3, 32
        handle = kernel32.CreateFileW(str(path), GENERIC_READ, 0, None, OPEN_EXISTING, 0, None)
        if handle in (None, wintypes.HANDLE(-1).value):
            return ctypes.get_last_error() == ERROR_SHARING_VIOLATION
        kernel32.CloseHandle(handle)
        return False

    @classmethod
    def recording_segments(
        cls, recording_path: Path, now: Optional[float] = None
    ) -> list[tuple[float, float, Path]]:
        """
        (start_epoch, end_epoch, path) for every OBS-named recording in the
        same folder as recording_path, oldest first. Each file is taken to run
        until the next one starts when they're back to back (a split), else
        until its own last write; the newest runs open-ended while it's still
        being written. recording_path itself is always included, even if its
        name isn't OBS-style (start then falls back to parse_session_start_epoch).
        """
        import time

        now = time.time() if now is None else now
        found: dict[Path, tuple[float, float]] = {}   # path -> (start, mtime)
        try:
            candidates = list(recording_path.parent.iterdir())
        except OSError:
            candidates = []
        for p in candidates:
            if p.suffix.lower() not in cls._VIDEO_SUFFIXES or not p.is_file():
                continue
            start = cls._obs_start_epoch(p)
            if start is None:
                continue
            try:
                found[p] = (start, p.stat().st_mtime)
            except OSError:
                continue

        if recording_path not in found and recording_path.exists():
            start = cls.parse_session_start_epoch(recording_path)
            if start is not None:
                found[recording_path] = (start, recording_path.stat().st_mtime)

        ordered = sorted(found.items(), key=lambda kv: kv[1][0])
        segments: list[tuple[float, float, Path]] = []
        for i, (path, (start, mtime)) in enumerate(ordered):
            if i + 1 < len(ordered):
                next_start = ordered[i + 1][1][0]
                end = next_start if next_start - mtime <= cls._SEAMLESS_GAP_SEC else max(mtime, start)
            else:
                still_writing = now - mtime < cls._STILL_WRITING_SEC or cls._open_elsewhere(path)
                end = float("inf") if still_writing else max(mtime, start) + 60
            segments.append((start, end, path))
        return segments

    @staticmethod
    def audio_pieces(
        segments: list[tuple[float, float, Path]],
        abs_start: float,
        abs_end: float,
    ) -> list[tuple[Path, float, float]]:
        """(file, seconds_into_file, duration) for each recording file that
        overlaps the wall-clock window [abs_start, abs_end], in order."""
        pieces: list[tuple[Path, float, float]] = []
        for seg_start, seg_end, path in segments:
            lo = max(abs_start, seg_start)
            hi = min(abs_end, seg_end)
            if hi - lo >= 1.0:
                pieces.append((path, lo - seg_start, hi - lo))
        return pieces

    @staticmethod
    def epochs_from_stamps(stamps: list[str]) -> list[float]:
        """Replay timestamps -> sorted epochs. The "Z" is dropped for the same
        reason as in _extract_timestamps: the digits are local time."""
        from datetime import datetime

        out = []
        for s in stamps:
            try:
                out.append(datetime.fromisoformat(str(s).rstrip("Z")).timestamp())
            except ValueError:
                continue
        return sorted(out)

    def get_match_window_abs(
        self, match_folder: Path, round_timestamps: Optional[list[str]] = None
    ) -> tuple[float, float]:
        """
        The match's wall-clock window as (start_epoch, end_epoch), independent
        of any particular recording file. Same buffers as get_match_window().
        Pass the round timestamps the importer already read to skip running
        r6-dissect over every file again.
        """
        rec_files = sorted(match_folder.glob("*.rec"))
        if not rec_files:
            raise FileNotFoundError(f"No .rec files in {match_folder.name}")

        timestamps = self.epochs_from_stamps(round_timestamps or [])
        if not timestamps:
            timestamps = self._extract_timestamps(rec_files)
        if timestamps:
            return (
                timestamps[0] - self.PRE_BUFFER_SEC,
                timestamps[-1] + self.ROUND_DURATION_ESTIMATE_SEC + self.POST_BUFFER_SEC,
            )

        mtimes = sorted(f.stat().st_mtime for f in rec_files)
        return (
            mtimes[0] - self.ROUND_DURATION_ESTIMATE_SEC - self.PRE_BUFFER_SEC,
            mtimes[-1] + self.POST_BUFFER_SEC,
        )

    def get_match_window(
        self,
        match_folder: Path,
        session_start_epoch: Optional[float] = None,
    ) -> tuple[float, float]:
        """
        Returns (start_seconds, end_seconds) relative to the OBS
        recording start time.

        If session_start_epoch is None, falls back to estimating
        from file modification times.
        """
        rec_files = sorted(match_folder.glob("*.rec"))

        if not rec_files:
            raise FileNotFoundError(
                f"No .rec files in {match_folder.name}"
            )

        timestamps = self._extract_timestamps(rec_files)

        if timestamps and session_start_epoch is not None:
            return self._align_to_session(
                timestamps, session_start_epoch
            )
        else:
            # Fallback: use file modification times
            return self._estimate_from_mtimes(
                rec_files, session_start_epoch
            )

    # =====================================================
    # TIMESTAMP EXTRACTION
    # =====================================================

    def _extract_timestamps(
        self, rec_files: list[Path]
    ) -> list[float]:
        """
        Runs r6-dissect on each .rec file and extracts the
        'timestamp' field as a Unix epoch float.
        Returns sorted list of epoch timestamps.
        """
        from datetime import datetime

        epochs: list[float] = []

        for rec in rec_files:
            try:
                result = subprocess.run(
                    [str(self.dissect_path), str(rec), "--format", "json"],
                    capture_output=True,
                    text=True,
                    timeout=30,
                    check=True,
                    creationflags=_CREATE_NO_WINDOW,
                )
                data = json.loads(result.stdout)
                ts_str = data.get("timestamp")   # e.g. "2026-03-26T22:24:08Z"

                if ts_str:
                    # That trailing "Z" is a lie. r6-dissect's own source
                    # (dissect/header.go) parses the replay's "datetime"
                    # property with time.Parse("2006-01-02-15-04-05", ...) --
                    # a layout with no timezone digit, which Go documents as
                    # defaulting to UTC. But the digits it's parsing are the
                    # recording PC's LOCAL clock (games write local time into
                    # their own replay metadata, not UTC) -- so "Z" gets
                    # stamped onto a value that was never UTC to begin with.
                    # Treating it as real UTC introduced a systematic error
                    # equal to this machine's UTC offset (five-plus hours on
                    # this one) into every alignment calculation below,
                    # surfacing live as "round timestamps predate session
                    # start" and silently-empty voice transcripts. Fix:
                    # take the digits as local wall-clock time as-is, with
                    # no UTC conversion at all.
                    dt = datetime.fromisoformat(ts_str.rstrip("Z"))
                    epochs.append(dt.timestamp())

            except Exception as e:
                print(f"[TimelineAligner] Failed to parse {rec.name}: {e}")
                continue

        return sorted(epochs)

    # =====================================================
    # ALIGNMENT
    # =====================================================

    def _align_to_session(
        self,
        timestamps: list[float],
        session_start_epoch: float,
    ) -> tuple[float, float]:
        first_round = timestamps[0]
        last_round  = timestamps[-1]

        start_offset = first_round - session_start_epoch
        end_offset   = last_round  - session_start_epoch

        # Sanity check — if timestamps are before session start, something is wrong
        # Fall back to estimating from file mtimes
        if start_offset < 0 or end_offset < 0:
            print(
                f"[TimelineAligner] Warning: round timestamps predate session start "
                f"(offset={start_offset:.0f}s). Falling back to mtime estimation."
            )
            # Use relative positioning: assume matches started near beginning
            # of session with a small buffer
            return 0.0, (last_round - first_round) + self.ROUND_DURATION_ESTIMATE_SEC + self.POST_BUFFER_SEC

        start_sec = max(0.0, start_offset - self.PRE_BUFFER_SEC)
        end_sec   = end_offset + self.ROUND_DURATION_ESTIMATE_SEC + self.POST_BUFFER_SEC

        return start_sec, end_sec

    def _estimate_from_mtimes(
        self,
        rec_files: list[Path],
        session_start_epoch: Optional[float],
    ) -> tuple[float, float]:
        """
        Fallback when session start time is unknown.
        Uses file modification times to estimate the window.
        """
        mtimes = sorted(f.stat().st_mtime for f in rec_files)
        first  = mtimes[0]
        last   = mtimes[-1]

        if session_start_epoch is not None:
            start_sec = max(0.0, (first - session_start_epoch) - self.PRE_BUFFER_SEC)
            end_sec   = (last - session_start_epoch) + self.ROUND_DURATION_ESTIMATE_SEC + self.POST_BUFFER_SEC
            return start_sec, end_sec
        else:
            # No session anchor at all — return relative window
            duration = (last - first) + self.ROUND_DURATION_ESTIMATE_SEC
            return 0.0, duration + self.PRE_BUFFER_SEC + self.POST_BUFFER_SEC