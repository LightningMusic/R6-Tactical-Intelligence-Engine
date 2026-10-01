"""
Lining a teammate's own mic up with the host's Discord track. Synthetic
"speech" (bursts of band-limited noise with pauses, like talking) stands in
for real voices; the Discord copy is delayed, drifts, is filtered and has
other noise on it, the way it would after going through Discord.
(Checked against real audio from the 2026-09-29 recording as well: 10 min,
2.37 s shift + 40 ppm drift recovered to within 15 ms.)
"""
import numpy as np
import pytest

from analysis.voice_align import align, keep_voiced_segments, speech_regions

SR = 16000


def talking(seconds: float, seed: int) -> np.ndarray:
    rng = np.random.default_rng(seed)
    x = np.zeros(int(seconds * SR), dtype=np.float32)
    t = 0.5
    while t < seconds - 2:
        dur = rng.uniform(0.3, 2.2)
        n0, n1 = int(t * SR), int(min(seconds, t + dur) * SR)
        burst = rng.standard_normal(n1 - n0).astype(np.float32)
        burst = np.convolve(burst, np.ones(4) / 4, mode="same")          # tame the top end
        envelope = np.sin(np.linspace(0, np.pi, n1 - n0)) ** 0.5
        x[n0:n1] += 0.3 * burst * envelope
        t += dur + rng.uniform(0.4, 4.0)
    return x


def through_discord(own: np.ndarray, delay: float, drift: float, seed: int) -> np.ndarray:
    """What the host's Discord track holds: the teammate's voice, later,
    drifting, low-passed, plus other people and noise."""
    n = len(own)
    t = np.arange(n) / SR
    src = (t - delay - drift * t) * SR                 # ref(t) = own(t - delay - drift*t)
    ref = np.interp(src, np.arange(n), own, left=0, right=0).astype(np.float32)
    ref = np.convolve(ref, np.ones(6) / 6, mode="same")
    others = talking(n / SR, seed + 100) * 0.8
    noise = np.random.default_rng(seed).standard_normal(n).astype(np.float32) * 0.01
    return (ref + others + noise).astype(np.float32)


@pytest.mark.parametrize("delay", [0.35, 2.4, -1.7, 48.0])
def test_recovers_the_offset(delay):
    own = talking(600, seed=1)
    ref = through_discord(own, delay, 0.0, seed=2)
    a = align(ref, own, SR)
    assert a is not None
    assert a.to_ref(300) - 300 == pytest.approx(delay, abs=0.06)


def test_follows_sound_card_drift():
    own = talking(900, seed=3)
    ref = through_discord(own, 1.2, 50e-6, seed=4)       # 50 ppm: 45 ms over 15 min
    a = align(ref, own, SR)
    assert a is not None
    for t in (60, 450, 840):
        assert a.to_ref(t) - t == pytest.approx(1.2 + 50e-6 * t, abs=0.06)


@pytest.mark.parametrize("seed", [0, 1, 2])
def test_finds_one_voice_among_four_on_discord(seed):
    """A full squad: the teammate (quieter than the rest) plus three others
    talking over the same Discord track. A single loudness envelope never
    found the offset here; the band-by-band match has to."""
    rng = np.random.default_rng(seed)
    length = 1200
    own = talking(length, seed=100 + seed) * 0.6
    delay = float(rng.uniform(-3, 3))
    ref = through_discord(own, delay, 0.0, seed=200 + seed)
    for k in range(2):
        ref = ref + talking(length, seed=500 + 10 * seed + k)
    a = align(ref.astype(np.float32), own, SR)
    assert a is not None
    assert a.to_ref(600) - 600 == pytest.approx(delay, abs=0.06)
    assert a.verified >= 0.5


def test_unrelated_audio_is_not_forced_into_a_match():
    assert align(talking(600, seed=5), talking(600, seed=6), SR) is None


def test_silence_does_not_align():
    assert align(talking(300, seed=7), np.zeros(300 * SR, dtype=np.float32), SR) is None


def test_speech_regions_and_whisper_filter():
    x = np.zeros(10 * SR, dtype=np.float32)
    rng = np.random.default_rng(0)
    x[2 * SR:4 * SR] = rng.standard_normal(2 * SR) * 0.3
    x[7 * SR:8 * SR] = rng.standard_normal(SR) * 0.3
    regions = speech_regions(x, SR)
    assert len(regions) == 2
    assert regions[0][0] == pytest.approx(2.0, abs=0.3) and regions[0][1] == pytest.approx(4.0, abs=0.3)
    segs = [{"start": 2.1, "end": 3.9, "text": "real"}, {"start": 5.0, "end": 6.5, "text": "Thank you."}]
    assert [s["text"] for s in keep_voiced_segments(segs, regions)] == ["real"]
