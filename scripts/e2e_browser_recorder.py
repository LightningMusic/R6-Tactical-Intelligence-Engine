"""
End-to-end check of the /join browser recorder in a real browser.

Starts a throwaway server (temp data dir, nothing of yours is touched), opens
/join in Edge/Chrome with a FAKE microphone that plays tone bursts, and checks:
the page follows the host's start/stop, chunks tile exactly on the server's
clock, the stored audio is real, an offline stretch is queued and then
uploaded, and a revoked link is shut out.

Needs playwright (pip install playwright; it drives the Edge/Chrome already
installed, no browser download). Run:  python scripts/e2e_browser_recorder.py
"""
from __future__ import annotations

import os
import socket
import sqlite3
import subprocess
import sys
import tempfile
import time
import wave
from pathlib import Path

import httpx
import numpy as np
import soundfile as sf
from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parent.parent
MAIN, VOICE = "e2e-main-token", "e2e-voice-token"
FAILS: list[str] = []


def check(ok: bool, what: str) -> None:
    print(("  PASS  " if ok else "  FAIL  ") + what, flush=True)
    if not ok:
        FAILS.append(what)


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def make_fake_mic(path: Path, seconds: int = 60, rate: int = 48000) -> None:
    t = np.arange(rate * seconds) / rate
    gate = ((t % 2.0) < 1.0).astype(float)                         # 1 s tone, 1 s silence
    x = (np.sin(2 * np.pi * 440 * t) * gate * 0.5 * 32767).astype("<i2")
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1); w.setsampwidth(2); w.setframerate(rate); w.writeframes(x.tobytes())


def wait_for(fn, timeout, what, step=0.5):
    end = time.time() + timeout
    while time.time() < end:
        try:
            v = fn()
            if v:
                return v
        except Exception:
            pass
        time.sleep(step)
    check(False, f"timed out waiting for: {what}")
    return None


def chunk_rows(db: Path) -> list[sqlite3.Row]:
    c = sqlite3.connect(db)
    c.row_factory = sqlite3.Row
    try:
        return c.execute("SELECT * FROM voice_chunks ORDER BY start_epoch, chunk_index").fetchall()
    finally:
        c.close()


