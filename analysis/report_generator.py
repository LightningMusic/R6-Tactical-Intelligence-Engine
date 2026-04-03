from datetime import datetime
from pathlib import Path
from typing import Dict

from database.repositories import Repository
from analysis.intel_engine import IntelEngine


class ReportGenerator:
    def __init__(self):
        self.repo = Repository()
        self.intel = IntelEngine()

    # ============================================================
    # ENTRY POINT
    # ============================================================

    def generate_match_report(self, match_id: int) -> str:
        match = self.repo.get_match_full(match_id)
        if match is None:
            raise ValueError("Match not found.")

        intel_bundle = self.intel.analyze_match(match_id)

        html = self._build_html(match, intel_bundle)
        path = self._save_report(match_id, html)

        return str(path)

    # ============================================================
    # HTML BUILDER
    # ============================================================

    def _build_html(self, match, intel: Dict) -> str:

        team = intel["team_metrics"]
        players = intel["player_intel"]["summary"]
        consistency = intel["player_intel"]["consistency"]
        scores = intel["player_intel"]["tactical_score"]
        roles = intel["player_roles"]
        insights = intel["team_insights"]

        def pct(v): return f"{v:.1%}"
        def num(v): return f"{v:.2f}"

        # --------------------------------------------
        # COLOR HELPERS
        # --------------------------------------------
        def color_scale(value, good=0.6, bad=0.4):
            if value >= good:
                return "#4CAF50"
            elif value <= bad:
                return "#f44336"
            return "#FFC107"

        html = f"""
        <html>
        <head>
            <title>R6 Tactical Report</title>
            <style>
                body {{
                    font-family: Arial;
                    background: #0f1116;
                    color: #eee;
                    padding: 20px;
                }}

                h1, h2 {{
                    color: #4CAF50;
                }}

                .card {{
                    background: #1a1d25;
                    padding: 15px;
                    border-radius: 10px;
                    margin-bottom: 20px;
                }}

                table {{
                    width: 100%;
                    border-collapse: collapse;
                }}

                th, td {{
                    padding: 10px;
                    border-bottom: 1px solid #333;
                    text-align: center;
                }}

                th {{
                    background: #222;
                }}

                .good {{ color: #4CAF50; }}
                .bad {{ color: #f44336; }}
                .mid {{ color: #FFC107; }}
            </style>
        </head>

        <body>

        <h1>R6 Tactical Intelligence Report</h1>

        <div class="card">
            <h2>Match Overview</h2>
            <p><b>Date:</b> {match.datetime}</p>
            <p><b>Opponent:</b> {match.opponent_name}</p>
            <p><b>Map:</b> {match.map}</p>
            <p><b>Result:</b> {match.result}</p>
        </div>

        <div class="card">
            <h2>Team Metrics</h2>
            <ul>
                <li>Win Rate: {pct(team['win_rate'])}</li>
                <li>Attack Win Rate: {pct(team['attack_win_rate'])}</li>
                <li>Defense Win Rate: {pct(team['defense_win_rate'])}</li>
                <li>Engagement Win Rate: {pct(team['engagement_win_rate'])}</li>
                <li>Drone Efficiency: {pct(team['drone_efficiency'])}</li>
                <li>Reinforcement Usage: {pct(team['reinforcement_usage_rate'])}</li>
                <li>Man Advantage Conversion: {pct(team['man_advantage_conversion'])}</li>
                <li>Clutch Rate: {pct(team['clutch_rate'])}</li>
            </ul>
        </div>

        <div class="card">
            <h2>Team Insights</h2>
            <ul>
                <li><b>Strong Side:</b> {insights.get("strong_side")}</li>
                <li><b>Gunfights:</b> {insights.get("gunfight_performance")}</li>
                <li><b>Drone Usage:</b> {insights.get("drone_usage")}</li>
                <li><b>Setup Quality:</b> {insights.get("setup_quality")}</li>
                <li><b>Closing Power:</b> {insights.get("closing_power")}</li>
            </ul>
        </div>

        <div class="card">
            <h2>Player Performance</h2>
            <table>
                <tr>
                    <th>Player</th>
                    <th>Role</th>
                    <th>KD</th>
                    <th>Eng%</th>
                    <th>Survival%</th>
                    <th>Utility%</th>
                    <th>Plant%</th>
                    <th>Consistency</th>
                    <th>Score</th>
                </tr>
        """

        for pid, data in players.items():
            html += f"""
            <tr>
                <td>{data['player'].name}</td>
                <td>{roles.get(pid, "Unknown")}</td>
                <td>{num(data['kd_ratio'])}</td>
                <td style="color:{color_scale(data['engagement_win_rate'])}">
                    {pct(data['engagement_win_rate'])}
                </td>
                <td>{pct(data['survival_rate'])}</td>
                <td>{pct(data['utility_efficiency'])}</td>
                <td>{pct(data['plant_success_rate'])}</td>
                <td>{num(consistency.get(pid, 0))}</td>
                <td><b>{num(scores.get(pid, 0))}</b></td>
            </tr>
            """

        html += """
            </table>
        </div>

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

        path = reports_dir / f"match_{match_id}_report.html"

        with open(path, "w", encoding="utf-8") as f:
            f.write(html)

        return path