"""
Objective play, utility and operator picks: the parts of the debrief that matter
more than kills. Cases come from the real 2026-10-01 replays (Fortress, Lair).
"""
from types import SimpleNamespace as NS

from analysis import team_facts as tf
from analysis.event_parser import MIN_DEFUSE_GAP_SEC, EventParser, parse_round_events
from integration.rec_importer import RecImporter


def stat(name, op, start, used, k=1, d=1):
    return NS(player=NS(name=name, is_team_member=False), player_id=abs(hash(name)) % 10_000, kills=k, deaths=d,
              assists=0, engagements_won=k, engagements_taken=k + d,
              operator=NS(name=op), ability_start=start, ability_used=used)


def rnd(n, side, outcome, stats):
    return NS(round_number=n, side=side, outcome=outcome, player_stats=stats, site="")


# ── plants and defuses from the replay's feedback ─────────────────────────

def feedback(*events):
    return [{"type": {"name": t}, "username": "someone_wrong", "time": clock, "elapsedSeconds": el}
            for t, clock, el in events]


def parse(role, *events, team=1):
    data = {"matchFeedback": feedback(*events), "players": [{"username": "someone_wrong", "teamIndex": 0}],
            "teams": [{"role": "Defense" if role == "attack" else "Attack"}, {"role": role.capitalize()}]}
    return parse_round_events(data, our_team_index=team, round_outcome="win")


def test_we_planted_when_we_attacked_even_if_the_replay_credits_an_enemy():
    e = parse("attack", ("DefuserPlantComplete", "0:35", 189))
    assert e.bomb_planted and e.planted_by_us is True
    assert e.plant_completed is True and e.planter_username is None      # the name is not trusted
    assert e.bomb_defused is False


def test_a_defuse_one_second_after_the_plant_is_a_false_positive():
    e = parse("attack", ("DefuserPlantComplete", "0:35", 189), ("DefuserDisableComplete", "0:44", 190))
    assert e.bomb_planted and e.bomb_defused is False            # Fortress R04: we planted and won


def test_a_defuse_long_after_the_plant_is_real():
    e = parse("attack", ("DefuserPlantComplete", "0:00", 224), ("DefuserDisableComplete", "0:15", 254))
    assert e.bomb_defused is True and (254 - 224) >= MIN_DEFUSE_GAP_SEC     # Fortress R05: they defused


def test_on_defense_a_plant_is_theirs_and_a_real_defuse_is_ours():
    e = parse("defense", ("DefuserPlantComplete", "0:44", 219))
    assert e.bomb_planted and e.planted_by_us is False and e.plant_completed is False
    e = parse("defense", ("DefuserPlantComplete", "1:00", 150), ("DefuserDisableComplete", "0:30", 175))
    assert e.bomb_defused and e.defuse_completed is True


def test_a_round_without_a_plant_has_no_objective_events():
    e = parse("attack")
    assert not e.bomb_planted and e.planted_by_us is None and not e.plant_completed


def test_the_objective_fields_are_stored():
    d = parse("attack", ("DefuserPlantComplete", "1:20", 144)).to_dict()
    assert d["bomb_planted"] is True and d["planted_by_us"] is True and d["our_role"] == "attack"
    assert d["plant_clock"] == "1:20"


# ── team-level objective numbers ──────────────────────────────────────────

def test_objective_summary_counts_plants_on_attack_and_defuses_on_defense():
    match = NS(rounds=[rnd(1, "attack", "win", []), rnd(2, "attack", "loss", []), rnd(3, "attack", "loss", []),
                       rnd(4, "attack", "win", []), rnd(5, "defense", "loss", []), rnd(6, "defense", "win", []),
                       rnd(7, "defense", "win", [])])
    events = {1: {"bomb_planted": True, "bomb_defused": False}, 2: {"bomb_planted": True, "bomb_defused": True},
              3: {"bomb_planted": False}, 4: {"bomb_planted": False},
              5: {"bomb_planted": True, "bomb_defused": False}, 6: {"bomb_planted": True, "bomb_defused": True},
              7: {"bomb_planted": False}}
    o = tf.objective_summary(match, events)
    assert (o["planted"], o["planted_won"], o["planted_defused"], o["unplanted_won"]) == (2, 1, 1, 1)
    assert (o["enemy_planted"], o["we_defused"], o["enemy_planted_won"]) == (2, 1, 1)
    text = " ".join(tf.objective_lines(o))
    assert "we planted in 2 of 4 rounds and won 1 of those" in text
    assert "they planted in 2 of 3 rounds" in text and "disabled the defuser in 1" in text
    assert "could not be read from this match's replays" in text      # no countdown data: says so, names nobody


