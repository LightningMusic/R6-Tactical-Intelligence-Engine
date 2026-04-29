@echo off
setlocal EnableExtensions

if /i not "%~1"=="__inner__" (
    start "R6 Analyzer USB Builder" cmd /k call "%~f0" __inner__
    exit /b 0
)

cd /d "%~dp0"
set "LOG_DIR=%CD%\logs"
set "LOG_FILE="
set "R6_SKIP_PAUSE=1"

if not exist "%LOG_DIR%" mkdir "%LOG_DIR%"
for /f "usebackq delims=" %%I in (`powershell -NoProfile -Command "Get-Date -Format 'yyyyMMdd_HHmmss'"`) do set "LOG_STAMP=%%I"
set "LOG_FILE=%LOG_DIR%\make_usb_%LOG_STAMP%.log"

call :log_header

echo ============================================================
echo  R6 Analyzer One-Click USB Builder
echo ============================================================
echo.
echo  This will:
echo   1. Set up or refresh the Python environment
echo   2. Validate dependencies and local runtime files
echo   3. Build the executable
echo   4. Deploy the finished app to the USB labeled R6_PROJ
echo.
echo  Combined log: %LOG_FILE%
echo.

echo [1/2] Running setup.bat...
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0run_and_stream_to_log.ps1" -ScriptPath "%~dp0setup.bat" -LogFile "%LOG_FILE%" -StepName "SETUP"
if errorlevel 1 (
    echo.
    echo [ERROR] setup.bat failed. USB build was not started.
    >> "%LOG_FILE%" echo [ERROR] setup.bat failed. USB build was not started.
    goto :finish_fail
)

echo.
echo [2/2] Running build_and_deploy.bat...
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0run_and_stream_to_log.ps1" -ScriptPath "%~dp0build_and_deploy.bat" -LogFile "%LOG_FILE%" -StepName "BUILD"
if errorlevel 1 (
    echo.
    echo [ERROR] build_and_deploy.bat failed.
    >> "%LOG_FILE%" echo [ERROR] build_and_deploy.bat failed.
    goto :finish_fail
)

echo.
echo ============================================================
echo  ONE-CLICK USB BUILD COMPLETE
echo ============================================================
echo  The environment was prepared and the USB deploy completed.
echo  Log: %LOG_FILE%
echo.
>> "%LOG_FILE%" echo [SUCCESS] One-click USB build completed.
pause
exit /b 0

:finish_fail
echo.
echo ============================================================
echo  ONE-CLICK USB BUILD FAILED
echo ============================================================
echo  Review the error output above.
echo  Log: %LOG_FILE%
echo.
>> "%LOG_FILE%" echo [FAILURE] One-click USB build failed.
pause
exit /b 1

:log_header
(
    echo ============================================================
    echo  R6 Analyzer One-Click USB Builder Log
    echo ============================================================
    echo  Started: %DATE% %TIME%
    echo  Working Directory: %CD%
    echo.
) > "%LOG_FILE%"
exit /b 0
