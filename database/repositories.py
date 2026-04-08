from typing import List, Optional
from datetime import datetime
from models.map import Map
from models.player import Player
from database.db_manager import DatabaseManager
from models.match import Match
from models.round import Round
from models.round_resources import RoundResources
from models.player_round_stats import PlayerRoundStats
from models.operator import Operator
from models.gadget import Gadget
from datetime import datetime


class Repository:
    """
    Central data access layer for the R6 Tactical Intelligence Engine.
    """

    def __init__(self):
        self.db = DatabaseManager()

    # =====================================================
    # Operators
    # =====================================================

    def get_all_operators(self) -> List[Operator]:
        with self.db.get_connection() as conn:
            rows = conn.execute("SELECT * FROM operators").fetchall()
            return [
                Operator(
                    operator_id=row["operator_id"],
                    name=row["name"],
                    side=row["side"],
                    ability_name=row["ability_name"],
                    ability_max_count=row["ability_max_count"],
                )
                for row in rows
            ]

    def get_operator_by_id(self, operator_id: int) -> Optional[Operator]:
        with self.db.get_connection() as conn:
            row = conn.execute(
                "SELECT * FROM operators WHERE operator_id = ?",
                (operator_id,),
            ).fetchone()

            if not row:
                return None

            return Operator(
                operator_id=row["operator_id"],
                name=row["name"],
                side=row["side"],
                ability_name=row["ability_name"],
                ability_max_count=row["ability_max_count"],
            )

    # =====================================================
    # Gadgets
    # =====================================================

    def get_gadgets_for_operator(self, operator_id: int) -> List[Gadget]:
        with self.db.get_connection() as conn:
            rows = conn.execute(
                """
                SELECT g.*, ogo.max_count
                FROM gadgets g
                JOIN operator_gadget_options ogo
                ON g.gadget_id = ogo.gadget_id
                WHERE ogo.operator_id = ?
                """,
                (operator_id,),
            ).fetchall()

            return [
                Gadget(
                    gadget_id=row["gadget_id"],
                    name=row["name"],
                    category=row["category"],
                    max_count=row["max_count"],
                )
                for row in rows
            ]

    # =====================================================
    # Matches
    # =====================================================

    def insert_match(self, match: Match) -> int:
        with self.db.get_connection() as conn:
            cursor = conn.execute(
                """
                INSERT INTO matches (datetime, opponent_name, map, result, recording_path)
                VALUES (?, ?, ?, ?, ?)
                """,
                (
                    match.datetime_played.isoformat(),
                    match.opponent_name,
                    match.map,
                    match.result,
                    match.recording_path,
                ),
            )
            conn.commit()

            if cursor.lastrowid is None:
                raise RuntimeError("Failed to retrieve match ID after insert.")

            return int(cursor.lastrowid)

    # =====================================================
    # Rounds
    # =====================================================

    def insert_round(self, round_obj: Round, match_id: int) -> int:
        with self.db.get_connection() as conn:
            cursor = conn.execute(
                """
                INSERT INTO rounds (match_id, round_number, side, site, outcome)
                VALUES (?, ?, ?, ?, ?)
                """,
                (
                    match_id,
                    round_obj.round_number,
                    round_obj.side,
                    round_obj.site,
                    round_obj.outcome,
                ),
            )
            conn.commit()
            if cursor.lastrowid is None:
                raise RuntimeError("Failed to retrieve round ID after insert.")
            return int(cursor.lastrowid)

    # =====================================================
    # Round Resources
    # =====================================================

    def insert_round_resources(self, resources: RoundResources, round_id: int) -> None:
        with self.db.get_connection() as conn:
            conn.execute(
                """
                INSERT INTO round_resources (
                    round_id,
                    team_drones_start,
                    team_drones_lost,
                    team_reinforcements_start,
                    team_reinforcements_used
                )
                VALUES (?, ?, ?, ?, ?)
                """,
                (
                    round_id,
                    resources.team_drones_start,
                    resources.team_drones_lost,
                    resources.team_reinforcements_start,
                    resources.team_reinforcements_used,
                ),
            )
            conn.commit()

    # =====================================================
    # Player Round Stats
    # =====================================================

    def insert_player_round_stats(
        self,
        stats: PlayerRoundStats,
        round_id: int,
        player_id: int,
    ) -> None:
        with self.db.get_connection() as conn:
            conn.execute(
                """
                INSERT INTO player_round_stats (
                    round_id,
                    player_id,
                    operator_id,
                    kills,
                    deaths,
                    assists,
                    engagements_taken,
                    engagements_won,
                    ability_start,
                    ability_used,
                    secondary_gadget_id,
                    secondary_start,
                    secondary_used,
                    plant_attempted,
                    plant_successful
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    round_id,
                    player_id,
                    stats.operator.operator_id,
                    stats.kills,
                    stats.deaths,
                    stats.assists,
                    stats.engagements_taken,
                    stats.engagements_won,
                    stats.ability_start,
                    stats.ability_used,
                    stats.secondary_gadget.gadget_id
                    if stats.secondary_gadget
                    else None,
                    stats.secondary_start,
                    stats.secondary_used,
                    int(stats.plant_attempted),
                    int(stats.plant_successful),
                ),
            )
            conn.commit()

    # =====================================================
    # Players
    # =====================================================

    def insert_player(self, player: Player) -> int:
        with self.db.get_connection() as conn:
            cursor = conn.execute(
                """
                INSERT INTO players (name, is_team_member)
                VALUES (?, ?)
                """,
                (player.name, int(player.is_team_member)),
            )
            conn.commit()

            if cursor.lastrowid is None:
                raise RuntimeError("Failed to retrieve player ID after insert.")

            return int(cursor.lastrowid)


    def clear_team_players(self):
        with self.db.get_connection() as conn:
            conn.execute(
                "DELETE FROM players WHERE is_team_member = 1"
            )
            conn.commit()

    # =====================================================
    # Maps
    # =====================================================
    def get_all_maps(self):
        with self.db.get_connection() as conn:
            rows = conn.execute("SELECT * FROM maps ORDER BY name").fetchall()
            return [row["name"] for row in rows]
    def insert_map(self, map_name: str) -> int:
        with self.db.get_connection() as conn:
            cursor = conn.execute(
                """
                INSERT INTO maps (name)
                VALUES (?)
                """,
                (map_name,),
            )
            conn.commit()

            if cursor.lastrowid is None:
                raise RuntimeError("Failed to retrieve map ID after insert.")

            return int(cursor.lastrowid)
    def get_map_id_by_name(self, name: str) -> Optional[int]:
        with self.db.get_connection() as conn:
            row = conn.execute(
                "SELECT map_id FROM maps WHERE name = ?", (name,)
            ).fetchone()
            return int(row["map_id"]) if row else None

    def get_map_by_id(self, map_id: int) -> Optional["Map"]:
        from models.map import Map
        with self.db.get_connection() as conn:
            row = conn.execute(
                "SELECT * FROM maps WHERE map_id = ?", (map_id,)
            ).fetchone()
            if not row:
                return None
            return Map(
                map_id=row["map_id"],
                name=row["name"],
                is_active_pool=bool(row["is_active_pool"]),
            )
    # =====================================================
    # FULL MATCH LOADER
    # =====================================================
    def create_match(self, opponent_name: str, map_name: str) -> int:
        match = Match(
            match_id=None,
            datetime_played=datetime.now(),
            opponent_name=opponent_name,
            map=map_name,
            result=None,
            recording_path="",
            rounds=[]
        )
        return self.insert_match(match)
    
    def get_match_full(self, match_id: int) -> Optional[Match]:

        with self.db.get_connection() as conn:

            # -------------------------------
            # Load Match
            # -------------------------------

            match_row = conn.execute(
                "SELECT * FROM matches WHERE match_id = ?",
                (match_id,),
            ).fetchone()

            if not match_row:
                return None

            match = Match(
                match_id=match_row["match_id"],
                datetime_played=datetime.fromisoformat(match_row["datetime"]),
                opponent_name=match_row["opponent_name"],
                map=match_row["map"],
                result=match_row["result"],
                recording_path=match_row["recording_path"],
                rounds=[],
            )

            # -------------------------------
            # Load Rounds
            # -------------------------------

            round_rows = conn.execute(
                "SELECT * FROM rounds WHERE match_id = ? ORDER BY round_number",
                (match_id,),
            ).fetchall()

            for round_row in round_rows:

                # ---------------------------
                # Load Resources FIRST
                # ---------------------------

                resource_row = conn.execute(
                    "SELECT * FROM round_resources WHERE round_id = ?",
                    (round_row["round_id"],),
                ).fetchone()

                if resource_row is None:
                    raise RuntimeError("Round missing resource entry.")

                resources = RoundResources(
                    resource_id=resource_row["resource_id"],
                    round_id=resource_row["round_id"],
                    side=round_row["side"],
                    team_drones_start=resource_row["team_drones_start"],
                    team_drones_lost=resource_row["team_drones_lost"],
                    team_reinforcements_start=resource_row["team_reinforcements_start"],
                    team_reinforcements_used=resource_row["team_reinforcements_used"],
                )

                # ---------------------------
                # Create Round
                # ---------------------------

                round_obj = Round(
                    round_id=round_row["round_id"],
                    match_id=round_row["match_id"],
                    round_number=round_row["round_number"],
                    side=round_row["side"],
                    site=round_row["site"],
                    outcome=round_row["outcome"],
                    resources=resources,
                    player_stats=[],
                )

                # ---------------------------
                # Load Player Stats
                # ---------------------------

                stat_rows = conn.execute(
                    "SELECT * FROM player_round_stats WHERE round_id = ?",
                    (round_obj.round_id,),
                ).fetchall()

                for stat_row in stat_rows:

                    operator = self.get_operator_by_id(stat_row["operator_id"])
                    if operator is None:
                        raise RuntimeError("Invalid operator reference in stats.")

                    secondary_gadget = None
                    if stat_row["secondary_gadget_id"]:
                        gadget_row = conn.execute(
                            "SELECT * FROM gadgets WHERE gadget_id = ?",
                            (stat_row["secondary_gadget_id"],),
                        ).fetchone()

                        if gadget_row:
                            secondary_gadget = Gadget(
                                gadget_id=gadget_row["gadget_id"],
                                name=gadget_row["name"],
                                category=gadget_row["category"],
                            )

                    player = self.get_player_by_id(stat_row["player_id"])
                    if player is None:
                        raise RuntimeError("Invalid player reference in stats.")

                    stats = PlayerRoundStats(
                        stat_id=stat_row["stat_id"],
                        round_id=stat_row["round_id"],
                        player_id=stat_row["player_id"],
                        player=player,
                        operator=operator,
                        kills=stat_row["kills"],
                        deaths=stat_row["deaths"],
                        assists=stat_row["assists"],
                        engagements_taken=stat_row["engagements_taken"],
                        engagements_won=stat_row["engagements_won"],
                        ability_start=stat_row["ability_start"],
                        ability_used=stat_row["ability_used"],
                        secondary_gadget=secondary_gadget,
                        secondary_start=stat_row["secondary_start"],
                        secondary_used=stat_row["secondary_used"],
                        plant_attempted=bool(stat_row["plant_attempted"]),
                        plant_successful=bool(stat_row["plant_successful"]),
                    )

                    round_obj.player_stats.append(stats)

                match.rounds.append(round_obj)

            return match

    def get_player_by_id(self, player_id: int) -> Optional[Player]:
        with self.db.get_connection() as conn:
            row = conn.execute(
                "SELECT * FROM players WHERE player_id = ?",
                (player_id,),
            ).fetchone()

            if not row:
                return None

            return Player(
                player_id=row["player_id"],
                name=row["name"],
                is_team_member=bool(row["is_team_member"]),
            )

    def get_team_players(self) -> list[Player]:
        with self.db.get_connection() as conn:
            rows = conn.execute(
                "SELECT * FROM players WHERE is_team_member = 1"
            ).fetchall()

            return [
                Player(
                    player_id=row["player_id"],
                    name=row["name"],
                    is_team_member=True,
                )
                for row in rows
            ]

    def get_all_matches(self) -> list[Match]:
        with self.db.get_connection() as conn:
            rows = conn.execute("SELECT * FROM matches ORDER BY datetime").fetchall()
            return [
                Match(
                    match_id=row["match_id"],
                    datetime_played=datetime.fromisoformat(row["datetime"]),
                    opponent_name=row["opponent_name"],
                    map=row["map"],
                    result=row["result"],
                    recording_path=row["recording_path"],
                    rounds=[]
                )
                for row in rows
            ]

    def export_match_to_csv(self, match_id, path):
        import csv

        match = self.get_match_full(match_id)

        if match is None:
            raise ValueError(f"Match {match_id} not found")

        with open(path, "w", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)

            writer.writerow([
                "Round", "Player", "Operator",
                "Kills", "Deaths", "Assists",
                "Engagements Taken", "Engagements Won"
            ])

            for r in match.rounds:
                for ps in r.player_stats:
                    writer.writerow([
                        r.round_number,
                        ps.player.name,
                        ps.operator.name,
                        ps.kills,
                        ps.deaths,
                        ps.assists,
                        ps.engagements_taken,
                        ps.engagements_won
                    ])

    # =====================================================
    # Transcripts
    # =====================================================

    def get_transcript_text(self, match_id: int) -> Optional[str]:
        with self.db.get_connection() as conn:
            row = conn.execute(
                "SELECT raw_text FROM transcripts WHERE match_id = ?",
                (match_id,),
            ).fetchone()

            if not row:
                return None

            return row["raw_text"]


    # =====================================================
    # Simple Match Fetch
    # =====================================================

    def get_match(self, match_id: int) -> Optional[Match]:
        with self.db.get_connection() as conn:
            row = conn.execute(
                "SELECT * FROM matches WHERE match_id = ?",
                (match_id,),
            ).fetchone()

            if not row:
                return None

            return Match(
                match_id=row["match_id"],
                datetime_played=datetime.fromisoformat(row["datetime"]),
                opponent_name=row["opponent_name"],
                map=row["map"],
                result=row["result"],
                recording_path=row["recording_path"],
                rounds=[],
            )
        
    def get_gadget_options(self, operator_id: int):
        with self.db.get_connection() as conn:
            rows = conn.execute(
                """
                SELECT *
                FROM operator_gadget_options
                WHERE operator_id = ?
                """,
                (operator_id,),
            ).fetchall()

            return rows  # simple for now (you can model it later)