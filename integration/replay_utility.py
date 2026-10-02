"""
Operator-gadget usage from a .rec replay: how many charges each player had and
how many they used.

r6-dissect does not read this, so this module scans the decompressed replay
itself. It relies on three facts about the replay's state stream (the format
knowledge is documented by the wnc-replay/replay-tool project; this is an
independent implementation, verified against real Y11S3 replays):

  1. Each player has a handle (the 4 bytes after the id indicator in their
     player record). Their own entity sits at handle - 7, and a property
     (hash 0xC7A0D64C) on that entity points at the entity of their OPERATOR
     GADGET item.
  2. The item entity carries a live charge counter, re-sent whenever it
     changes: [0x22|0x23 marker][entity ref] ... [hash 0x4FBDD114][0x04][count u32].
     Records appear in time order, so the series is the gadget's history
     (a template record of 0 near the start of the file is harmless).
  3. A drop in the counter is one use. Counts can also rise (gadgets that
     recharge, pick-ups), which is why "used" is the sum of the drops, not
     start minus end.

Attackers who never touch their gadget have a flat series, so used == 0 there
is a real zero. A player whose operator has no countable gadget (or whose
record isn't found) is simply absent from the result: "not measured" is never
reported as "not used". Anything unexpected returns {} instead of raising.
"""
from __future__ import annotations

import struct
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, Optional

ZSTD_MAGIC = bytes([0x28, 0xB5, 0x2F, 0xFD])
ID_INDICATOR = bytes([0x33, 0xD8, 0x3D, 0x4F, 0x23])
OWNER_ITEM_HASH = bytes.fromhex("4cd6a0c7")          # 0xC7A0D64C, little-endian
PRIMARY_GADGET_COUNT = struct.pack("<I", 0x4FBDD114)
MAX_COUNT = 16
HANDLE_SEARCH_LIMIT = 2_000_000


@dataclass
class GadgetUse:
    start: int                      # the most charges seen at once
    used: int                       # sum of the drops in the counter
    series: list[int] = field(default_factory=list)


def read_rec(path: Path) -> bytes:
    """The decompressed state stream of one round (zstd sections joined)."""
    import zstandard

    raw = Path(path).read_bytes()
    pos = raw.find(ZSTD_MAGIC)
    out: list[bytes] = []
    while 0 <= pos < len(raw):
        dobj = zstandard.ZstdDecompressor().decompressobj()
        try:
            out.append(dobj.decompress(raw[pos:]))
        except zstandard.ZstdError:
            break
        rest = dobj.unused_data
        nxt = rest.find(ZSTD_MAGIC)
        if nxt == -1:
            break
        pos = len(raw) - len(rest) + nxt
    return b"".join(out)


def _u32(data: bytes, o: int) -> int:
    return struct.unpack_from("<I", data, o)[0]


def find_handles(data: bytes, usernames: Iterable[str]) -> dict[str, int]:
    """username -> handle (the 4 bytes after the id indicator in their player record)."""
    handles: dict[str, int] = {}
    head = data[:HANDLE_SEARCH_LIMIT]
    for name in usernames:
        raw = name.encode("utf-8", "ignore")
        if not raw or len(raw) > 255:
            continue
        needle = bytes([len(raw)]) + raw
        i = head.find(needle)
        while i != -1:
            k = head.find(ID_INDICATOR, i, i + 600)
            if k != -1 and k + 9 <= len(head):
                handles[name] = _u32(head, k + 5)
                break
            i = head.find(needle, i + 1)
    return handles


def operator_gadget_items(data: bytes, handles: dict[str, int]) -> dict[str, list[int]]:
    """username -> entity refs of their operator-gadget item(s)."""
    by_owner = {h - 7: u for u, h in handles.items()}
    items: dict[str, list[int]] = {}
    i = 0
    while True:
        i = data.find(OWNER_ITEM_HASH, i)
        if i < 0:
            return items
        if i >= 9 and i + 8 <= len(data) and data[i - 9] in (0x1A, 0x1B, 0x22, 0x23):
            owner, child = _u32(data, i - 8), _u32(data, i + 4)
            user = by_owner.get(owner)
            if user is not None and child >> 24 >= 0xF0 and child not in items.setdefault(user, []):
                items[user].append(child)
        i += 4


def count_series(data: bytes, wanted: set[int]) -> dict[int, list[int]]:
    """entity ref -> its gadget-charge counter values, in stream order."""
    series: dict[int, list[int]] = {}
    i = 0
    while True:
        i = data.find(PRIMARY_GADGET_COUNT, i)
        if i < 0:
            return series
        if data[i + 4:i + 5] == b"\x04" and i + 9 <= len(data):
            count = _u32(data, i + 5)
            if count <= MAX_COUNT:
                for j in range(i - 5, max(0, i - 128), -1):
                    if data[j] in (0x22, 0x23) and data[j + 4] >= 0xF0:
                        ref = _u32(data, j + 1)
                        if ref in wanted:
                            series.setdefault(ref, []).append(count)
                        break
        i += 4


def gadget_usage(data: bytes, usernames: Iterable[str]) -> dict[str, GadgetUse]:
    handles = find_handles(data, usernames)
    items = operator_gadget_items(data, handles)
    wanted = {ref for refs in items.values() for ref in refs}
    series = count_series(data, wanted) if wanted else {}
    out: dict[str, GadgetUse] = {}
    for user, refs in items.items():
        best: Optional[list[int]] = None
        for ref in refs:
            s = series.get(ref)
            if s and (best is None or len(s) > len(best)):
                best = s
        if best:
            out[user] = GadgetUse(start=max(best), used=sum(max(0, a - b) for a, b in zip(best, best[1:])),
                                  series=list(best))
    return out


def analyze_rec(path: Path, usernames: Iterable[str]) -> dict[str, GadgetUse]:
    """Never raises: a replay this can't read just yields no utility data."""
    try:
        return gadget_usage(read_rec(path), list(usernames))
    except Exception as e:                                   # noqa: BLE001 - optional enrichment
        print(f"[ReplayUtility] could not read gadget usage from {Path(path).name}: {type(e).__name__}: {str(e)[:80]}")
        return {}
