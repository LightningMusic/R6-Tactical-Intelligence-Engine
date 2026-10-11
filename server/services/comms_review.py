"""
Listen and mark: the comms timeline checked against the truth.

The host plays random lines from stored sessions and marks who was really speaking and whether the words are
right. That turns "87% of lines carry a name" into how many carry the RIGHT name, for each way a line gets one:

  your_mic    the host's own mic: credited to the host
  bleed       heard on the host's mic but credited to the teammate beside them (detect_bleed)
  own_track   from a teammate's own recording, lined up against the Discord track
  voice_id    a Discord line named by a learned voice
  unassigned  a Discord line nobody could name: the answer says who it was

Audio: a line from the host's mic plays from that track; every other line plays from the Discord track at that
moment, which is where a teammate is heard, so a teammate track lined up at the wrong time, or a voice named
wrongly, shows up as the wrong voice.
"""
from __future__ import annotations

import collections
import io
import json
import random
import subprocess
import wave
from datetime import datetime, timezone
from typing import Any, Optional

from server.config import server_settings
from server.database import server_db

CATEGORIES = ("voice_id", "own_track", "bleed", "your_mic", "unassigned")
QUOTA = {"voice_id": 10, "own_track": 6, "unassigned": 6, "your_mic": 5, "bleed": 3}   # of 30
RECENT_SESSIONS = 8          # lines are drawn from the latest sessions with a comms timeline
MIN_LINE_SEC = 0.8
MIN_WORDS = 2
PAD_SEC = 0.4
MAX_CLIP_SEC = 12.0
CANT_TELL = "Can't tell"
SOMEONE_ELSE = "Someone else"
TEXT_VERDICTS = ("right", "mostly", "wrong", "cant_hear")
DESCRIBE = {
    "your_mic": "from your mic",
    "bleed": "heard on your mic, credited to the teammate next to you",
    "own_track": "from their own recording",
    "voice_id": "named by their voice on Discord",
    "unassigned": "on Discord, not named",
}


def _category(item: dict, host: str, unknown: str) -> Optional[str]:
    src, who = item.get("source"), item.get("speaker") or unknown
    if src == "self":
        return "your_mic" if who == host else "bleed"
    if src == "voice":
        return "own_track"
    if src == "team":
        return "unassigned" if who == unknown else "voice_id"
    return None


def _host_name(meta: dict) -> str:
    roster = {str(k).lower(): v for k, v in (meta.get("roster") or {}).items()}
    me = str(meta.get("self_username") or "")
    return roster.get(me.lower(), me or "You")


def _sessions(conn, limit: int = RECENT_SESSIONS) -> list[dict]:
    rows = conn.execute(
        "SELECT c.session_id, c.audio_meta_json, c.timeline_json, s.map_name, s.created_at "
        "FROM session_comms c LEFT JOIN server_sessions s ON s.session_id = c.session_id "
        "WHERE c.timeline_json IS NOT NULL ORDER BY COALESCE(c.window_start, 0) DESC, s.created_at DESC LIMIT ?",
        (limit,)).fetchall()
    return [dict(r) for r in rows]


_TRACKS: dict[str, set[str]] = {}


def _tracks(session_id: str) -> set[str]:
    """Which of the host's tracks a session can still play lines from ("team", "self"); remembered per session
    (finding the host's mic in an older session's package unpacks it once)."""
    if session_id not in _TRACKS:
        from server.services.comms_service import CommsService
        has = set()
        if next(iter((server_settings.COMMS_DIR / session_id).glob("team.*")), None) is not None:
            has.add("team")
        if CommsService._self_clip(session_id) is not None:
            has.add("self")
        _TRACKS[session_id] = has
    return _TRACKS[session_id]


