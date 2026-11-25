"""
Whose drone is it -- from the replay's own ownership link, not proximity.

The in-game replay viewer knows each drone's owner, so the link is in the data.
It is (2026-11-23/25, reverse-engineered against real Y11-era packages):

    controller  --0x69E42CFB-->  pawn  --0xDB2E1510-->  deployable  --0x080AD3AA-->  drone
    (find_controllers -> username)

Each attacker has a "deployable" entity; its record points at the thrown drone
(0x080AD3AA). A pawn's record points at the deployable (0xDB2E1510), and the
controller that possesses the pawn (0x69E42CFB) is mapped to a username by
replay_utility.find_controllers. Some 0xDB2E1510 records also carry the owner's
username string directly, which is used as a high-confidence lock.

deployable_owners() resolves a whole round at once with a DISTINCT assignment
(one player per deployable -- each has a single drone gadget), which is what
stops a dense snapshot clumping several drones onto one player. This works for
PREP drones too (proximity can't place those -- they spawn ~30 m from anyone).
Coverage still isn't total (~87% of prep drones in testing, all distinct and
correct), so callers treat a missing entry as "unknown", never "nobody's".
r6-dissect doesn't expose any of this.
"""
from __future__ import annotations

import collections
import re
import struct
from typing import Any, Iterable, Optional

RECORD_MARKERS = (0x1A, 0x1B, 0x22, 0x23)
DEPLOYABLE_TO_DRONE = struct.pack("<I", 0x080AD3AA)   # deployable record -> drone (property value)
OWNER_TO_DEPLOYABLE = struct.pack("<I", 0xDB2E1510)   # pawn record       -> deployable (property value)
CONTROLLER_TO_PAWN = struct.pack("<I", 0x69E42CFB)    # controller record -> its possessed pawn
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


def _norm(s: Any) -> str:
    return re.sub(r"[^a-z0-9]", "", str(s or "").lower())


def _named_owners(data: bytes, usernames: Iterable[str]) -> dict[int, str]:
    """deployable -> username read from the username STRING inside its own 0xDB2E1510 record
    (header..edge). High confidence (the name is literally in the record), but only ~half of
    deployables have a name-bearing record, so it's the lock, not the whole answer."""
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


def _pawn_owners(data: bytes, usernames: Iterable[str]) -> dict[int, str]:
    """pawn entity -> username, via each controller's 0x69E42CFB (possessed-pawn) property.
    Controllers come from replay_utility.find_controllers -- the same reliable anchor the gadget
    reader uses -- so this has no name-proximity noise and covers every player."""
    try:
        from integration.replay_utility import find_controllers
        ctrl = find_controllers(data, list(usernames))
    except Exception:
        return {}
    out: dict[int, str] = {}
    for controller, pawn in _value_links(data, CONTROLLER_TO_PAWN):
        if controller in ctrl:
            out.setdefault(pawn, ctrl[controller])
    return out


def _deployable_pawns(data: bytes) -> dict[int, collections.Counter]:
    """deployable -> Counter of the pawn entities whose records point at it via 0xDB2E1510."""
    out: dict[int, collections.Counter] = collections.defaultdict(collections.Counter)
    for pawn, deployable in _value_links(data, OWNER_TO_DEPLOYABLE):
        out[deployable][pawn] += 1
    return out


def _resolve_one(dep: int, named: dict[int, str], pawn_user: dict[int, str],
                 cand: dict[int, collections.Counter]) -> Optional[str]:
    """One deployable's owner, independently (no distinctness): the name in its record if any,
    else the user of its best (most re-sent) controller-linked pawn. Right for action drones,
    where one player can hold several drones."""
    if dep in named:
        return named[dep]
    for pawn, _w in cand.get(dep, collections.Counter()).most_common():
        if pawn in pawn_user:
            return pawn_user[pawn]
    return None


def _assign_distinct(deployables: Iterable[int], named: dict[int, str], pawn_user: dict[int, str],
                     cand: dict[int, collections.Counter]) -> dict[int, str]:
    """deployable -> username over a set that is ONE-per-player (a round's prep drones): the
    name-confirmed owners are locked first, the rest assigned to the remaining players by their
    controller->pawn link, best candidate first. Distinctness stops a dense snapshot clumping
    several drones onto one player."""
    deployables = set(deployables)
    owner: dict[int, str] = {}
    used: set[str] = set()
    for dep in deployables:                                   # 1. lock the name-confirmed owners
        u = named.get(dep)
        if u is not None and _norm(u) not in used:
            owner[dep] = u
            used.add(_norm(u))
    ranked = []                                               # 2. the rest, by controller->pawn link
    for dep in deployables:
        if dep in owner:
            continue
        for pawn, weight in cand.get(dep, collections.Counter()).most_common():
            u = pawn_user.get(pawn)
            if u is not None:
                ranked.append((weight, dep, u))
    for _weight, dep, u in sorted(ranked, key=lambda x: -x[0]):
        if dep in owner or _norm(u) in used:
            continue
        owner[dep] = u
        used.add(_norm(u))
    return owner


def deployable_owners(data: bytes, usernames: Iterable[str]) -> dict[int, str]:
    """deployable entity -> username, over the whole round with a DISTINCT assignment (one player
    per deployable). See the module docstring for the chain. Prefer drone_owner_map for drones --
    it keeps action drones (several per player) out of the distinct set."""
    named = _named_owners(data, usernames)
    pawn_user = _pawn_owners(data, usernames)
    cand = _deployable_pawns(data)
    return _assign_distinct(set(cand) | set(named), named, pawn_user, cand)


def drone_owner_map(data: bytes, usernames: Iterable[str],
                    prep_refs: Iterable[int] = ()) -> dict[int, str]:
    """drone entity -> username. Chains drone <- deployable <- player (see the module docstring).

    prep_refs: the drones thrown in the prep phase. Those are resolved with a DISTINCT assignment
    (each attacker drones once in prep), which is what stops clumping; every other drone is
    resolved on its own (a player may hold several action drones). Only resolved drones appear --
    a missing one means "unknown", not "nobody"."""
    named = _named_owners(data, usernames)
    pawn_user = _pawn_owners(data, usernames)
    cand = _deployable_pawns(data)
    drone_dep = {drone: dep for dep, drone in _value_links(data, DEPLOYABLE_TO_DRONE)}
    if not drone_dep:
        return {}
    prep_refs = set(prep_refs)
    prep_deps = {drone_dep[r] for r in prep_refs if r in drone_dep}
    distinct = _assign_distinct(prep_deps, named, pawn_user, cand)
    out: dict[int, str] = {}
    for drone, dep in drone_dep.items():
        user = distinct.get(dep) if drone in prep_refs else _resolve_one(dep, named, pawn_user, cand)
        if user is not None:
            out[drone] = user
    return out
