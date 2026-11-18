"""callout_coverage.analyze: was the enemy opening-picker called out first?"""
from analysis.callout_coverage import analyze, operator_map_from_rows


def _kill(t, killer, victim, killer_ours, victim_ours, clock="2:00"):
    return {"kind": "Kill", "t": t, "clock": clock, "killer": killer, "victim": victim,
            "killer_ours": killer_ours, "victim_ours": victim_ours}


def _say(t, speaker, text, alert=False):
    return {"kind": "speech", "t": t, "speaker": speaker, "text": text, "alert": alert}


def _ops(by_round=None):
    """{round: {username: operator}} -> operator_of callable."""
    m = {}
    for rn, d in (by_round or {}).items():
        for u, op in d.items():
            m[(rn, u.lower())] = op
    return lambda rn, u: m.get((rn, u.lower()), "")


def test_named_operator_in_window_counts_as_called():
    rounds = [{"round_number": 1, "items": [
        _say(40.0, "James", "Lion on top of the roof, watch out"),   # 10s before, names the operator
        _kill(50.0, "enemyguy", "Elijah", False, True),              # enemy opening pick
    ]}]
    out = analyze(rounds, _ops({1: {"enemyguy": "Lion"}}))
    f = out["findings"][0]
    assert f["called"] is True and f["caller"] == "James" and f["secs_before"] == 10.0
    assert out["called"] == 1 and out["with_known_operator"] == 1 and out["coverage"] == 1.0


def test_operator_named_too_early_does_not_count():
    rounds = [{"round_number": 1, "items": [
        _say(10.0, "James", "there's a Lion"),      # 40s before -> outside the 20s window
        _kill(50.0, "enemyguy", "Elijah", False, True),
    ]}]
    out = analyze(rounds, _ops({1: {"enemyguy": "Lion"}}))
    assert out["findings"][0]["called"] is False
    assert out["called"] == 0 and out["uncalled_rounds"] == [1]


def test_our_opening_kill_is_not_an_enemy_pick():
    rounds = [{"round_number": 1, "items": [
        _kill(30.0, "Elijah", "enemyguy", True, False),     # WE open-pick first
        _kill(60.0, "enemyguy", "Will", False, True),       # enemy's first kill on us is the trade-back
    ]}]
    out = analyze(rounds, _ops({1: {"enemyguy": "Lion"}}))
    # the opening pick AGAINST us is the enemy kill at t=60, not our t=30 kill
    assert out["findings"][0]["clock"] is not None
    assert out["findings"][0]["killer"] == "enemyguy" and out["findings"][0]["victim"] == "Will"


def test_position_only_callout_is_alert_but_not_named():
    rounds = [{"round_number": 1, "items": [
        _say(45.0, "Hector", "he's on the roof", alert=True),   # position, no operator
        _kill(50.0, "enemyguy", "Elijah", False, True),
    ]}]
    out = analyze(rounds, _ops({1: {"enemyguy": "Lion"}}))
    f = out["findings"][0]
    assert f["called"] is False and f["alert_before"] is True


def test_unknown_operator_excluded_from_coverage():
    rounds = [{"round_number": 1, "items": [_kill(50.0, "ghost", "Elijah", False, True)]}]
    out = analyze(rounds, _ops())      # no operator known for 'ghost'
    assert out["total_opening_picks"] == 1 and out["with_known_operator"] == 0
    assert out["coverage"] is None and "no enemy opening picks" in out["summary"]


def test_repeat_entry_fraggers_are_flagged():
    rounds = [
        {"round_number": 1, "items": [_kill(50.0, "yabo", "Will", False, True)]},
        {"round_number": 2, "items": [_kill(40.0, "yabo", "Carter", False, True)]},
        {"round_number": 3, "items": [_kill(30.0, "other", "Elijah", False, True)]},
    ]
    out = analyze(rounds, _ops({1: {"yabo": "Zofia"}, 2: {"yabo": "Buck"}, 3: {"other": "Ash"}}))
    rep = out["repeat_entry_fraggers"]
    assert rep and rep[0]["killer"] == "yabo" and rep[0]["times"] == 2


def test_operator_map_from_rows_round_trips():
    m = operator_map_from_rows([{"round": 2, "username": "GoodSpicy", "operator": "Lion"}])
    assert m[(2, "goodspicy")] == "Lion"
