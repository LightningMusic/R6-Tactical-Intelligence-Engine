"""Places learned from player paths (analysis/places.py) and the debrief's positioning facts (team_facts)."""
from types import SimpleNamespace as NS

from analysis import places as PL
from analysis import team_facts as tf


def track(points):
    """[(t, x, y, z)] -> stored samples [t, x, y, z, yaw]."""
    return [[t, x, y, z, None] for t, x, y, z in points]


def still(x, y, z, t0=0, t1=200, step=1.0):
    return track([(t0 + k * step, x, y, z) for k in range(int((t1 - t0) / step) + 1)])


def round_at(site, centre, z, roamer=None):
    cx, cy = centre
    players = {f"D{i}": {"team": 1, "pts": still(cx + i, cy + (i % 2), z)} for i in range(4)}
    players["D4"] = {"team": 1, "pts": still(*(roamer or (cx + 2, cy, z)))}
    players.update({f"A{i}": {"team": 0, "pts": still(cx + 60 + i, cy, 0.0, t0=45)} for i in range(5)})
    return {"site": site, "positions": {"def_team": 1, "players": players, "died": {}}}


ROUNDS = ([round_at("2F Armory Lockers, 2F Archives", (90, -68), 4.2, roamer=(60, 10, 0.0)) for _ in range(4)]
          + [round_at("1F Bathroom, 1F Tellers", (104, -75), 0.0) for _ in range(3)])


def test_floors_are_named_from_the_sites():
    fl = PL.floors(ROUNDS)
    names = {round(z): n for z, n in fl}
    assert names[4] == "2F" and names[0] == "1F"


def test_sites_are_found_where_defenders_set_up_and_places_read_naturally():
    fl = PL.floors(ROUNDS)
    model = PL.site_model(ROUNDS, fl)
    assert abs(model["2F Armory Lockers, 2F Archives"]["x"] - 91.5) < 2
    assert PL.where(model, fl, 91, -68, 4.2) == "2F, at Armory Lockers / Archives"
    assert PL.where(model, fl, 91, -40, 4.2).startswith("2F, 28 m from Armory Lockers / Archives")
    assert PL.where(model, fl, 105, -75, 0.0) == "1F, at Bathroom / Tellers"
    assert PL.area(model, fl, 91, -40, 4.2) == "2F, away from the sites"


def match_of(*outcomes):
    return NS(rounds=[NS(round_number=i + 1, side="defense", outcome=o, site="", player_stats=[]) for i, o in enumerate(outcomes)])


def test_untradeable_deaths_and_the_last_two_fighting_apart():
    ours = {"me", "mate", "third"}
    pos = {1: {"died": {"Third": 50.0, "Me": 70.0, "Mate": 90.0},
               "players": {"Me": {"team": 1, "pts": still(0, 0, 0, t1=70)},
                           "Mate": {"team": 1, "pts": still(25, 0, 0, t1=90)},                 # 25 m away
                           "Third": {"team": 1, "pts": still(3, 0, 0, t1=50)},                 # next to Me
                           "Enemy": {"team": 0, "pts": still(50, 0, 0)}}},
           2: {"died": {"Third": 40.0, "Mate": 44.0},
               "players": {"Me": {"team": 1, "pts": still(0, 0, 0)},
                           "Mate": {"team": 1, "pts": still(2, 0, 4.4, t1=44)},                # right above: another floor
                           "Third": {"team": 1, "pts": still(4, 0, 0, t1=40)}}}}
    p = tf.positioning(match_of("loss", "win"), ours, pos)
    # deaths: R1 Third (Me 3 m away: tradeable), Me (Mate 25 m: not), Mate (the last of us: not counted);
    #         R2 Third (Me 4 m: tradeable), Mate (Me on another floor: not)
    assert (p["deaths"], p["isolated"]) == (4, 2)
    two = {x["round"]: x for x in p["last_two"]}
    assert round(two[1]["median_m"]) == 25 and two[1]["died_apart_s"] == 20
    lines = tf.positioning_lines(p)
    assert "(nobody close enough to trade them): 2 of 4 (50%)." in lines[0]
    assert "It came down to two of us in 2 round(s); in 2 of them the two were more than 10 m apart" in lines[1]
    assert "R01 25 m apart, down 20 s apart" in lines[1] and "R02 on different floors (won)" in lines[1]


def corridor(x0, x1, y, z=0.0, t0=0.0):
    """A walked straight line, 4 samples a second at ~1.5 m/s."""
    n = int(abs(x1 - x0) / 0.375)
    return [[t0 + k * 0.25, x0 + (x1 - x0) * k / n, y, z, None] for k in range(n + 1)]


