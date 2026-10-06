"""
Which player's replay is this? On 2026-10-05, rounds 1-3 of a match came out as "0 ours, 10 theirs"
(no kill feed, no objective data) because the numeric player id carried a stray value for every
player; the Ubisoft profile id was right in every round.
"""
from pathlib import Path

import pytest

from integration.rec_importer import RecImporter

STRAY = 6366317606386794496            # the value every player carried in the bad rounds


def players(ids=None, profiles=True):
    ids = ids or {}
    rows = [("Me", 0), ("Mate", 0), ("Foe1", 1), ("Foe2", 1)]
    return [{"username": n, "teamIndex": t, "id": ids.get(n, i + 1),
             **({"profileID": f"profile-{n}"} if profiles else {}), "operator": {"name": "Ash"}}
            for i, (n, t) in enumerate(rows)]


def data(**extra):
    return {"players": players(), **extra}


def test_the_profile_id_finds_the_recorder_even_when_the_numeric_ids_are_stray():
    d = {"players": players({n: STRAY for n in ("Me", "Mate", "Foe1", "Foe2")}),
         "recordingPlayerID": 866496839743833217, "recordingProfileID": "profile-Me"}
    assert RecImporter.find_recorder(d)["username"] == "Me"


def test_the_numeric_id_is_the_fallback_when_there_is_no_profile_id():
    d = {"players": players(profiles=False), "recordingPlayerID": 2}
    assert RecImporter.find_recorder(d)["username"] == "Mate"


def test_a_numeric_id_shared_by_several_players_decides_nothing():
    d = {"players": players({n: STRAY for n in ("Me", "Mate", "Foe1", "Foe2")}, profiles=False),
         "recordingPlayerID": STRAY}
    assert RecImporter.find_recorder(d) is None


def test_the_profile_id_wins_over_a_numeric_id_that_points_at_someone_else():
    d = {"players": players(), "recordingPlayerID": 3, "recordingProfileID": "profile-Mate"}
    assert RecImporter.find_recorder(d)["username"] == "Mate"


def test_a_replay_that_names_nobody_gives_none_rather_than_a_guess():
    assert RecImporter.find_recorder({"players": players()}) is None
    assert RecImporter.find_recorder({"players": players(), "recordingProfileID": "nobody", "recordingPlayerID": 0}) is None
    assert RecImporter.find_recorder({}) is None


@pytest.fixture
def importer():
    return RecImporter(dissect_path=Path(__file__), log_callback=lambda msg: None)


def test_a_round_with_stray_ids_still_knows_which_team_is_ours(importer):
    d = {
        "roundNumber": 0, "site": "2F Office", "map": {"id": 1},
        "recordingPlayerID": 866496839743833217, "recordingProfileID": "profile-Me",
        "teams": [{"role": "Attack", "score": 1, "startingScore": 0, "won": True},
                  {"role": "Defense", "score": 0, "startingScore": 0, "won": False}],
        "players": players({n: STRAY for n in ("Me", "Mate", "Foe1", "Foe2")}),
        "matchFeedback": [], "stats": [],
    }
    round_obj, _ = importer._parse_round(d)
    ours = sorted(p["username"] for p in round_obj.raw_player_stats if p["is_our_team"])
    assert ours == ["Mate", "Me"]
    assert round_obj.side == "attack" and round_obj.outcome == "win"
    assert round_obj.round_events is not None and round_obj.round_events.our_role == "attack"


def test_the_comms_timeline_also_finds_our_team_with_stray_ids():
    d = {"roundNumber": 0, "players": players({n: STRAY for n in ("Me", "Mate", "Foe1", "Foe2")}),
         "recordingPlayerID": 866496839743833217, "recordingProfileID": "profile-Me",
         "teams": [{"role": "Attack"}, {"role": "Defense"}], "matchFeedback": []}
    out = RecImporter.timeline_round(d, 1)
    assert sorted(out["ours"]) == ["Mate", "Me"]
