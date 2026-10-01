"""
Lining a teammate's own mic recording up with the host's Discord track.

Every teammate's voice is in both: raw on their own recording, and (a few
hundred ms later, squeezed through Discord's codec and noise suppression) in
the host's Discord track, mixed with everyone else. The waveforms don't
match sample-for-sample after that, and with three other people talking the
overall loudness of the Discord track is mostly *theirs* -- a single speech
envelope failed to align every time in a 4-voice test mix. What survives is
the spectro-temporal pattern of this person's words: which frequency bands
light up, and when. So both signals become 32-band log spectrograms (10 ms
steps); the teammate's is kept only where they're actually talking; and the
two are cross-correlated band by band (FFT), summed. The peak is the offset.

A candidate offset is then *verified*: at that offset, most of the
teammate's individual lines should visibly show up in the Discord track
(heard_on_ref). A true offset passes nearly all of them, a chance peak
almost none -- which is what makes it safe to say "not in this match".

The two PCs' clocks put the recordings roughly in place (usually within a
second or two when Windows time sync is on), so the search is narrow first
and widens only if nothing convincing turns up. Sound cards also drift apart
slowly (tens of ms per half hour), so the offset is re-measured in windows
across the match and fitted as offset + drift.

numpy only -- this runs inside the frozen server build.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

import numpy as np

ENV_RATE = 100          # feature frames per second (10 ms)
_NFFT = 512
_BANDS = 32
_WIN_SEC = 0.025

MIN_Z = 4.0             # correlation peak, in standard deviations above the typical lag
MIN_VERIFIED = 0.35     # fraction of the teammate's lines that must show up at the offset


# ── Features ──────────────────────────────────────────────────────────────

def band_features(x: np.ndarray, sr: int) -> np.ndarray:
    """(frames, bands) log energy in ~_BANDS log-spaced bands up to 4 kHz,
    one frame per 10 ms."""
    x = np.asarray(x, dtype=np.float32)
    hop = sr // ENV_RATE
    n = 1 + (len(x) - _NFFT) // hop
    if n <= 0:
        return np.zeros((0, 1), dtype=np.float32)
    top = int(4000 * _NFFT / sr) + 1
    edges = np.unique(np.geomspace(4, top, _BANDS + 1).astype(int))
    win = np.hanning(_NFFT).astype(np.float32)
    out = np.empty((n, len(edges) - 1), dtype=np.float32)
    step = 20000
    for s in range(0, n, step):
        idx = np.arange(_NFFT)[None, :] + hop * np.arange(s, min(n, s + step))[:, None]
        power = np.abs(np.fft.rfft(x[idx] * win, axis=1)) ** 2
        cs = np.cumsum(power, axis=1)
        out[s:s + len(idx)] = np.log(cs[:, edges[1:] - 1] - cs[:, edges[:-1] - 1] + 1e-6)
    return out


def _normalise(F: np.ndarray, mask: Optional[np.ndarray] = None) -> np.ndarray:
    F = (F - np.median(F, axis=0)) / (F.std(axis=0) + 1e-6)
    return F * mask[:, None] if mask is not None else F


def _voiced(F: np.ndarray) -> np.ndarray:
    """Frames where a single-person mic is actually carrying speech."""
    e = F.mean(axis=1)
    return (e > np.percentile(e, 60)).astype(np.float32) if len(e) else np.zeros(0, np.float32)


def speech_envelope(x: np.ndarray, sr: int) -> np.ndarray:
    """Log energy of the pre-emphasised signal in 10 ms hops, with its slow
    background level removed and normalised -- used for voice-activity
    detection on a single person's mic."""
    x = np.asarray(x, dtype=np.float32)
    if x.ndim > 1:
        x = x.mean(axis=1)
    if len(x) < sr // 10:
        return np.zeros(0, dtype=np.float32)
    x = np.append(x[0], x[1:] - 0.97 * x[:-1])
    hop = sr // ENV_RATE
    win = max(hop, int(sr * _WIN_SEC))
    n = 1 + (len(x) - win) // hop
    if n <= 0:
        return np.zeros(0, dtype=np.float32)
    idx = np.arange(win)[None, :] + hop * np.arange(n)[:, None]
    frames = x[idx]
    e = np.log10(np.mean(frames * frames, axis=1) + 1e-10)
    block = 150
    pad = (-len(e)) % block
    floor = np.pad(e, (0, pad), mode="edge").reshape(-1, block).min(axis=1)
    floor = np.interp(np.arange(len(e)), np.arange(len(floor)) * block + block / 2, floor)
    env = np.clip(e - floor, 0, None)
    sd = env.std()
    return ((env - env.mean()) / sd).astype(np.float32) if sd > 1e-6 else np.zeros_like(env)


