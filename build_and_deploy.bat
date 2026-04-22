@echo off
setlocal EnableExtensions EnableDelayedExpansion
cd /d "%~dp0"

echo ============================================================
echo  R6 Tactical Intelligence Engine Build + USB Deploy
echo ============================================================
echo.

REM -- Config ---------------------------------------------------
set "APP_NAME=R6Analyzer"
set "DIST_DIR=dist\%APP_NAME%"
set "DIST_INTERNAL=%DIST_DIR%\_internal"
set "TARGET_LABEL=R6_PROJ"
set "USB_DRIVE="
set "USB_DEST="
set "MODEL_SRC=data\models"
set "FFMPEG_SRC="
set "DEPLOY_WARNINGS=0"
set "EXIT_CODE=0"
set "FINAL_MESSAGE=Build and deploy completed."

REM -- Activate venv --------------------------------------------
if exist ".venv\Scripts\activate.bat" (
    call ".venv\Scripts\activate.bat"
) else (
    echo [ERROR] .venv not found.
    echo         Run setup.bat first.
    set "EXIT_CODE=1"
    set "FINAL_MESSAGE=.venv not found. Run setup.bat first."
    goto :finish
)

REM -- Validate required source assets ---------------------------
echo [1/6] Validating source assets...
call :require_file "R6Analyzer.spec" "PyInstaller spec"
if errorlevel 1 goto :finish
call :require_file "database\schema.sql" "database schema"
if errorlevel 1 goto :finish
call :require_file "integration\bin\r6-dissect.exe" "r6-dissect executable"
if errorlevel 1 goto :finish
call :require_file "integration\bin\libr6dissect.dll" "r6-dissect runtime DLL"
if errorlevel 1 goto :finish

echo.
echo [2/6] Building exe...
python -m pip show pyinstaller >nul 2>&1 || python -m pip install pyinstaller
pyinstaller R6Analyzer.spec --noconfirm
if errorlevel 1 (
    echo [ERROR] Build failed.
    set "EXIT_CODE=1"
    set "FINAL_MESSAGE=PyInstaller build failed."
    goto :finish
)
echo [OK] Build complete.
echo.

REM -- Patch dist with runtime files PyInstaller does not collect --
echo [3/6] Staging runtime files into dist...
if not exist "%DIST_DIR%" (
    echo [ERROR] Build output not found: %DIST_DIR%
    set "EXIT_CODE=1"
    set "FINAL_MESSAGE=Build output folder was not created."
    goto :finish
)

call :ensure_dir "%DIST_INTERNAL%\database"
call :ensure_dir "%DIST_INTERNAL%\integration\bin"

copy /Y "database\schema.sql" "%DIST_INTERNAL%\database\schema.sql" >nul
if errorlevel 1 (
    echo [ERROR] Failed to copy schema.sql into dist.
    set "EXIT_CODE=1"
    set "FINAL_MESSAGE=Failed to stage schema.sql into dist."
    goto :finish
)

copy /Y "integration\bin\r6-dissect.exe" "%DIST_INTERNAL%\integration\bin\r6-dissect.exe" >nul
if errorlevel 1 (
    echo [ERROR] Failed to copy r6-dissect.exe into dist.
    set "EXIT_CODE=1"
    set "FINAL_MESSAGE=Failed to stage r6-dissect.exe into dist."
    goto :finish
)

copy /Y "integration\bin\libr6dissect.dll" "%DIST_INTERNAL%\integration\bin\libr6dissect.dll" >nul
if errorlevel 1 (
    echo [ERROR] Failed to copy libr6dissect.dll into dist.
    set "EXIT_CODE=1"
    set "FINAL_MESSAGE=Failed to stage libr6dissect.dll into dist."
    goto :finish
)

for %%F in ("integration\bin\LICENSE" "integration\bin\README.md" "integration\bin\libr6dissect.h") do (
    if exist "%%~fF" copy /Y "%%~fF" "%DIST_INTERNAL%\integration\bin\" >nul
)

call :locate_ffmpeg
if defined FFMPEG_SRC (
    copy /Y "!FFMPEG_SRC!" "%DIST_DIR%\ffmpeg.exe" >nul
    if errorlevel 1 (
        echo [WARN] Found ffmpeg but failed to copy it into dist.
        set "DEPLOY_WARNINGS=1"
    ) else (
        echo [OK] ffmpeg bundled from !FFMPEG_SRC!
    )
) else (
    echo [WARN] ffmpeg.exe not found locally or on PATH.
    echo        Whisper will not run in the deployed build until ffmpeg.exe is placed next to %APP_NAME%.exe.
    set "DEPLOY_WARNINGS=1"
)
echo [OK] Runtime files staged.
echo.

REM -- Find USB Drive by Label ----------------------------------
echo [4/6] Searching for USB with label: %TARGET_LABEL%...
for /f "tokens=1" %%d in ('powershell -NoProfile -Command "Get-Volume -FileSystemLabel '%TARGET_LABEL%' | Select-Object -ExpandProperty DriveLetter"') do (
    set "USB_DRIVE=%%d:"
)

