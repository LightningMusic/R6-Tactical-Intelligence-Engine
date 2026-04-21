@echo off
setlocal

echo ============================================
echo  R6 Tactical Intelligence Engine - Builder
echo ============================================
echo.

if exist ".venv\Scripts\activate.bat" (
    call .venv\Scripts\activate.bat
) else (
    echo [ERROR] No .venv found. Run setup.bat first.
    echo.
    pause
    exit /b 1
)

pip show pyinstaller >nul 2>&1
if errorlevel 1 (
    echo [INFO] Installing PyInstaller...
    pip install pyinstaller
)

echo [INFO] Building exe...
echo.
pyinstaller R6Analyzer.spec --noconfirm 2>&1

if errorlevel 1 (
    echo.
    echo ============================================
    echo  [ERROR] Build FAILED. See output above.
    echo ============================================
    echo.
    pause
    exit /b 1
)

echo.
echo ============================================
echo  [OK] Build complete.
echo  Output: dist\R6Analyzer\R6Analyzer.exe
echo ============================================
echo.
pause