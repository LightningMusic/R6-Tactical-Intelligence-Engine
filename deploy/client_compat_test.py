"""Checks that the USB client, R6Companion and R6Voice work against a RUNNING server.

    python deploy/client_compat_test.py --base https://r6-server.<tailnet>.ts.net [--package file.r6session]
                                        [--client-settings F:\\R6Analyzer\\data\\settings.json]

It drives the real client classes (app.uploader.SessionUploader, app.companion_link.CompanionLink,
voice_recorder.uploader.Uploader) with the same keys the deployed apps carry: the API key from the
USB client's own settings.json, and the voice key from the server's config. Keys are never printed.
It leaves a "Compat_Test" companion and voice recording on the server (delete them after, or run it
before a data migration, which replaces them).
"""
import argparse
import json
import sys
import tempfile
import time
import uuid
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parent.parent
sys.path[:0] = [str(ROOT), str(ROOT / "scripts"), str(ROOT / "deploy"), str(ROOT / "voice_recorder")]

FAILS = []


def check(ok, what):
    print(("  PASS  " if ok else "  FAIL  ") + what, flush=True)
    if not ok:
        FAILS.append(what)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", required=True)
    ap.add_argument("--package", default="")
    ap.add_argument("--client-settings", default=r"F:\R6Analyzer\data\settings.json")
    a = ap.parse_args()
    base = a.base.rstrip("/")

    api_key = json.loads(Path(a.client_settings).read_text(encoding="utf-8"))["api_key"]
    from sync_deployed_server_config import _read_docker_config
    voice_key = _read_docker_config()["voice_token"]

    from app.config import settings
    settings._data["server_url"] = base
    settings._data["api_key"] = api_key
    from app.uploader import SessionUploader
    from app.companion_link import CompanionLink

    print("[1] USB client: connection test, upload, status")
    up = SessionUploader()
    r = up.test_connection()
    check(r.success, f"Test Connection with the client's own key ({r.status_code} {r.error or ''})")
    settings._data["api_key"] = api_key + "x"
    check(not up.test_connection().success, "a wrong key is refused")
    settings._data["api_key"] = api_key
    if a.package:
        r = up.upload_package(Path(a.package))
        check(r.success and bool(r.session_id), f"package upload accepted (duplicate={r.is_duplicate}, status={r.status}, {r.error or ''})")
        if r.session_id:
            s = up.get_status(r.session_id)
            check(s.success, f"session status readable (status={s.status})")

    print("[2] USB client -> companions (Start/Stop the team)")
    link = CompanionLink()
    before = requests.get(f"{base}/api/v1/companion/status", headers={"Authorization": f"Bearer {api_key}"}, timeout=15).json()
    was_on = bool((before.get("control") or {}).get("recording"))
    check(link.status() is not None, "companion status readable")
    check(link.set_recording(True), "team recording switched on")
    check(link.set_recording(was_on), f"team recording restored to its previous state ({'on' if was_on else 'off'})")

    print("[3] R6Companion: heartbeat with the voice key")
    dev = uuid.uuid4().hex
    hb = {"Authorization": f"Bearer {voice_key}"}
    r = requests.post(f"{base}/api/v1/companion/heartbeat", headers=hb, timeout=15,
                      json={"device_id": dev, "username": "Compat_Test", "status": {"recording": False, "obs": "compat test"}})
    check(r.status_code == 200 and "control" in r.json(), f"heartbeat accepted and answered with the team state ({r.status_code})")
    check(requests.post(f"{base}/api/v1/companion/heartbeat", headers={"Authorization": "Bearer nope"}, timeout=15,
                        json={"device_id": dev, "username": "Compat_Test", "status": {}}).status_code in (401, 403),
          "heartbeat with a wrong key is refused")
    seen = [c for c in link.status().get("companions", []) if c.get("device_id") == dev]
    check(bool(seen), "the host's recording log can see the companion")

    print("[4] R6Companion / R6Voice: ping and a chunk upload")
    from smoke_test import tone_wav
    from uploader import Uploader
    with tempfile.TemporaryDirectory() as tmp:
        cfg = {"server_url": base, "voice_token": voice_key}
        u = Uploader(Path(tmp), lambda: cfg)
        check(u.ping(), f"ping ({u.last_error or 'ok'})")
        wav = Path(tmp) / "compat.wav"
        wav.write_bytes(tone_wav())
        u.enqueue_file(wav, recording_id=uuid.uuid4().hex, chunk_index=0, username="Compat_Test",
                       start_epoch=time.time() - 3, duration_sec=3.0, is_final=True)
        deadline = time.time() + 90
        while time.time() < deadline and u.waiting():
            time.sleep(1)
        check(u.waiting() == 0 and u.sent == 1 and not (Path(tmp) / "rejected").exists(),
              f"chunk accepted by the server ({u.last_error or 'sent'})")
        u.stop()

    print(f"\n{'ALL CHECKS PASSED' if not FAILS else str(len(FAILS)) + ' CHECK(S) FAILED'}")
    return 1 if FAILS else 0


if __name__ == "__main__":
    sys.exit(main())
