"""
OBS splits long recordings into several files (about every 4 GiB), so a
match's audio can be in any of them or straddle two. These pin down how
TimelineAligner maps a wall-clock match window onto those files, using the
real layout from the 2026-09-24 practice: three files, split at 18:10:33 and
19:41:33, with each finished file's mtime landing ~33s before the next
one's name-time.
"""
import os
import subprocess
from datetime import datetime
from pathlib import Path

import pytest

from analysis.timeline_aligner import TimelineAligner
from integration.whisper_transcriber import (
    _find_ffmpeg,
    _get_audio_duration,
    collapse_repetitions,
    extract_match_audio,
)


def epoch(stamp: str) -> float:
    return datetime.strptime(stamp, "%Y-%m-%d %H:%M:%S").timestamp()


@pytest.fixture
def recordings(tmp_path: Path):
    layout = [
        ("2026-09-24 16-39-34.mp4", "2026-09-24 18:10:00"),
        ("2026-09-24 18-10-33.mp4", "2026-09-24 19:41:00"),
        ("2026-09-24 19-41-33.mp4", "2026-09-24 19:43:28"),
    ]
    paths = []
    for name, mtime in layout:
        p = tmp_path / name
        p.write_bytes(b"x")
        os.utime(p, (epoch(mtime), epoch(mtime)))
        paths.append(p)
    return paths


NOW_AFTER_SESSION = epoch("2026-09-24 21:00:00")


def test_splits_are_treated_as_one_continuous_recording(recordings):
    segs = TimelineAligner.recording_segments(recordings[0], now=NOW_AFTER_SESSION)
    assert [s[2].name for s in segs] == [p.name for p in recordings]
    # Each file runs right up to where the next one starts, despite its mtime
    # being a little earlier.
    assert segs[0][1] == segs[1][0] == epoch("2026-09-24 18:10:33")
    assert segs[1][1] == segs[2][0] == epoch("2026-09-24 19:41:33")


def test_segments_are_found_from_any_file_in_the_chain(recordings):
    from_last = TimelineAligner.recording_segments(recordings[-1], now=NOW_AFTER_SESSION)
    from_first = TimelineAligner.recording_segments(recordings[0], now=NOW_AFTER_SESSION)
    assert from_last == from_first


def test_match_inside_the_first_file(recordings):
    segs = TimelineAligner.recording_segments(recordings[0], now=NOW_AFTER_SESSION)
    # Match 35: 16:44 - 17:14
    pieces = TimelineAligner.audio_pieces(segs, epoch("2026-09-24 16:44:00"), epoch("2026-09-24 17:14:00"))
    assert len(pieces) == 1
    path, start, duration = pieces[0]
    assert path == recordings[0]
    assert start == pytest.approx(4 * 60 + 26)          # 4m26s after 16:39:34
    assert duration == pytest.approx(30 * 60)


def test_match_straddling_a_split_uses_both_files(recordings):
    segs = TimelineAligner.recording_segments(recordings[0], now=NOW_AFTER_SESSION)
    # Starts 5 min before the 18:10:33 split, ends 20 min after it.
    pieces = TimelineAligner.audio_pieces(segs, epoch("2026-09-24 18:05:33"), epoch("2026-09-24 18:30:33"))
    assert [p[0] for p in pieces] == [recordings[0], recordings[1]]
    assert pieces[0][2] == pytest.approx(5 * 60)
    assert pieces[1][1] == pytest.approx(0.0)
    assert pieces[1][2] == pytest.approx(20 * 60)
    assert sum(p[2] for p in pieces) == pytest.approx(25 * 60)


def test_match_entirely_in_a_later_file_ignores_earlier_ones(recordings):
    segs = TimelineAligner.recording_segments(recordings[0], now=NOW_AFTER_SESSION)
    # Match 38: 18:36 - 19:03, which the old code looked for in file 1.
    pieces = TimelineAligner.audio_pieces(segs, epoch("2026-09-24 18:36:15"), epoch("2026-09-24 19:03:15"))
    assert [p[0] for p in pieces] == [recordings[1]]
    assert pieces[0][1] == pytest.approx(25 * 60 + 42)  # 25m42s after 18:10:33


def test_window_outside_any_recording_yields_nothing(recordings):
    segs = TimelineAligner.recording_segments(recordings[0], now=NOW_AFTER_SESSION)
    assert TimelineAligner.audio_pieces(segs, epoch("2026-09-24 12:00:00"), epoch("2026-09-24 12:30:00")) == []


def test_file_still_being_written_runs_open_ended(recordings):
    just_after = epoch("2026-09-24 19:44:00")            # newest file touched 32s ago
    segs = TimelineAligner.recording_segments(recordings[0], now=just_after)
    assert segs[-1][1] == float("inf")


@pytest.mark.skipif(os.name != "nt", reason="open-handle check is Windows only")
def test_file_held_open_runs_open_ended_even_with_a_stale_mtime(tmp_path: Path):
    # 2026-09-28 on the FAT32 Kingston: OBS had been writing for 36 minutes
    # but the file's mtime still said 18:12:47 (FAT only updates it on close),
    # so the Bank clip was cut to 60s.
    live = tmp_path / "2026-09-28 18-12-47.mp4"
    live.write_bytes(b"x")
    os.utime(live, (epoch("2026-09-28 18:12:47"),) * 2)
    at_packaging = epoch("2026-09-28 18:48:59")
    with open(live, "ab"):
        segs = TimelineAligner.recording_segments(live, now=at_packaging)
        assert segs[-1][1] == float("inf")
    segs = TimelineAligner.recording_segments(live, now=at_packaging)
    assert segs[-1][1] == epoch("2026-09-28 18:13:47")   # closed: the mtime rule stands