def test_old_matches_without_objective_data_say_nothing_rather_than_guess():
    match = NS(rounds=[rnd(1, "attack", "win", [])])
    assert tf.objective_lines(tf.objective_summary(match, {1: {"opening_duel_won": True}})) == []


# ── who planted / defused, and attempts that did not finish ───────────────

def att(kind, completed, who, op="", ours=None):
    return {"kind": kind, "completed": completed, "username": who, "operator": op, "confidence": "clear", "ours": ours}


def attempts_match():
    """R1/R2 attack, R3-R5 defense. We planted twice (Zed, then Amy) and had one plant cut short; they planted in
    R3 (we started a defuse and finished it) and R4 (we never went for it); R5 they tried and let go."""
    match = NS(rounds=[rnd(1, "attack", "win", []), rnd(2, "attack", "win", []), rnd(3, "defense", "win", []),
                       rnd(4, "defense", "loss", []), rnd(5, "defense", "win", [])])
    events = {
        1: {"bomb_planted": True, "objective_tracked": True, "bomb_defused": False,
            "objective_attempts": [att("plant", False, "Zed", "Ace", True), att("plant", True, "Zed", "Ace", True)]},
        2: {"bomb_planted": True, "objective_tracked": True, "bomb_defused": True,
            "objective_attempts": [att("plant", True, "Amy", "Lion", True), att("defuse", True, "enemy1", "Kaid", False)]},
        3: {"bomb_planted": True, "objective_tracked": True, "bomb_defused": True,
            "objective_attempts": [att("plant", True, "enemy1", "Ash", False), att("defuse", True, "Zed", "Thorn", True)]},
        4: {"bomb_planted": True, "objective_tracked": True, "bomb_defused": False,
            "objective_attempts": [att("plant", True, "enemy2", "Zofia", False)]},
        5: {"bomb_planted": False, "objective_tracked": True,
            "objective_attempts": [att("plant", False, "enemy2", "Zofia", False)]},
    }
    return match, events


def test_objective_summary_reads_who_planted_and_the_attempts_that_did_not_finish():
    match, events = attempts_match()
    o = tf.objective_summary(match, events)
    assert o["attempts_known"] is True
    assert o["plants_by"] == {"zed": [(1, "Ace")], "amy": [(2, "Lion")]}
    assert o["defuses_by"] == {"zed": [(3, "Thorn")]}
    assert (o["plant_cut"], o["enemy_plant_cut"]) == (1, 1)
    assert (o["counter_rounds"], o["we_defused"], o["counter_cut"]) == (1, 1, 0)
    assert (o["their_defuse_rounds"], o["planted_defused"]) == (1, 1)
    assert o["cut_by"] == {"zed": 1}


def test_objective_lines_name_only_the_saved_team_and_say_whether_we_went_for_the_defuse():
    match, events = attempts_match()
    o = tf.objective_summary(match, events)
    text = "\n".join(tf.objective_lines(o, {"zed": "Zander", "amy": "Amy"}, {"zed"}))
    assert "Planted by: Zander (R1 Ace), a teammate outside the saved team list (R2 Lion)." in text
    assert "1 plant attempt of ours started but did not finish" in text
    assert "Counter-defuse: we started a defuse in 1 of those 2 plants and finished it in 1" in text
    assert "Defuser disabled by: Zander (R3 Thorn)" in text
    assert "1 enemy plant attempt did not finish" in text
    assert "They went for the defuse in 1 of the 2 rounds we planted and finished it in 1" in text
    assert "could not be read" not in text


