"""
Milestone 4, phase 1: unit tests for the pure decision function that
decides whether end_session() should skip local Whisper transcription and
let the server's own job worker handle it instead.

Kept independent of the full SessionManager/end_session() call (which has
many heavyweight dependencies — RecImporter, DB repositories, Discord
capture, etc. — none of which are exercised by this decision) so this
logic gets direct, fast, dependency-free coverage.
"""

from app.session_manager import should_defer_transcription_to_server


def test_local_mode_never_defers():
    defer, reason = should_defer_transcription_to_server(
        analysis_mode="local",
        upload_voice=True,
        server_url="https://example.com",
        server_reachable=True,
        fallback_to_local_analysis=True,
    )
    assert defer is False
    assert "local" in reason.lower()


def test_voice_upload_off_runs_locally_even_if_reachable():
    defer, reason = should_defer_transcription_to_server(
        analysis_mode="remote",
        upload_voice=False,
        server_url="https://example.com",
        server_reachable=True,
        fallback_to_local_analysis=True,
    )
    assert defer is False
    assert "voice" in reason.lower() or "audio" in reason.lower()


def test_no_server_url_runs_locally():
    defer, reason = should_defer_transcription_to_server(
        analysis_mode="automatic",
        upload_voice=True,
        server_url="",
        server_reachable=False,
        fallback_to_local_analysis=True,
    )
    assert defer is False


def test_reachable_server_defers():
    defer, reason = should_defer_transcription_to_server(
        analysis_mode="remote",
        upload_voice=True,
        server_url="https://example.com",
        server_reachable=True,
        fallback_to_local_analysis=True,
    )
    assert defer is True
    assert "reachable" in reason.lower()


def test_unreachable_server_falls_back_to_local_by_default():
    defer, reason = should_defer_transcription_to_server(
        analysis_mode="automatic",
        upload_voice=True,
        server_url="https://example.com",
        server_reachable=False,
        fallback_to_local_analysis=True,
    )
    assert defer is False
    assert "not reachable" in reason.lower()


def test_unreachable_server_queues_instead_when_fallback_disabled():
    defer, reason = should_defer_transcription_to_server(
        analysis_mode="automatic",
        upload_voice=True,
        server_url="https://example.com",
        server_reachable=False,
        fallback_to_local_analysis=False,
    )
    assert defer is True
    assert "queued" in reason.lower() or "off" in reason.lower()
