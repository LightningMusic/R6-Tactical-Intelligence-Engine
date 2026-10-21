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
        {"name": "CCTV", "floor": "2F", "kind": "room", "x": 0, "y": 0, "z": 10},
        {"name": "CCTV", "floor": "2F", "x": 5, "y": 5, "z": 10},     # duplicate name dropped
        {"name": "", "floor": "2F", "x": 1, "y": 1, "z": 10},          # blank name dropped
        {"name": "Armory", "kind": "site", "x": 50, "y": 50, "z": 10},
        {"name": "Up", "kind": "nonsense", "x": 5, "y": 6, "z": 10}])   # unknown kind -> room
    assert saved == 3
    by = {r["name"]: r for r in match_db.get_rooms("Border")}
    assert set(by) == {"CCTV", "Armory", "Up"}
    assert by["Armory"]["kind"] == "site" and by["Up"]["kind"] == "room"
    # scatter now returns the saved rooms and no suggestions (only suggested when nothing saved)
    s = match_db.scatter("Border")
    assert {r["name"] for r in s["rooms"]} == {"CCTV", "Armory", "Up"} and s["suggested"] == []
    match_db.save_rooms("Border", [])
    assert match_db.get_rooms("Border") == []


def test_structures_returns_cached_walls_without_rebuilding(match_db):
    # Pre-fill the cache so structures() reads it instead of re-reading packages (which need the uploads store).
    with match_db._conn() as conn:
        match_db._ensure_table(conn)
        conn.execute("INSERT INTO map_structures (map, floor, reinforced, x, y, z) VALUES ('Border', '2F', 1, 1, 2, 10)")
        conn.execute("INSERT INTO map_structures (map, floor, reinforced, x, y, z) VALUES ('Border', '2F', 0, 3, 4, 10)")
        conn.commit()
    s = match_db.structures("Border")
    assert len(s["walls"]) == 2
    assert sum(w["reinforced"] for w in s["walls"]) == 1


def test_naming_an_unknown_map_relabels_its_matches(match_db):
    # The server parses a new map it can't name; naming it backfills the match and clears the flag.
    from database import game_catalog
    from server.match_db import ensure_match_database
    db = ensure_match_database()
    with db.get_connection() as conn:
        conn.execute("INSERT INTO matches (match_id, datetime, opponent_name, map, map_game_id) "
                     "VALUES (90, '2026-10-20T18:00', 'x', 'Unknown', 441408792952)")
        conn.execute("INSERT INTO unknown_maps (game_id, dissect_name, sites, first_seen, times_seen) "
                     "VALUES (441408792952, 'Map(441408792952)', '2F Server Room | B1 Kayaks', '2026-10-20', 1)")
        conn.commit()
    pending = game_catalog.pending_unknown_maps(db)
    assert any(p["game_id"] == 441408792952 for p in pending)
    final, n = game_catalog.name_unknown_map(db, 441408792952, "Kanal")
    assert final == "Kanal" and n == 1
    with db.get_connection() as conn:
        assert conn.execute("SELECT map FROM matches WHERE match_id = 90").fetchone()[0] == "Kanal"
    assert game_catalog.pending_unknown_maps(db) == [] or all(p["game_id"] != 441408792952 for p in game_catalog.pending_unknown_maps(db))


def test_maps_list_counts_rounds_and_labels(match_db):
    match_db.save_rooms("Border", [{"name": "CCTV", "floor": "2F", "x": 0, "y": 0, "z": 10}])
    maps = {m["map"]: m for m in match_db.maps_with_positions()}
    assert maps["Border"]["rounds"] == 1 and maps["Border"]["rooms"] == 1
