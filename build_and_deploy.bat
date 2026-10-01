@echo off
setlocal EnableExtensions EnableDelayedExpansion
cd /d "%~dp0"

REM ============================================================
REM  R6 Tactical Intelligence Engine -- Build + Deploy (ALL-IN-ONE)
REM ============================================================
REM  This is the ONLY script you should need to run. It used to be split
REM  across build.bat, build_and_deploy.bat, build_and_deploy_logged.bat,
REM  make_usb.bat, setup.bat, setup_logged.bat, run_setup_logged.ps1 and
REM  run_build_and_deploy_logged.ps1 -- eight files doing overlapping
REM  pieces of the same job. Everything they did now lives here:
REM
REM    SETUP  - create/refresh the venv, install/verify every dependency,
REM             create data folders + default settings.json, initialize
REM             the database -- runs automatically, every time, so a venv
REM             that's missing a newly-added package (exactly what
REM             happened with resemblyzer/scikit-learn after Milestone 6)
REM             can never silently go stale. It's fast when everything is
REM             already installed -- pip just confirms each package in a
REM             second or two -- so there's no real cost to always doing it.
REM    BUILD  - PyInstaller for both the client (R6Analyzer.exe) and the
REM             server (R6Server.exe), with server_data preserved across
REM             the rebuild.
REM    STAGE  - copy in the runtime files PyInstaller itself doesn't
REM             collect (schema.sql, r6-dissect, ffmpeg, the Whisper
REM             model, a portable Ollama install).
REM    DEPLOY - sync the client + server builds onto the USB drive
REM             labeled R6_PROJ, preserving your data/exports on it.
REM
REM  setup_tailscale_funnel.bat is intentionally NOT folded in here -- it
REM  configures network reachability on whichever machine ends up
REM  *hosting* the server, which is very often a different computer than
REM  the one you build on, and it's a one-time, opt-in thing rather than
REM  part of every build.
REM
REM  The other eight files above have been replaced with short "this has
REM  moved" stubs so nothing that still references them breaks. Run
REM  cleanup_old_build_scripts.bat once, whenever you're ready, to delete
REM  those stubs for good.
REM
REM  This file calls out to a handful of small helper scripts it needs
REM  (a couple .bat files, a few .ps1 files) that live in the build_scripts\
REM  subfolder next to it. You never need to open or run any of those
REM  directly -- they exist purely so this one file, the one you actually
REM  run, doesn't have a dozen of them cluttering the same folder.
REM ============================================================

REM -- Self-wrap with logging + a guaranteed pause on a standalone run --
REM When double-clicked directly -- the normal case -- nothing else is
REM capturing the output or holding the window open, so a crash or the
REM window simply closing leaves nothing to look at afterward. To fix
REM that, a standalone run relaunches itself through
REM build_scripts\run_and_stream_to_log.ps1,
REM writes a real timestamped log file under logs\, and always ends with a
REM single explicit pause so the window cannot disappear before you've had
REM a chance to read it.
if defined R6_SKIP_PAUSE goto :main

if not exist "logs" mkdir "logs"
for /f "usebackq delims=" %%I in (`powershell -NoProfile -Command "Get-Date -Format 'yyyyMMdd_HHmmss'"`) do set "BD_STAMP=%%I"
set "BD_LOG=%CD%\logs\build_and_deploy_%BD_STAMP%.log"
echo ============================================================
echo  R6 Tactical Intelligence Engine Build + USB Deploy
echo ============================================================
echo  Full output is also being saved to:
echo    %BD_LOG%
echo  If this window closes before you finish reading, open that
echo  file afterward to see exactly what happened.
echo ============================================================
echo.
set "R6_SKIP_PAUSE=1"
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0build_scripts\run_and_stream_to_log.ps1" -ScriptPath "%~f0" -LogFile "%BD_LOG%" -StepName "BUILD"
set "BD_EXIT=%ERRORLEVEL%"

REM Cross-check against the log itself rather than trust the relayed exit
REM code alone -- two separate reasons this matters. First, the exit code
REM relayed back through powershell.exe -> cmd.exe -> this batch has, in
REM practice, been observed to come back 0 even when the inner run's own
REM "Final Status" line (see :finish below) correctly said FAILURE.
REM Second, and more seriously: a raw cmd.exe fatal error -- a bad label,
REM a parenthesis-parsing crash, exactly the two real bugs already found
REM and fixed in this project's history -- aborts the inner script
REM immediately and skips :finish entirely, so it never writes *any*
REM "Final Status" line at all. The previous version of this check only
REM looked for an explicit FAILURE marker, so a run that crashed hard
REM enough to never reach :finish left neither marker, kept BD_EXIT at
REM its (wrongly relayed) 0, and reported SUCCESS despite having done
REM nothing -- exactly what happened the time this script died on
REM "The system cannot find the batch label specified". Requiring the
REM actual positive SUCCESS marker, instead of merely the absence of a
REM FAILURE one, means anything short of a confirmed clean finish -- a
REM real failure, a hard crash, or a hang -- is reported as a failure.
findstr /C:"Final Status: SUCCESS" "%BD_LOG%" >nul 2>&1
if errorlevel 1 set "BD_EXIT=1"
echo.
echo ============================================================
if "%BD_EXIT%"=="0" (
    echo  Final Status: SUCCESS
) else (
    echo  Final Status: FAILURE ^(exit code %BD_EXIT%^)
)
echo  Full log saved to: %BD_LOG%
echo ============================================================
echo.
echo Press any key to close this window . . .
pause >nul
exit /b %BD_EXIT%

:main
echo ============================================================
echo  R6 Tactical Intelligence Engine Build + USB Deploy
echo ============================================================
echo.

