"""
The recorder page must work for people who do nothing: after one Start on a browser, opening the link starts
the recording by itself, a microphone that sends only zeros is replaced by one that works (without picking a
camera or loopback device), and the choice is remembered for the next time.

Throwaway server (temp data dir), real Edge/Chrome. Part A replaces the browser's microphones with stand-ins
(a dead headset, a camera, a working headset) so the recovery can be checked exactly; part B uses Chromium's
own fake microphone WITHOUT the "autoplay without a touch" flag, which is how a teammate's browser behaves.

Needs playwright. Run:  python scripts/e2e_zero_touch.py
"""
from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import httpx
from playwright.sync_api import sync_playwright

sys.path.insert(0, str(Path(__file__).resolve().parent))
from e2e_browser_recorder import FAILS, MAIN, ROOT, VOICE, check, chunk_rows, free_port, make_fake_mic, wait_for  # noqa: E402

# Stand-in microphones. "dead" delivers exact zeros (what both teammates' recorders produced on 2026-10-05).
STUBS = """
(() => {
  const devices = [
    { deviceId: "default",  groupId: "g-dead", label: "Default - Microphone (Dead Headset)", mode: "dead" },
    { deviceId: "dead-id",  groupId: "g-dead", label: "Microphone (Dead Headset)", mode: "dead" },
    { deviceId: "cam-id",   groupId: "g-cam",  label: "Microphone (USB Live Camera audio)", mode: "tone" },
    { deviceId: "mix-id",   groupId: "g-mix",  label: "Stereo Mix (Realtek Audio)", mode: "tone" },
    { deviceId: "head-id",  groupId: "g-head", label: "Headset Microphone (Working Headset)", mode: "tone" },
    { deviceId: "other-id", groupId: "g-other", label: "Microphone (Realtek Audio)", mode: "tone" },
  ];
  window.__opened = [];
  const md = navigator.mediaDevices;
  md.enumerateDevices = async () => devices.map((d) => ({ deviceId: d.deviceId, groupId: d.groupId, label: d.label, kind: "audioinput" }));
  md.getUserMedia = async (c) => {
    const want = c && c.audio && c.audio.deviceId;
    const id = (want && (want.exact || want)) || "default";
    const d = devices.find((x) => x.deviceId === id);
    window.__opened.push(id);
    if (!d) throw new DOMException("no such device", "NotFoundError");
    const ctx = new AudioContext();
    await ctx.resume().catch(() => {});
    const dest = ctx.createMediaStreamDestination();
    if (d.mode === "tone") {
      const o = ctx.createOscillator(), g = ctx.createGain();
      o.frequency.value = 220; g.gain.value = 0.2; o.connect(g); g.connect(dest); o.start();
    } else {
      const k = ctx.createConstantSource(); k.offset.value = 0; k.connect(dest); k.start();
    }
    const track = dest.stream.getAudioTracks()[0];
    Object.defineProperty(track, "label", { value: d.label });
    track.getSettings = () => ({ deviceId: d.deviceId, groupId: d.groupId });
    return dest.stream;
  };
})();
"""


def launch(p, args):
    for channel in ("msedge", "chrome"):
        try:
            return p.chromium.launch(channel=channel, headless=True, args=args)
        except Exception:
            pass
    print("No Edge/Chrome available.")
    sys.exit(2)


