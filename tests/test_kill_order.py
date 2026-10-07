"""
r6-dissect's "time" is the clock on screen (time LEFT, and after a plant the 45 s defuser countdown).
Sorting the kill feed by it reversed every round, so "first kill" was really the last kill before the plant,
and a 1v3 clutch read as an opening kill. These are two real rounds of 2026-10-06 (names changed), with the
order the host remembered: teammates fall, then three kills from one player alone, then the clock runs out.
"""
from analysis.event_parser import EventParser

TEAM = {"Me": 0, "A": 0, "B": 0, "C": 0, "D": 0, "Glaz": 1, "Lion": 1, "Buck": 1, "Twitch": 1, "Iana": 1}


def k(clock, elapsed, killer, victim, hs=False):
    return {"type": {"name": "Kill"}, "time": clock, "elapsedSeconds": elapsed, "username": killer,
            "target": victim, "headshot": hs}


ROUND4 = [k("2:25", 79, "Glaz", "A"), k("2:08", 96, "Twitch", "B"), k("1:22", 142, "D", "Twitch"),
          k("1:13", 151, "Glaz", "C"), k("1:11", 153, "Me", "Glaz", True), k("1:03", 161, "Lion", "D"),
          k("0:53", 171, "Me", "Lion", True), k("0:34", 190, "Me", "Buck")]


def test_kills_are_in_the_order_they_happened_not_by_the_clock():
    ev = EventParser(our_team_index=0, our_role="defense").parse(ROUND4, TEAM, "win")
    assert [x.victim for x in ev.kills] == ["A", "B", "Twitch", "C", "Glaz", "D", "Lion", "Buck"]
    assert ev.first_blood_killer == "Glaz" and ev.first_blood_victim == "A"
    assert ev.opening_duel_won is False


def test_last_one_standing_who_wins_is_a_clutch_and_the_kills_after_count():
    ev = EventParser(our_team_index=0, our_role="defense").parse(ROUND4, TEAM, "win")
    assert ev.clutch_player == "Me" and ev.clutch_kill_count == 2       # Lion and Buck, then the clock


def test_a_round_won_alone_without_a_kill_is_still_a_clutch():
    feed = [k("2:00", 100, "Glaz", "A"), k("1:50", 110, "Lion", "B"), k("1:40", 120, "Lion", "C"),
            k("1:30", 130, "Buck", "D"), k("1:20", 140, "D", "Twitch")]
    ev = EventParser(our_team_index=0, our_role="defense").parse(feed, TEAM, "win")
    assert ev.clutch_player == "Me" and ev.clutch_kill_count == 0


def test_kills_after_the_plant_come_after_the_kills_before_it():
    plant = {"type": {"name": "DefuserPlantComplete"}, "time": "0:06", "elapsedSeconds": 218, "username": "Buck"}
    feed = [k("0:12", 212, "Me", "Glaz"), k("0:08", 216, "Buck", "D"), plant,
            k("0:44", 222, "Me", "Buck"), k("0:09", 257, "Lion", "Me")]
    ev = EventParser(our_team_index=0, our_role="defense").parse(feed, TEAM, "loss")
    assert [x.victim for x in ev.kills] == ["Glaz", "D", "Buck", "Me"]   # the clock alone said D, Me, Glaz, Buck
    assert ev.first_blood_killer == "Me" and ev.bomb_planted


def test_without_elapsed_times_the_feed_order_is_kept_and_no_timing_is_claimed():
    feed = [{"type": {"name": "Kill"}, "time": t, "username": a, "target": b}
            for t, a, b in (("2:00", "Glaz", "A"), ("0:30", "Me", "Glaz"))]
    ev = EventParser(our_team_index=0).parse(feed, TEAM, "loss")
    assert [x.victim for x in ev.kills] == ["A", "Glaz"]
    assert ev.first_blood_killer == "Glaz" and ev.first_blood_time is None
