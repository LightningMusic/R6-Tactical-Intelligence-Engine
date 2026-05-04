"""
gui/dashboard_view.py

Landing dashboard showing team performance at a glance.
Loads from the database on demand — no live polling needed.
"""
from __future__ import annotations

from datetime import datetime
from typing import Optional

from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QTableWidget, QTableWidgetItem, QHeaderView, QFrame,
    QScrollArea, QSizePolicy, QGridLayout, QSpacerItem
)
from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QColor, QFont


# ─────────────────────────────────────────────────────────────────────────────
# SMALL STAT CARD WIDGET
# ─────────────────────────────────────────────────────────────────────────────

class _StatCard(QFrame):
    def __init__(
        self,
        title: str,
        value: str,
        sub: str = "",
        color: str = "#55e07a",
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)
        self.setFrameShape(QFrame.Shape.StyledPanel)
        self.setStyleSheet(f"""
            QFrame {{
                background: #1e1e1e;
                border: 1px solid #333;
                border-radius: 8px;
                padding: 4px;
            }}
        """)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.setMinimumHeight(90)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 10, 14, 10)
        layout.setSpacing(2)

        title_lbl = QLabel(title)
        title_lbl.setStyleSheet("color: #888; font-size: 11px; font-weight: bold; letter-spacing: 1px;")
        layout.addWidget(title_lbl)

        self._value_lbl = QLabel(value)
        self._value_lbl.setStyleSheet(f"color: {color}; font-size: 26px; font-weight: bold;")
        layout.addWidget(self._value_lbl)

        if sub:
            sub_lbl = QLabel(sub)
            sub_lbl.setStyleSheet("color: #666; font-size: 10px;")
            layout.addWidget(sub_lbl)

        layout.addStretch()

    def set_value(self, value: str, color: str = "") -> None:
        self._value_lbl.setText(value)
        if color:
            current = self._value_lbl.styleSheet()
            # Replace colour
            import re
            updated = re.sub(r"color: #[0-9a-fA-F]+;", f"color: {color};", current)
            self._value_lbl.setStyleSheet(updated)


# ─────────────────────────────────────────────────────────────────────────────
# DASHBOARD VIEW
# ─────────────────────────────────────────────────────────────────────────────

