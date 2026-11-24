from types import SimpleNamespace as NS

import pytest

from analysis import round_detail as rd


@pytest.mark.parametrize("s, v", [("1:57", 117), ("0:05", 5), ("2:23", 143), ("x", None), ("", None), (None, None)])
def test_clock_to_secs(s, v):
    assert rd.clock_to_secs(s) == v


OURS = {"zed", "amy"}


def test_plant_timing_rows_and_scramble():
    match = NS(rounds=[NS(round_number=1, outcome="win"), NS(round_number=2, outcome="loss"),
                       NS(round_number=3, outcome="win")])
    events = {
        1: {"planted_by_us": True, "plant_clock": "0:45", "kills": []},                    # clean plant, won
        2: {"planted_by_us": True, "plant_clock": "0:20",                                   # scramble: 2 of us down first
            "kills": [{"victim": "Zed", "t": 80.0}, {"victim": "Amy", "t": 120.0}]},
        3: {"planted_by_us": False},                                                        # no plant: skipped
    }
    rows = rd.plant_timing(match, OURS, events)
    assert [r["round"] for r in rows] == [1, 2]
    assert rows[0] == {"round": 1, "secs_left": 45, "down": 0, "won": True}
    # t_plant for 0:20 = 45 + 180 - 20 = 205; both our deaths (80,120) are before -> down 2
    assert rows[1]["secs_left"] == 20 and rows[1]["down"] == 2 and rows[1]["won"] is False
    lines = rd.plant_timing_lines(rows)
    assert any("post-plant record 1-1" in ln for ln in lines)
    assert any("Scramble plants" in ln and "R02 (down 2)" in ln for ln in lines)


def test_plant_timing_is_empty_when_we_never_planted():
    match = NS(rounds=[NS(round_number=1, outcome="loss")])
    assert rd.plant_timing(match, OURS, {1: {"planted_by_us": False}}) == []
    assert rd.plant_timing_lines([]) == []


def test_opening_places_splits_by_who_died_first(monkeypatch):
    monkeypatch.setattr("integration.positions.at", lambda pts, t: (t, 10.0, 0.0, 0.0))
    monkeypatch.setattr("analysis.places.area", lambda model, fl, x, y, z, rooms: "Blue Stairs")
    match = NS(rounds=[NS(round_number=1), NS(round_number=2)])
    positions = {1: {"players": {"Zed": {"pts": [[0, 0, 0, 0]]}}},
                 2: {"players": {"Enemy1": {"pts": [[0, 0, 0, 0]]}}}}
    events = {
        1: {"first_blood_time": 60.0, "first_blood_victim": "Zed", "first_blood_killer": "Enemy1"},   # we got opened
        2: {"first_blood_time": 55.0, "first_blood_victim": "Enemy1", "first_blood_killer": "Amy"},    # our pick
    }
    lost, won = rd.opening_places(match, OURS, events, positions, {}, [], None)
    assert lost == {"Blue Stairs": 1} and won == {"Blue Stairs": 1}
    lines = rd.opening_place_lines(lost, won)
    assert any("Where we got opened" in ln and "Blue Stairs x1" in ln for ln in lines)
    assert any("Where our opening picks came" in ln for ln in lines)


def test_callout_outcomes():
    timeline = {"rounds": [{"items": [
        {"kind": "speech", "alert": True, "t": 50.0, "text": "two site"},
        {"kind": "Kill", "killer_ours": True, "t": 53.0},          # within 8s of the call -> productive
        {"kind": "speech", "alert": True, "t": 100.0, "text": "one roam"},  # no kill after -> not productive
        {"kind": "callout_then_death", "t": 70.0},                 # late info
    ]}]}
    o = rd.callout_outcomes(timeline)
    assert o == {"alerts": 2, "productive": 1, "late": 1}
    lines = rd.callout_outcome_lines(o)
    assert any("turned into one of our picks" in ln and "1 of 2" in ln for ln in lines)
    assert any("info too late): 1" in ln for ln in lines)


def test_droned_picks():
    match = NS(rounds=[NS(round_number=1)])
    events = {1: {"kills": [{"killer": "Zed", "t": 100.0}, {"killer": "Enemy", "t": 50.0}]}}
    positions = {1: {"drones": [{"t": 92.0, "prep": False}, {"t": 5.0, "prep": True}]}}
    d = rd.droned_picks(match, OURS, events, positions)
    assert d == {"our_kills": 1, "droned": 1}        # Zed's kill at 100 had an action drone out at 92
    assert rd.droned_pick_lines(d) and "1 of 1" in rd.droned_pick_lines(d)[0]


def stat(name, op):
    return NS(player=NS(name=name), operator=NS(name=op))


def test_objective_fit_flags_rounds_without_a_breacher_or_util_clear():
    match = NS(rounds=[
        NS(round_number=1, player_stats=[stat("Zed", "Thermite"), stat("Amy", "Thatcher")]),   # fully equipped
        NS(round_number=2, player_stats=[stat("Zed", "Sledge"), stat("Amy", "Montagne")]),      # no hard breach, no util-clear
        NS(round_number=3, player_stats=[stat("Zed", "Doc")]),                                  # defense: ignored
    ])
    events = {1: {"our_role": "attack"}, 2: {"our_role": "attack"}, 3: {"our_role": "defense"}}
    f = rd.objective_fit(match, OURS, events)
    assert f["attack_rounds"] == 2 and f["no_hard_breach"] == [2] and f["no_util_clear"] == [2]
    lines = rd.objective_fit_lines(f)
    assert any("no hard breacher in 1 of 2" in ln and "R02" in ln for ln in lines)
