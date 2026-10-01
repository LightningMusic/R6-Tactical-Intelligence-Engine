@echo off
setlocal EnableExtensions EnableDelayedExpansion
cd /d "%~dp0"

REM -- Self-wrap with logging on a remote (SSH-triggered) run -----------
REM Added 2026-09-08. Before this, the only record of a remote run was
REM whatever scrolled past in the LAPTOP's own SSH client window -- and
REM when the SSH session itself drops mid-run (exactly the "client_loop:
REM send disconnect: Connection reset" failure this project hit twice
REM now), that scrollback is incomplete, and there was no way to see the
REM actual last thing that happened before the drop. build_and_deploy.bat
REM already has its own version of this same self-wrap-with-logging trick
REM for a local double-click run, but this script sets R6_SKIP_PAUSE
REM before calling it specifically to skip that (the interactive `pause`
REM at build_and_deploy.bat's own end would hang forever over a
REM non-interactive SSH session) -- which meant a remote-triggered run
REM never got a log file at all, from either script. This wraps
REM remote_deploy.bat itself the same way instead, so a persistent,
REM complete transcript is saved to logs\ on THIS PC every time, whether
REM or not the SSH session survives long enough to show it to you live.
if defined R6_REMOTE_LOGGED goto :main

if not exist "logs" mkdir "logs"
for /f "usebackq delims=" %%I in (`powershell -NoProfile -Command "Get-Date -Format 'yyyyMMdd_HHmmss'"`) do set "RD_STAMP=%%I"
set "RD_LOG=%CD%\logs\remote_deploy_%RD_STAMP%.log"
set "R6_REMOTE_LOGGED=1"
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0build_scripts\run_and_stream_to_log.ps1" -ScriptPath "%~f0" -LogFile "%RD_LOG%" -StepName "REMOTE"
set "RD_EXIT=%ERRORLEVEL%"

REM Same lesson as build_and_deploy.bat's own version of this check (see
REM claude/build-deploy-false-success-reporting-fix.md): a hard crash --
REM including the SSH connection itself getting cut -- skips straight
REM past this script's own "Final Status" lines below, which can leave a
REM relayed exit code claiming success when nothing of the sort happened.
REM Trust the log's own SUCCESS marker over the number handed back.
findstr /C:"Final Status: SUCCESS" "%RD_LOG%" >nul 2>&1
if errorlevel 1 set "RD_EXIT=1"

exit /b %RD_EXIT%

:main
REM ============================================================
REM  R6 Tactical Intelligence Engine -- REMOTE deploy over SSH
REM ============================================================
REM  NOT the one you run by hand. This is the target of the SSH command
REM  your laptop runs (see rebuild_via_ssh.bat, delivered separately for
REM  the laptop) when you're away from the main PC and just want to
REM  push a small fix to the USB stick that's plugged into your laptop.
REM
REM  build_and_deploy.bat itself is completely unchanged and untouched
REM  by this -- this script just calls it with R6_SKIP_USB_DEPLOY set
REM  (so it builds and stages normally, then stops before trying to find
REM  a local USB drive that, in this scenario, was never going to be
REM  here anyway -- the USB is in your laptop, not this machine) and
REM  then pushes the finished build to your laptop itself, over the
REM  network, using the SMB share you set up there.
REM
REM  When you're at your main PC with the USB plugged in directly, keep
REM  running build_and_deploy.bat exactly like always -- this file has
REM  no effect on that at all.
REM ============================================================