class DashboardView(QWidget):
    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._build_ui()
        # Auto-refresh when tab becomes visible
        self._refresh_timer = QTimer(self)
        self._refresh_timer.setSingleShot(True)
        self._refresh_timer.timeout.connect(self.refresh)

    def showEvent(self, event) -> None:  # type: ignore[override]
        super().showEvent(event)
        # Defer refresh slightly so the tab finishes painting first
        self._refresh_timer.start(50)

    # ─────────────────────────────────────────────────────────────────────────
    # UI BUILD
    # ─────────────────────────────────────────────────────────────────────────

    def _build_ui(self) -> None:
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        # Scrollable content area
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setStyleSheet("QScrollArea { background: transparent; border: none; }")

        content = QWidget()
        content.setStyleSheet("background: #121212;")
        self._layout = QVBoxLayout(content)
        self._layout.setContentsMargins(24, 20, 24, 24)
        self._layout.setSpacing(20)

        # ── Header row ────────────────────────────────────────────
        header_row = QHBoxLayout()
        title = QLabel("Team Dashboard")
        title.setStyleSheet("font-size: 24px; font-weight: bold; color: #fff;")
        header_row.addWidget(title)
        header_row.addStretch()

        self._last_updated = QLabel("")
        self._last_updated.setStyleSheet("font-size: 10px; color: #555;")
        header_row.addWidget(self._last_updated)

        refresh_btn = QPushButton("↺  Refresh")
        refresh_btn.setFixedHeight(30)
        refresh_btn.setFixedWidth(90)
        refresh_btn.setStyleSheet(
            "QPushButton { background: #1e1e1e; border: 1px solid #444; "
            "color: #aaa; border-radius: 4px; font-size: 11px; }"
            "QPushButton:hover { background: #2a2a2a; color: #fff; }"
        )
        refresh_btn.clicked.connect(self.refresh)
        header_row.addWidget(refresh_btn)
        self._layout.addLayout(header_row)

        # ── Stat cards row ────────────────────────────────────────
        cards_grid = QGridLayout()
        cards_grid.setSpacing(12)

        self._card_matches   = _StatCard("MATCHES PLAYED", "—", color="#55e07a")
        self._card_winrate   = _StatCard("OVERALL WIN RATE", "—", color="#55e07a")
        self._card_atk       = _StatCard("ATTACK WIN RATE", "—", color="#5599e0")
        self._card_def       = _StatCard("DEFENSE WIN RATE", "—", color="#e09955")
        self._card_ewr       = _StatCard("AVG ENGAGEMENT WIN%", "—", color="#aa55e0")
        self._card_streak    = _StatCard("CURRENT STREAK", "—", color="#e05555")

        cards_grid.addWidget(self._card_matches,  0, 0)
        cards_grid.addWidget(self._card_winrate,  0, 1)
        cards_grid.addWidget(self._card_atk,      0, 2)
        cards_grid.addWidget(self._card_def,      1, 0)
        cards_grid.addWidget(self._card_ewr,      1, 1)
        cards_grid.addWidget(self._card_streak,   1, 2)
        self._layout.addLayout(cards_grid)

        # ── Section: Recent Matches ───────────────────────────────
        self._layout.addWidget(self._section_label("RECENT MATCHES"))
        self._matches_table = self._make_table(
            ["#", "Date", "Opponent", "Map", "Score", "Result", "ATK%", "DEF%"],
            stretch_col=2,
        )
        self._matches_table.setMaximumHeight(220)
        self._layout.addWidget(self._matches_table)

        # ── Section: Player Leaderboard ───────────────────────────
        self._layout.addWidget(self._section_label("PLAYER LEADERBOARD  (all matches)"))
        self._players_table = self._make_table(
            ["Player", "Matches", "K", "D", "A", "K/D", "Eng Win%", "Survival%", "TPS"],
            stretch_col=0,
        )
        self._players_table.setMaximumHeight(200)
        self._layout.addWidget(self._players_table)

        # ── Section: Most-Played Maps ─────────────────────────────
        self._layout.addWidget(self._section_label("MAP PERFORMANCE"))
        self._maps_table = self._make_table(
            ["Map", "Played", "Wins", "Win%", "ATK Win%", "DEF Win%"],
            stretch_col=0,
        )
        self._maps_table.setMaximumHeight(180)
        self._layout.addWidget(self._maps_table)

        # ── Section: Most-Played Operators ───────────────────────
        self._layout.addWidget(self._section_label("OPERATOR USAGE  (team picks, replay data)"))
        self._ops_table = self._make_table(
            ["Operator", "Side", "Rounds Played", "Wins", "Win%"],
            stretch_col=0,
        )
        self._ops_table.setMaximumHeight(180)
        self._layout.addWidget(self._ops_table)

        self._layout.addStretch()

        scroll.setWidget(content)
        outer.addWidget(scroll)

    # ─────────────────────────────────────────────────────────────────────────
    # HELPERS
    # ─────────────────────────────────────────────────────────────────────────

    def _section_label(self, text: str) -> QLabel:
        lbl = QLabel(text)
        lbl.setStyleSheet(
            "color: #666; font-size: 10px; font-weight: bold; "
            "letter-spacing: 1px; padding-top: 4px;"
        )
        return lbl

    def _make_table(self, headers: list[str], stretch_col: int = 0) -> QTableWidget:
        t = QTableWidget(0, len(headers))
        t.setHorizontalHeaderLabels(headers)
        t.verticalHeader().setVisible(False)
        t.setAlternatingRowColors(True)
        t.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        t.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        t.setShowGrid(False)
        t.horizontalHeader().setSectionResizeMode(
            stretch_col, QHeaderView.ResizeMode.Stretch
        )
        for i in range(len(headers)):
            if i != stretch_col:
                t.horizontalHeader().setSectionResizeMode(
                    i, QHeaderView.ResizeMode.ResizeToContents
                )
        t.setStyleSheet("""
            QTableWidget { background: #1a1a1a; border: 1px solid #2a2a2a; border-radius: 6px; }
            QTableWidget::item { padding: 5px 8px; color: #ddd; }
            QTableWidget::item:alternate { background: #1e1e1e; }
            QHeaderView::section { background: #222; color: #888; padding: 6px 8px;
                font-size: 10px; font-weight: bold; border: none; border-bottom: 1px solid #333; }
        """)
        return t

    def _cell(self, text: str, color: str = "") -> QTableWidgetItem:
        item = QTableWidgetItem(str(text))
        item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
        if color:
            item.setForeground(QColor(color))
        return item

    # ─────────────────────────────────────────────────────────────────────────
    # DATA LOADING
    # ─────────────────────────────────────────────────────────────────────────

    def refresh(self) -> None:
        try:
            from database.repositories import Repository
            from analysis.metrics_engine import MetricsEngine
            repo = Repository()
            matches = repo.get_all_matches()

            if not matches:
                self._show_empty()
                return

            # Load full match objects (with rounds + player stats)
            full_matches = []
            for m in matches:
                try:
                    fm = repo.get_match_full(m.match_id)
                    if fm is not None:
                        full_matches.append(fm)
                except Exception:
                    pass

            self._populate_cards(full_matches)
            self._populate_recent_matches(full_matches[-10:][::-1])  # 10 most recent
            self._populate_player_leaderboard(full_matches, MetricsEngine)
            self._populate_map_stats(full_matches)
            self._populate_operator_stats(full_matches)

            self._last_updated.setText(
                f"Last updated: {datetime.now().strftime('%H:%M:%S')}"
            )

        except Exception as e:
            self._last_updated.setText(f"Error loading data: {e}")

    def _show_empty(self) -> None:
        self._card_matches.set_value("0")
        self._card_winrate.set_value("—")
        self._card_atk.set_value("—")
        self._card_def.set_value("—")
        self._card_ewr.set_value("—")
        self._card_streak.set_value("—")
        self._last_updated.setText("No matches recorded yet.")

    def _populate_cards(self, matches: list) -> None:
        from analysis.metrics_engine import MetricsEngine

        completed = [m for m in matches if m.result in ("win", "loss")]
        n = len(completed)

        if n == 0:
            self._show_empty()
            return

        wins = sum(1 for m in completed if m.result == "win")
        win_rate = wins / n

        # Aggregate metrics across all completed matches
        total_atk_rounds = total_atk_wins = 0
        total_def_rounds = total_def_wins = 0
        total_eng_taken = total_eng_won = 0

        for m in completed:
            for r in m.rounds:
                if r.side == "attack":
                    total_atk_rounds += 1
                    if r.outcome == "win":
                        total_atk_wins += 1
                else:
                    total_def_rounds += 1
                    if r.outcome == "win":
                        total_def_wins += 1
                for ps in r.player_stats:
                    total_eng_taken += ps.engagements_taken
                    total_eng_won   += ps.engagements_won

        atk_wr = total_atk_wins / total_atk_rounds if total_atk_rounds else 0
        def_wr = total_def_wins / total_def_rounds if total_def_rounds else 0
        ewr    = total_eng_won  / total_eng_taken  if total_eng_taken  else 0

        # Current streak
        streak_val = 0
        streak_type = ""
        for m in reversed(completed):
            if streak_val == 0:
                streak_type = m.result
                streak_val = 1
            elif m.result == streak_type:
                streak_val += 1
            else:
                break

        streak_color = "#55e07a" if streak_type == "win" else "#e05555"
        streak_text  = f"{'W' if streak_type == 'win' else 'L'}{streak_val}"

        wr_color = "#55e07a" if win_rate >= 0.5 else "#e05555"
        self._card_matches.set_value(str(n))
        self._card_winrate.set_value(f"{win_rate:.0%}", wr_color)
        self._card_atk.set_value(f"{atk_wr:.0%}")
        self._card_def.set_value(f"{def_wr:.0%}")
        self._card_ewr.set_value(f"{ewr:.0%}")
        self._card_streak.set_value(streak_text, streak_color)

    def _populate_recent_matches(self, matches: list) -> None:
        t = self._matches_table
        t.setRowCount(0)

        for m in matches:
            rounds = m.rounds
            atk_rounds = [r for r in rounds if r.side == "attack"]
            def_rounds = [r for r in rounds if r.side == "defense"]
            atk_wins = sum(1 for r in atk_rounds if r.outcome == "win")
            def_wins = sum(1 for r in def_rounds if r.outcome == "win")
            total_wins  = sum(1 for r in rounds if r.outcome == "win")
            total_losses = sum(1 for r in rounds if r.outcome == "loss")

            atk_pct = f"{atk_wins}/{len(atk_rounds)}" if atk_rounds else "—"
            def_pct = f"{def_wins}/{len(def_rounds)}" if def_rounds else "—"

            result_text = (m.result or "—").upper()
            result_color = "#55e07a" if m.result == "win" else "#e05555" if m.result == "loss" else "#888"

            row = t.rowCount()
            t.insertRow(row)
            t.setItem(row, 0, self._cell(str(m.match_id)))
            t.setItem(row, 1, self._cell(m.datetime_played.strftime("%m/%d %H:%M")))
            t.setItem(row, 2, self._cell(m.opponent_name or "—"))
            t.setItem(row, 3, self._cell(m.map or "—"))
            t.setItem(row, 4, self._cell(f"{total_wins}–{total_losses}"))
            t.setItem(row, 5, self._cell(result_text, result_color))
            t.setItem(row, 6, self._cell(atk_pct))
            t.setItem(row, 7, self._cell(def_pct))

    def _populate_player_leaderboard(self, matches: list, MetricsEngine) -> None:  # type: ignore[type-arg]
        t = self._players_table
        t.setRowCount(0)

        # Aggregate across all matches
        player_totals: dict[int, dict] = {}

        for m in matches:
            if not m.rounds:
                continue
            try:
                engine = MetricsEngine(m)
                summary = engine.player_summary()
                tps     = engine.tactical_performance_score()

                for pid, data in summary.items():
                    if pid not in player_totals:
                        player_totals[pid] = {
                            "name":            data["player"].name,
                            "matches":         0,
                            "kills":           0,
                            "deaths":          0,
                            "assists":         0,
                            "eng_taken":       0,
                            "eng_won":         0,
                            "rounds_survived": 0,
                            "rounds_played":   0,
                            "tps_scores":      [],
                        }
                    pt = player_totals[pid]
                    pt["matches"]         += 1
                    pt["kills"]           += data["kills"]
                    pt["deaths"]          += data["deaths"]
                    pt["assists"]         += data["assists"]
                    pt["eng_taken"]       += data["engagements_taken"]
                    pt["eng_won"]         += data["engagements_won"]
                    pt["rounds_survived"] += data.get("rounds_survived", 0)
                    pt["rounds_played"]   += data["rounds_played"]
                    pt["tps_scores"].append(tps.get(pid, 0.0))
            except Exception:
                continue

        if not player_totals:
            return

        # Sort by avg TPS descending
        sorted_players = sorted(
            player_totals.items(),
            key=lambda x: (
                sum(x[1]["tps_scores"]) / len(x[1]["tps_scores"])
                if x[1]["tps_scores"] else 0
            ),
            reverse=True,
        )

        for pid, pt in sorted_players:
            kd = pt["kills"] / pt["deaths"] if pt["deaths"] else float(pt["kills"])
            ewr = pt["eng_won"] / pt["eng_taken"] if pt["eng_taken"] else 0.0
            surv = pt["rounds_survived"] / pt["rounds_played"] if pt["rounds_played"] else 0.0
            avg_tps = sum(pt["tps_scores"]) / len(pt["tps_scores"]) if pt["tps_scores"] else 0.0

            row = t.rowCount()
            t.insertRow(row)
            t.setItem(row, 0, self._cell(pt["name"]))
            t.setItem(row, 1, self._cell(str(pt["matches"])))
            t.setItem(row, 2, self._cell(str(pt["kills"])))
            t.setItem(row, 3, self._cell(str(pt["deaths"])))
            t.setItem(row, 4, self._cell(str(pt["assists"])))
            t.setItem(row, 5, self._cell(f"{kd:.2f}"))
            t.setItem(row, 6, self._cell(f"{ewr:.0%}"))
            t.setItem(row, 7, self._cell(f"{surv:.0%}"))
            t.setItem(row, 8, self._cell(f"{avg_tps:.3f}"))

    def _populate_map_stats(self, matches: list) -> None:
        t = self._maps_table
        t.setRowCount(0)

        map_data: dict[str, dict] = {}

        for m in matches:
            if m.result not in ("win", "loss"):
                continue
            key = m.map or "Unknown"
            if key not in map_data:
                map_data[key] = {
                    "played": 0, "wins": 0,
                    "atk_rounds": 0, "atk_wins": 0,
                    "def_rounds": 0, "def_wins": 0,
                }
            md = map_data[key]
            md["played"] += 1
            if m.result == "win":
                md["wins"] += 1
            for r in m.rounds:
                if r.side == "attack":
                    md["atk_rounds"] += 1
                    if r.outcome == "win":
                        md["atk_wins"] += 1
                else:
                    md["def_rounds"] += 1
                    if r.outcome == "win":
                        md["def_wins"] += 1

        sorted_maps = sorted(map_data.items(), key=lambda x: x[1]["played"], reverse=True)

        for map_name, md in sorted_maps:
            wr  = md["wins"]      / md["played"]      if md["played"]      else 0
            awr = md["atk_wins"]  / md["atk_rounds"]  if md["atk_rounds"]  else 0
            dwr = md["def_wins"]  / md["def_rounds"]  if md["def_rounds"]  else 0

            wr_color = "#55e07a" if wr >= 0.5 else "#e05555"

            row = t.rowCount()
            t.insertRow(row)
            t.setItem(row, 0, self._cell(map_name))
            t.setItem(row, 1, self._cell(str(md["played"])))
            t.setItem(row, 2, self._cell(str(md["wins"])))
            t.setItem(row, 3, self._cell(f"{wr:.0%}", wr_color))
            t.setItem(row, 4, self._cell(f"{awr:.0%}"))
            t.setItem(row, 5, self._cell(f"{dwr:.0%}"))

    def _populate_operator_stats(self, matches: list) -> None:
        t = self._ops_table
        t.setRowCount(0)

        op_data: dict[str, dict] = {}

        for m in matches:
            for r in m.rounds:
                for ps in r.player_stats:
                    op_name = ps.operator.name
                    op_side = ps.operator.side
                    key = op_name
                    if key not in op_data:
                        op_data[key] = {
                            "side": op_side,
                            "rounds": 0,
                            "round_wins": 0,
                        }
                    op_data[key]["rounds"] += 1
                    if r.outcome == "win":
                        op_data[key]["round_wins"] += 1

        sorted_ops = sorted(op_data.items(), key=lambda x: x[1]["rounds"], reverse=True)[:20]

        for op_name, od in sorted_ops:
            wr = od["round_wins"] / od["rounds"] if od["rounds"] else 0
            wr_color = "#55e07a" if wr >= 0.5 else "#e05555"
            side_color = "#5599e0" if od["side"] == "attack" else "#e09955"

            row = t.rowCount()
            t.insertRow(row)
            t.setItem(row, 0, self._cell(op_name))
            t.setItem(row, 1, self._cell(od["side"].capitalize(), side_color))
            t.setItem(row, 2, self._cell(str(od["rounds"])))
            t.setItem(row, 3, self._cell(str(od["round_wins"])))
            t.setItem(row, 4, self._cell(f"{wr:.0%}", wr_color))