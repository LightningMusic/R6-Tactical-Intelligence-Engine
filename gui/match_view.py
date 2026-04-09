from PySide6.QtWidgets import (
    QAbstractItemView, QInputDialog, QWidget, QVBoxLayout, QHBoxLayout, QLabel, QComboBox,
    QTableWidget, QTableWidgetItem, QPushButton, QSpinBox,
    QCheckBox, QMessageBox, QHeaderView, QTabWidget, QAbstractScrollArea
)
from PySide6.QtCore import Qt
from typing import cast

from models.import_result import ImportResult
from app.app_controller import AppController
from database.repositories import Repository


class MatchView(QWidget):

    def __init__(self, parent=None):
        super().__init__(parent)
        self.controller = AppController()
        self.repo = Repository()
        self.current_match_id = None
        self.players = self.repo.get_team_players()[:5]
        self._secondary_handlers: dict = {}
        self._row_operator_cache: dict = {}
        self._updating = False
        self.init_ui()

    # ============================================================
    # COLUMN LAYOUT (single source of truth)
    # ============================================================
    # Col 0:  Player
    # Col 1:  Operator        (fixed 200px)
    # Col 2:  Kills
    # Col 3:  Died            (checkbox)
    # Col 4:  Assists
    # Col 5:  Eng Taken
    # Col 6:  Eng Won
    # Col 7:  Ability         (fixed 200px — label)
    # Col 8:  Ability Uses    (checkbox or dropdown)
    # Col 9:  Secondary       (dropdown)
    # Col 10: Secondary Used  (checkbox or dropdown)
    # Col 11: Obj Attempted   (checkbox)
    # Col 12: Obj Successful  (checkbox — enforced single)

    HEADERS = [
        "Player", "Operator", "Kills", "Died", "Assists",
        "Eng Taken", "Eng Won",
        "Ability", "Ability Uses", "Secondary", "Sec Used",
        "Obj Attempted", "Obj Successful"
    ]

    # ============================================================
    # UI SETUP
    # ============================================================

    def setup_table(self, table: QTableWidget, is_team: bool) -> None:
        table.setColumnCount(13)
        table.verticalHeader().setVisible(False)
        table.verticalHeader().setDefaultSectionSize(42)
        table.setHorizontalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
        table.setHorizontalHeaderLabels(self.HEADERS)

        hdr = table.horizontalHeader()
        hdr.setMinimumSectionSize(60)

        # Fixed-width columns
        for col in (1, 7):
            table.setColumnWidth(col, 200)
            hdr.setSectionResizeMode(col, QHeaderView.ResizeMode.Fixed)

        # ResizeToContents for everything else
        for col in (0, 2, 3, 4, 5, 6, 8, 9, 10, 11, 12):
            hdr.setSectionResizeMode(col, QHeaderView.ResizeMode.ResizeToContents)

        hdr.setStretchLastSection(False)
        table.setAlternatingRowColors(True)
        table.setRowCount(5)
        table.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        table.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        table.setSizeAdjustPolicy(QAbstractScrollArea.SizeAdjustPolicy.AdjustToContents)

    def init_ui(self) -> None:
        self.setStyleSheet("""
            QTableWidget         { gridline-color: #444; }
            QTableWidget::item   { padding: 4px; }
            QHeaderView::section {
                padding: 6px 10px;
                font-weight: bold;
                background-color: #333;
                color: white;
            }
            QComboBox, QSpinBox  { padding: 4px; min-height: 26px; }
            QCheckBox            { margin-left: 6px; }
            QTabWidget::pane     { border: 1px solid #444; }
            QSpinBox::up-button, QSpinBox::down-button { width: 0px; border: none; }
            QScrollBar:horizontal {
                border: none; background: #222; height: 10px;
            }
            QScrollBar::handle:horizontal {
                background: #444; min-width: 30px; border-radius: 5px;
            }
            QScrollBar::handle:horizontal:hover { background: #666; }
        """)

        layout = QVBoxLayout()
        layout.setSpacing(14)
        layout.setContentsMargins(12, 12, 12, 12)

        # ── Match selector ────────────────────────────────────
        match_layout = QHBoxLayout()
        match_layout.addWidget(QLabel("Select Match:"))
        self.match_selector = QComboBox()
        self.match_selector.currentIndexChanged.connect(self.on_match_selected)
        self.load_matches()
        match_layout.addWidget(self.match_selector, stretch=1)
        layout.addLayout(match_layout)

        # ── Round controls ────────────────────────────────────
        round_layout = QHBoxLayout()

        round_layout.addWidget(QLabel("Round #:"))
        self.round_number_spin = QSpinBox()
        self.round_number_spin.setRange(1, 50)
        round_layout.addWidget(self.round_number_spin)

        round_layout.addWidget(QLabel("Side:"))
        self.side_selector = QComboBox()
        self.side_selector.addItems(["attack", "defense"])
        self.side_selector.currentTextChanged.connect(self.populate_tables)
        self.side_selector.currentTextChanged.connect(self.update_objective_headers)
        self.side_selector.currentTextChanged.connect(self.update_resource_label)
        round_layout.addWidget(self.side_selector)

        round_layout.addWidget(QLabel("Outcome:"))
        self.outcome_selector = QComboBox()
        self.outcome_selector.addItems(["", "win", "loss"])
        round_layout.addWidget(self.outcome_selector)

        round_layout.addWidget(QLabel("Site:"))
        self.site_edit = QComboBox()
        self.site_edit.setEditable(True)
        self.site_edit.addItems([
            "", "1F Armory", "1F Lobby", "2F Master Office",
            "B Server Room", "B Garage", "2F Bedroom",
        ])
        self.site_edit.setMinimumWidth(160)
        round_layout.addWidget(self.site_edit)

        round_layout.addStretch()
        layout.addLayout(round_layout)

        # ── Resource row ──────────────────────────────────────
        resource_layout = QHBoxLayout()
        self.resource_label = QLabel()
        resource_layout.addWidget(self.resource_label)

        self.resource_spin = QSpinBox()
        self.resource_spin.setRange(0, 10)
        self.resource_spin.setValue(0)
        self.resource_spin.setFixedWidth(60)
        resource_layout.addWidget(self.resource_spin)
        resource_layout.addStretch()
        layout.addLayout(resource_layout)

        # ── Player tables ─────────────────────────────────────
        self.tabs = QTabWidget()

        self.team_table = QTableWidget()
        self.setup_table(self.team_table, True)
        self.team_table.setMinimumHeight(280)
        self.tabs.addTab(self.team_table, "Team")

        self.enemy_table = QTableWidget()
        self.setup_table(self.enemy_table, False)
        self.enemy_table.setMinimumHeight(280)
        self.tabs.addTab(self.enemy_table, "Enemies")

        layout.addWidget(self.tabs)

        # ── Buttons ───────────────────────────────────────────
        btn_layout = QHBoxLayout()
        save_btn = QPushButton("Save Round")
        save_btn.clicked.connect(self.save_round)
        btn_layout.addWidget(save_btn)

        report_btn = QPushButton("Generate Report")
        report_btn.clicked.connect(self.generate_report)
        btn_layout.addWidget(report_btn)
        layout.addLayout(btn_layout)

        self.setLayout(layout)
        self.populate_tables()
        self.update_objective_headers()
        self.update_resource_label()

    # ============================================================
    # HEADER LABELS (side-aware)
    # ============================================================

    def update_objective_headers(self) -> None:
        team_side  = self.side_selector.currentText()
        enemy_side = "defense" if team_side == "attack" else "attack"

        def labels(side: str) -> tuple[str, str]:
            return ("Plant Attempted", "Plant Successful") \
                if side == "attack" else ("Defuse Attempted", "Defuse Successful")

        ta, ts = labels(team_side)
        ea, es = labels(enemy_side)
        self.team_table.setHorizontalHeaderItem(11, QTableWidgetItem(ta))
        self.team_table.setHorizontalHeaderItem(12, QTableWidgetItem(ts))
        self.enemy_table.setHorizontalHeaderItem(11, QTableWidgetItem(ea))
        self.enemy_table.setHorizontalHeaderItem(12, QTableWidgetItem(es))

    def update_resource_label(self) -> None:
        if self.side_selector.currentText() == "attack":
            self.resource_label.setText("Drones Lost:")
        else:
            self.resource_label.setText("Reinforcements Used:")

    # ============================================================
    # MATCH SELECTOR
    # ============================================================

    def load_matches(self, select_match_id: int | None = None) -> None:
        self.match_selector.blockSignals(True)
        self.match_selector.clear()
        self.match_selector.addItem("➕ Create New Match", "NEW")
        self.match_selector.addItem("— Select a match —", None)

        matches = self.repo.get_all_matches()
        target_index = 1

        for m in matches:
            idx = self.match_selector.count()
            self.match_selector.addItem(
                f"{m.match_id}: {m.opponent_name} ({m.map})",
                m.match_id
            )
            if select_match_id is not None and m.match_id == select_match_id:
                target_index = idx

        self.match_selector.blockSignals(False)
        self.match_selector.setCurrentIndex(target_index)

        if select_match_id is not None:
            self.current_match_id = select_match_id
            self.populate_tables()
            self.update_resource_label()

    def on_match_selected(self, index: int) -> None:
        data = self.match_selector.currentData()

        if data == "NEW":
            opponent, ok1 = QInputDialog.getText(self, "New Match", "Opponent Name:")
            if not ok1 or not opponent.strip():
                self.match_selector.blockSignals(True)
                self.match_selector.setCurrentIndex(1)
                self.match_selector.blockSignals(False)
                return

            maps = self.repo.get_all_maps()
            if not maps:
                QMessageBox.critical(self, "Error", "No maps in database.")
                self.match_selector.blockSignals(True)
                self.match_selector.setCurrentIndex(1)
                self.match_selector.blockSignals(False)
                return

            map_name, ok2 = QInputDialog.getItem(
                self, "New Match", "Select Map:", maps, 0, False
            )
            if not ok2:
                self.match_selector.blockSignals(True)
                self.match_selector.setCurrentIndex(1)
                self.match_selector.blockSignals(False)
                return

            try:
                match_id = self.controller.create_match(opponent.strip(), map_name)
                self.load_matches(select_match_id=match_id)
            except Exception as e:
                QMessageBox.critical(self, "Error", str(e))
            return

        if data is None:
            self.current_match_id = None
            return

        self.current_match_id = data
        self.round_number_spin.setValue(1)
        self.populate_tables()
        self.update_resource_label()

    # ============================================================
    # TABLE POPULATION
    # ============================================================

    def clear_table_widgets(self, table: QTableWidget) -> None:
            for row in range(table.rowCount()):
                for col in range(table.columnCount()):
                    widget = table.cellWidget(row, col)
                    if widget:
                        # Clean up our internal event handler tracking
                        self._secondary_handlers.pop(id(widget), None)
                        # Safely schedule the widget for deletion
                        widget.deleteLater()
                        # Properly remove the widget from the table cell
                        table.removeCellWidget(row, col)

    def populate_tables(self) -> None:
        self.team_table.blockSignals(True)
        self.enemy_table.blockSignals(True)

        self.clear_table_widgets(self.team_table)
        self.clear_table_widgets(self.enemy_table)

        self.populate_team_table()
        self.populate_enemy_table()

        self.team_table.blockSignals(False)
        self.enemy_table.blockSignals(False)

        self.refresh_operator_dropdowns(self.team_table)
        self.refresh_operator_dropdowns(self.enemy_table)
        self.refresh_all_loadouts()

    def populate_team_table(self) -> None:
        self.team_table.setRowCount(5)
        for row, player in enumerate(self.players):
            item = QTableWidgetItem(player.name)
            item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEditable)
            self.team_table.setItem(row, 0, item)
            self.populate_common_cells(self.team_table, row, True)

    def populate_enemy_table(self) -> None:
        for row in range(self.enemy_table.rowCount()):
            self.enemy_table.setItem(row, 0, QTableWidgetItem(""))
            self.populate_common_cells(self.enemy_table, row, False)

    def populate_common_cells(self, table: QTableWidget, row: int, is_team: bool) -> None:
        if self._updating:
            return

        # Col 1: Operator dropdown
        op_selector = QComboBox()
        table.setCellWidget(row, 1, op_selector)

        def on_op_change() -> None:
            if self._updating:
                return
            self.update_loadout(table, row)
            self.refresh_operator_dropdowns(table)
            self._enforce_single_success(table)

        op_selector.currentIndexChanged.connect(on_op_change)

        # Col 2: Kills (spinbox)
        kills_spin = QSpinBox()
        kills_spin.setRange(0, 50)
        table.setCellWidget(row, 2, kills_spin)

        # Col 3: Died (checkbox — you can only die once)
        died_cb = QCheckBox()
        self._center_widget(table, row, 3, died_cb)

        # Col 4: Assists
        assists_spin = QSpinBox()
        assists_spin.setRange(0, 50)
        table.setCellWidget(row, 4, assists_spin)

        # Col 5: Eng Taken
        eng_taken = QSpinBox()
        eng_taken.setRange(0, 50)
        table.setCellWidget(row, 5, eng_taken)

        # Col 6: Eng Won
        eng_won = QSpinBox()
        eng_won.setRange(0, 50)
        table.setCellWidget(row, 6, eng_won)

        # Col 7: Ability label
        ability_label = QLabel("Ability")
        ability_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        table.setCellWidget(row, 7, ability_label)

        # Col 8: Ability uses (placeholder — filled by update_loadout)
        table.setCellWidget(row, 8, QComboBox())

        # Col 9: Secondary gadget dropdown
        sec_selector = QComboBox()
        table.setCellWidget(row, 9, sec_selector)

        # Col 10: Secondary used (placeholder — filled by update_loadout)
        table.setCellWidget(row, 10, QComboBox())

        # Col 11: Obj Attempted (checkbox)
        obj_attempt = QCheckBox()
        self._center_widget(table, row, 11, obj_attempt)

        # Col 12: Obj Successful (checkbox — only one per table)
        obj_success = QCheckBox()
        obj_success.stateChanged.connect(
            lambda state, r=row, t=table: self._on_success_changed(state, r, t)
        )
        self._center_widget(table, row, 12, obj_success)

        self.update_loadout(table, row)
        self.refresh_operator_dropdowns(table)

    def _center_widget(self, table: QTableWidget, row: int, col: int, widget: QWidget) -> None:
        """Wraps a widget in a centered container cell."""
        container = QWidget()
        layout = QHBoxLayout(container)
        layout.addWidget(widget)
        layout.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.setContentsMargins(0, 0, 0, 0)
        table.setCellWidget(row, col, container)

    # ============================================================
    # SINGLE-SUCCESS ENFORCEMENT
    # ============================================================

    def _on_success_changed(self, state: int, changed_row: int, table: QTableWidget) -> None:
        """When one row's Obj Successful is checked, uncheck all others."""
        if state != Qt.CheckState.Checked.value:
            return
        self._enforce_single_success(table, checked_row=changed_row)

    def _enforce_single_success(self, table: QTableWidget, checked_row: int = -1) -> None:
        for row in range(table.rowCount()):
            container = table.cellWidget(row, 12)
            if container is None:
                continue
            cb = container.findChild(QCheckBox)
            if cb is None:
                continue
            if row != checked_row and cb.isChecked():
                cb.blockSignals(True)
                cb.setChecked(False)
                cb.blockSignals(False)

    # ============================================================
    # LOADOUT (ability + secondary gadget)
    # ============================================================

    def refresh_all_loadouts(self) -> None:
        for table in (self.team_table, self.enemy_table):
            for row in range(table.rowCount()):
                self.update_loadout(table, row)

    def update_loadout(self, table: QTableWidget, row: int) -> None:
        op_widget     = cast(QComboBox, table.cellWidget(row, 1))
        ability_label = cast(QLabel,    table.cellWidget(row, 7))
        sec_widget    = cast(QComboBox, table.cellWidget(row, 9))

        if not op_widget or not sec_widget:
            return

        operator_id = op_widget.currentData()
        row_key = (id(table), row)

        if self._row_operator_cache.get(row_key) == operator_id:
            return
        self._row_operator_cache[row_key] = operator_id

        # ── Reset ─────────────────────────────────────────────
        sec_widget.blockSignals(True)
        sec_widget.clear()
        sec_widget.addItem("None", None)

        if ability_label:
            ability_label.setText("Ability")

        # Clear ability widget
        old_ability = table.cellWidget(row, 8)
        if old_ability:
            old_ability.deleteLater()
        table.setCellWidget(row, 8, QComboBox())

        # Clear sec used widget
        old_sec_used = table.cellWidget(row, 10)
        if old_sec_used:
            old_sec_used.deleteLater()
        table.setCellWidget(row, 10, QComboBox())

        if operator_id is None:
            sec_widget.blockSignals(False)
            return

        operator = self.repo.get_operator_by_id(operator_id)
        if operator is None:
            sec_widget.blockSignals(False)
            return

        # ── Ability ───────────────────────────────────────────
        if ability_label:
            ability_label.setText(operator.ability_name)

        if operator.ability_max_count <= 1:
            ab_widget: QWidget = QCheckBox()
        else:
            ab_cb = QComboBox()
            for i in range(operator.ability_max_count + 1):
                ab_cb.addItem(str(i), i)
            ab_widget = ab_cb

        table.setCellWidget(row, 8, ab_widget)

        # ── Secondary gadgets ─────────────────────────────────
        gadget_map: dict[int, int] = {}
        for g in self.repo.get_gadgets_for_operator(operator_id):
            sec_widget.addItem(g.name, g.gadget_id)
            gadget_map[g.gadget_id] = g.max_count

        def update_secondary_uses() -> None:
            selected_id = sec_widget.currentData()
            max_count   = gadget_map.get(selected_id, 0)

            old = table.cellWidget(row, 10)
            if old:
                old.deleteLater()

            if max_count == 1 and (operator is None or operator.name != "Solid Snake"):
                new_widget: QWidget = QCheckBox()
            else:
                dd = QComboBox()
                for i in range(max_count + 1):
                    dd.addItem(str(i), i)
                new_widget = dd

            table.setCellWidget(row, 10, new_widget)

        # Disconnect old handler
        existing = self._secondary_handlers.get(id(sec_widget))
        if existing is not None:
            try:
                sec_widget.currentIndexChanged.disconnect(existing)
            except Exception:
                pass

        self._secondary_handlers[id(sec_widget)] = update_secondary_uses
        sec_widget.currentIndexChanged.connect(update_secondary_uses)
        update_secondary_uses()

        sec_widget.blockSignals(False)

    # ============================================================
    # OPERATOR DROPDOWNS
    # ============================================================

    def refresh_operator_dropdowns(self, table: QTableWidget) -> None:
        if self._updating:
            return
        self._updating = True

        full_selected: set[int] = set()
        for row in range(table.rowCount()):
            w = cast(QComboBox, table.cellWidget(row, 1))
            if w and w.currentData() is not None:
                full_selected.add(w.currentData())

        side = self.side_selector.currentText()
        if table is self.enemy_table:
            side = "defense" if side == "attack" else "attack"

        operators = self.repo.get_all_operators()

        for row in range(table.rowCount()):
            w = cast(QComboBox, table.cellWidget(row, 1))
            if not w:
                continue

            current_id   = w.currentData()
            current_text = w.currentText()

            selected_ids = full_selected - ({current_id} if current_id is not None else set())

            w.blockSignals(True)
            w.clear()

            for op in operators:
                if op.side != side:
                    continue
                if op.operator_id not in selected_ids:
                    w.addItem(op.name, op.operator_id)

            if current_id is not None:
                idx = w.findData(current_id)
                if idx >= 0:
                    w.setCurrentIndex(idx)
                else:
                    idx = w.findText(current_text)
                    if idx >= 0:
                        w.setCurrentIndex(idx)

            w.blockSignals(False)

        self._updating = False

    # ============================================================
    # PREFILL (partial import)
    # ============================================================

    def prefill_from_import(self, result: ImportResult) -> None:
        if result.match_id is not None:
            for i in range(self.match_selector.count()):
                if self.match_selector.itemData(i) == result.match_id:
                    self.match_selector.blockSignals(True)
                    self.match_selector.setCurrentIndex(i)
                    self.match_selector.blockSignals(False)
                    self.current_match_id = result.match_id
                    self.populate_tables()
                    break

        if result.rounds:
            r = result.rounds[0]
            idx = self.side_selector.findText(r.side or "attack")
            if idx >= 0:
                self.side_selector.setCurrentIndex(idx)
            self.round_number_spin.setValue(r.round_number or 1)

        msg = result.error_message or "Some data could not be parsed."
        QMessageBox.information(
            self, "Partial Import — Please Review",
            f"{msg}\n\nPre-filled what was recovered. Please complete and save."
        )

    # ============================================================
    # SAVE
    # ============================================================

    def save_round(self) -> None:
        if self.current_match_id is None:
            QMessageBox.warning(self, "Error", "No match selected.")
            return

        side    = self.side_selector.currentText()
        outcome = self.outcome_selector.currentText()
        site    = self.site_edit.currentText().strip()

        if not outcome:
            QMessageBox.warning(self, "Error", "Please select a round outcome (win/loss).")
            return

        resource_value = self.resource_spin.value()

        round_data: dict = {
            "match_id":    self.current_match_id,
            "round_number": self.round_number_spin.value(),
            "side":        side,
            "outcome":     outcome,
            "site":        site,
            "player_stats": [],
        }

        # Attach resource field
        if side == "attack":
            round_data["team_drones_lost"] = resource_value
        else:
            round_data["team_reinforcements_used"] = resource_value

        for row, player in enumerate(self.players):
            table = self.team_table

            op_box    = cast(QComboBox, table.cellWidget(row, 1))
            kills     = cast(QSpinBox,  table.cellWidget(row, 2))

            # Col 3 is now a centered container holding a QCheckBox
            died_container = table.cellWidget(row, 3)
            died_cb = died_container.findChild(QCheckBox) if died_container else None
            deaths = 1 if (died_cb and died_cb.isChecked()) else 0

            assists   = cast(QSpinBox, table.cellWidget(row, 4))
            eng_taken = cast(QSpinBox, table.cellWidget(row, 5))
            eng_won   = cast(QSpinBox, table.cellWidget(row, 6))

            ability_widget = table.cellWidget(row, 8)
            if isinstance(ability_widget, QCheckBox):
                ability_used = 1 if ability_widget.isChecked() else 0
            elif isinstance(ability_widget, QComboBox):
                ability_used = ability_widget.currentData() or 0
            else:
                ability_used = 0

            sec_box    = cast(QComboBox, table.cellWidget(row, 9))
            sec_widget = table.cellWidget(row, 10)
            if isinstance(sec_widget, QCheckBox):
                secondary_used = 1 if sec_widget.isChecked() else 0
            elif isinstance(sec_widget, QComboBox):
                secondary_used = sec_widget.currentData() or 0
            else:
                secondary_used = 0

            # Obj Attempted — centered container
            attempt_container = table.cellWidget(row, 11)
            attempt_cb = attempt_container.findChild(QCheckBox) if attempt_container else None

            # Obj Successful — centered container
            success_container = table.cellWidget(row, 12)
            success_cb = success_container.findChild(QCheckBox) if success_container else None

            round_data["player_stats"].append({
                "player_id":          player.player_id,
                "operator_id":        op_box.currentData(),
                "kills":              kills.value(),
                "deaths":             deaths,
                "assists":            assists.value(),
                "engagements_taken":  eng_taken.value(),
                "engagements_won":    eng_won.value(),
                "ability_used":       ability_used,
                "secondary_gadget_id": sec_box.currentData(),
                "secondary_used":     secondary_used,
                "plant_attempted":    attempt_cb.isChecked() if attempt_cb else False,
                "plant_successful":   success_cb.isChecked() if success_cb else False,
            })

        try:
            self.controller.save_round(round_data)
            # Auto-advance round number
            self.round_number_spin.setValue(self.round_number_spin.value() + 1)
            self.outcome_selector.setCurrentIndex(0)
            QMessageBox.information(self, "Saved", "Round saved!")
        except Exception as e:
            QMessageBox.critical(self, "Error", str(e))

    # ============================================================
    # REPORT
    # ============================================================

    def generate_report(self) -> None:
        if not self.current_match_id:
            QMessageBox.warning(self, "Error", "No match selected.")
            return
        try:
            self.controller.process_completed_match(self.current_match_id)
            QMessageBox.information(self, "Done", "Report generated!")
        except Exception as e:
            QMessageBox.critical(self, "Error", str(e))