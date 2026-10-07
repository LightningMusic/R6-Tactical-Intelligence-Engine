"""
Which microphones to try when the one in use delivers only silence.

The rule for this app is that teammates do nothing: they plug the stick in (or open a link) and it has to
find a working microphone by itself. On 2026-10-06 a headset that OBS opened without complaint delivered
nothing but zeros for 102 minutes. Trying the other real microphones in a sensible order, and remembering
which one worked, is how it recovers without anybody noticing a thing.

Not every device that can capture audio is a microphone worth trying, or safe to record: a webcam or laptop
array hears the whole room (in the esports room, the teammates sitting next to you), and loopback or virtual
devices carry the game, not the player. Those are skipped.
"""
from __future__ import annotations

import re
from typing import Iterable, Optional

# Never recorded as "the player's microphone": things that hear the room, or carry the computer's own sound.
SKIP = re.compile(r"camera|webcam|stereo mix|loopback|virtual|cable|voicemeeter|steam streaming|what u hear|"
                  r"line out|speaker|output|obs|nvidia|broadcast|array", re.I)
# Most to least likely to be the player's own headset.
PREFER = [re.compile(p, re.I) for p in (r"head ?set", r"head ?phone|ear ?phone|ear ?bud", r"wireless|bluetooth",
                                        r"usb", r"micro")]

DEFAULT_ID = "default"


def usable(name: str) -> bool:
    return bool(name) and not SKIP.search(name)


def _score(name: str) -> int:
    for i, p in enumerate(PREFER):
        if p.search(name):
            return i
    return len(PREFER)


def candidates(devices: Iterable[tuple[str, str]], tried: Iterable[str] = ()) -> list[tuple[str, str]]:
    """(device id, name) pairs worth trying next, best first. `tried` are ids already given a chance.
    The system default comes first when it hasn't been tried (a different default is the most common fix),
    then the rest by how much their name says "this is a headset"."""
    seen = set(tried)
    pool = [(str(i), str(n)) for i, n in devices if str(i) not in seen and (str(i) == DEFAULT_ID or usable(str(n)))]
    default = [d for d in pool if d[0] == DEFAULT_ID]
    rest = sorted((d for d in pool if d[0] != DEFAULT_ID), key=lambda d: (_score(d[1]), d[1].lower()))
    return default + rest


def _norm(name: str) -> str:
    """Windows renames a device when it is plugged into another port ("Microphone (2- USB Audio)"): ignore the
    numbering so the same headset is still the same headset."""
    s = re.sub(r"\b\d+\s*-\s*", "", (name or "").lower())
    s = re.sub(r"\b\d+\b", "", s)
    return re.sub(r"\s+", " ", s).replace(" )", ")").strip()


def find_by_name(devices: Iterable[tuple[str, str]], name: str) -> Optional[tuple[str, str]]:
    """The device with this name on this PC. Device ids differ from PC to PC but the name of a headset
    doesn't, so a stick that worked on one PC finds its microphone again on another."""
    want = _norm(name)
    if not want:
        return None
    devs = [(str(i), str(n)) for i, n in devices]
    for i, n in devs:
        if _norm(n) == want:
            return i, n
    for i, n in devs:
        if i != DEFAULT_ID and _norm(n) and (want in _norm(n) or _norm(n) in want):
            return i, n
    return None
