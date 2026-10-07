"""Operator roles, so the debrief reads 'anchored with traps' instead of 'low kills' for a Lesion night."""
from analysis.siege_roles import READING_GUIDE, role_of, roles_text


def test_common_operators_have_the_role_teams_use_them_for():
    assert role_of("Thermite", "attack") == "hard breach"
    assert role_of("lesion", "defense") == "anchor / trap"
    assert role_of("Bandit", "defense") == "anti-breach"
    assert role_of("Sledge", "attack") == "entry / fragger"           # one answer, the first group that lists it
    assert role_of("Nøkk", "attack") == "flank / pressure"
    assert role_of("Lesion", "attack") == "" and role_of("", "defense") == ""


def test_a_players_night_is_summed_up_by_role():
    text = roles_text([("Lesion", "defense")] * 6 + [("Twitch", "attack")] * 3 + [("Unknown Op", "attack")])
    assert text == "anchor / trap x6 (def), breach support / anti-gadget x3 (att)"


def test_the_guide_tells_the_model_not_to_inflate_small_differences():
    assert "Do not call 86% \"significantly above\" 82%" in READING_GUIDE
    assert len(READING_GUIDE) < 2000                                   # short: an 8B model follows short rules
