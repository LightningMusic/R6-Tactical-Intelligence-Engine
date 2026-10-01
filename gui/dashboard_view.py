"""
gui/dashboard_view.py

Landing dashboard: team form, maps, operators, players, and whether the
server and the upload queue are healthy. Data is loaded on a worker thread
and only when the database has actually changed, so switching to this tab
never freezes the window.
"""
from __future__ import annotations

import json
import threading
from datetime import datetime
from typing import Optional

from PySide6.QtCore import QObject, QPointF, QRectF, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import (
    QComboBox, QFrame, QGridLayout, QHBoxLayout, QHeaderView, QLabel, QPushButton,
    QScrollArea, QSizePolicy, QStyledItemDelegate, QTableWidget, QTableWidgetItem,
    QVBoxLayout, QWidget,
)

BG = "#0f1115"
PANEL = "#171a21"
PANEL_ALT = "#1b1f27"
BORDER = "#262b35"
TEXT = "#e6e8ec"
MUTED = "#8a93a3"
FAINT = "#5b6474"
GREEN = "#3ecf8e"
RED = "#ef5b5b"
AMBER = "#f2b84b"
BLUE = "#5b9cf2"
ORANGE = "#f28b4b"
PURPLE = "#a47cf2"

_RATE_ROLE = Qt.ItemDataRole.UserRole + 1


def _pct(v: Optional[float]) -> str:
    return "—" if v is None else f"{v:.0%}"


def _rate_color(v: Optional[float]) -> str:
    if v is None:
        return MUTED
    return GREEN if v >= 0.55 else RED if v < 0.45 else AMBER


# ─────────────────────────────────────────────────────────────────────────────
# SMALL WIDGETS
# ─────────────────────────────────────────────────────────────────────────────

class _Card(QFrame):
    def __init__(self, title: str, accent: str = GREEN, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setObjectName("card")
        self.setStyleSheet(
            f"#card {{ background: {PANEL}; border: 1px solid {BORDER}; border-radius: 10px; "
            f"border-top: 3px solid {accent}; }}"
        )
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(16, 12, 16, 12)
        lay.setSpacing(2)
        t = QLabel(title.upper())
        t.setStyleSheet(f"color: {MUTED}; font-size: 10px; font-weight: 600; letter-spacing: 1px; border: none;")
        self._value = QLabel("—")
        self._value.setStyleSheet(f"color: {TEXT}; font-size: 26px; font-weight: 700; border: none;")
        self._sub = QLabel("")
        self._sub.setStyleSheet(f"color: {FAINT}; font-size: 11px; border: none;")
        lay.addWidget(t)
        lay.addWidget(self._value)
        lay.addWidget(self._sub)

    def set(self, value: str, sub: str = "", color: str = TEXT) -> None:
        self._value.setText(value)
        self._value.setStyleSheet(f"color: {color}; font-size: 26px; font-weight: 700; border: none;")
        self._sub.setText(sub)


class _Pill(QLabel):
    def set_state(self, text: str, color: str) -> None:
        self.setText(f"●  {text}")
        self.setStyleSheet(
            f"color: {color}; background: {PANEL}; border: 1px solid {BORDER}; "
            f"border-radius: 11px; padding: 3px 10px; font-size: 11px; font-weight: 600;"
        )


class _FormStrip(QWidget):
    """Last-10 results as coloured tiles, oldest on the left."""

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._form: list[str] = []
        self.setFixedHeight(30)
        self.setMinimumWidth(10 * 30)

    def set_form(self, form: list[str]) -> None:
        self._form = form
        self.update()

    def paintEvent(self, _event) -> None:  # type: ignore[override]
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        size, gap = 26, 5
        for i, res in enumerate(self._form):
            r = QRectF(i * (size + gap), 2, size, size)
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor(GREEN if res == "win" else RED))
            p.drawRoundedRect(r, 6, 6)
            p.setPen(QColor("#0b0d10"))
            f = p.font()
            f.setBold(True)
            p.setFont(f)
            p.drawText(r, Qt.AlignmentFlag.AlignCenter, "W" if res == "win" else "L")
        if not self._form:
            p.setPen(QColor(FAINT))
            p.drawText(self.rect(), Qt.AlignmentFlag.AlignVCenter, "No decided matches yet")


