"""
A recording is only deleted once every match from it is uploaded AND analysed by the server.
(2026-10-05: the app deleted a 5 GB recording on the same night every upload was being refused.)
"""
import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from app import recording_safety as rs

NOW = datetime(2026, 10, 20, 12, 0, 0).timestamp()         # "now", a local time, after every recording below


def make_recording(tmp_path: Path, name: str, hours: float, age_hours: float = 30.0) -> Path:
    """A recording that started at `name` (local time), ran `hours`, and last changed `age_hours` before NOW."""
    p = tmp_path / name
    p.write_bytes(b"video")
    start = rs.recording_start(p)
    end = min(start + hours * 3600, NOW - age_hours * 3600)
    os.utime(p, (end, end))
    return p


def match(made: datetime, status="uploaded", analysis="completed", error=None, sid="session_aaaaaaaaaaaa") -> dict:
    return {"session_id": sid, "created_at": made.astimezone(timezone.utc).isoformat(),
            "package_status": status, "remote_analysis_status": analysis, "last_error": error}


def local(y, mo, d, h, mi=0, s=0) -> datetime:
    return datetime(y, mo, d, h, mi, s)


def test_start_time_comes_from_the_file_name(tmp_path):
    p = tmp_path / "2026-10-05 16-22-33.mp4"
    assert rs.recording_start(p) == datetime(2026, 10, 5, 16, 22, 33).timestamp()
    assert rs.recording_start(tmp_path / "holiday video.mp4") is None
    assert rs.recording_start(tmp_path / "2026-13-45 99-99-99.mp4") is None


def test_a_recording_whose_matches_are_all_uploaded_and_analysed_may_go(tmp_path):
    rec = make_recording(tmp_path, "2026-10-01 17-34-15.mp4", hours=1.5)
    q = [match(local(2026, 10, 1, 18, 5)), match(local(2026, 10, 1, 18, 40), sid="session_bbbbbbbbbbbb")]
    assert rs.why_keep(rec, q, NOW) is None


def test_a_match_that_has_not_uploaded_keeps_the_recording_and_says_why(tmp_path):
    rec = make_recording(tmp_path, "2026-10-01 17-34-15.mp4", hours=1.5)
    q = [match(local(2026, 10, 1, 18, 5)),
         match(local(2026, 10, 1, 18, 40), status="upload_failed", analysis="none",
               error="Invalid or expired API token.", sid="session_bbbbbbbbbbbb")]
    why = rs.why_keep(rec, q, NOW)
    assert "has not uploaded yet" in why and "Invalid or expired API token." in why and "session_bbbbbbbb" in why


def test_uploaded_but_not_yet_analysed_also_keeps_it(tmp_path):
    rec = make_recording(tmp_path, "2026-10-01 17-34-15.mp4", hours=1.5)
    for analysis in ("processing", "queued", "none", "failed"):
        why = rs.why_keep(rec, [match(local(2026, 10, 1, 18, 5), analysis=analysis)], NOW)
        assert why and "not finished analysing" in why


def test_a_recording_with_no_match_on_record_is_kept(tmp_path):
    rec = make_recording(tmp_path, "2026-10-01 17-34-15.mp4", hours=1.5)
    assert "no match from it" in rs.why_keep(rec, [], NOW)
    # matches from other days do not count
    assert "no match from it" in rs.why_keep(rec, [match(local(2026, 9, 21, 18))], NOW)


def test_a_recent_recording_is_never_touched(tmp_path):
    rec = make_recording(tmp_path, "2026-10-20 08-00-00.mp4", hours=3, age_hours=1)
    assert "within the last few hours" in rs.why_keep(rec, [match(local(2026, 10, 20, 9))], NOW)


def test_a_file_name_that_isnt_a_timestamp_is_kept(tmp_path):
    p = tmp_path / "clip.mp4"
    p.write_bytes(b"v")
    os.utime(p, (NOW - 40 * 3600,) * 2)
    assert "start time" in rs.why_keep(p, [match(local(2026, 10, 1, 18))], NOW)


def test_a_match_queued_shortly_after_the_recording_stopped_still_belongs_to_it(tmp_path):
    rec = make_recording(tmp_path, "2026-10-01 17-34-15.mp4", hours=1.0)         # stops 18:34:15
    late = match(local(2026, 10, 1, 18, 34) + timedelta(minutes=20), status="upload_failed", analysis="none")
    assert rs.why_keep(rec, [late], NOW)                                           # counted, and not safe
    far = match(local(2026, 10, 1, 18, 34) + timedelta(hours=3), status="upload_failed", analysis="none")
    assert rs.why_keep(rec, [far], NOW) and "no match from it" in rs.why_keep(rec, [far], NOW)


