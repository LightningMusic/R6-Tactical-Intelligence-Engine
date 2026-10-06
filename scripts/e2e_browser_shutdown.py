"""
What survives when a teammate's computer is switched off mid-recording?

Starts a throwaway server (temp data dir, nothing of yours is touched), records in a real browser with a
fake microphone while the network is down (so every 20 s chunk can only be kept in the browser), then
KILLS the browser process the hard way, as a shutdown or power cut would. A fresh browser on the same
profile then opens /join (no invite link: the page must remember it) and everything that was kept must
reach the server by itself. Only the audio still in memory (under one chunk, 20 s) may be lost.

Needs playwright (pip install playwright; it drives the Edge/Chrome already installed).
Run:  python scripts/e2e_browser_shutdown.py
"""
from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import httpx
import numpy as np
import soundfile as sf
from playwright.sync_api import sync_playwright

sys.path.insert(0, str(Path(__file__).resolve().parent))
from e2e_browser_recorder import (FAILS, MAIN, ROOT, VOICE, check, chunk_rows, free_port,  # noqa: E402
                                  make_fake_mic, wait_for)


def kill_browser(profile: Path) -> int:
    """Hard-kills every browser process using this profile (what a power cut does, minus the disk cache)."""
    script = ("Get-CimInstance Win32_Process | Where-Object { $_.CommandLine -like '*" + str(profile).replace("'", "''")
              + "*' } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force; $_.ProcessId }")
    out = subprocess.run(["powershell", "-NoProfile", "-Command", script], capture_output=True, text=True).stdout
    return len(out.split())


def main() -> int:
    work = Path(tempfile.mkdtemp(prefix="r6_e2e_shutdown_"))
    data, profile = work / "server_data", work / "profile"
    port = free_port()
    base = f"http://127.0.0.1:{port}"
    mic = work / "fake_mic.wav"
    make_fake_mic(mic, seconds=120)
    env = {**os.environ, "R6_SERVER_DATA_DIR": str(data), "R6_SERVER_API_TOKEN": MAIN, "R6_SERVER_VOICE_TOKEN": VOICE,
           "PYTHONIOENCODING": "utf-8"}
    log = (work / "server.log").open("w")
    srv = subprocess.Popen([os.environ.get("R6_E2E_SERVER_PYTHON", sys.executable), "-m", "uvicorn", "server.main:app",
                            "--host", "127.0.0.1", "--port", str(port)], cwd=ROOT, env=env, stdout=log, stderr=subprocess.STDOUT)
    H = {"Authorization": f"Bearer {MAIN}"}
    args = ["--use-fake-device-for-media-stream", "--use-fake-ui-for-media-stream",
            f"--use-file-for-fake-audio-capture={mic}", "--autoplay-policy=no-user-gesture-required"]
    try:
        if not wait_for(lambda: httpx.get(base + "/api/v1/health", timeout=2).status_code == 200, 90, "server up"):
            return 1
        invite = httpx.post(base + "/api/v1/invites", headers=H, json={"username": "Shutdown_Player"}).json()
        control = lambda on: httpx.put(base + "/api/v1/companion/control", headers=H, json={"recording": on}, timeout=5)
        db = data / "server_matches.db"

        with sync_playwright() as p:
            def launch():
                for channel in ("msedge", "chrome"):
                    try:
                        return p.chromium.launch_persistent_context(str(profile), channel=channel, headless=True, args=args,
                                                                    permissions=["microphone"])
                    except Exception as exc:
                        print(f"could not launch {channel}: {str(exc)[:100]}")
                return None

            print("\n[1] record while the network is down, so audio can only be kept in the browser")
            ctx = launch()
            if ctx is None:
                print("No Edge/Chrome available.")
                return 2
            page = ctx.new_page()
            state = lambda: page.evaluate("window.__r6rec && window.__r6rec.get()")
            page.goto(base + "/join#" + invite["token"])
            wait_for(lambda: (st := state()) and st.get("username") and st, 15, "page identified the player")
            page.click("#startBtn")
            wait_for(lambda: (st := state()) and st.get("started") and st, 15, "recorder started")
            control(True)
            wait_for(lambda: state()["recording"], 12, "page began recording")
            time.sleep(4)
            ctx.set_offline(True)                                   # from here on nothing can upload
            time.sleep(48)
            waiting = state()["queued"]
            check(waiting >= 2, f"{waiting} chunk(s) saved in the browser while offline")
            check(len(chunk_rows(db)) <= 1, "(almost) nothing reached the server before the shutdown")
            recorded = (time.time())

            print("\n[2] hard kill: the process is gone mid-recording, no Stop pressed")
            killed = kill_browser(profile)
            check(killed >= 1, f"killed {killed} browser process(es)")
            try:
                ctx.close()
            except Exception:
                pass
            time.sleep(2)
            control(False)
            before = len(chunk_rows(db))

            print("\n[3] next day: a fresh browser on the same profile, opened WITHOUT the invite link")
            ctx = launch()
            page = ctx.new_page()
            state = lambda: page.evaluate("window.__r6rec && window.__r6rec.get()")
            page.goto(base + "/join")
            s = wait_for(lambda: (st := state()) and st.get("username") and st, 15, "page remembered the invite")
            check(bool(s) and s["username"] == "Shutdown_Player", "the page remembered who this is, with no link")
            wait_for(lambda: len(chunk_rows(db)) >= before + waiting, 40, "leftover audio uploaded by itself")
            rows = chunk_rows(db)
            new = rows[before:]
            check(len(new) >= waiting, f"{len(new)} leftover chunk(s) uploaded without anyone pressing anything")
            check(len({r["recording_id"] for r in new}) == 1, "all from the one interrupted recording")
            gaps = [new[i + 1]["start_epoch"] - (new[i]["start_epoch"] + new[i]["duration_sec"]) for i in range(len(new) - 1)]
            check(all(abs(g) < 0.005 for g in gaps), "the saved chunks still tile exactly")
            ok = True
            for r in new:
                d, sr = sf.read(Path(r["file_path"]), dtype="float32")
                ok &= sr == 16000 and abs(len(d) / sr - r["duration_sec"]) < 0.1 and float(np.abs(d).max()) > 0.1
            check(ok, "the uploaded audio is real and decodes")
            secs = sum(r["duration_sec"] for r in new)
            check(secs >= 40, f"{secs:.0f} s of the ~52 s recorded survived (the last unsaved <20 s may be lost)")
            check(wait_for(lambda: state()["queued"] == 0, 15, "queue empty") is not None, "the browser's own queue is empty afterwards")
            ctx.close()
    finally:
        srv.terminate()
        try:
            srv.wait(10)
        except Exception:
            srv.kill()
    print("\nALL CHECKS PASSED" if not FAILS else f"\nFAILED: {FAILS}", f"  (work dir: {work})")
    return 1 if FAILS else 0


if __name__ == "__main__":
    sys.exit(main())
