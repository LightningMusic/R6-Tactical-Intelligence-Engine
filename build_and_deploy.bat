@echo off
setlocal enabledelayedexpansion

echo ============================================================
echo  R6 Tactical Intelligence Engine — Build + USB Deploy
echo ============================================================
echo.

REM ── Config ───────────────────────────────────────────────────
set APP_NAME=R6Analyzer
set DIST_DIR=dist\%APP_NAME%
set TARGET_LABEL=R6_PROJ

REM ── Activate venv ────────────────────────────────────────────
if exist ".venv\Scripts\activate.bat" (
    call .venv\Scripts\activate.bat
) else (
    echo [ERROR] .venv not found.
    pause & exit /b 1
)

REM ── Build ────────────────────────────────────────────────────
echo [1/4] Building exe...
pip show pyinstaller >nul 2>&1 || pip install pyinstaller
pyinstaller R6Analyzer.spec --noconfirm

if errorlevel 1 (
    echo [ERROR] Build failed.
    pause & exit /b 1
)
echo [OK] Build complete.
echo.

REM ── Find USB Drive by Label ──────────────────────────────────
echo [2/4] Searching for USB with label: %TARGET_LABEL%...
set USB_DRIVE=
for /f "tokens=1" %%d in ('powershell -NoProfile -Command "Get-Volume -FileSystemLabel '%TARGET_LABEL%' | Select-Object -ExpandProperty DriveLetter"') do (
    set USB_DRIVE=%%d:
)

if "%USB_DRIVE%"==":" set USB_DRIVE=
if "%USB_DRIVE%"=="" (
    echo [ERROR] Could not find a USB drive labeled "%TARGET_LABEL%".
    pause & exit /b 1
)

set USB_DEST=%USB_DRIVE%\%APP_NAME%
echo [OK] USB found at %USB_DRIVE%
echo.

REM ── Ensure destination exists ────────────────────────────────
if not exist "%USB_DEST%" mkdir "%USB_DEST%"

REM ── Robocopy with safe exclusions ────────────────────────────
echo [3/4] Syncing build to %USB_DEST%...
echo       Preserving: data\, exports\, settings.json, matches.db
echo.

REM /PURGE  — removes files in dest that don't exist in source
REM /XF     — exclude specific files from purge/overwrite
REM /XD     — exclude specific folders from purge entirely
REM /E      — include subdirectories (even empty ones)
REM /XO     — skip files that are older than destination copy

robocopy "%DIST_DIR%" "%USB_DEST%" /E /PURGE /XO /R:3 /W:5 ^
    /XF settings.json matches.db ^
    /XD data exports recordings transcripts reports models

if errorlevel 8 (
    echo [ERROR] Robocopy encountered a serious error (code %errorlevel%).
    pause & exit /b 1
)
echo [OK] Build synced. Code updated, personal data preserved.
echo.

REM ── Ensure data subdirs exist on USB ─────────────────────────
if not exist "%USB_DEST%\data"               mkdir "%USB_DEST%\data"
if not exist "%USB_DEST%\data\models"        mkdir "%USB_DEST%\data\models"
if not exist "%USB_DEST%\data\recordings"    mkdir "%USB_DEST%\data\recordings"
if not exist "%USB_DEST%\data\transcripts"   mkdir "%USB_DEST%\data\transcripts"
if not exist "%USB_DEST%\data\reports"       mkdir "%USB_DEST%\data\reports"
if not exist "%USB_DEST%\exports"            mkdir "%USB_DEST%\exports"

REM ── Copy model files (only if newer or missing) ──────────────
echo [4/4] Checking model files...

set MODEL_SRC=data\models
set MODEL_DEST=%USB_DEST%\data\models

if exist "%MODEL_SRC%\model.gguf" (
    echo      Syncing model.gguf ^(large file — may take a moment^)...
    robocopy "%MODEL_SRC%" "%MODEL_DEST%" model.gguf /XO /R:1 /W:2 >nul
    echo      [OK] model.gguf synced.
) else (
    echo      [WARN] model.gguf not found in %MODEL_SRC%
    echo             Copy manually: data\models\model.gguf
)

if exist "%MODEL_SRC%\whisper-base.pt" (
    echo      Syncing whisper-base.pt...
    robocopy "%MODEL_SRC%" "%MODEL_DEST%" whisper-base.pt /XO /R:1 /W:2 >nul
    echo      [OK] whisper-base.pt synced.
) else (
    echo      [WARN] whisper-base.pt not found in %MODEL_SRC%
    echo             Copy manually: data\models\whisper-base.pt
)

REM ── Copy DB if present (only if newer) ───────────────────────
if exist "data\matches.db" (
    robocopy "data" "%USB_DEST%\data" matches.db /XO /R:1 /W:2 >nul
    echo      [OK] matches.db synced.
)

echo.
echo ============================================================
echo  DEPLOY COMPLETE
echo  Launch : %USB_DEST%\R6Analyzer.exe
echo  USB    : %USB_DRIVE%\
echo ============================================================
echo.
echo  Preserved on USB:
echo    %USB_DEST%\data\           (database, models, recordings)
echo    %USB_DEST%\exports\        (exported reports and CSVs)
echo    %USB_DEST%\data\settings.json
echo.
pause