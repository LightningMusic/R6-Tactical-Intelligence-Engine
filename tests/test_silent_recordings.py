"""
2026-10-05/06: two teammates' recorders (a browser page and a companion stick) ran for over an hour and
uploaded only digital silence, and nothing said so. The server now measures every chunk when it arrives,
flags a silent recording, and tells the host app (which shows it in the recording log) and the dashboard.
"""
import sys
import types
from pathlib import Path

import numpy as np
import pytest
import soundfile as sf

from server.config import server_settings                       # noqa: F401  (fixture below patches it)
from tests.test_voice_comms_server import API, SR, VOICE, _h, _ogg, _upload, server   # noqa: F401
from tests.test_voice_align import talking

from app.companion_link import CompanionLink
from server.services.comms_service import CommsService

H = {"Authorization": f"Bearer {API}"}


def silence(seconds=5):
    return np.zeros(int(SR * seconds), dtype="float32")


def recordings(client):
    return client.get("/api/v1/voice/recordings", headers=H).json()["recordings"]


def test_a_chunk_of_zeros_is_stored_with_its_peak_and_the_recording_is_flagged_silent(server, tmp_path):
    client, db = server
    assert _upload(client, _ogg(tmp_path / "z.ogg", silence()), recording_id="1" * 32, username="Quiet_Player").status_code == 200
    r = recordings(client)[0]
    assert r["silent"] is True and r["peak"] == 0.0


def test_speech_is_not_flagged_and_its_peak_is_recorded(server, tmp_path):
    client, db = server
    assert _upload(client, _ogg(tmp_path / "t.ogg", talking(5, seed=3)), recording_id="2" * 32, username="Loud_Player").status_code == 200
    r = recordings(client)[0]
    assert r["silent"] is False and r["peak"] > 0.05


def test_one_loud_chunk_is_enough_to_say_the_recording_has_sound(server, tmp_path):
    client, db = server
    rid = "3" * 32
    _upload(client, _ogg(tmp_path / "a.ogg", silence()), recording_id=rid, chunk_index=0, username="Mixed_Player")
    _upload(client, _ogg(tmp_path / "b.ogg", talking(5, seed=4)), recording_id=rid, chunk_index=1, username="Mixed_Player")
    r = recordings(client)[0]
    assert r["silent"] is False and r["chunks"] == 2


def test_chunks_not_measured_yet_never_make_a_recording_silent(server, tmp_path):
    client, db = server
    rid = "4" * 32
    _upload(client, _ogg(tmp_path / "a.ogg", silence()), recording_id=rid, chunk_index=0, username="Old_Player")
    _upload(client, _ogg(tmp_path / "b.ogg", talking(5, seed=5)), recording_id=rid, chunk_index=1, username="Old_Player")
    with db.get_connection() as conn:                         # as if chunk 1 had been stored before loudness was measured
        conn.execute("UPDATE voice_chunks SET peak = NULL WHERE chunk_index = 1")
        conn.commit()
    r = [x for x in recordings(client) if x["recording_id"] == rid][0]
    assert r["silent"] is False and r["peak"] is None         # "not known yet", never "silent"


def test_old_chunks_get_measured_in_the_background(server, tmp_path):
    client, db = server
    _upload(client, _ogg(tmp_path / "a.ogg", talking(5, seed=6)), recording_id="5" * 32, username="Backfill_Player")
    with db.get_connection() as conn:
        conn.execute("UPDATE voice_chunks SET peak = NULL")
        conn.commit()
    assert CommsService.fill_missing_peaks() == 1
    r = [x for x in recordings(client) if x["username"] == "Backfill_Player"][0]
    assert r["peak"] > 0.05 and r["silent"] is False
    assert CommsService.fill_missing_peaks() == 0             # nothing left to measure


def test_a_file_that_cannot_be_decoded_is_unmeasured_not_silent(tmp_path):
    bad = tmp_path / "x.ogg"
    bad.write_bytes(b"not audio")
    assert CommsService.peak_of(bad) is None
    assert CommsService.peak_of(tmp_path / "missing.ogg") is None


def test_the_hosts_status_call_carries_each_players_last_recording(server, tmp_path):
    client, db = server
    _upload(client, _ogg(tmp_path / "z.ogg", silence()), recording_id="6" * 32, username="James_Mic", start_epoch=1790900000.0)
    _upload(client, _ogg(tmp_path / "t.ogg", talking(5, seed=7)), recording_id="7" * 32, username="Zander_Mic", start_epoch=1790900100.0)
    for name, dev in (("James_Mic", "dev1"), ("Zander_Mic", "dev2")):
        r = client.post("/api/v1/companion/heartbeat", headers={"Authorization": f"Bearer {VOICE}"},
                        json={"device_id": dev, "username": name, "status": {}})
        assert r.status_code == 200
    by = {c["username"]: c for c in client.get("/api/v1/companion/status", headers=H).json()["companions"]}
    assert by["James_Mic"]["last_recording"]["silent"] is True
    assert by["Zander_Mic"]["last_recording"]["silent"] is False
    assert by["James_Mic"]["last_recording"]["seconds"] == pytest.approx(300)


def test_the_host_app_log_says_a_silent_last_recording_was_silent():
    c = {"username": "Comp_User", "seconds_since_seen": 5, "status": {"obs": "ok", "recording": False},
         "last_recording": {"silent": True, "seconds": 6149, "start_epoch": 1791327592.0, "finished": 1}}
    line = CompanionLink.describe(c, {"comp_user": "Pat"})
    assert "Pat (Comp_User)" in line and "last recording" in line and "SILENT" in line
    assert "102 min" in line and "headset" in line


def test_while_a_recording_is_still_going_the_host_log_says_so_in_the_present_tense():
    c = {"username": "Comp_User", "seconds_since_seen": 5, "status": {"recording": True, "since": 0},
         "last_recording": {"silent": True, "seconds": 600, "start_epoch": 1791327592.0, "finished": 0}}
    line = CompanionLink.describe(c, now=900)
    assert "recording ✓" in line and "audio uploaded so far (10 min) is SILENT" in line and "last recording" not in line


def test_a_short_or_working_recording_adds_nothing_to_the_log_line():
    base = {"username": "Comp_User", "seconds_since_seen": 5, "status": {"obs": "ok", "recording": False}}
    ok = CompanionLink.describe({**base, "last_recording": {"silent": False, "seconds": 6000, "start_epoch": 1.0}})
    blip = CompanionLink.describe({**base, "last_recording": {"silent": True, "seconds": 20, "start_epoch": 1.0}})
    none = CompanionLink.describe(base)
    assert ok == blip == none == "Comp_User: not recording -- ok"


def test_a_silent_note_also_shows_while_the_companion_is_recording():
    c = {"username": "Comp_User", "seconds_since_seen": 5, "status": {"recording": True, "since": 0},
         "last_recording": {"silent": True, "seconds": 3000, "start_epoch": 1791327592.0, "finished": 1}}
    assert "SILENT" in CompanionLink.describe(c, now=600)
