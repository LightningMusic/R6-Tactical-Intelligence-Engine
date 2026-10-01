REM ============================================================
REM  R6 Tactical Intelligence Engine -- locate ffmpeg.exe
REM ============================================================
REM  Called by build_and_deploy.bat (`call "build_scripts\locate_ffmpeg.bat"`)
REM  during staging -- not something you run by hand. Sets FFMPEG_SRC in
REM  the caller's own environment (no setlocal here on purpose, same
REM  reasoning as env_setup.bat in this folder) to the first ffmpeg.exe
REM  found, checking a few likely local spots before falling back to PATH.
REM  Leaves FFMPEG_SRC undefined if nothing is found -- the caller already
REM  handles that case.
REM ============================================================

set "FFMPEG_SRC="
for %%F in ("%CD%\ffmpeg.exe" "%CD%\tools\ffmpeg.exe" "%CD%\integration\bin\ffmpeg.exe") do (
    if exist "%%~fF" if not defined FFMPEG_SRC set "FFMPEG_SRC=%%~fF"
)
if defined FFMPEG_SRC exit /b 0

for /f "delims=" %%F in ('where ffmpeg 2^>nul') do (
    if not defined FFMPEG_SRC set "FFMPEG_SRC=%%~fF"
)
exit /b 0
