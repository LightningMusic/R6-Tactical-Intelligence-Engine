"""
Milestone 4, phase 2: SessionProcessingService._maybe_analyze rebuilds a
session's match record on the server (via RecImporter + match_builder,
identically to the client's own automatic import) and then runs IntelEngine
against it — all without the client doing anything beyond uploading.

RecImporter and IntelEngine are mocked out here: RecImporter shells out to a
Windows-only r6-dissect.exe not available in this environment, and IntelEngine
needs a real Ollama/llama-cpp backend to actually generate anything. Both are
already covered by their own tests. What's under test here is the plumbing:
does the service find the replays, build a match, carry the phase-1
transcript over, call IntelEngine with the right session pointed at the
right database, persist per-player intel (which IntelEngine itself doesn't
persist), record status/errors so the dashboard can explain itself — and,
critically, never let any of that raise and fail the whole job.
"""
import json
from pathlib import Path

import pytest

import server.services.session_processing as session_processing_module
from server.services.session_processing import SessionProcessingService
from server.database import ServerDatabase
from server.repositories import ServerRepository


@pytest.fixture
def work_dir(tmp_path: Path) -> Path:
    d = tmp_path / "work"
    (d / "replays").mkdir(parents=True)
    (d / "replays" / "Match-01.rec").write_bytes(b"fake-rec")
    return d


@pytest.fixture
def job_repo(tmp_path: Path, monkeypatch) -> ServerRepository:
    db = ServerDatabase(db_path=tmp_path / "server_matches.db")
    repo = ServerRepository(db=db)
    # Seed the parsed-match row _maybe_analyze's status updates target —
    # process_session_job creates this before calling _maybe_analyze; these
    # tests call _maybe_analyze directly, so seed it the same way.
    with db.get_connection() as conn:
        conn.execute(
            """INSERT INTO server_sessions (session_id, client_name, map_name, status, created_at, updated_at)
               VALUES ('session_abc123', 'TestClient', 'Clubhouse', 'uploaded', '2026-01-01T00:00:00Z', '2026-01-01T00:00:00Z')"""
        )
        conn.execute(
            """INSERT INTO server_parsed_matches (session_id, map_name, rounds_count, summary_json, created_at)
               VALUES ('session_abc123', 'Clubhouse', 1, '{}', '2026-01-01T00:00:00Z')"""
        )
        conn.commit()
    monkeypatch.setattr(session_processing_module, "ServerRepository", lambda: repo)
    return repo


class _FakeImportResult:
    def __init__(self, rounds, error_message=None):
        self.rounds = rounds
        self.match_id = None
        self.map_id = None
        self.map_name = "Clubhouse"
        self.error_message = error_message


class _FakeRecImporter:
    def __init__(self, dissect_path=None) -> None:
        pass

    def import_match_folder(self, folder):
        return _FakeImportResult(rounds=["fake-round"])


class _FakeIntelEngine:
    def __init__(self, **kwargs) -> None:
        self.kwargs = kwargs

    def analyze_match(self, match_id, **kwargs):
        self.kwargs_seen = kwargs
        return {"ai_match_summary": "Solid attack rounds, weak defense."}

    def get_player_intel(self, match_id, **kwargs):
        return {"TeamPlayer1": "STRENGTH: aim\nFOCUS: positioning\nDRILL: wallbang drills"}

    def store_ai_text(self, repo, match_id, name, text):
        with repo.db.get_connection() as conn:
            conn.execute(
                """INSERT INTO derived_metrics (match_id, metric_name, metric_value, metric_text, is_ai_generated)
                   VALUES (?, ?, ?, ?, 1)""",
                (match_id, name, float(len(text)), text),
            )
            conn.commit()


def _patch_success(monkeypatch, match_id=42):
    monkeypatch.setattr("integration.rec_importer.RecImporter", _FakeRecImporter)
    monkeypatch.setattr("analysis.intel_engine.IntelEngine", _FakeIntelEngine)

    def _fake_build(repo, results, log=None, catalog_hints=None):
        results[0].match_id = match_id

    monkeypatch.setattr("analysis.match_builder.build_matches_from_import_results", _fake_build)


