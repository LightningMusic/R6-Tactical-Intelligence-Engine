"""
Where every player was, read from a round's .rec replay.

The replay's movement stream (decompressed with replay_utility.read_rec) carries a packet per body update:

  60 73 85 FE  "FC update". The body's entity id sits 16 bytes before it (12 in older versions); the u16 after
               the pattern is a set of flags: 0x0100 = a position follows at +6 (x, y, z float32, metres, z up),
               0x0200 = a rotation follows (quaternion x, y, z, w; at +22 after a position, else at +6).
               Low byte 0xB0/0xB8 = a living body; 0x90/0x98/0x10/0x18/0xD0 = the ragdoll of a dead one.

A body standing still sends no positions (only aim packets) until it moves or dies, and there is no clock in
the movement stream at all (the round clock and kill feed come later in the file, in a separate stream).
So:
  * who is who: the snapshot at the start lists each player's 8-byte id followed by their body's id, as
    `<id> 39 ... <body>` (defenders) or `<id> 21 04 FF <body>` / `<id> 01 04 <body>` (attackers);
    r6-dissect reports some ids wrong (top half zero), filled in by elimination, then by death order;
  * a death is the first ragdoll packet after the body's last live movement (Iana's body sends ragdoll-family
    packets while she steers her hologram, which is an unlinked body appearing mid-round: dropped);
  * time is pinned to known moments (prep start, the attackers' bodies appearing = 45 s, every death at the
    kill feed's second) and interpolated in between.

Checked on 53 rounds over four maps (2026-10-06..08): the killer faces the victim within 15 degrees in 95%
of kills (median 1.6 degrees). The decoding notes and experiments live outside this repo
(R6PosDecode-experiment/FINDINGS.md). Built on wnc-replay/replay-tool's published packet notes.
"""
from __future__ import annotations

import base64
import bisect
import collections
import gzip
import json
import math
import re
import struct
from typing import Any, Optional

FC = bytes([0x60, 0x73, 0x85, 0xFE])
CLOCK = bytes([0x1F, 0x07, 0xEF, 0xC9])
HAS_POS, HAS_ROT = 0x0100, 0x0200
BODY = (0xB0, 0xB8)
RAGDOLL = {0x90, 0x98, 0x10, 0x18, 0xD0}
PREP_SEC = 45.0
SPAWN_WINDOW = 0.02              # share of the stream within which a side's bodies all appear
MIN_SAMPLES = 200                # fewer packets than this is not a player's body
FORMAT_VERSION = 1


def _ref_at(data: bytes, i: int) -> Optional[int]:
    for back in (16, 12):
        if i >= back:
            r = struct.unpack_from("<I", data, i - back)[0]
            if r >> 24 == 0xF0:
                return r
    return None


def movement(data: bytes) -> tuple[dict[int, list[tuple]], dict[int, int]]:
    """(entity -> [(offset, x, y, z, yaw or None)], entity -> offset of its death)."""
    tracks: dict[int, list[tuple]] = collections.defaultdict(list)
    ragdolls: dict[int, list[int]] = collections.defaultdict(list)
    last_pos: dict[int, tuple] = {}
    i = data.find(FC)
    while i != -1:
        if i + 40 <= len(data):
            flags = struct.unpack_from("<H", data, i + 4)[0]
            ref = _ref_at(data, i)
            if ref is not None:
                low = flags & 0xFF
                if low in RAGDOLL:
                    ragdolls[ref].append(i)
                elif low in BODY and flags & (HAS_POS | HAS_ROT):
                    pos, yaw = None, None
                    if flags & HAS_POS:
                        x, y, z = struct.unpack_from("<3f", data, i + 6)
                        if all(math.isfinite(v) and abs(v) < 500 for v in (x, y, z)) and not (abs(x) < 0.5 and abs(y) < 0.5):
                            pos = (x, y, z)
                    if flags & HAS_ROT:
                        q = struct.unpack_from("<4f", data, i + (22 if flags & HAS_POS else 6))
                        if all(math.isfinite(v) for v in q) and abs(sum(v * v for v in q) - 1) < 0.01:
                            yaw = yaw_deg(q)
                    if pos is not None:
                        last_pos[ref] = pos
                    elif yaw is not None and ref in last_pos:
                        pos = last_pos[ref]
                    if pos is not None:
                        tracks[ref].append((i, pos[0], pos[1], pos[2], yaw))
        i = data.find(FC, i + 1)
    deaths = {}
    for ref, offs in ragdolls.items():
        last_live = tracks[ref][-1][0] if tracks.get(ref) else -1
        after = [o for o in offs if o > last_live]
        if after:
            deaths[ref] = after[0]
    return dict(tracks), deaths


