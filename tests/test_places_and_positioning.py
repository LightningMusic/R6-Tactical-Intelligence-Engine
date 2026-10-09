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


def test_the_report_gets_a_positioning_section_only_when_positions_exist():
    m = NS(rounds=[NS(round_number=1, side="defense", outcome="loss", site="", player_stats=[])])
    facts = tf.build_match_facts(m, {"me"}, {}, {})
    assert "POSITIONING" not in tf.assemble_report("## MATCH SUMMARY\nx", facts, "- y")
    facts["positioning_lines"] = ["- Deaths with no teammate within 10 m: 1 of 1 (100%)."]
    rep = tf.assemble_report("## MATCH SUMMARY\nx", facts, "- y")
    assert rep.index("## ROUND PATTERNS") < rep.index("## POSITIONING") < rep.index("## WHAT TO FOCUS")
