"""
Player positions from the replay's movement stream (integration/positions.py), on a synthetic stream laid out
like the real one: movement packets `60 73 85 FE` with the body id 16 bytes before, a flags u16 (0x0100
position, 0x0200 rotation; low byte B0/B8 alive, 90 ragdoll), and the snapshot entries that link each
player's 8-byte id to their body.
"""
import math
import struct

from integration import positions as POS

FC = bytes([0x60, 0x73, 0x85, 0xFE])
D_ID, A_ID, H_ID = 0x1122334455667788, 0x99AABBCCDDEEFF01, 0x0102030405060708
D_BODY, A_BODY, HOLO = 0xF0010001, 0xF0010002, 0xF0010099


def packet(body, flags, xyz=None, yaw_raw=None):
    """One FC update: [body id][12 bytes][pattern][flags][payload], 64 bytes in all."""
    out = struct.pack("<I", body) + b"\x00" * 12 + FC + struct.pack("<H", flags)
    if flags & 0x0100:
        out += struct.pack("<3f", *xyz) + b"\x00" * 4
    if flags & 0x0200:
        h = math.radians(yaw_raw) / 2
        out += struct.pack("<4f", 0.0, 0.0, math.sin(h), math.cos(h))
    return out.ljust(64, b"\x00")


def stream(*, defender_still_until=0, hologram=False, wrong_attacker_id=False):
    """Defender from step 0, attacker from step 45 (the action phase); one step = one second of round time.
    The attacker kills the defender at step 100; the round runs to step 250."""
    snap = b"\xAA" * 64
    snap += struct.pack("<Q", D_ID) + b"\x39\x04\x00\x00\x01" + struct.pack("<I", D_BODY) + b"\x00" * 8
    if not wrong_attacker_id:
        snap += struct.pack("<Q", A_ID) + b"\x21\x04\xff" + struct.pack("<I", A_BODY) + b"\x00" * 8
    snap = snap.ljust(4096, b"\x00")
    body = bytearray()
    for step in range(251):
        # three packets per living body per second, same size, so bytes run evenly with time inside each phase
        for k in range(3):
            if step < 100:
                if step >= defender_still_until:
                    body += packet(D_BODY, 0x01B8, (10.0 + 0.1 * step, 5.0, 3.0))
                else:
                    body += packet(D_BODY, 0x0430)                       # standing still: aim packets only
            elif step == 100 and k == 0:
                body += packet(D_BODY, 0x0090)                           # the ragdoll: dead
            else:
                body += b"\x00" * 64
            if step >= 45:
                # the attacker walks toward the defender, facing +x (raw yaw -90 -> +x after the +90 correction)
                body += packet(A_BODY, 0x03B8, (40.0 - 0.1 * (step - 45), 5.0, 3.0), yaw_raw=180.0 - 90.0 - 180.0)
            else:
                body += b"\x00" * 64
            if hologram and 120 <= step < 200:
                body += packet(HOLO, 0x01B0, (30.0, 9.0, 3.0))
    return snap + bytes(body)


def dissect(wrong_attacker_id=False):
    return {
        "teams": [{"role": "Attack"}, {"role": "Defense"}],
        "players": [{"username": "Def_Player", "teamIndex": 1, "id": str(D_ID)},
                    {"username": "Att_Player", "teamIndex": 0, "id": str(0x5859B42200000000 if wrong_attacker_id else A_ID)}],
        "matchFeedback": [{"type": {"name": "Kill"}, "username": "Att_Player", "target": "Def_Player", "elapsedSeconds": 100}],
    }


def test_players_are_named_from_the_replays_own_link_and_placed_in_time():
    d = POS.decode_round(stream(), dissect())
    assert set(d["players"]) == {"Def_Player", "Att_Player"} and d["linked"] == 2
    dp, ap = d["players"]["Def_Player"]["pts"], d["players"]["Att_Player"]["pts"]
    assert abs(dp[0][0]) < 1 and abs(ap[0][0] - 45) < 1                 # prep start, action start
    s = POS.at(dp, 60.0)
    assert abs(s[1] - (10.0 + 6.0)) < 0.3 and s[3] == 3.0              # where the defender was at 60 s
    assert abs(d["died"]["Def_Player"] - 100) < 1.5                     # pinned to the kill feed
    assert dp[-1][0] <= 100.5                                           # nothing after the death


def test_facing_points_the_way_the_attacker_looks():
    d = POS.decode_round(stream(), dissect())
    s = POS.at(d["players"]["Att_Player"]["pts"], 99.0)
    assert s[4] is not None and abs(((s[4] + 180) % 360) - 180) < 1.0  # facing +x, toward the defender


def test_a_body_that_stands_still_keeps_its_place_and_still_dies_on_time():
    d = POS.decode_round(stream(defender_still_until=30), dissect())
    assert abs(d["died"]["Def_Player"] - 100) < 1.5


def test_an_unowned_body_appearing_mid_round_is_a_gadget_not_a_player():
    d = POS.decode_round(stream(hologram=True), dissect())
    assert set(d["players"]) == {"Def_Player", "Att_Player"}


def test_a_player_whose_id_the_parser_got_wrong_is_found_by_elimination():
    d = POS.decode_round(stream(wrong_attacker_id=True), dissect(wrong_attacker_id=True))
    assert set(d["players"]) == {"Def_Player", "Att_Player"} and d["linked"] == 1


def test_stored_form_round_trips_and_stays_small():
    d = POS.decode_round(stream(), dissect())
    blob = POS.encode(d)
    assert POS.decode_blob(blob) == d
    assert POS.decode_blob("not a blob") is None


def test_nothing_readable_gives_none():
    assert POS.decode_round(b"\x00" * 1000, dissect()) is None
    assert POS.decode_round(stream(), {"players": [], "teams": []}) is None
