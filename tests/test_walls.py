"""Wall state from a replay (integration/walls.py) and the debrief's SITE SETUP & BREACHES facts (team_facts)."""
import struct
from types import SimpleNamespace as NS

from analysis import places as PL
from analysis import team_facts as tf
from integration import replay_utility as ru
from integration import walls as W
from integration.positions import CLOCK, FC

TEAM = 0xF00CBE2B
CTRL = {"Def1": 0xF00CB187, "Def2": 0xF00CB181, "Att1": 0xF00CB1D4}
DISSECT = {"teams": [{"role": "Attack"}, {"role": "Defense"}],
           "players": [{"username": "Def1", "teamIndex": 1}, {"username": "Def2", "teamIndex": 1},
                       {"username": "Att1", "teamIndex": 0}]}
SECOND = 2000                                    # event-stream bytes per clock tick in these fakes


class Replay:
    def __init__(self):
        self.b = bytearray()
        for i, (name, ctrl) in enumerate(CTRL.items()):
            raw = name.encode()
            self.b += (ru.CONTROLLER_INDICATOR + struct.pack("<I", ctrl) + b"\x00" * 8 + bytes([len(raw)]) + raw
                       + b"\x00" * 10 + ru.ID_INDICATOR + struct.pack("<I", 0xF0033800 + i) + b"\x00" * 8)
        # the snapshot: the defending team created with its pool full
        self.b += b"\x1b" + struct.pack("<I", TEAM) + b"\x00" * 4 + b"\x22" + W.POOL + struct.pack("<I", 10) + b"\x00" * 64
        self.events: dict[int, list[bytes]] = {}

    def at(self, second: float, raw: bytes):
        self.events.setdefault(int(second * SECOND), []).append(raw)
        return self

    def pool(self, second, value):
        return self.at(second, b"\x23" + struct.pack("<I", TEAM) + b"\x00" * 4 + W.POOL + struct.pack("<I", value) + b"\x00" * 8)

    def ready(self, second, name, value):
        return self.at(second, b"\x23" + struct.pack("<I", CTRL[name]) + b"\x00" * 4 + ru.READY_FLAG
                       + bytes([1 if value else 0]) + b"\x00" * 6)

    def build(self, seconds=60) -> bytes:
        out = bytearray(self.b)
        stream = bytearray(b"\x00" * (seconds * SECOND))
        for s in range(seconds):                                   # the round clock, one tick a second
            tick = CLOCK + b"\x04" + struct.pack("<I", 45 - s if s < 45 else 225 - s)
            stream[s * SECOND:s * SECOND + len(tick)] = tick
        for off, raws in sorted(self.events.items()):
            o = off + 64
            for raw in raws:
                stream[o:o + len(raw)] = raw
                o += len(raw) + 16
        return bytes(out + stream)


def positions_for(**spots):
    """Def players standing still facing +x at the given (x, y, z) from 0 s."""
    return {"players": {n: {"team": 1, "pts": [[0.0, x, y, z, 0.0]]} for n, (x, y, z) in spots.items()}}


def test_reinforcements_are_counted_named_and_placed():
    rp = (Replay()
          .pool(3, 9).ready(3.1, "Def1", False).ready(8.2, "Def1", True)         # 5 s away: Def1 reinforced
          .pool(10, 8).ready(10.1, "Def2", False).ready(15.3, "Def2", True)      # Def2 reinforced
          .pool(20, 7).ready(20.1, "Def2", False).ready(21.0, "Def2", True)      # 1 s: cancelled, not a reinforcement
          .pool(21, 8)
          .pool(50, 7).ready(50.2, "Def1", False).ready(55.4, "Def1", True))     # after prep
    w = W.decode_walls(rp.build(), DISSECT, positions_for(Def1=(10.0, 0.0, 4.0), Def2=(20.0, 5.0, 4.0)), None)
    assert (w["start"], w["end"]) == (10, 7)
    got = [(round(r["t"]), r["who"], r["x"], r["y"]) for r in w["reinforcements"]]
    assert got == [(3, "Def1", 10.8, 0.0), (10, "Def2", 20.8, 5.0), (20, None, None, None), (50, "Def1", 10.8, 0.0)]
    assert w["breaches"] == []


def test_no_pool_means_no_wall_data():
    assert W.decode_walls(b"\x00" * 5000, DISSECT, None, None) is None


def surface(oid, x, y, z):
    pkt = bytearray(64)
    pkt[0:4] = W.OBJECT
    struct.pack_into("<Q", pkt, 4, oid)
    struct.pack_into("<3f", pkt, 16, x, y, z)
    struct.pack_into("<4f", pkt, 28, 0.0, 0.0, 0.0, 1.0)
    struct.pack_into("<I", pkt, 57, W.SURFACE)
    return bytes(pkt)


def hit(oid, cause, res, n=W.EXPLOSIVE):
    pkt = bytearray(12 + 200)
    struct.pack_into("<QI", pkt, 0, oid, 0x9E)
    pkt[12:16] = FC
    struct.pack_into("<H", pkt, 16, 0x0140)
    struct.pack_into("<I", pkt, 40, cause)
    struct.pack_into("<QI", pkt, 56, res, n)
    return bytes(pkt)


