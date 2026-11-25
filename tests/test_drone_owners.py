"""
The replay's drone-ownership chain, decoded:
    player record --0xDB2E1510--> deployable --0x080AD3AA--> drone
Each property is [type][hash][value]; the owner's username string sits in the
player record. These synthetic records exercise the reader end to end.
"""
import struct

from integration import drone_owners as DO
from integration import replay_utility as RU


def ent(n):
    return struct.pack("<I", n)


def player_record(entity, name, deployable):
    # [marker][entity][0000] <name (length-prefixed)> [type 0x1A][0xDB2E1510][deployable]
    return (bytes([0x23]) + ent(entity) + b"\0\0\0\0"
            + bytes([len(name)]) + name.encode()
            + bytes([0x1A]) + ent(0xDB2E1510) + ent(deployable))


def deployable_record(deployable, drone):
    # [marker][deployable][0000] [type 0x3E][0x080AD3AA][drone]
    return (bytes([0x22]) + ent(deployable) + b"\0\0\0\0"
            + bytes([0x3E]) + ent(0x080AD3AA) + ent(drone))


PAD = b"\x00" * 32
# two attackers, each with their own deployable and drone
P1, DEP1, DR1 = 0xF0001001, 0xF0002002, 0xF0003003
P2, DEP2, DR2 = 0xF0001010, 0xF0002020, 0xF0003030
BUF = (PAD + player_record(P1, "Zed", DEP1) + PAD + deployable_record(DEP1, DR1)
       + PAD + player_record(P2, "Amy", DEP2) + PAD + deployable_record(DEP2, DR2) + PAD)


def test_each_drone_resolves_to_its_own_owner():
    owners = DO.drone_owner_map(BUF, ["Zed", "Amy"])
    assert owners == {DR1: "Zed", DR2: "Amy"}


def test_deployables_map_to_the_right_player():
    assert DO.deployable_owners(BUF, ["Zed", "Amy"]) == {DEP1: "Zed", DEP2: "Amy"}


def test_a_drone_with_no_ownership_record_is_absent_not_guessed():
    # a lone deployable->drone with no player pointing at the deployable
    buf = PAD + deployable_record(0xF0009009, 0xF000A00A) + PAD
    assert DO.drone_owner_map(buf, ["Zed", "Amy"]) == {}


def test_unknown_usernames_do_not_match():
    assert DO.drone_owner_map(BUF, ["Someone", "Else"]) == {}


def _controller_block(controller, name):
    """A player record find_controllers recognises: CONTROLLER_INDICATOR + id, then the
    length-prefixed username, with the ID_INDICATOR shortly after it."""
    return (RU.CONTROLLER_INDICATOR + ent(controller) + b"\x00" * 8
            + bytes([len(name)]) + name.encode() + b"\x00" * 4 + RU.ID_INDICATOR + b"\x00" * 8)


def test_owner_resolves_through_the_controller_pawn_link_without_a_name():
    # controller -0x69E42CFB-> pawn -0xDB2E1510-> deployable -0x080AD3AA-> drone, no username in the
    # deployable record -- it must resolve via the controller the pawn belongs to.
    CTRL, PAWN, DEP, DRONE = 0xF00262E2, 0xF002629B, 0xF00661E7, 0xF0067292
    cp = bytes([0x23]) + ent(CTRL) + b"\0\0\0\0" + ent(0x69E42CFB) + ent(PAWN)
    pd = bytes([0x23]) + ent(PAWN) + b"\0\0\0\0" + ent(0xDB2E1510) + ent(DEP)
    dd = bytes([0x22]) + ent(DEP) + b"\0\0\0\0" + ent(0x080AD3AA) + ent(DRONE)
    buf = (b"\x00" * 16 + _controller_block(CTRL, "Vxral6224") + b"\x00" * 16
           + cp + b"\x00" * 16 + pd + b"\x00" * 16 + dd + b"\x00" * 16)
    assert DO.deployable_owners(buf, ["Vxral6224"]) == {DEP: "Vxral6224"}
    assert DO.drone_owner_map(buf, ["Vxral6224"]) == {DRONE: "Vxral6224"}
