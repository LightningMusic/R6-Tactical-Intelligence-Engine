from typing import Dict, List
from models.match import Match
from models.round import Round
from models.player import Player
from models.player_round_stats import PlayerRoundStats
import statistics


class MetricsEngine:
    """
    Core analytics brain of the R6 Tactical Intelligence Engine.

    Consumes validated domain models and produces tactical intelligence.
    """

    def __init__(self, match: Match):
        self.match = match

    # ============================================================
    # MATCH LEVEL METRICS
    # ============================================================

    def win_rate(self) -> float:
        if not self.match.rounds:
            return 0.0

        wins = sum(1 for r in self.match.rounds if r.outcome == "win")
        return wins / len(self.match.rounds)

    def attack_win_rate(self) -> float:
        attack_rounds = [r for r in self.match.rounds if r.side == "attack"]
        if not attack_rounds:
            return 0.0

        wins = sum(1 for r in attack_rounds if r.outcome == "win")
        return wins / len(attack_rounds)

    def defense_win_rate(self) -> float:
        defense_rounds = [r for r in self.match.rounds if r.side == "defense"]
        if not defense_rounds:
            return 0.0

        wins = sum(1 for r in defense_rounds if r.outcome == "win")
        return wins / len(defense_rounds)

    def average_team_engagement_win_rate(self) -> float:
        if not self.match.rounds:
            return 0.0

        rates = [r.team_engagement_win_rate() for r in self.match.rounds]
        return sum(rates) / len(rates)

    # ============================================================
    # PLAYER LEVEL METRICS
    # ============================================================

    def player_summary(self) -> Dict[int, dict]:
        """
        Returns aggregated per-player performance across the match.
        Keyed by player_id.
        """

        summary: Dict[int, dict] = {}

        for round_obj in self.match.rounds:
            for stats in round_obj.player_stats:
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
                    }

                summary[pid]["kills"] += stats.kills
                summary[pid]["deaths"] += stats.deaths
                summary[pid]["assists"] += stats.assists
                summary[pid]["engagements_taken"] += stats.engagements_taken
                summary[pid]["engagements_won"] += stats.engagements_won
                summary[pid]["plants_attempted"] += int(stats.plant_attempted)
                summary[pid]["plants_successful"] += int(stats.plant_successful)
                summary[pid]["rounds_played"] += 1

        # Post-process derived metrics
        for pid, data in summary.items():
            deaths = data["deaths"]
            data["kd_ratio"] = (
                data["kills"] / deaths if deaths > 0 else float(data["kills"])
            )

            taken = data["engagements_taken"]
            data["engagement_win_rate"] = (
                data["engagements_won"] / taken if taken > 0 else 0.0
            )

            data["plant_success_rate"] = (
                data["plants_successful"] / data["plants_attempted"]
                if data["plants_attempted"] > 0
                else 0.0
            )

        return summary

    # ============================================================
    # OPERATOR PERFORMANCE
    # ============================================================

    def operator_performance(self) -> Dict[str, dict]:
        """
        Aggregates performance by operator name.
        """

        operator_stats: Dict[str, dict] = {}

        for round_obj in self.match.rounds:
            for stats in round_obj.player_stats:
                name = stats.operator.name

                if name not in operator_stats:
                    operator_stats[name] = {
                        "kills": 0,
                        "deaths": 0,
                        "rounds_played": 0,
                    }

                operator_stats[name]["kills"] += stats.kills
                operator_stats[name]["deaths"] += stats.deaths
                operator_stats[name]["rounds_played"] += 1

        for name, data in operator_stats.items():
            deaths = data["deaths"]
            data["kd_ratio"] = (
                data["kills"] / deaths if deaths > 0 else float(data["kills"])
            )

        return operator_stats

    # ============================================================
    # TEAM RESOURCE EFFICIENCY
    # ============================================================

    def drone_efficiency(self) -> float:
        """
        Measures how efficiently drones are preserved on attack.
        """

        attack_rounds = [r for r in self.match.rounds if r.side == "attack"]
        if not attack_rounds:
            return 0.0

        total_start = sum(r.resources.team_drones_start for r in attack_rounds)
        total_lost = sum(r.resources.team_drones_lost for r in attack_rounds)

        if total_start == 0:
            return 0.0

        return 1 - (total_lost / total_start)

    def reinforcement_usage_rate(self) -> float:
        defense_rounds = [r for r in self.match.rounds if r.side == "defense"]
        if not defense_rounds:
            return 0.0

        total_start = sum(r.resources.team_reinforcements_start for r in defense_rounds)
        total_used = sum(r.resources.team_reinforcements_used for r in defense_rounds)

        if total_start == 0:
            return 0.0

        return total_used / total_start
    
    def opening_kill_impact(self) -> float:
        """
        Measures how often winning the first engagement leads to winning the round.
        """

        total_with_opening = 0
        converted = 0

        for round_obj in self.match.rounds:
            if not round_obj.player_stats:
                continue

            # Determine if team got first kill
            team_kills = sum(p.kills for p in round_obj.player_stats)
            team_deaths = sum(p.deaths for p in round_obj.player_stats)

            if team_kills == 0 and team_deaths == 0:
                continue

            # Simplified assumption:
            # If total kills > total deaths → assume opening advantage
            if team_kills > team_deaths:
                total_with_opening += 1
                if round_obj.outcome == "win":
                    converted += 1

        if total_with_opening == 0:
            return 0.0

        return converted / total_with_opening
    
    def man_advantage_conversion(self) -> float:
        """
        Measures how often rounds with more kills than deaths result in wins.
        """

        advantage_rounds = 0
        wins = 0

        for round_obj in self.match.rounds:
            total_kills = sum(p.kills for p in round_obj.player_stats)
            total_deaths = sum(p.deaths for p in round_obj.player_stats)

            if total_kills > total_deaths:
                advantage_rounds += 1
                if round_obj.outcome == "win":
                    wins += 1

        if advantage_rounds == 0:
            return 0.0

        return wins / advantage_rounds


    def clutch_rate(self) -> float:
        """
        Measures how often a player wins a round
        where they had >=2 kills and team won.
        """

        clutch_attempts = 0
        clutch_wins = 0

        for round_obj in self.match.rounds:
            for stats in round_obj.player_stats:
                if stats.kills >= 2:
                    clutch_attempts += 1
                    if round_obj.outcome == "win":
                        clutch_wins += 1

        if clutch_attempts == 0:
            return 0.0

        return clutch_wins / clutch_attempts


    def player_consistency_index(self) -> Dict[int, float]:
        """
        Lower score = more consistent.
        Based on standard deviation of kills per round.
        """

        player_kill_history: Dict[int, list[int]] = {}

        for round_obj in self.match.rounds:
            for stats in round_obj.player_stats:
                player_kill_history.setdefault(stats.player_id, []).append(stats.kills)

        consistency_scores: Dict[int, float] = {}

        for pid, kills in player_kill_history.items():
            if len(kills) < 2:
                consistency_scores[pid] = 0.0
            else:
                consistency_scores[pid] = statistics.stdev(kills)

        return consistency_scores


    def tactical_performance_score(self) -> Dict[int, float]:
        """
        Composite weighted rating per player.
        """

        summary = self.player_summary()
        scores: Dict[int, float] = {}

        for pid, data in summary.items():

            score = (
                data["kd_ratio"] * 0.35 +
                data["engagement_win_rate"] * 0.25 +
                data["plant_success_rate"] * 0.15 +
                (data["kills"] / max(data["rounds_played"], 1)) * 0.25
            )

            scores[pid] = round(score, 3)

        return scores