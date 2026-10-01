"""
Comms timeline: who said what, and when, against the kill feed.

Everything is placed on one clock -- POSIX epoch seconds -- so speech from
several sources lines up with each other and with the game:

  * Speech ("utterances") arrives already in epoch time: the host's own mic
    track, the host's Discord track (every teammate mixed together), and any
    teammate's own mic recording, aligned against that Discord track.
  * Game events come from the replays. Each round's replay header carries the
    host PC's LOCAL wall-clock time (r6-dissect labels it "Z", but it isn't
    UTC -- see TimelineAligner._extract_timestamps), so the host's UTC offset
    turns it into epoch time. Each event's elapsedSeconds (seconds since prep
    began, counted from the round clock's once-a-second ticks) then places
    it within the round.

Pure data in, data out: no I/O, no audio. The server builds the inputs.

Utterance dict:
    {"speaker": display name, "username": in-game name or "",
     "source": "self" | "team" | "voice" | "mixed",
     "start": epoch, "end": epoch, "text": str}
"""
from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Iterable, Optional

# The replay's timestamp is taken when the round's recording starts, a moment
# before the first prep tick; elapsedSeconds counts from that first tick.
# Checked 2026-09-30 on the 2026-09-29 Border match (10 rounds, 41 min of
# comms): speech onsets bunch up 1-3 s after kills -- the "got him" / "he's
# one" reaction -- which is where they should land if this is right.
PREP_OFFSET_SEC = 1.0
PREP_SEC = 45.0
ACTION_SEC = 180.0

UNKNOWN_TEAM_SPEAKER = "Team (unassigned)"

# Callouts that carry enemy info -- the kind that should change what a
# teammate does next. Deliberately excludes bare "left"/"right"/"one", which
# show up in almost every sentence and would drown the list.
_ALERT_PATTERNS = [
    r"\bbehind\b", r"\bflank", r"\bwatch\b", r"\bcareful\b", r"\babove\b", r"\bbelow\b",
    r"\bunder (you|me|us)\b", r"\bon you\b", r"\bcoming\b", r"\bpush(ing)?\b", r"\brotat",
    r"\broam", r"\bholding\b", r"\bpeek", r"\bhatch", r"\bstairs\b", r"\bwindow\b",
    r"\bdoor\b", r"\b(one|two|three|last one) (left|down|up)\b", r"\bhe'?s\b", r"\bthey'?re\b",
    r"\bspotted\b", r"\bdrone", r"\brun ?out\b", r"\bspawn ?peek", r"\bwall\b", r"\bvert",
    r"\bsite\b", r"\bdefuser\b", r"\bplant", r"\bdefus", r"\blow\b", r"\bweak\b", r"\blit\b",
]
_ALERT_RE = re.compile("|".join(_ALERT_PATTERNS), re.IGNORECASE)

WARN_WINDOW_SEC = 12.0        # callout this soon before a teammate's death
DEATH_CALLOUT_WINDOW_SEC = 8.0
TALK_OVER_MIN_SEC = 1.0
FIGHT_GAP_SEC = 8.0
FIGHT_QUIET_PAD_SEC = 5.0


# ── Clock helpers ─────────────────────────────────────────────────────────

def local_stamp_to_epoch(stamp: str, utc_offset_sec: float) -> Optional[float]:
    """Replay timestamp (host-local digits, bogus "Z") -> POSIX epoch."""
    try:
        naive = datetime.fromisoformat(str(stamp).strip().rstrip("Z"))
    except ValueError:
        return None
    return naive.replace(tzinfo=timezone.utc).timestamp() - float(utc_offset_sec or 0)


def round_start_epoch(rnd: dict, utc_offset_sec: float) -> Optional[float]:
    base = local_stamp_to_epoch(rnd.get("timestamp", ""), utc_offset_sec)
    return None if base is None else base + PREP_OFFSET_SEC


def clock_label(t: float, plant_t: Optional[float] = None, end_t: Optional[float] = None) -> str:
    """Seconds since prep began -> what the in-game clock showed, roughly."""
    def mmss(s: float) -> str:
        s = max(0, int(round(s)))
        return f"{s // 60}:{s % 60:02d}"
    if t < 0:
        return f"-{mmss(-t)}"
    if end_t is not None and t > end_t + 1:
        return f"end+{mmss(t - end_t)}"
    if plant_t is not None and t >= plant_t:
        return f"PP {mmss(t - plant_t)}"
    if t < PREP_SEC:
        return f"prep {mmss(PREP_SEC - t)}"
    return mmss(ACTION_SEC - (t - PREP_SEC))