def walls_between():
    """Two parallel corridors 1 m apart (a wall between), joined only by a doorway at x = 20; plus stairs at
    x = 0 up to a floor 4 m higher."""
    fl = [(0.0, "1F"), (4.0, "2F")]
    wm = PL.WalkMap(fl)
    wm.add_path(corridor(0, 20, 0.0))
    wm.add_path(corridor(0, 20, 1.0))
    wm.add_path([[0, 20, 0.0, 0.0, None], [0.25, 20, 0.33, 0.0, None], [0.5, 20, 0.66, 0.0, None], [0.75, 20, 1.0, 0.0, None]])
    wm.add_path([[0, 0.0, 0.0, 0.0, None], [0.25, 0.5, 0.0, 2.0, None], [0.5, 1.0, 0.0, 4.0, None]])
    wm.add_path(corridor(1.0, 10.0, 0.0, z=4.0))
    return wm


def test_walking_distance_goes_around_walls_and_up_stairs():
    wm = walls_between()
    assert abs(wm.distance((2, 0, 0), (2, 1, 0)) - 37) < 3       # 1 m through the wall, ~37 m via the doorway
    assert wm.distance((18, 0, 0), (18, 1, 0)) < 6                # next to the doorway: a short walk
    assert wm.distance((2, 0, 0), (8, 0, 4.0)) < 15               # up the stairs at x = 0
    assert wm.distance((2, 0, 0), (50, 50, 0)) == float("inf")    # nobody ever walked there


def test_the_last_two_are_judged_by_walking_distance_when_a_walk_map_exists():
    wm = walls_between()
    pos = {1: {"died": {"Third": 10.0, "Me": 20.0, "Mate": 21.0},
               "players": {"Me": {"team": 1, "pts": still(2, 0, 0, t1=20)},
                           "Mate": {"team": 1, "pts": still(2, 1, 0, t1=21)},
                           "Third": {"team": 1, "pts": still(5, 0, 0, t1=10)}}}}
    p = tf.positioning(match_of("loss"), {"me", "mate", "third"}, pos, walk=wm)
    line = tf.positioning_lines(p)[-1]
    assert "in 1 of them the two were a walk of more than 10 m apart" in line
    assert "m to walk between them (only 1 m in a straight line: a wall between)" in line


def test_where_our_players_died_most():
    fl = PL.floors(ROUNDS)
    model = PL.site_model(ROUNDS, fl)
    pos = {1: {"died": {"Me": 10.0, "Mate": 12.0}, "players": {"Me": {"team": 1, "pts": still(91, -68, 4.2, t1=10)},
                                                               "Mate": {"team": 1, "pts": still(92, -69, 4.2, t1=12)}}}}
    assert tf.death_places(match_of("loss"), {"me", "mate"}, pos, model, fl) == ["- Where our players died most: 2F at Armory Lockers / Archives x2."]


ROOMS = [{"name": "CCTV", "floor": "2F", "kind": "room", "x": 91.0, "y": -68.0, "z": 4.2},
         {"name": "Armory Lockers", "floor": "2F", "kind": "site", "x": 150.0, "y": -68.0, "z": 4.2},
         {"name": "Tellers", "floor": "1F", "kind": "site", "x": 104.0, "y": -75.0, "z": 0.0}]


def test_labelled_rooms_name_a_spot_precisely_when_the_map_is_labelled():
    fl = PL.floors(ROUNDS)
    model = PL.site_model(ROUNDS, fl)
    # a death right in CCTV -> the labelled room, not the bomb site
    assert PL.area(model, fl, 91, -68, 4.2, ROOMS) == "2F CCTV"
    assert PL.where(model, fl, 91, -68, 4.2, ROOMS) == "2F CCTV"
    # a room only counts on the same floor: the 2F point ignores the 1F Tellers pin
    assert PL.room_at(ROOMS, fl, 104, -75, 4.2) is None
    assert PL.room_at(ROOMS, fl, 104, -75, 0.0) == "Tellers"
    # far from every labelled room -> falls back to the site model
    assert PL.area(model, fl, 91, -40, 4.2, ROOMS) == "2F, away from the sites"
    # no rooms given -> unchanged behaviour
    assert PL.area(model, fl, 91, -68, 4.2, None) == "2F at Armory Lockers / Archives"


def test_death_places_uses_labelled_rooms_when_present():
    fl = PL.floors(ROUNDS)
    model = PL.site_model(ROUNDS, fl)
    pos = {1: {"died": {"Me": 10.0, "Mate": 12.0}, "players": {"Me": {"team": 1, "pts": still(91, -68, 4.2, t1=10)},
                                                               "Mate": {"team": 1, "pts": still(92, -69, 4.2, t1=12)}}}}
    assert tf.death_places(match_of("loss"), {"me", "mate"}, pos, model, fl, ROOMS) == ["- Where our players died most: 2F CCTV x2."]


def test_yaw_at_takes_the_last_heading_before_the_shot():
    from integration.positions import yaw_at
    pts = [[8.0, 0, 0, 4, 10.0], [9.0, 0, 0, 4, 20.0], [11.0, 0, 0, 4, 200.0]]
    assert yaw_at(pts, 10.0) == 20.0                                   # at-or-before t wins over the later sample
    assert yaw_at([[10.0, 0, 0, 4, None], [9.5, 0, 0, 4, 42.0]], 10.0) == 42.0   # a None yaw is skipped
    assert yaw_at([[0.0, 0, 0, 4, 10.0]], 10.0) is None                # nothing within the window