class _Sparkline(QWidget):
    """TPS per match, dots coloured by the match result."""

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._rows: list[dict] = []
        self.setMinimumHeight(110)
        self.setStyleSheet(f"background: {PANEL}; border: 1px solid {BORDER}; border-radius: 10px;")

    def set_rows(self, rows: list[dict]) -> None:
        self._rows = rows
        self.update()

    def paintEvent(self, _event) -> None:  # type: ignore[override]
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setPen(QPen(QColor(BORDER), 1))
        p.setBrush(QColor(PANEL))
        p.drawRoundedRect(QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5), 10, 10)
        vals = [r["tps"] for r in self._rows]
        if len(vals) < 2:
            p.setPen(QColor(FAINT))
            p.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, "Trend appears after 2+ matches")
            return
        left, right, top, bottom = 44, 14, 14, 20
        w = self.width() - left - right
        h = self.height() - top - bottom
        lo, hi = min(vals), max(vals)
        if hi - lo < 1e-9:
            lo, hi = lo - 0.05, hi + 0.05

        def pt(i: int, v: float) -> QPointF:
            return QPointF(left + w * i / (len(vals) - 1), top + h * (1 - (v - lo) / (hi - lo)))

        p.setPen(QColor(FAINT))
        f = p.font()
        f.setPointSize(8)
        p.setFont(f)
        for v in (lo, hi):
            p.drawText(QRectF(0, pt(0, v).y() - 8, left - 6, 16),
                       Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter, f"{v:.2f}")
        path = QPainterPath(pt(0, vals[0]))
        for i, v in enumerate(vals[1:], 1):
            path.lineTo(pt(i, v))
        p.setPen(QPen(QColor(PURPLE), 2))
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawPath(path)
        p.setPen(Qt.PenStyle.NoPen)
        for i, r in enumerate(self._rows):
            p.setBrush(QColor(GREEN if r["result"] == "win" else RED))
            p.drawEllipse(pt(i, r["tps"]), 3.5, 3.5)


class _RateBarDelegate(QStyledItemDelegate):
    """Draws a thin win-rate bar under cells that carry a rate."""

    def paint(self, painter, option, index) -> None:  # type: ignore[override]
        rate = index.data(_RATE_ROLE)
        if rate is not None:
            painter.save()
            r = option.rect.adjusted(8, option.rect.height() - 6, -8, -3)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor(BORDER))
            painter.drawRoundedRect(r, 1.5, 1.5)
            filled = QRectF(r)
            filled.setWidth(r.width() * max(0.0, min(1.0, rate)))
            painter.setBrush(QColor(_rate_color(rate)))
            painter.drawRoundedRect(filled, 1.5, 1.5)
            painter.restore()
        super().paint(painter, option, index)


# ─────────────────────────────────────────────────────────────────────────────
# BACKGROUND LOADING
# ─────────────────────────────────────────────────────────────────────────────

class _Signals(QObject):
    data_ready = Signal(object)
    data_failed = Signal(str)
    status_ready = Signal(object)


def _load_dashboard(force: bool, last_signature) -> dict:
    from analysis.dashboard_stats import build_dashboard
    from database.repositories import Repository

    repo = Repository()
    sig = repo.data_signature()
    if not force and sig == last_signature:
        return {"unchanged": True, "signature": sig}
    matches = repo.get_all_matches_full()
    roster = [(p.player_id, p.name) for p in repo.get_team_players()]
    vm = build_dashboard(matches, {pid for pid, _ in roster}, roster)
    vm["roster"] = roster
    vm["unlinked"] = repo.get_frequent_unlinked_players()
    vm["signature"] = sig
    return vm


def _load_connection_status() -> dict:
    from app.config import settings
    from app.upload_queue import QUEUE_FILE
    from app.uploader import SessionUploader

    status: dict = {"configured": bool(settings.SERVER_URL) and settings.ANALYSIS_MODE != "local"}
    if status["configured"]:
        res = SessionUploader().test_connection()
        status["online"] = res.success
        status["error"] = res.error
    counts = {"uploaded": 0, "waiting": 0, "failed": 0}
    try:
        data = json.loads(QUEUE_FILE.read_text(encoding="utf-8"))
        items = data.get("items", data) if isinstance(data, dict) else data
        items = list(items.values()) if isinstance(items, dict) else items
        for it in items:
            st = it.get("package_status")
            if st == "uploaded":
                counts["uploaded"] += 1
            elif st == "upload_failed":
                counts["failed"] += 1
            elif st in ("pending_upload", "uploading", "partial_data"):
                counts["waiting"] += 1
    except (OSError, ValueError):
        pass
    status["queue"] = counts
    return status