def test_a_gap_before_the_next_recording_is_not_papered_over(tmp_path: Path):
    first = tmp_path / "2026-09-24 10-00-00.mp4"
    second = tmp_path / "2026-09-24 14-00-00.mp4"
    for p, mtime in ((first, "2026-09-24 10:30:00"), (second, "2026-09-24 14:20:00")):
        p.write_bytes(b"x")
        os.utime(p, (epoch(mtime), epoch(mtime)))
    segs = TimelineAligner.recording_segments(first, now=epoch("2026-09-24 21:00:00"))
    # 3.5 hours between them: the first file ends at its own last write, not
    # at the second one's start.
    assert segs[0][1] < epoch("2026-09-24 10:40:00")
    assert TimelineAligner.audio_pieces(segs, epoch("2026-09-24 12:00:00"), epoch("2026-09-24 12:30:00")) == []


def test_non_obs_names_and_other_file_types_are_ignored(tmp_path: Path):
    good = tmp_path / "2026-09-24 10-00-00.mkv"
    good.write_bytes(b"x")
    (tmp_path / "notes.txt").write_text("hi")
    (tmp_path / "2026-09-24 10-00-00.r6session").write_bytes(b"x")
    segs = TimelineAligner.recording_segments(good, now=NOW_AFTER_SESSION)
    assert [s[2].name for s in segs] == [good.name]


# ----------------------------------------------------------------------
# Repetition guard
# ----------------------------------------------------------------------

def seg(text, start=0.0):
    return {"text": text, "start": start, "end": start + 1}


def test_a_looped_phrase_is_cut_to_a_few_repeats():
    looped = [seg(" I don't know,", i) for i in range(649)]
    kept, dropped = collapse_repetitions(looped)
    assert len(kept) == 3
    assert dropped == 646


def test_alternating_loops_are_caught_too():
    looped = []
    for i in range(100):
        looped.append(seg("Can you help me,", i))
        looped.append(seg("I don't know who to play on this one.", i))
    kept, _ = collapse_repetitions(looped)
    assert len(kept) <= 6


def test_ordinary_speech_is_left_alone():
    speech = [seg(t, i) for i, t in enumerate([
        "We got Cade on go.", "Push site now.", "Drone left.", "Yeah.",
        "Where are they?", "Yeah.", "Reinforce that wall.", "Nice one.", "Yeah.",
    ])]
    kept, dropped = collapse_repetitions(speech)
    assert dropped == 0 and kept == speech


def test_a_phrase_is_allowed_again_once_it_has_left_the_window():
    filler = [seg(f"different sentence {i}", i) for i in range(20)]
    early = [seg("Yeah.", i) for i in range(3)]
    late = [seg("Yeah.", 100 + i) for i in range(3)]
    kept, dropped = collapse_repetitions(early + filler + late)
    assert dropped == 0


def test_empty_segments_are_dropped():
    kept, dropped = collapse_repetitions([seg("   "), seg("..."), seg("Real words")])
    assert [s["text"] for s in kept] == ["Real words"] and dropped == 2


# ----------------------------------------------------------------------
# Real ffmpeg round trip (skipped where ffmpeg isn't available)
# ----------------------------------------------------------------------

ffmpeg = _find_ffmpeg()
needs_ffmpeg = pytest.mark.skipif(ffmpeg is None, reason="ffmpeg not available")


def _tone(path: Path, seconds: int, hz: int):
    subprocess.run(
        [str(ffmpeg), "-y", "-f", "lavfi", "-i", f"sine=frequency={hz}:duration={seconds}",
         "-c:a", "aac", str(path)],
        capture_output=True, check=True,
    )


@needs_ffmpeg
def test_extract_match_audio_joins_pieces_from_two_files_and_compresses(tmp_path: Path):
    a, b = tmp_path / "a.mp4", tmp_path / "b.mp4"
    _tone(a, 10, 440)
    _tone(b, 10, 880)
    out = tmp_path / "clip.m4a"

    ok = extract_match_audio(ffmpeg, [(a, 6.0, 4.0), (b, 0.0, 3.0)], out)

    assert ok and out.exists()
    assert _get_audio_duration(ffmpeg, out) == pytest.approx(7.0, abs=0.3)
    # ~48 kbps: nowhere near the ~32 KB/s of the 16-bit WAV this replaces.
    assert out.stat().st_size < 7 * 32_000 / 3


@needs_ffmpeg
def test_extract_match_audio_single_piece(tmp_path: Path):
    a = tmp_path / "a.mp4"
    _tone(a, 8, 440)
    out = tmp_path / "clip.m4a"
    assert extract_match_audio(ffmpeg, [(a, 2.0, 5.0)], out)
    assert _get_audio_duration(ffmpeg, out) == pytest.approx(5.0, abs=0.3)


@needs_ffmpeg
def test_extract_match_audio_fails_cleanly_when_the_window_is_past_the_end(tmp_path: Path):
    a = tmp_path / "a.mp4"
    _tone(a, 5, 440)
    out = tmp_path / "clip.m4a"
    assert extract_match_audio(ffmpeg, [(a, 60.0, 30.0)], out) is False


def test_extract_match_audio_with_no_pieces_is_false(tmp_path: Path):
    assert extract_match_audio(Path("ffmpeg"), [], tmp_path / "x.m4a") is False
