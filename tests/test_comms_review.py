"""Listen and mark (server/services/comms_review.py): which lines are offered, the clip played for each, and what
the answers add up to."""
import io
import json
import wave

import numpy as np
import pytest

from analysis.comms_timeline import UNKNOWN_TEAM_SPEAKER
from server.config import server_settings

SID = "session_review_test"
CLIP_START = 1000.0                       # the host's recording starts at this epoch
ROUND_START = 1010.0


def write_wav(path, tone_from, tone_to, seconds=60, sr=16000):
    t = np.arange(seconds * sr) / sr
    x = np.where((t >= tone_from) & (t < tone_to), 0.5 * np.sin(2 * np.pi * 440 * t), 0.0)
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1); w.setsampwidth(2); w.setframerate(sr)
        w.writeframes((x * 32767).astype("<i2").tobytes())


def speech(t, end, speaker, source, text):
    return {"t": t, "end": end, "clock": "", "kind": "speech", "speaker": speaker, "source": source, "text": text}


@pytest.fixture
def review(tmp_path, monkeypatch):
    d = tmp_path / "server_data"
    for attr, path in (("DATA_DIR", d), ("DATABASE_PATH", d / "server_matches.db"), ("COMMS_DIR", d / "comms")):
        monkeypatch.setattr(server_settings, attr, path)
    from server.database import ServerDatabase
    db = ServerDatabase(d / "server_matches.db")
    monkeypatch.setattr("server.services.comms_review.server_db", db)
    monkeypatch.setattr("server.roster.server_db", db)
    items = [speech(5, 7, "Alex", "self", "push the stairs now"),           # the host's own mic
             speech(10, 12, "Sam", "self", "one in the corner"),           # host's mic, credited to the teammate beside him
             speech(15, 17, "Sam", "voice", "going hatch"),                 # the teammate's own track
             speech(20, 22, "Kim", "team", "rotate to kitchen"),            # named by voice
             speech(25, 27, UNKNOWN_TEAM_SPEAKER, "team", "he's low hp"),   # nobody could name it
             speech(30, 30.3, UNKNOWN_TEAM_SPEAKER, "team", "uh")]          # too short to judge: never offered
    timeline = {"rounds": [{"round_number": 3, "start_epoch": ROUND_START, "items": items}]}
    meta = {"pieces": [[CLIP_START, 60.0]], "self_username": "HostGuy", "roster": {"hostguy": "Alex"}}
    with db.get_connection() as conn:
        conn.execute("INSERT INTO session_comms (session_id, audio_meta_json, timeline_json, window_start, updated_at) "
                     "VALUES (?, ?, ?, ?, 'now')", (SID, json.dumps(meta), json.dumps(timeline), CLIP_START))
        conn.execute("INSERT INTO team_roster (username, label, position) VALUES ('HostGuy', 'Alex', 0), ('SamIGN', 'Sam', 1)")
        conn.commit()
    comms = d / "comms" / SID
    write_wav(comms / "team.wav", 30.0, 32.0)        # the "Kim" line: round start 1010 + 20 s = 30 s into the clip
    write_wav(comms / "self.wav", 15.0, 17.0)        # the host's line: 1010 + 5 s = 15 s into the clip
    from server.services import comms_review
    return comms_review


def test_every_way_of_naming_a_line_is_offered_and_short_lines_are_not(review):
    s = review.sample(n=10, seed=1)
    cats = sorted(x["category"] for x in s["items"])
    assert cats == ["bleed", "own_track", "unassigned", "voice_id", "your_mic"]
    unnamed = next(x for x in s["items"] if x["category"] == "unassigned")
    assert unnamed["claimed"] == "" and unnamed["text"] == "he's low hp"
    assert s["choices"][:2] == ["Alex", "Sam"] and "Kim" in s["choices"]
    assert s["choices"][-2:] == [review.SOMEONE_ELSE, review.CANT_TELL]


def level(wav_bytes, a, b, sr=16000):
    with wave.open(io.BytesIO(wav_bytes)) as w:
        x = np.frombuffer(w.readframes(w.getnframes()), dtype="<i2") / 32768
    return float(np.abs(x[int(a * sr):int(b * sr)]).max()) if len(x) > int(a * sr) else 0.0, len(x) / sr


