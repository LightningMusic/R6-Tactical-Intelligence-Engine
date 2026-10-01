# -*- mode: python ; coding: utf-8 -*-
#
# Builds the standalone R6Server.exe — the headless remote server, packaged
# separately from the R6Analyzer.exe client. Run `pyinstaller R6Server.spec`
# (build_and_deploy.bat does this automatically as part of the normal client
# build — see the "Building the server" step there).
#
# This is intentionally a completely separate spec from R6Analyzer.spec:
# the client has no FastAPI/Uvicorn dependencies, and the server excludes
# everything client-only (see the excludes list below) — keeping them in
# separate builds means neither exe carries dead weight from the other.
#
# As of Milestone 4 phase 1, the server DOES bundle Whisper (it now runs
# transcription itself, see server/services/session_processing.py) — that
# used to be excluded when the server was purely a lightweight ingest
# service.
#
# As of Milestone 4 phase 2, the server also runs the full Intel Engine AI
# analysis (analysis/intel_engine.py) against its own match database (see
# server/match_db.py) — reusing that class, database/repositories.py, and
# database/schema.sql completely unmodified from the client. The AI backend
# itself is portable Ollama, launched as a subprocess exactly like the
# client's own setup — its binaries are NOT bundled by PyInstaller (same as
# the client never bundled them either); drop an extracted
# ollama-windows-amd64.zip into server_data/ollama/ once. llama-cpp-python
# stays excluded below — a bundled GGUF model was deliberately not chosen
# for the server (see milestone4 project doc for why), so there's no reason
# to carry that dependency's weight.
import sys
from pathlib import Path
from PyInstaller.building.build_main import Analysis, PYZ, EXE, COLLECT
from PyInstaller.utils.hooks import collect_all

PROJECT_ROOT = Path(SPECPATH)

# ── Locate whisper's packaged assets ─────────────────────────
# Same gotcha as R6Analyzer.spec: openai-whisper ships mel_filters.npz /
# multilingual.tiktoken etc. inside its own package dir, loaded via a path
# relative to whisper/__file__ at runtime — invisible to PyInstaller's
# static analysis without bundling it explicitly.
whisper_datas = []
try:
    import whisper
    whisper_assets_dir = Path(whisper.__file__).parent / "assets"
    if whisper_assets_dir.is_dir():
        whisper_datas.append((str(whisper_assets_dir), "whisper/assets"))
        print(f"[spec] Found whisper assets: {whisper_assets_dir}")
    else:
        print(f"[spec] WARNING: whisper assets not found at {whisper_assets_dir}")
except ImportError:
    print("[spec] whisper not installed — skipping (server transcription will fail in the frozen exe)")

# ── This project's own data files ──────────────────────────────
# server/dashboard_routes.py reads server/static/dashboard.html off disk
# at request time via a path relative to its own __file__. PyInstaller's
# static analysis only follows *imports* — a plain file read like this is
# invisible to it, so without an explicit datas entry the file simply isn't
# in the frozen bundle and every /dashboard request 500s. r6-dissect.exe is
# bundled the same way for the same reason: TimelineAligner and (as of
# phase 2) RecImporter both shell out to it at request time, not via an
# import PyInstaller could trace. database/schema.sql is the same story
# again: server/match_db.py reads it off disk via DatabaseManager, the
# exact path client and server share for their own respective databases.
datas = [
    (str(PROJECT_ROOT / "server" / "static" / "dashboard.html"), "server/static"),
    (str(PROJECT_ROOT / "server" / "static" / "join.html"), "server/static"),
    (str(PROJECT_ROOT / "integration" / "bin" / "r6-dissect.exe"), "integration/bin"),
    (str(PROJECT_ROOT / "database" / "schema.sql"), "database"),
] + whisper_datas
binaries = []
hiddenimports = [
    "whisper",
    "whisper.audio",
    "whisper.model",
    "whisper.tokenizer",
    "whisper.transcribe",
    "whisper.utils",
    "whisper.decoding",
    "tiktoken",
    "tiktoken_ext",
    "tiktoken_ext.openai_public",
    "numpy",
    "scipy",
    "scipy.signal",
    "tqdm",
    "tqdm.auto",
    "soundfile",
    "regex",
    "numba",
]

# ── Collect FastAPI/Uvicorn/Starlette/Pydantic wholesale ──────
# These do a lot of dynamic, plugin-style importing internally (uvicorn's
# protocol/loop auto-detection, pydantic's compiled core, starlette's
# optional extras) that PyInstaller's static analysis alone won't reliably
# catch. collect_all() pulls in every submodule, data file, and binary for
# each package, which is far more robust than hand-maintaining a
# hiddenimports list that silently rots as these libraries update.

for pkg in ("fastapi", "starlette", "uvicorn", "pydantic", "pydantic_core", "multipart"):
    try:
        pkg_datas, pkg_binaries, pkg_hidden = collect_all(pkg)
        datas += pkg_datas
        binaries += pkg_binaries
        hiddenimports += pkg_hidden
        print(f"[spec] Collected {pkg}: {len(pkg_hidden)} submodules")
    except Exception as e:
        print(f"[spec] WARNING: could not collect_all('{pkg}'): {e}")

# uvicorn resolves its HTTP/loop/lifespan implementations via importlib at
# runtime based on the string "auto" rather than a static import — even
# collect_all can miss the specific impl modules actually selected at
# startup. Belt-and-suspenders on top of the loop above.
hiddenimports += [
    "uvicorn.logging",
    "uvicorn.loops",
    "uvicorn.loops.auto",
    "uvicorn.protocols",
    "uvicorn.protocols.http",
    "uvicorn.protocols.http.auto",
    "uvicorn.protocols.http.h11_impl",
    "uvicorn.protocols.websockets",
    "uvicorn.protocols.websockets.auto",
    "uvicorn.lifespan",
    "uvicorn.lifespan.on",
    "uvicorn.lifespan.off",
]

# ── Everything the server must NEVER pull in ──────────────────
# The client and server are built from the same repo but must ship as two
# completely independent executables. If a future change accidentally adds
# an import from gui/ or a client-only package into anything under server/,
# this list turns that into an obvious missing-module error at build time
# instead of silently bloating (or breaking) the server exe.
excludes = [
    "PySide6",
    "gui",
    "obswebsocket",
    "llama_cpp",  # AI backend is portable Ollama, not a bundled GGUF model — see header
    "discord",
    "nacl",
    "requests",
    "certifi",
    "matplotlib",
    "PIL",
    "cv2",
    "IPython",
    "notebook",
    "pytest",
    "tkinter",
]

a = Analysis(
    ["server_main.py"],
    pathex=[str(PROJECT_ROOT)],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=excludes,
    noarchive=False,
)

pyz = PYZ(a.pure)

# Same icon as R6Analyzer.exe (see R6Analyzer.spec) -- shared from
# resources/icons/icon.ico so the server's console window and Explorer
# entry are recognizable as part of the same app, not a shared "None".
icon_path = PROJECT_ROOT / "resources" / "icons" / "icon.ico"

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="R6Server",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    # Console stays visible on purpose: the console window IS this app's
    # UI for a first-run token, live upload/processing logs, and errors.
    console=True,
    icon=str(icon_path) if icon_path.exists() else None,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="R6Server",
)
