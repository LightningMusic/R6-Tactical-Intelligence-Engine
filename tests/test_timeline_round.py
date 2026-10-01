"""RecImporter.timeline_round: what the comms timeline takes from one round's
r6-dissect JSON -- our team (from the recording player), and kill-feed events
with elapsedSeconds (added to our r6-dissect fork on 2026-09-30)."""
from integration.rec_importer import RecImporter

ROUND = {
    "timestamp": "2026-09-29T18:49:32Z",
    "recordingPlayerID": 111,
    "players": [
        {"id": 111, "username": "LightningMusic6", "teamIndex": 0},
        {"id": 112, "username": "lammtozzz", "teamIndex": 0},
        {"id": 201, "username": "YABO1HAM", "teamIndex": 1},
    ],
    "matchFeedback": [
        {"type": {"id": 9, "name": "OperatorSwap"}, "username": "lammtozzz", "time": "0:44", "elapsedSeconds": 0},
        {"type": {"id": 0, "name": "Kill"}, "username": "YABO1HAM", "target": "lammtozzz", "headshot": True,
         "time": "1:17", "timeInSeconds": 77, "elapsedSeconds": 147},
        {"type": {"id": 3, "name": "DefuserPlantComplete"}, "username": "LightningMusic6", "time": "0:25",
         "elapsedSeconds": 199},
    ],
}


def test_timeline_round():
    r = RecImporter.timeline_round(ROUND, 1)
    assert r["round_number"] == 1 and r["timestamp"] == "2026-09-29T18:49:32Z"
    assert r["recording_username"] == "LightningMusic6"
    assert r["ours"] == ["LightningMusic6", "lammtozzz"] and r["theirs"] == ["YABO1HAM"]
    assert [(e["type"], e["elapsed"]) for e in r["events"]] == [("Kill", 147.0), ("DefuserPlantComplete", 199.0)]
    assert r["events"][0]["target"] == "lammtozzz" and r["events"][0]["headshot"] is True


def test_timeline_round_without_the_recorder_still_lists_events():
    data = dict(ROUND, recordingPlayerID=999)
    r = RecImporter.timeline_round(data, 2)
    assert r["ours"] == [] and r["recording_username"] == ""
    assert len(r["events"]) == 2
