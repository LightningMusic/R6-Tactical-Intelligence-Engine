"""
Milestone 4, phase 1: server-side transcription pipeline
(SessionProcessingService._maybe_transcribe), scoped to a single
already-extracted job's work directory.

The client (app/session_manager.py) already slices the uploaded audio down
to this match's own window before it's ever bundled, against the real OBS
recording and the real session start time — the server transcribes that
clip directly and does not re-align or re-crop it. An earlier version of
this method did try to re-crop it via TimelineAligner, using a "session
start" derived from the clip's own random temp filename; that could never
work (the filename never matches what TimelineAligner looks for) and was
producing empty transcripts for every real session, fixed 2026-09-21.

WhisperTranscriber/TranscriptParser are mocked out — they're already
covered by their own tests, and neither (nor a real Whisper model) is
available in this environment. What's under test here is the plumbing:
does the service find the audio, call the pipeline, store the result, and
— critically — never let a transcription failure propagate and fail the
whole job.
"""

import json
from pathlib import Path

import pytest

import server.services.session_processing as session_processing_module
from server.services.session_processing import SessionProcessingService


@pytest.fixture
def work_dir(tmp_path: Path) -> Path:
    d = tmp_path / "work"
    (d / "audio").mkdir(parents=True)
    (d / "replays").mkdir(parents=True)
    (d / "audio" / "2026-04-27 16-38-48.mp4").write_bytes(b"fake-audio")
    (d / "replays" / "Match-01.rec").write_bytes(b"fake-rec")
    return d


class _FakeTranscriber:
    def __init__(self, model_path=None, model_size=None) -> None:
        self.model_path = model_path
        self.model_size = model_size

    def transcribe_full(self, audio_path):
        return {"text": "push site now", "segments": [{"text": "push site now", "start": 0, "end": 1}]}


class _FakeParsedTranscript:
    pass


class _FakeParser:
    def parse_segments_list(self, segments, match_id=None):
        return _FakeParsedTranscript()

    def to_storage_dict(self, parsed):
        return {"location_freq": {}, "action_freq": {"push": 1}}


def _patch_pipeline(monkeypatch):
    monkeypatch.setattr(
        "integration.whisper_transcriber.WhisperTranscriber", _FakeTranscriber
    )
    monkeypatch.setattr(
        "analysis.transcript_parser.TranscriptParser", _FakeParser
    )


def test_transcribes_and_saves_when_audio_present(monkeypatch, work_dir):
    _patch_pipeline(monkeypatch)

    saved = {}

    class _FakeRepo:
        def save_transcript(self, session_id, raw_text, processed_segments_json, word_count):
            saved["session_id"] = session_id
            saved["raw_text"] = raw_text
            saved["processed_segments_json"] = processed_segments_json
            saved["word_count"] = word_count

    monkeypatch.setattr(session_processing_module, "ServerRepository", _FakeRepo)

    SessionProcessingService._maybe_transcribe(work_dir, "session_abc123")

    assert saved["session_id"] == "session_abc123"
    assert saved["raw_text"] == "push site now"
    assert saved["word_count"] == 3
    # No re-cropping: transcribe_full()'s own output is what gets saved.
    assert json.loads(saved["processed_segments_json"])["action_freq"] == {"push": 1}


def test_no_audio_directory_is_a_silent_noop(tmp_path: Path, monkeypatch):
    _patch_pipeline(monkeypatch)
    calls = []
    monkeypatch.setattr(
        session_processing_module,
        "ServerRepository",
        lambda: pytest.fail("ServerRepository should not be constructed when there is no audio"),
    )

    empty_work_dir = tmp_path / "work_no_audio"
    empty_work_dir.mkdir()

    # Must not raise.
    SessionProcessingService._maybe_transcribe(empty_work_dir, "session_xyz")


def test_transcription_failure_does_not_raise(monkeypatch, work_dir):
    class _BrokenTranscriber:
        def __init__(self, model_path=None, model_size=None) -> None:
            pass

        def transcribe_full(self, audio_path):
            raise RuntimeError("whisper model not found")

    monkeypatch.setattr(
        "integration.whisper_transcriber.WhisperTranscriber", _BrokenTranscriber
    )

    # Must not raise — a transcription failure shouldn't fail the whole job.
    SessionProcessingService._maybe_transcribe(work_dir, "session_will_fail")