REM -- Config ---------------------------------------------------
set "APP_NAME=R6Analyzer"
set "DIST_DIR=dist\%APP_NAME%"
set "DIST_INTERNAL=%DIST_DIR%\_internal"
set "SERVER_APP_NAME=R6Server"
set "SERVER_DIST_DIR=dist\%SERVER_APP_NAME%"
set "SERVER_DIST_INTERNAL=%SERVER_DIST_DIR%\_internal"
set "TARGET_LABEL=R6_PROJ"
REM Safety ceiling for the drive-identity check further down: a volume
REM labeled R6_PROJ but bigger than this many GB is treated as a
REM probable wrong-drive match (e.g. a large external/backup drive)
REM rather than trusted automatically. Raise this if the real USB stick
REM legitimately grows past it.
set "USB_SIZE_CEILING_GB=512"
set "USB_DRIVE="
set "USB_DEST="
set "USB_SERVER_DEST="
set "MODEL_SRC=data\models"
set "OLLAMA_SRC=ollama"
set "FFMPEG_SRC="
set "DEPLOY_WARNINGS=0"
set "SETUP_WARNINGS=0"
set "EXIT_CODE=0"
set "FINAL_MESSAGE=Build and deploy completed."

REM -- Environment setup (formerly setup.bat) --------------------
REM Always runs, not just on a first-ever build: pip confirming an
REM already-installed package takes a second or two, so there's no real
REM downside, and it means a venv that's missing a dependency someone
REM just added to requirements.txt gets caught and fixed automatically
REM instead of silently degrading (that's exactly how the Milestone 6
REM speaker-diarization packages went unnoticed for a while).
echo [SETUP] Preparing the Python environment...
call "%~dp0build_scripts\env_setup.bat"
if errorlevel 1 (
    set "EXIT_CODE=1"
    set "FINAL_MESSAGE=Environment setup failed -- see above."
    goto :finish
)
if "%SETUP_WARNINGS%"=="1" set "DEPLOY_WARNINGS=1"
echo [OK] Environment ready.
echo.

call ".venv\Scripts\activate.bat"
if errorlevel 1 (
    echo [ERROR] Failed to activate the virtual environment even after setup.
    set "EXIT_CODE=1"
    set "FINAL_MESSAGE=Could not activate .venv."
    goto :finish
)

REM -- Validate required source assets ---------------------------
REM Inlined rather than a shared `call :require_file` subroutine on
REM purpose: this file grew large enough today that a `call` to a label
REM defined hundreds of lines away started intermittently failing with
REM "The system cannot find the batch label specified" -- reproduced
REM live, not theoretical (the label genuinely existed; a `call` to it
REM would work once and then fail on the very next call in the same
REM run, a known-flaky cmd.exe behavior in large batch files, not
REM anything about the label itself being wrong). Inlining every check
REM removes the dependency on that lookup entirely for the checks that
REM actually hit it.
echo [1/7] Validating source assets...
if not exist "R6Analyzer.spec" (
    echo [ERROR] Missing PyInstaller spec: R6Analyzer.spec
    set "EXIT_CODE=1"
    set "FINAL_MESSAGE=Missing required source file: R6Analyzer.spec"
    goto :finish
) else (
    echo [OK] Found PyInstaller spec: R6Analyzer.spec
)
if not exist "database\schema.sql" (
    echo [ERROR] Missing database schema: database\schema.sql
    set "EXIT_CODE=1"
    set "FINAL_MESSAGE=Missing required source file: database\schema.sql"
    goto :finish
) else (
    echo [OK] Found database schema: database\schema.sql
)
if not exist "integration\bin\r6-dissect.exe" (
    echo [ERROR] Missing r6-dissect executable: integration\bin\r6-dissect.exe
    set "EXIT_CODE=1"
    set "FINAL_MESSAGE=Missing required source file: integration\bin\r6-dissect.exe"
    goto :finish
) else (
    echo [OK] Found r6-dissect executable: integration\bin\r6-dissect.exe
)
if not exist "integration\bin\libr6dissect.dll" (
    echo [ERROR] Missing r6-dissect runtime DLL: integration\bin\libr6dissect.dll
    set "EXIT_CODE=1"
    set "FINAL_MESSAGE=Missing required source file: integration\bin\libr6dissect.dll"
    goto :finish
) else (
    echo [OK] Found r6-dissect runtime DLL: integration\bin\libr6dissect.dll
)
if not exist "R6Server.spec" (
    echo [ERROR] Missing server PyInstaller spec: R6Server.spec
    set "EXIT_CODE=1"
    set "FINAL_MESSAGE=Missing required source file: R6Server.spec"
    goto :finish
) else (
    echo [OK] Found server PyInstaller spec: R6Server.spec
)
if not exist "server_main.py" (
    echo [ERROR] Missing server entry point: server_main.py
    set "EXIT_CODE=1"
    set "FINAL_MESSAGE=Missing required source file: server_main.py"
    goto :finish
) else (
    echo [OK] Found server entry point: server_main.py
)

echo.
echo [2/7] Building client exe (%APP_NAME%)...

