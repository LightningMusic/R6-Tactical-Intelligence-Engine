"""
Turns a finished OBS recording into the 5-minute, 16 kHz mono Opus chunks
the server takes (the same format R6Voice uploads), each stamped with the
wall-clock time of its first sample.

The recording's start time comes from its OBS file name ("2026-09-30
18-01-05.mp4" -- this PC's local time), so no timing has to be kept
anywhere else; the server lines the audio up against the host's Discord
track anyway, which absorbs any clock difference between the two PCs.
"""
from __future__ import annotations

import hashlib
import re
import subprocess
import sys
import time
from pathlib import Path
from typing import Optional

import soundfile as sf

CHUNK_SEC = 300
_OBS_NAME = re.compile(r"(\d{4}-\d{2}-\d{2})[ _](\d{2}-\d{2}-\d{2})")
_NO_WINDOW = 0x08000000 if sys.platform == "win32" else 0


def recording_start_epoch(path: Path) -> Optional[float]:
    m = _OBS_NAME.search(path.stem)
    if not m:
        return None
    return time.mktime(time.strptime(f"{m.group(1)} {m.group(2)}", "%Y-%m-%d %H-%M-%S"))


def recording_id(device_id: str, path: Path) -> str:
    return hashlib.sha1(f"{device_id}|{path.name}".encode()).hexdigest()[:32]


def export_piece(ffmpeg: Path, recording: Path, out_path: Path, start_sec: float, max_sec: float) -> float:
    """
    Cuts [start_sec, start_sec + max_sec) of the recording's audio into one
    16 kHz mono Opus file and returns how long it came out (0.0 if there was
    nothing there yet). Works on the file OBS is still writing -- hybrid MP4
    is written in fragments that can be read back mid-recording -- which is
    what lets a practice's audio go up while it's still going on.
    """
    out_path.parent.mkdir(parents=True, exist_ok=True)
    r = subprocess.run(
        [str(ffmpeg), "-y", "-hide_banner", "-loglevel", "error", "-ss", f"{start_sec:.3f}",
         "-i", str(recording), "-t", f"{max_sec:.3f}", "-map", "0:a:0", "-vn", "-ac", "1", "-ar", "16000",
         "-c:a", "libopus", "-b:a", "24k", str(out_path)],
        capture_output=True, text=True, creationflags=_NO_WINDOW, timeout=600,
    )
    if r.returncode != 0 or not out_path.exists():
        out_path.unlink(missing_ok=True)
        return 0.0
    try:
        return float(sf.info(str(out_path)).duration)
    except Exception:
        out_path.unlink(missing_ok=True)
        return 0.0


def measure_peak(ffmpeg: Path, recording: Path, start_sec: float, seconds: float = 20.0) -> Optional[float]:
    """Loudest sample (0..1) in [start_sec, start_sec + seconds) of a recording's audio, readable while OBS is
    still writing the file (the same read export_piece does for uploads). None means nothing could be read,
    which is "don't know", never "silent": only a real read of real samples can say a mic is dead."""
    import tempfile
    tmp = Path(tempfile.gettempdir()) / f"r6comp_probe_{int(time.time() * 1000)}.ogg"
    try:
        dur = export_piece(ffmpeg, recording, tmp, max(0.0, start_sec), seconds)
        if dur < 2.0 or not tmp.exists():
            return None
        peak = 0.0
        with sf.SoundFile(str(tmp)) as f:
            for block in f.blocks(blocksize=1 << 16, dtype="float32"):
                if len(block):
                    peak = max(peak, float(abs(block).max()))
        return peak
    except Exception:
        return None
    finally:
        tmp.unlink(missing_ok=True)


def export_chunks(ffmpeg: Path, recording: Path, out_dir: Path, rid: str) -> list[tuple[Path, float, float]]:
    """(chunk file, seconds into the recording, duration) for each chunk."""
    out_dir.mkdir(parents=True, exist_ok=True)
    pattern = out_dir / f"{rid}_%04d.ogg"
    r = subprocess.run(
        [str(ffmpeg), "-y", "-hide_banner", "-loglevel", "error", "-i", str(recording),
         "-map", "0:a:0", "-vn", "-ac", "1", "-ar", "16000", "-c:a", "libopus", "-b:a", "24k",
         "-f", "segment", "-segment_time", str(CHUNK_SEC), "-reset_timestamps", "1", str(pattern)],
        capture_output=True, text=True, creationflags=_NO_WINDOW, timeout=1800,
    )
    if r.returncode != 0:
        raise RuntimeError(r.stderr.strip()[-300:] or f"ffmpeg exited {r.returncode}")
    chunks, offset = [], 0.0
    for f in sorted(out_dir.glob(f"{rid}_*.ogg")):
        dur = float(sf.info(str(f)).duration)
        chunks.append((f, offset, dur))
        offset += dur
    return chunks
