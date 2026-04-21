from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QComboBox,
    QPushButton, QTableWidget, QTableWidgetItem, QMessageBox,
    QHeaderView, QTabWidget, QTextEdit, QSplitter
)
from PySide6.QtCore import Qt

from app.app_controller import AppController


class AnalysisView(QWidget):
    def __init__(self, parent, controller: AppController):
        super().__init__(parent)
        self.controller = controller
        self._build_layout()
        self.load_matches()

    def _build_layout(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 20, 20, 20)
        layout.setSpacing(15)

        # ── Header ────────────────────────────────────────────
        header = QLabel("Match Analysis")
        header.setStyleSheet("font-size: 24px; font-weight: bold;")
        header.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(header)

        # ── Match selector row ────────────────────────────────
        selection_layout = QHBoxLayout()
        selection_layout.addWidget(QLabel("Select Match:"))

        self.match_dropdown = QComboBox()
        self.match_dropdown.setMinimumWidth(300)
        selection_layout.addWidget(self.match_dropdown, stretch=1)

        run_btn = QPushButton("▶  Run Analysis")
        run_btn.clicked.connect(self.run_analysis)
        self._run_btn = run_btn   # store reference
        selection_layout.addWidget(self._run_btn)

        report_btn = QPushButton("📄  Generate Report")
        report_btn.clicked.connect(self.generate_report)
        selection_layout.addWidget(report_btn)

        layout.addLayout(selection_layout)

        # ── Match summary bar ─────────────────────────────────
        self._summary_label = QLabel("")
        self._summary_label.setStyleSheet(
            "font-size: 13px; color: #aaa; padding: 4px 0;"
        )
        layout.addWidget(self._summary_label)

        # ── Tabs: Metrics / AI Intel / Rounds ─────────────────
        self._tabs = QTabWidget()

        # Tab 1: Metrics table
        metrics_widget = QWidget()
        metrics_layout = QVBoxLayout(metrics_widget)
        metrics_layout.setContentsMargins(0, 8, 0, 0)

        self.results_table = QTableWidget(0, 2)
        self.results_table.setHorizontalHeaderLabels(["Metric", "Value"])
        self.results_table.horizontalHeader().setSectionResizeMode(
            0, QHeaderView.ResizeMode.Stretch
        )
        self.results_table.horizontalHeader().setSectionResizeMode(
            1, QHeaderView.ResizeMode.ResizeToContents
        )
        self.results_table.setAlternatingRowColors(True)
        self.results_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.results_table.verticalHeader().setVisible(False)
        metrics_layout.addWidget(self.results_table)
        self._tabs.addTab(metrics_widget, "📊 Metrics")

        # Tab 2: AI Intel
        intel_widget = QWidget()
        intel_layout = QVBoxLayout(intel_widget)
        intel_layout.setContentsMargins(0, 8, 0, 0)

        self._intel_match_label = QLabel("Match Summary")
        self._intel_match_label.setStyleSheet("font-weight: bold; font-size: 13px;")
        intel_layout.addWidget(self._intel_match_label)

        self._intel_match_text = QTextEdit()
        self._intel_match_text.setReadOnly(True)
        self._intel_match_text.setPlaceholderText(
            "Run Analysis to generate AI tactical summary..."
        )
        self._intel_match_text.setMaximumHeight(160)
        intel_layout.addWidget(self._intel_match_text)

        self._intel_player_label = QLabel("Player Intel")
        self._intel_player_label.setStyleSheet(
            "font-weight: bold; font-size: 13px; margin-top: 8px;"
        )
        intel_layout.addWidget(self._intel_player_label)

        self._intel_player_table = QTableWidget(0, 2)
        self._intel_player_table.setHorizontalHeaderLabels(["Player", "AI Feedback"])
        self._intel_player_table.horizontalHeader().setSectionResizeMode(
            0, QHeaderView.ResizeMode.ResizeToContents
        )
        self._intel_player_table.horizontalHeader().setSectionResizeMode(
            1, QHeaderView.ResizeMode.Stretch
        )
        self._intel_player_table.setAlternatingRowColors(True)
        self._intel_player_table.setEditTriggers(
            QTableWidget.EditTrigger.NoEditTriggers
        )
        self._intel_player_table.verticalHeader().setVisible(False)
        self._intel_player_table.setWordWrap(True)
        intel_layout.addWidget(self._intel_player_table)
        self._tabs.addTab(intel_widget, "🤖 AI Intel")

        # Tab 3: Rounds breakdown
        rounds_widget = QWidget()
        rounds_layout = QVBoxLayout(rounds_widget)
        rounds_layout.setContentsMargins(0, 8, 0, 0)

        self._rounds_table = QTableWidget(0, 5)
        self._rounds_table.setHorizontalHeaderLabels(
            ["Round", "Side", "Site", "Outcome", "Team K/D"]
        )
        for col in range(5):
            self._rounds_table.horizontalHeader().setSectionResizeMode(
                col, QHeaderView.ResizeMode.Stretch
            )
        self._rounds_table.setAlternatingRowColors(True)
        self._rounds_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self._rounds_table.verticalHeader().setVisible(False)
        rounds_layout.addWidget(self._rounds_table)
        self._tabs.addTab(rounds_widget, "📋 Rounds")

        layout.addWidget(self._tabs)

    # =====================================================
    # LOAD MATCHES
    # =====================================================

    def load_matches(self, select_match_id: int | None = None) -> None:
        try:
            from database.repositories import Repository
            repo = Repository()
            matches = repo.get_all_matches()

            self.match_dropdown.blockSignals(True)
            self.match_dropdown.clear()

            target_index = 0
            for i, m in enumerate(matches):
                label = f"{m.match_id}: {m.opponent_name} ({m.map})"
                self.match_dropdown.addItem(label, m.match_id)
                if select_match_id is not None and m.match_id == select_match_id:
                    target_index = i

            self.match_dropdown.blockSignals(False)
            if matches:
                self.match_dropdown.setCurrentIndex(target_index)
                self._load_rounds_tab(matches[target_index].match_id)

        except Exception as e:
            QMessageBox.critical(self, "Error", str(e))

    # =====================================================
    # RUN ANALYSIS
    # =====================================================

    def run_analysis(self) -> None:
        match_id = self.match_dropdown.currentData()
        if match_id is None:
            QMessageBox.warning(self, "Warning", "Select a match first.")
            return

        # Disable controls and show loading state
        self._run_btn.setEnabled(False)
        self._run_btn.setText("⏳ Analyzing...")
        self._summary_label.setText(
            "🤖 Loading AI model (first run may take 30–60 seconds)..."
        )

        from PySide6.QtCore import QThread, QObject, Signal as QSignal

        class _Worker(QObject):
            done     = QSignal(dict)
            failed   = QSignal(str)
            progress = QSignal(str)

            def __init__(self, ctrl, mid):
                super().__init__()
                self._ctrl = ctrl
                self._mid  = mid

            def run(self):
                try:
                    def on_progress(attempt, total, msg):
                        self.progress.emit(msg)

                    result = self._ctrl.fetch_match_intel(
                        self._mid,
                        progress_callback=on_progress,
                    )
                    self.done.emit(result)
                except Exception as e:
                    self.failed.emit(str(e))

        self._analysis_thread = QThread()
        self._analysis_worker = _Worker(self.controller, match_id)
        self._analysis_worker.moveToThread(self._analysis_thread)

        self._analysis_thread.started.connect(self._analysis_worker.run)
        self._analysis_worker.progress.connect(
            lambda msg: self._summary_label.setText(f"🤖 {msg}")
        )
        self._analysis_worker.done.connect(self._on_analysis_done)
        self._analysis_worker.failed.connect(self._on_analysis_error)
        self._analysis_worker.done.connect(self._analysis_thread.quit)
        self._analysis_worker.failed.connect(self._analysis_thread.quit)
        self._analysis_thread.finished.connect(self._analysis_thread.deleteLater)

        self._analysis_thread.start()

    def _on_analysis_done(self, metrics: dict) -> None:
        self._run_btn.setEnabled(True)
        self._run_btn.setText("▶  Run Analysis")
        match_id = self.match_dropdown.currentData()
        try:
            self._display_metrics(metrics)
            summary = metrics.get("ai_summary", "")
            if summary:
                self._intel_match_text.setPlainText(str(summary))
            player_intel = metrics.get("players", {})
            self._display_player_intel(player_intel)
            self._load_rounds_tab(match_id)
            self._update_summary_bar(match_id)
            self._tabs.setCurrentIndex(0)
            self._summary_label.setText("✅ Analysis complete.")
        except Exception as e:
            QMessageBox.critical(self, "Display Error", str(e))

    def _on_analysis_error(self, message: str) -> None:
        self._run_btn.setEnabled(True)
        self._run_btn.setText("▶  Run Analysis")
        self._summary_label.setText(f"❌ {message}")

    # =====================================================
    # DISPLAY HELPERS
    # =====================================================

    def _display_metrics(self, metrics: dict) -> None:
        self.results_table.setRowCount(0)

        # Flatten nested dicts for display
        flat: dict = {}
        for key, value in metrics.items():
            if isinstance(value, dict):
                for k2, v2 in value.items():
                    flat[f"{key} → {k2}"] = v2
            else:
                flat[key] = value

        for key, value in flat.items():
            row = self.results_table.rowCount()
            self.results_table.insertRow(row)
            self.results_table.setItem(row, 0, QTableWidgetItem(str(key)))
            self.results_table.setItem(row, 1, QTableWidgetItem(str(value)))

    def _display_player_intel(self, player_intel: dict) -> None:
        self._intel_player_table.setRowCount(0)
        for player_name, feedback in player_intel.items():
            row = self._intel_player_table.rowCount()
            self._intel_player_table.insertRow(row)
            self._intel_player_table.setItem(
                row, 0, QTableWidgetItem(str(player_name))
            )
            self._intel_player_table.setItem(
                row, 1, QTableWidgetItem(str(feedback))
            )
        self._intel_player_table.resizeRowsToContents()

    def _load_rounds_tab(self, match_id: int | None) -> None:
        self._rounds_table.setRowCount(0)
        if match_id is None:
            return
        try:
            from database.repositories import Repository
            repo = Repository()
            match = repo.get_match_full(match_id)
            if match is None:
                return
            for r in match.rounds:
                kills  = sum(p.kills  for p in r.player_stats)
                deaths = sum(p.deaths for p in r.player_stats)
                row = self._rounds_table.rowCount()
                self._rounds_table.insertRow(row)
                self._rounds_table.setItem(
                    row, 0, QTableWidgetItem(str(r.round_number))
                )
                self._rounds_table.setItem(
                    row, 1, QTableWidgetItem(r.side.capitalize())
                )
                self._rounds_table.setItem(
                    row, 2, QTableWidgetItem(r.site or "—")
                )
                outcome_item = QTableWidgetItem(r.outcome.capitalize())
                outcome_item.setForeground(
                    Qt.GlobalColor.green
                    if r.outcome == "win"
                    else Qt.GlobalColor.red
                )
                self._rounds_table.setItem(row, 3, outcome_item)
                self._rounds_table.setItem(
                    row, 4, QTableWidgetItem(f"{kills} / {deaths}")
                )
        except Exception as e:
            print(f"[AnalysisView] Rounds load error: {e}")

    def _update_summary_bar(self, match_id: int) -> None:
        try:
            from database.repositories import Repository
            repo = Repository()
            match = repo.get_match_full(match_id)
            if match is None:
                return
            wins   = sum(1 for r in match.rounds if r.outcome == "win")
            losses = sum(1 for r in match.rounds if r.outcome == "loss")
            result = match.result or "In Progress"
            self._summary_label.setText(
                f"vs {match.opponent_name}  |  Map: {match.map}  |  "
                f"Score: {wins}–{losses}  |  Result: {result.upper()}"
            )
        except Exception:
            pass

    # =====================================================
    # GENERATE REPORT
    # =====================================================

    def generate_report(self) -> None:
        match_id = self.match_dropdown.currentData()
        if match_id is None:
            QMessageBox.warning(self, "Warning", "Select a match first.")
            return
        try:
            path = self.controller.regenerate_report(match_id)
            QMessageBox.information(
                self, "Report Generated", f"Saved to:\n{path}"
            )
        except Exception as e:
            QMessageBox.critical(self, "Report Error", str(e))