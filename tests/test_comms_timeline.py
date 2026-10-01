"""
The comms timeline puts speech and the kill feed on one clock and flags
moments worth reviewing. These pin the clock arithmetic (replay timestamps
are host-local digits with a bogus "Z"; elapsedSeconds count from the first
prep tick) and each kind of flag.
"""
from datetime import datetime, timezone

import pytest

from analysis import comms_timeline as ct

OFFSET = -5 * 3600          # host is UTC-5 (Central, daylight time)
ROUND_TS = "2026-09-29T18:49:32Z"


def epoch_of_round_start():
    naive = datetime(2026, 9, 29, 18, 49, 32, tzinfo=timezone.utc).timestamp()
    return naive - OFFSET + ct.PREP_OFFSET_SEC


def at(t):
    """Epoch for t seconds after round 1's prep began."""
    return epoch_of_round_start() + t


def rnd(events, number=1, ts=ROUND_TS):
    return {"round_number": number, "timestamp": ts, "recording_username": "LightningMusic6",
            "ours": ["LightningMusic6", "lammtozzz", "Teddy_Dance"],
            "theirs": ["YABO1HAM", "HykoRev"], "events": events}


def kill(t, killer, victim):
    return {"type": "Kill", "username": killer, "target": victim, "headshot": False, "clock": "", "elapsed": t}


def say(t, speaker, text, dur=2.0, source="self"):
    return {"speaker": speaker, "source": source, "start": at(t), "end": at(t + dur), "text": text}


NAMES = {"lightningmusic6": "Elijah", "lammtozzz": "Zander", "teddy_dance": "James"}


def test_replay_stamp_is_local_time_not_utc():
    # 18:49:32 local at UTC-5 is 23:49:32 UTC.
    e = ct.local_stamp_to_epoch(ROUND_TS, OFFSET)
    assert datetime.fromtimestamp(e, tz=timezone.utc).strftime("%H:%M:%S") == "23:49:32"


@pytest.mark.parametrize("t,plant,label", [
    (0, None, "prep 0:45"), (30, None, "prep 0:15"), (45, None, "3:00"),
    (105, None, "2:00"), (200, 150, "PP 0:50"), (-4, None, "-0:04"),
])
def test_clock_labels(t, plant, label):
    assert ct.clock_label(t, plant) == label


def test_speech_and_kills_share_one_clock():
    tl = ct.build_timeline([rnd([kill(80, "YABO1HAM", "lammtozzz")])],
                           [say(70, "Elijah", "he's on the stairs")], OFFSET, NAMES)
    items = tl["rounds"][0]["items"]
    assert [(i["kind"], round(i["t"])) for i in items] == [("speech", 70), ("Kill", 80)]
    assert items[1]["victim"] == "Zander" and items[1]["victim_ours"] is True
    assert items[0]["clock"] == "2:35"


def test_callout_before_a_teammate_dies_is_flagged():
    tl = ct.build_timeline([rnd([kill(80, "YABO1HAM", "lammtozzz")])],
                           [say(72, "Elijah", "watch your flank, he's rotating")], OFFSET, NAMES)
    flags = [f for f in tl["flags"] if f["kind"] == "callout_then_death"]
    assert len(flags) == 1
    assert flags[0]["players"] == ["Elijah", "Zander"]
    assert "died 6s later" in flags[0]["text"]


def test_chatter_without_enemy_info_is_not_a_callout():
    tl = ct.build_timeline([rnd([kill(80, "YABO1HAM", "lammtozzz")])],
                           [say(74, "Elijah", "lol nice")], OFFSET, NAMES)
    assert not [f for f in tl["flags"] if f["kind"] == "callout_then_death"]


def test_silent_death_only_flagged_for_players_with_their_own_track():
    rounds = [rnd([kill(80, "YABO1HAM", "lammtozzz")])]
    untracked = ct.build_timeline(rounds, [], OFFSET, NAMES)
    tracked = ct.build_timeline(rounds, [], OFFSET, NAMES, tracked_usernames=["lammtozzz"])
    assert not [f for f in untracked["flags"] if f["kind"] == "no_death_callout"]
    assert [f["players"] for f in tracked["flags"] if f["kind"] == "no_death_callout"] == [["Zander"]]
    spoke = ct.build_timeline(rounds, [say(82, "Zander", "one on me, he's low", source="voice")],
                              OFFSET, NAMES, tracked_usernames=["lammtozzz"])
    assert not [f for f in spoke["flags"] if f["kind"] == "no_death_callout"]


