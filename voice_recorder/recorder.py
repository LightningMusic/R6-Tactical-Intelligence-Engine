"""
Records one person's microphone in 5-minute Opus chunks.

Each chunk is a complete, closed file the moment it's done, so a crash or a
pulled plug loses at most the chunk in progress, and chunks can be uploaded
while the recording carries on. Every chunk carries the wall-clock time of
its first sample (from PortAudio's own capture timestamps, not "when the
file was opened"), which is what lets the server line it up with the
host's recording.
"""
from __future__ import annotations

import queue
import threading
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Optional

import numpy as np
import sounddevice as sd
import soundfile as sf

SAMPLE_RATE = 16000          # plenty for speech, and a rate Opus supports natively
CHUNK_SEC = 300
BLOCK = 1600                 # 100 ms callbacks


@dataclass
class Chunk:
    recording_id: str
    index: int
    path: Path
    start_epoch: float
    frames: int = 0
    is_final: bool = False

    @property
    def duration_sec(self) -> float:
        return self.frames / SAMPLE_RATE


def input_devices() -> list[tuple[int, str]]:
    """(index, name) for every microphone, via Windows' MME layer: it lists
    each device once and converts sample rates itself."""
    out = []
    try:
        apis = sd.query_hostapis()
        mme = next((i for i, a in enumerate(apis) if a["name"] == "MME"), None)
        for i, d in enumerate(sd.query_devices()):
            if d["max_input_channels"] > 0 and (mme is None or d["hostapi"] == mme):
                if "Microsoft Sound Mapper" in d["name"]:
                    continue
                out.append((i, d["name"]))
    except Exception:
        pass
    return out


@dataclass
class Recorder:
    out_dir: Path
    on_chunk: Callable[[Chunk], None]
    device: Optional[int] = None
    level: float = 0.0                       # latest RMS level 0..1, for the meter
    recording_id: str = ""
    started_at: float = 0.0
    paused: bool = False
    _stream: Optional[sd.InputStream] = None
    _q: "queue.Queue[tuple[np.ndarray, float]]" = field(default_factory=queue.Queue)
    _writer: Optional[threading.Thread] = None
    _running: bool = False
    _clock_offset: Optional[float] = None    # epoch - PortAudio stream time

    def start(self) -> None:
        self.recording_id = uuid.uuid4().hex
        self.out_dir.mkdir(parents=True, exist_ok=True)
        self.started_at = time.time()
        self._clock_offset = None
        self._running = True
        self._stream = sd.InputStream(samplerate=SAMPLE_RATE, channels=1, dtype="float32",
                                      blocksize=BLOCK, device=self.device, callback=self._callback)
        self._writer = threading.Thread(target=self._write_loop, daemon=True, name="R6VoiceWriter")
        self._writer.start()
        self._stream.start()

    def stop(self) -> None:
        self._running = False
        if self._stream is not None:
            try:
                self._stream.stop()
                self._stream.close()
            finally:
                self._stream = None
        self._q.put((np.zeros(0, dtype=np.float32), -1.0))   # wake the writer so it finishes
        if self._writer is not None:
            self._writer.join(timeout=10)

    def _callback(self, indata, frames, time_info, status) -> None:
        if self._clock_offset is None:
            # Map PortAudio's capture clock onto wall-clock time once; every
            # block's own ADC timestamp then says when its first sample was
            # actually heard.
            self._clock_offset = time.time() - self._stream.time if self._stream else 0.0
        adc = getattr(time_info, "inputBufferAdcTime", 0.0) or (self._stream.time if self._stream else 0.0)
        block = indata[:, 0].copy()
        self.level = float(min(1.0, np.sqrt(np.mean(block * block)) * 6))
        if self.paused:
            block[:] = 0.0            # keep the timeline continuous, record nothing
        self._q.put((block, adc + (self._clock_offset or 0.0)))

    def _write_loop(self) -> None:
        index = 0
        current: Optional[Chunk] = None
        f: Optional[sf.SoundFile] = None

        def close(final: bool) -> None:
            nonlocal f, current
            if f is not None and current is not None:
                f.close()
                current.is_final = final
                if current.frames > 0:
                    self.on_chunk(current)
                else:
                    current.path.unlink(missing_ok=True)
            f, current = None, None

        while True:
            block, epoch = self._q.get()
            if epoch < 0:
                close(final=True)
                return
            if current is None:
                path = self.out_dir / f"{self.recording_id}_{index:04d}.ogg"
                current = Chunk(self.recording_id, index, path, start_epoch=epoch)
                f = sf.SoundFile(path, "w", samplerate=SAMPLE_RATE, channels=1, format="OGG", subtype="OPUS")
                index += 1
            f.write(block)
            current.frames += len(block)
            if current.frames >= CHUNK_SEC * SAMPLE_RATE:
                close(final=False)