# ── Correlation ───────────────────────────────────────────────────────────

def _xcorr(R: np.ndarray, S: np.ndarray) -> tuple[np.ndarray, int]:
    """Band-summed cross-correlation; corr[zero + k] scores S placed k frames
    later in R."""
    n = len(R) + len(S) - 1
    size = 1 << (n - 1).bit_length()
    acc = np.zeros(size)
    for b in range(R.shape[1]):
        acc += np.fft.irfft(np.fft.rfft(R[:, b], size) * np.conj(np.fft.rfft(S[:, b], size)), size)
    corr = np.concatenate([acc[-(len(S) - 1):], acc[:len(R)]]) if len(S) > 1 else acc[:len(R)]
    return corr, len(S) - 1


def _best_lag(R: np.ndarray, S: np.ndarray, expected: float, max_lag: float,
              r_offset: int = 0) -> Optional[tuple[float, float]]:
    """(lag seconds, z) of the best lag in expected +/- max_lag. R may be a
    slice of the full reference starting r_offset frames in."""
    if len(R) < ENV_RATE * 5 or len(S) < ENV_RATE * 5:
        return None
    corr, zero = _xcorr(R, S)
    lags = np.arange(len(corr)) - zero + r_offset
    lo, hi = (expected - max_lag) * ENV_RATE, (expected + max_lag) * ENV_RATE
    sel = (lags >= lo) & (lags <= hi)
    if sel.sum() < 10:
        return None
    c, l = corr[sel], lags[sel]
    best = int(np.argmax(c))
    z = float((c[best] - np.median(c)) / (c.std() + 1e-9))
    return float(l[best]) / ENV_RATE, z


# ── Alignment ─────────────────────────────────────────────────────────────

@dataclass
class Alignment:
    offset_sec: float          # sig time t appears in ref at t + offset (at t = 0)
    drift_per_sec: float       # extra offset per second of sig
    confidence: float          # correlation peak z-score
    windows_used: int
    verified: float = 0.0      # share of the teammate's lines found in ref at this offset

    def to_ref(self, t_sig: float) -> float:
        return t_sig + self.offset_sec + self.drift_per_sec * t_sig


def align(ref: np.ndarray, sig: np.ndarray, sr: int,
          search_sec: tuple[float, ...] = (15.0, 120.0, 600.0),
          window_sec: float = 180.0) -> Optional[Alignment]:
    """
    Where does sig sit inside ref? Both are mono arrays at sr that nominally
    start at the same instant (per the two PCs' clocks). Returns None when no
    verified match exists -- e.g. the teammate was muted throughout, or
    wasn't in this match at all.
    """
    Fs = band_features(sig, sr)
    voiced = _voiced(Fs)
    if len(voiced) == 0 or voiced.sum() < ENV_RATE * 5:
        return None
    R = _normalise(band_features(ref, sr))
    S = _normalise(Fs, voiced)
    regions = speech_regions(sig, sr)

    for max_lag in search_sec:
        found = _best_lag(R, S, 0.0, max_lag)
        if not found or found[1] < MIN_Z:
            continue
        coarse = Alignment(found[0], 0.0, found[1], 0)
        refined = _refine(R, S, voiced, coarse, window_sec) or coarse
        refined.verified = _verify(ref, sig, sr, refined, regions)
        if refined.verified >= MIN_VERIFIED:
            return refined
    return None


