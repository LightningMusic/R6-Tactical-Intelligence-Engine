"""
The whole web-recorder chain against the LIVE server, over the public address, as it happens at practice:

  host app's own key -> "recording" signal -> a real browser page (fake microphone) notices -> records ->
  uploads 20 s chunks -> the server stores them -> the host stops -> the page stops -> final chunk lands.

Uses a throwaway invite ("Preflight_Web", revoked at the end) and briefly flips the host's start/stop signal
(restored to what it was). Don't run it while teammates' recorders are online. Leaves one short test
recording on the server (named Preflight_Web), which is easy to delete.

Needs playwright and Edge/Chrome:  python scripts/e2e_live_recorder.py [--base URL] [--seconds 45]
"""
from __future__ import annotations

import argparse
import sys
import tempfile
import time
from pathlib import Path

import requests
from playwright.sync_api import sync_playwright

sys.path.insert(0, str(Path(__file__).resolve().parent))
from e2e_browser_recorder import make_fake_mic  # noqa: E402

FAILS: list[str] = []


def check(ok: bool, what: str, detail: str = "") -> None:
    print(("  PASS  " if ok else "  FAIL  ") + what + (f"  ({detail})" if detail else ""), flush=True)
    if not ok:
        FAILS.append(what)


def host_credentials() -> tuple[str | None, str | None]:
    """(address, key) the host app has built in; falls back to the dev machine's own server config."""
    try:
        from preflight_connections import read_embedded, drive_for
        root = drive_for("R6_PROJ")
        if root:
            url, key = read_embedded(Path(root) / "R6Analyzer" / "R6Analyzer.exe", "app.server_credentials")
            if key:
                return url, key
    except Exception:
        pass
    try:
        from sync_deployed_server_config import _read_docker_config
        cfg = _read_docker_config()
        return cfg.get("public_url"), cfg["api_token"]
    except Exception:
        return None, None


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


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", help="server address (default: the one built into the host app's exe)")
    ap.add_argument("--seconds", type=int, default=45)
    a = ap.parse_args()
    url, key = host_credentials()
    base = (a.base or url or "").rstrip("/")
    if not base or not key:
        print("No server address or host key found: plug in the R6_PROJ stick or pass --base.")
        return 2
    H = {"Authorization": f"Bearer {key}"}
    work = Path(tempfile.mkdtemp(prefix="r6_live_"))
    mic = work / "mic.wav"
    make_fake_mic(mic, seconds=90)

    st = requests.get(base + "/api/v1/companion/status", headers=H, timeout=15).json()
    online = [c["username"] for c in st["companions"] if c["seconds_since_seen"] < 60]
    if online:
        print(f"Teammates' recorders are online right now ({', '.join(online)}); not flipping the host signal. Try again later.")
        return 3
    was = bool(st["control"]["recording"])
    control = lambda on: requests.put(base + "/api/v1/companion/control", headers=H, json={"recording": on}, timeout=15)
    inv = requests.post(base + "/api/v1/invites", headers=H, json={"username": "Preflight_Web"}, timeout=15).json()
    rid = None
    try:
        with sync_playwright() as p:
            browser = None
            for channel in ("msedge", "chrome"):
                try:
                    browser = p.chromium.launch(channel=channel, headless=True, args=[
                        "--use-fake-device-for-media-stream", "--use-fake-ui-for-media-stream",
                        f"--use-file-for-fake-audio-capture={mic}", "--autoplay-policy=no-user-gesture-required"])
                    break
                except Exception as exc:
                    print(f"could not launch {channel}: {str(exc)[:100]}")
            if browser is None:
                print("No Edge/Chrome available.")
                return 2
            ctx = browser.new_context(permissions=["microphone"])
            page = ctx.new_page()
            state = lambda: page.evaluate("window.__r6rec && window.__r6rec.get()")

            print("\n[1] a teammate opens their invite link on the public address")
            page.goto(base + "/join#" + inv["token"], timeout=60000)
            s = wait_for(lambda: (st := state()) and st.get("username") and st, 30, "page identified the player")
            check(bool(s) and s["username"] == "Preflight_Web", "the page knows who it is", s and s["username"])
            check("inv_" not in page.url, "the link's secret is gone from the address bar")
            page.click("#startBtn")
            check(bool(wait_for(lambda: (st := state()) and st.get("started") and st, 20, "mic started")), "Start works and the microphone opens")
            time.sleep(6)
            check(not state()["recording"], "it waits (records nothing) until the host starts")

            print("\n[2] the host app starts its session: its own key sends the signal")
            t0 = time.time()
            r = control(True)
            check(r.status_code == 200, "the host's start signal is accepted", f"HTTP {r.status_code}")
            check(bool(wait_for(lambda: state()["recording"], 20, "page began recording")),
                  "the page notices and starts recording", f"{time.time() - t0:.1f} s")
            time.sleep(a.seconds)
            print("\n[3] the host stops")
            control(False)
            check(bool(wait_for(lambda: not state()["recording"], 20, "page stopped")), "the page stops with the host")
            wait_for(lambda: state()["queued"] == 0, 40, "uploads drained")
            recs = requests.get(base + "/api/v1/voice/recordings?limit=20", headers=H, timeout=15).json()["recordings"]
            mine = [r for r in recs if r["username"] == "Preflight_Web"]
            rid = mine[0]["recording_id"] if mine else None
            check(bool(mine), "the server has the recording")
            if mine:
                check(mine[0]["chunks"] >= 2, f"{mine[0]['chunks']} chunks stored", f"{mine[0]['seconds']:.0f} s of audio")
                check(abs(mine[0]["seconds"] - a.seconds) < 12, "about as much audio as the host session lasted")
                check(bool(mine[0]["finished"]), "the final chunk arrived (the recording is marked finished)")
            browser.close()
    finally:
        control(was)                                                   # leave the host signal as it was
        requests.delete(base + f"/api/v1/invites/{inv['invite_id']}", headers=H, timeout=15)
    r = requests.get(base + "/api/v1/join/whoami", headers={"Authorization": f"Bearer {inv['token']}"}, timeout=15)
    check(r.status_code == 401, "the throwaway invite is revoked")
    print("\nALL CHECKS PASSED" if not FAILS else f"\nFAILED: {FAILS}")
    return 1 if FAILS else 0


if __name__ == "__main__":
    sys.exit(main())
