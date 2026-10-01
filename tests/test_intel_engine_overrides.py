"""
Milestone 4, phase 2: analysis/intel_engine.py's IntelEngine, _OllamaBackend,
and _LlamaCppBackend used to hard-import MODEL_PATH/OLLAMA_EXE/OLLAMA_MODELS
from app.config at module load time — fine for the client, impossible to
repoint for the server. They now accept everything as constructor overrides,
falling back to the client's app.config values only when omitted, the same
pattern already used for WhisperTranscriber and TimelineAligner.

These tests don't touch a real Ollama server or GGUF model — they just
confirm the override plumbing itself: the right path ends up in the right
place, nothing crashes on construction, and the client's zero-argument
behavior (reading from app.config) is unchanged.
"""
from pathlib import Path

from analysis.intel_engine import IntelEngine, _OllamaBackend, _LlamaCppBackend


def test_ollama_backend_uses_explicit_overrides(tmp_path: Path):
    exe = tmp_path / "ollama.exe"
    models_dir = tmp_path / "models"

    backend = _OllamaBackend(ollama_exe=exe, ollama_models=models_dir, default_model="llama3.2:3b")

    assert backend.ollama_exe == exe
    assert backend.ollama_models == models_dir
    assert backend.model == "llama3.2:3b"


def test_ollama_backend_defaults_to_client_config():
    # No overrides — should fall back to app.config's client-side paths
    # without raising, exactly like before this refactor.
    backend = _OllamaBackend()
    assert backend.ollama_exe.name == "ollama.exe"
    assert backend.model  # non-empty, either configured or DEFAULT_MODEL


def test_external_ollama_is_addressed_by_url_and_never_spawned(tmp_path: Path, monkeypatch):
    import analysis.intel_engine as ie

    def no_spawn(*a, **k):
        raise AssertionError("must not launch a local Ollama when a URL is configured")

    monkeypatch.setattr(ie.subprocess, "Popen", no_spawn)
    backend = _OllamaBackend(ollama_exe=tmp_path / "ollama.exe", ollama_models=tmp_path / "m",
                             default_model="llama3.2:3b", ollama_url="http://127.0.0.1:9/")
    assert backend.api_base == "http://127.0.0.1:9"
    assert backend.external
    assert backend.ensure_running() is False

    default = _OllamaBackend(ollama_exe=tmp_path / "ollama.exe", ollama_models=tmp_path / "m",
                             default_model="llama3.2:3b")
    assert default.api_base == "http://localhost:11434" and not default.external


def test_llama_cpp_backend_uses_explicit_model_path(tmp_path: Path):
    model_path = tmp_path / "model.gguf"
    backend = _LlamaCppBackend(model_path=model_path)
    assert backend.model_path == model_path


def test_intel_engine_wires_overrides_into_backends(tmp_path: Path):
    ollama_exe = tmp_path / "ollama.exe"
    ollama_models = tmp_path / "ollama_models"
    model_path = tmp_path / "model.gguf"
    db_path = tmp_path / "matches.db"
    schema_path = tmp_path / "schema.sql"

    engine = IntelEngine(
        model_path=model_path,
        ollama_exe=ollama_exe,
        ollama_models=ollama_models,
        default_model="llama3.2:3b",
        db_path=db_path,
        schema_path=schema_path,
    )

    assert engine._ollama.ollama_exe == ollama_exe
    assert engine._ollama.ollama_models == ollama_models
    assert engine._ollama.model == "llama3.2:3b"
    assert engine._llama_cpp.model_path == model_path
    assert engine._db_path == db_path
    assert engine._schema_path == schema_path


def test_intel_engine_make_repo_uses_overridden_db_path(tmp_path: Path):
    from database.migrations import run_migrations
    from database.seed_operators import seed_database

    schema_path = Path(__file__).parent.parent / "database" / "schema.sql"
    db_path = tmp_path / "matches.db"

    engine = IntelEngine(db_path=db_path, schema_path=schema_path)
    repo = engine._make_repo()

    assert repo.db.db_path == db_path
    assert db_path.exists()


def test_generate_reports_unavailable_backend_without_crashing(tmp_path: Path):
    # Point both backends at paths that definitely don't exist — this must
    # degrade to the "[AI unavailable]" message, not raise, matching the
    # server's requirement that a missing AI backend never fails the job.
    engine = IntelEngine(
        model_path=tmp_path / "no_model.gguf",
        ollama_exe=tmp_path / "no_ollama" / "ollama.exe",
        ollama_models=tmp_path / "no_ollama" / "models",
    )

    result = engine.generate("does this crash?")

    assert "[AI unavailable]" in result
    assert str(tmp_path / "no_ollama") in result
