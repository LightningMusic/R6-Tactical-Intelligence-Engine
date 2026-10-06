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
     changes: [marker][entity ref][0 0 0 0] ... [hash 0x4FBDD114][0x04][count u32].
     Records appear in time order, so the series is the gadget's history. Its
     first real value is the starting count, written in the entity's creation
     record (marker 0x1B); a template record of 0 may come before it.
     A second slot, hash 0xF7B590D8, points at the player's SECONDARY gadget
     (frag, claymore, wire...) and is read the same way.
  3. A drop in the counter is one use. Counts can also rise (gadgets that
     recharge, pick-ups), which is why "used" is the sum of the drops, not
     start minus end.

Attackers who never touch their gadget have a flat series, so used == 0 there
is a real zero. A player whose operator has no countable gadget (or whose
record isn't found) is simply absent from the result: "not measured" is never
reported as "not used". Anything unexpected returns {} instead of raising.
"""
from __future__ import annotations

import re
import struct
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, Optional

ZSTD_MAGIC = bytes([0x28, 0xB5, 0x2F, 0xFD])
ID_INDICATOR = bytes([0x33, 0xD8, 0x3D, 0x4F, 0x23])
OWNER_ITEM_HASH = bytes.fromhex("4cd6a0c7")          # 0xC7A0D64C, little-endian: player -> operator-gadget item
SECONDARY_ITEM_HASH = bytes.fromhex("d890b5f7")      # 0xF7B590D8: player -> secondary-gadget item
RECORD_MARKERS = (0x1A, 0x1B, 0x22, 0x23)
PRIMARY_GADGET_COUNT = struct.pack("<I", 0x4FBDD114)  # the count property of either gadget slot's item
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


def operator_gadget_items(data: bytes, handles: dict[str, int],
                          slot_hash: bytes = OWNER_ITEM_HASH) -> dict[str, list[int]]:
    """username -> entity refs of the item(s) in one of their loadout slots: the operator gadget by
    default, the secondary gadget (frag, claymore, wire...) with SECONDARY_ITEM_HASH."""
    by_owner = {h - 7: u for u, h in handles.items()}
    items: dict[str, list[int]] = {}
    i = 0
    while True:
        i = data.find(slot_hash, i)
        if i < 0:
            return items
        if i >= 9 and i + 8 <= len(data) and data[i - 9] in RECORD_MARKERS:
            owner, child = _u32(data, i - 8), _u32(data, i + 4)
            user = by_owner.get(owner)
            if user is not None and child >> 24 >= 0xF0 and child not in items.setdefault(user, []):
                items[user].append(child)
        i += 4


def _record_ref(data: bytes, i: int) -> Optional[int]:
    """The entity whose record the property at `i` belongs to: the nearest record header before it,
    which is [marker][entity ref][four zero bytes]. Insisting on the zero bytes is what keeps a
    stray byte inside some other record from being mistaken for a header."""
    for j in range(i - 5, max(0, i - 128), -1):
        if data[j] in RECORD_MARKERS and data[j + 4] >= 0xF0 and data[j + 5:j + 9] == b"\x00\x00\x00\x00":
            return _u32(data, j + 1)
    return None


def count_series(data: bytes, wanted: set[int]) -> dict[int, list[int]]:
    """entity ref -> its gadget-charge counter values, in stream order. The first value is the
    starting count, written when the entity is created (marker 0x1B/0x1A, a 0 template may precede it)."""
    series: dict[int, list[int]] = {}
    i = 0
    while True:
        i = data.find(PRIMARY_GADGET_COUNT, i)
        if i < 0:
            return series
        if data[i + 4:i + 5] == b"\x04" and i + 9 <= len(data):
            count = _u32(data, i + 5)
            if count <= MAX_COUNT:
                ref = _record_ref(data, i)
                if ref in wanted:
                    series.setdefault(ref, []).append(count)
        i += 4


def _usage(data: bytes, usernames: Iterable[str], slot_hash: bytes) -> dict[str, GadgetUse]:
    handles = find_handles(data, usernames)
    items = operator_gadget_items(data, handles, slot_hash)
    wanted = {ref for refs in items.values() for ref in refs}
    series = count_series(data, wanted) if wanted else {}
    out: dict[str, GadgetUse] = {}
    for user, refs in items.items():
        best: Optional[list[int]] = None
        for ref in refs:
            s = series.get(ref)
            if s and (best is None or len(s) > len(best)):
                best = s
        if best and max(best) > 0:
            out[user] = GadgetUse(start=max(best), used=sum(max(0, a - b) for a, b in zip(best, best[1:])),
                                  series=list(best))
    return out


def gadget_usage(data: bytes, usernames: Iterable[str]) -> dict[str, GadgetUse]:
    """Operator-gadget charges per player (Thorn's shells, Mute's jammers...)."""
    return _usage(data, usernames, OWNER_ITEM_HASH)


def secondary_usage(data: bytes, usernames: Iterable[str]) -> dict[str, GadgetUse]:
    """Secondary-gadget charges per player (frags, stuns, claymores, wire...). Each player's own count
    is read, not the team pool the game also keeps; a death does not change it."""
    return _usage(data, usernames, SECONDARY_ITEM_HASH)


def analyze_rec(path: Path, usernames: Iterable[str]) -> dict[str, GadgetUse]:
    """Never raises: a replay this can't read just yields no utility data."""
    try:
        return gadget_usage(read_rec(path), list(usernames))
    except Exception as e:                                   # noqa: BLE001 - optional enrichment
        print(f"[ReplayUtility] could not read gadget usage from {Path(path).name}: {type(e).__name__}: {str(e)[:80]}")
        return {}


# ── plants and defuses ────────────────────────────────────────────────────
#
# r6-dissect used to read the player out of the defuser packet; replays from
# Y11S3 no longer carry it, so it credits whoever is first in the player list.
# The replay still knows: a plant or defuse is a countdown (7 s down to 0) in
# the defuser-timer packets, and the player doing it has their controller's
# "weapon ready" flag drop to false in the same packet and come back up just
# after the countdown ends. The same idea (and the controller marker below)
# is used by the julio208920 fork of r6-dissect; this is an independent
# implementation, checked on 16 real rounds where exactly one player showed
# that signature in every countdown.
CONTROLLER_INDICATOR = bytes([0x84, 0x1D, 0x24, 0xAB, 0x01, 0x00, 0x00, 0x00, 0x01, 0x00, 0x1B])
READY_FLAG = bytes([0xA4, 0xDC, 0x8D, 0xD4, 0x01])           # hash 0xD48DDCA4, 1-byte value
DEFUSER_TIMER = bytes([0x22, 0xA9, 0xC8, 0x58, 0xD9])        # then a length byte and the countdown text
COMPLETE_BELOW = 0.05                                        # a countdown that reaches this was completed
NEW_COUNTDOWN_JUMP = 0.5                                     # the value rising by this much starts a new attempt
COUNTDOWN_MIN_START = 1.0                                    # a real countdown starts near 7; a lone "0.00" is not one
START_BEFORE, START_AFTER = 256, 1024                        # where, around the first packet, the flag may drop
_COUNTDOWN_TEXT = re.compile(rb"\d{1,3}(\.\d{1,4})?")


@dataclass
class ObjectiveAttempt:
    kind: str                      # "plant" | "defuse"
    completed: bool
    username: Optional[str]        # None when the replay doesn't say clearly enough
    confidence: str                # "clear" | "likely" | "unknown"
    lowest: float                  # the lowest countdown value reached (about 0 when completed)
    offset: int                    # byte position of the countdown: orders attempts within the round


def find_controllers(data: bytes, usernames: Iterable[str]) -> dict[int, str]:
    """controller entity id -> username, from the marker that precedes each player's record."""
    out: dict[int, str] = {}
    head = data[:HANDLE_SEARCH_LIMIT]
    for name in usernames:
        raw = name.encode("utf-8", "ignore")
        if not raw or len(raw) > 255:
            continue
        needle = bytes([len(raw)]) + raw
        i = head.find(needle)
        while i != -1:
            if head.find(ID_INDICATOR, i, i + 600) != -1:
                j = head.rfind(CONTROLLER_INDICATOR, max(0, i - 120), i)
                if j != -1 and j + len(CONTROLLER_INDICATOR) + 4 <= len(head):
                    out[_u32(head, j + len(CONTROLLER_INDICATOR))] = name
                break
            i = head.find(needle, i + 1)
    return out


def ready_states(data: bytes, controllers: dict[int, str]) -> dict[str, list[tuple[int, bool]]]:
    """username -> [(byte offset, weapon ready)] in stream order."""
    out: dict[str, list[tuple[int, bool]]] = {}
    i = data.find(READY_FLAG)
    while i != -1:
        s = i - 8                       # controller id, four zero bytes, then the field hash
        if s >= 1 and data[s - 1] == 0x23 and data[s + 4:s + 8] == b"\0\0\0\0" and i + 5 < len(data):
            who = controllers.get(_u32(data, s))
            if who is not None:
                out.setdefault(who, []).append((i, data[i + 5] != 0))
        i = data.find(READY_FLAG, i + 1)
    return out


def countdowns(data: bytes) -> list[dict]:
    """Every plant/defuse countdown in the round: where it starts and ends and how low it got.
    A value jumping back up means a new attempt (the last one was let go or cut short)."""
    segs: list[dict] = []
    cur: Optional[dict] = None
    last = 0.0
    i = data.find(DEFUSER_TIMER)
    while i != -1:
        n = data[i + 5] if i + 6 <= len(data) else 0
        text = data[i + 6:i + 6 + n]
        if text:
            # plain digits only: float() would also accept "nan" or "inf" if stray bytes ever spelt them
            v = float(text) if _COUNTDOWN_TEXT.fullmatch(text) else None
            if v is not None:
                if cur is None or v > last + NEW_COUNTDOWN_JUMP:
                    if v < COUNTDOWN_MIN_START:
                        v = None                        # a lone "0.00" is not the start of an attempt
                    else:
                        if cur is not None:
                            segs.append(cur)
                        cur = {"start": i, "end": i, "lowest": v}
            if v is not None:
                cur["end"] = i
                cur["lowest"] = min(cur["lowest"], v)
                last = v
        i = data.find(DEFUSER_TIMER, i + 1)
    if cur is not None:
        segs.append(cur)
    return segs


def _interactor(states: dict[str, list[tuple[int, bool]]], roles: dict[str, str], role: str,
                start: int, end: int) -> tuple[Optional[str], str]:
    """Who was planting/defusing in the countdown that ran from `start` to `end`.
    Clear: exactly one player on that side whose flag dropped as it began and was still down when it
    ended. Likely: several such players (the nearest to the start), or one whose flag was already down
    and never changed (a shield held out, say) when nobody else qualifies."""
    dropped: list[tuple[int, str]] = []
    carried: list[str] = []
    for name, side in roles.items():
        if side != role:
            continue
        seq = [(o, ready) for o, ready in states.get(name, []) if o <= end]
        if not seq or seq[-1][1]:
            continue                                    # unknown, or ready again by the end: not it
        in_window = [o for o, ready in seq if not ready and start - START_BEFORE <= o <= start + START_AFTER]
        if in_window:
            dropped.append((min(in_window), name))
        elif all(o < start - START_BEFORE for o, _ in seq):
            carried.append(name)
    if len(dropped) == 1:
        return dropped[0][1], "clear"
    if dropped:
        return min(dropped)[1], "likely"
    if len(carried) == 1:
        return carried[0], "likely"
    return None, "unknown"


def objective_attempts(data: bytes, roles: dict[str, str]) -> list[ObjectiveAttempt]:
    """Every plant and defuse attempt in one round, completed or not, in order.
    `roles` maps each username to "attack" or "defense" for this round."""
    states = ready_states(data, find_controllers(data, roles))
    planted = False
    out: list[ObjectiveAttempt] = []
    for seg in countdowns(data):
        kind = "defuse" if planted else "plant"
        done = seg["lowest"] < COMPLETE_BELOW
        who, how = _interactor(states, roles, "defense" if planted else "attack", seg["start"], seg["end"])
        out.append(ObjectiveAttempt(kind, done, who, how, round(seg["lowest"], 3), seg["start"]))
        if done and kind == "plant":
            planted = True
    return out


def analyze_rec_full(path: Path, roles: dict[str, str]
                     ) -> tuple[dict[str, GadgetUse], dict[str, GadgetUse], Optional[list[ObjectiveAttempt]]]:
    """One read of the replay for everything: (operator gadgets, secondary gadgets, plant/defuse
    attempts). Never raises; a part that can't be read comes back empty ({} / None) so 'not measured'
    is never shown as 'did nothing'."""
    try:
        data = read_rec(path)
    except Exception as e:                                   # noqa: BLE001 - optional enrichment
        print(f"[ReplayUtility] could not read {Path(path).name}: {type(e).__name__}: {str(e)[:80]}")
        return {}, {}, None
    if not data:
        # No readable state stream at all: that is "unknown", not "nobody planted".
        print(f"[ReplayUtility] {Path(path).name} has no readable state stream")
        return {}, {}, None
    usage: dict[str, GadgetUse] = {}
    secondary: dict[str, GadgetUse] = {}
    attempts: Optional[list[ObjectiveAttempt]] = None
    for label, step in (("gadget usage", lambda: usage.update(gadget_usage(data, list(roles)))),
                        ("secondary gadgets", lambda: secondary.update(secondary_usage(data, list(roles))))):
        try:
            step()
        except Exception as e:                               # noqa: BLE001
            print(f"[ReplayUtility] {label} unreadable in {Path(path).name}: {type(e).__name__}: {str(e)[:80]}")
    try:
        attempts = objective_attempts(data, roles)
    except Exception as e:                                   # noqa: BLE001
        print(f"[ReplayUtility] plants/defuses unreadable in {Path(path).name}: {type(e).__name__}: {str(e)[:80]}")
    return usage, secondary, attempts
