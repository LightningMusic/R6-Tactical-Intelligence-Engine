"""
Whose drone is it -- from the replay's own ownership link, not proximity.

The in-game replay viewer knows each drone's owner, so the link is in the data.
It is (2026-11-23, reverse-engineered against real Y11-era packages):

    player record  --0xDB2E1510-->  deployable entity  --0x080AD3AA-->  drone

Each attacker has a "deployable" entity; its record points at the thrown drone
by property 0x080AD3AA, and a record that points at the deployable by property
0xDB2E1510 is the player's own entity -- the one that carries their username
string. So: find the drone's deployable, find who owns the deployable, and read
the name off that player record.

This works for PREP drones too (proximity can't place those -- they spawn ~30 m
from anyone). Coverage isn't total: a deployable resolves only when one of the
records that point at it is a name-bearing player record, so callers treat a
missing entry as "unknown", never "nobody's". r6-dissect doesn't expose this.
"""
from __future__ import annotations

import collections
import struct
from typing import Iterable, Optional

RECORD_MARKERS = (0x1A, 0x1B, 0x22, 0x23)
DEPLOYABLE_TO_DRONE = struct.pack("<I", 0x080AD3AA)   # deployable record -> drone (property value)
OWNER_TO_DEPLOYABLE = struct.pack("<I", 0xDB2E1510)   # player record    -> deployable (property value)
_SCAN_BACK = 260                                      # a property can sit this far into its record


def _u32(data: bytes, o: int) -> int:
    return struct.unpack_from("<I", data, o)[0] if 0 <= o and o + 4 <= len(data) else -1


def _record_start(data: bytes, p: int) -> Optional[int]:
    """Offset of the header of the record the byte at p belongs to: the nearest
    [marker][entity ref (high byte >= 0xF0)][00 00 00 00] before p."""
    for q in range(p - 1, max(0, p - _SCAN_BACK), -1):
        if data[q] in RECORD_MARKERS and (_u32(data, q + 1) >> 24) >= 0xF0 and data[q + 5:q + 9] == b"\0\0\0\0":
            return q
    return None


def _record_entity(data: bytes, p: int) -> Optional[int]:
    q = _record_start(data, p)
    return _u32(data, q + 1) if q is not None else None


def _occurrences(data: bytes, needle: bytes):
    i = data.find(needle)
    while i != -1:
        yield i
        i = data.find(needle, i + 1)


def _value_links(data: bytes, hash_bytes: bytes) -> list[tuple[int, int]]:
    """(subject entity, value entity) for every property [hash_bytes][value] whose value
    is an entity ref (high byte >= 0xF0), attributed to the record the hash sits in."""
    links = []
    for p in _occurrences(data, hash_bytes):
        val = _u32(data, p + 4)
        if (val >> 24) < 0xF0:
            continue
        subj = _record_entity(data, p)
        if subj is not None and subj != val:
            links.append((subj, val))
    return links


def deployable_owners(data: bytes, usernames: Iterable[str]) -> dict[int, str]:
    """deployable entity -> username. A record points at the deployable via 0xDB2E1510,
    and the OWNER is the player whose username string sits in that same record -- read it
    locally (header..edge), not from a global name map, so each deployable gets its own
    player instead of clumping onto whoever was named nearby."""
    needles = {}
    for name in usernames:
        raw = (name or "").encode("utf-8", "ignore")
        if raw and len(raw) <= 255:
            needles[name] = bytes([len(raw)]) + raw
    votes: dict[int, collections.Counter] = collections.defaultdict(collections.Counter)
    for p in _occurrences(data, OWNER_TO_DEPLOYABLE):
        deployable = _u32(data, p + 4)
        if (deployable >> 24) < 0xF0:
            continue
        hp = _record_start(data, p)
        if hp is None:
            continue
        span = data[hp:p + 8]                       # just this record's bytes up to the link
        for name, nb in needles.items():
            if nb in span:
                votes[deployable][name] += 1
    return {dep: c.most_common(1)[0][0] for dep, c in votes.items()}


def drone_owner_map(data: bytes, usernames: Iterable[str]) -> dict[int, str]:
    """drone entity -> username. Chains drone <- deployable <- player record.
    Only drones whose owner is resolved appear; absence means "unknown", not "nobody"."""
    dep_owner = deployable_owners(data, usernames)
    if not dep_owner:
        return {}
    out: dict[int, str] = {}
    for deployable, drone in _value_links(data, DEPLOYABLE_TO_DRONE):
        user = dep_owner.get(deployable)
        if user is not None:
            out.setdefault(drone, user)
    return out
