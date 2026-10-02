"""
The AI debrief is only as good as the facts it is handed. These pin the parts
that were wrong on 2026-10-01: enemies analysed as if they were teammates, a
1.00 K/D called "high", and "0%" man-advantage / utility for everyone.
"""
from types import SimpleNamespace as NS

from analysis import team_facts as tf
from analysis.intel_engine import IntelEngine


def stat(name, k, d, a=0, ew=0, et=0, pid=None):
    return NS(player=NS(name=name, is_team_member=False), player_id=pid or abs(hash(name)) % 10_000,
              kills=k, deaths=d, assists=a, engagements_won=ew, engagements_taken=et)


def rnd(n, side, outcome, stats):
    return NS(round_number=n, side=side, outcome=outcome, player_stats=stats, site="")


def fortress():
    """Last night's Fortress: R1 def L, R2 def W, R3 def W, R4 att W, R5 att L, R6 att L, R7 def W, R8 att W."""
    plan = [(1, "defense", "loss"), (2, "defense", "win"), (3, "defense", "win"), (4, "attack", "win"),
            (5, "attack", "loss"), (6, "attack", "loss"), (7, "defense", "win"), (8, "attack", "win")]
    rounds = []
    for n, side, out in plan:
        rounds.append(rnd(n, side, out, [
            stat("Hector", 1, 1, ew=1, et=2, pid=1), stat("Elijah", 1, 1, ew=1, et=2, pid=2),
            stat("EnemyOne", 2, 0, ew=2, et=2, pid=3), stat("EnemyTwo", 0, 2, ew=0, et=2, pid=4),
        ]))
    return NS(rounds=rounds, opponent_name="Imported", map="Fortress", result="win")


OURS = {"hector", "elijah"}


def test_enemies_are_left_out_of_the_player_table():
    t = tf.player_table(fortress(), OURS)
    assert set(t) == {"Hector", "Elijah"}
    assert t["Hector"]["rounds"] == 8 and t["Hector"]["k"] == 8


def test_without_a_known_team_everyone_is_listed_as_before():
    assert len(tf.player_table(fortress(), None)) == 4


def test_round_patterns_are_counted_in_code():
    text = tf.round_patterns(fortress())
    assert "- Attack: 2-2 (won R04, R08; lost R05, R06)" in text
    assert "- Defense: 3-1 (won R02, R03, R07; lost R01)" in text
    assert "R01 L R02 W R03 W R04 W R05 L R06 L R07 W R08 W" in text
    assert "Longest win streak: 3 (R02-R04)" in text
    assert "Longest losing streak: 2 (R05-R06)" in text
    assert "Sides switched at R04" in text


def test_an_even_kd_is_never_called_high():
    assert tf.band_ratio(1.00, 1.00) == "IN LINE WITH"
    assert tf.band_ratio(1.00, 1.35) == "BELOW"
    assert tf.band_ratio(1.80, 1.00) == "WELL ABOVE"
    assert tf.band_ratio(0.43, 1.00) == "WELL BELOW"


def test_a_player_is_judged_against_their_own_history_and_the_team():
    row = {"name": "Hector", "k": 7, "d": 7, "a": 0, "rounds": 8, "survived": 1, "survival": 0.125,
           "ew": 7, "et": 14, "ewr": 0.5, "kd": 1.0}
    team = {"kd": 1.0, "survival": 0.20, "ewr": 0.5, "players": 4, "k": 24, "d": 24}
    baseline = {"matches": 12, "rounds": 96, "kd": 1.35, "survival": 0.30, "ewr": 0.55}
    facts = tf.player_facts(row, team, baseline)
    assert any(m == "-" and "BELOW their usual 1.35" in t for m, t in facts)
    assert any(m == "=" and "IN LINE WITH the team's 1.00" in t for m, t in facts)
    assert not any("high" in t.lower() for _, t in facts)


def test_without_enough_history_the_team_is_the_yardstick():
    row = {"name": "New", "k": 4, "d": 4, "a": 0, "rounds": 8, "survived": 4, "survival": 0.5,
           "ew": 0, "et": 0, "ewr": None, "kd": 1.0}
    team = {"kd": 1.0, "survival": 0.3, "ewr": None, "players": 4, "k": 16, "d": 16}
    out = " ".join(t for _, t in tf.player_facts(row, team, {"matches": 1, "kd": 2.0, "survival": 0.1, "ewr": None}))
    assert "usual" not in out and "team's" in out


def test_first_kill_conversion_comes_from_the_kill_feed():
    events = {1: {"opening_duel_won": True}, 2: {"opening_duel_won": True}, 3: {"opening_duel_won": False},
              4: {"opening_duel_won": None}}
    m = NS(rounds=[rnd(1, "defense", "win", []), rnd(2, "defense", "loss", []),
                   rnd(3, "defense", "loss", []), rnd(4, "attack", "win", [])])
    s = tf.opening_summary(m, events)
    assert (s["first_kill_rounds"], s["first_kill_wins"], s["conceded_rounds"], s["conceded_wins"]) == (2, 1, 1, 0)
    assert "first kill in 2 rounds and won 1" in tf.opening_lines(s)[0]