REM -- Config: fill these in once during setup ---------------------
REM LAPTOP_HOST: your laptop's Tailscale MagicDNS name (from `tailscale
REM status` run on either machine -- looks like "lx15pro" or
REM "lx15pro.tailXXXXX.ts.net") or its Tailscale IP (100.x.y.z). Either
REM works; the MagicDNS name is nicer since it won't change.
set "LAPTOP_HOST=lx15pro"

REM SHARE_NAME: the name you gave the shared folder on your laptop when
REM you set up the SMB share (see the setup instructions). This should
REM be a folder that IS the USB stick's R6_PROJ root, or contains it.
set "SHARE_NAME=r6_proj"

REM LAPTOP_SMB_USER / LAPTOP_SMB_PASS: the laptop account (and its actual
REM password) that has Read/Write access to the share above. This has to
REM be the real username and password, not something cached with cmdkey --
REM cmdkey's stored credentials CANNOT be used here. SSH public-key logins
REM create a Windows "Network" logon session that has no password of its
REM own, so it can never unlock Credential Manager's saved secrets (they're
REM encrypted with a key derived from your password) -- this is a known,
REM documented Windows OpenSSH limitation, not something a setting fixes.
REM Passing the real username/password explicitly, right here, is the
REM actual working fix. Avoid " ^ % & | < > in the password if you can --
REM if your laptop login password has one of those, it's simplest to
REM create a small dedicated local account on the laptop (e.g. "r6sync")
REM with its own simple password, share the folder with that account
REM too, and use its credentials here instead of your everyday login.
set "LAPTOP_SMB_USER=CHANGE_ME_laptop-account"
set "LAPTOP_SMB_PASS=CHANGE_ME_laptop-password"

REM 2026-09-10: auto-load the two values above from laptop_smb_credentials.json
REM instead of hand-typing them, if that file is sitting next to this script.
REM That file is produced ON THE LAPTOP by setup_laptop_smb_account.ps1 (new,
REM delivered alongside rebuild_via_ssh.bat), which creates a small dedicated
REM local account there, generates its password itself (never typed by
REM anyone, on either machine), and writes both into the JSON file -- you
REM just copy that ONE file over to this project root (same USB stick you
REM already carry between machines, or however you like) and this script
REM picks it up automatically from then on. Nothing here changes if you
REM never do that -- the CHANGE_ME_ values above still work exactly as
REM before, hand-edited, if you'd rather not create a dedicated account.
set "LAPTOP_CREDS_FILE=%~dp0laptop_smb_credentials.json"
if exist "%LAPTOP_CREDS_FILE%" (
    for /f "usebackq delims=" %%U in (`powershell -NoProfile -Command "(Get-Content -Raw '%LAPTOP_CREDS_FILE%' | ConvertFrom-Json).laptop_smb_user"`) do set "LAPTOP_SMB_USER=%%U"
    for /f "usebackq delims=" %%P in (`powershell -NoProfile -Command "(Get-Content -Raw '%LAPTOP_CREDS_FILE%' | ConvertFrom-Json).laptop_smb_pass"`) do set "LAPTOP_SMB_PASS=%%P"
    echo [OK] Loaded laptop SMB credentials from laptop_smb_credentials.json ^(account: !LAPTOP_SMB_USER!^).
) else (
    echo [INFO] No laptop_smb_credentials.json found next to this script --
    echo        falling back to the LAPTOP_SMB_USER/LAPTOP_SMB_PASS values
    echo        set above. Run setup_laptop_smb_account.ps1 on your laptop
    echo        once and copy the laptop_smb_credentials.json it creates
    echo        here instead, so nothing has to be typed by hand on either
    echo        machine.
)

set "REMOTE_ROOT=\\%LAPTOP_HOST%\%SHARE_NAME%"
set "REMOTE_CLIENT_DEST=%REMOTE_ROOT%\R6Analyzer"
set "REMOTE_SERVER_DEST=%REMOTE_ROOT%\R6Server"

echo ============================================================
echo  R6 Tactical Intelligence Engine -- Remote Deploy
echo ============================================================
echo  Target: %REMOTE_ROOT%
echo ============================================================
echo.

if "%LAPTOP_HOST%"=="CHANGE_ME_laptop-tailscale-name" (
    echo [ERROR] remote_deploy.bat has not been configured yet.
    echo         Edit LAPTOP_HOST near the top of this file to your
    echo         laptop's actual Tailscale name first.
    exit /b 1
)

if "!LAPTOP_SMB_USER!"=="CHANGE_ME_laptop-account" (
    echo [ERROR] No laptop SMB credentials configured yet. Either:
    echo           1^) Run setup_laptop_smb_account.ps1 on your laptop once
    echo              and copy laptop_smb_credentials.json here ^(recommended
    echo              -- see the comment above LAPTOP_SMB_USER^), or
    echo           2^) Edit LAPTOP_SMB_USER / LAPTOP_SMB_PASS near the top of
    echo              this file by hand.
    exit /b 1
)

echo [1/2] Building and staging locally ^(USB step skipped on purpose^)...
set "R6_SKIP_PAUSE=1"
set "R6_SKIP_USB_DEPLOY=1"
call build_and_deploy.bat
set "BUILD_EXIT=%ERRORLEVEL%"

if not "%BUILD_EXIT%"=="0" (
    echo.
    echo [ERROR] build_and_deploy.bat failed with exit code %BUILD_EXIT%.
    echo         Not pushing anything to your laptop -- see the build
    echo         output above for what went wrong.
    exit /b %BUILD_EXIT%
)

echo.
echo [2/2] Pushing the build to your laptop over the network...

REM Clear any stale connection from a previous failed run first -- a
REM leftover connection with different (or no) credentials makes the next
REM authenticated attempt fail with "multiple connections" instead of
REM giving a useful error.
net use "%REMOTE_ROOT%" /delete /y >nul 2>&1
net use "%REMOTE_ROOT%" /user:"%LAPTOP_SMB_USER%" "%LAPTOP_SMB_PASS%" >nul 2>&1
if errorlevel 1 (
    echo [ERROR] Could not authenticate to %REMOTE_ROOT%.
    echo         Things to check, in order:
    echo           1. Is your laptop actually online right now? Run
    echo              "tailscale status" on THIS PC and look for lx15pro --
    echo              if it says "offline", run "tailscale ping lx15pro"
    echo              from this PC to force a fresh connection attempt.
    echo           2. Is the SMB share still shared on the laptop
    echo              ^(r6_proj^)?
    echo           3. Do LAPTOP_SMB_USER / LAPTOP_SMB_PASS near the top of
    echo              this file exactly match a real account with access
    echo              to that share? Saved/cached credentials ^(cmdkey,
    echo              Credential Manager^) do NOT work for this -- this
    echo              script needs the actual username and password typed
    echo              directly into those two variables.
    exit /b 1
)

if not exist "%REMOTE_ROOT%" (
    echo [ERROR] Authenticated but still could not reach %REMOTE_ROOT%.
    echo         Double-check the share name still matches SHARE_NAME
    echo         above and that the folder is still shared on the laptop.
    net use "%REMOTE_ROOT%" /delete /y >nul 2>&1
    exit /b 1
)

set "PUSH_FAILED=0"

robocopy "dist\R6Analyzer" "%REMOTE_CLIENT_DEST%" /E /PURGE /XO /R:3 /W:5 ^
    /XD data exports
if errorlevel 8 (
    echo [ERROR] Robocopy failed pushing the client build -- code !errorlevel!.
    set "PUSH_FAILED=1"
)

robocopy "dist\R6Server" "%REMOTE_SERVER_DEST%" /E /PURGE /XO /R:3 /W:5 ^
    /XD server_data
if errorlevel 8 (
    echo [ERROR] Robocopy failed pushing the server build -- code !errorlevel!.
    set "PUSH_FAILED=1"
)

REM Drop the authenticated connection now that the push is done, so it
REM doesn't linger and doesn't collide with the next run's fresh net use.
net use "%REMOTE_ROOT%" /delete /y >nul 2>&1

echo.
if "%PUSH_FAILED%"=="1" (
    echo ============================================================
    echo  Final Status: FAILURE
    echo  Build succeeded locally but the push to your laptop failed.
    echo  dist\R6Analyzer and dist\R6Server are intact here if you need
    echo  to grab them another way.
    echo ============================================================
    exit /b 1
) else (
    echo ============================================================
    echo  Final Status: SUCCESS
    echo  Pushed to %REMOTE_ROOT%
    echo  Your laptop's USB should now have the latest build.
    echo ============================================================
    exit /b 0
)
