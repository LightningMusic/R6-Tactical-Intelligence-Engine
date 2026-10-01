@echo off
rem Controls the R6 server stack (Docker inside the isolated WSL distro).
rem   r6ctl help
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0r6ctl.ps1" %*
exit /b %errorlevel%
