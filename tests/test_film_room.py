"""The spatial film-room extraction (analysis/film_room.py): deaths placed in the round from the traces."""
from types import SimpleNamespace as NS

from analysis import film_room as fr


def line(x0, x1, y, z, t0, t1, hz=4):
    n = int((t1 - t0) * hz)
    return [[t0 + k / hz, x0 + (x1 - x0) * k / n, y, z, None] for k in range(n + 1)]


def still(x, y, z, t0=0.0, t1=120.0, hz=1):
    n = int((t1 - t0) * hz)
    return [[t0 + k / hz, x, y, z, None] for k in range(n + 1)]


def _match():
    ops = {"Me": "Frost", "Lo": "Lesion", "Hi": "Mira", "Safe": "Rook"}
    ps = [NS(player=NS(name=nm), operator=NS(name=op)) for nm, op in ops.items()]
    return NS(rounds=[NS(round_number=1, side="defense", site="1F Bar", outcome="loss", player_stats=ps)])


def _positions():
    return {1: {"died": {"Lo": 30.0, "Me": 40.0, "Hi": 50.0},
                "players": {
                    "Me":   {"team": 1, "pts": line(0, 10, 0, 0.0, 28, 40)},    # moving on 1F
                    "Lo":   {"team": 1, "pts": still(5, 5, -3.7, t1=40)},       # basement
                    "Hi":   {"team": 1, "pts": still(50, 50, 4.4, t1=60)},      # 2F, far away
                    "Safe": {"team": 1, "pts": still(11, 0, 0.0)},              # survives, next to Me's death
                    "Baddie": {"team": 0, "pts": still(20, 0, 0.0)}}}}


def _events():
    return {1: {"kill_order": True, "kills": [
        {"victim": "Lo", "killer": "Baddie", "trade": False},
        {"victim": "Me", "killer": "Baddie", "headshot": True, "opening": True, "trade": True},
        {"victim": "Hi", "killer": "Baddie"}]}}                                  # no trade key -> unknown


OURS = {"me", "lo", "hi", "safe"}
DISPLAY = {"me": "Elijah", "safe": "Carter"}


def test_death_film_places_each_death_with_floor_operator_killer_and_nearest_mate():
    film = fr.death_film(_match(), OURS, _positions(), _events(), display=DISPLAY)
    assert film["floors"] == ["1F", "2F", "Basement"]
    me = next(d for d in film["deaths"] if d["op"] == "Frost")
    assert (me["floor"], me["killer"], me["hs"], me["opening"], me["traded"]) == ("1F", "Baddie", True, True, True)
    assert me["mates_alive"] == 2 and me["near_mate"]["who"] == "Carter" and me["near_mate"]["dist_m"] < 2
    assert me["killer_xy"][0] == 20.0 and 1 < len(me["path"]) <= fr.PATH_MAX_PTS
    lo = next(d for d in film["deaths"] if d["op"] == "Lesion")
    assert lo["floor"] == "Basement" and lo["traded"] is False and lo["mates_alive"] == 3
    hi = next(d for d in film["deaths"] if d["op"] == "Mira")
    assert hi["floor"] == "2F" and hi["traded"] is None and hi["mates_alive"] == 1
    assert [d["round"] for d in film["deaths"]] == [1, 1, 1]   # Lo(30) < Me(40) < Hi(50) by time


def test_death_film_can_filter_to_one_player():
    film = fr.death_film(_match(), OURS, _positions(), _events(), player="Me", display=DISPLAY)
    assert film["player"] == "Elijah"
    assert len(film["deaths"]) == 1 and film["deaths"][0]["op"] == "Frost"


def test_death_film_uses_supplied_floor_plan_when_given():
    floors = [(0.0, "Ground"), (4.4, "Up"), (-3.7, "Cellar")]
    film = fr.death_film(_match(), OURS, _positions(), _events(), floors=floors)
    got = {d["op"]: d["floor"] for d in film["deaths"]}
    assert got == {"Frost": "Ground", "Mira": "Up", "Lesion": "Cellar"}


def test_floor_labeler_learns_basement_1f_2f_from_death_heights():
    label = fr.floor_labeler([-3.7, -3.6, 0.0, 0.1, 4.4, 4.3])
    assert (label(-3.7), label(0.0), label(4.4)) == ("Basement", "1F", "2F")
    flat = fr.floor_labeler([0.0, 0.1, -0.1])                # single level, no basement
    assert flat(0.0) == "1F"
