# -*- mode: python ; coding: utf-8 -*-
import sys
from pathlib import Path
from PyInstaller.building.build_main import Analysis, PYZ, EXE, COLLECT

PROJECT_ROOT = Path(SPECPATH)

# ── Locate llama_cpp native libs ─────────────────────────────
# PyInstaller misses these because they're loaded at runtime via ctypes.
llama_binaries = []
try:
    import llama_cpp
    llama_lib_dir = Path(llama_cpp.__file__).parent / "lib"
    if llama_lib_dir.exists():
        for dll in llama_lib_dir.glob("*.dll"):
            llama_binaries.append((str(dll), "llama_cpp/lib"))
        for dll in llama_lib_dir.glob("*.so*"):
            llama_binaries.append((str(dll), "llama_cpp/lib"))
        print(f"[spec] Found {len(llama_binaries)} llama_cpp lib files")
    else:
        print(f"[spec] WARNING: llama_cpp/lib not found at {llama_lib_dir}")
except ImportError:
    print("[spec] llama_cpp not installed — skipping")

# ── Locate certifi's CA bundle ────────────────────────────────
# `requests` (app/uploader.py) loads its default CA bundle from certifi's
# packaged cacert.pem at runtime via importlib.resources. PyInstaller's
# static analysis doesn't pick up that data file on its own, and without
# it HTTPS uploads fail inside the frozen exe with a TLS verification
# error even though the exact same code works fine from source. Bundle it
# explicitly, the same way llama_cpp's native libs are handled below.
certifi_datas = []
try:
    import certifi
    cacert_path = Path(certifi.where())
    if cacert_path.exists():
        certifi_datas.append((str(cacert_path), "certifi"))
        print(f"[spec] Found certifi CA bundle at {cacert_path}")
    else:
        print(f"[spec] WARNING: certifi CA bundle not found at {cacert_path}")
except ImportError:
    print("[spec] certifi not installed — skipping (requests HTTPS uploads may fail in the frozen exe)")

# ── Locate whisper's packaged assets ─────────────────────────
# openai-whisper ships mel_filters.npz / multilingual.tiktoken etc. inside
# its own package directory and loads them via a path relative to
# whisper/__file__ at runtime. PyInstaller's static analysis won't pick
# these up on its own — bundle them explicitly, same pattern as llama_cpp
# and certifi above.
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
    print("[spec] whisper not installed — skipping (transcription will fail in the frozen exe)")

# ── Locate resemblyzer's pretrained weights ───────────────────
# Milestone 6 speaker-diarization voice clustering (optional — see
# integration/whisper_transcriber.py _cluster_segments_by_voice()).
# Same story as whisper's assets above: resemblyzer loads pretrained.pt
# via a path relative to its own __file__ at runtime, invisible to
# PyInstaller's static analysis without bundling it explicitly. Only
# present at all if `pip install resemblyzer` was run — see requirements.txt.
resemblyzer_datas = []
resemblyzer_hidden = []
try:
    import resemblyzer
    resemblyzer_dir = Path(resemblyzer.__file__).parent
    weights_path = resemblyzer_dir / "pretrained.pt"
    if weights_path.exists():
        resemblyzer_datas.append((str(weights_path), "resemblyzer"))
        print(f"[spec] Found resemblyzer weights: {weights_path}")
    else:
        print(f"[spec] WARNING: resemblyzer installed but pretrained.pt not found at {weights_path}")
    resemblyzer_hidden = ["resemblyzer", "sklearn", "sklearn.cluster", "webrtcvad", "librosa", "soxr"]
except ImportError:
    print("[spec] resemblyzer not installed — skipping (voice-based speaker clustering "
          "will fall back to the silence-gap heuristic in the frozen exe)")

