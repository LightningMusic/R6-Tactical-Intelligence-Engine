import statistics
from typing import Dict

from database.repositories import Repository
from analysis.metrics_engine import MetricsEngine


class IntelEngine:
    """
    Orchestrates intelligence generation for a match.

    Responsible for:
    - Loading match
    - Running metrics
    - Persisting derived metrics
    - Providing structured report data
    """

    def __init__(self):
        self.repo = Repository()

    # ============================================================
    # CORE ENTRY POINT
    # ============================================================

    def analyze_match(self, match_id: int) -> Dict[str, float]:
        """
        Runs full analytics pipeline for a match
        and stores derived metrics.
        """

        match = self.repo.get_match_full(match_id)

        if match is None:
            raise ValueError("Match not found.")

        engine = MetricsEngine(match)

        derived = {
            "win_rate": engine.win_rate(),
            "attack_win_rate": engine.attack_win_rate(),
            "defense_win_rate": engine.defense_win_rate(),
            "avg_engagement_win_rate": engine.average_team_engagement_win_rate(),
            "drone_efficiency": engine.drone_efficiency(),
            "reinforcement_usage_rate": engine.reinforcement_usage_rate(),
            "opening_kill_impact": engine.opening_kill_impact(),
            "man_advantage_conversion": engine.man_advantage_conversion(),
            "clutch_rate": engine.clutch_rate(),
        }

        self._persist_metrics(match_id, derived)

        return derived

    # ============================================================
    # PERSISTENCE
    # ============================================================

    def _persist_metrics(self, match_id: int, metrics: Dict[str, float]) -> None:
        """
        Stores derived metrics in DB.
        Overwrites previous metrics for match.
        """

        with self.repo.db.get_connection() as conn:

            # Remove old metrics
            conn.execute(
                "DELETE FROM derived_metrics WHERE match_id = ?",
                (match_id,),
            )

            # Insert new
            for name, value in metrics.items():
                conn.execute(
                    """
                    INSERT INTO derived_metrics (match_id, metric_name, metric_value)
                    VALUES (?, ?, ?)
                    """,
                    (match_id, name, value),
                )

            conn.commit()

    # ============================================================
    # PLAYER INTEL ACCESS
    # ============================================================

    def get_player_intel(self, match_id: int):
        """
        Returns structured per-player intelligence.
        """

        match = self.repo.get_match_full(match_id)
        if match is None:
            raise ValueError("Match not found.")

        engine = MetricsEngine(match)

        return {
            "summary": engine.player_summary(),
            "consistency": engine.player_consistency_index(),
            "tactical_score": engine.tactical_performance_score(),
        }