def main() -> int:
    work = Path(tempfile.mkdtemp(prefix="r6_e2e_"))
    data = work / "server_data"
    port = free_port()
    base = f"http://127.0.0.1:{port}"
    mic = work / "fake_mic.wav"
    make_fake_mic(mic)
    env = {**os.environ, "R6_SERVER_DATA_DIR": str(data), "R6_SERVER_API_TOKEN": MAIN, "R6_SERVER_VOICE_TOKEN": VOICE,
           "PYTHONIOENCODING": "utf-8"}
    log = (work / "server.log").open("w")
    server_python = os.environ.get("R6_E2E_SERVER_PYTHON", sys.executable)
    srv = subprocess.Popen([server_python, "-m", "uvicorn", "server.main:app", "--host", "127.0.0.1", "--port", str(port)],
                           cwd=ROOT, env=env, stdout=log, stderr=subprocess.STDOUT)
    H = {"Authorization": f"Bearer {MAIN}"}
    try:
        up = wait_for(lambda: httpx.get(base + "/api/v1/health", timeout=2).status_code == 200, 90, "server up")
        if not up:
            return 1
        invite = httpx.post(base + "/api/v1/invites", headers=H, json={"username": "E2E_Player"}).json()
        control = lambda on: httpx.put(base + "/api/v1/companion/control", headers=H, json={"recording": on}, timeout=5)
        status = lambda: httpx.get(base + "/api/v1/companion/status", headers=H, timeout=5).json()
        db = data / "server_matches.db"

        with sync_playwright() as p:
            browser = None
            for channel in ("msedge", "chrome"):
                try:
                    browser = p.chromium.launch(channel=channel, headless=True, args=[
                        "--use-fake-device-for-media-stream", "--use-fake-ui-for-media-stream",
                        f"--use-file-for-fake-audio-capture={mic}", "--autoplay-policy=no-user-gesture-required"])
                    print(f"browser: {channel} {browser.version}")
                    break
                except Exception as exc:
                    print(f"could not launch {channel}: {str(exc)[:120]}")
            if browser is None:
                print("No Edge/Chrome available.")
                return 2
            ctx = browser.new_context(permissions=["microphone"])
            page = ctx.new_page()
            errors: list[str] = []
            page.on("pageerror", lambda e: errors.append(str(e)))
            page.on("console", lambda m: errors.append(m.text) if m.type == "error" else None)
            state = lambda: page.evaluate("window.__r6rec && window.__r6rec.get()")

            print("\n[1] invite link opens, identifies the player, hides the token")
            page.goto(base + "/join#" + invite["token"])
            s = wait_for(lambda: (st := state()) and st.get("username") and st, 15, "page identified the player")
            check(s and s["username"] == "E2E_Player", "page shows the invite's own username")
            check("inv_" not in page.url, "token removed from the address bar")
            check("E2E_Player" in page.inner_text("#who"), "greeting names the player")
            comp = wait_for(lambda: [c for c in status()["companions"] if c["username"] == "E2E_Player"], 10,
                            "page checked in before Start")
            check(bool(comp) and comp[0]["status"].get("kind") == "browser", "host sees the page as a browser recorder")

            print("\n[2] Start: mic opens, but nothing is recorded until the host starts")
            page.click("#startBtn")
            s = wait_for(lambda: (st := state()) and st.get("started") and st, 15, "recorder started")
            check(bool(s), "recorder started with a (fake) microphone")
            check(s and s["rate"] == 16000, f"audio context runs at 16 kHz (got {s and s['rate']})")
            time.sleep(7)
            check(not state()["recording"] and not chunk_rows(db), "idle while the host isn't recording")

            print("\n[3] host starts: page records ~55 s, then follows the stop")
            t_start = time.time()
            control(True)
            wait_for(lambda: state()["recording"], 12, "page began recording after host start")
            t_rec = time.time()
            check(t_rec - t_start < 9, f"followed the host's start within {t_rec - t_start:.1f}s")
            time.sleep(55)
            t_stop = time.time()
            control(False)
            wait_for(lambda: not state()["recording"], 12, "page stopped after host stop")
            rows = wait_for(lambda: (r := chunk_rows(db)) and r[-1]["is_final"] and r, 30, "final chunk uploaded")
            wait_for(lambda: state()["queued"] == 0, 15, "upload queue drained")
            rows = chunk_rows(db)
            check(len(rows) >= 3, f"20 s chunks arrived ({len(rows)} chunks)")
            check(len({r["recording_id"] for r in rows}) == 1, "one recording id for the whole cycle")
            check([r["chunk_index"] for r in rows] == list(range(len(rows))), "chunk indexes contiguous from 0")
            gaps = [rows[i + 1]["start_epoch"] - (rows[i]["start_epoch"] + rows[i]["duration_sec"]) for i in range(len(rows) - 1)]
            check(all(abs(g) < 0.005 for g in gaps), f"chunks tile exactly (max gap {max(map(abs, gaps), default=0) * 1000:.2f} ms)")
            total = sum(r["duration_sec"] for r in rows)
            check(abs(total - (t_stop - t_rec)) < 9, f"recorded {total:.1f}s for a {t_stop - t_rec:.1f}s session")
            first_off = rows[0]["start_epoch"] - t_rec
            check(-1.0 < first_off < 8, f"first chunk starts {first_off:+.2f}s from when the page began recording")
            check(rows[-1]["is_final"] == 1 and sum(r["is_final"] for r in rows) == 1, "exactly one final chunk, and it is last")
            check(all(r["username"] == "E2E_Player" and r["sample_rate"] == 16000 for r in rows), "username and sample rate correct")
            ok_audio, burst = True, True
            for r in rows:
                f = Path(r["file_path"])
                ok_audio &= f.suffix == ".ogg" and f.exists()
                d, sr = sf.read(f, dtype="float32")
                ok_audio &= sr == 16000 and abs(len(d) / sr - r["duration_sec"]) < 0.1
                rms = np.sqrt(np.convolve(d ** 2, np.ones(1600) / 1600, mode="valid"))   # 0.1 s windows
                burst &= bool(rms.max() > 0.1 and rms.min() < 0.02) if r["duration_sec"] > 4 else True
            check(ok_audio, "stored chunks are Opus files that decode at 16 kHz with the right length")
            check(burst, "stored audio contains the fake mic's tone bursts and silences (real audio, not zeros)")

            print("\n[4] offline: audio is kept in the browser, then uploaded when the network returns")
            before = len(chunk_rows(db))
            control(True)
            wait_for(lambda: state()["recording"], 12, "recording again")
            ctx.set_offline(True)
            time.sleep(28)
            check(state()["queued"] >= 1, f"{state()['queued']} chunk(s) waiting while offline")
            check("Can't reach the server" in page.inner_text("#bannerWarn"), "page tells the player it's offline")
            ctx.set_offline(False)
            control(False)
            wait_for(lambda: state()["queued"] == 0 and not state()["recording"], 40, "queue drained after reconnect")
            after = chunk_rows(db)
            check(len(after) > before + 1, f"offline audio arrived after reconnect ({len(after) - before} new chunks)")
            new = [r for r in after if r["recording_id"] != rows[0]["recording_id"]]
            check(len({r["recording_id"] for r in new}) == 1, "second cycle got its own recording id")
            g2 = [new[i + 1]["start_epoch"] - (new[i]["start_epoch"] + new[i]["duration_sec"]) for i in range(len(new) - 1)]
            check(all(abs(g) < 0.005 for g in g2), "chunks still tile exactly after an outage")

            print("\n[5] pause, then revoke")
            control(True)
            wait_for(lambda: state()["recording"], 12, "recording for pause test")
            page.click("#opts > summary")                              # the buttons live under "Options" now (nobody needs them)
            page.click("#pauseBtn")
            wait_for(lambda: not state()["recording"], 8, "pause stops recording")
            check("paused" in page.inner_text("#headline").lower(), "page says the mic is paused")
            page.click("#pauseBtn")
            control(False)
            httpx.delete(base + f"/api/v1/invites/{invite['invite_id']}", headers=H)
            wait_for(lambda: state()["auth"], 10, "page noticed the revoked link")
            check("doesn't work" in page.inner_text("#headline"), "revoked link shows a clear message")
            r = httpx.get(base + "/api/v1/join/whoami", headers={"Authorization": f"Bearer {invite['token']}"})
            check(r.status_code == 401, "server refuses the revoked token")

            print("\n[6] a bad link")
            page2 = ctx.new_page()
            page2.goto(base + "/join#inv_deadbeef_notarealtoken")
            wait_for(lambda: "not valid" in page2.inner_text("#who"), 10, "bad token rejected")
            check("not valid" in page2.inner_text("#who"), "bad invite shows 'Invite not valid'")

            check(not [e for e in errors if "Failed to load resource" not in e], f"no JavaScript errors ({errors[:3]})")
            browser.close()
    finally:
        srv.terminate()
        try:
            srv.wait(10)
        except Exception:
            srv.kill()
        log.close()
    print(f"\n{'ALL CHECKS PASSED' if not FAILS else str(len(FAILS)) + ' CHECK(S) FAILED'}   (work dir: {work})")
    return 1 if FAILS else 0


if __name__ == "__main__":
    sys.exit(main())
