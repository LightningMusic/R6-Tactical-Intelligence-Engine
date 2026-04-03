from typing import Dict

from database.repositories import Repository
from analysis.metrics_engine import MetricsEngine


class IntelEngine:
    def __init__(self):
        self.repo = Repository()

    # ============================================================
    # CORE ENTRY POINT
    # ============================================================

    def analyze_match(self, match_id: int) -> Dict:
        match = self.repo.get_match_full(match_id)

        if match is None:
            raise ValueError("Match not found.")

        engine = MetricsEngine(match)

        # ---------------- TEAM METRICS ----------------
        team_metrics = {
            "win_rate": engine.win_rate(),
            "attack_win_rate": engine.attack_win_rate(),
            "defense_win_rate": engine.defense_win_rate(),
            "engagement_win_rate": engine.average_team_engagement_win_rate(),
            "drone_efficiency": engine.drone_efficiency(),
            "reinforcement_usage_rate": engine.reinforcement_usage_rate(),
            "man_advantage_conversion": engine.man_advantage_conversion(),
            "clutch_rate": engine.clutch_rate(),
        }

        # ---------------- PLAYER DATA ----------------
        summary = engine.player_summary()
        consistency = engine.player_consistency_index()
        tactical_scores = engine.tactical_performance_score()

        # ---------------- INTELLIGENCE LAYER ----------------
        team_insights = self._generate_team_insights(team_metrics)
        player_roles = self._classify_player_roles(summary)

        # Persist only flat metrics
        self._persist_metrics(match_id, team_metrics)

        return {
            "team_metrics": team_metrics,
            "player_intel": {
                "summary": summary,
                "consistency": consistency,
                "tactical_score": tactical_scores,
            },
            "team_insights": team_insights,
            "player_roles": player_roles,
        }

    # ============================================================
    # TEAM INSIGHTS (NO AI — PURE LOGIC)
    # ============================================================

    def _generate_team_insights(self, metrics: Dict[str, float]) -> Dict[str, str]:
        insights = {}

        # Win Condition
        if metrics["attack_win_rate"] > metrics["defense_win_rate"]:
            insights["strong_side"] = "Attack"
        else:
            insights["strong_side"] = "Defense"

        # Engagement quality
        if metrics["engagement_win_rate"] > 0.55:
            insights["gunfight_performance"] = "Strong"
        elif metrics["engagement_win_rate"] < 0.45:
            insights["gunfight_performance"] = "Weak"
        else:
            insights["gunfight_performance"] = "Average"

        # Resource usage
        if metrics["drone_efficiency"] < 0.6:
            insights["drone_usage"] = "Wasteful"
        else:
            insights["drone_usage"] = "Efficient"

        if metrics["reinforcement_usage_rate"] < 0.7:
            insights["setup_quality"] = "Unstructured"
        else:
            insights["setup_quality"] = "Structured"

        # Closing ability
        if metrics["man_advantage_conversion"] < 0.6:
            insights["closing_power"] = "Poor"
        else:
            insights["closing_power"] = "Reliable"

        return insights

    # ============================================================
    # PLAYER ROLE CLASSIFICATION (HUGE FEATURE)
    # ============================================================

    def _classify_player_roles(self, summary: Dict[int, dict]) -> Dict[int, str]:
        roles = {}

        for pid, data in summary.items():
            kd = data["kd_ratio"]
            survival = data["survival_rate"]
            engagement = data["engagement_win_rate"]
            utility = data["utility_efficiency"]

            # Entry Fraggers
            if engagement > 0.6 and survival < 0.5:
                roles[pid] = "Entry Fragger"

            # Support
            elif utility > 0.6 and kd < 1.0:
                roles[pid] = "Support"

            # Anchor
            elif survival > 0.7 and engagement < 0.5:
                roles[pid] = "Anchor"

            # Carry
            elif kd > 1.3 and engagement > 0.55:
                roles[pid] = "Carry"

            # Flex
            else:
                roles[pid] = "Flex"

        return roles

    # ============================================================
    # PERSISTENCE
    # ============================================================

    def _persist_metrics(self, match_id: int, metrics: Dict[str, float]) -> None:
        with self.repo.db.get_connection() as conn:

            conn.execute(
                "DELETE FROM derived_metrics WHERE match_id = ?",
                (match_id,),
            )

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
    # OPTIONAL DIRECT ACCESS
    # ============================================================

    def get_player_intel(self, match_id: int):
        match = self.repo.get_match_full(match_id)

        if match is None:
            raise ValueError("Match not found.")

        engine = MetricsEngine(match)

        return {
            "summary": engine.player_summary(),
            "consistency": engine.player_consistency_index(),
            "tactical_score": engine.tactical_performance_score(),
        }