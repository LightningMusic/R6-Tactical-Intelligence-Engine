import csv
import json
from datetime import datetime
from pathlib import Path
from typing import Optional

from app.config import REPORTS_DIR


class ReportGenerator:
    """
    Generates match reports in CSV, HTML, and TXT formats.
    All output goes to data/reports/.
    """

    def __init__(self) -> None:
        REPORTS_DIR.mkdir(parents=True, exist_ok=True)

    # =====================================================
    # PUBLIC ENTRY POINT
    # =====================================================

    def generate_match_report(self, match_id: int) -> str:
        """
        Generates HTML, CSV, and TXT reports for a match.
        Returns the path to the HTML report (primary deliverable).
        """
        from database.repositories import Repository
        from analysis.metrics_engine import MetricsEngine

        repo  = Repository()
        match = repo.get_match_full(match_id)

        if match is None:
            raise ValueError(f"Match {match_id} not found.")

        engine  = MetricsEngine(match)
        summary = engine.player_summary()
        tps     = engine.tactical_performance_score()

        metrics = {
            "win_rate":                 engine.win_rate(),
            "attack_win_rate":          engine.attack_win_rate(),
            "defense_win_rate":         engine.defense_win_rate(),
            "engagement_win_rate":      engine.average_team_engagement_win_rate(),
            "drone_efficiency":         engine.drone_efficiency(),
            "reinforcement_usage_rate": engine.reinforcement_usage_rate(),
            "man_advantage_conversion": engine.man_advantage_conversion(),
            "clutch_rate":              engine.clutch_rate(),
        }

        stem = f"match_{match_id}_{datetime.now().strftime('%Y%m%d_%H%M%S')}"

        html_path = self._write_html(match, metrics, summary, tps, stem)
        self._write_csv(match, summary, tps, stem)
        self._write_txt(match, metrics, summary, tps, stem)

        return str(html_path)

    # =====================================================
    # HTML REPORT
    # =====================================================

    def _write_html(self, match, metrics: dict, summary: dict, tps: dict, stem: str) -> Path:
        wins   = sum(1 for r in match.rounds if r.outcome == "win")
        losses = sum(1 for r in match.rounds if r.outcome == "loss")
        result = (match.result or "In Progress").upper()

        player_rows = ""
        for pid, data in summary.items():
            name = data["player"].name
            score = tps.get(pid, 0.0)
            player_rows += f"""
            <tr>
                <td>{name}</td>
                <td>{data['kills']}</td>
                <td>{data['deaths']}</td>
                <td>{data['assists']}</td>
                <td>{data['kd_ratio']:.2f}</td>
                <td>{data['engagement_win_rate']:.0%}</td>
                <td>{data['survival_rate']:.0%}</td>
                <td>{data['utility_efficiency']:.0%}</td>
                <td>{data['plant_success_rate']:.0%}</td>
                <td><strong>{score:.3f}</strong></td>
            </tr>"""

        round_rows = ""
        for r in match.rounds:
            k = sum(p.kills  for p in r.player_stats)
            d = sum(p.deaths for p in r.player_stats)
            color = "#2a5c2a" if r.outcome == "win" else "#5c2a2a"
            round_rows += f"""
            <tr style="background:{color}">
                <td>{r.round_number}</td>
                <td>{r.side.capitalize()}</td>
                <td>{r.site or '—'}</td>
                <td>{r.outcome.capitalize()}</td>
                <td>{k} / {d}</td>
            </tr>"""

        html = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<title>R6 Match Report — {match.opponent_name}</title>
