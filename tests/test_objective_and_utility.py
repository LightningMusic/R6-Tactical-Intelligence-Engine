"""
Objective play, utility and operator picks: the parts of the debrief that matter
more than kills. Cases come from the real 2026-10-01 replays (Fortress, Lair).
"""
from types import SimpleNamespace as NS

from analysis import team_facts as tf
from analysis.event_parser import MIN_DEFUSE_GAP_SEC, parse_round_events
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
    assert "they planted in 2 of 3 rounds" in text and "we disabled the defuser in 1" in text
    assert "credits the wrong player" in text


def test_old_matches_without_objective_data_say_nothing_rather_than_guess():
    match = NS(rounds=[rnd(1, "attack", "win", [])])
    assert tf.objective_lines(tf.objective_summary(match, {1: {"opening_duel_won": True}})) == []


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

def importer_round(usernames):
    return NS(raw_player_stats=[{"username": u} for u in usernames], round_events=NS(utility_tracked=False))


def test_gadget_usage_is_attached_per_player_and_zero_means_no_countable_gadget(monkeypatch, tmp_path):
    from integration import replay_utility
    monkeypatch.setattr(replay_utility, "analyze_rec",
                        lambda path, names: {"Zed": replay_utility.GadgetUse(start=3, used=2, series=[3, 2, 1])})
    imp = RecImporter.__new__(RecImporter)
    imp._log = lambda m: None
    rd = importer_round(["Zed", "Warden_Guy"])
    imp._attach_gadget_usage(tmp_path / "r.rec", rd)
    assert rd.raw_player_stats[0] == {"username": "Zed", "gadget_start": 3, "gadget_used": 2}
    assert rd.raw_player_stats[1] == {"username": "Warden_Guy", "gadget_start": 0, "gadget_used": 0}
    assert rd.round_events.utility_tracked is True


def test_an_unreadable_replay_adds_nothing(monkeypatch, tmp_path):
    from integration import replay_utility
    monkeypatch.setattr(replay_utility, "analyze_rec", lambda path, names: {})
    imp = RecImporter.__new__(RecImporter)
    imp._log = lambda m: None
    rd = importer_round(["Zed"])
    imp._attach_gadget_usage(tmp_path / "r.rec", rd)
    assert rd.raw_player_stats == [{"username": "Zed"}] and rd.round_events.utility_tracked is False
