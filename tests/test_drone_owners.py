"""
The replay's drone-ownership chain, decoded:
    player record --0xDB2E1510--> deployable --0x080AD3AA--> drone
Each property is [type][hash][value]; the owner's username string sits in the
player record. These synthetic records exercise the reader end to end.
"""
import struct

from integration import drone_owners as DO


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
