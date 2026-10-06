"""
Pre-practice check of every connection a session depends on, using the credentials that are actually
INSIDE each built exe (not the ones in a settings file) against the live public address.

  python scripts/preflight_connections.py                 # read-only checks
  python scripts/preflight_connections.py --live-invite   # also run a real browser-recorder round trip with
                                                          # a throwaway invite (leaves one small test recording)

Finds the USB sticks by volume label (R6_PROJ, R6_COMPANION), because drive letters change between
plug-ins. Never prints a key or token. Exit code 0 only when nothing FAILED.
"""
from __future__ import annotations

import argparse
import json
import math
import re
import statistics
import subprocess
import sys
import time
import types
import uuid
import wave
from pathlib import Path

import requests

PASS, WARN, FAIL = "PASS", "WARN", "FAIL"
results: list[tuple[str, str, str]] = []


def say(level: str, what: str, detail: str = "") -> None:
    results.append((level, what, detail))
    print(f"  {level:<4}  {what}" + (f"  ({detail})" if detail else ""), flush=True)


def drive_for(label: str) -> str | None:
    try:
        out = subprocess.run(["powershell", "-NoProfile", "-Command",
                              f"(Get-Volume -FileSystemLabel '{label}' -ErrorAction SilentlyContinue).DriveLetter"],
                             capture_output=True, text=True, timeout=20).stdout.strip()
        return f"{out[0]}:\\" if out else None
    except Exception:
        return None


def embedded_strings(code) -> list[str]:
    out: list[str] = []
    for c in code.co_consts:
        if isinstance(c, str):
            out.append(c)
        elif isinstance(c, types.CodeType):
            out += embedded_strings(c)
    return out


def read_embedded(exe: Path, module: str) -> tuple[str | None, str | None]:
    """(address, secret) baked into an exe at build time."""
    from PyInstaller.archive.readers import CArchiveReader
    code = CArchiveReader(str(exe)).open_embedded_archive("PYZ.pyz").extract(module)
    strs = embedded_strings(code)
    url = next((s for s in strs if s.startswith("http")), None)
    secret = next((s for s in sorted(strs, key=len, reverse=True) if len(s) >= 20 and not s.startswith("http")
                   and " " not in s and "\n" not in s), None)
    return url, secret


def get(url, key=None, **kw):
    headers = {"Authorization": f"Bearer {key}"} if key else {}
    return requests.get(url, headers=headers, timeout=kw.pop("timeout", 15), **kw)


def tone_wav(path: Path, seconds=2, rate=16000) -> None:
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1); w.setsampwidth(2); w.setframerate(rate)
        w.writeframes(b"".join(int(9000 * math.sin(2 * math.pi * 440 * i / rate)).to_bytes(2, "little", signed=True)
                               for i in range(seconds * rate)))