REM Milestone 5: embed the server's self-provisioned API token and Tailscale
REM Funnel public address (if setup_tailscale_funnel.bat has been run) into
REM app/server_credentials.py right before PyInstaller reads the source
REM tree, so they end up baked into R6Analyzer.exe and nobody has to type a
REM server URL/API key into Settings by hand. The clear step below always
REM runs immediately after the PyInstaller call -- success or failure -- so
REM the real values never sit in the working tree longer than this one
REM build step takes.
REM
REM 2026-09-10 fix: server/config.py resolves server_data\ relative to the
REM process's own working directory, not the repo root -- so a deployed
REM R6Server.exe run from dist\R6Server\ self-provisions its OWN, separate
REM api_token there the first time it's actually run, independent of the
REM one this build_and_deploy.bat sees under the project root. Left alone,
REM the client below would embed a token the real deployed server has never
REM heard of. sync_deployed_server_config.py copies dist\R6Server\server_data
REM 's token (and public_url, if set) into the project root's server_data
REM first, if that deployed copy exists, so the embed step right after it
REM always reflects whatever server is actually running. It's a no-op (exit
REM 0) the first time you ever build, before R6Server.exe has been run once.
echo       Syncing embedded credentials with the actually-deployed server (if any)...
python scripts\sync_deployed_server_config.py
if errorlevel 1 (
    echo [WARN] Could not sync deployed server config -- continuing with the
    echo        project root's own server_data as-is. If the client build's
    echo        API key doesn't match a server you've already deployed, copy
    echo        the deployed dist\R6Server\server_data\server_config.json's
    echo        api_token into server_data\server_config.json by hand, or
    echo        just paste the deployed server's token into the client's
    echo        Settings -^> Remote Sync -^> API Key manually.
    set "DEPLOY_WARNINGS=1"
)

echo       Embedding server credentials for this build...
python scripts\embed_client_credentials.py --write
if errorlevel 1 (
    echo [WARN] Could not embed server credentials -- continuing without them.
    echo        The built client will need a server URL/API key typed into
    echo        Settings -^> Remote Sync manually, same as before this feature.
    set "DEPLOY_WARNINGS=1"
)

pyinstaller R6Analyzer.spec --noconfirm
set "CLIENT_BUILD_EXIT=%ERRORLEVEL%"

echo       Clearing embedded credentials from the source tree...
python scripts\embed_client_credentials.py --clear

if not "%CLIENT_BUILD_EXIT%"=="0" (
    echo [ERROR] Client build failed.
    set "EXIT_CODE=1"
    set "FINAL_MESSAGE=PyInstaller build failed for %APP_NAME%."
    goto :finish
)
echo [OK] Client build complete.

echo.
echo [3/7] Building server exe (%SERVER_APP_NAME%)...
echo       This is a completely separate, headless build from the client --
echo       see R6Server.spec. It runs on whichever machine (this one, your
echo       laptop, anywhere) you want to act as the remote server; it does
echo       not need to go on the USB stick to work, though the USB copy
echo       below gives you a portable way to carry it to that machine.

REM The COLLECT step below wipes dist\R6Server, which fails outright while
REM R6Server.exe is running (the "file in use" failure described next).
REM Stop it here instead of leaving that as something to remember before
REM every build, and remember whether it WAS running so :finish can start
REM it again -- both on success and on failure, so a failed build never
REM silently leaves the server down.
set "SERVER_WAS_RUNNING="
tasklist /FI "IMAGENAME eq %SERVER_APP_NAME%.exe" 2>nul | find /I "%SERVER_APP_NAME%.exe" >nul
if not errorlevel 1 (
    echo       %SERVER_APP_NAME%.exe is running -- stopping it for the rebuild...
    set "SERVER_WAS_RUNNING=1"
    taskkill /IM "%SERVER_APP_NAME%.exe" /F >nul 2>&1
    REM Let Windows release the file handles before PyInstaller deletes the folder.
    ping -n 4 127.0.0.1 >nul
)

REM PyInstaller's COLLECT step deletes the ENTIRE existing dist\R6Server
REM folder before repopulating it (this is also what caused the earlier
REM "file in use" build failure when R6Server.exe was left running) --
REM including server_data\, where the server keeps its self-provisioned
REM API token, its Tailscale Funnel address, and anything you've dropped in
REM manually (the Whisper model, a portable Ollama install). Left
REM unhandled, every single rebuild silently rotates the token out from
REM under every client already built against it and throws away anything
REM seeded into server_data\. Back it up here, restore it immediately after
REM the build -- success or failure -- so a rebuild never loses it.
set "SERVER_DATA_BACKUP=%TEMP%\r6server_data_backup_%RANDOM%"
if exist "%SERVER_DIST_DIR%\server_data" (
    echo       Preserving existing server_data across the rebuild...
    if not exist "%SERVER_DATA_BACKUP%" mkdir "%SERVER_DATA_BACKUP%"
    xcopy /Y /E /I /Q "%SERVER_DIST_DIR%\server_data\*" "%SERVER_DATA_BACKUP%\" >nul
)

pyinstaller R6Server.spec --noconfirm
set "SERVER_BUILD_EXIT=%ERRORLEVEL%"

if exist "%SERVER_DATA_BACKUP%" (
    echo       Restoring preserved server_data...
    if not exist "%SERVER_DIST_DIR%\server_data" mkdir "%SERVER_DIST_DIR%\server_data"
    xcopy /Y /E /I /Q "%SERVER_DATA_BACKUP%\*" "%SERVER_DIST_DIR%\server_data\" >nul
    rmdir /S /Q "%SERVER_DATA_BACKUP%" 2>nul
)

if not "%SERVER_BUILD_EXIT%"=="0" (
    echo [ERROR] Server build failed.
    echo         If this is a missing-module error, this script's own SETUP
    echo         phase above should have already installed everything in
    echo         server\requirements.txt -- check its output for a step that
    echo         failed or was skipped.
    set "EXIT_CODE=1"
    set "FINAL_MESSAGE=PyInstaller build failed for %SERVER_APP_NAME%."
    goto :finish
)
echo [OK] Server build complete.
echo.

