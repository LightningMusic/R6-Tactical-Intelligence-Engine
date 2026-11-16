"""voice_id.enroll_from_clip: the host-voice enrolment path (their own mic track)."""
import numpy as np

from server import voice_id


def test_enroll_from_clip_picks_long_enough_segments_and_enrolls(monkeypatch):
    captured = {}
    monkeypatch.setattr(voice_id, "enroll",
                        lambda user, embs, secs: captured.update(user=user, n=len(embs), secs=secs))
    # stand-in encoder: a clip's embedding is just its length (varies per segment)
    voice_id.set_encoder(lambda wav: np.full(16, float(len(wav))))
    try:
        sr = voice_id.SR
        wav = np.ones(sr * 20, dtype=np.float32)
        segs = [
            {"start": 0.0, "end": 2.0},    # 2.0s  -> kept
            {"start": 3.0, "end": 3.5},    # 0.5s  -> dropped (< MIN_SEG_SEC)
            {"start": 5.0, "end": 9.0},    # 4.0s  -> kept
        ]
        n = voice_id.enroll_from_clip(wav, segs, "HostUser", max_n=10)
        assert n == 2
        assert captured["user"] == "HostUser"
        assert captured["n"] == 2
        assert captured["secs"] == 6.0       # 2.0 + 4.0
    finally:
        voice_id.set_encoder(None)


def test_enroll_from_clip_no_segments_is_a_noop(monkeypatch):
    called = {"enroll": False}
    monkeypatch.setattr(voice_id, "enroll", lambda *a, **k: called.update(enroll=True))
    assert voice_id.enroll_from_clip(np.ones(16000, dtype=np.float32), [], "HostUser") == 0
    assert called["enroll"] is False


def test_enroll_from_clip_caps_segment_length_at_max(monkeypatch):
    seen = {}
    monkeypatch.setattr(voice_id, "enroll",
                        lambda user, embs, secs: seen.update(secs=secs, n=len(embs)))
    voice_id.set_encoder(lambda wav: np.full(16, float(len(wav))))
    try:
        sr = voice_id.SR
        wav = np.ones(sr * 60, dtype=np.float32)
        segs = [{"start": 0.0, "end": 40.0}]          # 40s, capped to MAX_SEG_SEC
        n = voice_id.enroll_from_clip(wav, segs, "HostUser")
        assert n == 1
        assert seen["secs"] == voice_id.MAX_SEG_SEC
    finally:
        voice_id.set_encoder(None)