def test_talking_over_each_other_mid_round():
    tl = ct.build_timeline([rnd([])], [
        say(90, "Elijah", "push now push now", dur=3.0),
        say(91, "Zander", "wait wait he's holding", dur=3.0, source="voice"),
    ], OFFSET, NAMES)
    said = [i for i in tl["rounds"][0]["items"] if i["kind"] == "speech"]
    assert said[0]["over"] == ["Zander"] and said[1]["over"] == ["Elijah"]
    assert [f["players"] for f in tl["flags"] if f["kind"] == "talk_over"] == [["Elijah", "Zander"]]


def test_overlap_during_prep_is_not_flagged():
    tl = ct.build_timeline([rnd([])], [
        say(10, "Elijah", "I'll go Thermite", dur=3.0),
        say(11, "Zander", "I'll drone", dur=3.0, source="voice"),
    ], OFFSET, NAMES)
    assert not [f for f in tl["flags"] if f["kind"] == "talk_over"]


def test_quiet_fight():
    events = [kill(100, "lammtozzz", "YABO1HAM"), kill(104, "HykoRev", "lammtozzz")]
    quiet = ct.build_timeline([rnd(events)], [say(60, "Elijah", "going A")], OFFSET, NAMES)
    loud = ct.build_timeline([rnd(events)], [say(101, "Elijah", "got one")], OFFSET, NAMES)
    assert [f["kind"] for f in quiet["flags"]].count("quiet_fight") == 1
    assert "quiet_fight" not in [f["kind"] for f in loud["flags"]]


def test_speech_goes_to_the_round_it_was_said_in():
    r1 = rnd([], 1, "2026-09-29T18:49:32Z")
    r2 = rnd([], 2, "2026-09-29T18:53:53Z")                    # 261 s later
    tl = ct.build_timeline([r1, r2], [say(100, "Elijah", "a"), say(270, "Elijah", "b")], OFFSET, NAMES)
    assert [[i["text"] for i in r["items"]] for r in tl["rounds"]] == [["a"], ["b"]]
    assert round(tl["rounds"][1]["items"][0]["t"]) == 9


def test_team_track_duplicates_of_a_teammates_own_track_are_dropped():
    team = [say(10, ct.UNKNOWN_TEAM_SPEAKER, "he's in the kitchen", source="team"),
            say(40, ct.UNKNOWN_TEAM_SPEAKER, "rotate B", source="team")]
    own = [say(10.2, "Zander", "he's in the kitchen", dur=1.8, source="voice")]
    kept = ct.drop_team_duplicates(team, own)
    assert [u["text"] for u in kept] == ["rotate B"]


def test_same_words_on_both_tracks_are_an_echo_not_two_people():
    own = [say(10, "Elijah", "Where's the fucking bandit sitting?"),
           say(20, "Elijah", "rotate to B, I'll hold stairs")]
    team = [say(10.3, ct.UNKNOWN_TEAM_SPEAKER, "Where the fuck is bandit sitting?", source="team"),
            say(20.5, ct.UNKNOWN_TEAM_SPEAKER, "ok going", source="team")]
    kept, dropped = ct.drop_echoes(own, team)
    assert dropped == 1 and [u["text"] for u in kept] == ["rotate to B, I'll hold stairs"]


def test_speaker_stats_and_summary():
    tl = ct.build_timeline([rnd([kill(80, "YABO1HAM", "lammtozzz")])], [
        say(70, "Elijah", "he's above you", dur=4.0),
        say(76, "Zander", "ok", dur=1.0, source="voice"),
    ], OFFSET, NAMES)
    stats = {s["speaker"]: s for s in tl["speakers"]}
    assert stats["Elijah"]["share"] == pytest.approx(0.8)
    assert stats["Elijah"]["alerts"] == 1
    text = ct.ai_summary_text(tl)
    assert "Elijah 80%" in text and "enemy info called" in text
    assert "Elijah: he's above you" in ct.transcript_text(tl)
    assert "x YABO1HAM killed Zander" in ct.transcript_text(tl)