def is_alert(text: str) -> bool:
    return bool(_ALERT_RE.search(text or ""))


# ── Merging speech sources ────────────────────────────────────────────────

def _overlap(a0: float, a1: float, b0: float, b1: float) -> float:
    return max(0.0, min(a1, b1) - max(a0, b0))


def drop_team_duplicates(team: list[dict], individual: list[dict], min_cover: float = 0.5) -> list[dict]:
    """
    A teammate who records their own mic is also in the host's Discord track.
    Their own track is cleaner and knows who's talking, so a Discord-track
    line mostly covered by some teammate's own speech is the same words twice
    and goes.
    """
    if not individual:
        return list(team)
    ind = sorted((u["start"], u["end"]) for u in individual)
    kept = []
    for u in team:
        dur = max(1e-6, u["end"] - u["start"])
        covered = sum(_overlap(u["start"], u["end"], s, e) for s, e in ind
                      if e > u["start"] and s < u["end"])
        if covered / dur < min_cover:
            kept.append(u)
    return kept


def _norm_words(text: str) -> str:
    return " ".join(re.findall(r"[a-z0-9']+", (text or "").lower()))


def drop_echoes(own: list[dict], team: list[dict], min_similarity: float = 0.6) -> tuple[list[dict], int]:
    """
    With routing right, Discord never plays your own voice back, so your
    mic track and the Discord track never say the same words at the same
    moment. When they do, one track is picking up the other -- the mic
    hearing Discord through speakers, or OBS mixing both into each track (as
    on 2026-09-29) -- and every such line would also show up as "talking
    over each other". Returns own lines with the echoes removed (the Discord
    copy is kept: it can't wrongly put a teammate's words in your mouth),
    and how many were removed.
    """
    from difflib import SequenceMatcher
    kept, dropped = [], 0
    for u in own:
        words = _norm_words(u["text"])
        echo = any(
            _overlap(u["start"], u["end"], t["start"], t["end"]) > 0.3
            and SequenceMatcher(None, words, _norm_words(t["text"])).ratio() >= min_similarity
            for t in team if t["start"] < u["end"] + 1.0 and t["end"] > u["start"] - 1.0
        )
        if echo:
            dropped += 1
        else:
            kept.append(u)
    return kept, dropped


# ── Timeline ──────────────────────────────────────────────────────────────

def build_timeline(
    rounds: list[dict],
    utterances: list[dict],
    utc_offset_sec: float,
    names: Optional[dict[str, str]] = None,
    tracked_usernames: Iterable[str] = (),
) -> dict:
    """
    rounds: RecImporter.timeline_round() dicts.
    names: lower-case in-game username -> display name (the roster).
    tracked_usernames: players whose own voice is on a track of its own (the
      host's mic, teammates' recorders) -- only for them can silence be read
      as "didn't say anything".
    """
    names = {k.lower(): v for k, v in (names or {}).items()}
    tracked = {u.lower() for u in tracked_usernames if u}

    def display(username: str) -> str:
        return names.get((username or "").lower(), username or "?")

    rounds = sorted((r for r in rounds if round_start_epoch(r, utc_offset_sec) is not None),
                    key=lambda r: r.get("round_number", 0))
    starts = [round_start_epoch(r, utc_offset_sec) for r in rounds]
    speech = sorted((u for u in utterances if (u.get("text") or "").strip()), key=lambda u: u["start"])

    out_rounds = []
    flags: list[dict] = []
    for i, rnd in enumerate(rounds):
        start = starts[i]
        next_start = starts[i + 1] if i + 1 < len(starts) else None
        events = sorted(rnd.get("events") or [], key=lambda e: e.get("elapsed", 0.0))
        last_event = max((e.get("elapsed", 0.0) for e in events), default=PREP_SEC)
        # The last round has no next prep to end at: keep what's said until a
        # full round's length, or a minute past its last event if later.
        end_epoch = next_start if next_start is not None else \
            start + max(last_event + 60.0, PREP_SEC + ACTION_SEC + 30.0)
        ours = {u.lower() for u in rnd.get("ours") or []}
        plant_t = next((e["elapsed"] for e in events if e.get("type") == "DefuserPlantComplete"), None)
        decided_t = last_event

        items: list[dict] = []
        deaths: list[dict] = []
        for e in events:
            t = float(e.get("elapsed", 0.0))
            kind = e.get("type", "")
            item = {"t": round(t, 2), "clock": clock_label(t, plant_t),
                    "kind": kind}
            if kind == "Kill":
                killer, victim = e.get("username", ""), e.get("target", "")
                item.update(killer=display(killer), victim=display(victim),
                            killer_ours=killer.lower() in ours, victim_ours=victim.lower() in ours,
                            headshot=bool(e.get("headshot")))
                if victim.lower() in ours:
                    deaths.append({"t": t, "username": victim, "who": display(victim)})
            elif kind == "Death":
                who = e.get("username", "")
                item.update(victim=display(who), victim_ours=who.lower() in ours)
                if who.lower() in ours:
                    deaths.append({"t": t, "username": who, "who": display(who)})
            else:
                item.update(player=display(e.get("username", "")),
                            player_ours=(e.get("username", "").lower() in ours))
            items.append(item)

        # Speech from this round's prep until the next round's prep; the
        # first round also takes what was said in the lobby before it.
        lo = start - (120.0 if i == 0 else 0.0)
        in_round = [u for u in speech if lo <= u["start"] < end_epoch]
        for u in in_round:
            t0, t1 = u["start"] - start, u["end"] - start
            items.append({"t": round(t0, 2), "end": round(t1, 2),
                          "clock": clock_label(t0, plant_t, decided_t),
                          "kind": "speech", "speaker": u.get("speaker") or UNKNOWN_TEAM_SPEAKER,
                          "source": u.get("source", ""), "text": u["text"].strip(),
                          "alert": is_alert(u["text"])})
        items.sort(key=lambda it: (it["t"], 0 if it["kind"] != "speech" else 1))
        _mark_overlaps(items)
        round_flags = _flags_for_round(rnd, items, deaths, ours, tracked, display, plant_t)
        flags.extend(round_flags)
        out_rounds.append({
            "round_number": rnd.get("round_number"),
            "start_epoch": start,
            "ours": [display(u) for u in rnd.get("ours") or []],
            "items": items,
            "flag_count": len(round_flags),
        })

    return {
        "version": 1,
        "rounds": out_rounds,
        "flags": flags,
        "speakers": speaker_stats(speech, rounds, starts),
        "sources": sorted({u.get("source", "") for u in speech}),
    }


