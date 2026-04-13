@echo off
setlocal enabledelayedexpansion

echo ============================================================
echo  R6 Tactical Intelligence Engine — Build + USB Deploy
echo ============================================================
echo.

REM ── Config ───────────────────────────────────────────────────
set APP_NAME=R6Analyzer
set DIST_DIR=dist\%APP_NAME%
set USB_DRIVE=E:
set USB_DEST=%USB_DRIVE%\%APP_NAME%

REM Allow override: build_and_deploy.bat F:  (different drive letter)
if not "%1"=="" set USB_DRIVE=%1
if not "%1"=="" set USB_DEST=%1\%APP_NAME%

echo Target USB: %USB_DEST%
echo.

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

REM ── Config ───────────────────────────────────────────────────
set APP_NAME=R6Analyzer
set DIST_DIR=dist\%APP_NAME%
set TARGET_LABEL=R6_PROJ

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

REM ── Copy build to USB ─────────────────────────────────────────
echo [3/4] Copying build to %USB_DEST%...
if exist "%USB_DEST%" (
    echo      Removing old build...
    rmdir /s /q "%USB_DEST%"
)
xcopy "%DIST_DIR%" "%USB_DEST%\" /e /i /h /y /q
if errorlevel 1 (
    echo [ERROR] Copy failed. Check USB is writable.
    pause & exit /b 1
)
echo [OK] Build copied.
echo.

REM ── Copy model files if they exist ───────────────────────────
echo [4/4] Copying model files...

set MODEL_DEST=%USB_DEST%\data\models
if not exist "%MODEL_DEST%" mkdir "%MODEL_DEST%"

if exist "data\models\model.gguf" (
    echo      Copying model.gguf...
    copy /y "data\models\model.gguf" "%MODEL_DEST%\model.gguf" >nul
    echo      [OK] model.gguf copied.
) else (
    echo      [WARN] model.gguf not found in data\models\ — copy manually.
)

if exist "data\models\whisper-base.pt" (
    echo      Copying whisper-base.pt...
    copy /y "data\models\whisper-base.pt" "%MODEL_DEST%\whisper-base.pt" >nul
    echo      [OK] whisper-base.pt copied.
) else (
    echo      [WARN] whisper-base.pt not found in data\models\ — copy manually.
)

REM ── Copy existing DB if present (preserve match history) ─────
if exist "data\matches.db" (
    echo      Copying matches.db...
    set DB_DEST=%USB_DEST%\data
    if not exist "!DB_DEST!" mkdir "!DB_DEST!"
    copy /y "data\matches.db" "!DB_DEST!\matches.db" >nul
    echo      [OK] matches.db copied.
)

echo.
echo ============================================================
echo  DEPLOY COMPLETE
echo  Launch:  %USB_DEST%\%APP_NAME%.exe
echo ============================================================
echo.

REM ── USB structure reminder ───────────────────────────────────
echo  Expected USB structure:
echo  %USB_DRIVE%\
echo    %APP_NAME%\
echo      R6Analyzer.exe
echo      data\
echo        matches.db
echo        models\
echo          model.gguf          ^(~4.4GB^)
echo          whisper-base.pt     ^(~145MB^)
echo        recordings\
echo        transcripts\
echo        reports\
echo      integration\
echo        bin\
echo          r6-dissect.exe
echo      database\
echo        schema.sql
echo.
pause