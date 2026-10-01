# -*- mode: python ; coding: utf-8 -*-
# R6Companion.exe -- runs from a teammate's USB stick next to a portable OBS
# (companion/). One self-contained exe: tkinter UI, obs-websocket, psutil,
# requests, soundfile (+numpy) for reading chunk lengths. The heavy parts of
# the build venv (torch, whisper, PySide6) are excluded explicitly.

a = Analysis(
    ['companion/companion_app.py'],
    pathex=['companion', 'voice_recorder'],
    binaries=[],
    datas=[],
    hiddenimports=['credentials', 'core', 'obs_link', 'audio_export', 'uploader'],
    hookspath=[],
    runtime_hooks=[],
    excludes=[
        'torch', 'torchaudio', 'whisper', 'PySide6', 'shiboken6', 'scipy', 'numba', 'llvmlite',
        'sklearn', 'resemblyzer', 'librosa', 'matplotlib', 'pandas', 'IPython', 'fastapi',
        'uvicorn', 'llama_cpp', 'tiktoken', 'discord', 'watchdog', 'sounddevice',
    ],
    noarchive=False,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name='R6Companion',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    runtime_tmpdir=None,
    console=False,
)