def test_never_starting_a_defuse_is_said_plainly():
    match, events = attempts_match()
    events[3]["objective_attempts"] = [att("plant", True, "enemy1", "Ash", False)]
    events[3]["bomb_defused"] = False
    text = "\n".join(tf.objective_lines(tf.objective_summary(match, events)))
    assert "we started a defuse in 0" not in text
    assert "Counter-defuse: we never started a defuse in the 2 rounds they planted" in text


def test_player_objective_credits_only_our_players():
    match, events = attempts_match()
    po = tf.player_objective(events, {"zed", "amy"})
    assert po["zed"] == {"plants": [(1, "Ace")], "defuses": [(3, "Thorn")], "cut": 1}
    assert po["amy"]["plants"] == [(2, "Lion")]
    assert "enemy1" not in po and "enemy2" not in po
    assert "amy" not in tf.player_objective(events, {"zed"})


def test_planting_and_defusing_become_strengths_that_outrank_a_kd_line():
    row = {"kd": 2.0, "survival": 0.5, "ewr": None, "et": 0, "ew": 0}
    team = {"players": 4, "kd": 1.0, "survival": 0.5, "ewr": None}
    facts = tf.player_facts(row, team, None, objective={"plants": [(1, "Ace"), (4, "Thermite")], "defuses": [(3, "Thorn")], "cut": 0})
    strength, _ = tf.pick_lines(facts)
    assert strength.startswith("Planted the defuser in 2 rounds (R1 Ace, R4 Thermite)")
    assert any(t.startswith("Disabled the enemy defuser in 1 round (R3 Thorn)") for _, t in facts)


def test_not_planting_is_never_called_a_weakness():
    row = {"kd": 1.0, "survival": 0.5, "ewr": None, "et": 0, "ew": 0}
    team = {"players": 4, "kd": 1.0, "survival": 0.5, "ewr": None}
    facts = tf.player_facts(row, team, None, objective={"plants": [], "defuses": [], "cut": 2})
    assert not any(m == "-" for m, _ in facts)


def test_focus_points_flag_a_team_that_rarely_goes_for_the_defuse():
    match = NS(rounds=[rnd(n, "defense", "loss", []) for n in (1, 2, 3)] + [rnd(4, "attack", "win", [])])
    events = {n: {"bomb_planted": True, "objective_tracked": True, "objective_attempts": [att("plant", True, "e", "Ash", False)]}
              for n in (1, 2, 3)}
    events[4] = {"bomb_planted": False, "objective_tracked": True}
    facts = {"objective": tf.objective_summary(match, events), "opening": {"conceded_rounds": 0}}
    pts = tf.focus_points(match, facts)
    assert "we started a defuse in 0" in pts


# ── secondary gadgets (frags, stuns, claymores, wire) ─────────────────────

def sec_events():
    def pd(**kw):
        return {name: {"secondary_start": s, "secondary_used": u} for name, (s, u) in kw.items()}
    return {
        1: {"secondary_tracked": True, "our_role": "defense", "player_derived": pd(Zed=(2, 2), Amy=(1, 0), enemy=(2, 2))},
        2: {"secondary_tracked": True, "our_role": "attack", "player_derived": pd(Zed=(2, 0), Amy=(2, 1), enemy=(1, 1))},
        3: {"secondary_tracked": True, "our_role": "attack", "player_derived": pd(Zed=(0, 0), Amy=(1, 3))},   # Amy re-collected one
        4: {"our_role": "attack", "player_derived": {"Zed": {"kills": 2}}},                                   # not measured
    }