def lines(limit_sessions: int = RECENT_SESSIONS) -> list[dict[str, Any]]:
    """Every reviewable line of the latest sessions: [{"key", "session_id", "map", "round", "t", "end",
    "clock", "category", "claimed", "text", "source"}]."""
    from analysis.comms_timeline import UNKNOWN_TEAM_SPEAKER
    out = []
    with server_db.get_connection() as conn:
        sessions = _sessions(conn, limit_sessions)
    for s in sessions:
        try:
            meta = json.loads(s["audio_meta_json"] or "{}")
            tl = json.loads(s["timeline_json"] or "{}")
        except ValueError:
            continue
        host = _host_name(meta)
        has = _tracks(s["session_id"])
        for rnd in tl.get("rounds") or []:
            for it in rnd.get("items") or []:
                if it.get("kind") != "speech":
                    continue
                text = (it.get("text") or "").strip()
                if float(it.get("end", it["t"])) - float(it["t"]) < MIN_LINE_SEC or len(text.split()) < MIN_WORDS:
                    continue
                cat = _category(it, host, UNKNOWN_TEAM_SPEAKER)
                if cat is None or ("self" if it.get("source") == "self" else "team") not in has:
                    continue                                  # nothing to play it from

                out.append({"key": f"{s['session_id']}|{rnd.get('round_number')}|{float(it['t']):.2f}",
                            "session_id": s["session_id"], "map": s.get("map_name") or "",
                            "round": rnd.get("round_number"), "t": float(it["t"]), "end": float(it.get("end", it["t"])),
                            "clock": it.get("clock", ""), "category": cat, "source": it.get("source"),
                            "claimed": "" if cat == "unassigned" else (it.get("speaker") or ""), "text": text})
    return out


def answered_keys() -> set[str]:
    with server_db.get_connection() as conn:
        return {r[0] for r in conn.execute("SELECT line_key FROM comms_review")}


def choices(extra: list[str] = ()) -> list[str]:
    """Who the host can say was speaking: the saved team (nicknames), anyone the lines name, then the two
    catch-alls."""
    names: list[str] = []
    try:
        from server import roster
        names = [(e.get("label") or e.get("username") or "").strip() for e in roster.get_all()]
    except Exception:
        names = []
    for n in extra:
        if n and n not in names:
            names.append(n)
    return [n for n in names if n] + [SOMEONE_ELSE, CANT_TELL]


def sample(n: int = 30, seed: Optional[int] = None) -> dict[str, Any]:
    """{"items": [...], "choices": [...], "left": {category: lines not yet answered}}: up to n lines nobody has
    marked yet, spread over the categories by QUOTA (a short category's places go to the others)."""
    rng = random.Random(seed)
    done = answered_keys()
    pool: dict[str, list[dict]] = collections.defaultdict(list)
    for ln in lines():
        if ln["key"] not in done:
            pool[ln["category"]].append(ln)
    total = sum(QUOTA.values())
    want = {c: round(QUOTA[c] * n / total) for c in CATEGORIES}
    picked: list[dict] = []
    for c in CATEGORIES:
        k = min(want[c], len(pool[c]))
        chosen = rng.sample(pool[c], k)
        picked += chosen
        pool[c] = [x for x in pool[c] if x not in chosen]
    rest = [x for c in CATEGORIES for x in pool[c]]
    if len(picked) < n and rest:
        picked += rng.sample(rest, min(n - len(picked), len(rest)))
    rng.shuffle(picked)
    items = [{**{k: x[k] for k in ("key", "map", "round", "clock", "category", "claimed", "text")},
              "duration": round(min(x["end"] - x["t"] + 2 * PAD_SEC, MAX_CLIP_SEC), 1),
              "how": DESCRIBE[x["category"]]} for x in picked[:n]]
    left = collections.Counter(x["category"] for c in CATEGORIES for x in pool[c])
    return {"items": items, "choices": choices(sorted({x["claimed"] for x in items if x["claimed"]})),
            "left": dict(left)}


def _find(key: str) -> Optional[tuple[dict, dict, dict]]:
    """(the line, its round, the session's audio meta) for a key, or None."""
    try:
        session_id, rn, t = key.rsplit("|", 2)
        t_val = float(t)
    except ValueError:
        return None
    with server_db.get_connection() as conn:
        row = conn.execute("SELECT audio_meta_json, timeline_json FROM session_comms WHERE session_id = ?",
                           (session_id,)).fetchone()
    if not row or not row["timeline_json"]:
        return None
    tl = json.loads(row["timeline_json"])
    for rnd in tl.get("rounds") or []:
        if str(rnd.get("round_number")) != rn:
            continue
        for it in rnd.get("items") or []:
            if it.get("kind") == "speech" and abs(float(it["t"]) - t_val) < 0.006:
                return it, rnd, json.loads(row["audio_meta_json"] or "{}")
    return None