def _refine(R, S, voiced, coarse: Alignment, window_sec: float) -> Optional[Alignment]:
    """Re-measure the offset in windows across the recording and fit offset +
    drift. Each window searches +/-20 s of reference and counts only if it
    agrees with the coarse answer to within 1.5 s."""
    w = int(window_sec * ENV_RATE)
    pts, confs = [], []
    for s in range(0, max(1, len(S) - w // 3), w):
        seg = S[s:s + w]
        if len(seg) < ENV_RATE * 20 or voiced[s:s + w].mean() < 0.05:
            continue
        exp = coarse.offset_sec + s / ENV_RATE           # where seg[0] should land in R, seconds
        r0 = max(0, int((exp - 20.0) * ENV_RATE))
        r1 = min(len(R), int((exp + 20.0) * ENV_RATE) + len(seg))
        found = _best_lag(R[r0:r1], seg, exp, 20.0, r_offset=r0)
        if found and found[1] >= MIN_Z - 0.5 and abs(found[0] - exp) <= 1.5:
            pts.append((s / ENV_RATE + len(seg) / ENV_RATE / 2, found[0] - s / ENV_RATE))
            confs.append(found[1])
    if len(pts) >= 3:
        t = np.array([p[0] for p in pts])
        o = np.array([p[1] for p in pts])
        drift, off = np.polyfit(t, o, 1)
        resid = np.abs(o - (off + drift * t))
        keep = resid <= max(0.05, np.percentile(resid, 75))
        if keep.sum() >= 3:
            drift, off = np.polyfit(t[keep], o[keep], 1)
        if abs(drift) < 5e-4:                       # >0.5 ms/s is not a sound card, it's a bad fit
            return Alignment(float(off), float(drift), float(np.median(confs)), int(keep.sum()))
    if pts:
        return Alignment(float(np.median([p[1] for p in pts])), 0.0, float(np.median(confs)), len(pts))
    return None


def _verify(ref, sig, sr, a: Alignment, regions, max_checks: int = 40) -> float:
    if not regions:
        return 0.0
    pick = regions if len(regions) <= max_checks else \
        [regions[i] for i in np.linspace(0, len(regions) - 1, max_checks).astype(int)]
    hits = sum(heard_on_ref(ref, sig, sr, r0, r1, a) >= HEARD_MIN_CORR for r0, r1 in pick)
    return hits / len(pick)


# ── Per-line checks ───────────────────────────────────────────────────────

HEARD_MIN_CORR = 0.15


def heard_on_ref(ref: np.ndarray, sig: np.ndarray, sr: int, t0: float, t1: float,
                 alignment: Alignment, pad: float = 0.15) -> float:
    """
    How strongly a stretch of sig (seconds t0..t1) shows up in ref at the
    aligned time: correlation (about -1..1) of the two band spectrograms over
    the frames where this person is loud on their own mic.

    "Did this go out over Discord?" can't be answered by "was anyone talking
    on Discord then" -- someone else often was -- nor by the loudness
    envelope alone, which someone else's speech mimics well enough. The
    band pattern of these exact words survives Discord's codec; someone
    else's voice doesn't match it. One 10 ms step of slack either way
    absorbs what's left of the alignment error.

    Synthetic 15-minute mixes, threshold 0.15 -- genuine lines kept vs lines
    said while muted that got through: with one other voice on Discord,
    ~95% vs ~1 in 20; with three, ~80-85% vs none. A genuine line that fails
    isn't lost: it's still in the host's Discord track, just unattributed.
    """
    a0 = max(0.0, t0 - pad)
    s = sig[int(a0 * sr):int((t1 + pad) * sr)]
    A = band_features(s, sr)
    if len(A) < 5:
        return 0.0
    energy = A.mean(axis=1)
    keep = energy >= energy.max() - 2.3                # ~10 dB below this line's loudest frame
    A = A[keep] - A[keep].mean(axis=0)
    start = int(round(alignment.to_ref(a0) * sr))
    hop = sr // ENV_RATE
    best = -1.0
    for shift in (-hop, 0, hop):
        st = start + shift
        if st < 0 or st + len(s) > len(ref):
            continue
        B = band_features(ref[st:st + len(s)], sr)[keep]
        B = B - B.mean(axis=0)
        den = np.sqrt((A * A).sum() * (B * B).sum())
        if den > 1e-9:
            best = max(best, float((A * B).sum() / den))
    return best


def speech_regions(x: np.ndarray, sr: int, min_sec: float = 0.25,
                   merge_gap_sec: float = 0.4, pad_sec: float = 0.2) -> list[tuple[float, float]]:
    """Where someone is talking on a single-person mic track, in seconds.
    Used to throw out Whisper's inventions on silence ("Thank you." over
    ten minutes of nothing) and to pick the lines to verify an alignment on."""
    env = speech_envelope(x, sr)
    if len(env) == 0:
        return []
    active = env > 0.6
    regions: list[list[float]] = []
    start = None
    for i, a in enumerate(np.append(active, False)):
        if a and start is None:
            start = i
        elif not a and start is not None:
            regions.append([start / ENV_RATE, i / ENV_RATE])
            start = None
    merged: list[list[float]] = []
    for r in regions:
        if merged and r[0] - merged[-1][1] <= merge_gap_sec:
            merged[-1][1] = r[1]
        else:
            merged.append(r)
    total = len(x) / sr
    return [(max(0.0, a - pad_sec), min(total, b + pad_sec))
            for a, b in merged if b - a >= min_sec]


def keep_voiced_segments(segments: list[dict], regions: list[tuple[float, float]],
                         min_fraction: float = 0.3) -> list[dict]:
    """Whisper segments that mostly sit on actual speech."""
    kept = []
    for s in segments:
        dur = max(1e-6, s["end"] - s["start"])
        voiced = sum(max(0.0, min(b, s["end"]) - max(a, s["start"])) for a, b in regions)
        if voiced / dur >= min_fraction:
            kept.append(s)
    return kept