def test_player_secondary_counts_rounds_by_side_and_ignores_the_enemy_and_empty_loadouts():
    sec = tf.player_secondary(sec_events(), {"zed", "amy"})
    assert set(sec) == {"zed", "amy"}
    z, a = sec["zed"], sec["amy"]
    assert (z["rounds"], z["used_rounds"], z["charges_total"], z["charges_used"]) == (2, 1, 4, 2)    # R3 had none, R4 not measured
    assert (z["defense"]["used_rounds"], z["defense"]["rounds"], z["attack"]["used_rounds"], z["attack"]["rounds"]) == (1, 1, 0, 1)
    assert (a["rounds"], a["used_rounds"]) == (3, 2) and a["charges_used"] == 0 + 1 + 1             # R3: capped at the 1 she carried


def test_secondary_lines_cover_only_the_saved_team_and_say_nothing_when_unmeasured():
    sec = tf.player_secondary(sec_events(), {"zed", "amy", "enemy"})
    lines = tf.secondary_lines(tf.secondary_summary(sec, {"zed", "amy"}))
    assert len(lines) == 1 and "used in 3 of 5 player-rounds that carried one (60%)" in lines[0]
    assert "attack 2/3" in lines[0] and "defense 1/2" in lines[0]
    assert tf.secondary_lines(tf.secondary_summary({}, None)) == []
    assert tf.secondary_lines(tf.secondary_summary(tf.player_secondary({4: {"our_role": "attack"}}, None), None)) == []


def test_player_secondary_text_and_a_low_use_focus_point():
    row = tf.player_secondary(sec_events(), {"zed"})["zed"]
    assert tf.secondary_text(row) == ("secondary gadget used in 1 of 2 rounds that had one (attack 0/1, defense 1/1); "
                                      "charges used 2 of 4.")
    match = NS(rounds=[rnd(1, "attack", "win", [])])
    low = {"measured": True, "rounds": 20, "used": 4}
    pts = tf.focus_points(match, {"objective": {"tracked": False}, "secondary": low, "opening": {"conceded_rounds": 0}})
    assert "used in only 4 of 20 player-rounds" in pts
    fine = tf.focus_points(match, {"objective": {"tracked": False}, "secondary": {"measured": True, "rounds": 20, "used": 12},
                                   "opening": {"conceded_rounds": 0}})
    assert "Secondary gadgets" not in fine


# ── folding the replay's countdowns into a round's events ─────────────────

def events_for(role="attack"):
    from models.round_events import RoundEvents
    e = RoundEvents()
    e.our_role = role
    return e


def test_attempts_replace_dissects_guess_about_defuses_and_name_the_planter():
    e = events_for("attack")
    e.bomb_planted, e.bomb_defused = True, True              # dissect's false "disable" a second after the plant
    e.plant_completed = True
    EventParser.apply_objective_attempts(e, [
        {"kind": "plant", "completed": True, "username": "Zed", "operator": "Thermite", "confidence": "clear"}], "attack")
    assert e.objective_tracked and e.bomb_planted and e.planted_by_us is True
    assert e.bomb_defused is False                           # no completed defuse countdown
    assert e.plant_completed and e.planter_username == "Zed"
    assert e.objective_attempts[0]["ours"] is True


def test_on_defense_the_defuse_attempts_are_ours_and_the_plant_is_theirs():
    e = events_for("defense")
    EventParser.apply_objective_attempts(e, [
        {"kind": "plant", "completed": True, "username": "enemy", "operator": "Ash", "confidence": "clear"},
        {"kind": "defuse", "completed": False, "username": "Zed", "operator": "Thorn", "confidence": "clear"}], "defense")
    assert e.bomb_planted and e.planted_by_us is False and not e.plant_completed
    assert e.defuse_attempted and not e.defuse_completed and e.defuser_username is None
    assert [a["ours"] for a in e.objective_attempts] == [False, True]
    d = e.to_dict()
    assert d["objective_tracked"] is True and len(d["objective_attempts"]) == 2


