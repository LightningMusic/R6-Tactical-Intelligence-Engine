from typing import Dict

from database.repositories import Repository
from analysis.intel_engine import IntelEngine
from analysis.report_generator import ReportGenerator

from models.round import Round
from models.round_resources import RoundResources
from models.player_round_stats import PlayerRoundStats


class AppController:
    """
    Orchestrates post-match workflows for R6 Analyzer.
    """

    def __init__(self):
        self.repo = Repository()
        self.intel = IntelEngine()
        self.report_gen = ReportGenerator()

    # ============================================================
    # POST-MATCH WORKFLOW
    # ============================================================
    def create_match(self, opponent_name, map_name):
        return self.repo.create_match(opponent_name, map_name)
    def process_completed_match(self, match_id: int) -> Dict:
        match = self.repo.get_match_full(match_id)

        if match is None:
            raise ValueError(f"Match {match_id} not found.")

        derived_metrics = self.intel.analyze_match(match_id)
        player_intel = self.intel.get_player_intel(match_id)
        report_path = self.report_gen.generate_match_report(match_id)

        return {
            "match_id": match_id,
            "derived_metrics": derived_metrics,
            "player_intel": player_intel,
            "report_path": report_path,
        }

    # ============================================================
    # MANUAL ACCESS
    # ============================================================

    def regenerate_report(self, match_id: int) -> str:
        return self.report_gen.generate_match_report(match_id)

    def fetch_match_intel(self, match_id: int) -> Dict:
        return {
            "derived_metrics": self.intel.analyze_match(match_id),
            "player_intel": self.intel.get_player_intel(match_id),
        }

    # ============================================================
    # ROUND SAVE (CORE PIPELINE)
    # ============================================================

    def save_round(self, round_data: dict):

        match_id = round_data["match_id"]
        round_number = round_data["round_number"]
        side = round_data["side"]

        # ----------------------------
        # Create Round Resources
        # ----------------------------
        if side == "attack":
            resources = RoundResources(
                resource_id=None,
                round_id=0,
                side=side,
                team_drones_start=10,
                team_drones_lost=round_data.get("team_drones_lost", 0),
                team_reinforcements_start=0,
                team_reinforcements_used=0,
            )
        else:
            resources = RoundResources(
                resource_id=None,
                round_id=0,
                side=side,
                team_drones_start=0,
                team_drones_lost=0,
                team_reinforcements_start=10,
                team_reinforcements_used=round_data.get("team_reinforcements_used", 0),
            )

        # ----------------------------
        # Create Round
        # ----------------------------
        round_obj = Round(
            round_id=None,
            match_id=match_id,
            round_number=round_number,
            side=side,
            site=round_data.get("site", ""),
            outcome=round_data.get("outcome", ""),
            resources=resources,
            player_stats=[],
        )

        round_id = self.repo.insert_round(round_obj, match_id)

        # Save resources AFTER round exists
        self.repo.insert_round_resources(resources, round_id)

        # ----------------------------
        # Insert Player Stats
        # ----------------------------
        for ps in round_data["player_stats"]:

            # Operator
            operator = self.repo.get_operator_by_id(ps["operator_id"])
            if operator is None:
                raise ValueError(f"Invalid operator ID: {ps['operator_id']}")

            # Player
            player = self.repo.get_player_by_id(ps["player_id"])
            if player is None or player.player_id is None:
                raise ValueError(f"Invalid player ID: {ps['player_id']}")

            player_id: int = player.player_id  # ✅ type safe

            # ----------------------------
            # Secondary Gadget
            # ----------------------------
            secondary_gadget = None
            secondary_start = 0

            if ps["secondary_gadget_id"]:
                gadgets = self.repo.get_gadgets_for_operator(operator.operator_id)

                for g in gadgets:
                    if g.gadget_id == ps["secondary_gadget_id"]:
                        secondary_gadget = g
                        break

                # Get max count
                options = self.repo.get_gadget_options(operator.operator_id)
                for opt in options:
                    if opt["gadget_id"] == ps["secondary_gadget_id"]:
                        secondary_start = opt["max_count"]
                        break

            # ----------------------------
            # Build Stats Object
            # ----------------------------
            stat = PlayerRoundStats(
                stat_id=None,
                round_id=round_id,
                player_id=player_id,
                player=player,
                operator=operator,

                kills=ps["kills"],
                deaths=ps["deaths"],
                assists=ps["assists"],

                engagements_taken=ps["engagements_taken"],
                engagements_won=ps["engagements_won"],

                ability_start=operator.ability_max_count,
                ability_used=ps["ability_used"],

                secondary_gadget=secondary_gadget,
                secondary_start=secondary_start,
                secondary_used=ps["secondary_used"],

                plant_attempted=ps["plant_attempted"],
                plant_successful=ps["plant_successful"],
            )

            # ----------------------------
            # Insert into DB
            # ----------------------------
            self.repo.insert_player_round_stats(stat, round_id, player_id)

    # ============================================================
    # EXPORT / FETCH HELPERS
    # ============================================================

    def export_match_csv(self, match_id, path):
        self.repo.export_match_to_csv(match_id, path)

    def get_transcript_text(self, match_id):
        return self.repo.get_transcript_text(match_id)

    def get_recording_path(self, match_id):
        match = self.repo.get_match(match_id)

        if match is None:
            raise ValueError(f"Match {match_id} not found")

        return match.recording_path