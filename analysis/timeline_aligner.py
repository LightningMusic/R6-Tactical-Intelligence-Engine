import json
import subprocess
from pathlib import Path
from typing import Optional

from app.config import R6_DISSECT_PATH


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
        from datetime import datetime, timezone

        epochs: list[float] = []

        for rec in rec_files:
            try:
                result = subprocess.run(
                    [str(self.dissect_path), str(rec), "--format", "json"],
                    capture_output=True,
                    text=True,
                    timeout=30,
                    check=True,
                )
                data = json.loads(result.stdout)
                ts_str = data.get("timestamp")   # e.g. "2026-03-26T22:24:08Z"

                if ts_str:
                    dt = datetime.fromisoformat(
                        ts_str.replace("Z", "+00:00")
                    )
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
        """
        Converts absolute epoch timestamps to seconds-since-session-start.
        """
        first_round = timestamps[0]
        last_round  = timestamps[-1]

        start_sec = max(
            0.0,
            (first_round - session_start_epoch) - self.PRE_BUFFER_SEC
        )
        end_sec = (
            (last_round - session_start_epoch)
            + self.ROUND_DURATION_ESTIMATE_SEC
            + self.POST_BUFFER_SEC
        )

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