def test_a_defuse_countdown_that_reached_zero_in_a_round_the_attackers_won_was_not_a_defuse():
    # Fortress-style: the defuser is killed in the last instant, the timer reads 0.000, and the attackers score.
    done = [{"kind": "plant", "completed": True, "username": "enemy", "operator": "Ash", "confidence": "clear"},
            {"kind": "defuse", "completed": True, "username": "Zed", "operator": "Thorn", "confidence": "clear"}]
    e = events_for("defense")
    EventParser.apply_objective_attempts(e, done, "defense", "loss")
    assert e.bomb_defused is False and e.defuse_completed is False and e.defuse_attempted is True
    assert [a["completed"] for a in e.objective_attempts] == [True, False]
    # ...and the same countdown stands when the round really was ours
    e = events_for("defense")
    EventParser.apply_objective_attempts(e, done, "defense", "win")
    assert e.bomb_defused is True and e.defuse_completed is True and e.defuser_username == "Zed"
    # as attackers, a "defuse" in a round we lost is real; in a round we won it is not
    e = events_for("attack")
    EventParser.apply_objective_attempts(e, [done[0], {**done[1], "username": "enemy"}], "attack", "win")
    assert e.bomb_defused is False
    e = events_for("attack")
    EventParser.apply_objective_attempts(e, [done[0], {**done[1], "username": "enemy"}], "attack", "loss")
    assert e.bomb_defused is True
    # no known result: the countdown is taken as it reads
    e = events_for("defense")
    EventParser.apply_objective_attempts(e, done, "defense", None)
    assert e.bomb_defused is True


def test_a_round_with_no_attempts_is_tracked_and_has_no_plant():
    e = events_for("attack")
    EventParser.apply_objective_attempts(e, [], "attack")
    assert e.objective_tracked and not e.bomb_planted and not e.plant_attempted


# ── utility ───────────────────────────────────────────────────────────────

def thorn_rounds():
    """Thorn places everything on defense and does nothing useful on attack (as on 2026-10-01)."""
    rs = [rnd(n, "defense", "win", [stat("Zed", "Thorn", 3, 3)]) for n in (1, 2, 3)]
    rs += [rnd(n, "attack", "loss", [stat("Zed", "Thermite", 2, 0)]) for n in (4, 5, 6)]
    return rs


def test_a_players_gadget_use_is_split_by_side():
    t = tf.player_table(NS(rounds=thorn_rounds()), None)
    u = tf.player_utility(t["Zed"])
    assert u["defense"] == {"rounds": 3, "used_rounds": 3, "ops": ["Thorn"]}
    assert u["attack"] == {"rounds": 3, "used_rounds": 0, "ops": ["Thermite"]}
    assert (u["charges_used"], u["charges_total"]) == (9, 15)


def test_passive_gadgets_are_never_called_unused():
    # Sledge's hammer and Montagne's shield have no stock of charges, even if a counter exists.
    rs = [rnd(n, "attack", "loss", [stat("Zed", op, 2, 0)]) for n, op in ((1, "Sledge"), (2, "Montagne"), (3, "Sledge"))]
    assert tf.player_utility(tf.player_table(NS(rounds=rs), None)["Zed"]) is None


def test_the_utility_totals_cover_the_same_players_as_the_lines_under_them():
    rs = [rnd(n, "attack", "loss", [stat("Named", "Thermite", 2, 1), stat("Random", "Ace", 2, 0)]) for n in (1, 2, 3)]
    facts = tf.build_match_facts(NS(rounds=rs), None, {1: {"utility_tracked": True}}, {},
                                 report_players={"named"})
    assert (facts["utility"]["used"], facts["utility"]["total"]) == (3, 3)      # the random is not counted
    assert "used in 3 of 3 operator-rounds" in facts["utility_lines"][0]
    assert not any("Random" in line for line in facts["utility_lines"])


def test_an_operator_with_no_countable_gadget_is_not_judged():
    t = tf.player_table(NS(rounds=[rnd(1, "defense", "win", [stat("Zed", "Warden", 0, 0)])]), None)
    assert tf.player_utility(t["Zed"]) is None


