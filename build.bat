@echo off
setlocal

echo ============================================
echo  R6 Tactical Intelligence Engine - Builder
echo ============================================
echo.

REM ── Activate virtual environment ─────────────────────────────
if exist ".venv\Scripts\activate.bat" (
    call .venv\Scripts\activate.bat
) else (
    echo [ERROR] No .venv found. Run: python -m venv .venv
    pause
    exit /b 1
)

REM ── Ensure PyInstaller is installed ──────────────────────────
pip show pyinstaller >nul 2>&1
if errorlevel 1 (
    echo [INFO] Installing PyInstaller...
    pip install pyinstaller
)

REM ── Run PyInstaller with the spec file ───────────────────────
echo [INFO] Building exe...
pyinstaller R6Analyzer.spec --noconfirm

if errorlevel 1 (
    echo.
    echo [ERROR] Build failed. Check output above.
    pause
    exit /b 1
)

echo.
echo [OK] Build complete.
echo      Output: dist\R6Analyzer\R6Analyzer.exe
echo.
pause