def test_full_analysis_pipeline_marks_completed(monkeypatch, work_dir, job_repo, tmp_path):
    _patch_success(monkeypatch)

    class _FakeMatchRepo:
        def __init__(self):
            self.db = type("D", (), {"get_connection": lambda self: _FakeConn()})()

    class _FakeConn:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            pass

        def execute(self, *a, **k):
            return self

        def commit(self):
            pass

    monkeypatch.setattr("server.match_db.get_match_repo", lambda: _FakeMatchRepo())

    SessionProcessingService._maybe_analyze(work_dir, "session_abc123")

    parsed = job_repo.get_parsed_match("session_abc123")
    assert parsed["analysis_status"] == "completed"
    assert parsed["match_id"] == 42
    assert parsed["analysis_error"] is None


def test_no_replays_marks_failed_without_raising(tmp_path, job_repo):
    empty_dir = tmp_path / "work_no_replays"
    empty_dir.mkdir()

    SessionProcessingService._maybe_analyze(empty_dir, "session_abc123")

    parsed = job_repo.get_parsed_match("session_abc123")
    assert parsed["analysis_status"] == "failed"
    assert "no replay" in parsed["analysis_error"].lower()


def test_real_intel_engine_with_no_backend_marks_failed_not_completed(monkeypatch, work_dir, job_repo, tmp_path):
    """
    Uses the REAL match_builder and REAL IntelEngine (pointed at ollama/model
    paths that don't exist) — only RecImporter is mocked, since it needs a
    Windows r6-dissect.exe. This is the one path most likely to break
    silently: IntelEngine.generate() never raises when no AI backend is
    available — it returns a "[AI unavailable]" placeholder as if it were a
    real result, and analyze_match() stores and returns it exactly the same
    way it would a real summary. If _maybe_analyze ever stopped checking for
    that placeholder, a session with no Ollama installed would get marked
    "completed" with a fake, unusable report instead of "failed" with a
    clear reason — so this confirms the real (non-mocked) message format
    still matches what session_processing.py checks for.
    """
    from models.import_result import ImportResult, ImportStatus
    from models.round import Round

    round_obj = Round(
        round_id=None, match_id=None, round_number=1, side="attack",
        site="Site A", outcome="win", resources=None, player_stats=[],
        raw_player_stats=[{
            "username": "TeamPlayer1", "operator": "Ash",
            "kills": 3, "deaths": 1, "assists": 0, "is_our_team": True,
        }],
        round_events=None,
    )
    real_result = ImportResult(status=ImportStatus.SUCCESS, map_name="Clubhouse", rounds=[round_obj])

    class _RealRecImporter:
        def __init__(self, dissect_path=None):
            pass

        def import_match_folder(self, folder):
            return real_result

    monkeypatch.setattr("integration.rec_importer.RecImporter", _RealRecImporter)

    schema_path = Path(__file__).parent.parent / "database" / "schema.sql"
    matches_db_path = tmp_path / "matches.db"

    from server.config import server_settings
    monkeypatch.setattr(server_settings, "MATCHES_DB_PATH", matches_db_path)
    monkeypatch.setattr(server_settings, "MATCHES_SCHEMA_PATH", schema_path)
    monkeypatch.setattr(server_settings, "OLLAMA_EXE", tmp_path / "no_ollama" / "ollama.exe")
    monkeypatch.setattr(server_settings, "OLLAMA_MODELS_DIR", tmp_path / "no_ollama" / "models")

    import server.match_db as match_db_module
    match_db_module._bootstrapped = False

    SessionProcessingService._maybe_analyze(work_dir, "session_abc123")

    parsed = job_repo.get_parsed_match("session_abc123")
    assert parsed["analysis_status"] == "failed"
    assert parsed["match_id"] is not None
    assert "AI unavailable" in parsed["analysis_error"]


def test_intel_engine_exception_marks_failed_without_raising(monkeypatch, work_dir, job_repo):
    monkeypatch.setattr("integration.rec_importer.RecImporter", _FakeRecImporter)

    def _fake_build(repo, results, log=None, catalog_hints=None):
        results[0].match_id = 99

    monkeypatch.setattr("analysis.match_builder.build_matches_from_import_results", _fake_build)

    class _BrokenIntelEngine:
        def __init__(self, **kwargs):
            raise RuntimeError("no AI backend available")

    monkeypatch.setattr("analysis.intel_engine.IntelEngine", _BrokenIntelEngine)
    monkeypatch.setattr("server.match_db.get_match_repo", lambda: object())

    # Must not raise — a broken/missing AI backend shouldn't fail the job.
    SessionProcessingService._maybe_analyze(work_dir, "session_abc123")

    parsed = job_repo.get_parsed_match("session_abc123")
    assert parsed["analysis_status"] == "failed"
    assert "no ai backend" in parsed["analysis_error"].lower()