def test_unused_gadgets_are_a_weakness_and_outrank_a_good_kd():
    t = tf.player_table(NS(rounds=thorn_rounds()), None)
    team = tf.team_totals(t)
    facts = tf.player_facts(t["Zed"], team, None, None, utility=tf.player_utility(t["Zed"]))
    strength, weakness = tf.pick_lines(facts)
    assert strength.startswith("Used their gadget in 3 of 3 defense rounds")
    assert weakness.startswith("Used their gadget in only 0 of 3 attack rounds")
    assert "decide where every charge goes" in tf.drill_for(weakness).replace("Before each round ", "").lower() \
        or "where every charge goes" in tf.drill_for(weakness)


def test_kd_is_demoted_below_objective_play():
    strong_kd = ("+", "K/D 2.00 is WELL ABOVE their usual 1.00 (over 9 earlier matches)")
    gadget = ("-", "Used their gadget in only 1 of 4 attack rounds (Thermite)")
    assert tf._weight(*gadget) > tf._weight(*strong_kd)
    assert tf._weight("-", "K/D 0.40 is WELL BELOW the team's 1.00 tonight") < tf._weight("-", "Survival 10% is WELL BELOW the team's 40% tonight")


def test_team_utility_lines_and_gaps():
    match = NS(rounds=thorn_rounds())
    facts = tf.build_match_facts(match, None, {1: {"utility_tracked": True}}, {"zed": "Zander"})
    assert facts["utility"]["measured"] and (facts["utility"]["used"], facts["utility"]["total"]) == (3, 6)
    assert facts["utility"]["gaps"] == [("Zander", "attack", 0, 3)]
    line = " ".join(facts["utility_lines"])
    assert "used in 3 of 6 operator-rounds" in line and "Zander: gadget used in defense 3/3 (Thorn); attack 0/3 (Thermite, )" .replace(", )", ")") in line


def test_utility_is_not_judged_when_it_was_never_measured():
    match = NS(rounds=[rnd(1, "defense", "win", [stat("Zed", "Thorn", 3, 0)])])      # catalog count, nothing read
    assert tf.utility_measured(match, None, {}) is False
    assert tf.utility_measured(match, None, {1: {"utility_tracked": True}}) is True


# ── operators and the assembled report ────────────────────────────────────

def test_attack_composition_reports_hard_breach_coverage():
    rs = [rnd(1, "attack", "win", [stat("A", "Thermite", 2, 1), stat("B", "Ash", 2, 0)]),
          rnd(2, "attack", "loss", [stat("A", "Ace", 2, 0), stat("B", "Ash", 2, 0)]),
          rnd(3, "attack", "loss", [stat("A", "Zofia", 2, 0), stat("B", "Ash", 2, 0)]),
          rnd(4, "defense", "win", [stat("A", "Mute", 3, 3), stat("B", "Jager", 1, 1)])]
    out = " ".join(tf.composition_lines(NS(rounds=rs), None))
    assert "Attack operators: Ash x3" in out
    assert "hard breacher (Thermite, Hibana, Ace or Maverick) was on the team in 2 of 3 attack rounds" in out
    assert "Defense operators:" in out


def test_the_report_is_assembled_in_a_fixed_order_from_code():
    match = NS(rounds=thorn_rounds())
    facts = tf.build_match_facts(match, None, {1: {"utility_tracked": True, "bomb_planted": False}}, {})
    model = "## MATCH SUMMARY\nWe lost 0-3 on attack.\n\n## ROUND PATTERNS\nBOGUS-PATTERN\n\n## COMMUNICATION\nQuiet.\n"
    out = tf.assemble_report(model, facts, tf.focus_points(match, facts))
    heads = [line for line in out.splitlines() if line.startswith("## ")]
    assert heads == ["## MATCH SUMMARY", "## ROUND PATTERNS", "## OBJECTIVE PLAY", "## UTILITY & OPERATORS",
                     "## WHAT TO FOCUS ON NEXT", "## COMMUNICATION"]
    assert "BOGUS-PATTERN" not in out and "We lost 0-3 on attack." in out and "Quiet." in out
    assert "Gadgets left unused: Zed on attack (0/3)" in out


