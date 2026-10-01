@echo off
setlocal
REM ============================================================
REM  Pauses / resumes the remote server's heavy analysis stage.
REM
REM  PAUSED does NOT stop the server and does NOT reject uploads --
REM  the client can still send sessions at any time and they queue up
REM  normally. It only stops the expensive part (Whisper transcription
REM  and Ollama AI analysis) from starting, which is what saturates the
REM  machine. Flip it back to RESUMED and the server immediately works
REM  through everything that piled up while you were gaming.
REM
REM  Safe to run whether the server is up or down: the flag is a file the
REM  worker re-checks every couple of seconds, so there is no need to
REM  restart the server after toggling.
REM
REM  Targets dist\R6Server by default. For the USB copy instead:
REM      toggle_analysis.bat H:\R6Server
REM ============================================================

if "%~1"=="" (
    set "SERVER_DIR=%~dp0dist\R6Server"
) else (
    set "SERVER_DIR=%~1"
)
set "SERVER_DATA=%SERVER_DIR%\server_data"
set "FLAG=%SERVER_DATA%\analysis_paused.flag"

if not exist "%SERVER_DATA%" mkdir "%SERVER_DATA%" >nul 2>&1
if not exist "%SERVER_DATA%" (
    echo [ERROR] Could not find or create: %SERVER_DATA%
    echo         Pass the server folder as an argument, e.g.
    echo             toggle_analysis.bat H:\R6Server
    echo.
    pause
    exit /b 1
)

echo ============================================================
if exist "%FLAG%" (
    del /F /Q "%FLAG%" >nul 2>&1
    if exist "%FLAG%" (
        echo  [ERROR] Could not remove the pause flag:
        echo          %FLAG%
        echo  Analysis is still PAUSED.
        echo ============================================================
        echo.
        pause
        exit /b 1
    )
    echo   Analysis is now RESUMED.
    echo.
    echo   Any sessions uploaded while paused start processing within
    echo   a couple of seconds. Expect heavy CPU/GPU use while they run.
) else (
    echo. > "%FLAG%"
    if not exist "%FLAG%" (
        echo  [ERROR] Could not write the pause flag:
        echo          %FLAG%
        echo  Analysis is still RUNNING.
        echo ============================================================
        echo.
        pause
        exit /b 1
    )
    echo   Analysis is now PAUSED.
    echo.
    echo   The server keeps running and keeps accepting uploads --
    echo   they just wait in the queue until you resume.
)
echo ============================================================
echo.
echo   Server folder: %SERVER_DIR%
echo.
pause
exit /b 0