def test_two_back_to_back_recordings_do_not_hide_each_others_unsent_matches(tmp_path):
    first = make_recording(tmp_path, "2026-10-05 16-22-33.mp4", hours=0.5)          # 16:22 - 16:52
    second = make_recording(tmp_path, "2026-10-05 16-55-02.mp4", hours=2.0)         # 16:55 - 18:55
    q = [match(local(2026, 10, 5, 16, 53, 45)),                                     # from the first: fine
         match(local(2026, 10, 5, 17, 30), status="upload_failed", analysis="none", sid="session_cccccccccccc")]
    assert rs.why_keep(first, q, NOW) is None
    assert "has not uploaded yet" in rs.why_keep(second, q, NOW)


def test_split_deletable_separates_the_two(tmp_path):
    ok = make_recording(tmp_path, "2026-09-28 19-43-49.mp4", hours=0.5)
    bad = make_recording(tmp_path, "2026-09-29 17-31-27.mp4", hours=2.0)
    q = [match(local(2026, 9, 28, 20, 5)),
         match(local(2026, 9, 29, 18, 55), status="upload_failed", analysis="none")]
    go, keep = rs.split_deletable([ok, bad], q, NOW)
    assert go == [ok] and [p for p, _ in keep] == [bad]


def test_load_queue_reads_the_real_queue_file_shape(tmp_path):
    f = tmp_path / "queue.json"
    f.write_text(json.dumps({"s1": {"session_id": "s1", "package_status": "uploaded"}, "junk": 5}), encoding="utf-8")
    assert [i["session_id"] for i in rs.load_queue(f)] == ["s1"]
    assert rs.load_queue(tmp_path / "missing.json") == []
    f.write_text("{not json", encoding="utf-8")
    assert rs.load_queue(f) == []


def test_the_automatic_cleanup_keeps_what_has_not_uploaded(tmp_path, monkeypatch):
    import sys
    import types

    import app.config as config
    # session_manager imports the Discord recorder at module level; the cleanup needs none of it.
    monkeypatch.setitem(sys.modules, "integration.discord_capture", types.SimpleNamespace(DiscordCapture=object))
    from app.session_manager import SessionManager

    rec_dir, data_dir = tmp_path / "recordings", tmp_path / "data"
    (data_dir / "queue").mkdir(parents=True)
    rec_dir.mkdir()
    monkeypatch.setattr(config, "RECORDINGS_DIR", rec_dir)
    monkeypatch.setattr(config, "DATA_DIR", data_dir)
    # five recordings from last year (so all are old by any clock), one hour long each
    names = ["2025-09-21 17-00-00.mp4", "2025-09-22 17-00-00.mp4", "2025-09-23 17-00-00.mp4",
             "2025-09-24 17-00-00.mp4", "2025-09-25 17-00-00.mp4"]
    queue = {}
    for i, n in enumerate(names):
        p = rec_dir / n
        p.write_bytes(b"video")
        start = rs.recording_start(p)
        os.utime(p, (start + 3600, start + 3600))
        if i < 2:        # the two oldest have a match on record: the first uploaded, the second never did
            queue[f"s{i}"] = {"session_id": f"s{i}", "created_at": datetime.fromtimestamp(start + 1800, timezone.utc).isoformat(),
                              "package_status": "uploaded" if i == 0 else "upload_failed",
                              "remote_analysis_status": "completed" if i == 0 else "none", "last_error": None}
    (data_dir / "queue" / "queue.json").write_text(json.dumps(queue), encoding="utf-8")

    mgr = SessionManager.__new__(SessionManager)
    logs = []
    deleted = mgr.cleanup_old_recordings(keep_latest_n=3, log_callback=logs.append)
    left = sorted(p.name for p in rec_dir.glob("*.mp4"))
    assert deleted == 1
    assert names[0] not in left                                   # uploaded + analysed: gone
    assert names[1] in left                                       # its match never uploaded: kept
    assert all(n in left for n in names[2:])                      # the newest three are always spared
    assert any("Kept 2025-09-22 17-00-00.mp4" in line and "has not uploaded yet" in line for line in logs)
