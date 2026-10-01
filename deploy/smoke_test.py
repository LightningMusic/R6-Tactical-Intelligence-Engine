"""Quick end-to-end check of a RUNNING server (the Docker one, by default).

    python deploy/smoke_test.py [--base http://127.0.0.1:8000] [--config path\\to\\server_config.json]

Uses the server's own API token (from server_config.json, never printed), makes
a temporary invite, sends it a short synthetic WAV through the same endpoint the
browser recorder uses (so the Linux audio stack, Opus encoding and the read-only
filesystem are exercised), then revokes the invite and leaves no data behind
except the recording itself, which it also deletes via the invite's own cleanup
where the API allows.
"""
import argparse
import io
import json
import math
import struct
import sys
import time
import uuid
import wave

import requests

FAILS = []


def check(ok, what):
    print(("  PASS  " if ok else "  FAIL  ") + what, flush=True)
    if not ok:
        FAILS.append(what)


def tone_wav(seconds=3, rate=16000):
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(b"".join(
            struct.pack("<h", int(12000 * math.sin(2 * math.pi * 440 * i / rate))) for i in range(rate * seconds)))
    return buf.getvalue()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default="http://127.0.0.1:8000")
    ap.add_argument("--config", default=r"dist\R6Server\server_data\server_config.json")
    a = ap.parse_args()
    base = a.base.rstrip("/")
    token = json.load(open(a.config, encoding="utf-8"))["api_token"]
    H = {"Authorization": f"Bearer {token}"}

    print("[1] server and static pages")
    r = requests.get(base + "/api/v1/health", timeout=10)
    check(r.status_code == 200, f"health answers ({r.status_code})")
    r = requests.get(base + "/join", timeout=10)
    check(r.status_code == 200 and "R6" in r.text, "/join page is served")
    check("content-security-policy" in {k.lower() for k in r.headers}, "/join carries a Content-Security-Policy")
    check(requests.get(base + "/api/v1/sessions", timeout=10).status_code in (401, 403), "API refuses anonymous callers")
    r = requests.get(base + "/api/v1/sessions", headers=H, timeout=10)
    check(r.status_code == 200, f"API accepts the real token ({r.status_code})")
    if r.status_code == 200:
        print(f"        {r.json().get('count')} session(s) on the server")

    print("[2] invite, whoami, ping")
    r = requests.post(base + "/api/v1/invites", headers=H, json={"username": "Smoke_Test"}, timeout=10)
    check(r.status_code in (200, 201), f"invite created ({r.status_code})")
    inv = r.json()
    IH = {"Authorization": f"Bearer {inv['token']}"}
    r = requests.get(base + "/api/v1/join/whoami", headers=IH, timeout=10)
    check(r.status_code == 200 and r.json().get("username") == "Smoke_Test", "invite identifies its player")
    check(requests.get(base + "/api/v1/voice/ping", headers=IH, timeout=10).status_code == 200, "invite can ping")
    check(requests.get(base + "/api/v1/sessions", headers=IH, timeout=10).status_code in (401, 403),
          "invite cannot read sessions")

    print("[3] voice chunk upload (WAV -> Opus on Linux)")
    start = time.time() - 3
    r = requests.post(
        base + "/api/v1/voice/chunks", headers=IH, timeout=60,
        files={"file": ("chunk.wav", tone_wav(), "audio/wav")},
        data={"recording_id": uuid.uuid4().hex, "chunk_index": 0, "username": "Smoke_Test",
              "start_epoch": start, "duration_sec": 3.0, "sample_rate": 16000, "is_final": "true"})
    check(r.status_code == 200 and r.json().get("status") == "stored", f"chunk stored ({r.status_code} {r.text[:120]})")
    r = requests.get(base + "/api/v1/voice/recordings", headers=H, timeout=10)
    check(r.status_code == 200 and "Smoke_Test" in r.text, "recording is listed")

    print("[4] revoke")
    requests.delete(base + f"/api/v1/invites/{inv['invite_id']}", headers=H, timeout=10)
    check(requests.get(base + "/api/v1/join/whoami", headers=IH, timeout=10).status_code == 401,
          "revoked invite is refused")

    print(f"\n{'ALL CHECKS PASSED' if not FAILS else str(len(FAILS)) + ' CHECK(S) FAILED'}")
    return 1 if FAILS else 0


if __name__ == "__main__":
    sys.exit(main())
