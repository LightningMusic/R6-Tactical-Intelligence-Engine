REM ============================================================
REM  R6 Tactical Intelligence Engine -- environment setup
REM ============================================================
REM  Called by build_and_deploy.bat (`call "build_scripts\env_setup.bat"`)
REM  as the first step of every build -- this is not something you run by
REM  hand. It relies on running with build_and_deploy.bat's own current
REM  directory (the project root) already in effect, and on that script's
REM  own `setlocal EnableExtensions EnableDelayedExpansion` already being
REM  active, so it deliberately does not `cd` anywhere or add its own
REM  setlocal -- every variable it sets (PYTHON_EXE, VENV_DIR,
REM  SETUP_WARNINGS, ...) is meant to still be visible back in
REM  build_and_deploy.bat once this returns.
REM
REM  Extracted out of build_and_deploy.bat itself (previously reached via
REM  `call :env_setup`, an internal label) purely to keep that file
REM  smaller -- large batch files have already caused real, reproduced
REM  failures in this project from cmd.exe's `call :label` lookup
REM  becoming unreliable for a label far from its call site. Moving this
REM  ~190-line block out removes it from that file entirely, rather than
REM  just hoping the remaining internal labels never grow far enough
REM  apart to hit the same bug.
REM ============================================================

set "VENV_DIR=.venv"
set "PYTHON_EXE="
set "PYTHON_ARGS="

call :resolve_python
if errorlevel 1 exit /b 1

if exist "%VENV_DIR%\Scripts\activate.bat" (
    echo       [1/10] Using existing virtual environment...
) else (
    echo       [1/10] Creating virtual environment...
    "%PYTHON_EXE%" %PYTHON_ARGS% -m venv "%VENV_DIR%"
    if errorlevel 1 (
        echo [ERROR] Failed to create virtual environment.
        exit /b 1
    )
)

call "%VENV_DIR%\Scripts\activate.bat"
if errorlevel 1 (
    echo [ERROR] Failed to activate virtual environment.
    exit /b 1
)

echo       [2/10] Upgrading pip tooling...
python -m pip install --upgrade pip setuptools wheel 2>&1
if errorlevel 1 (
    echo [ERROR] Failed to upgrade pip tooling.
    exit /b 1
)

echo       [3/10] Installing core runtime dependencies...
python -m pip install PySide6 psutil watchdog obs-websocket-py openai-whisper requests pyinstaller 2>&1
if errorlevel 1 (
    echo [ERROR] Failed to install core runtime dependencies.
    exit /b 1
)

echo       [4/10] Installing optional Discord per-user audio capture support...
echo              (Enables speaker identification in transcripts; everything
echo              else works fine without it, so a failure here is a
echo              warning, not a setup failure.)
echo              2026-09-11: added discord-ext-voice-recv -- stock discord.py
echo              can only SEND audio, it has no receive path at all, so this
echo              package is the actual thing that makes per-user capture
echo              possible. Previously missing here, which meant even a
echo              "successful" install of this step could never have worked.
python -m pip install "discord.py[voice]" PyNaCl discord-ext-voice-recv 2>&1
if errorlevel 1 (
    echo [WARN] Optional Discord voice support failed to install -- continuing.
    echo        Per-user speaker identification via Discord will not be available.
    set "SETUP_WARNINGS=1"
)

echo       [5/10] Installing optional speaker-diarization support...
echo              (Milestone 6 -- resemblyzer + scikit-learn let
echo              integration/whisper_transcriber.py cluster transcript
echo              segments by actual voice similarity instead of just
echo              silence gaps. This was missing from setup entirely until
echo              now, which is why diarization has been silently falling
echo              back to the older, cruder heuristic. Same as Discord
echo              above: optional, so a failure here only warns.)
python -m pip install resemblyzer scikit-learn 2>&1
if errorlevel 1 (
    echo [WARN] Optional speaker-diarization support failed to install -- continuing.
    echo        Transcripts will use the simpler silence-gap speaker heuristic.
    set "SETUP_WARNINGS=1"
)
echo       Installing audio I/O for the teammate recorder (R6Voice) and the
echo       server's comms timeline (sounddevice, soundfile, numpy)...
python -m pip install sounddevice soundfile numpy 2>&1
if errorlevel 1 (
    echo [WARN] sounddevice/soundfile failed to install -- R6Voice.exe will not build.
    set "SETUP_WARNINGS=1"
)

