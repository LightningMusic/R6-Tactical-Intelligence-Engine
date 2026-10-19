"""Room (callout) labels per map: the scatter the labelling page draws, saving/loading pins, and naming a
position by its nearest room."""
import pytest

from integration.positions import encode
from server.config import server_settings


def _positions(floor_z, pts_by_player):
    # Hold each player on their last spot through the end of prep (t up to 46s) so places.floors can map the
    # site's "2F" prefix to this level from where the defenders stand at setup time.
    def track(ps):
        pts = [[float(t), x, y, floor_z, None] for t, (x, y) in enumerate(ps)]
        lx, ly = ps[-1]
        pts += [[float(t), lx, ly, floor_z, None] for t in range(len(ps), 47)]
        return pts
    return {"v": 1, "hz": 2.0, "def_team": 1, "linked": True, "died": {},
            "players": {n: {"team": 1, "pts": track(ps)} for n, ps in pts_by_player.items()}}


@pytest.fixture
def match_db(tmp_path, monkeypatch):
    monkeypatch.setattr(server_settings, "MATCHES_DB_PATH", tmp_path / "matches.db")
    import importlib
    import server.match_db as mdb
    importlib.reload(mdb)
    import server.services.rooms as rooms
    importlib.reload(rooms)
    db = mdb.ensure_match_database()
    with db.get_connection() as conn:
        conn.execute("INSERT INTO matches (match_id, datetime, opponent_name, map) VALUES (1, '2026-10-19T12:00', 'x', 'Border')")
        conn.execute("INSERT INTO rounds (match_id, round_number, side, site, outcome) VALUES (1, 1, 'defense', '2F Armory Lockers, 2F Archives', 'win')")
        # two clusters of movement on one floor (z=10): around (0,0) and around (50,50)
        blob = encode(_positions(10.0, {"A": [(0, 0), (1, 1), (0, 1)], "B": [(50, 50), (51, 50), (50, 51)],
                                        "C": [(2, 2), (2, 3)]}))     # 3 defenders so the floor's "2F" vote sticks
        conn.execute("INSERT INTO derived_metrics (match_id, metric_name, metric_value, metric_text) VALUES (1, 'round_1_positions', 0, ?)", (blob,))
        conn.commit()
    return rooms


def test_room_of_picks_the_nearest_room_on_the_same_floor():
    from server.services.rooms import room_of
    rooms = [{"name": "CCTV", "floor": "2F", "x": 0, "y": 0, "z": 10},
             {"name": "Armory", "floor": "2F", "x": 50, "y": 50, "z": 10},
             {"name": "Basement", "floor": "B", "x": 0, "y": 0, "z": 0}]
    assert room_of(rooms, 1, 1, 10) == "CCTV"
    assert room_of(rooms, 49, 50, 10) == "Armory"
    assert room_of(rooms, 0, 0, 0) == "Basement"        # same spot, different floor -> the basement room
    assert room_of(rooms, 200, 200, 10) is None          # nothing within range


def test_scatter_gives_points_floors_and_a_site_suggestion(match_db):
    s = match_db.scatter("Border")
    assert s["bounds"]["minx"] == 0 and s["bounds"]["maxx"] == 50       # both clusters present (one sample each)
    assert s["points"] and all(len(p) == 3 for p in s["points"])
    assert any(abs(p[0] - 50) < 1 and abs(p[1] - 50) < 1 for p in s["points"])
    assert [f["name"] for f in s["floors"]] == ["2F"]
    assert any(x["name"] == "Armory Lockers" for x in s["suggested"])   # from the site string
    assert match_db.scatter("Chalet")["points"] == []                   # a map with no data


def test_saving_and_loading_rooms(match_db):
    saved = match_db.save_rooms("Border", [
        {"name": "CCTV", "floor": "2F", "x": 0, "y": 0, "z": 10},
        {"name": "CCTV", "floor": "2F", "x": 5, "y": 5, "z": 10},     # duplicate name dropped
        {"name": "", "floor": "2F", "x": 1, "y": 1, "z": 10},          # blank name dropped
        {"name": "Armory", "x": 50, "y": 50, "z": 10}])
    assert saved == 2
    got = {r["name"] for r in match_db.get_rooms("Border")}
    assert got == {"CCTV", "Armory"}
    # scatter now returns the saved rooms and no suggestions (only suggested when nothing saved)
    s = match_db.scatter("Border")
    assert {r["name"] for r in s["rooms"]} == {"CCTV", "Armory"} and s["suggested"] == []
    match_db.save_rooms("Border", [])
    assert match_db.get_rooms("Border") == []


def test_maps_list_counts_rounds_and_labels(match_db):
    match_db.save_rooms("Border", [{"name": "CCTV", "floor": "2F", "x": 0, "y": 0, "z": 10}])
    maps = {m["map"]: m for m in match_db.maps_with_positions()}
    assert maps["Border"]["rounds"] == 1 and maps["Border"]["rooms"] == 1
