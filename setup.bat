@echo off
setlocal EnableExtensions
cd /d "%~dp0"

set "VENV_DIR=.venv"
set "PYTHON_EXE="
set "PYTHON_ARGS="


echo ============================================
echo  R6 Tactical Intelligence Engine - Setup
echo ============================================
echo.

call :resolve_python
if errorlevel 1 goto :fail

if exist "%VENV_DIR%\Scripts\activate.bat" (
    echo [INFO] Using existing virtual environment...
) else (
    echo [INFO] Creating virtual environment...
    "%PYTHON_EXE%" %PYTHON_ARGS% -m venv "%VENV_DIR%"
    if errorlevel 1 (
        echo [ERROR] Failed to create virtual environment.
        goto :fail
    )
)

call "%VENV_DIR%\Scripts\activate.bat"
if errorlevel 1 (
    echo [ERROR] Failed to activate virtual environment.
    goto :fail
)

echo [1/7] Upgrading pip tooling...
python -m pip install --upgrade pip setuptools wheel 2>&1
if errorlevel 1 (
    echo [ERROR] Failed to upgrade pip tooling.
    goto :fail
)

echo.
echo [2/7] Installing core runtime dependencies...
python -m pip install PySide6 psutil watchdog obs-websocket-py openai-whisper pyinstaller 2>&1
if errorlevel 1 (
    echo [ERROR] Failed to install core runtime dependencies.
    goto :fail
)

echo.
echo [3/7] Installing llama-cpp-python (CPU-only, no AVX required)...
python -m pip install llama-cpp-python --extra-index-url https://abetlen.github.io/llama-cpp-python/whl/cpu 2>&1
if errorlevel 1 (
    echo [WARN] Pre-built wheel failed. Trying source build without AVX...
    set "CMAKE_ARGS=-DLLAMA_AVX=OFF -DLLAMA_AVX2=OFF -DLLAMA_F16C=OFF -DLLAMA_FMA=OFF"
    python -m pip install llama-cpp-python --no-binary llama-cpp-python 2>&1
    if errorlevel 1 (
        echo [ERROR] Failed to install llama-cpp-python.
        goto :fail
    )
)

echo.
echo [4/7] Creating required folders and default settings...
python -c "from app.config import ensure_data_dirs, settings; ensure_data_dirs(); settings.save()"
if errorlevel 1 (
    echo [ERROR] Failed to create data folders or settings.json.
    goto :fail
)

echo.
echo [5/7] Initializing database schema, migrations, and seed data...
python -c "from database.db_manager import DatabaseManager; from database.migrations import run_migrations; from database.seed_operators import seed_database; db=DatabaseManager(); run_migrations(db); seed_database(db)"
if errorlevel 1 (
    echo [ERROR] Failed to initialize the database.
    goto :fail
)

echo.
echo [6/7] Validating Python imports...
python -c "import PySide6, psutil, watchdog, obswebsocket, whisper, llama_cpp; print('Python dependencies OK')"
if errorlevel 1 (
    echo [ERROR] Dependency validation failed.
    goto :fail
)

echo.
echo [7/7] Validating required local assets...
set "SETUP_WARNINGS=0"

call :check_exists "database\schema.sql" "database schema"
call :check_exists "integration\bin\r6-dissect.exe" "r6-dissect executable"
call :check_exists "integration\bin\libr6dissect.dll" "r6-dissect runtime DLL"

if exist "data\models\model.gguf" (
    echo [OK] Found AI model: data\models\model.gguf
) else (
    echo [WARN] Missing AI model: data\models\model.gguf
    set "SETUP_WARNINGS=1"
)

if exist "data\models\whisper-base.pt" (
    echo [OK] Found Whisper model: data\models\whisper-base.pt
) else (
    echo [WARN] Missing Whisper model: data\models\whisper-base.pt
    set "SETUP_WARNINGS=1"
)

where ffmpeg >nul 2>&1
if errorlevel 1 (
    if exist "ffmpeg.exe" (
        echo [OK] Found local ffmpeg.exe in project root.
    ) else (
        echo [WARN] ffmpeg not found on PATH and no local ffmpeg.exe present.
        echo        Whisper transcription needs ffmpeg to decode recordings.
        set "SETUP_WARNINGS=1"
    )
) else (
    echo [OK] ffmpeg found on PATH.
)

echo.
echo ============================================
echo  Setup complete.
if "%SETUP_WARNINGS%"=="1" (
    echo  Completed with warnings. Review the warnings above.
) else (
    echo  Environment looks ready.
)

echo  Run  : python main.py
echo  Build: build_and_deploy.bat
echo ============================================
echo.
pause
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
    echo [OK] Found %~2: %~1
) else (
    echo [WARN] Missing %~2: %~1
    set "SETUP_WARNINGS=1"
)
exit /b 0

:fail
echo.
echo ============================================
echo  Setup failed.
echo ============================================
echo.
pause
exit /b 1
