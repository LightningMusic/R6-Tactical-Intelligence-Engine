#!/usr/bin/env python3
"""Debrief video -> local transcript. Reusable pipeline for the recorded
post-scrim talks (Elijah's personal memory aid; see the project memory notes).

These are phone-in-pocket recordings made outside / in a car, so speaker
diarization usually can't separate the two voices -- this does the reliable
part: clean audio extraction + a timestamped transcript. For the occasional
clip recorded with the phone held between the two of you, diarization may work:
see tools/diarize_runner.py and tools/README_debrief.md.

Usage (run with a Python that has faster-whisper + numpy -- the system
Python 3.12, not the project venv):

    python tools/debrief.py "D:\\path\\VID_20261112_200931821.mp4"
    python tools/debrief.py "<video>" --out debriefs/nov12 --model small

Outputs (transcript.txt, segments.json, audio.wav) go to --out, default
debriefs/<video-stem>/. That folder is gitignored on purpose: the transcripts
hold personal conversation and must never be committed or shared. Pull only the
game-relevant signal into any shareable report.
"""
from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import time
import wave
from pathlib import Path

import numpy as np


def find_ffmpeg() -> str:
    exe = shutil.which("ffmpeg")
    if exe:
        return exe
    root = Path(__file__).resolve().parent.parent
    for cand in (root / "ffmpeg.exe", root / "dist" / "R6Analyzer" / "ffmpeg.exe"):
        if cand.exists():
            return str(cand)
    sys.exit("ffmpeg not found on PATH or in the project folder.")


def hhmmss(s: float) -> str:
    s = int(round(s))
    return f"{s // 3600:02d}:{(s % 3600) // 60:02d}:{s % 60:02d}"


def extract_audio(ffmpeg: str, video: Path, wav: Path) -> None:
    r = subprocess.run([ffmpeg, "-y", "-v", "error", "-i", str(video),
                        "-vn", "-ac", "1", "-ar", "16000", "-acodec", "pcm_s16le", str(wav)])
    if r.returncode != 0 or not wav.exists():
        sys.exit(f"ffmpeg failed to extract audio (exit {r.returncode}).")


def load_wav(path: Path):
    with wave.open(str(path), "rb") as w:
        sr, ch = w.getframerate(), w.getnchannels()
        raw = w.readframes(w.getnframes())
    a = np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0
    if ch > 1:
        a = a.reshape(-1, ch).mean(axis=1)
    return a, sr


def main() -> None:
    ap = argparse.ArgumentParser(description="Transcribe a debrief video.")
    ap.add_argument("video", help="path to the VID_*.mp4 (or any media file)")
    ap.add_argument("--out", help="output dir (default: debriefs/<video-stem>/)")
    ap.add_argument("--model", default="small", help="faster-whisper model size (default: small)")
    args = ap.parse_args()

    video = Path(args.video)
    if not video.exists():
        sys.exit(f"no such file: {video}")
    out = Path(args.out) if args.out else Path("debriefs") / video.stem
    out.mkdir(parents=True, exist_ok=True)

    ffmpeg = find_ffmpeg()
    wav = out / "audio.wav"
    print(f"[1/3] extracting audio -> {wav}", flush=True)
    extract_audio(ffmpeg, video, wav)

    audio, sr = load_wav(wav)
    dur = len(audio) / sr
    print(f"[2/3] loading faster-whisper '{args.model}' (CPU int8)", flush=True)
    from faster_whisper import WhisperModel          # imported late so --help works without it
    model = WhisperModel(args.model, device="cpu", compute_type="int8")

    print(f"[3/3] transcribing {dur:.0f}s of audio ...", flush=True)
    t0 = time.time()
    segments, _info = model.transcribe(
        audio, language="en", beam_size=5, vad_filter=True,
        vad_parameters=dict(min_silence_duration_ms=500),
        condition_on_previous_text=False,
    )
    txt, jsn = out / "transcript.txt", out / "segments.json"
    seglist = []
    with open(txt, "w", encoding="utf-8") as tf:
        tf.write(f"# Debrief transcript: {video.name}  (audio {dur:.0f}s, {time.strftime('%Y-%m-%d')})\n")
        tf.write("# Personal memory aid -- keep private; pull only game-relevant signal into shared reports.\n\n")
        for seg in segments:
            tf.write(f"[{hhmmss(seg.start)} - {hhmmss(seg.end)}] {seg.text.strip()}\n")
            tf.flush()
            seglist.append({"start": seg.start, "end": seg.end, "text": seg.text.strip()})
    json.dump(seglist, open(jsn, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print(f"done: {len(seglist)} segments in {time.time() - t0:.0f}s", flush=True)
    print(f"  transcript: {txt}", flush=True)
    print(f"  segments:   {jsn}", flush=True)


if __name__ == "__main__":
    main()
