@echo off
setlocal

echo ============================================
echo  R6 Tactical Intelligence Engine - Setup
echo ============================================
echo.

if exist ".venv\Scripts\activate.bat" (
    call .venv\Scripts\activate.bat
) else (
    echo [INFO] Creating virtual environment...
    python -m venv .venv
    call .venv\Scripts\activate.bat
)

echo [1/5] Installing core dependencies...
pip install PySide6 psutil watchdog obs-websocket-py openai-whisper 2>&1

echo.
echo [2/5] Installing llama-cpp-python (CPU-only, no AVX required)...
pip install llama-cpp-python --extra-index-url https://abetlen.github.io/llama-cpp-python/whl/cpu 2>&1

if errorlevel 1 (
    echo [WARN] Pre-built wheel failed. Trying source build without AVX...
    set CMAKE_ARGS=-DLLAMA_AVX=OFF -DLLAMA_AVX2=OFF -DLLAMA_F16C=OFF -DLLAMA_FMA=OFF
    pip install llama-cpp-python --no-binary llama-cpp-python 2>&1
)

echo.
echo [3/5] Installing PyInstaller for builds...
pip install pyinstaller 2>&1

echo.
echo ============================================
echo  Setup complete.
echo  Run: python main.py
echo  Build: build_and_deploy.bat
echo ============================================
echo.
pause