REM -- Teammates' voice recorder ---------------------------------
REM R6Voice.exe is a small standalone app teammates run during practice to
REM record their own mic (see voice_recorder\). It carries the server
REM address and the narrow voice-upload key only, embedded the same way as
REM the client's credentials and cleared from the source tree right after.
REM A failure here is a warning: the client and server are unaffected.
echo       Building the teammate voice recorder (R6Voice.exe)...
REM R6Companion.exe (companion\) is built in the same window: it runs from a
REM teammate's USB stick next to a portable OBS, carries the same voice key,
REM and follows this app's sessions through the server.
python scripts\embed_client_credentials.py --write-voice
pyinstaller R6Voice.spec --noconfirm
set "VOICE_BUILD_EXIT=%ERRORLEVEL%"
pyinstaller R6Companion.spec --noconfirm
set "COMPANION_BUILD_EXIT=%ERRORLEVEL%"
python scripts\embed_client_credentials.py --clear-voice
if not "%VOICE_BUILD_EXIT%"=="0" (
    echo [WARN] R6Voice build failed -- the client and server builds are unaffected.
    set "DEPLOY_WARNINGS=1"
) else (
    echo [OK] R6Voice.exe built: dist\R6Voice.exe
)
if not "%COMPANION_BUILD_EXIT%"=="0" (
    echo [WARN] R6Companion build failed -- the client and server builds are unaffected.
    set "DEPLOY_WARNINGS=1"
) else (
    echo [OK] R6Companion.exe built: dist\R6Companion.exe
)
echo.

REM -- Patch dist with runtime files PyInstaller does not collect --
echo [4/7] Staging runtime files into dist...
if not exist "%DIST_DIR%" (
    echo [ERROR] Build output not found: %DIST_DIR%
    set "EXIT_CODE=1"
    set "FINAL_MESSAGE=Build output folder was not created."
    goto :finish
)

if not exist "%DIST_INTERNAL%\database" mkdir "%DIST_INTERNAL%\database"
if not exist "%DIST_INTERNAL%\integration\bin" mkdir "%DIST_INTERNAL%\integration\bin"
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

call "%~dp0build_scripts\locate_ffmpeg.bat"
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

REM qt.conf tells Qt/Windows this process is per-monitor DPI aware. Without
REM it, a PyInstaller-frozen PySide6 exe is left DPI-unaware on Windows, so
REM the OS bitmap-stretches the whole rendered window to match the display's
REM actual scaling -- on anything other than 100% scaling (the default on
REM most laptops, and very commonly changed by RDP sessions too) that shows
REM up as the entire UI looking "scrunched" and cut off at the edges, even
REM though every layout/geometry calculation inside the app (see
REM MainWindow._fit_to_screen) is correct. Qt looks for qt.conf next to the
REM .exe, not inside _internal, so it goes straight into DIST_DIR here.
copy /Y "resources\qt.conf" "%DIST_DIR%\qt.conf" >nul
if errorlevel 1 (
    echo [WARN] Failed to copy qt.conf into dist -- the built exe may render
    echo        at the wrong scale on non-100%% displays.
    set "DEPLOY_WARNINGS=1"
) else (
    echo [OK] qt.conf staged ^(per-monitor DPI awareness^).
)

echo [OK] Runtime files staged.
echo.

REM -- Stage the same runtime files into the server's dist too ---
REM Milestone 4 phase 1: the server now runs Whisper transcription itself
REM (see server/services/session_processing.py), so it needs the same
REM r6-dissect + ffmpeg + Whisper-model files the client does. r6-dissect.exe
REM itself is already bundled by R6Server.spec's own datas; libr6dissect.dll
REM and ffmpeg.exe are staged here the same way as for the client above.
if not exist "%SERVER_DIST_DIR%" (
    echo [ERROR] Server build output not found: %SERVER_DIST_DIR%
    set "EXIT_CODE=1"
    set "FINAL_MESSAGE=Server build output folder was not created."
    goto :finish
)

if not exist "%SERVER_DIST_INTERNAL%\integration\bin" mkdir "%SERVER_DIST_INTERNAL%\integration\bin"
copy /Y "integration\bin\libr6dissect.dll" "%SERVER_DIST_INTERNAL%\integration\bin\libr6dissect.dll" >nul
if errorlevel 1 (
    echo [WARN] Failed to copy libr6dissect.dll into the server build.
    set "DEPLOY_WARNINGS=1"
)

if defined FFMPEG_SRC (
    copy /Y "!FFMPEG_SRC!" "%SERVER_DIST_DIR%\ffmpeg.exe" >nul
    if errorlevel 1 (
        echo [WARN] Found ffmpeg but failed to copy it into the server build.
        set "DEPLOY_WARNINGS=1"
    )
) else (
    echo [WARN] ffmpeg.exe not found — server-side transcription will not run
    echo        until ffmpeg.exe is placed next to %SERVER_APP_NAME%.exe.
    set "DEPLOY_WARNINGS=1"
)

