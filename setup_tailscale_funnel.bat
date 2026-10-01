@echo off
setlocal EnableExtensions
cd /d "%~dp0"

echo ============================================
echo  R6 Tactical Intelligence Engine
echo  Tailscale Funnel Setup (Milestone 5)
echo ============================================
echo  Run this ONCE on the machine that runs
echo  R6Server.exe (your home PC that stays on),
echo  after installing Tailscale and running
echo  `tailscale up` to log in there.
echo.
echo  This turns on Tailscale Funnel so the server
echo  has a permanent public HTTPS address reachable
echo  from any network with internet access, and
echo  saves that address for build_and_deploy.bat
echo  to bake into future R6Analyzer.exe builds.
echo ============================================
echo.

if exist "%VENV_DIR%\Scripts\activate.bat" (
    call ".venv\Scripts\activate.bat"
)

set "SU_PORT=8000"
if not "%~1"=="" set "SU_PORT=%~1"

python scripts\setup_tailscale_funnel.py %SU_PORT%
set "SU_EXIT=%ERRORLEVEL%"

echo.
if "%SU_EXIT%"=="0" (
    echo  Done. See above for the public address that was saved.
) else (
    echo  Failed — see the error above.
)
echo.
pause
exit /b %SU_EXIT%