def main() -> int:
    work = Path(tempfile.mkdtemp(prefix="r6_e2e_zero_"))
    port = free_port()
    base = f"http://127.0.0.1:{port}"
    tone = work / "tone.wav"
    make_fake_mic(tone, seconds=120)
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
            # ───── A: stand-in microphones ─────
            print("\n[A1] first visit: the player presses Start once; the dead microphone is replaced by itself")
            inv = httpx.post(base + "/api/v1/invites", headers=H, json={"username": "Zero_Touch_A"}).json()
            browser = launch(p, ["--autoplay-policy=no-user-gesture-required"])
            ctx = browser.new_context(permissions=["microphone"])
            ctx.add_init_script(STUBS)
            page = ctx.new_page()
            state = lambda: page.evaluate("window.__r6rec && window.__r6rec.get()")
            page.goto(base + "/join#" + inv["token"])
            wait_for(lambda: (s := state()) and s.get("username") and s, 15, "page identified the player")
            check(not page.is_visible("#pauseBtn") and not page.is_visible("#stopBtn"),
                  "Pause and Stop are tucked away under Options (nobody needs them)")
            page.click("#startBtn")
            wait_for(lambda: (s := state()) and s.get("started") and s, 15, "recorder started")
            healed = wait_for(lambda: (s := state()) and s.get("healed") and s, 40, "the page finding a microphone that works")
            check(bool(healed) and "Working Headset" in healed["healed"], f"it chose the headset: {healed and healed['healed']}")
            opened = page.evaluate("window.__opened")
            check("cam-id" not in opened and "mix-id" not in opened, f"never tried the camera or Stereo Mix (opened: {opened})")
            check("dead-id" not in opened[1:], "never retried the duplicate entry of the dead headset")
            time.sleep(1)
            s = state()
            check(not s["dead"] and not s["healing"], "no 'mic is silent' state after the switch")
            check("Working Headset" in page.inner_text("#micName"), "the page shows which microphone it uses: " + page.inner_text("#micName"))
            check("by itself" in page.inner_text("#bannerWarn"), "and says it switched: " + page.inner_text("#bannerWarn")[:80])
            check(page.evaluate("localStorage.getItem('r6rec.mic')") == "head-id", "the working microphone is remembered")
            check(page.evaluate("localStorage.getItem('r6rec.auto')") == "1", "Start is remembered: next time nothing needs pressing")

            def host_line():
                st = httpx.get(base + "/api/v1/companion/status", headers=H).json()["companions"]
                return [c for c in st if c["username"] == "Zero_Touch_A" and "Working Headset" in str(c["status"].get("mic_healed"))]
            check(bool(wait_for(host_line, 10, "host being told")), "the host is told which microphone it switched to")
            check(all(c["status"].get("mic_ok") is not False for c in httpx.get(base + "/api/v1/companion/status", headers=H).json()["companions"]),
                  "and the host sees a working microphone")

            print("\n[A2] closing and reopening the link: it starts by itself, on the remembered microphone")
            page.close()
            page = ctx.new_page()
            state = lambda: page.evaluate("window.__r6rec && window.__r6rec.get()")
            page.goto(base + "/join")                                       # no #token: the browser remembers the invite
            s = wait_for(lambda: (st := state()) and st.get("started") and st, 20, "automatic start")
            check(bool(s), "the page started recording-ready with nobody pressing anything")
            check(bool(s) and s["autoStarted"], "...and knows it was an automatic start")
            check(bool(s) and "Working Headset" in s["mic"], f"on the microphone that worked last time: {s and s['mic']}")
            opened = page.evaluate("window.__opened")
            check(opened == ["head-id"], f"without trying the dead one first (opened: {opened})")
            check(not page.is_visible("#startBtn"), "the Start button is out of the way")

            print("\n[A3] the host's start/stop is all that is needed")
            httpx.put(base + "/api/v1/companion/control", headers=H, json={"recording": True}, timeout=5)
            check(bool(wait_for(lambda: state()["recording"], 12, "page began recording")), "it recorded when the host started")
            time.sleep(25)
            httpx.put(base + "/api/v1/companion/control", headers=H, json={"recording": False}, timeout=5)
            wait_for(lambda: not state()["recording"], 12, "page stopped")
            wait_for(lambda: state()["queued"] == 0, 20, "upload queue drained")
            rows = [r for r in chunk_rows(work / "server_data" / "server_matches.db") if r["username"] == "Zero_Touch_A"]
            check(len(rows) >= 1, f"its audio reached the server ({len(rows)} chunks)")
            browser.close()

            print("\n[A4] no microphone works at all: it says so, keeps trying later, and puts the original back")
            inv_b = httpx.post(base + "/api/v1/invites", headers=H, json={"username": "Zero_Touch_B"}).json()
            browser = launch(p, ["--autoplay-policy=no-user-gesture-required"])
            ctx = browser.new_context(permissions=["microphone"])
            ctx.add_init_script(STUBS.replace('mode: "tone"', 'mode: "dead"'))
            page = ctx.new_page()
            state = lambda: page.evaluate("window.__r6rec && window.__r6rec.get()")
            page.goto(base + "/join#" + inv_b["token"])
            wait_for(lambda: (s := state()) and s.get("username") and s, 15, "page identified the player")
            page.click("#startBtn")
            done = wait_for(lambda: (s := state()) and s.get("healFailed") and not s.get("healing") and s, 60, "the search ending")
            check(bool(done), "after trying everything it concludes that nothing works")
            wait_for(lambda: "mute switch" in page.inner_text("#bannerBad"), 15, "the red banner")
            check("mute switch" in page.inner_text("#bannerBad"), "the red banner says what to check")
            opened = page.evaluate("window.__opened")
            check("cam-id" not in opened and "mix-id" not in opened, f"still never touched the camera or Stereo Mix ({opened})")
            check(opened[-1] == "default", f"and put the original microphone back ({opened[-1]})")
            def b_silent():
                st = httpx.get(base + "/api/v1/companion/status", headers=H).json()["companions"]
                return [c for c in st if c["username"] == "Zero_Touch_B" and c["status"].get("mic_ok") is False]
            check(bool(wait_for(b_silent, 15, "host being told")), "the host sees that this one is silent")
            browser.close()

            # ───── B: the browser's own fake microphone, with the strict autoplay rule ─────
            print("\n[B1] real browser rules: after one Start, does opening the link start by itself?")
            inv_c = httpx.post(base + "/api/v1/invites", headers=H, json={"username": "Zero_Touch_C"}).json()
            browser = launch(p, ["--use-fake-device-for-media-stream", "--use-fake-ui-for-media-stream",
                                 f"--use-file-for-fake-audio-capture={tone}"])             # note: NO autoplay flag
            ctx = browser.new_context(permissions=["microphone"])
            page = ctx.new_page()
            state = lambda: page.evaluate("window.__r6rec && window.__r6rec.get()")
            page.goto(base + "/join#" + inv_c["token"])
            wait_for(lambda: (s := state()) and s.get("username") and s, 15, "page identified the player")
            page.click("#startBtn")
            first = wait_for(lambda: (s := state()) and s.get("started") and s, 15, "manual start")
            check(bool(first) and first["ctx"] == "running", f"after a real click the audio engine runs ({first and first['ctx']})")
            page.close()
            page = ctx.new_page()
            state = lambda: page.evaluate("window.__r6rec && window.__r6rec.get()")
            page.goto(base + "/join")
            s = wait_for(lambda: (st := state()) and (st.get("started") or st.get("needsTap")) and st, 20, "automatic start or a tap request")
            by_itself = bool(s) and s["started"] and s["ctx"] == "running" and not s["needsTap"]
            print(f"      >>> with no touch at all this browser {'STARTED BY ITSELF' if by_itself else 'asked for one tap'} (ctx={s and s['ctx']})")
            if not by_itself:
                check(bool(s) and s["needsTap"], "it says plainly that it needs one touch")
                check("click" in page.inner_text("#headline").lower(), "the headline says so: " + page.inner_text("#headline"))
                page.mouse.click(300, 300)                                      # any touch anywhere
                s = wait_for(lambda: (st := state()) and st.get("started") and st["ctx"] == "running" and not st["needsTap"] and st, 10,
                             "the touch finishing the start")
                check(bool(s), "one touch anywhere on the page is enough")
            else:
                check(True, "no touch needed at all")
            httpx.put(base + "/api/v1/companion/control", headers=H, json={"recording": True}, timeout=5)
            check(bool(wait_for(lambda: state()["recording"], 12, "recording")), "and it follows the host's start")
            httpx.put(base + "/api/v1/companion/control", headers=H, json={"recording": False}, timeout=5)
            browser.close()

            print("\n[B2] a browser that forgot the microphone permission: it waits for a touch instead of asking nobody")
            inv_d = httpx.post(base + "/api/v1/invites", headers=H, json={"username": "Zero_Touch_D"}).json()
            browser = launch(p, ["--use-fake-device-for-media-stream", f"--use-file-for-fake-audio-capture={tone}"])
            ctx = browser.new_context()                                         # no permission granted, and no auto-accepting prompt
            ctx.add_init_script("try { localStorage.setItem('r6rec.auto', '1'); } catch (e) {}")
            page = ctx.new_page()
            state = lambda: page.evaluate("window.__r6rec && window.__r6rec.get()")
            page.goto(base + "/join#" + inv_d["token"])
            s = wait_for(lambda: (st := state()) and st.get("username") and st, 15, "page identified the player")
            time.sleep(2)
            s = state()
            check(not s["started"] and s["needsTap"], f"it does not start behind the player's back (started={s['started']}, needsTap={s['needsTap']})")
            def host_waiting():
                st = httpx.get(base + "/api/v1/companion/status", headers=H).json()["companions"]
                return [c for c in st if c["username"] == "Zero_Touch_D" and c["status"].get("needs_tap")]
            check(bool(wait_for(host_waiting, 10, "host being told")), "the host is told this page is waiting for a click")
            ctx.grant_permissions(["microphone"])                               # what pressing "Allow" on the browser's prompt does
            page.mouse.click(300, 300)
            check(bool(wait_for(lambda: state()["started"], 10, "touch starting it")), "a touch (and Allow) starts it")
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
