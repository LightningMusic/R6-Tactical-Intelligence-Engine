from datetime import datetime
from re import Match
from typing import Dict, Optional

from database.repositories import Repository
from analysis.intel_engine import IntelEngine
from analysis.report_generator import ReportGenerator

from models.import_result import ImportResult
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
    # MATCH CREATION
    # ============================================================

    def create_match(self, opponent_name: str, map_name: str) -> int:
        return self.repo.create_match(opponent_name, map_name)

    # ============================================================
    # AUTOMATED IMPORT SAVE
    # ============================================================

    def save_imported_match(self, result: ImportResult) -> int:
        """
        Saves a fully parsed ImportResult to the DB.
        Creates the match record, then saves each round.
        Returns the new match_id.
        """
        if not result.is_success:
            raise ValueError("Cannot save a non-successful ImportResult.")

        # ── Resolve map name to map_id if possible ───────────────
        map_id: Optional[int] = result.map_id
        map_name: str = "Unknown"

        if map_id is None and result.map_name:
            map_id = self.repo.get_map_id_by_name(result.map_name)
            map_name = result.map_name
        elif map_id is not None:
            resolved = self.repo.get_map_by_id(map_id)
            map_name = resolved.name if resolved else "Unknown"

        # ── Create match record ───────────────────────────────────
        from models.match import Match
        match = Match(
            match_id=None,
            datetime_played=datetime.now(),
            opponent_name="Imported",
            map=map_name,
            result=None,
            recording_path=result.recording_path,  # ← was None
            rounds=[],
        )
        match_id = self.repo.insert_match(match)

        # ── Save each parsed round ────────────────────────────────
        for round_obj in result.rounds:
            round_obj.match_id = match_id

            # Rounds from rec_importer have no resources yet — default them
            side = round_obj.side
            if side == "attack":
                resources = RoundResources(
                    resource_id=None,
                    round_id=0,
                    side=side,
                    team_drones_start=10,
                    team_drones_lost=0,
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
                    team_reinforcements_used=0,
                )

            round_id = self.repo.insert_round(round_obj, match_id)
            self.repo.insert_round_resources(resources, round_id)

            # Player stats are empty from rec_importer for now —
            # they will be filled in manually via match_view if needed.

        return match_id

    # ============================================================
    # POST-MATCH WORKFLOW
    # ============================================================

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

    def fetch_match_intel(self, match_id: int) -> dict:
        from analysis.metrics_engine import MetricsEngine

        match = self.repo.get_match_full(match_id)
        if match is None:
            return {"error": f"Match {match_id} not found."}

        engine = MetricsEngine(match)

        metrics: dict = {
            "win_rate":                    round(engine.win_rate(), 3),
            "attack_win_rate":             round(engine.attack_win_rate(), 3),
            "defense_win_rate":            round(engine.defense_win_rate(), 3),
            "engagement_win_rate":         round(engine.average_team_engagement_win_rate(), 3),
            "drone_efficiency":            round(engine.drone_efficiency(), 3),
            "reinforcement_usage_rate":    round(engine.reinforcement_usage_rate(), 3),
            "man_advantage_conversion":    round(engine.man_advantage_conversion(), 3),
            "clutch_rate":                 round(engine.clutch_rate(), 3),
        }

        summary     = engine.player_summary()
        tps         = engine.tactical_performance_score()
        consistency = engine.player_consistency_index()

        player_metrics: dict = {}
        for pid, data in summary.items():
            name = data["player"].name
            player_metrics[name] = {
                "kills":               data["kills"],
                "deaths":              data["deaths"],
                "assists":             data["assists"],
                "kd_ratio":            round(data["kd_ratio"], 2),
                "engagement_win_rate": round(data["engagement_win_rate"], 2),
                "survival_rate":       round(data["survival_rate"], 2),
                "ability_efficiency":  round(data["ability_efficiency"], 2),
                "gadget_efficiency":   round(data["gadget_efficiency"], 2),
                "utility_efficiency":  round(data["utility_efficiency"], 2),
                "plant_success_rate":  round(data["plant_success_rate"], 2),
                "tps":                 tps.get(pid, 0.0),
                "consistency_stdev":   round(consistency.get(pid, 0.0), 3),
            }

        metrics["players"] = player_metrics

        try:
            ai_result = self.intel.analyze_match(match_id)
            metrics["ai_summary"] = ai_result.get("ai_match_summary", "")
        except Exception as e:
            metrics["ai_summary"] = f"[AI unavailable: {e}]"

        return metrics

    # ============================================================
    # ROUND SAVE (MANUAL ENTRY PIPELINE)
    # ============================================================

    def save_round(self, round_data: dict) -> None:
        match_id     = round_data["match_id"]
        round_number = round_data["round_number"]
        side         = round_data["side"]

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
        self.repo.insert_round_resources(resources, round_id)

        for ps in round_data["player_stats"]:
            operator = self.repo.get_operator_by_id(ps["operator_id"])
            if operator is None:
                raise ValueError(f"Invalid operator ID: {ps['operator_id']}")

            player = self.repo.get_player_by_id(ps["player_id"])
            if player is None or player.player_id is None:
                raise ValueError(f"Invalid player ID: {ps['player_id']}")

            player_id: int = player.player_id

            secondary_gadget = None
            secondary_start  = 0

            if ps["secondary_gadget_id"]:
                for g in self.repo.get_gadgets_for_operator(operator.operator_id):
                    if g.gadget_id == ps["secondary_gadget_id"]:
                        secondary_gadget = g
                        break

                for opt in self.repo.get_gadget_options(operator.operator_id):
                    if opt["gadget_id"] == ps["secondary_gadget_id"]:
                        secondary_start = opt["max_count"]
                        break

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

            self.repo.insert_player_round_stats(stat, round_id, player_id)

    # ============================================================
    # EXPORT / FETCH HELPERS
    # ============================================================

    def export_match_csv(self, match_id: int, path: str) -> None:
        self.repo.export_match_to_csv(match_id, path)

    def get_transcript_text(self, match_id: int) -> Optional[str]:
        return self.repo.get_transcript_text(match_id)

    def get_recording_path(self, match_id: int) -> Optional[str]:
        match = self.repo.get_match(match_id)
        if match is None:
            raise ValueError(f"Match {match_id} not found")
        return match.recording_path