def _aim(x, y, yaw):
    return [[float(t), x, y, 4.0, yaw] for t in range(0, 20)]


def test_facing_at_death_flags_looking_the_wrong_way(monkeypatch):
    monkeypatch.setattr(tf, "FACING_MIN_DEATHS", 2)
    # Me looks east (yaw 0) but the killer is due north (bearing 90) -> 90 off, wrong way.
    # Mate looks north (yaw 90) straight at the killer due north -> facing, 0 off.
    positions = {1: {"died": {"Me": 10.0, "Mate": 12.0}, "players": {
        "Me":    {"team": 1, "pts": _aim(0, 0, 0.0)},
        "Enemy": {"team": 0, "pts": _aim(0, 10, 0.0)},
        "Mate":  {"team": 1, "pts": _aim(20, 0, 90.0)},
        "Foe":   {"team": 0, "pts": _aim(20, 10, 0.0)},
    }}}
    events = {1: {"kills": [{"t": 10.0, "killer": "Enemy", "victim": "Me"},
                            {"t": 12.0, "killer": "Foe", "victim": "Mate"}]}}
    lines = tf.facing_at_death(match_of("loss"), {"me", "mate"}, positions, events)
    assert lines and "1 of 2 measurable deaths" in lines[0] and "Me 90" in lines[0]


def test_facing_at_death_is_silent_without_enough_measurable_deaths():
    positions = {1: {"players": {"Me": {"team": 1, "pts": _aim(0, 0, 0.0)},
                                 "Enemy": {"team": 0, "pts": _aim(0, 10, 0.0)}}}}
    events = {1: {"kills": [{"t": 10.0, "killer": "Enemy", "victim": "Me"}]}}
    assert tf.facing_at_death(match_of("loss"), {"me"}, positions, events) == []


ROOMS_CALL = [{"name": "Workshop", "floor": "2F", "kind": "room", "x": 10.0, "y": 10.0, "z": 4.0},
              {"name": "Archives", "floor": "2F", "kind": "site", "x": 80.0, "y": 80.0, "z": 4.0},
              {"name": "Break Room", "floor": "2F", "kind": "room", "x": 50.0, "y": 50.0, "z": 4.0}]


def test_called_rooms_matches_distinctive_words_but_not_generic_ones():
    idx = tf._room_index(ROOMS_CALL)
    assert [r["name"] for r in tf._called_rooms("they are workshop", idx)] == ["Workshop"]
    assert tf._called_rooms("break the cam, oh", idx) == []            # 'break' alone is not Break Room
    assert [r["name"] for r in tf._called_rooms("push break room now", idx)] == ["Break Room"]
    assert tf._called_rooms("insecurity about this", idx) == []        # whole-word, so no false substring hit


def _hold(x, y, z, t0=40, t1=120):
    return [[float(t), x, y, z, None] for t in range(t0, t1)]


def test_callout_accuracy_checks_calls_against_enemy_positions(monkeypatch):
    monkeypatch.setattr(tf, "CALLOUT_MIN_CHECKS", 1)
    positions = {1: {"players": {
        "Me": {"team": 1, "pts": _hold(0, 0, 4.0)},
        "EnemyX": {"team": 0, "pts": _hold(10, 10, 4.0)},     # sits in Workshop all round
    }}}
    timeline = {"rounds": [{"round_number": 1, "ours": ["James"], "items": [
        {"kind": "speech", "speaker": "James", "t": 20, "text": "they are workshop"},   # enemy IS there -> match
        {"kind": "speech", "speaker": "James", "t": 30, "text": "one in archives"},     # enemy NOT there -> miss
        {"kind": "speech", "speaker": "James", "t": 35, "text": "break the cam oh"},     # not a room call
        {"kind": "speech", "speaker": "Enemy", "t": 40, "text": "workshop"},            # not our speaker
    ]}]}
    lines = tf.callout_accuracy(timeline, positions, ROOMS_CALL, {"me"})
    assert lines and "1 of 2 checkable room calls" in lines[0] and "James 1/2" in lines[0]


def test_the_report_gets_a_positioning_section_only_when_positions_exist():
    m = NS(rounds=[NS(round_number=1, side="defense", outcome="loss", site="", player_stats=[])])
    facts = tf.build_match_facts(m, {"me"}, {}, {})
    assert "POSITIONING" not in tf.assemble_report("## MATCH SUMMARY\nx", facts, "- y")
    facts["positioning_lines"] = ["- Deaths with no teammate within 10 m: 1 of 1 (100%)."]
    rep = tf.assemble_report("## MATCH SUMMARY\nx", facts, "- y")
    assert rep.index("## ROUND PATTERNS") < rep.index("## POSITIONING") < rep.index("## WHAT TO FOCUS")
