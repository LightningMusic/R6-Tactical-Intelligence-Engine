"""
Milestone 4, phase 1: TimelineAligner.parse_session_start_epoch is a small
static helper factored out of app/session_manager.py's inline filename
regex so both the client's local transcription path and the server's new
transcription pipeline (server/services/session_processing.py) compute a
session's start time identically. Filename-based parsing is deliberately
preferred over mtime/ctime because it survives the recording being zipped
into a package and re-extracted on the server, where ctime/mtime reflect
extraction time, not recording time.
"""

from datetime import datetime
from pathlib import Path

from analysis.timeline_aligner import TimelineAligner


def test_parses_obs_style_filename(tmp_path: Path):
    recording = tmp_path / "2026-04-27 16-38-48.mp4"
    recording.write_bytes(b"fake")

    epoch = TimelineAligner.parse_session_start_epoch(recording)

    assert epoch is not None
    dt = datetime.fromtimestamp(epoch)
    assert (dt.year, dt.month, dt.day) == (2026, 4, 27)
    assert (dt.hour, dt.minute, dt.second) == (16, 38, 48)


def test_falls_back_to_mtime_for_non_obs_filename(tmp_path: Path):
    recording = tmp_path / "session_audio.mp4"
    recording.write_bytes(b"fake")

    epoch = TimelineAligner.parse_session_start_epoch(recording)

    assert epoch is not None
    assert epoch == recording.stat().st_mtime


def test_returns_none_for_missing_file(tmp_path: Path):
    missing = tmp_path / "does_not_exist.mp4"
    assert TimelineAligner.parse_session_start_epoch(missing) is None


def test_returns_none_for_none_path():
    assert TimelineAligner.parse_session_start_epoch(None) is None