def test_replace_section_swaps_only_that_section():
    text = "## MATCH SUMMARY\nWon.\n\n## ROUND PATTERNS\nwrong: R05 was an attack win\n\n## WHAT TO FOCUS ON NEXT\n1. x\n"
    out = tf.replace_section(text, "ROUND PATTERNS", "- Attack: 2-2")
    assert "wrong" not in out and "- Attack: 2-2" in out
    assert "## MATCH SUMMARY\nWon." in out and "## WHAT TO FOCUS ON NEXT\n1. x" in out
    assert "## ROUND PATTERNS" in tf.replace_section("## MATCH SUMMARY\nWon.\n", "ROUND PATTERNS", "- x")


def _engine():
    return IntelEngine.__new__(IntelEngine)       # the prompt builders need no backend


def test_the_match_prompt_lists_only_our_team_and_drops_the_broken_metrics():
    m = fortress()
    ours = tf.ours_set(["Hector", "Elijah"])
    facts = tf.build_match_facts(m, ours, {}, {"hector": "Hector Nick"})
    summary = {s.player_id: {"player": s.player, "kills": 8, "deaths": 8, "assists": 0, "rounds_played": 8,
                             "kd_ratio": 1.0, "engagement_win_rate": 0.5, "survival_rate": 0.1}
               for s in m.rounds[0].player_stats}
    metrics = {"win_rate": 0.625, "attack_win_rate": 0.5, "defense_win_rate": 0.75, "engagement_win_rate": 0.5,
               "man_advantage": 0.0, "clutch_rate": 0.0}
    prompt = _engine()._build_match_prompt(m, metrics, summary, {}, {}, facts=facts, display={"hector": "Hector Nick"})
    assert "EnemyOne" not in prompt and "EnemyTwo" not in prompt
    assert "Hector Nick" in prompt
    assert "Man-advantage conversion" not in prompt and "Clutch rate" not in prompt
    assert 'The opponent is "the opposing team"' in prompt


def test_the_strongest_checked_facts_become_the_strength_and_the_weakness():
    facts = [("+", "K/D 1.20 is ABOVE the team's 1.00 tonight"),
             ("+", "K/D 1.80 is WELL ABOVE their usual 1.00 (over 9 earlier matches)"),
             ("-", "Survival 12% is BELOW the team's 30% tonight"),
             ("=", "Gunfights won 4/8 (50%) is IN LINE WITH the team's 48%")]
    strength, weakness = tf.pick_lines(facts)
    assert strength.startswith("K/D 1.80 is WELL ABOVE their usual")
    assert weakness.startswith("Survival 12% is BELOW")


def test_each_kind_of_weakness_gets_its_own_sensible_drill():
    assert "fallen back" in tf.drill_for("Survival 12% is BELOW their usual 33%")
    assert "crosshair" in tf.drill_for("Gunfights won 1/8 (12%) is WELL BELOW their usual 44%")
    assert "trade" in tf.drill_for("Opening kills: got the first kill in 0 round(s), died first in 3")
    assert "traded" in tf.drill_for("K/D 0.14 is WELL BELOW their usual 0.78")
    assert "aggressive" not in tf.drill_for("K/D 0.14 is WELL BELOW their usual 0.78").lower()
    assert tf.drill_for("Something else").startswith("Replay")


def test_a_neutral_night_is_reported_as_neutral_not_as_a_weakness():
    assert tf.pick_lines([("=", "Survival 25% is IN LINE WITH their usual 15%")]) == (None, None)


def test_the_focus_list_comes_from_the_measured_numbers():
    plan = [(1, "defense", "loss"), (2, "defense", "loss"), (3, "defense", "win"), (4, "attack", "win"),
            (5, "attack", "loss"), (6, "attack", "win"), (7, "defense", "loss"), (8, "attack", "loss")]
    m = NS(rounds=[rnd(n, s, o, []) for n, s, o in plan])
    facts = {"opening": {"first_kill_rounds": 3, "first_kill_wins": 2, "conceded_rounds": 3, "conceded_wins": 0},
             "comms_counts": {"talk_over": 108, "callout_then_death": 13}}
    out = tf.focus_points(m, facts)
    assert "Defense was the weaker side: 1-3 against 2-2 on attack." in out
    assert "they got the first kill in 3 rounds and we won only 0 of them (we won 2 of the 3 where we got it)" in out
    assert "108 moments of two people talking over each other mid-round, and 13 callouts followed by" in out
    assert out.count("\n") == 2                            # three numbered points


def test_the_focus_list_admits_when_nothing_stands_out():
    plan = [(n, "attack" if n > 4 else "defense", "win" if n % 2 else "loss") for n in range(1, 9)]
    m = NS(rounds=[rnd(n, s, o, []) for n, s, o in plan])
    out = tf.focus_points(m, {"opening": {"first_kill_rounds": 0, "first_kill_wins": 0, "conceded_rounds": 0,
                                          "conceded_wins": 0}, "comms_counts": {}})
    assert out.startswith("1. No clear weakness")


def test_utility_is_hidden_when_no_ability_use_was_ever_recorded():
    pdata = {"kills": 3, "deaths": 2, "assists": 1, "rounds_played": 8, "kd_ratio": 1.5, "engagement_win_rate": 0.5,
             "survival_rate": 0.4, "ability_total": 40, "ability_used": 0, "gadget_total": 0, "gadget_used": 0,
             "utility_efficiency": 0.0}
    prompt = _engine()._build_player_prompt(NS(player=NS(name="X")), pdata, 0.5, None)
    assert "Utility Efficiency" not in prompt
