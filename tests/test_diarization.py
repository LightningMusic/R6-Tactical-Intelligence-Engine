"""
Milestone 6 — speaker diarization.

Covers both diarization paths on WhisperTranscriber:
  * _diarize_by_silence_gaps  — the original, dependency-free heuristic,
    kept as the always-available fallback.
  * _cluster_segments_by_voice / diarize_speakers(audio_path=...) — the
    new resemblyzer-based voice clustering, which actually groups
    segments by who's talking instead of just how long the pause was.

The voice-clustering tests are skipped (not failed) when resemblyzer
isn't installed, since it's an optional dependency (see requirements.txt)
— diarize_speakers() must still work perfectly well without it.
"""
import wave
import struct
import math

import pytest

from integration.whisper_transcriber import WhisperTranscriber


def _segments(pairs):
    """[(start, end, text), ...] -> Whisper-shaped segment dicts."""
    return [{"start": s, "end": e, "text": t} for s, e, t in pairs]


# =====================================================
# Silence-gap fallback (no audio_path)
# =====================================================

class TestSilenceGapFallback:
    def test_empty_segments_returns_empty(self):
        t = WhisperTranscriber()
        assert t.diarize_speakers([]) == {}

    def test_no_audio_path_uses_silence_gap_heuristic(self):
        t = WhisperTranscriber()
        segs = _segments([
            (0.0, 1.0, "hello there team"),
            (1.2, 2.0, "rotating to site"),      # small gap — same speaker
            (5.0, 6.0, "enemy spotted north"),     # big gap — new speaker
        ])
        result = t.diarize_speakers(segs, n_speakers=5)
        assert set(result.keys()) == {"Speaker_1", "Speaker_2"}
        assert result["Speaker_1"]["word_count"] == 6  # first two segments combined
        assert result["Speaker_2"]["word_count"] == 3

    def test_round_robins_through_n_speakers(self):
        t = WhisperTranscriber()
        # Five long gaps in a row should cycle back to Speaker_1 on the 6th.
        segs = _segments([(i * 10.0, i * 10.0 + 1.0, f"line {i}") for i in range(6)])
        result = t._diarize_by_silence_gaps(segs, n_speakers=5)
        assert len(result) == 5
        assert "Speaker_1" in result and "Speaker_5" in result

    def test_top_words_excludes_stop_words(self):
        t = WhisperTranscriber()
        segs = _segments([(0.0, 1.0, "the enemy is planting the defuser now")])
        result = t.diarize_speakers(segs)
        top = result["Speaker_1"]["top_words"]
        assert "enemy" in top or "planting" in top or "defuser" in top
        assert "the" not in top and "is" not in top

    def test_audio_path_missing_file_falls_back_without_crashing(self, tmp_path):
        t = WhisperTranscriber()
        segs = _segments([(0.0, 1.0, "hello"), (5.0, 6.0, "world")])
        missing = tmp_path / "does_not_exist.wav"
        result = t.diarize_speakers(segs, n_speakers=5, audio_path=missing)
        # Falls back to the silence-gap heuristic — same result as no audio_path
        assert set(result.keys()) == {"Speaker_1", "Speaker_2"}


# =====================================================
# Voice clustering (requires resemblyzer + scikit-learn)
# =====================================================

def _write_tone_wav(path, freq_hz, duration_sec, sr=16000, amplitude=0.5):
    """A pure sine tone — not real speech, but a distinct, reproducible
    'voiceprint' input that lets us exercise the embed+cluster pipeline
    mechanically without needing a real recorded voice sample."""
    n_samples = int(duration_sec * sr)
    with wave.open(str(path), "w") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sr)
        frames = bytearray()
        for i in range(n_samples):
            val = int(amplitude * 32767 * math.sin(2 * math.pi * freq_hz * i / sr))
            frames += struct.pack("<h", val)
        wf.writeframes(bytes(frames))


class TestVoiceClustering:
    def test_cluster_returns_none_below_dependency_or_data_floor(self, tmp_path):
        pytest.importorskip("resemblyzer")
        t = WhisperTranscriber()
        # Only one usable segment — nothing to cluster against.
        segs = _segments([(0.0, 1.0, "hello")])
        wav_path = tmp_path / "one_speaker.wav"
        _write_tone_wav(wav_path, 220.0, 2.0)
        assert t._cluster_segments_by_voice(segs, wav_path, n_speakers=5) is None

    def test_cluster_end_to_end_shape(self, tmp_path):
        pytest.importorskip("resemblyzer")
        pytest.importorskip("sklearn")
        t = WhisperTranscriber()

        wav_path = tmp_path / "two_tones.wav"
        # Two clearly different tones back to back — a stand-in for two
        # different voices so the clustering machinery has *something*
        # distinguishable to key off of.
        n_samples = 16000 * 6
        with wave.open(str(wav_path), "w") as wf:
            wf.setnchannels(1)
            wf.setsampwidth(2)
            wf.setframerate(16000)
            frames = bytearray()
            for i in range(n_samples):
                t_sec = i / 16000
                freq = 220.0 if t_sec < 3.0 else 440.0
                val = int(0.5 * 32767 * math.sin(2 * math.pi * freq * i / 16000))
                frames += struct.pack("<h", val)
            wf.writeframes(bytes(frames))

        segs = _segments([
            (0.0, 1.4, "first tone segment one"),
            (1.5, 2.9, "first tone segment two"),
            (3.1, 4.4, "second tone segment one"),
            (4.5, 5.9, "second tone segment two"),
        ])

        result = t.diarize_speakers(segs, n_speakers=5, audio_path=wav_path)

        # Structural guarantees regardless of how the (non-speech) audio
        # happens to cluster: valid Speaker_N keys, all segments accounted
        # for, and the normal word-count/top-words shape intact.
        assert len(result) >= 1
        total_segments = sum(len(v["segments"]) for v in result.values())
        assert total_segments == 4
        for name, data in result.items():
            assert name.startswith("Speaker_")
            assert "word_count" in data and "talk_time" in data and "top_words" in data
