"""
Voice recognition for the Discord track: who is talking when nobody recorded them.

LEARNING. A teammate who records (R6Companion or the browser page) is lined up
against the host's Discord track. The stretches of that track where their own
mic shows them speaking are, by construction, THEIR voice as Discord delivers
it (codec, noise suppression and all). Those stretches are embedded with
Resemblyzer (a small d-vector model that ships inside the image, no downloads)
and averaged into one profile per in-game name.

RECOGNISING. Lines on the Discord track that nobody recorded are matched
against the profiles and named only when the match is clear (a high score AND a
clear gap to the runner-up). Anything else stays "Team (unassigned)", so a wrong
name is much rarer than a missing one.

Conservative on purpose: the thresholds below are starting values that have not
been tuned on real recordings yet. R6_VOICE_ID=0 switches the whole thing off.
"""
from __future__ import annotations

import json
import os
import threading
import time
from pathlib import Path
from typing import Any, Callable, Iterable, Optional

import numpy as np

from server.database import server_db

SR = 16000
MIN_SEG_SEC = 1.0           # shorter than this carries too little of a voice
MAX_SEG_SEC = 12.0
MATCH_MIN = 0.75            # cosine similarity needed to name a voice
MARGIN_MIN = 0.05           # ...and the lead it must have over the next-best profile
MIN_PROFILE_SAMPLES = 5     # a profile built from fewer stretches than this is not used
PROFILE_CAP = 300           # old samples stop outweighing new ones beyond this many
MAX_ENROLL_PER_SESSION = 40

_encoder_fn: Optional[Callable[[np.ndarray], Optional[np.ndarray]]] = None
_encoder_lock = threading.Lock()


def enabled() -> bool:
    return os.environ.get("R6_VOICE_ID", "1").strip().lower() not in ("0", "false", "off", "no")


# ── the embedding model ──────────────────────────────────────────────────

def set_encoder(fn: Optional[Callable[[np.ndarray], Optional[np.ndarray]]]) -> None:
    """Swap the embedding function (tests use a stand-in; None restores Resemblyzer)."""
    global _encoder_fn
    _encoder_fn = fn


def _resemblyzer_embed(wav: np.ndarray) -> Optional[np.ndarray]:
    global _encoder_obj
    from resemblyzer import VoiceEncoder, preprocess_wav
    with _encoder_lock:
        if _encoder_obj is None:
            _encoder_obj = VoiceEncoder("cpu")
        enc = _encoder_obj
    wav = preprocess_wav(wav.astype(np.float32), source_sr=SR)
    if len(wav) < int(SR * 0.5):
        return None
    return np.asarray(enc.embed_utterance(wav), dtype=np.float64)


_encoder_obj: Any = None


def embed(wav: np.ndarray) -> Optional[np.ndarray]:
    try:
        fn = _encoder_fn or _resemblyzer_embed
        v = fn(wav)
        return None if v is None else np.asarray(v, dtype=np.float64)
    except Exception as e:
        print(f"[VoiceID] could not embed a clip ({type(e).__name__}: {str(e)[:80]})")
        return None


# ── profiles ─────────────────────────────────────────────────────────────

def _unit(v: np.ndarray) -> np.ndarray:
    n = float(np.linalg.norm(v))
    return v / n if n > 0 else v


def load_profiles(min_samples: int = MIN_PROFILE_SAMPLES) -> dict[str, np.ndarray]:
    with server_db.get_connection() as conn:
        rows = conn.execute("SELECT username, embedding_json, n_samples FROM voice_profiles").fetchall()
    return {r["username"]: _unit(np.asarray(json.loads(r["embedding_json"]), dtype=np.float64))
            for r in rows if int(r["n_samples"]) >= min_samples}


def list_profiles() -> list[dict]:
    with server_db.get_connection() as conn:
        rows = conn.execute("SELECT username, n_samples, seconds, updated_at FROM voice_profiles "
                            "ORDER BY n_samples DESC").fetchall()
    return [{"username": r["username"], "samples": int(r["n_samples"]), "seconds": round(float(r["seconds"]), 1),
             "usable": int(r["n_samples"]) >= MIN_PROFILE_SAMPLES, "updated_at": r["updated_at"]} for r in rows]


def delete_profile(username: str) -> bool:
    with server_db.get_connection() as conn:
        cur = conn.execute("DELETE FROM voice_profiles WHERE lower(username) = lower(?)", (username,))
        conn.commit()
        return cur.rowcount > 0