def yaw_deg(q) -> float:
    """Facing on the map plane, in the frame of atan2(dy, dx). The body's forward axis is +y (hence +90)."""
    x, y, z, w = q
    a = math.degrees(math.atan2(2 * (w * z + x * y), 1 - 2 * (y * y + z * z))) + 90.0
    return (a + 180.0) % 360.0 - 180.0


def link_bodies(data: bytes, players: list[dict], bodies) -> dict[int, str]:
    """body entity -> username, from the snapshot's `<player id> ... <body>` entries (exact layouts only:
    each id appears hundreds of times elsewhere, next to unrelated entities)."""
    ids = {}
    for p in players:
        try:
            pid = int(p.get("id") or 0)
        except (TypeError, ValueError):
            continue
        if pid >> 32:
            ids[struct.pack("<Q", pid)] = p.get("username")
    body_bytes = {struct.pack("<I", r): r for r in bodies}
    votes: dict[int, collections.Counter] = collections.defaultdict(collections.Counter)
    for key, user in ids.items():
        for m in re.finditer(re.escape(key), data):
            win = data[m.end():m.end() + 17]
            for j in range(2, min(14, len(win) - 3)):
                ref = body_bytes.get(win[j:j + 4])
                if ref is None:
                    continue
                if win[0] == 0x39 or win[:j] in (b"\x21\x04\xff", b"\x01\x04"):
                    votes[ref][user] += 1
                break
    out, taken = {}, set()
    for ref, user, _n in sorted(((r, u, n) for r, c in votes.items() for u, n in c.items()), key=lambda x: -x[2]):
        if ref not in out and user not in taken:
            out[ref] = user
            taken.add(user)
    return out


def round_end_elapsed(data: bytes) -> float:
    """Seconds from prep start to the last clock tick (one tick a second; the first non-zero one starts prep)."""
    ticks, started = 0, False
    i = data.find(CLOCK)
    while i != -1:
        v = struct.unpack_from("<I", data, i + 5)[0] if i + 9 <= len(data) else 0
        if started:
            ticks += 1
        elif v:
            started = True
        i = data.find(CLOCK, i + 1)
    return float(ticks)


def _feed_deaths(dissect: dict) -> list[tuple[float, Optional[str], str]]:
    out, seen = [], set()
    for k in dissect.get("matchFeedback") or []:
        kind = (k.get("type") or {}).get("name") if isinstance(k.get("type"), dict) else k.get("type")
        victim = k.get("target") if kind == "Kill" else k.get("username") if kind == "Death" else None
        if victim and victim not in seen and k.get("elapsedSeconds") is not None:
            seen.add(victim)
            out.append((float(k["elapsedSeconds"]), k.get("username") if kind == "Kill" else None, victim))
    return out


