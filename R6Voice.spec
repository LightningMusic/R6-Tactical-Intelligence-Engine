# -*- mode: python ; coding: utf-8 -*-
# R6Voice.exe -- the teammates' mic recorder (voice_recorder/). One small
# self-contained exe to hand out: tkinter UI, PortAudio (sounddevice) and
# libsndfile (soundfile, for Opus) plus requests. The build venv also holds
# torch/whisper/PySide6 for the main app; none of that is needed here, so it
# is excluded explicitly to keep the download small.

a = Analysis(
    ['voice_recorder/r6voice.py'],
    pathex=['voice_recorder'],
    binaries=[],
    datas=[],
    hiddenimports=['credentials', 'recorder', 'uploader'],
    hookspath=[],
    runtime_hooks=[],
    excludes=[
        'torch', 'torchaudio', 'whisper', 'PySide6', 'shiboken6', 'scipy', 'numba', 'llvmlite',
        'sklearn', 'resemblyzer', 'librosa', 'matplotlib', 'pandas', 'IPython', 'fastapi',
        'uvicorn', 'llama_cpp', 'tiktoken', 'discord', 'obswebsocket', 'watchdog',
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
    name='R6Voice',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    runtime_tmpdir=None,
    console=False,
)
