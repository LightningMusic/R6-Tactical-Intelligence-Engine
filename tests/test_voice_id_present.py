"""A learned voice may only name a Discord line in a match its owner actually played (on our side)."""
from server.services.comms_service import voice_id_exclusions

PROFILES = {"Zed_Player": [0.1], "Will_Player": [0.2]}


def test_someone_who_sat_the_match_out_is_never_named():
    rounds = [{"round": 1, "ours": ["Host", "Will_Player", "A", "B", "C"]}]
    assert voice_id_exclusions(set(), rounds, PROFILES) == {"Zed_Player"}


def test_a_player_with_their_own_aligned_recording_stays_excluded_too():
    rounds = [{"round": 1, "ours": ["host", "zed_player", "will_player"]}]       # names compared case-insensitively
    assert voice_id_exclusions({"Zed_Player"}, rounds, PROFILES) == {"Zed_Player"}


def test_without_a_known_lineup_nothing_extra_is_excluded():
    assert voice_id_exclusions({"X"}, [{"round": 1}], PROFILES) == {"X"}
    assert voice_id_exclusions(set(), [], PROFILES) == set()