<style>
  body {{ font-family: 'Segoe UI', sans-serif; background: #1a1a1a;
         color: #ddd; padding: 32px; max-width: 1100px; margin: auto; }}
  h1   {{ color: #fff; border-bottom: 2px solid #444; padding-bottom: 8px; }}
  h2   {{ color: #aaa; margin-top: 32px; }}
  .meta {{ display: flex; gap: 32px; margin: 16px 0; }}
  .meta span {{ background: #2a2a2a; padding: 8px 16px; border-radius: 6px; }}
  .result-win  {{ color: #55e07a; font-weight: bold; }}
  .result-loss {{ color: #e05555; font-weight: bold; }}
  table {{ width: 100%; border-collapse: collapse; margin-top: 12px; }}
  th    {{ background: #333; padding: 10px 12px; text-align: left;
           font-weight: bold; color: #fff; }}
  td    {{ padding: 8px 12px; border-bottom: 1px solid #333; }}
  tr:hover td {{ background: #252525; }}
</style>
</head>
<body>
<h1>R6 Tactical Intelligence — Match Report</h1>
<div class="meta">
  <span>vs <strong>{match.opponent_name}</strong></span>
  <span>Map: <strong>{match.map}</strong></span>
  <span>Score: <strong>{wins} – {losses}</strong></span>
  <span>Result: <span class="result-{'win' if match.result == 'win' else 'loss'}">{result}</span></span>
  <span>Date: {match.datetime_played.strftime('%Y-%m-%d %H:%M')}</span>
</div>

<h2>Match Metrics</h2>
<table>
  <tr><th>Metric</th><th>Value</th></tr>
  <tr><td>Win Rate</td><td>{metrics['win_rate']:.0%}</td></tr>
  <tr><td>Attack Win Rate</td><td>{metrics['attack_win_rate']:.0%}</td></tr>
  <tr><td>Defense Win Rate</td><td>{metrics['defense_win_rate']:.0%}</td></tr>
  <tr><td>Engagement Win Rate</td><td>{metrics['engagement_win_rate']:.0%}</td></tr>
  <tr><td>Drone Efficiency (Attack)</td><td>{metrics['drone_efficiency']:.0%}</td></tr>
  <tr><td>Reinforcement Usage (Defense)</td><td>{metrics['reinforcement_usage_rate']:.0%}</td></tr>
  <tr><td>Man Advantage Conversion</td><td>{metrics['man_advantage_conversion']:.0%}</td></tr>
  <tr><td>Clutch Rate</td><td>{metrics['clutch_rate']:.0%}</td></tr>
</table>

<h2>Player Performance</h2>
<table>
  <tr>
    <th>Player</th><th>K</th><th>D</th><th>A</th>
    <th>K/D</th><th>Eng Win%</th><th>Survival%</th>
    <th>Utility%</th><th>Plant%</th><th>TPS</th>
  </tr>
  {player_rows}
</table>

<h2>Round Breakdown</h2>
<table>
  <tr><th>Round</th><th>Side</th><th>Site</th><th>Outcome</th><th>K/D</th></tr>
  {round_rows}
</table>

<p style="color:#555; margin-top:40px; font-size:12px;">
  Generated by R6 Tactical Intelligence Engine —
  {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}
</p>
</body>
</html>"""

        path = REPORTS_DIR / f"{stem}.html"
        path.write_text(html, encoding="utf-8")
        print(f"[Report] HTML → {path}")
        return path

    # =====================================================
    # CSV REPORT
    # =====================================================

    def _write_csv(self, match, summary: dict, tps: dict, stem: str) -> Path:
        path = REPORTS_DIR / f"{stem}.csv"

        with open(path, "w", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)

            writer.writerow([
                "Player", "Kills", "Deaths", "Assists",
                "K/D", "Eng Win%", "Survival%",
                "Ability Eff%", "Gadget Eff%", "Utility%",
                "Plant Success%", "Rounds Played", "TPS"
            ])

            for pid, data in summary.items():
                writer.writerow([
                    data["player"].name,
                    data["kills"],
                    data["deaths"],
                    data["assists"],
                    round(data["kd_ratio"], 2),
                    f"{data['engagement_win_rate']:.0%}",
                    f"{data['survival_rate']:.0%}",
                    f"{data['ability_efficiency']:.0%}",
                    f"{data['gadget_efficiency']:.0%}",
                    f"{data['utility_efficiency']:.0%}",
                    f"{data['plant_success_rate']:.0%}",
                    data["rounds_played"],
                    tps.get(pid, 0.0),
                ])

        print(f"[Report] CSV  → {path}")
        return path

    # =====================================================
    # TXT REPORT
    # =====================================================

    def _write_txt(self, match, metrics: dict, summary: dict, tps: dict, stem: str) -> Path:
        path = REPORTS_DIR / f"{stem}.txt"
        wins   = sum(1 for r in match.rounds if r.outcome == "win")
        losses = sum(1 for r in match.rounds if r.outcome == "loss")

        lines = [
            "=" * 60,
            "  R6 TACTICAL INTELLIGENCE ENGINE — MATCH REPORT",
            "=" * 60,
            f"  vs {match.opponent_name}  |  Map: {match.map}",
            f"  Score: {wins}–{losses}  |  Result: {(match.result or 'In Progress').upper()}",
            f"  Date:  {match.datetime_played.strftime('%Y-%m-%d %H:%M')}",
            "",
            "── MATCH METRICS " + "─" * 43,
            f"  Win Rate:                  {metrics['win_rate']:.0%}",
            f"  Attack Win Rate:           {metrics['attack_win_rate']:.0%}",
            f"  Defense Win Rate:          {metrics['defense_win_rate']:.0%}",
            f"  Engagement Win Rate:       {metrics['engagement_win_rate']:.0%}",
            f"  Drone Efficiency:          {metrics['drone_efficiency']:.0%}",
            f"  Reinforcement Usage:       {metrics['reinforcement_usage_rate']:.0%}",
            f"  Man Advantage Conversion:  {metrics['man_advantage_conversion']:.0%}",
            f"  Clutch Rate:               {metrics['clutch_rate']:.0%}",
            "",
            "── PLAYER PERFORMANCE " + "─" * 38,
        ]

        for pid, data in summary.items():
            name  = data["player"].name
            score = tps.get(pid, 0.0)
            lines += [
                f"  {name}",
                f"    K/D/A:      {data['kills']} / {data['deaths']} / {data['assists']}  "
                f"(K/D: {data['kd_ratio']:.2f})",
                f"    Eng Win%:   {data['engagement_win_rate']:.0%}   "
                f"Survival: {data['survival_rate']:.0%}",
                f"    Utility%:   {data['utility_efficiency']:.0%}   "
                f"TPS: {score:.3f}",
                "",
            ]

        lines += [
            "── ROUNDS " + "─" * 50,
        ]
        for r in match.rounds:
            k = sum(p.kills  for p in r.player_stats)
            d = sum(p.deaths for p in r.player_stats)
            lines.append(
                f"  R{r.round_number:02d}  {r.side.capitalize():<8}  "
                f"{(r.site or '—'):<28}  {r.outcome.upper():<4}  K/D {k}/{d}"
            )

        lines += [
            "",
            "=" * 60,
            f"  Generated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
            "=" * 60,
        ]

        path.write_text("\n".join(lines), encoding="utf-8")
        print(f"[Report] TXT  → {path}")
        return path