if "%USB_DRIVE%"==":" set "USB_DRIVE="
if "%USB_DRIVE%"=="" (
    echo [ERROR] Could not find a USB drive labeled "%TARGET_LABEL%".
    set "EXIT_CODE=1"
    set "FINAL_MESSAGE=USB drive with label %TARGET_LABEL% was not found."
    goto :finish
)

set "USB_DEST=%USB_DRIVE%\%APP_NAME%"
echo [OK] USB found at %USB_DRIVE%
echo.

REM -- Ensure destination layout exists -------------------------
echo [5/6] Preparing USB folders...
call :ensure_dir "%USB_DEST%"
call :ensure_dir "%USB_DEST%\data"
call :ensure_dir "%USB_DEST%\data\models"
call :ensure_dir "%USB_DEST%\data\recordings"
call :ensure_dir "%USB_DEST%\data\transcripts"
call :ensure_dir "%USB_DEST%\data\reports"
call :ensure_dir "%USB_DEST%\exports"
echo [OK] USB folder layout ready.
echo.

REM -- Sync build + runtime data --------------------------------
echo [6/6] Syncing build and runtime assets...
echo       Preserving USB data and exports folders.
echo.

robocopy "%DIST_DIR%" "%USB_DEST%" /E /PURGE /XO /R:3 /W:5 ^
    /XD data exports
if errorlevel 8 (
    echo [ERROR] Robocopy encountered a serious error while syncing the app (code %errorlevel%).
    set "EXIT_CODE=1"
    set "FINAL_MESSAGE=Robocopy failed while syncing the app to the USB."
    goto :finish
)

call :copy_if_exists "data\settings.json" "%USB_DEST%\data\settings.json" "settings.json"
call :copy_if_exists "data\matches.db" "%USB_DEST%\data\matches.db" "matches.db"
call :copy_if_exists "%MODEL_SRC%\model.gguf" "%USB_DEST%\data\models\model.gguf" "model.gguf"
call :copy_if_exists "%MODEL_SRC%\whisper-base.pt" "%USB_DEST%\data\models\whisper-base.pt" "whisper-base.pt"

echo.
echo ============================================================
echo  DEPLOY COMPLETE
echo  Launch : %USB_DEST%\R6Analyzer.exe
echo  USB    : %USB_DRIVE%\

if "%DEPLOY_WARNINGS%"=="1" (
    echo  Status : Completed with warnings
) else (
    echo  Status : Ready
)
echo ============================================================
echo.
echo  Verified layout:
echo    %USB_DEST%\_internal\database\schema.sql
echo    %USB_DEST%\_internal\integration\bin\r6-dissect.exe
echo    %USB_DEST%\_internal\integration\bin\libr6dissect.dll
echo    %USB_DEST%\data\models\model.gguf
echo    %USB_DEST%\data\models\whisper-base.pt
echo    %USB_DEST%\data\settings.json
echo    %USB_DEST%\data\matches.db
echo.
if "%DEPLOY_WARNINGS%"=="1" (
    set "FINAL_MESSAGE=Deploy completed with warnings. Review them before relying on the USB build."
) else (
    set "FINAL_MESSAGE=Deploy completed successfully. The USB build should be ready."
)
goto :finish

:require_file
if exist "%~1" (
    echo [OK] Found %~2: %~1
    exit /b 0
)
echo [ERROR] Missing %~2: %~1
set "EXIT_CODE=1"
set "FINAL_MESSAGE=Missing required source file: %~1"
exit /b 1

:ensure_dir
if not exist "%~1" mkdir "%~1"
exit /b 0

:locate_ffmpeg
set "FFMPEG_SRC="
for %%F in ("%CD%\ffmpeg.exe" "%CD%\tools\ffmpeg.exe" "%CD%\integration\bin\ffmpeg.exe") do (
    if exist "%%~fF" if not defined FFMPEG_SRC set "FFMPEG_SRC=%%~fF"
)
if defined FFMPEG_SRC exit /b 0

for /f "delims=" %%F in ('where ffmpeg 2^>nul') do (
    if not defined FFMPEG_SRC set "FFMPEG_SRC=%%~fF"
)
exit /b 0

:copy_if_exists
set "SRC=%~1"
set "DEST=%~2"
set "LABEL=%~3"

if exist "!SRC!" (
    copy /Y "!SRC!" "!DEST!" >nul
    if errorlevel 1 (
        echo [WARN] Failed to copy !LABEL! to !DEST!
        set "DEPLOY_WARNINGS=1"
    ) else (
        echo [OK] !LABEL! synced.
    )
) else (
    echo [WARN] !LABEL! not found at !SRC!
    set "DEPLOY_WARNINGS=1"
)
exit /b 0

:finish
echo.
echo ============================================================
if "%EXIT_CODE%"=="0" (
    echo  Final Status: SUCCESS
) else (
    echo  Final Status: FAILURE
)
echo  %FINAL_MESSAGE%
echo ============================================================
echo.
pause
exit /b %EXIT_CODE%
