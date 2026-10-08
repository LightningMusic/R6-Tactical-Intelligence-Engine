"""
The host sits next to a teammate, so the host's mic hears them and their words were credited to the host
(2026-10-07: 23 and 34 lines in two matches). Whoever speaks is loud on their own mic and quiet on the other.
"""
import numpy as np

from server.services.comms_service import loud_elsewhere, reassign_bleed

SR = 1000                       # a small rate keeps the arrays tiny; the rule doesn't care


def tone(level, sec):
    return (level * np.sin(np.arange(int(sec * SR)) / 3.0)).astype(np.float32)


def scene():
    """20 one-second lines, each said either by the host (H) or the teammate (T)."""
    who = ["H", "H", "T", "H", "H", "T", "H", "H", "H", "T", "H", "H", "T", "H", "H", "H", "T", "H", "H", "H"]
    mine = np.zeros(25 * SR, dtype=np.float32)
    theirs = np.zeros(25 * SR, dtype=np.float32)
    offset = 0.5                                   # their recording runs half a second behind the clip
    host_lines, their_lines = [], []
    for i, w in enumerate(who):
        t = 100.0 + i
        a, b = int(i * SR), int((i + 1) * SR)
        mine[a:b] = tone(1.0 if w == "H" else 0.25, 1)          # the other person is quieter on your mic
        ta, tb = int((i - offset) * SR), int((i + 1 - offset) * SR)
        if ta >= 0:
            theirs[ta:tb] = tone(0.8 if w == "T" else 0.1, 1)
        host_lines.append({"start": t, "end": t + 1.0, "text": f"line {i}"})
        if w == "T":
            their_lines.append({"start": t, "end": t + 1.0, "text": f"line {i}"})
    their_lines += [{"start": 100.0 + i, "end": 101.0 + i} for i in (0, 1, 3)]   # enough to know their level
    return who, host_lines, mine, theirs, offset, their_lines


def test_the_teammate_lines_on_the_host_mic_are_found_and_only_those():
    who, host_lines, mine, theirs, offset, their_lines = scene()
    found = loud_elsewhere(host_lines, mine, theirs, 100.0, offset, their_lines, sr=SR)
    assert [int(s - 100) for s, _ in found] == [i for i, w in enumerate(who) if w == "T"]


def test_no_recording_at_that_moment_means_no_claim():
    who, host_lines, mine, theirs, offset, their_lines = scene()
    theirs[:] = 0
    theirs[: int(6 * SR)] = tone(0.8, 6)                        # covers the start only
    found = loud_elsewhere(host_lines, mine, theirs, 100.0, offset, their_lines[:6], sr=SR)
    assert all(s < 107 for s, _ in found)


def test_too_little_speech_to_know_anyones_level_claims_nothing():
    who, host_lines, mine, theirs, offset, their_lines = scene()
    assert loud_elsewhere(host_lines[:3], mine, theirs, 100.0, offset, their_lines, sr=SR) == []


def test_marked_lines_move_to_the_teammate_or_go_when_their_track_has_them():
    own = [{"start": 1.0, "end": 2.0, "text": "watch the stairs", "speaker": "Host", "source": "self"},
           {"start": 5.0, "end": 6.0, "text": "you could shoot those", "speaker": "Host", "source": "self"},
           {"start": 9.0, "end": 10.0, "text": "nice", "speaker": "Host", "source": "self"}]
    voice = [{"start": 5.1, "end": 6.2, "text": "Hector, you could shoot those", "speaker": "Zed", "username": "zed_p"}]
    out, moved, dropped = reassign_bleed(own, {"zed_p": [[1.0, 2.0], [5.0, 6.0]]}, voice)
    assert (moved, dropped) == (1, 1)
    assert [(u["text"], u["speaker"]) for u in out] == [("watch the stairs", "Zed"), ("nice", "Host")]
    assert out[0]["source"] == "voice" and out[0]["via"] == "host mic"