def _mark_overlaps(items: list[dict]) -> None:
    said = [it for it in items if it["kind"] == "speech"]
    for a in said:
        a["over"] = []
    for i, a in enumerate(said):
        for b in said[i + 1:]:
            if b["t"] >= a["end"]:
                break
            if a["speaker"] == b["speaker"]:
                continue
            ov = _overlap(a["t"], a["end"], b["t"], b["end"])
            if ov >= TALK_OVER_MIN_SEC:
                if b["speaker"] not in a["over"]:
                    a["over"].append(b["speaker"])
                if a["speaker"] not in b["over"]:
                    b["over"].append(a["speaker"])


def _flags_for_round(rnd, items, deaths, ours, tracked, display, plant_t) -> list[dict]:
    rn = rnd.get("round_number")
    said = [it for it in items if it["kind"] == "speech"]
    flags: list[dict] = []

    for d in deaths:
        td = d["t"]
        # 1. Someone gave enemy info just before a teammate died.
        warnings = [s for s in said
                    if s["alert"] and s["speaker"] != d["who"]
                    and td - WARN_WINDOW_SEC <= s["end"] <= td + 0.5 and s["t"] < td]
        if warnings:
            w = warnings[-1]
            flags.append({
                "kind": "callout_then_death", "round": rn, "t": round(w["t"], 2),
                "clock": w["clock"], "players": [w["speaker"], d["who"]],
                "text": f'{w["speaker"]}: "{w["text"]}" -- {d["who"]} died '
                        f'{max(0.0, td - w["end"]):.0f}s later',
            })
        # 2. A player with their own track died and said nothing after.
        if d["username"].lower() in tracked:
            after = [s for s in said if s["speaker"] == d["who"]
                     and td - 0.5 <= s["t"] <= td + DEATH_CALLOUT_WINDOW_SEC]
            if not after:
                flags.append({
                    "kind": "no_death_callout", "round": rn, "t": round(td, 2),
                    "clock": clock_label(td, plant_t), "players": [d["who"]],
                    "text": f"{d['who']} died and didn't call anything in the next "
                            f"{DEATH_CALLOUT_WINDOW_SEC:.0f}s",
                })

    # 3. Two people talking over each other while it matters (action phase).
    seen = set()
    for s in said:
        for other in s.get("over") or []:
            key = tuple(sorted((s["speaker"], other))) + (int(s["t"] // 5),)
            if key in seen or s["t"] < PREP_SEC:
                continue
            seen.add(key)
            flags.append({
                "kind": "talk_over", "round": rn, "t": round(s["t"], 2), "clock": s["clock"],
                "players": sorted((s["speaker"], other)),
                "text": f"{s['speaker']} and {other} talking at the same time: \"{s['text']}\"",
            })

    # 4. A fight (two or more kills close together involving us) with no
    #    one on our side saying anything around it.
    kills = [it for it in items if it["kind"] == "Kill" and (it.get("killer_ours") or it.get("victim_ours"))]
    cluster: list[dict] = []
    for k in kills + [None]:
        if k is not None and (not cluster or k["t"] - cluster[-1]["t"] <= FIGHT_GAP_SEC):
            cluster.append(k)
            continue
        if len(cluster) >= 2:
            a, b = cluster[0]["t"] - FIGHT_QUIET_PAD_SEC, cluster[-1]["t"] + FIGHT_QUIET_PAD_SEC
            if not any(_overlap(s["t"], s["end"], a, b) > 0 for s in said):
                flags.append({
                    "kind": "quiet_fight", "round": rn, "t": round(cluster[0]["t"], 2),
                    "clock": cluster[0]["clock"], "players": [],
                    "text": f"{len(cluster)} kills in {cluster[-1]['t'] - cluster[0]['t']:.0f}s "
                            f"with no comms around them",
                })
        cluster = [k] if k is not None else []
    return flags


def speaker_stats(speech: list[dict], rounds: list[dict], starts: list[float]) -> list[dict]:
    """Talk time and words per speaker, plus how often each one started
    talking over someone who was already speaking."""
    by: dict[str, dict] = {}
    ordered = sorted(speech, key=lambda u: u["start"])
    for i, u in enumerate(ordered):
        who = u.get("speaker") or UNKNOWN_TEAM_SPEAKER
        s = by.setdefault(who, {"speaker": who, "lines": 0, "words": 0, "talk_sec": 0.0,
                                "interrupted_others": 0, "alerts": 0})
        s["lines"] += 1
        s["words"] += len(u["text"].split())
        s["talk_sec"] += max(0.0, u["end"] - u["start"])
        s["alerts"] += 1 if is_alert(u["text"]) else 0
        if any(p.get("speaker") != who and p["start"] < u["start"] < p["end"] - 0.3
               for p in ordered[max(0, i - 6):i]):
            s["interrupted_others"] += 1
    total = sum(s["talk_sec"] for s in by.values()) or 1.0
    for s in by.values():
        s["talk_sec"] = round(s["talk_sec"], 1)
        s["share"] = round(s["talk_sec"] / total, 3)
    return sorted(by.values(), key=lambda s: -s["talk_sec"])


# ── Text views ────────────────────────────────────────────────────────────

def transcript_text(timeline: dict) -> str:
    """Readable, speaker-labelled transcript with the kill feed inline."""
    lines: list[str] = []
    for r in timeline.get("rounds", []):
        lines.append(f"=== Round {r['round_number']} ===")
        for it in r["items"]:
            if it["kind"] == "speech":
                lines.append(f"[{it['clock']}] {it['speaker']}: {it['text']}")
            elif it["kind"] == "Kill":
                lines.append(f"[{it['clock']}] x {it['killer']} killed {it['victim']}")
            elif it["kind"] == "DefuserPlantComplete":
                lines.append(f"[{it['clock']}] * defuser planted")
            elif it["kind"] == "DefuserDisableComplete":
                lines.append(f"[{it['clock']}] * defuser disabled")
    return "\n".join(lines)


_FLAG_LABELS = {
    "callout_then_death": "enemy info called, then a teammate died shortly after",
    "no_death_callout": "died without calling anything afterwards",
    "talk_over": "two people talking over each other mid-round",
    "quiet_fight": "fights with no comms around them",
}


def ai_summary_text(timeline: dict, max_examples: int = 3) -> str:
    """Compact comms facts for the match-summary prompt: measured, not guessed."""
    speakers = timeline.get("speakers") or []
    if not speakers:
        return ""
    parts = ["Talk share: " + ", ".join(
        f"{s['speaker']} {s['share']:.0%} ({s['words']} words, {s['alerts']} enemy-info callouts)"
        for s in speakers)]
    flags = timeline.get("flags") or []
    counts: dict[str, int] = {}
    for f in flags:
        counts[f["kind"]] = counts.get(f["kind"], 0) + 1
    for kind, n in sorted(counts.items(), key=lambda kv: -kv[1]):
        parts.append(f"{n} x {_FLAG_LABELS.get(kind, kind)}")
        for f in [f for f in flags if f["kind"] == kind][:max_examples]:
            parts.append(f"  - R{f['round']} {f['clock']}: {f['text']}")
    return "\n".join(parts)