def clip(key: str) -> Optional[bytes]:
    """The line's audio as a 16 kHz mono WAV, a little padding either side."""
    found = _find(key)
    if not found:
        return None
    item, rnd, meta = found
    session_id = key.rsplit("|", 2)[0]
    from server.services.comms_service import ClipClock, CommsService
    if item.get("source") == "self":
        path = CommsService._self_clip(session_id)          # sessions before 2026-10-08 keep it only in the package
    else:
        path = next(iter(sorted((server_settings.COMMS_DIR / session_id).glob("team.*"))), None)
    if path is None:
        return None
    from integration.whisper_transcriber import _find_ffmpeg
    clock = ClipClock(meta.get("pieces") or [[float(rnd["start_epoch"]), 0.0]])
    start = clock.offset(float(rnd["start_epoch"]) + float(item["t"])) - PAD_SEC
    length = min(float(item.get("end", item["t"])) - float(item["t"]) + 2 * PAD_SEC, MAX_CLIP_SEC)
    if start + length <= 0:
        return None
    # Raw samples, with the WAV header written here: ffmpeg writing WAV to a pipe can't go back to fill in the
    # length, and some phone browsers won't play a file whose header says it is 37 hours long.
    cmd = [_find_ffmpeg(), "-v", "quiet", "-ss", f"{max(0.0, start):.3f}", "-i", str(path), "-t", f"{length:.3f}",
           "-ac", "1", "-ar", "16000", "-f", "s16le", "-"]
    try:
        pcm = subprocess.run(cmd, capture_output=True, timeout=30).stdout
    except (OSError, subprocess.SubprocessError):
        return None
    if len(pcm) < 2:
        return None
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1); w.setsampwidth(2); w.setframerate(16000)
        w.writeframes(pcm[:len(pcm) // 2 * 2])
    return buf.getvalue()


def record(key: str, heard: str, text_verdict: str) -> dict[str, Any]:
    """Saves one answer (a second answer for the same line replaces the first)."""
    if text_verdict not in TEXT_VERDICTS:
        raise ValueError(f"text must be one of {', '.join(TEXT_VERDICTS)}")
    heard = (heard or "").strip()[:60]
    if not heard:
        raise ValueError("say who was speaking (or Can't tell)")
    found = _find(key)
    if not found:
        raise ValueError("that line is no longer in the timeline")
    item, _rnd, meta = found
    from analysis.comms_timeline import UNKNOWN_TEAM_SPEAKER
    cat = _category(item, _host_name(meta), UNKNOWN_TEAM_SPEAKER) or "unknown"
    claimed = "" if cat == "unassigned" else (item.get("speaker") or "")
    with server_db.get_connection() as conn:
        conn.execute("INSERT OR REPLACE INTO comms_review (line_key, session_id, category, claimed, heard, text_verdict, "
                     "line_text, answered_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                     (key, key.rsplit("|", 2)[0], cat, claimed, heard, text_verdict, (item.get("text") or "")[:500],
                      datetime.now(timezone.utc).isoformat()))
        conn.commit()
    return {"ok": True, "answered": len(answered_keys())}


def summary() -> dict[str, Any]:
    """What the answers say: per category how often the name was right, what unnamed lines really were, the
    mix-ups, and how often the words were right."""
    with server_db.get_connection() as conn:
        rows = [dict(r) for r in conn.execute("SELECT * FROM comms_review")]
    per: dict[str, dict[str, Any]] = {}
    mixups: collections.Counter = collections.Counter()
    for c in CATEGORIES:
        rs = [r for r in rows if r["category"] == c]
        judged = [r for r in rs if r["heard"] != CANT_TELL]
        entry: dict[str, Any] = {"answered": len(rs), "how": DESCRIBE[c],
                                 "text": dict(collections.Counter(r["text_verdict"] for r in rs))}
        if c == "unassigned":
            entry["really"] = dict(collections.Counter(r["heard"] for r in rs).most_common())
        else:
            right = sum(1 for r in judged if r["heard"].lower() == r["claimed"].lower())
            entry.update(judged=len(judged), right=right, accuracy=round(right / len(judged), 3) if judged else None)
            mixups.update((r["claimed"], r["heard"]) for r in judged if r["heard"].lower() != r["claimed"].lower())
        per[c] = entry
    named = [r for r in rows if r["category"] != "unassigned" and r["heard"] != CANT_TELL]
    right = sum(1 for r in named if r["heard"].lower() == r["claimed"].lower())
    heard_text = [r for r in rows if r["text_verdict"] != "cant_hear"]
    return {"answered": len(rows),
            "named_accuracy": round(right / len(named), 3) if named else None, "named_judged": len(named),
            "text_right_or_mostly": (round(sum(1 for r in heard_text if r["text_verdict"] in ("right", "mostly"))
                                           / len(heard_text), 3) if heard_text else None),
            "text_judged": len(heard_text),
            "categories": per,
            "mixups": [{"named": a, "really": b, "times": k} for (a, b), k in mixups.most_common(10)]}