def test_the_focus_list_puts_objective_and_utility_before_comms_and_kills():
    match = NS(rounds=[rnd(n, "attack", "loss", [stat("Zed", "Thermite", 2, 0)]) for n in (1, 2, 3, 4)]
               + [rnd(n, "defense", "win", [stat("Zed", "Thorn", 3, 3)]) for n in (5, 6)])
    events = {n: {"bomb_planted": False, "utility_tracked": True} for n in (1, 2, 3, 4)}
    facts = tf.build_match_facts(match, None, events, {})
    facts["comms_counts"] = {"talk_over": 80, "callout_then_death": 9}
    out = tf.focus_points(match, facts).splitlines()
    assert out[0].startswith("1. Plants: we planted in only 0 of 4 attack rounds")
    assert "Utility" in out[1] or "Gadgets left unused" in out[1]
    assert any("Comms" in line for line in out)


# ── the importer attaches gadget data without ever claiming "not used" for "not measured" ──

def importer_round(usernames, ops=None):
    from models.round_events import RoundEvents
    ev = RoundEvents()
    ev.our_role = "attack"
    ops = ops or {}
    return NS(raw_player_stats=[{"username": u, "side": "attack", "operator": ops.get(u, "")} for u in usernames],
              round_events=ev)


def importer():
    imp = RecImporter.__new__(RecImporter)
    imp._log = lambda m: None
    return imp


def test_gadget_usage_is_attached_per_player_and_zero_means_no_countable_gadget(monkeypatch, tmp_path):
    from integration import replay_utility as ru
    monkeypatch.setattr(ru, "analyze_rec_full", lambda path, roles: (
        {"Zed": ru.GadgetUse(start=3, used=2, series=[3, 2, 1])}, {"Zed": ru.GadgetUse(start=2, used=1, series=[2, 1])}, []))
    rd = importer_round(["Zed", "Warden_Guy"])
    importer()._attach_gadget_usage(tmp_path / "r.rec", rd)
    assert (rd.raw_player_stats[0]["gadget_start"], rd.raw_player_stats[0]["gadget_used"]) == (3, 2)
    assert (rd.raw_player_stats[1]["gadget_start"], rd.raw_player_stats[1]["gadget_used"]) == (0, 0)
    ev = rd.round_events
    assert ev.utility_tracked is True and ev.secondary_tracked is True and ev.objective_tracked is True
    assert ev.player_derived["Zed"] == {"secondary_start": 2, "secondary_used": 1}
    assert ev.player_derived["Warden_Guy"] == {"secondary_start": 0, "secondary_used": 0}    # measured: nothing to use


def test_plant_attempts_are_attached_with_the_operator_each_player_was_on(monkeypatch, tmp_path):
    from integration import replay_utility as ru
    monkeypatch.setattr(ru, "analyze_rec_full", lambda path, roles: ({}, {}, [
        ru.ObjectiveAttempt("plant", False, "Zed", "clear", 3.66, 100),
        ru.ObjectiveAttempt("plant", True, "Amy", "likely", 0.003, 900)]))
    rd = importer_round(["Zed", "Amy"], {"Zed": "Thermite", "Amy": "Montagne"})
    importer()._attach_gadget_usage(tmp_path / "r.rec", rd)
    ev = rd.round_events
    assert ev.objective_tracked and ev.bomb_planted and ev.planter_username == "Amy"
    assert [(a["username"], a["operator"], a["completed"], a["confidence"]) for a in ev.objective_attempts] == [
        ("Zed", "Thermite", False, "clear"), ("Amy", "Montagne", True, "likely")]
    assert ev.utility_tracked is False                      # no gadget data came back: not claimed


def test_an_unreadable_replay_adds_nothing(monkeypatch, tmp_path):
    from integration import replay_utility as ru
    monkeypatch.setattr(ru, "analyze_rec_full", lambda path, roles: ({}, {}, None))
    rd = importer_round(["Zed"])
    importer()._attach_gadget_usage(tmp_path / "r.rec", rd)
    assert rd.raw_player_stats == [{"username": "Zed", "side": "attack", "operator": ""}]
    ev = rd.round_events
    assert not (ev.utility_tracked or ev.secondary_tracked or ev.objective_tracked or ev.player_derived)