def test_breaches_are_named_by_their_gadget_and_placed_on_the_wall():
    data = (surface(0x6159D69736, 48.3, -12.8, 36.7) + surface(0x6159D69735, 48.3, -14.6, 36.7)
            + b"\x00" * 300 + hit(0x6159D69736, 0xF00FE001, 0x094688197C) + hit(0x6159D69735, 0xF00FE001, 0x094688197C)
            + hit(0x6159D69736, 0xF00FE002, 0x0BADBADBAD) + b"\x00" * 300)
    known, other = W.breaches(data, lambda off: 92.9)
    assert len(known) == 1
    b = known[0]
    assert (b["op"], b["kind"], b["objects"], b["t"]) == ("Thermite", "hard", 2, 92.9)
    assert abs(b["x"] - 48.3) < 0.01 and abs(b["y"] + 13.7) < 0.01
    assert other == [["BADBADBAD", 92.9, 48.3, -12.8, 36.7, 1]]      # kept so a new gadget can be named later


def test_clock_reads_like_the_screen():
    assert W.clock_left(20) == "20 s into prep"
    assert W.clock_left(45 + 49) == "2:11 left"


# ── the debrief's facts ──────────────────────────────────────────────────

def walls(reinf, breaches=(), start=10):
    return {"start": start, "end": start - len(reinf),
            "reinforcements": [{"t": t, "who": who, "x": x, "y": y, "z": 4.0} for t, who, x, y in reinf],
            "breaches": [{"t": t, "op": op, "kind": "hard", "x": x, "y": y, "z": 4.0, "objects": 3} for t, op, x, y in breaches]}


def stat(name, op):
    return NS(player=NS(name=name), operator=NS(name=op))


def test_setup_lines_for_defense_and_attack():
    ours = {"me", "mate"}
    usual = {"2F Armory Lockers, 2F Archives": {"rounds": 4, "walls": [
        {"x": 10.0, "y": 0.0, "z": 4.0, "rounds": 4}, {"x": 20.0, "y": 0.0, "z": 4.0, "rounds": 3},
        {"x": 30.0, "y": 0.0, "z": 4.0, "rounds": 1}]}}
    match = NS(rounds=[
        NS(round_number=1, side="defense", site="2F Armory Lockers, 2F Archives", outcome="loss",
           player_stats=[stat("Me", "Bandit"), stat("Them", "Thermite")]),
        NS(round_number=2, side="attack", site="1F Bathroom, 1F Tellers", outcome="win",
           player_stats=[stat("Me", "Thermite"), stat("Mate", "Hibana")]),
    ])
    pos = {1: {"walls": walls([(5, "Me", 10.2, 0.1), (12, "Mate", 40.0, 0.0), (50, None, None, None)],
                              breaches=[(94, "Thermite", 10.5, 0.3)])},
           2: {"walls": walls([(4, "Them", 70.0, 0.0)], breaches=[(80, "Thermite", 70.4, 0.0)])}}
    s = tf.setup(match, ours, pos, usual)
    lines = tf.setup_lines(s, {"me": "Alex", "mate": "Sam"})
    text = "\n".join(lines)
    assert "- Reinforcements on defense: 3 of 10 used over 1 round(s)" in text
    assert "1 went up after the action had started: R01." in text
    assert "- Who put them up: Alex 1, Sam 1 (1 not clear from the replay)." in text
    assert "left open: R01 (Armory Lockers / Archives) 1 of 2." in text          # the 20 m wall; 30 m is not usual
    assert "went through walls we reinforced in 1 of 1 defense round(s): R01 Thermite at 2:11 left (Alex's wall)" in text
    assert "- Our hard breach: R02 Thermite at 2:25 left on reinforced walls." in text
    assert "(Hibana: charges are not read from the replay yet.)" in text


def test_a_hard_breacher_with_no_charge_going_off():
    match = NS(rounds=[NS(round_number=3, side="defense", site="", outcome="win",
                          player_stats=[stat("Me", "Kaid"), stat("Them", "Ace")])])
    lines = tf.setup_lines(tf.setup(match, {"me"}, {3: {"walls": walls([(5, "Me", 1.0, 1.0)])}}))
    assert any("They had a hard breacher but no charge went off in 1 of 1 such round(s): R03" in l for l in lines)


def test_usual_walls_are_learned_per_site():
    def rnd(*xs):
        return {"site": "S", "positions": {"walls": {"reinforcements": [{"x": x, "y": 0.0, "z": 4.0} for x in xs]}}}
    spots = PL.wall_spots([rnd(10.0, 20.0), rnd(10.3, 20.1), rnd(10.1, 30.0), rnd(9.9)])
    assert spots["S"]["rounds"] == 4
    assert [(round(w["x"]), w["rounds"]) for w in spots["S"]["walls"]] == [(10, 4), (20, 2), (30, 1)]
    assert [round(w["x"]) for w in PL.usual_walls(spots, "S")] == [10, 20]
    assert PL.usual_walls(spots, "other") == []


def test_the_report_gets_the_section_only_with_wall_data():
    m = NS(rounds=[NS(round_number=1, side="defense", site="", outcome="loss", player_stats=[])])
    facts = tf.build_match_facts(m, {"me"}, {}, {})
    assert "SITE SETUP" not in tf.assemble_report("## MATCH SUMMARY\nx", facts, "- y")
    facts["setup_lines"] = ["- Reinforcements on defense: 9 of 10 used over 1 round(s)."]
    rep = tf.assemble_report("## MATCH SUMMARY\nx", facts, "- y")
    assert rep.index("## ROUND PATTERNS") < rep.index("## SITE SETUP & BREACHES") < rep.index("## UTILITY")
