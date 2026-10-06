"""
Plants and defuses read from a replay's defuser countdowns, on synthetic bytes laid out the way
the real stream is (the layout was checked against 16 real Y11S3 rounds, where exactly one player
in every countdown had the weapon-ready signature).
"""
import struct

from integration import replay_utility as ru

CTRL = {"Atk1": 0xF00338B9, "Atk2": 0xF00338BA, "Atk3": 0xF00338BB, "Def1": 0xF003384C, "Def2": 0xF003384B}
ROLES = {"Atk1": "attack", "Atk2": "attack", "Atk3": "attack", "Def1": "defense", "Def2": "defense"}


class Stream:
    def __init__(self):
        self.b = bytearray(b"\x00" * 64)
        for i, (name, ctrl) in enumerate(CTRL.items()):
            raw = name.encode()
            self.b += (ru.CONTROLLER_INDICATOR + struct.pack("<I", ctrl) + b"\x00" * 8
                       + bytes([len(raw)]) + raw + b"\x00" * 10
                       + ru.ID_INDICATOR + struct.pack("<I", 0xF0033800 + i) + b"\x00" * 8)

    def ready(self, name, value):
        self.b += b"\x23" + struct.pack("<I", CTRL[name]) + b"\x00" * 4 + ru.READY_FLAG + bytes([1 if value else 0]) + b"\x00" * 6
        return self

    def timer(self, text):
        raw = text.encode()
        self.b += ru.DEFUSER_TIMER + bytes([len(raw)]) + raw + b"\x00" * 30
        return self

    def idle(self, n=4000):
        self.b += b"\x00" * n
        return self

    def countdown(self, who, lowest=0.003, side_effects=()):
        """A countdown from 7 down to `lowest`; `who`'s flag drops in the first packet and rises a little after."""
        self.timer("6.982")
        if who:
            self.ready(who, False)
        for v in [v for v in (5.0, 3.0, 1.0) if v > lowest] + [lowest]:
            self.idle(2000)             # real countdowns span 20-34 KB, so later packets sit well past the start window
            for name, val in side_effects:
                self.ready(name, val)
            self.timer(f"{v:.3f}")
        if who:
            self.idle(1500).ready(who, True)
        return self

    def read(self):
        return bytes(self.b)


def attempts(stream):
    return ru.objective_attempts(stream.read(), ROLES)


def test_the_player_whose_flag_drops_as_the_countdown_starts_is_the_planter():
    s = Stream().idle().countdown("Atk2")
    [a] = attempts(s)
    assert (a.kind, a.completed, a.username, a.confidence) == ("plant", True, "Atk2", "clear")


def test_other_players_flags_changing_later_in_the_countdown_are_ignored():
    s = Stream().idle().countdown("Atk1", side_effects=[("Atk3", False)])
    [a] = attempts(s)
    assert a.username == "Atk1" and a.confidence == "clear"


def test_a_defender_is_never_credited_with_a_plant():
    s = Stream().idle()
    s.timer("6.982")
    s.ready("Def1", False).ready("Atk3", False)
    s.timer("0.003")
    [a] = attempts(s)
    assert a.username == "Atk3"


def test_a_plant_then_a_defuse_by_a_defender():
    s = Stream().idle().countdown("Atk1").idle(20000).countdown("Def2")
    plant, defuse = attempts(s)
    assert (plant.kind, plant.username) == ("plant", "Atk1")
    assert (defuse.kind, defuse.completed, defuse.username) == ("defuse", True, "Def2")


def test_a_countdown_that_never_finishes_is_an_interrupted_attempt_and_the_next_one_is_a_new_plant():
    s = Stream().idle().countdown("Atk1", lowest=3.66).idle(9000).countdown("Atk3")
    first, second = attempts(s)
    assert (first.kind, first.completed, first.username) == ("plant", False, "Atk1")
    assert first.lowest == 3.66
    assert (second.kind, second.completed, second.username) == ("plant", True, "Atk3")


def test_a_defuse_that_does_not_finish_is_not_a_defuse():
    s = Stream().idle().countdown("Atk1").idle(20000).countdown("Def1", lowest=2.5)
    plant, defuse = attempts(s)
    assert plant.completed and defuse.kind == "defuse" and defuse.completed is False


def test_a_player_whose_flag_was_already_down_and_never_changed_is_the_likely_planter():
    s = Stream().idle().ready("Atk2", False).idle(5000).countdown(None)
    [a] = attempts(s)
    assert (a.username, a.confidence) == ("Atk2", "likely")


def test_two_players_who_both_qualify_give_a_likely_name_not_a_clear_one():
    s = Stream().idle()
    s.timer("6.982")
    s.ready("Atk1", False).idle(100).ready("Atk3", False)
    s.timer("0.003")
    [a] = attempts(s)
    assert a.confidence == "likely" and a.username == "Atk1"


def test_nobody_qualifying_leaves_the_name_blank_instead_of_guessing():
    s = Stream().idle().countdown(None)
    [a] = attempts(s)
    assert (a.username, a.confidence, a.completed) == (None, "unknown", True)


def test_a_flag_that_is_back_up_by_the_end_is_not_the_planter():
    s = Stream().idle()
    s.timer("6.982")
    s.ready("Atk1", False).idle(100).ready("Atk1", True)
    s.timer("0.003")
    [a] = attempts(s)
    assert a.username is None


def test_a_round_with_no_countdown_has_no_attempts():
    assert attempts(Stream().idle().ready("Atk1", False)) == []


def test_a_lone_zero_packet_is_not_an_attempt_but_a_trailing_one_after_a_countdown_adds_nothing():
    assert attempts(Stream().idle().timer("0.000").idle().timer("0.00")) == []
    s = Stream().idle().countdown("Atk1").timer("0.000").timer("0.00")
    [a] = attempts(s)
    assert a.kind == "plant" and a.completed


def test_text_that_is_not_plain_digits_is_ignored():
    s = Stream().idle()
    for text in ("nan", "inf", "1e3", "7.9.1", "-3"):
        s.timer(text)
    assert attempts(s) == []


def test_empty_timer_packets_are_not_countdowns():
    s = Stream().idle()
    for _ in range(5):
        s.b += ru.DEFUSER_TIMER + b"\x00" + b"\x11" * 30
    assert attempts(s) == []


def test_unreadable_replay_gives_nothing_instead_of_raising(tmp_path):
    bad = tmp_path / "x.rec"
    bad.write_bytes(b"not a replay")
    assert ru.analyze_rec_full(bad, ROLES) == ({}, {}, None)
