"""
R6Voice (voice_recorder/), without a microphone or a network: audio blocks
are fed straight into the recorder's writer, and requests.post is faked.
"""
import json
import sys
import threading
import time
from pathlib import Path

import numpy as np
import pytest

pytest.importorskip("sounddevice")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "voice_recorder"))
import recorder as rec_mod  # noqa: E402
import uploader as up_mod   # noqa: E402

import soundfile as sf  # noqa: E402

SR = rec_mod.SAMPLE_RATE


def run_writer(tmp_path, seconds, chunk_sec, t0=1790700000.0):
    chunks = []
    r = rec_mod.Recorder(out_dir=tmp_path, on_chunk=chunks.append)
    r.recording_id = "f" * 32
    rec_mod.CHUNK_SEC = chunk_sec
    t = threading.Thread(target=r._write_loop)
    t.start()
    block = rec_mod.BLOCK
    for i in range(int(seconds * SR / block)):
        r._q.put((np.full(block, 0.01 * (i % 7), dtype=np.float32), t0 + i * block / SR))
    r._q.put((np.zeros(0, dtype=np.float32), -1.0))
    t.join(timeout=20)
    return chunks


def test_recording_is_cut_into_closed_timestamped_chunks(tmp_path, monkeypatch):
    monkeypatch.setattr(rec_mod, "CHUNK_SEC", 300)
    chunks = run_writer(tmp_path, seconds=25, chunk_sec=10)
    assert [c.index for c in chunks] == [0, 1, 2]
    assert [round(c.duration_sec) for c in chunks] == [10, 10, 5]
    assert [c.is_final for c in chunks] == [False, False, True]
    # Each chunk's timestamp is its own first sample's -- they tile exactly.
    assert chunks[1].start_epoch - chunks[0].start_epoch == pytest.approx(10.0, abs=0.001)
    data, sr = sf.read(chunks[0].path)
    assert sr == SR and abs(len(data) / SR - 10.0) < 0.1       # a complete, readable Opus file


def test_pause_keeps_time_but_records_silence(tmp_path):
    r = rec_mod.Recorder(out_dir=tmp_path, on_chunk=lambda c: None)
    r.paused = True
    r._stream = None
    r._clock_offset = 0.0

    class TI:
        inputBufferAdcTime = 123.0
    r._callback(np.full((rec_mod.BLOCK, 1), 0.5, dtype=np.float32), rec_mod.BLOCK, TI(), None)
    block, epoch = r._q.get_nowait()
    assert not block.any() and epoch == 123.0


class FakeResponse:
    def __init__(self, code, body=None):
        self.status_code, self._body, self.text = code, body or {}, json.dumps(body or {})

    def json(self):
        return self._body

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(self.status_code)


def make_chunk(tmp_path, index, final=False):
    path = tmp_path / f"{'e' * 32}_{index:04d}.ogg"
    sf.write(path, np.zeros(SR, dtype=np.float32), SR, format="OGG", subtype="OPUS")
    c = rec_mod.Chunk("e" * 32, index, path, start_epoch=1790700000.0 + index * 300, frames=SR, is_final=final)
    return c


def wait_for(cond, timeout=10):
    end = time.time() + timeout
    while time.time() < end:
        if cond():
            return True
        time.sleep(0.05)
    return False


def test_uploads_then_deletes_and_retries_when_offline(tmp_path, monkeypatch):
    sent, online = [], {"up": False}

    def fake_post(url, headers, files, data, timeout):
        if not online["up"]:
            raise ConnectionError("offline")
        sent.append((data["chunk_index"], data["username"], data["is_final"], headers["Authorization"]))
        return FakeResponse(200, {"status": "stored"})

    monkeypatch.setattr(up_mod.requests, "post", fake_post)
    up = up_mod.Uploader(tmp_path, lambda: {"server_url": "https://srv", "voice_token": "vk"})
    up.enqueue(make_chunk(tmp_path, 0), "lammtozzz")
    assert wait_for(lambda: up.connected is False)
    assert up.waiting() == 1                                     # kept while offline
    online["up"] = True
    up._wake.set()
    assert wait_for(lambda: up.waiting() == 0, timeout=15)
    assert sent == [("0", "lammtozzz", "False", "Bearer vk")]
    assert not list(tmp_path.glob("*.ogg"))                      # deleted only after the server had it
    up.stop()


def test_a_chunk_the_server_refuses_is_set_aside_not_retried_forever(tmp_path, monkeypatch):
    monkeypatch.setattr(up_mod.requests, "post",
                        lambda *a, **k: FakeResponse(400, {"detail": "Username must be your in-game name"}))
    up = up_mod.Uploader(tmp_path, lambda: {"server_url": "https://srv", "voice_token": "vk"})
    up.enqueue(make_chunk(tmp_path, 3, final=True), "bad name")
    assert wait_for(lambda: up.waiting() == 0)
    assert len(list((tmp_path / "rejected").glob("*.ogg"))) == 1
    assert up.last_error.startswith("Server rejected")
    up.stop()


def test_ping_reports_clock_offset(tmp_path, monkeypatch):
    monkeypatch.setattr(up_mod.requests, "get",
                        lambda *a, **k: FakeResponse(200, {"server_time": time.time() + 95.0}))
    up = up_mod.Uploader(tmp_path, lambda: {"server_url": "https://srv", "voice_token": "vk"})
    assert up.ping() is True
    assert up.clock_offset == pytest.approx(95.0, abs=1.0)
    up.stop()
