# hook-whisper.py — PyInstaller hook for openai-whisper
from PyInstaller.utils.hooks import collect_data_files, collect_submodules

datas    = collect_data_files("whisper")
hiddenimports = collect_submodules("whisper")