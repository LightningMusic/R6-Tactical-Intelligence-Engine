@echo off
REM Turns the always-on server off (stays off, even through the watchdog) or back on.
REM   toggle_server.bat          flip it
REM   toggle_server.bat off|on   set it explicitly
setlocal
set "SERVER_DIR=%~dp0dist\R6Server"
set "FLAG=%SERVER_DIR%\server_data\server_stopped.flag"
set "WANT=%~1"
if "%WANT%"=="" (
    if exist "%FLAG%" (set "WANT=on") else (set "WANT=off")
)

if /i "%WANT%"=="off" (
    if not exist "%SERVER_DIR%\server_data" mkdir "%SERVER_DIR%\server_data"
    type nul > "%FLAG%"
    taskkill /IM R6Server.exe /F >nul 2>nul
    echo Server stopped. The watchdog will leave it off until you run: toggle_server.bat on
) else (
    if exist "%FLAG%" del "%FLAG%"
    tasklist /FI "IMAGENAME eq R6Server.exe" | find /I "R6Server.exe" >nul || start "" /D "%SERVER_DIR%" "%SERVER_DIR%\R6Server.exe"
    echo Server is on. The watchdog will restart it automatically if it ever stops.
)
endlocal
