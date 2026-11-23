"""
Plan & commit: did we call a plan in prep, and did we go for the objective?
Detection is tuned against real prep comms, so the examples here are real lines.
"""
import pytest

from analysis import plan_commit as pc


@pytest.mark.parametrize("text, cat", [
    ("I'm gonna hold RAPT", "assignment"),
    ("I'll cover the window out in the hallway for you", "assignment"),
    ("I'm just gonna play upstairs", "assignment"),
    ("they know I'm roaming, which I don't really mind", "assignment"),
    ("I'm gonna go drop myself in basement quick so they can't find me", "assignment"),
    ("Can we reinforce sight as well?", "setup"),                       # "sight" = whisper's "site"
    ("put the reinforceable wall we have upstairs in B site", "setup"),
    ("Can you electric the walls that I'm on so they have to do something", "setup"),
    ("default plant on this one", "objective"),
    ("let's just rush site", "objective"),
])
def test_real_plan_calls_are_detected(text, cat):
    assert pc.classify_line(text) == cat


@pytest.mark.parametrize("text", [
    "check your mic I can't seem to hear you",
    "Echo, actually, if I can survive on Echo, we can win this",
    "games you should play the game",
    "that was dumb of me",
    "",
])
def test_banter_is_not_a_plan(text):
    assert pc.classify_line(text) is None


def test_plan_in_prep_only_reads_prep_speech():
    items = [
        {"kind": "speech", "t": 10.0, "speaker": "James", "text": "I'm gonna hold RAPT"},
        {"kind": "speech", "t": 120.0, "speaker": "James", "text": "let's rush site now"},  # action phase, ignored
        {"kind": "Kill", "t": 5.0, "text": "x"},
    ]
    p = pc.plan_in_prep(items)
    assert p["called"] and p["categories"] == {"assignment"}
    assert p["examples"] == [("James", "I'm gonna hold RAPT")]


OURS = {"a", "b", "c", "d", "e"}
def kills(*victims):
    return {"kills": [{"victim": v} for v in victims]}

def test_attack_states():
    assert pc.round_state({"our_role": "attack", "planted_by_us": True}, OURS)["state"] == "planted"
    assert pc.round_state({"our_role": "attack", "plant_attempted": True}, OURS)["state"] == "attempted"
    # alive (3 dead) and never attempted = the no-commit case
    st = pc.round_state({"our_role": "attack", **kills("a", "b", "c")}, OURS)
    assert st["state"] == "no_commit" and not st["committed"]
    # wiped first
    assert pc.round_state({"our_role": "attack", **kills("a", "b", "c", "d", "e")}, OURS)["state"] == "wiped"

def test_defense_states():
    assert pc.round_state({"our_role": "defense", "bomb_planted": False}, OURS)["state"] == "held"
    assert pc.round_state({"our_role": "defense", "bomb_planted": True, "defuse_completed": True}, OURS)["state"] == "retook"
    st = pc.round_state({"our_role": "defense", "bomb_planted": True, **kills("a", "b")}, OURS)
    assert st["state"] == "no_retake" and not st["committed"]

def test_enemy_kills_dont_count_as_our_deaths():
    st = pc.round_state({"our_role": "attack", **kills("enemy1", "enemy2", "enemy3", "enemy4", "enemy5")}, OURS)
    assert st["deaths"] == 0 and st["state"] == "no_commit"


def test_summary_cross_tab_plan_to_plant():
    events = {
        1: {"our_role": "attack", "planted_by_us": True},                       # planned + planted
        2: {"our_role": "attack", **kills("a", "b")},                           # planned, no commit
        3: {"our_role": "attack", "planted_by_us": True},                       # NOT planned, planted
        4: {"our_role": "defense", "bomb_planted": True, **kills("a", "b")},    # defense no-retake
    }
    timeline = {"rounds": [
        {"round_number": 1, "items": [{"kind": "speech", "t": 5.0, "speaker": "J", "text": "I'll plant, you cover site"}]},
        {"round_number": 2, "items": [{"kind": "speech", "t": 5.0, "speaker": "J", "text": "I'm gonna reinforce upstairs"}]},
        {"round_number": 3, "items": [{"kind": "speech", "t": 5.0, "speaker": "J", "text": "lol what a game"}]},   # no plan
        {"round_number": 4, "items": []},
    ]}
    s = pc.plan_commit_summary(events, timeline, OURS)
    assert s["attack_rounds"] == 3 and s["plan_called"] == 2
    assert s["committed"] == 2 and s["no_commit"] == [2]
    assert (s["called_planted"], s["called_total"]) == (1, 2)      # R1 planned+planted, R2 planned not planted
    assert (s["uncalled_planted"], s["uncalled_total"]) == (1, 1)  # R3 planted without a call
    assert s["no_retake"] == [4]
    lines = pc.plan_commit_lines(s)
    assert any("A plan was called in prep in 2 of 3" in ln for ln in lines)
    assert any("Plan -> plant: we planted 1/2" in ln for ln in lines)