# ── Discord per-user voice capture (optional — 2026-09-11) ─────
# discord.py's gateway/voice code and PyNaCl's compiled libsodium bindings
# both do their own runtime importing that PyInstaller's static analysis
# misses by default -- the same class of problem R6Server.spec already
# works around for FastAPI/Starlette/Uvicorn via collect_all() (see
# milestone3-client-uploader.md). Without this, the frozen exe could
# build "successfully" and then fail with an ImportError the moment
# Discord capture is actually used -- exactly the "looks updated but
# doesn't actually work" bug class this project has hit before. Only
# present at all if `pip install "discord.py[voice]" PyNaCl
# discord-ext-voice-recv` was run (requirements.txt /
# build_scripts/env_setup.bat step 4).
discord_hidden = []
try:
    from PyInstaller.utils.hooks import collect_submodules
    import discord  # noqa
    import nacl  # noqa
    from discord.ext import voice_recv  # noqa
    discord_hidden = (
        collect_submodules("discord")
        + collect_submodules("nacl")
        + collect_submodules("discord.ext.voice_recv")
    )
    print(f"[spec] Found discord.py + PyNaCl + discord-ext-voice-recv "
          f"({len(discord_hidden)} submodules collected)")
except ImportError:
    print("[spec] discord.py/PyNaCl/discord-ext-voice-recv not installed — "
          "skipping (Discord per-user capture will report unavailable in "
          "the frozen exe)")

# ── Collect data files ────────────────────────────────────────
datas = [
    (str(PROJECT_ROOT / "database" / "schema.sql"),       "database"),
    (str(PROJECT_ROOT / "integration" / "bin" / "r6-dissect.exe"), "integration/bin"),
] + certifi_datas + whisper_datas + resemblyzer_datas

# Add icons if they exist
icons_dir = PROJECT_ROOT / "resources" / "icons"
if icons_dir.exists():
    datas.append((str(icons_dir), "resources/icons"))

# ── Hidden imports ────────────────────────────────────────────
hiddenimports = [
    "PySide6.QtCore",
    "PySide6.QtGui",
    "PySide6.QtWidgets",
    "sqlite3",
    "psutil",
    "obswebsocket",
    "obswebsocket.requests",
    "obswebsocket.events",
    "whisper",
    "whisper.audio",
    "whisper.model",
    "whisper.tokenizer",
    "whisper.transcribe",
    "whisper.utils",
    "whisper.decoding",
    "zstandard",
    "zstandard.backend_c",
    "llama_cpp",
    "llama_cpp.llama",
    "llama_cpp.llama_cpp",
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
    # Remote sync (Milestone 3 client uploader — app/uploader.py). Listed
    # explicitly because PyInstaller has historically missed requests'
    # own conditional/compat imports; see the certifi note above too.
    "requests",
    "urllib3",
    "certifi",
    "charset_normalizer",
    "idna",
] + resemblyzer_hidden + discord_hidden  # Speaker diarization (M6) + Discord voice capture, both optional

excludes = [
    "matplotlib",
    "PIL",
    # NOTE: do NOT put "typing" here. It looks like defense-in-depth against
    # the ancient Python-2-era "typing" PyPI backport (a transitive
    # dependency of webrtcvad, via resemblyzer), but PyInstaller excludes
    # match by module NAME only — it can't tell that fake package apart
    # from the real Python stdlib `typing` module, and strips both. The
    # stdlib `typing` module is a hard dependency of functools.py and of
    # PyInstaller's own pyi_rth_pkgutil runtime hook, so excluding it here
    # crashes the frozen .exe on startup with "No module named 'typing'"
    # before main.py ever runs (confirmed the hard way — this exact entry
    # caused that crash in a real build). The real, correct fix for the
    # PyPI package risk is `pip uninstall -y typing` in the build venv,
    # which build_and_deploy.bat's :env_setup step already does
    # automatically — that's sufficient on its own, no exclude needed.
    "cv2",
    "IPython",
    "notebook",
    "pytest",
    "tkinter",
    # server/ is a separate headless component (see server/requirements.txt)
    # that main.py's import graph never reaches — excluded explicitly as a
    # defense-in-depth guard so a future accidental import can't silently
    # drag a web framework onto the USB build.
    "fastapi",
    "uvicorn",
    "starlette",
    "httpx",
]

a = Analysis(
    ["main.py"],
    pathex=[str(PROJECT_ROOT)],
    binaries=llama_binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[str(PROJECT_ROOT)],
    hooksconfig={},
    runtime_hooks=[],
    excludes=excludes,
    noarchive=False,
)

pyz = PYZ(a.pure)

icon_path = PROJECT_ROOT / "resources" / "icons" / "icon.ico"

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="R6Analyzer",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    icon=str(icon_path) if icon_path.exists() else None,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="R6Analyzer",
)