def enroll(username: str, embeddings: list[np.ndarray], seconds: float) -> None:
    """Fold new embeddings into a person's running-mean profile."""
    embeddings = [np.asarray(e, dtype=np.float64) for e in embeddings if e is not None]
    if not embeddings:
        return
    new_sum = np.sum([_unit(e) for e in embeddings], axis=0)
    with server_db.get_connection() as conn:
        row = conn.execute("SELECT embedding_json, n_samples, seconds FROM voice_profiles WHERE username = ?",
                           (username,)).fetchone()
        if row:
            n_old = min(int(row["n_samples"]), PROFILE_CAP)
            mean = (np.asarray(json.loads(row["embedding_json"]), dtype=np.float64) * n_old + new_sum) \
                / (n_old + len(embeddings))
            n_total, secs = int(row["n_samples"]) + len(embeddings), float(row["seconds"]) + seconds
        else:
            mean, n_total, secs = new_sum / len(embeddings), len(embeddings), seconds
        conn.execute(
            """INSERT INTO voice_profiles (username, embedding_json, n_samples, seconds, updated_at)
               VALUES (?, ?, ?, ?, ?)
               ON CONFLICT(username) DO UPDATE SET embedding_json=excluded.embedding_json,
                 n_samples=excluded.n_samples, seconds=excluded.seconds, updated_at=excluded.updated_at""",
            (username, json.dumps(mean.tolist()), n_total, secs, time.strftime("%Y-%m-%dT%H:%M:%S")),
        )
        conn.commit()


def identify(emb: np.ndarray, profiles: dict[str, np.ndarray],
             exclude: Iterable[str] = ()) -> Optional[tuple[str, float]]:
    """(username, score) when one profile clearly owns this voice, else None."""
    skip = {e.lower() for e in exclude}
    e = _unit(np.asarray(emb, dtype=np.float64))
    scored = sorted(((float(np.dot(e, p)), name) for name, p in profiles.items() if name.lower() not in skip),
                    reverse=True)
    if not scored or scored[0][0] < MATCH_MIN:
        return None
    if len(scored) > 1 and scored[0][0] - scored[1][0] < MARGIN_MIN:
        return None
    return scored[0][1], scored[0][0]


# ── learning from a verified alignment ───────────────────────────────────

def enroll_from_alignment(ref: np.ndarray, to_ref: Callable[[float], float], segments: list[dict],
                          username: str) -> int:
    """`segments` are lines the teammate said (times on THEIR recording) that were
    verified audible on the Discord track `ref`; `to_ref` converts those times to
    Discord-track time. Returns how many stretches were learned from."""
    if not enabled() or not segments:
        return 0
    picks = sorted(segments, key=lambda s: float(s["end"]) - float(s["start"]), reverse=True)[:MAX_ENROLL_PER_SESSION]
    embs, secs = [], 0.0
    for s in picks:
        r0, r1 = to_ref(float(s["start"])), to_ref(float(s["end"]))
        if r1 - r0 < MIN_SEG_SEC:
            continue
        r1 = min(r1, r0 + MAX_SEG_SEC)
        clip = ref[max(0, int(r0 * SR)):int(r1 * SR)]
        if len(clip) < int(MIN_SEG_SEC * SR):
            continue
        v = embed(clip)
        if v is not None:
            embs.append(v)
            secs += len(clip) / SR
    if embs:
        enroll(username, embs, secs)
        print(f"[VoiceID] learned {username}'s voice from {len(embs)} stretch(es) of the Discord track.")
    return len(embs)


# ── naming the unassigned lines ──────────────────────────────────────────

def label_team_lines(utterances: list[dict], ref: np.ndarray, seconds_in_clip: Callable[[float], float],
                     unknown_speaker: str, exclude_usernames: Iterable[str],
                     display: dict[str, str], cache_path: Optional[Path] = None) -> tuple[list[dict], dict]:
    """Returns (utterances with recognised voices named, stats). Never raises."""
    stats = {"profiles": 0, "considered": 0, "labelled": 0}
    if not enabled() or not utterances:
        return utterances, stats
    try:
        profiles = load_profiles()
        stats["profiles"] = len(profiles)
        if not profiles:
            return utterances, stats
        cache: dict[str, Any] = {}
        if cache_path and cache_path.exists():
            try:
                cache = json.loads(cache_path.read_text(encoding="utf-8"))
            except Exception:
                cache = {}
        dirty = False
        out = []
        for u in utterances:
            if u.get("speaker") != unknown_speaker or u.get("source") != "team":
                out.append(u)
                continue
            t0, t1 = seconds_in_clip(float(u["start"])), seconds_in_clip(float(u["end"]))
            if t1 - t0 < MIN_SEG_SEC or t0 < 0:
                out.append(u)
                continue
            stats["considered"] += 1
            key = f"{t0:.2f}-{t1:.2f}"
            if key in cache:
                emb = None if cache[key] is None else np.asarray(cache[key], dtype=np.float64)
            else:
                clip = ref[int(t0 * SR):int(min(t1, t0 + MAX_SEG_SEC) * SR)]
                emb = embed(clip) if len(clip) >= int(MIN_SEG_SEC * SR) else None
                cache[key] = None if emb is None else [round(float(x), 5) for x in emb]
                dirty = True
            hit = identify(emb, profiles, exclude_usernames) if emb is not None else None
            if hit is None:
                out.append(u)
                continue
            name, score = hit
            stats["labelled"] += 1
            out.append({**u, "speaker": display.get(name.lower(), name), "username": name, "voice_id": round(score, 2)})
        if dirty and cache_path:
            try:
                cache_path.write_text(json.dumps(cache), encoding="utf-8")
            except OSError:
                pass
        return out, stats
    except Exception as e:
        print(f"[VoiceID] skipped ({type(e).__name__}: {str(e)[:100]})")
        return utterances, stats
