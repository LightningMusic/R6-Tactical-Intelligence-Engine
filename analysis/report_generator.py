from datetime import datetime
from pathlib import Path
from typing import Dict

from database.repositories import Repository
from analysis.intel_engine import IntelEngine


class ReportGenerator:
    """
    Generates formatted HTML reports from stored match data
    and derived intelligence.
    """

    def __init__(self):
        self.repo = Repository()
        self.intel = IntelEngine()

    # ============================================================
    # PUBLIC ENTRY POINT
    # ============================================================

    def generate_match_report(self, match_id: int) -> str:
        """
        Generates and saves an HTML report.
        Returns file path.
        """

        match = self.repo.get_match_full(match_id)
        if match is None:
            raise ValueError("Match not found.")

        # Ensure metrics are up to date
        derived = self.intel.analyze_match(match_id)
        player_intel = self.intel.get_player_intel(match_id)

        html = self._build_html(match, derived, player_intel)

        path = self._save_report(match_id, html)

        return str(path)

    # ============================================================
    # HTML BUILDER
    # ============================================================

    def _build_html(
        self,
        match,
        derived: Dict[str, float],
        player_intel: Dict,
    ) -> str:

        summary = player_intel["summary"]
        consistency = player_intel["consistency"]
        tactical_scores = player_intel["tactical_score"]

        html = f"""
        <html>
        <head>
            <title>Match Report</title>
            <style>
                body {{ font-family: Arial; background: #111; color: #eee; }}
                h1, h2 {{ color: #4CAF50; }}
                table {{ border-collapse: collapse; width: 100%; }}
                th, td {{ border: 1px solid #444; padding: 8px; text-align: center; }}
                th {{ background-color: #222; }}
            </style>
        </head>
        <body>

        <h1>R6 Tactical Intelligence Report</h1>

        <h2>Match Overview</h2>
        <p><b>Date:</b> {match.datetime}</p>
        <p><b>Opponent:</b> {match.opponent_name}</p>
        <p><b>Map:</b> {match.map}</p>
        <p><b>Result:</b> {match.result}</p>

        <h2>Team Metrics</h2>
        <ul>
            <li>Win Rate: {derived['win_rate']:.2%}</li>
            <li>Attack Win Rate: {derived['attack_win_rate']:.2%}</li>
            <li>Defense Win Rate: {derived['defense_win_rate']:.2%}</li>
            <li>Engagement Win Rate: {derived['avg_engagement_win_rate']:.2%}</li>
            <li>Drone Efficiency: {derived['drone_efficiency']:.2%}</li>
            <li>Reinforcement Usage Rate: {derived['reinforcement_usage_rate']:.2%}</li>
            <li>Opening Kill Conversion: {derived['opening_kill_impact']:.2%}</li>
            <li>Man Advantage Conversion: {derived['man_advantage_conversion']:.2%}</li>
            <li>Clutch Rate: {derived['clutch_rate']:.2%}</li>
        </ul>

        <h2>Player Performance</h2>
        <table>
            <tr>
                <th>Player</th>
                <th>KD</th>
                <th>Engagement %</th>
                <th>Plant %</th>
                <th>Consistency (σ)</th>
                <th>Tactical Score</th>
            </tr>
        """

        for pid, data in summary.items():
            html += f"""
            <tr>
                <td>{data['player'].name}</td>
                <td>{data['kd_ratio']:.2f}</td>
                <td>{data['engagement_win_rate']:.2%}</td>
                <td>{data['plant_success_rate']:.2%}</td>
                <td>{consistency.get(pid, 0):.2f}</td>
                <td>{tactical_scores.get(pid, 0):.2f}</td>
            </tr>
            """

        html += """
        </table>

        </body>
        </html>
        """

        return html

    # ============================================================
    # SAVE FILE
    # ============================================================

    def _save_report(self, match_id: int, html: str) -> Path:

        reports_dir = Path("data/reports")
        reports_dir.mkdir(parents=True, exist_ok=True)

        filename = f"match_{match_id}_report.html"
        path = reports_dir / filename

        with open(path, "w", encoding="utf-8") as f:
            f.write(html)

        return path