def load_json(p: Path):
    try:
        return json.loads(p.read_text(encoding="utf-8-sig"))
    except Exception:
        return None


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--live-invite", action="store_true", help="run a real round trip with a throwaway invite")
    ap.add_argument("--host-drive", help="host stick root, e.g. F:\\ (default: find by label R6_PROJ)")
    ap.add_argument("--companion-drive", help="companion stick root (default: find by label R6_COMPANION)")
    args = ap.parse_args()

    host_root = Path(args.host_drive or drive_for("R6_PROJ") or "")
    comp_root = Path(args.companion_drive or drive_for("R6_COMPANION") or "")
    exes = {
        "host app (R6Analyzer)": (host_root / "R6Analyzer" / "R6Analyzer.exe", "app.server_credentials", "main"),
        "R6Voice (teammate recorder)": (host_root / "R6Voice" / "R6Voice.exe", "credentials", "voice"),
        "R6Companion (companion stick)": (comp_root / "R6Companion" / "R6Companion.exe", "credentials", "voice"),
    }

    print("\n[1] what is plugged in")
    say(PASS if host_root.exists() else FAIL, f"host stick R6_PROJ found at {host_root}" if host_root.exists() else "host stick R6_PROJ not found")
    say(PASS if comp_root.exists() else WARN, f"companion stick R6_COMPANION found at {comp_root}" if comp_root.exists() else "companion stick R6_COMPANION not plugged in (skipping its checks)")

    print("\n[2] credentials baked into each exe")
    creds: dict[str, tuple[str, str, str]] = {}
    try:
        import PyInstaller.archive.readers  # noqa: F401
        have_pyi = True
    except ImportError:
        have_pyi = False
        say(WARN, "PyInstaller isn't installed here, so the exes' built-in keys can't be read (run with the project's .venv python)")
    for name, (exe, module, kind) in exes.items():
        if not exe.exists():
            say(FAIL if "companion" not in name or comp_root.exists() else WARN, f"{name}: exe not found", str(exe))
            continue
        if not have_pyi:
            continue
        try:
            url, secret = read_embedded(exe, module)
        except Exception as e:                                          # noqa: BLE001
            say(FAIL, f"{name}: couldn't read the built-in credentials", f"{type(e).__name__}")
            continue
        if url and secret:
            creds[name] = (url.rstrip("/"), secret, kind)
            say(PASS, f"{name}: has a server address and a key built in")
        else:
            say(FAIL, f"{name}: NO credentials built in (address={bool(url)}, key={bool(secret)})")
    urls = {c[0] for c in creds.values()}
    if urls:
        say(PASS if len(urls) == 1 else FAIL, "every exe points at the same server address" if len(urls) == 1 else f"exes disagree on the address: {len(urls)} different")
    base = next(iter(urls)) if len(urls) == 1 else (next(iter(urls)) if urls else None)
    if not base:
        print("\nNo server address to test; stopping.")
        return 1

    print(f"\n[3] the server at the public address ({base})")
    times, bad = [], 0
    for _ in range(12):
        t0 = time.time()
        try:
            r = get(base + "/api/v1/health", timeout=10)
            if r.status_code == 200:
                times.append(time.time() - t0)
            else:
                bad += 1
        except Exception:
            bad += 1
        time.sleep(0.4)
    if times:
        p95 = sorted(times)[max(0, int(len(times) * 0.95) - 1)]
        say(PASS if bad == 0 else WARN, f"answered {len(times)} of 12 health checks over HTTPS (certificate valid)",
            f"median {statistics.median(times) * 1000:.0f} ms, slowest {max(times) * 1000:.0f} ms, p95 {p95 * 1000:.0f} ms")
    else:
        say(FAIL, "the public address did not answer at all", "is the Tailscale Funnel up? run: deploy\\r6ctl.bat status")
        return 1
    r = get(base + "/join")
    say(PASS if r.status_code == 200 and "R6 Team Recorder" in r.text else FAIL, "the web recorder page /join loads", f"HTTP {r.status_code}")
    r = get(base + "/api/v1/auth/test", key="definitely-not-a-key")
    say(PASS if r.status_code == 401 else FAIL, "a wrong key is refused (so a 'pass' below means something)", f"HTTP {r.status_code}")

    print("\n[4] each exe's own key against the live server")
    main_key = None
    for name, (url, secret, kind) in creds.items():
        if kind == "main":
            main_key = secret
            r = get(url + "/api/v1/auth/test", key=secret)
            say(PASS if r.status_code == 200 else FAIL, f"{name}: the server accepts its API key", f"HTTP {r.status_code}")
        else:
            r = get(url + "/api/v1/voice/ping", key=secret)
            ok = r.status_code == 200
            say(PASS if ok else FAIL, f"{name}: the server accepts its voice key", f"HTTP {r.status_code}")
            if ok:
                off = r.json()["server_time"] - time.time()
                say(PASS if abs(off) < 2 else WARN, "this PC's clock agrees with the server's", f"{off:+.1f} s")

    print("\n[5] the host stick's saved settings (these override the built-in key)")
    s = load_json(host_root / "R6Analyzer" / "data" / "settings.json")
    if s is None:
        say(FAIL, "host settings.json unreadable")
    else:
        saved = str(s.get("api_key", "")).strip()
        if not saved:
            say(PASS, "no saved API key: the built-in one is used")
        elif main_key and saved == main_key:
            say(PASS, "the saved API key is the current one")
        else:
            say(FAIL, "a saved API key that is NOT the current one overrides the built-in key (the 2026-10-05 failure)")
        su = str(s.get("server_url", "")).strip().rstrip("/")
        say(PASS if (not su or su == base) else FAIL, "saved server address matches" if su else "no saved server address (built-in used)")
        say(PASS if s.get("upload_replays") and s.get("upload_voice") and s.get("upload_automatically") else WARN,
            "uploads are switched on (replays, voice, automatic)")
    q = load_json(host_root / "R6Analyzer" / "data" / "queue" / "queue.json") or {}
    stuck = [k for k, v in q.items() if isinstance(v, dict) and v.get("package_status") != "uploaded"]
    say(PASS if not stuck else WARN, "nothing is waiting to upload from the host stick" if not stuck else f"{len(stuck)} package(s) still waiting to upload")
    try:
        import shutil
        free = shutil.disk_usage(str(host_root)).free / 1e9
        say(PASS if free > 25 else WARN, f"host stick has {free:.0f} GB free for recordings")
    except OSError:
        pass

    print("\n[6] the host app's link to OBS (local)")
    obs = load_json(host_root / "OBS-Studio" / "config" / "obs-studio" / "plugin_config" / "obs-websocket" / "config.json")
    if s and obs:
        enabled = bool(obs.get("server_enabled", True))
        port_ok = int(s.get("obs_port", 4455)) == int(obs.get("server_port", 4455))
        pw_ok = (not obs.get("auth_required", True)) or str(s.get("obs_password", "")) == str(obs.get("server_password", ""))
        say(PASS if enabled else FAIL, "OBS's websocket server is switched on")
        say(PASS if port_ok else FAIL, "the app and OBS agree on the websocket port")
        say(PASS if pw_ok else FAIL, "the app's saved OBS password matches OBS's")
    else:
        say(WARN, "couldn't read the OBS websocket settings to compare")
    say(PASS if (host_root / "OBS-Studio" / "bin" / "64bit" / "obs64.exe").exists() else FAIL, "OBS is on the host stick")

    print("\n[7] the host's 'start recording' signal to teammates' recorders (what failed on 2026-10-05)")
    if main_key:
        st = get(base + "/api/v1/companion/status", key=main_key).json()
        cur = bool(st["control"]["recording"])
        r = requests.put(base + "/api/v1/companion/control", headers={"Authorization": f"Bearer {main_key}"},
                         json={"recording": cur}, timeout=15)       # re-sends the CURRENT state: changes nothing
        say(PASS if r.status_code == 200 else FAIL, "the host's start/stop signal is accepted", f"HTTP {r.status_code}")
        online = [c for c in st["companions"] if c["seconds_since_seen"] < 60]
        say(PASS, f"{len(online)} teammate recorder(s) checking in right now" if online else "no teammate recorders online right now (expected before practice)")
    else:
        say(WARN, "no host key to test the signal with")

    print("\n[8] the host app's upload route")
    if main_key:
        r = requests.post(base + "/api/v1/sessions/upload", headers={"Authorization": f"Bearer {main_key}"},
                          files={"file": ("junk.r6session", b"not a package", "application/zip")}, timeout=30)
        say(PASS if r.status_code in (400, 422) else FAIL,
            "a bad package is rejected on its merits (the key, route and size limit all work)", f"HTTP {r.status_code}")

    print("\n[9] the companion stick")
    if comp_root.exists():
        cs = load_json(comp_root / "R6Companion" / "data" / "settings.json")
        if cs:
            say(PASS if cs.get("username") else FAIL, "a player name is set", str(cs.get("username", "")) or "MISSING")
            say(PASS if cs.get("mic_device") else WARN, "a microphone is chosen", str(cs.get("mic_device", "")) or "none chosen")
            obs_c = load_json(comp_root / "R6Companion" / "OBS-Studio" / "config" / "obs-studio" / "plugin_config" / "obs-websocket" / "config.json")
            if obs_c:
                say(PASS if int(cs.get("ws_port", 0)) == int(obs_c.get("server_port", -1)) and str(cs.get("ws_password")) == str(obs_c.get("server_password"))
                    else FAIL, "the companion and its own OBS agree on the websocket port and password")
            say(PASS if int(cs.get("ws_port", 4466)) != int((s or {}).get("obs_port", 4455)) else FAIL,
                "the companion's OBS port differs from the host's (both can run on one PC)")
        rec_dir = comp_root / "R6Companion" / "recordings"
        mp4 = list(rec_dir.glob("*.mp4")) if rec_dir.exists() else []
        exported = load_json(comp_root / "R6Companion" / "data" / "exported.json") or {}
        pending = [m.name for m in mp4 if m.name not in json.dumps(exported)]
        say(PASS if not pending else WARN, "no recordings waiting to be exported/uploaded" if not pending
            else f"{len(pending)} recording(s) not exported yet: they upload when the companion next starts")
        import shutil
        say(PASS if shutil.disk_usage(str(comp_root)).free / 1e9 > 10 else WARN, f"companion stick has {shutil.disk_usage(str(comp_root)).free / 1e9:.0f} GB free")
    else:
        say(WARN, "companion stick not plugged in: not checked")

    print("\n[10] the invite links teammates use")
    if main_key:
        rows = get(base + "/api/v1/invites", key=main_key).json()
        rows = rows.get("invites", rows)
        live = [r for r in rows if not r.get("revoked") and not r.get("expired")]
        say(PASS if live else WARN, f"{len(live)} working invite link(s): " + ", ".join(sorted(r["username"] for r in live)))
        stale = [r["username"] for r in live if str(r["username"]).lower().startswith("test")]
        if stale:
            say(WARN, "leftover test invite(s) still active", ", ".join(stale))
        if args.live_invite:
            print("     (live round trip with a throwaway invite, over the public address)")
            name = "Preflight_Test"
            inv = requests.post(base + "/api/v1/invites", headers={"Authorization": f"Bearer {main_key}"},
                                json={"username": name}, timeout=15).json()
            tok = inv["token"]
            try:
                r = get(base + "/api/v1/join/whoami", key=tok)
                say(PASS if r.status_code == 200 and r.json().get("username") == name else FAIL, "a new invite link identifies its player", f"HTTP {r.status_code}")
                dev = uuid.uuid4().hex[:16]
                r = requests.post(base + "/api/v1/companion/heartbeat", headers={"Authorization": f"Bearer {tok}"},
                                  json={"device_id": dev, "username": name, "status": {"started": False}}, timeout=15)
                say(PASS if r.status_code == 200 and "control" in r.json() else FAIL, "the page can check in and read the host's signal", f"HTTP {r.status_code}")
                wav = Path(f"preflight_{uuid.uuid4().hex[:6]}.wav")
                tone_wav(wav)
                try:
                    with wav.open("rb") as fh:
                        r = requests.post(base + "/api/v1/voice/chunks", headers={"Authorization": f"Bearer {tok}"},
                                          files={"file": ("chunk.wav", fh, "audio/wav")},
                                          data={"recording_id": uuid.uuid4().hex, "chunk_index": "0", "username": name,
                                                "start_epoch": f"{time.time():.3f}", "duration_sec": "2.0",
                                                "sample_rate": "16000", "is_final": "true"}, timeout=60)
                finally:
                    wav.unlink(missing_ok=True)
                say(PASS if r.status_code == 200 and r.json().get("status") == "stored" else FAIL, "an audio chunk uploads and is stored", f"HTTP {r.status_code}")
            finally:
                requests.delete(base + f"/api/v1/invites/{inv['invite_id']}", headers={"Authorization": f"Bearer {main_key}"}, timeout=15)
            r = get(base + "/api/v1/join/whoami", key=tok)
            say(PASS if r.status_code == 401 else FAIL, "the throwaway invite was revoked and no longer works", f"HTTP {r.status_code}")

    bad = [r for r in results if r[0] == FAIL]
    warn = [r for r in results if r[0] == WARN]
    print(f"\n{'ALL CLEAR' if not bad else 'PROBLEMS FOUND'}: {len(results) - len(bad) - len(warn)} passed, {len(warn)} warning(s), {len(bad)} failed")
    for lvl, what, detail in bad + warn:
        print(f"   {lvl}: {what}" + (f" ({detail})" if detail else ""))
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