# ─────────────────────────────────────────────────────────────────────────────
# DASHBOARD VIEW
# ─────────────────────────────────────────────────────────────────────────────

class DashboardView(QWidget):
    STATUS_INTERVAL_MS = 60_000
    PLAYER_ROWS = 12

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._vm: dict = {}
        self._signature = None
        self._loading = False
        self._checking = False
        self._signals = _Signals(self)
        self._signals.data_ready.connect(self._on_data)
        self._signals.data_failed.connect(self._on_failed)
        self._signals.status_ready.connect(self._on_status)
        self._build_ui()

        self._status_timer = QTimer(self)
        self._status_timer.setInterval(self.STATUS_INTERVAL_MS)
        self._status_timer.timeout.connect(self._check_connection)

    def showEvent(self, event) -> None:  # type: ignore[override]
        super().showEvent(event)
        QTimer.singleShot(0, lambda: self.refresh(force=False))
        self._check_connection()
        self._status_timer.start()

    def hideEvent(self, event) -> None:  # type: ignore[override]
        super().hideEvent(event)
        self._status_timer.stop()

    # ─────────────────────────────────────────────────────────────────────────
    # UI BUILD
    # ─────────────────────────────────────────────────────────────────────────

    def _build_ui(self) -> None:
        self.setStyleSheet(f"DashboardView {{ background: {BG}; }}")
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        content = QWidget()
        content.setObjectName("dashContent")
        content.setStyleSheet(f"#dashContent {{ background: {BG}; }} QLabel {{ color: {TEXT}; }}")
        lay = QVBoxLayout(content)
        lay.setContentsMargins(24, 20, 24, 28)
        lay.setSpacing(16)

        # Header
        head = QHBoxLayout()
        titles = QVBoxLayout()
        titles.setSpacing(0)
        title = QLabel("Team Dashboard")
        title.setStyleSheet(f"font-size: 24px; font-weight: 700; color: {TEXT};")
        self._subtitle = QLabel("Loading…")
        self._subtitle.setStyleSheet(f"font-size: 12px; color: {MUTED};")
        titles.addWidget(title)
        titles.addWidget(self._subtitle)
        head.addLayout(titles)
        head.addStretch()
        self._server_pill = _Pill()
        self._server_pill.set_state("Server: checking…", MUTED)
        self._queue_pill = _Pill()
        self._queue_pill.set_state("Uploads: —", MUTED)
        head.addWidget(self._server_pill)
        head.addWidget(self._queue_pill)
        refresh = QPushButton("↻  Refresh")
        refresh.setCursor(Qt.CursorShape.PointingHandCursor)
        refresh.setStyleSheet(
            f"QPushButton {{ background: {PANEL}; border: 1px solid {BORDER}; color: {TEXT}; "
            f"border-radius: 6px; padding: 6px 14px; font-size: 12px; }}"
            f"QPushButton:hover {{ background: {PANEL_ALT}; border-color: {BLUE}; }}"
        )
        refresh.clicked.connect(lambda: (self.refresh(force=True), self._check_connection()))
        head.addWidget(refresh)
        lay.addLayout(head)

        # Unlinked-teammate hint
        self._unlinked = QLabel("")
        self._unlinked.setWordWrap(True)
        self._unlinked.setStyleSheet(
            f"background: #2a2314; color: {AMBER}; border: 1px solid #4a3b1a; "
            f"border-radius: 8px; padding: 9px 12px; font-size: 12px;"
        )
        self._unlinked.hide()
        lay.addWidget(self._unlinked)

        # KPI cards
        cards = QGridLayout()
        cards.setSpacing(12)
        self._c_record = _Card("Record", GREEN)
        self._c_winrate = _Card("Match win rate", GREEN)
        self._c_rounds = _Card("Round win rate", PURPLE)
        self._c_atk = _Card("Attack", BLUE)
        self._c_def = _Card("Defense", ORANGE)
        self._c_streak = _Card("Current streak", RED)
        for i, c in enumerate((self._c_record, self._c_winrate, self._c_rounds,
                               self._c_atk, self._c_def, self._c_streak)):
            cards.addWidget(c, i // 3, i % 3)
        lay.addLayout(cards)

        # Form
        form_row = QHBoxLayout()
        form_row.addWidget(self._section("Last 10 matches"))
        form_row.addSpacing(12)
        self._form = _FormStrip()
        form_row.addWidget(self._form)
        form_row.addStretch()
        lay.addLayout(form_row)

        # Recent matches | Maps
        two = QHBoxLayout()
        two.setSpacing(16)
        self._recent = self._table(["Date", "Map", "Rounds", "ATK", "DEF", "Result"], stretch=1)
        self._maps = self._table(["Map", "Played", "W–L", "Win %", "ATK %", "DEF %"], stretch=0)
        two.addLayout(self._panel("Recent matches", self._recent), 3)
        two.addLayout(self._panel("Map performance", self._maps), 2)
        lay.addLayout(two)

        # Leaderboard
        self._board = self._table(["Player", "Matches", "Kills", "Deaths", "Assists", "K/D", "Survival", "Avg TPS"], stretch=0)
        lay.addLayout(self._panel("Player leaderboard  ·  linked teammates", self._board))

        # Player detail
        detail_head = QHBoxLayout()
        detail_head.addWidget(self._section("Player detail"))
        detail_head.addSpacing(12)
        self._player_combo = QComboBox()
        self._player_combo.setMinimumWidth(220)
        self._player_combo.setStyleSheet(
            f"QComboBox {{ background: {PANEL}; color: {TEXT}; border: 1px solid {BORDER}; "
            f"border-radius: 6px; padding: 4px 8px; }}"
        )
        self._player_combo.currentIndexChanged.connect(self._render_player)
        detail_head.addWidget(self._player_combo)
        detail_head.addStretch()
        lay.addLayout(detail_head)

        pcards = QGridLayout()
        pcards.setSpacing(12)
        self._p_matches = _Card("Matches", GREEN)
        self._p_tps = _Card("Avg TPS", PURPLE)
        self._p_trend = _Card("Recent trend", BLUE)
        self._p_consistency = _Card("Consistency", ORANGE)
        self._p_consistency.setToolTip(
            "Spread of this player's TPS across matches — lower means steadier performance."
        )
        for i, c in enumerate((self._p_matches, self._p_tps, self._p_trend, self._p_consistency)):
            pcards.addWidget(c, 0, i)
        lay.addLayout(pcards)
        self._spark = _Sparkline()
        lay.addWidget(self._spark)
        self._p_table = self._table(["Date", "Map", "Result", "K–D–A", "K/D", "Survival", "TPS"], stretch=1)
        lay.addWidget(self._p_table)
        self._p_more = QLabel("")
        self._p_more.setStyleSheet(f"color: {FAINT}; font-size: 11px;")
        lay.addWidget(self._p_more)

        # Operators
        ops = QHBoxLayout()
        ops.setSpacing(16)
        self._ops_atk = self._table(["Operator", "Rounds", "Won", "Win %"], stretch=0)
        self._ops_def = self._table(["Operator", "Rounds", "Won", "Win %"], stretch=0)
        ops.addLayout(self._panel("Attack picks  ·  your team", self._ops_atk), 1)
        ops.addLayout(self._panel("Defense picks  ·  your team", self._ops_def), 1)
        lay.addLayout(ops)

        lay.addStretch()
        scroll.setWidget(content)
        outer.addWidget(scroll)

    def _section(self, text: str) -> QLabel:
        lbl = QLabel(text.upper())
        lbl.setStyleSheet(f"color: {MUTED}; font-size: 11px; font-weight: 600; letter-spacing: 1px;")
        return lbl

    def _panel(self, title: str, table: QTableWidget) -> QVBoxLayout:
        v = QVBoxLayout()
        v.setSpacing(8)
        v.addWidget(self._section(title))
        v.addWidget(table)
        return v

    def _table(self, headers: list[str], stretch: int) -> QTableWidget:
        t = QTableWidget(0, len(headers))
        t.setHorizontalHeaderLabels(headers)
        t.verticalHeader().setVisible(False)
        t.verticalHeader().setDefaultSectionSize(30)
        t.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        t.setSelectionMode(QTableWidget.SelectionMode.NoSelection)
        t.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        t.setShowGrid(False)
        t.setAlternatingRowColors(True)
        t.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        t.setItemDelegate(_RateBarDelegate(t))
        hh = t.horizontalHeader()
        for i in range(len(headers)):
            hh.setSectionResizeMode(i, QHeaderView.ResizeMode.Stretch if i == stretch
                                    else QHeaderView.ResizeMode.ResizeToContents)
        t.setStyleSheet(
            f"QTableWidget {{ background: {PANEL}; alternate-background-color: {PANEL_ALT}; "
            f"border: 1px solid {BORDER}; border-radius: 10px; color: {TEXT}; }}"
            f"QTableWidget::item {{ padding: 0 10px; border: none; }}"
            f"QHeaderView::section {{ background: {PANEL}; color: {MUTED}; padding: 8px 10px; "
            f"font-size: 10px; font-weight: 600; border: none; border-bottom: 1px solid {BORDER}; }}"
        )
        return t

    @staticmethod
    def _cell(text, color: str = TEXT, rate: Optional[float] = None,
              align=Qt.AlignmentFlag.AlignCenter, tip: str = "") -> QTableWidgetItem:
        it = QTableWidgetItem(str(text))
        it.setTextAlignment(align | Qt.AlignmentFlag.AlignVCenter)
        it.setForeground(QColor(color))
        if rate is not None:
            it.setData(_RATE_ROLE, rate)
        if tip:
            it.setToolTip(tip)
        return it

    @staticmethod
    def _fit(t: QTableWidget, rows: int) -> None:
        """Show every row without an inner scrollbar; the page scrolls."""
        t.setRowCount(rows)
        h = t.horizontalHeader().height() + max(rows, 1) * t.verticalHeader().defaultSectionSize() + 4
        t.setFixedHeight(h)

    # ─────────────────────────────────────────────────────────────────────────
    # LOADING
    # ─────────────────────────────────────────────────────────────────────────

    def refresh(self, force: bool = True) -> None:
        if self._loading:
            return
        self._loading = True
        if not self._vm:
            self._subtitle.setText("Loading…")
        sig = self._signature

        def work() -> None:
            try:
                self._signals.data_ready.emit(_load_dashboard(force, sig))
            except Exception as e:  # shown in the header, never crashes the tab
                self._signals.data_failed.emit(str(e))

        threading.Thread(target=work, daemon=True, name="DashboardLoad").start()

    def _check_connection(self) -> None:
        if self._checking:
            return
        self._checking = True

        def work() -> None:
            try:
                self._signals.status_ready.emit(_load_connection_status())
            except Exception as e:
                self._signals.status_ready.emit({"configured": True, "online": False, "error": str(e), "queue": {}})

        threading.Thread(target=work, daemon=True, name="DashboardStatus").start()

    def _on_failed(self, msg: str) -> None:
        self._loading = False
        self._subtitle.setText(f"Could not load data: {msg}")

    def _on_status(self, st: dict) -> None:
        self._checking = False
        if not st.get("configured"):
            self._server_pill.set_state("Server: local mode", MUTED)
        elif st.get("online"):
            self._server_pill.set_state("Server online", GREEN)
            self._server_pill.setToolTip(f"Checked {datetime.now():%H:%M:%S}")
        else:
            self._server_pill.set_state("Server unreachable", RED)
            self._server_pill.setToolTip(str(st.get("error") or ""))
        q = st.get("queue") or {}
        pending = q.get("waiting", 0) + q.get("failed", 0)
        if pending:
            self._queue_pill.set_state(f"{pending} upload(s) waiting", AMBER)
            self._queue_pill.setToolTip(
                f"{q.get('waiting', 0)} queued, {q.get('failed', 0)} retrying. "
                f"They send on their own when the server is reachable."
            )
        elif q.get("uploaded"):
            self._queue_pill.set_state(f"All {q['uploaded']} uploads sent", GREEN)
            self._queue_pill.setToolTip("")
        else:
            self._queue_pill.set_state("No uploads yet", MUTED)
            self._queue_pill.setToolTip("")

    def _on_data(self, vm: dict) -> None:
        self._loading = False
        self._signature = vm.get("signature")
        if vm.get("unchanged"):
            return
        self._vm = vm
        self._render()

    # ─────────────────────────────────────────────────────────────────────────
    # RENDER
    # ─────────────────────────────────────────────────────────────────────────

    def _render(self) -> None:
        vm = self._vm
        if not vm.get("matches"):
            self._subtitle.setText("No matches recorded yet — they appear here after your first import.")
        else:
            last = vm["last_played"]
            self._subtitle.setText(
                f"{vm['matches']} matches  ·  last played {last:%b %d, %H:%M}  ·  updated {datetime.now():%H:%M:%S}"
            )

        names = [n for n, _ in vm.get("unlinked", [])]
        if names:
            listed = ", ".join(f"{n} ({c})" for n, c in vm["unlinked"][:4])
            self._unlinked.setText(
                f"⚠  Frequent players not linked to a teammate: {listed}.  Match counts in brackets — "
                f"link them in Settings › Players so their stats count in the leaderboard."
            )
            self._unlinked.show()
        else:
            self._unlinked.hide()

        d, w, l = vm.get("decided", 0), vm.get("wins", 0), vm.get("losses", 0)
        self._c_record.set(f"{w}–{l}" if d else "—", f"{d} of {vm.get('matches', 0)} matches decided")
        self._c_winrate.set(_pct(vm.get("win_rate")), "matches won", _rate_color(vm.get("win_rate")))
        rw, rt = vm.get("rounds_won", 0), vm.get("rounds_total", 0)
        rr = rw / rt if rt else None
        self._c_rounds.set(_pct(rr), f"{rw} of {rt} rounds", _rate_color(rr))
        for card, key in ((self._c_atk, "atk"), (self._c_def, "def")):
            won, tot = vm.get(key, (0, 0))
            rate = won / tot if tot else None
            card.set(_pct(rate), f"{won} of {tot} rounds won", _rate_color(rate))
        kind, n = vm.get("streak", (None, 0))
        self._c_streak.set(f"{'W' if kind == 'win' else 'L'}{n}" if kind else "—",
                           "wins in a row" if kind == "win" else "losses in a row" if kind else "",
                           GREEN if kind == "win" else RED if kind else TEXT)
        self._form.set_form(vm.get("form", []))

        self._render_recent(vm.get("recent", []))
        self._render_maps(vm.get("maps", []))
        self._render_board(vm.get("leaderboard", []))
        self._render_ops(self._ops_atk, vm.get("operators", {}).get("attack", [])[:10])
        self._render_ops(self._ops_def, vm.get("operators", {}).get("defense", [])[:10])
        self._fill_player_combo()

    def _render_recent(self, rows: list[dict]) -> None:
        t = self._recent
        self._fit(t, len(rows))
        for i, r in enumerate(rows):
            res = r["result"]
            label = (res or "draw/unknown").upper()
            color = GREEN if res == "win" else RED if res == "loss" else MUTED
            tip = "Result worked out from the round scores (older import)." if r["derived"] else ""
            t.setItem(i, 0, self._cell(f"{r['date']:%b %d  %H:%M}", MUTED))
            t.setItem(i, 1, self._cell(r["map"], align=Qt.AlignmentFlag.AlignLeft))
            t.setItem(i, 2, self._cell(f"{r['rounds_won']}–{r['rounds_lost']}"))
            aw, at = r["atk"]
            dw, dt = r["def"]
            t.setItem(i, 3, self._cell(f"{aw}/{at}" if at else "—", BLUE))
            t.setItem(i, 4, self._cell(f"{dw}/{dt}" if dt else "—", ORANGE))
            t.setItem(i, 5, self._cell(label + (" *" if r["derived"] else ""), color, tip=tip))

    def _render_maps(self, rows: list[dict]) -> None:
        t = self._maps
        self._fit(t, len(rows))
        for i, m in enumerate(rows):
            t.setItem(i, 0, self._cell(m["map"], align=Qt.AlignmentFlag.AlignLeft))
            t.setItem(i, 1, self._cell(m["played"]))
            t.setItem(i, 2, self._cell(f"{m['wins']}–{m['losses']}", MUTED))
            t.setItem(i, 3, self._cell(_pct(m["win_rate"]), _rate_color(m["win_rate"]), m["win_rate"]))
            t.setItem(i, 4, self._cell(_pct(m["atk_rate"]), MUTED, m["atk_rate"]))
            t.setItem(i, 5, self._cell(_pct(m["def_rate"]), MUTED, m["def_rate"]))

    def _render_board(self, rows: list[dict]) -> None:
        t = self._board
        self._fit(t, len(rows))
        for i, p in enumerate(rows):
            t.setItem(i, 0, self._cell(p["name"], align=Qt.AlignmentFlag.AlignLeft))
            t.setItem(i, 1, self._cell(p["matches"]))
            t.setItem(i, 2, self._cell(p["kills"]))
            t.setItem(i, 3, self._cell(p["deaths"]))
            t.setItem(i, 4, self._cell(p["assists"], BLUE))
            t.setItem(i, 5, self._cell(f"{p['kd']:.2f}", GREEN if p["kd"] >= 1 else RED))
            t.setItem(i, 6, self._cell(_pct(p["survival"]), MUTED, p["survival"]))
            t.setItem(i, 7, self._cell(f"{p['tps']:.3f}", PURPLE))

    def _render_ops(self, t: QTableWidget, rows: list[dict]) -> None:
        self._fit(t, len(rows))
        for i, o in enumerate(rows):
            t.setItem(i, 0, self._cell(o["operator"], align=Qt.AlignmentFlag.AlignLeft))
            t.setItem(i, 1, self._cell(o["rounds"]))
            t.setItem(i, 2, self._cell(o["wins"], MUTED))
            t.setItem(i, 3, self._cell(_pct(o["win_rate"]), _rate_color(o["win_rate"]), o["win_rate"]))

    # ── Player detail ────────────────────────────────────────────────────────

    def _fill_player_combo(self) -> None:
        detail = self._vm.get("player_detail", {})
        roster = sorted(self._vm.get("roster", []),
                        key=lambda p: (-len(detail.get(p[0], {}).get("rows", [])), p[1].lower()))
        combo = self._player_combo
        prev = combo.currentData()
        combo.blockSignals(True)
        combo.clear()
        for pid, name in roster:
            n = len(detail.get(pid, {}).get("rows", []))
            combo.addItem(f"{name}  ({n} match{'es' if n != 1 else ''})" if n else f"{name}  (no matches yet)", pid)
        idx = combo.findData(prev) if prev is not None else -1
        combo.setCurrentIndex(idx if idx >= 0 else 0)
        combo.blockSignals(False)
        self._render_player()

    def _render_player(self, *_args) -> None:
        pid = self._player_combo.currentData()
        info = self._vm.get("player_detail", {}).get(pid) if pid is not None else None
        rows = info["rows"] if info else []
        self._spark.set_rows(rows)
        t = self._p_table
        shown = list(reversed(rows))[:self.PLAYER_ROWS]
        self._fit(t, len(shown))
        self._p_more.setText(
            f"Showing the latest {len(shown)} of {len(rows)} matches — the chart above covers all of them."
            if len(rows) > len(shown) else ""
        )
        for i, r in enumerate(shown):
            t.setItem(i, 0, self._cell(f"{r['date']:%b %d  %H:%M}", MUTED))
            t.setItem(i, 1, self._cell(r["map"], align=Qt.AlignmentFlag.AlignLeft))
            t.setItem(i, 2, self._cell(r["result"].upper(), GREEN if r["result"] == "win" else RED))
            t.setItem(i, 3, self._cell(f"{r['k']}–{r['d']}–{r['a']}"))
            t.setItem(i, 4, self._cell(f"{r['kd']:.2f}", GREEN if r["kd"] >= 1 else RED))
            t.setItem(i, 5, self._cell(_pct(r["survival"]), MUTED, r["survival"]))
            t.setItem(i, 6, self._cell(f"{r['tps']:.3f}", PURPLE))

        if not rows:
            for c in (self._p_matches, self._p_tps, self._p_trend, self._p_consistency):
                c.set("—", "")
            if pid is not None:
                self._p_matches.set("0", "no linked stats yet")
            return
        wins = sum(1 for r in rows if r["result"] == "win")
        self._p_matches.set(str(len(rows)), f"{wins}–{len(rows) - wins} in those matches")
        self._p_tps.set(f"{info['avg_tps']:.3f}", "tactical performance score", PURPLE)
        tr = info["trend"]
        if tr is None:
            self._p_trend.set("—", "needs 4+ matches", MUTED)
        elif abs(tr) < 0.01:
            self._p_trend.set("Flat", "recent third vs earlier", MUTED)
        else:
            self._p_trend.set(f"{'▲' if tr > 0 else '▼'} {abs(tr):.3f}", "recent third vs earlier",
                              GREEN if tr > 0 else RED)
        cs = info["consistency"]
        if cs is None:
            self._p_consistency.set("—", "needs 2+ matches", MUTED)
        else:
            self._p_consistency.set(f"{cs:.3f}", "lower is steadier",
                                    GREEN if cs < 0.05 else AMBER if cs < 0.12 else RED)