REM Seed server_data/models/ with the same Whisper model the client uses,
REM so the server can transcribe out of the box. Checks the DESTINATION
REM first: once a model is there (from a prior build, from the server
REM having already run once, or from you dropping it in by hand) it's left
REM alone and reported as already present, rather than warning just because
REM MODEL_SRC (the client's own copy) happens not to exist on this machine.
if exist "%SERVER_DIST_DIR%\server_data\models\whisper-base.pt" (
    echo [OK] Server already has a Whisper model.
) else if exist "%MODEL_SRC%\whisper-base.pt" (
    if not exist "%SERVER_DIST_DIR%\server_data\models" mkdir "%SERVER_DIST_DIR%\server_data\models"
    copy /Y "%MODEL_SRC%\whisper-base.pt" "%SERVER_DIST_DIR%\server_data\models\whisper-base.pt" >nul
    if errorlevel 1 (
        echo [WARN] Failed to seed the server's Whisper model.
        set "DEPLOY_WARNINGS=1"
    ) else (
        echo [OK] Seeded server Whisper model from %MODEL_SRC%\whisper-base.pt
    )
) else (
    echo [WARN] No Whisper model at %MODEL_SRC%\whisper-base.pt to seed the server with —
    echo        server-side transcription will not work until one is placed at
    echo        %SERVER_DIST_DIR%\server_data\models\whisper-base.pt
    set "DEPLOY_WARNINGS=1"
)

REM Milestone 4 phase 2: seed server_data/ollama/ with a portable Ollama
REM install. Same destination-first check as the Whisper model above --
REM this used to check ONLY the OLLAMA_SRC seed folder next to the repo, so
REM if you extracted ollama-windows-amd64.zip straight into
REM server_data\ollama\ yourself (which is what the old warning text below
REM actually told you to do), it would keep warning "not found" forever
REM even though the server already had everything it needed. Now it checks
REM the destination first, so either way of providing it is recognized.
if exist "%SERVER_DIST_DIR%\server_data\ollama\ollama.exe" (
    echo [OK] Server already has a portable Ollama install.
) else if exist "%OLLAMA_SRC%\ollama.exe" (
    if not exist "%SERVER_DIST_DIR%\server_data\ollama" mkdir "%SERVER_DIST_DIR%\server_data\ollama"
    xcopy /Y /E /I "%OLLAMA_SRC%\*" "%SERVER_DIST_DIR%\server_data\ollama\" >nul
    if errorlevel 1 (
        echo [WARN] Failed to seed the server's portable Ollama install.
        set "DEPLOY_WARNINGS=1"
    ) else (
        echo [OK] Seeded server Ollama install from %OLLAMA_SRC%\
    )
) else (
    echo [WARN] No portable Ollama at %OLLAMA_SRC%\ollama.exe to seed the server with —
    echo        AI analysis will not run until ollama-windows-amd64.zip's contents
    echo        (from https://github.com/ollama/ollama/releases^) are extracted to
    echo        %SERVER_DIST_DIR%\server_data\ollama\
    set "DEPLOY_WARNINGS=1"
)
echo [OK] Server runtime files staged.
echo.

REM Build+stage only, no local USB step at all: used by remote_deploy.bat
REM when this is triggered over SSH from the laptop and the USB is
REM physically plugged into the LAPTOP, not this machine -- there is no
REM local drive to find, so skip straight past detection instead of
REM burning time on it (and, critically, instead of ever reaching the
REM manual drive-letter prompt further down, which does `set /p` and
REM would hang forever waiting for input that a non-interactive SSH
REM session can never provide). remote_deploy.bat does its own network
REM push to the laptop after this script returns.
if defined R6_SKIP_USB_DEPLOY (
    echo [SKIP] R6_SKIP_USB_DEPLOY is set -- build and staging are done,
    echo        skipping local USB detection and sync.
    set "FINAL_MESSAGE=Build and staging completed successfully. USB sync skipped (R6_SKIP_USB_DEPLOY)."
    goto :finish
)

REM -- Find USB Drive by Label ----------------------------------
echo [5/7] Searching for USB with label: %TARGET_LABEL%...
for /f "tokens=1" %%d in ('powershell -NoProfile -Command "Get-Volume -FileSystemLabel '%TARGET_LABEL%' -ErrorAction SilentlyContinue | Select-Object -ExpandProperty DriveLetter"') do (
    set "USB_DRIVE=%%d:"
)
if "%USB_DRIVE%"==":" set "USB_DRIVE="

REM Get-Volume only sees genuine local volumes. Running this while RDP'd
REM into this PC with client drive redirection on shows the USB (plugged
REM into the far end -- your laptop) as a drive letter here too, but it
REM is a redirected network drive under the hood, not a real local
REM volume, so Get-Volume can't see it even though File Explorer can.
REM Win32_LogicalDisk enumerates every drive letter regardless of type
REM (local, removable, or network/redirected), so try that next.
if "%USB_DRIVE%"=="" (
    echo       Not found as a local volume -- checking mapped/redirected drives too...
    for /f "tokens=1" %%d in ('powershell -NoProfile -Command "Get-CimInstance -ClassName Win32_LogicalDisk -ErrorAction SilentlyContinue | Where-Object { $_.VolumeName -eq '%TARGET_LABEL%' } | Select-Object -First 1 -ExpandProperty DeviceID"') do (
        set "USB_DRIVE=%%d"
    )
)

REM Last resort: ask for the drive letter directly. This is the reliable
REM path over RDP -- you can see the redirected drive's own letter in
REM File Explorer (e.g. "R6_PROJ on YOURLAPTOP" mapped to D:^) even in
REM cases the two automated checks above don't catch.
REM
REM Only ask when there is genuinely a console to ask on. `set /p` does
REM NOT fail gracefully when stdin is an inherited non-console handle --
REM a hidden process, a build whose output is redirected to a log file,
REM anything scripted -- it blocks forever on input that can never
REM arrive, which silently wedges the whole build. Opening CON for
REM reading succeeds only when a real console is attached, so this tells
REM those cases apart BEFORE committing to a prompt. Set
REM R6_NONINTERACTIVE=1 to force the unattended path explicitly.
set "R6_HAS_CONSOLE="
>nul 2>nul <con rem && set "R6_HAS_CONSOLE=1"
if defined R6_NONINTERACTIVE set "R6_HAS_CONSOLE="

if "%USB_DRIVE%"=="" if defined R6_HAS_CONSOLE (
    echo.
    echo [WARN] Could not auto-detect a drive labeled "%TARGET_LABEL%".
    echo        If you can see it in File Explorer right now ^(including
    echo        as a redirected drive over RDP^), type its drive letter
    echo        below. Otherwise just press Enter to skip.
    set /p "MANUAL_DRIVE=Drive letter, e.g. D, or Enter to skip: "
    if not "!MANUAL_DRIVE!"=="" (
        set "MANUAL_DRIVE=!MANUAL_DRIVE:~0,1!"
        if exist "!MANUAL_DRIVE!:\" (
            set "USB_DRIVE=!MANUAL_DRIVE!:"
        ) else (
            echo [WARN] !MANUAL_DRIVE!:\ does not exist or is not accessible.
        )
    )
)

if "%USB_DRIVE%"==":" set "USB_DRIVE="
if "%USB_DRIVE%"=="" (
    REM Deliberately NOT a build failure. Both exes and every staged
    REM runtime file are finished and usable at the paths below; the only
    REM thing that did not happen is the copy onto the stick. That is the
    REM normal, expected state when building with the USB unplugged or
    REM from the laptop, and failing the whole build for it would mean
    REM you can never rebuild without the stick in hand.
    echo.
    echo [WARN] No drive labeled "%TARGET_LABEL%" is attached -- skipping the USB sync.
    echo        The build itself finished fine and is ready here:
    echo          %DIST_DIR%
    echo          %SERVER_DIST_DIR%
    echo        Plug the stick in and run this again to copy it across.
    set "DEPLOY_WARNINGS=1"
    set "FINAL_MESSAGE=Build completed. USB sync skipped -- no %TARGET_LABEL% drive attached."
    goto :finish
)

REM ============================================================
REM  HARD SAFETY GATE -- confirm identity before anything is written
REM ============================================================
REM Every path above (auto-detected as a local volume, auto-detected
REM as a redirected/network drive, or typed in by hand) funnels through
REM here, and none of them is trusted just because a drive letter came
REM out the other end. This re-checks the actual volume label AND the
REM drive's total size against sanity limits, and refuses outright on
REM any mismatch -- a wrong drive here doesn't just corrupt a build, it
REM runs /PURGE against whatever that drive actually holds.
echo       Verifying %USB_DRIVE% is really "%TARGET_LABEL%" before touching it...
set "VERIFY_STATUS="
set "VERIFY_LABEL="
set "VERIFY_SIZE_GB="
for /f "usebackq tokens=1,2,3 delims=|" %%A in (`powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0build_scripts\verify_usb_drive.ps1" -DriveLetter "%USB_DRIVE:~0,1%" -ExpectedLabel "%TARGET_LABEL%" -MaxSizeGb %USB_SIZE_CEILING_GB%`) do (
    set "VERIFY_STATUS=%%A"
    set "VERIFY_LABEL=%%B"
    set "VERIFY_SIZE_GB=%%C"
)

if "%VERIFY_STATUS%"=="OK" (
    echo [OK] Confirmed: %USB_DRIVE% is "%VERIFY_LABEL%" ^(%VERIFY_SIZE_GB% GB^).
) else if "%VERIFY_STATUS%"=="LABEL_MISMATCH" (
    echo.
    echo [ERROR] Refusing to continue: %USB_DRIVE% is labeled "%VERIFY_LABEL%",
    echo         not "%TARGET_LABEL%". This is almost certainly the wrong
    echo         drive -- a different local disk, a different USB stick, or
    echo         a mistyped letter. Nothing has been written.
    echo         Double-check the drive letter in File Explorer -- on RDP,
    echo         look under "Redirected drives and folders", shown as
    echo         "^<letter^> on ^<computer name^>".
    set "EXIT_CODE=1"
    set "FINAL_MESSAGE=Refused to deploy: %USB_DRIVE% is labeled %VERIFY_LABEL%, not %TARGET_LABEL%."
    goto :finish
) else if "%VERIFY_STATUS%"=="TOO_LARGE" (
    echo.
    echo [ERROR] Refusing to continue: %USB_DRIVE% is labeled "%TARGET_LABEL%"
    echo         but is %VERIFY_SIZE_GB% GB -- far bigger than a project USB
    echo         stick should be. Treating this as a likely wrong-drive match
    echo         ^(e.g. a large external/backup drive^) rather than risk it.
    echo         Nothing has been written. If this really is the right drive,
    echo         raise USB_SIZE_CEILING_GB near the top of this script.
    set "EXIT_CODE=1"
    set "FINAL_MESSAGE=Refused to deploy: %USB_DRIVE% is %VERIFY_SIZE_GB% GB, above the safety ceiling."
    goto :finish
) else (
    echo.
    echo [ERROR] Could not verify %USB_DRIVE% -- it disappeared or is no
    echo         longer accessible between detection and verification.
    echo         Nothing has been written.
    set "EXIT_CODE=1"
    set "FINAL_MESSAGE=Refused to deploy: could not verify %USB_DRIVE%."
    goto :finish
)
echo.

set "USB_DEST=%USB_DRIVE%\%APP_NAME%"
set "USB_SERVER_DEST=%USB_DRIVE%\%SERVER_APP_NAME%"
echo [OK] USB found at %USB_DRIVE%
echo.

REM -- Ensure destination layout exists -------------------------
echo [6/7] Preparing USB folders...
if not exist "%USB_DEST%" mkdir "%USB_DEST%"
if not exist "%USB_DEST%\data" mkdir "%USB_DEST%\data"
if not exist "%USB_DEST%\data\models" mkdir "%USB_DEST%\data\models"
if not exist "%USB_DEST%\data\recordings" mkdir "%USB_DEST%\data\recordings"
if not exist "%USB_DEST%\data\transcripts" mkdir "%USB_DEST%\data\transcripts"
if not exist "%USB_DEST%\data\reports" mkdir "%USB_DEST%\data\reports"
if not exist "%USB_DEST%\exports" mkdir "%USB_DEST%\exports"
if not exist "%USB_SERVER_DEST%" mkdir "%USB_SERVER_DEST%"
echo [OK] USB folder layout ready.
echo.

REM -- Back up the USB's live saved data before anything below can
REM    touch the drive at all --------------------------------------
REM data\settings.json and data\matches.db are the user's real saved
REM settings and match history -- nothing in this script is supposed to
REM ever write to them (the client robocopy below excludes data\ with
REM /XD, and sync_deploy_assets.ps1 no longer touches them either, after
REM a bug there force-copied this checkout's own local data over the
REM USB's real data and wiped it). This backup is the safety net behind
REM that fix: if any future change ever reintroduces something that
REM touches these files, there is an actual undo path instead of a
REM silent, permanent loss.
set "USB_DATA_BACKUP_ROOT=%USB_DEST%\data\_deploy_backups"
for /f "usebackq delims=" %%I in (`powershell -NoProfile -Command "Get-Date -Format 'yyyyMMdd_HHmmss'"`) do set "USB_BACKUP_STAMP=%%I"
set "USB_DATA_BACKUP=%USB_DATA_BACKUP_ROOT%\%USB_BACKUP_STAMP%"
if exist "%USB_DEST%\data\settings.json" (
    if not exist "%USB_DATA_BACKUP%" mkdir "%USB_DATA_BACKUP%"
    copy /Y "%USB_DEST%\data\settings.json" "%USB_DATA_BACKUP%\settings.json" >nul 2>nul
)
if exist "%USB_DEST%\data\matches.db" (
    if not exist "%USB_DATA_BACKUP%" mkdir "%USB_DATA_BACKUP%"
    copy /Y "%USB_DEST%\data\matches.db" "%USB_DATA_BACKUP%\matches.db" >nul 2>nul
)
if exist "%USB_DATA_BACKUP%" echo [OK] Backed up existing settings/matches to %USB_DATA_BACKUP%
REM Keep only the 5 most recent backups so this doesn't grow forever.
if exist "%USB_DATA_BACKUP_ROOT%" (
    for /f "skip=5 delims=" %%B in ('dir /b /ad /o-d "%USB_DATA_BACKUP_ROOT%" 2^>nul') do (
        rmdir /S /Q "%USB_DATA_BACKUP_ROOT%\%%B" 2>nul
    )
)
echo.

REM -- Sync build + runtime data --------------------------------
echo [7/7] Syncing build and runtime assets...
echo       Preserving USB data and exports folders.
echo       The client and server are two independent deploys to two
echo       independent USB folders -- a failure copying one must not
echo       skip the other, so each robocopy below stands on its own.
echo.

set "CLIENT_SYNC_FAILED=0"
robocopy "%DIST_DIR%" "%USB_DEST%" /E /PURGE /XO /R:3 /W:5 ^
    /XD data exports
if errorlevel 8 (
    echo [ERROR] Robocopy encountered a serious error while syncing the app -- code !errorlevel!.
    echo         Common causes: a locked file -- R6Analyzer.exe still running --
    echo         the USB drive running out of free space, or a permissions
    echo         issue on the drive. Full detail is in the log above/below.
    set "EXIT_CODE=1"
    set "DEPLOY_WARNINGS=1"
    set "CLIENT_SYNC_FAILED=1"
    set "FINAL_MESSAGE=Robocopy failed while syncing the app to the USB."
) else (
    powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0build_scripts\sync_deploy_assets.ps1" -UsbDest "%USB_DEST%" -ModelSrc "%MODEL_SRC%"
    set "ASSET_SYNC_EXIT=%ERRORLEVEL%"
    if not "%ASSET_SYNC_EXIT%"=="0" set "DEPLOY_WARNINGS=1"
)

REM -- Copy the server build onto the USB too --------------------
REM The server doesn't run from the USB in normal use -- it runs on
REM whichever machine (this one, your laptop) you designate as the host.
REM Putting a copy on the USB just means that machine is wherever you plug
REM the stick in next: copy %USB_SERVER_DEST% off onto that machine and run
REM R6Server.exe there. server_data\ (its database and config, including
REM the self-provisioned API token) is preserved here between builds the
REM same way the client's data\ and exports\ folders are above.
REM NOTE: this always runs, even if the client sync above failed -- it is
REM an independent deploy to an independent USB folder and must not be
REM skipped just because the client copy hit a problem.
robocopy "%SERVER_DIST_DIR%" "%USB_SERVER_DEST%" /E /PURGE /XO /R:3 /W:5 ^
    /XD server_data
if errorlevel 8 (
    echo [WARN] Robocopy encountered an error while syncing the server build to the USB -- code !errorlevel!.
    echo        The server exe is still available locally at %SERVER_DIST_DIR%\%SERVER_APP_NAME%.exe
    set "DEPLOY_WARNINGS=1"
) else (
    echo [OK] Server build synced to %USB_SERVER_DEST%
)

REM -- Teammates' recorder onto the USB, ready to hand out ----------
if exist "dist\R6Voice.exe" (
    if not exist "%USB_DRIVE%\R6Voice" mkdir "%USB_DRIVE%\R6Voice"
    copy /Y "dist\R6Voice.exe" "%USB_DRIVE%\R6Voice\R6Voice.exe" >nul
    copy /Y "voice_recorder\FOR_TEAMMATES.txt" "%USB_DRIVE%\R6Voice\FOR_TEAMMATES.txt" >nul
    echo [OK] Teammate recorder copied to %USB_DRIVE%\R6Voice
)

REM -- Teammates' companion sticks (label R6_COMPANION), if plugged in ----
if exist "dist\R6Companion.exe" (
    for /f "tokens=1" %%d in ('powershell -NoProfile -Command "Get-Volume -FileSystemLabel 'R6_COMPANION' -ErrorAction SilentlyContinue | Select-Object -ExpandProperty DriveLetter"') do (
        powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\make_companion_usb.ps1" -Drive %%d:
        if errorlevel 1 (
            echo [WARN] Could not update the companion stick on %%d:
            set "DEPLOY_WARNINGS=1"
        )
    )
)

REM -- Verify the live user data actually survived the sync ---------
REM This should be structurally impossible now (see the backup step and
REM the sync_deploy_assets.ps1 comment above) but this check stays as a
REM hard backstop: if settings.json or matches.db is missing after the
REM sync steps ran, restore it immediately from the backup just taken,
REM rather than ever shipping a "successful" deploy that quietly erased
REM the user's saved data.
if exist "%USB_DATA_BACKUP%\settings.json" if not exist "%USB_DEST%\data\settings.json" (
    echo [WARN] data\settings.json is missing after sync -- restoring from backup.
    copy /Y "%USB_DATA_BACKUP%\settings.json" "%USB_DEST%\data\settings.json" >nul
    set "DEPLOY_WARNINGS=1"
)
if exist "%USB_DATA_BACKUP%\matches.db" if not exist "%USB_DEST%\data\matches.db" (
    echo [WARN] data\matches.db is missing after sync -- restoring from backup.
    copy /Y "%USB_DATA_BACKUP%\matches.db" "%USB_DEST%\data\matches.db" >nul
    set "DEPLOY_WARNINGS=1"
)

if "%CLIENT_SYNC_FAILED%"=="1" (
    echo.
    echo [ERROR] Client sync failed above. The independent server sync
    echo         still ran ^(see result above^), but the client deploy
    echo         needs to be re-run after resolving the error.
    goto :finish
)

echo.
echo ============================================================
echo  DEPLOY COMPLETE
echo  Client exe (USB)   : %USB_DEST%\R6Analyzer.exe
echo  Server exe (USB)   : %USB_SERVER_DEST%\R6Server.exe
echo  Server exe (local) : %SERVER_DIST_DIR%\R6Server.exe
echo  USB drive          : %USB_DRIVE%\
echo.
echo  To run the server: copy the R6Server folder (from the USB or from
echo  dist\R6Server) to whichever machine will host it, and double-click
echo  R6Server.exe there. It self-configures on first run and prints an
echo  API token to paste into the client's Settings -> Remote Sync tab
echo  (only needed if this build had no server configured to embed).
echo.
echo  Reachable from any network (not just this LAN/tailnet): run
echo  setup_tailscale_funnel.bat once on the server machine, then rebuild
echo  so the next R6Analyzer.exe has that address baked in automatically.

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
echo    %USB_SERVER_DEST%\R6Server.exe
echo    %SERVER_DIST_DIR%\server_data\models\whisper-base.pt  (local build, not copied to USB)
echo.
if "%DEPLOY_WARNINGS%"=="1" (
    set "FINAL_MESSAGE=Deploy completed with warnings. Review them before relying on the USB build."
) else (
    set "FINAL_MESSAGE=Deploy completed successfully. The USB build should be ready."
)
goto :finish

REM ============================================================
REM  SUBROUTINES
REM ============================================================

REM :require_file and :ensure_dir used to live here as shared
REM subroutines. Both are gone -- every call site was inlined instead,
REM after `call :require_file` (defined here, called from hundreds of
REM lines away in :main) was caught failing intermittently with "The
REM system cannot find the batch label specified", a known-flaky
REM cmd.exe behavior with GOTO/CALL in large batch files.
REM
REM :locate_ffmpeg and :env_setup (which itself called :resolve_python and
REM :check_exists) used to live here too, for the same "shared subroutine
REM called from far away in :main" reason -- and so were exposed to that
REM exact same flaky-label risk as this file kept growing. Rather than
REM just hoping the distance never gets bad enough to trip it again, all
REM four have been moved out entirely, into their own files under
REM build_scripts\ (env_setup.bat, locate_ffmpeg.bat), called from :main
REM with `call "%~dp0build_scripts\<name>.bat"` -- a plain file call, not
REM a same-file label, so this failure mode cannot happen to them at all
REM anymore. This also keeps this file itself shorter, and keeps every
REM OTHER helper script this build uses (the three .ps1 scripts, plus
REM those two .bat files) out of the project's root directory and
REM together in one place, so build_and_deploy.bat is the only script you
REM ever need to see there.
REM
REM None of this touched :main's own step-by-step flow above, or any of
REM its `goto :finish` error-handling jumps -- those all stay exactly
REM where they were, in this file, since `goto` can only target a label
REM in the SAME file and cannot reach into or out of a called .bat file.

:finish
REM Restart the server only if this build is what stopped it, so a build
REM run while the server was already down leaves it down.
if defined SERVER_WAS_RUNNING (
    if exist "%SERVER_DIST_DIR%\%SERVER_APP_NAME%.exe" (
        echo.
        echo Restarting %SERVER_APP_NAME%.exe ^(it was running before this build^)...
        start "" /D "%SERVER_DIST_DIR%" "%SERVER_DIST_DIR%\%SERVER_APP_NAME%.exe"
    )
)

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
if not defined R6_SKIP_PAUSE pause
exit /b %EXIT_CODE%
