"""
Placed-gadget attribution: a gadget is owned by whoever stood next to it when it appeared,
and its type is read off that player's operator. The raw-byte scan is stubbed so the
attribution + typing logic is what's under test.
"""
from integration import gadgets as G


def _positions():
    # two players on the same floor (z~5), one at (0,0), one 20 m away at (20,0)
    return {"players": {
        "Mute_Guy": {"pts": [[8.0, 0.0, 0.0, 5.0, 0.0], [40.0, 0.0, 0.0, 5.0, 0.0]]},
        "Far_Guy":  {"pts": [[8.0, 20.0, 0.0, 5.0, 0.0], [40.0, 20.0, 0.0, 5.0, 0.0]]},
    }}


def test_gadget_goes_to_the_player_it_spawned_next_to_and_is_typed_by_operator(monkeypatch):
    # a placed entity at (1, 0, 5) at t=10 -> Mute_Guy is ~1 m away, Far_Guy ~19 m
    monkeypatch.setattr(G, "placed_entities", lambda data, t_at: [(0xF0001234, 10.0, 1.0, 0.0, 5.0)])
    out = G.decode_gadgets(b"", _positions(), (lambda off: 0.0), {"muteguy": "Mute"})
    assert len(out) == 1
    g = out[0]
    assert g["who"] == "Mute_Guy" and g["op"] == "Mute" and g["type"] == "Jammer"
    assert (g["x"], g["y"], g["z"]) == (1.0, 0.0, 5.0)


def test_a_placement_with_nobody_close_is_left_out(monkeypatch):
    monkeypatch.setattr(G, "placed_entities", lambda data, t_at: [(0xF0005678, 10.0, 50.0, 50.0, 5.0)])
    assert G.decode_gadgets(b"", _positions(), (lambda off: 0.0), {}) == []


def test_a_placement_on_another_floor_does_not_attribute(monkeypatch):
    # right above Mute_Guy in x/y but a floor up (z 9 vs 5) -> beyond FLOOR_M
    monkeypatch.setattr(G, "placed_entities", lambda data, t_at: [(0xF0009999, 10.0, 0.5, 0.0, 9.0)])
    assert G.decode_gadgets(b"", _positions(), (lambda off: 0.0), {"muteguy": "Mute"}) == []


def test_unknown_operator_falls_back_to_the_operator_name(monkeypatch):
    monkeypatch.setattr(G, "placed_entities", lambda data, t_at: [(0xF000aaaa, 10.0, 1.0, 0.0, 5.0)])
    out = G.decode_gadgets(b"", _positions(), (lambda off: 0.0), {"muteguy": "Sentry"})
    assert out[0]["type"] == "Sentry"


def test_none_positions_returns_none():
    assert G.decode_gadgets(b"", None, (lambda off: 0.0), {}) is None