REM webrtcvad, pulled in by resemblyzer, drags in the ancient Python-2-era
REM "typing" PyPI backport. It's dead weight on Python 3 and, worse,
REM PyInstaller can bundle it and have it shadow the real stdlib typing
REM module inside the frozen exe -- that exact crash has happened before.
REM Nothing in this codebase imports the standalone typing package, so
REM removing it is always safe. This used to only run in the success
REM branch above, which meant a FAILED resemblyzer install (network
REM hiccup, version conflict, whatever) could leave a partially-installed
REM "typing" package sitting in the build venv -- which persists across
REM runs -- with nothing ever cleaning it back out. Running it here,
REM unconditionally, after the install attempt either way, closes that gap.
python -m pip uninstall -y typing >nul 2>&1

echo       [6/10] Installing server build-time dependencies...
echo              (FastAPI/Uvicorn are only ever bundled into R6Server.exe
echo              by R6Server.spec's own excludes/includes -- installing
echo              them into this venv is what lets this script build that
echo              exe in the same run as R6Analyzer.exe. They never ship
echo              inside R6Analyzer.exe itself; see R6Analyzer.spec's
echo              excludes list.)
if exist "server\requirements.txt" (
    python -m pip install -r server\requirements.txt 2>&1
    if errorlevel 1 (
        echo [ERROR] Failed to install server build-time dependencies.
        exit /b 1
    )
) else (
    echo [WARN] server\requirements.txt not found -- skipping.
    echo        R6Server.exe will not be buildable until it exists.
    set "SETUP_WARNINGS=1"
)

echo       [7/10] Installing llama-cpp-python (CPU-only, no AVX required)...
python -m pip install llama-cpp-python --extra-index-url https://abetlen.github.io/llama-cpp-python/whl/cpu 2>&1
if errorlevel 1 (
    echo [WARN] Pre-built wheel failed. Trying source build without AVX...
    set "CMAKE_ARGS=-DLLAMA_AVX=OFF -DLLAMA_AVX2=OFF -DLLAMA_F16C=OFF -DLLAMA_FMA=OFF"
    python -m pip install llama-cpp-python --no-binary llama-cpp-python 2>&1
    if errorlevel 1 (
        echo [ERROR] Failed to install llama-cpp-python.
        exit /b 1
    )
)

echo       [8/10] Creating required folders and default settings...
python -c "from app.config import ensure_data_dirs, settings; ensure_data_dirs(); settings.save()"
if errorlevel 1 (
    echo [ERROR] Failed to create data folders or settings.json.
    exit /b 1
)

echo       [9/10] Initializing database schema, migrations, and seed data...
python -c "from database.db_manager import DatabaseManager; from database.migrations import run_migrations; from database.seed_operators import seed_database; db=DatabaseManager(); run_migrations(db); seed_database(db)"
if errorlevel 1 (
    echo [ERROR] Failed to initialize the database.
    exit /b 1
)

echo       [10/10] Validating environment...
python -c "import PySide6, psutil, watchdog, obswebsocket, whisper, llama_cpp, requests; print('       [OK] Python dependencies present.')"
if errorlevel 1 (
    echo [ERROR] Dependency validation failed.
    exit /b 1
)

call :check_exists "database\schema.sql" "database schema"
call :check_exists "integration\bin\r6-dissect.exe" "r6-dissect executable"
call :check_exists "integration\bin\libr6dissect.dll" "r6-dissect runtime DLL"

if exist "data\models\model.gguf" (
    echo       [OK] Found AI model: data\models\model.gguf
) else (
    echo       [WARN] Missing AI model: data\models\model.gguf
    set "SETUP_WARNINGS=1"
)

if exist "data\models\whisper-base.pt" (
    echo       [OK] Found Whisper model: data\models\whisper-base.pt
) else (
    echo       [WARN] Missing Whisper model: data\models\whisper-base.pt
    set "SETUP_WARNINGS=1"
)

where ffmpeg >nul 2>&1
if errorlevel 1 (
    if exist "ffmpeg.exe" (
        echo       [OK] Found local ffmpeg.exe in project root.
    ) else (
        echo       [WARN] ffmpeg not found on PATH and no local ffmpeg.exe present.
        echo              Whisper transcription needs ffmpeg to decode recordings.
        set "SETUP_WARNINGS=1"
    )
) else (
    echo       [OK] ffmpeg found on PATH.
)

exit /b 0

:resolve_python
where py >nul 2>&1
if not errorlevel 1 (
    py -3 -c "import sys" >nul 2>&1
    if not errorlevel 1 (
        set "PYTHON_EXE=py"
        set "PYTHON_ARGS=-3"
        exit /b 0
    )
)

where python >nul 2>&1
if not errorlevel 1 (
    set "PYTHON_EXE=python"
    set "PYTHON_ARGS="
    exit /b 0
)

echo [ERROR] Python 3 was not found on PATH.
exit /b 1

:check_exists
if exist "%~1" (
    echo       [OK] Found %~2: %~1
) else (
    echo       [WARN] Missing %~2: %~1
    set "SETUP_WARNINGS=1"
)
exit /b 0
