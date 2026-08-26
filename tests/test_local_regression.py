import pytest
from pathlib import Path
from unittest.mock import MagicMock

from app.session_manager import SessionManager
from integration.rec_importer import RecImporter
from models.import_result import ImportResult, ImportStatus
from models.round import Round
from database.repositories import Repository
from analysis.metrics_engine import MetricsEngine
from analysis.report_generator import ReportGenerator


def test_full_local_session_end_to_end_regression(tmp_path: Path, monkeypatch):
    """
    Integration regression test covering the full local pipeline:
    Start Session -> Stop Session -> stable replay detection -> package creation ->
    local import -> local SQLite match/round/player persistence -> MetricsEngine -> report generation.
    """
    # Isolated queue directory MUST be patched before SessionManager instantiation
    test_queue_dir = tmp_path / "queue"
    monkeypatch.setattr("app.session_manager.QUEUE_DIR", test_queue_dir)
    monkeypatch.setattr("app.upload_queue.QUEUE_DIR", test_queue_dir)

    # 1. Setup mock replay directory structure
    replay_dir = tmp_path / "Replays"
    replay_dir.mkdir()

    match_folder = replay_dir / "Match-2026-08-26_15-00-00-000"
    match_folder.mkdir()

    rec1 = match_folder / "Match-2026-08-26_15-00-00-000-R01.rec"
    rec1.write_bytes(b"MOCK_REPLAY_BYTES_ROUND_1")
    rec2 = match_folder / "Match-2026-08-26_15-00-00-000-R02.rec"
    rec2.write_bytes(b"MOCK_REPLAY_BYTES_ROUND_2")

    # 2. Mock RecImporter to return structured ImportResult without invoking r6-dissect binary
    mock_importer = MagicMock(spec=RecImporter)
    mock_rounds = [
        Round(round_id=None, match_id=None, round_number=1, side="attack", site="Laundry", outcome="win", resources=None, player_stats=[]),
        Round(round_id=None, match_id=None, round_number=2, side="defense", site="Master", outcome="loss", resources=None, player_stats=[]),
    ]
    mock_importer.import_multiple_folders.return_value = [
        ImportResult(status=ImportStatus.SUCCESS, map_name="Oregon", score_us=1, score_them=1, rounds=mock_rounds)
    ]

    # Instantiate SessionManager
    mgr = SessionManager(
        replay_folder=replay_dir,
        importer=mock_importer,
        transcribe=False,
        stability_wait=0.001,
        stability_checks=2,
    )

    # 3. Execute Start Session
    mgr.start_session()

    # Create new folder after session start snapshot
    new_match_folder = replay_dir / "Match-2026-08-26_15-10-00-000"
    new_match_folder.mkdir()
    new_rec = new_match_folder / "Match-2026-08-26_15-10-00-000-R01.rec"
    new_rec.write_bytes(b"NEW_MOCK_REPLAY_BYTES")

    # 4. Execute Stop Session
    results = mgr.end_session()

    # 5. Verify packaging & queueing
    assert len(results) == 1
    assert results[0].status == ImportStatus.SUCCESS
    assert results[0].match_id is not None
    match_id = results[0].match_id

    queue_items = mgr.upload_queue.list_items()
    assert len(queue_items) == 1
    pkg_item = queue_items[0]
    assert pkg_item.package_path.exists()
    assert pkg_item.local_analysis_status == "completed"

    # 6. Verify local SQLite database match/round persistence
    repo = Repository()
    match = repo.get_match_full(match_id)
    assert match is not None
    assert match.map == "Oregon"
    assert len(match.rounds) == 2

    # 7. Verify MetricsEngine
    engine = MetricsEngine(match)
    assert engine.win_rate() == 0.5
    assert engine.attack_win_rate() == 1.0
    assert engine.defense_win_rate() == 0.0

    # 8. Verify ReportGenerator report creation
    report_gen = ReportGenerator()
    report_path_str = report_gen.generate_match_report(match_id)
    report_path = Path(report_path_str)
    assert report_path.exists()
    report_content = report_path.read_text(encoding="utf-8")
    assert "Oregon" in report_content
    assert "MATCH REPORT" in report_content
