"""The host's answers on the review page decide who said a line when the timeline is rebuilt."""
from server.database import ServerDatabase
from server.services import comms_service as cs


def test_a_line_the_host_named_keeps_that_name(tmp_path, monkeypatch):
    db = ServerDatabase(tmp_path / "s.db")
    monkeypatch.setattr(cs, "server_db", db)
    sid = "session_x"
    speech = [{"speaker": "Zander", "username": "lammtozzz", "source": "team", "start": 1000.0, "end": 1002.0,
               "text": "As long as we just bum our site we're fine"},
              {"speaker": "James", "username": "Teddy_Dance", "source": "voice", "start": 1010.0, "end": 1011.5,
               "text": "one on stairs"},
              {"speaker": "Zander", "username": "lammtozzz", "source": "team", "start": 1020.0, "end": 1021.0,
               "text": "rotate rotate"}]
    timeline = {"rounds": [{"round_number": 6, "items": [
        {"kind": "speech", "t": 55.0, "speaker": "Zander", "text": "As long as we just bum our site we're fine"},
        {"kind": "speech", "t": 65.0, "speaker": "James", "text": "one on stairs"},
        {"kind": "speech", "t": 75.0, "speaker": "Zander", "text": "rotate rotate"}]}]}
    with db.get_connection() as conn:
        for key, heard in ((f"{sid}|6|55.00", "Carter"), (f"{sid}|6|65.00", "James"), (f"{sid}|6|75.00", "Can't tell")):
            conn.execute("INSERT INTO comms_review (line_key, session_id, category, claimed, heard, text_verdict, "
                         "line_text, answered_at) VALUES (?, ?, 'voice_id', '', ?, 'right', '', '')", (key, sid, heard))
        conn.commit()
    changed = cs.apply_review_answers(sid, timeline, speech, {"benten_910": "Carter", "lammtozzz": "Zander"})
    assert changed == 1
    assert (speech[0]["speaker"], speech[0]["username"], speech[0]["named_by"]) == ("Carter", "benten_910", "review")
    assert speech[1]["speaker"] == "James" and "named_by" not in speech[1]          # already right
    assert speech[2]["speaker"] == "Zander"                                          # "Can't tell" changes nothing
    assert cs.apply_review_answers("other_session", timeline, speech, {}) == 0
