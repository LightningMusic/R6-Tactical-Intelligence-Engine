"""The competition gate's classifier (analysis/match_type.py)."""
from analysis import match_type as mt


def test_custom_lobbies_are_competitions():
    for v in ({"name": "CustomGameOnline", "id": 4}, {"name": "CustomGameLocal", "id": 5}, "CustomGameOnline"):
        assert mt.is_custom(v) is True
        assert mt.is_competition(v) is True
        assert mt.label(v).lower().startswith("custom")


def test_matchmaking_is_not_a_competition():
    for v in ({"name": "Ranked", "id": 2}, {"name": "Standard", "id": 1}, "Quick Match"):
        assert mt.is_custom(v) is False
        assert mt.is_competition(v) is False


def test_unknown_match_type_falls_back_to_the_unknown_flag():
    for v in (None, {}, {"id": 4}, "", 123):
        assert mt.is_custom(v) is None
        assert mt.is_competition(v) is True            # default: don't drop a real debrief
        assert mt.is_competition(v, unknown=False) is False
        assert mt.label(v) == "unknown"


def test_label_and_name_read_both_shapes():
    assert mt.match_type_name({"name": "Ranked"}) == "Ranked"
    assert mt.match_type_name("  CustomGameOnline  ") == "CustomGameOnline"
    assert mt.match_type_name(None) is None
