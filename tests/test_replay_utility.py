"""
Gadget usage from a replay's state stream, checked on synthetic bytes laid out
the way the real stream is (verified separately against real Y11S3 replays).
"""
import struct

import pytest

from integration import replay_utility as ru


def build(players, links, counts):
    """players {name: handle}; links [(owner entity, item entity)]; counts [(item entity, count)] in time order."""
    data = bytearray(b"\x00" * 64)
    for name, handle in players.items():
        raw = name.encode()
        data += bytes([len(raw)]) + raw + b"\x00" * 10 + ru.ID_INDICATOR + struct.pack("<I", handle) + b"\x00" * 8
    for owner, item in links:
        data += b"\x1b" + struct.pack("<I", owner) + b"\x00" * 4 + ru.OWNER_ITEM_HASH + struct.pack("<I", item) + b"\x00" * 4 + b"\x55" * 6
    for item, count in counts:
        data += b"\x23" + struct.pack("<I", item) + b"\x00" * 24 + ru.PRIMARY_GADGET_COUNT + b"\x04" + struct.pack("<I", count) + b"\x00" * 8
    return bytes(data)


H_A, H_B, H_C = 0xF00338A7, 0xF00338B6, 0xF00338C9
ITEM_A, ITEM_B, ITEM_C = 0xF002AA39, 0xF0025AC8, 0xF002AD49


def usage(series_by_item, players=None, links=None):
    players = players or {"Alice": H_A, "Bob": H_B, "Cara": H_C}
    links = links if links is not None else [(H_A - 7, ITEM_A), (H_B - 7, ITEM_B), (H_C - 7, ITEM_C)]
    counts = [(item, c) for item, s in series_by_item.items() for c in s]
    # keep each item's records in order, but interleave items like a real stream
    return ru.gadget_usage(build(players, links, counts), list(players))


def test_a_player_who_places_everything_used_every_charge():
    u = usage({ITEM_A: [2, 3, 2, 1, 0]})
    assert (u["Alice"].start, u["Alice"].used) == (3, 3)


def test_a_flat_counter_is_a_real_zero():
    u = usage({ITEM_A: [2]})
    assert (u["Alice"].start, u["Alice"].used) == (2, 0)


def test_a_counter_that_only_rises_means_nothing_was_used():
    assert usage({ITEM_A: [0, 1, 2]})["Alice"].used == 0


def test_a_recharging_gadget_counts_each_drop():
    # drops: 2->1, 1->0, 1->0 = three uses, even though it ends above zero
    assert usage({ITEM_A: [1, 2, 1, 0, 1, 0]})["Alice"].used == 3


def test_players_are_kept_apart():
    u = usage({ITEM_A: [2, 1, 0], ITEM_B: [1, 1], ITEM_C: [3, 2]})
    assert (u["Alice"].used, u["Bob"].used, u["Cara"].used) == (2, 0, 1)


def test_a_player_with_no_gadget_item_is_absent_not_zero():
    u = usage({ITEM_A: [2, 1]}, links=[(H_A - 7, ITEM_A)])
    assert "Alice" in u and "Bob" not in u and "Cara" not in u


def test_a_player_whose_item_has_no_counter_records_is_absent():
    u = usage({ITEM_A: [2, 1]})
    assert "Bob" not in u


def test_junk_counts_are_ignored():
    u = usage({ITEM_A: [2, 24, 1]})            # 24 is not a gadget count
    assert (u["Alice"].start, u["Alice"].used) == (2, 1)


def test_an_unknown_username_is_skipped():
    data = build({"Alice": H_A}, [(H_A - 7, ITEM_A)], [(ITEM_A, 2), (ITEM_A, 1)])
    assert ru.gadget_usage(data, ["Alice", "Nobody"]).keys() == {"Alice"}


def test_the_longest_item_series_wins_when_a_player_has_two_items():
    items = {ITEM_A: [2], 0xF0028BB9: [2, 1, 0]}
    data = build({"Alice": H_A}, [(H_A - 7, ITEM_A), (H_A - 7, 0xF0028BB9)],
                 [(i, c) for i, s in items.items() for c in s])
    assert ru.gadget_usage(data, ["Alice"])["Alice"].used == 2


def test_unreadable_files_yield_nothing_instead_of_raising(tmp_path):
    bad = tmp_path / "x.rec"
    bad.write_bytes(b"not a replay at all")
    assert ru.analyze_rec(bad, ["Alice"]) == {}
    assert ru.analyze_rec(tmp_path / "missing.rec", ["Alice"]) == {}


def test_zstd_sections_with_junk_between_them_are_joined(tmp_path):
    zstd = pytest.importorskip("zstandard")
    c = zstd.ZstdCompressor()
    payload = build({"Alice": H_A}, [(H_A - 7, ITEM_A)], [(ITEM_A, 3), (ITEM_A, 2), (ITEM_A, 0)])
    half = len(payload) // 2
    rec = tmp_path / "round.rec"
    rec.write_bytes(b"dissect\x00" + b"\x01" * 40 + c.compress(payload[:half]) + b"\xAB" * 9 + c.compress(payload[half:]))
    assert ru.read_rec(rec) == payload
    assert ru.analyze_rec(rec, ["Alice"])["Alice"].used == 3
