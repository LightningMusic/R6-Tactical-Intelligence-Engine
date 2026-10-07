"""
A microphone that delivers nothing but zeros (what both teammates' recorders produced on 2026-10-05) must be
impossible to miss on the recorder page, and a working one must not trigger the warning.

Throwaway server (temp data dir), real Edge/Chrome, a fake microphone that is (a) pure digital silence and
(b) tone bursts. Checks: the red banner, the "mic is silent" headline, the tab title (what shows on the taskbar
when the page is minimized), the mic's name on the page, that nothing false appears for a working mic, and that
the host's recorder log would say the same thing.

Needs playwright. Run:  python scripts/e2e_silent_mic.py
"""
from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import time
import wave
from pathlib import Path

import httpx
from playwright.sync_api import sync_playwright

sys.path.insert(0, str(Path(__file__).resolve().parent))
from e2e_browser_recorder import FAILS, MAIN, ROOT, VOICE, check, free_port, make_fake_mic, wait_for  # noqa: E402


def make_silent_mic(path: Path, seconds: int = 60, rate: int = 48000) -> None:
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1); w.setsampwidth(2); w.setframerate(rate)
        w.writeframes(b"\x00\x00" * rate * seconds)


def main() -> int:
    work = Path(tempfile.mkdtemp(prefix="r6_e2e_silent_"))
    port = free_port()
    base = f"http://127.0.0.1:{port}"
    silent, tone = work / "silent.wav", work / "tone.wav"
    make_silent_mic(silent)
    make_fake_mic(tone, seconds=60)
    env = {**os.environ, "R6_SERVER_DATA_DIR": str(work / "server_data"), "R6_SERVER_API_TOKEN": MAIN,
           "R6_SERVER_VOICE_TOKEN": VOICE, "PYTHONIOENCODING": "utf-8"}
    log = (work / "server.log").open("w")
    srv = subprocess.Popen([os.environ.get("R6_E2E_SERVER_PYTHON", sys.executable), "-m", "uvicorn", "server.main:app",
                            "--host", "127.0.0.1", "--port", str(port)], cwd=ROOT, env=env, stdout=log, stderr=subprocess.STDOUT)
    H = {"Authorization": f"Bearer {MAIN}"}
    try:
        if not wait_for(lambda: httpx.get(base + "/api/v1/health", timeout=2).status_code == 200, 90, "server up"):
            return 1
        with sync_playwright() as p:
            def run(label: str, mic: Path, username: str):
                inv = httpx.post(base + "/api/v1/invites", headers=H, json={"username": username}).json()
                browser = None
                for channel in ("msedge", "chrome"):
                    try:
                        browser = p.chromium.launch(channel=channel, headless=True, args=[
                            "--use-fake-device-for-media-stream", "--use-fake-ui-for-media-stream",
                            f"--use-file-for-fake-audio-capture={mic}", "--autoplay-policy=no-user-gesture-required"])
                        break
                    except Exception:
                        pass
                if browser is None:
                    print("No Edge/Chrome available.")
                    sys.exit(2)
                page = browser.new_context(permissions=["microphone"]).new_page()
                state = lambda: page.evaluate("window.__r6rec && window.__r6rec.get()")
                page.goto(base + "/join#" + inv["token"])
                wait_for(lambda: (s := state()) and s.get("username") and s, 15, f"{label}: page identified the player")
                page.click("#startBtn")
                wait_for(lambda: (s := state()) and s.get("started") and s, 15, f"{label}: recorder started")
                return browser, page, state, inv

            print("\n[1] a microphone that sends only zeros")
            browser, page, state, inv = run("silent", silent, "Silent_Mic_Player")
            # Nothing is recording yet (the host hasn't started): the warning must already be there.
            ok = wait_for(lambda: state()["dead"], 15, "dead-mic detection")
            check(bool(ok), "the page detects a mic that sends nothing, before any session has started")
            # The page now looks for another microphone by itself first (every fake device here plays the same
            # silence), then says that nothing on the PC works. Wait for that verdict.
            wait_for(lambda: state()["healFailed"] and not state()["healing"], 60, "the page finishing its search for another microphone")
            wait_for(lambda: "sending any sound" in page.inner_text("#bannerBad"), 15, "the banner (the page redraws twice a second)")
            time.sleep(0.6)
            check("sending any sound" in page.inner_text("#bannerBad"), "a red banner explains it: " + page.inner_text("#bannerBad")[:80])
            check("mute switch" in page.inner_text("#bannerBad"), "and tells them what to check")
            check("silent" in page.inner_text("#headline").lower(), "the headline says the mic is silent")
            check(state()["title"].startswith("⚠"), "the tab title carries the warning (visible when minimized): " + state()["title"])
            check("Using:" in page.inner_text("#micName"), "the page shows which microphone is in use: " + page.inner_text("#micName"))
            def mine_now():
                st = httpx.get(base + "/api/v1/companion/status", headers=H).json()["companions"]
                return [c for c in st if c["username"] == "Silent_Mic_Player" and c["status"].get("mic_ok") is False]
            mine = wait_for(mine_now, 12, "the host being told within a few seconds") or []
            check(bool(mine), "the host is told too, within seconds (mic_ok false in the check-in)")
            from app.companion_link import CompanionLink
            line = CompanionLink.describe(mine[0]) if mine else ""
            check("mic looks silent" in line, "and the host app's log line says so: " + line)
            browser.close()

            print("\n[2] a working microphone: no false alarm")
            browser, page, state, inv2 = run("tone", tone, "Working_Mic_Player")
            time.sleep(10)
            check(not state()["dead"], "no dead-mic warning for a working mic")
            check(not page.inner_text("#bannerBad").strip(), "no red banner")
            check(not state()["title"].startswith("⚠"), "the tab title stays normal: " + state()["title"])
            check(bool(state()["mic"]), "its name is shown: " + str(state()["mic"]))

            print("\n[3] audio arriving at the wrong speed (2026-10-06: 4,070 s of audio in 6,160 s of real time)")
            httpx.put(base + "/api/v1/companion/control", headers=H, json={"recording": True}, timeout=5)
            wait_for(lambda: state()["recording"], 15, "page began recording")
            time.sleep(66)                                            # the page judges the speed after a minute
            r = state()["ratio"]
            check(r is not None and 0.95 < r < 1.05, f"a healthy page measures its audio at real-time speed ({r})")
            check("normal speed" not in page.inner_text("#bannerWarn"), "and shows no speed warning")
            page.evaluate("window.__r6rec.testLoseAudio(33)")         # as if a third of the audio never arrived
            time.sleep(1.5)
            r2 = state()["ratio"]
            check(r2 is not None and r2 < 0.6, f"lost audio shows up in the measurement ({r2})")
            wait_for(lambda: "normal speed" in page.inner_text("#bannerWarn"), 5, "the speed warning")
            check("% of normal speed" in page.inner_text("#bannerWarn"), "the player is told: " + page.inner_text("#bannerWarn")[:90])

            def host_sees():
                st = httpx.get(base + "/api/v1/companion/status", headers=H).json()["companions"]
                return [c for c in st if c["username"] == "Working_Mic_Player" and (c["status"].get("audio_ratio") or 1) < 0.6]
            seen = wait_for(host_sees, 10, "the host being told")
            check(bool(seen), "the server holds the slow ratio for the host: " + (str(seen[0]["status"].get("audio_ratio")) if seen else "none"))
            httpx.put(base + "/api/v1/companion/control", headers=H, json={"recording": False}, timeout=5)
            browser.close()
    finally:
        srv.terminate()
        try:
            srv.wait(10)
        except Exception:
            srv.kill()
    print("\nALL CHECKS PASSED" if not FAILS else f"\nFAILED: {FAILS}", f"  (work dir: {work})")
    return 1 if FAILS else 0


if __name__ == "__main__":
    sys.path.insert(0, str(ROOT))
    sys.exit(main())
