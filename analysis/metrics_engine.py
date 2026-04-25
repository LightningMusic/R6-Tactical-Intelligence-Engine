from typing import Dict, List
import statistics

from models.match import Match


class MetricsEngine:
    def __init__(self, match: Match):
        self.match = match

    # ============================================================
    # INTERNAL HELPERS (SCALABILITY CORE)
    # ============================================================

    @staticmethod
    def _safe_div(numerator: float, denominator: float) -> float:
        return numerator / denominator if denominator > 0 else 0.0

    def _all_player_stats(self):
        for r in self.match.rounds:
            if not r.player_stats:
                continue   # imported rounds have no stats yet
            for p in r.player_stats:
                yield r, p

    # ============================================================
    # MATCH LEVEL METRICS
    # ============================================================

    def win_rate(self) -> float:
        total = len(self.match.rounds)
        wins = sum(1 for r in self.match.rounds if r.outcome == "win")
        return self._safe_div(wins, total)

    def attack_win_rate(self) -> float:
        rounds = [r for r in self.match.rounds if r.side == "attack"]
        wins = sum(1 for r in rounds if r.outcome == "win")
        return self._safe_div(wins, len(rounds))

    def defense_win_rate(self) -> float:
        rounds = [r for r in self.match.rounds if r.side == "defense"]
        wins = sum(1 for r in rounds if r.outcome == "win")
        return self._safe_div(wins, len(rounds))

    def average_team_engagement_win_rate(self) -> float:
        rates = [
            r.team_engagement_win_rate()
            for r in self.match.rounds
            if r.player_stats  # skip rounds with no stats
        ]
        if not rates:
            return 0.0
        return self._safe_div(sum(rates), len(rates))

    # ============================================================
    # PLAYER AGGREGATION
    # ============================================================

    def player_summary(self) -> Dict[int, dict]:
        summary: Dict[int, dict] = {}

        for round_obj, stats in self._all_player_stats():
            pid = stats.player_id

            if pid not in summary:
                summary[pid] = {
                    "player": stats.player,
                    "kills": 0,
                    "deaths": 0,
                    "assists": 0,
                    "engagements_taken": 0,
                    "engagements_won": 0,
                    "plants_attempted": 0,
                    "plants_successful": 0,
                    "rounds_played": 0,

                    # NEW TRACKING
                    "rounds_survived": 0,
                    "ability_used": 0,
                    "ability_total": 0,
                    "gadget_used": 0,
                    "gadget_total": 0,
                }

            data = summary[pid]

            data["kills"] += stats.kills
            data["deaths"] += stats.deaths
            data["assists"] += stats.assists
            data["engagements_taken"] += stats.engagements_taken
            data["engagements_won"] += stats.engagements_won
            data["plants_attempted"] += int(stats.plant_attempted)
            data["plants_successful"] += int(stats.plant_successful)
            data["rounds_played"] += 1

            # NEW
            if stats.deaths == 0:
                data["rounds_survived"] += 1

            data["ability_used"] += stats.ability_used
            data["ability_total"] += stats.ability_start

            data["gadget_used"] += stats.secondary_used
            data["gadget_total"] += stats.secondary_start

        # ========================================================
        # DERIVED PLAYER METRICS
        # ========================================================

        for data in summary.values():
            data["kd_ratio"] = self._safe_div(data["kills"], data["deaths"])
            data["engagement_win_rate"] = self._safe_div(
                data["engagements_won"], data["engagements_taken"]
            )
            data["plant_success_rate"] = self._safe_div(
                data["plants_successful"], data["plants_attempted"]
            )

            # NEW
            data["survival_rate"] = self._safe_div(
                data["rounds_survived"], data["rounds_played"]
            )

            data["ability_efficiency"] = self._safe_div(
                data["ability_used"], data["ability_total"]
            )

            data["gadget_efficiency"] = self._safe_div(
                data["gadget_used"], data["gadget_total"]
            )

            data["utility_efficiency"] = (
                data["ability_efficiency"] * 0.6 +
                data["gadget_efficiency"] * 0.4
            )

        return summary

    # ============================================================
    # TEAM RESOURCE METRICS
    # ============================================================

    def drone_efficiency(self) -> float:
        rounds = [
            r for r in self.match.rounds
            if r.side == "attack" and r.resources is not None
        ]
        if not rounds:
            return 0.0

        total_start = 0
        total_lost  = 0
        has_data    = False

        for r in rounds:
            res = r.resources
            if res:
                total_start += res.team_drones_start
                total_lost  += res.team_drones_lost
                if res.team_drones_lost > 0:
                    has_data = True

        # If no drone loss was ever recorded, we have no real data
        # (imported rounds default to 0 lost — don't report as 100% efficient)
        if not has_data:
            return 0.0

        return 1.0 - self._safe_div(total_lost, total_start)


    def reinforcement_usage_rate(self) -> float:
        rounds = [
            r for r in self.match.rounds
            if r.side == "defense" and r.resources is not None
        ]
        if not rounds:
            return 0.0

        total_start = 0
        total_used  = 0
        has_data    = False

        for r in rounds:
            res = r.resources
            if res:
                total_start += res.team_reinforcements_start
                total_used  += res.team_reinforcements_used
                if res.team_reinforcements_used > 0:
                    has_data = True

        if not has_data:
            return 0.0

        return self._safe_div(total_used, total_start)

    # ============================================================
    # ADVANTAGE / CONVERSION
    # ============================================================

    def man_advantage_conversion(self) -> float:
        advantage = 0
        wins = 0

        for r in self.match.rounds:
            kills = sum(p.kills for p in r.player_stats)
            deaths = sum(p.deaths for p in r.player_stats)

            if kills > deaths:
                advantage += 1
                if r.outcome == "win":
                    wins += 1

        return self._safe_div(wins, advantage)

    # ============================================================
    # TRUE CLUTCH LOGIC (BEST POSSIBLE WITHOUT TIMELINE)
    # ============================================================

    def clutch_rate(self) -> float:
        """
        Heuristic clutch detection:
        - Player gets >=2 kills
        - Team total kills >=4 (indicates late-round scenario)
        - Round is won
        """

        attempts = 0
        wins = 0

        for r in self.match.rounds:
            team_kills = sum(p.kills for p in r.player_stats)

            for p in r.player_stats:
                if p.kills >= 2 and team_kills >= 4:
                    attempts += 1
                    if r.outcome == "win":
                        wins += 1

        return self._safe_div(wins, attempts)

    # ============================================================
    # CONSISTENCY
    # ============================================================

    def player_consistency_index(self) -> Dict[int, float]:
        history: Dict[int, List[int]] = {}

        for _, stats in self._all_player_stats():
            history.setdefault(stats.player_id, []).append(stats.kills)

        return {
            pid: (statistics.stdev(kills) if len(kills) > 1 else 0.0)
            for pid, kills in history.items()
        }

    # ============================================================
    # COMPOSITE SCORE (UPDATED)
    # ============================================================

    def tactical_performance_score(self) -> Dict[int, float]:
        summary = self.player_summary()
        scores: Dict[int, float] = {}

        for pid, d in summary.items():
            score = (
                d["kd_ratio"] * 0.25 +
                d["engagement_win_rate"] * 0.20 +
                d["utility_efficiency"] * 0.20 +
                d["survival_rate"] * 0.15 +
                d["plant_success_rate"] * 0.10 +
                (d["kills"] / max(d["rounds_played"], 1)) * 0.10
            )

            scores[pid] = round(score, 3)

        return scores