def test_the_clip_is_cut_from_the_right_track_at_the_right_moment(review):
    key = f"{SID}|3|20.00"                                     # "Kim", a Discord line
    data = review.clip(key)
    peak, dur = level(data, 0.5, 2.3)
    assert abs(dur - 2.8) < 0.05 and peak > 0.3               # the line plus 0.4 s either side, and the tone is in it
    with wave.open(io.BytesIO(data)) as w:                    # and the header says how long it really is
        assert abs(w.getnframes() / 16000 - 2.8) < 0.05
    peak, dur = level(review.clip(f"{SID}|3|5.00"), 0.5, 2.3)  # the host's own line plays from his mic's track
    assert peak > 0.3
    peak, _ = level(review.clip(f"{SID}|3|25.00"), 0.0, 2.8)   # nothing was said there on the Discord track here
    assert peak < 0.01
    assert review.clip(f"{SID}|3|99.00") is None and review.clip("nonsense") is None


def test_answers_add_up_per_way_of_naming(review):
    review.record(f"{SID}|3|20.00", "Kim", "right")            # voice ID right
    review.record(f"{SID}|3|15.00", "Alex", "mostly")          # own track: really the host (lined up wrong)
    review.record(f"{SID}|3|25.00", "Sam", "wrong")            # unnamed: it was Sam
    review.record(f"{SID}|3|5.00", review.CANT_TELL, "cant_hear")
    s = review.summary()
    assert s["answered"] == 4
    assert s["categories"]["voice_id"]["accuracy"] == 1.0
    assert s["categories"]["own_track"]["accuracy"] == 0.0
    assert s["categories"]["your_mic"]["judged"] == 0          # "can't tell" is not counted either way
    assert s["categories"]["unassigned"]["really"] == {"Sam": 1}
    assert s["named_accuracy"] == 0.5 and s["named_judged"] == 2
    assert s["mixups"] == [{"named": "Sam", "really": "Alex", "times": 1}]
    assert s["text_right_or_mostly"] == round(2 / 3, 3)
    left = {x["key"] for x in review.sample(n=10, seed=2)["items"]}
    assert left == {f"{SID}|3|10.00"}                          # only the one nobody has marked


def test_lines_with_nothing_to_play_them_from_are_not_offered(review, monkeypatch):
    (server_settings.COMMS_DIR / SID / "self.wav").unlink()           # an older session: no copy of the host's mic
    monkeypatch.setattr(review, "_TRACKS", {})
    monkeypatch.setattr("server.services.utility_backfill.package_path", lambda sid: None)
    cats = {x["category"] for x in review.sample(n=10, seed=1)["items"]}
    assert "your_mic" not in cats and "bleed" not in cats and "voice_id" in cats


def test_an_identified_discord_line_teaches_that_persons_voice(review, monkeypatch):
    from server import voice_id
    monkeypatch.setattr("server.voice_id.server_db", review.server_db)
    voice_id.set_encoder(lambda wav: [1.0, 0.0, 0.0] if len(wav) else None)
    try:
        out = review.record(f"{SID}|3|20.00", "Sam", "right")     # a Discord line, said by Sam (nickname)
        assert out["learned"] == {"SamIGN": 1}
        review.record(f"{SID}|3|5.00", "Alex", "right")           # the host's own mic: not a Discord voice sample
        assert review.learn_voices() == {}                         # and nothing is learned twice
        prof = {p["name"]: p for p in review.summary()["profiles"]}
        assert prof["Sam"]["samples"] == 1 and prof["Sam"]["needs"] == voice_id.MIN_PROFILE_SAMPLES - 1
        assert prof["Alex"]["samples"] == 0
    finally:
        voice_id.set_encoder(None)


def test_a_bad_answer_is_refused(review):
    with pytest.raises(ValueError):
        review.record(f"{SID}|3|20.00", "Kim", "great")
    with pytest.raises(ValueError):
        review.record(f"{SID}|3|20.00", "", "right")
    with pytest.raises(ValueError):
        review.record(f"{SID}|3|77.00", "Kim", "right")


def test_the_page_is_served_and_the_data_needs_the_key():
    from fastapi.testclient import TestClient
    from server.main import app
    with TestClient(app) as c:
        assert "Check the comms" in c.get("/review").text
        assert c.get("/api/v1/review/comms/sample").status_code == 401
        assert c.get("/api/v1/review/comms/summary").status_code == 401