def decode_round(data: bytes, dissect: dict, hz: float = 2.0) -> Optional[dict[str, Any]]:
    """Every player's path through one round: {"v", "hz", "def_team", "linked", "died": {name: t},
    "players": {name: {"team": i, "pts": [[t, x, y, z, yaw or None], ...]}}} with t in seconds since prep began.
    None when the replay has no readable movement or doesn't say which side defends."""
    players = {p.get("username"): p for p in dissect.get("players") or [] if p.get("username")}
    def_team = next((i for i, t in enumerate(dissect.get("teams") or []) if t.get("role") == "Defense"), None)
    if not players or def_team is None:
        return None
    tracks, deaths = movement(data)
    bodies = {r: p for r, p in tracks.items() if len(p) >= MIN_SAMPLES}
    if not bodies:
        return None
    team_of = lambda n: players[n].get("teamIndex")
    names = link_bodies(data, list(players.values()), bodies)
    linked = len(names)
    first = min(p[0][0] for p in bodies.values())
    window = SPAWN_WINDOW * len(data)
    att_starts = sorted(bodies[r][0][0] for r, n in names.items() if team_of(n) != def_team)
    if not att_starts:
        att_starts = sorted(p[0][0] for p in bodies.values() if p[0][0] - first >= window)
    action_start = att_starts[0] if att_starts else None
    sides: dict[bool, list[int]] = {True: [], False: []}
    for r in list(bodies):
        if r in names:
            sides[team_of(names[r]) == def_team].append(r)
        elif bodies[r][0][0] - first < window:
            sides[True].append(r)
        elif action_start is not None and abs(bodies[r][0][0] - action_start) < window:
            sides[False].append(r)
        else:
            del bodies[r]                  # an unowned body appearing mid-round: a gadget (Iana's hologram)
    feed = _feed_deaths(dissect)
    end_of = lambda r: deaths.get(r, bodies[r][-1][0])
    for is_def, refs in sides.items():
        team = [n for n in players if (team_of(n) == def_team) == is_def and n not in names.values()]
        free = [r for r in refs if r not in names]
        if len(free) == 1 and len(team) == 1:
            names[free[0]] = team[0]
            continue
        died = sorted((r for r in free if r in deaths), key=end_of)
        lived = sorted((r for r in free if r not in deaths), key=lambda r: bodies[r][-1][0])
        gone = [(t, v) for t, _, v in feed if v in team]
        if len(died) != len(gone):
            died = sorted(free, key=end_of)[:len(gone)]
            lived = [r for r in free if r not in died]
        for r, (_, v) in zip(died, gone):
            names[r] = v
        for r, n in zip(lived, [n for n in team if n not in {v for _, v in gone}]):
            names[r] = n
    # time
    death_t = {v: t for t, _, v in feed}
    anchors = [(first, 0.0)] + ([(action_start, PREP_SEC)] if action_start is not None else [])
    anchors += [(deaths[r], death_t[n]) for r, n in names.items() if n in death_t and r in deaths]
    anchors.sort()
    kept: list[tuple] = []
    for a in anchors:
        if not kept or a[1] >= kept[-1][1]:
            kept.append(a)
    end_off = max(p[-1][0] for p in bodies.values())
    if len(kept) >= 3 and kept[-1][1] > PREP_SEC:
        (o_a, t_a) = next(a for a in kept if a[1] == PREP_SEC)
        o_z, t_z = kept[-1]
        rate = (o_z - o_a) / max(t_z - t_a, 1.0)
        kept.append((end_off, t_z + (end_off - o_z) / max(rate, 1.0)))
    else:
        kept.append((end_off, max([round_end_elapsed(data)] + [t for t, _, _ in feed])))
    offs = [a[0] for a in kept]

    def t_at(o: int) -> float:
        k = bisect.bisect_right(offs, o)
        if k <= 0:
            return kept[0][1]
        if k >= len(kept):
            return kept[-1][1]
        (o0, t0), (o1, t1) = kept[k - 1], kept[k]
        return t0 + (t1 - t0) * (o - o0) / max(o1 - o0, 1)

    out_players: dict[str, dict] = {}
    died: dict[str, float] = {}
    step = 1.0 / hz
    for r, pts in bodies.items():
        n = names.get(r)
        if not n:
            continue
        cut = deaths.get(r)
        thin, last_t = [], -1e9
        for o, x, y, z, yaw in pts:
            if cut is not None and o > cut:
                break
            t = t_at(o)
            if t - last_t >= step:
                thin.append([round(t, 2), round(x, 2), round(y, 2), round(z, 2), None if yaw is None else round(yaw, 1)])
                last_t = t
        if cut is not None:
            died[n] = round(t_at(cut), 2)
        out_players[n] = {"team": team_of(n), "pts": thin}
    return {"v": FORMAT_VERSION, "hz": hz, "def_team": def_team, "linked": linked, "died": died, "players": out_players}


def encode(positions: dict) -> str:
    """Compact text for storage (gzip + base64 of the JSON)."""
    return base64.b64encode(gzip.compress(json.dumps(positions, separators=(",", ":")).encode("utf-8"))).decode("ascii")


def decode_blob(text: str) -> Optional[dict]:
    try:
        return json.loads(gzip.decompress(base64.b64decode(text)).decode("utf-8"))
    except Exception:
        return None


def at(pts: list, t: float) -> Optional[list]:
    """The sample at (or just after) t, or None once the track has ended more than 2 s before t."""
    if not pts:
        return None
    k = bisect.bisect_left([p[0] for p in pts], t)
    if k >= len(pts):
        return pts[-1] if t - pts[-1][0] <= 2.0 else None
    return pts[k]
