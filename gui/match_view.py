from PySide6.QtWidgets import (
    QAbstractItemView, QInputDialog, QWidget, QVBoxLayout, QHBoxLayout, QLabel,
    QComboBox, QTableWidget, QTableWidgetItem, QPushButton, QSpinBox,
    QCheckBox, QMessageBox, QHeaderView, QTabWidget, QAbstractScrollArea,
    QGroupBox, QFormLayout
)
from PySide6.QtCore import Qt
from typing import cast

from models.import_result import ImportResult
from app.app_controller import AppController
from database.repositories import Repository


class MatchView(QWidget):

    HEADERS = [
        "Player", "Operator", "Kills", "Died", "Assists",
        "Eng Taken", "Eng Won",
        "Ability", "Ability Uses", "Secondary", "Sec Used",
        "Obj Attempted", "Obj Successful"
    ]

    MAP_SITES: dict[str, list[str]] = {
        "Bank":             ["1F Teller's Office", "2F Open Area", "B Lockers", "B CCTV"],
        "Border":           ["1F Armory Lockers", "1F Supply Room", "2F Ventilation", "2F Tram Control"],
        "Chalet":           ["B Wine Cellar", "1F Kitchen", "2F Master Bedroom", "2F Gaming Room"],
        "Clubhouse":        ["B Cash Room", "1F Bar", "2F CCTV Room", "2F Gym"],
        "Coastline":        ["1F Kitchen", "1F Billiards", "2F Penthouse", "2F Hookah Lounge"],
        "Consulate":        ["B Garage", "1F Consul Office", "2F Lobby", "2F Meeting Room"],
        "Emerald Plains":   ["1F Dining Hall", "1F Kitchen", "2F Master Bedroom", "2F Office"],
        "Favela":           ["1F Laundry", "2F Master Bedroom", "3F Bedroom", "3F Office"],
        "Fortress":         ["1F Armory", "1F Bedroom", "1F Commander's Office", "B Prison"],
        "Hereford Base":    ["B Armory", "1F Dining Hall", "2F Recruit Dorm", "3F Commander's Office"],
        "House":            ["1F Kitchen", "1F Living Room", "2F Master Bedroom", "2F Kids Bedroom"],
        "Kafe Dostoyevsky": ["1F Kitchen", "2F Reading Room", "3F Cocktail Bar", "3F Mining Room"],
        "Kanal":            ["1F Coast Guard Room", "1F Server Room", "2F Map Room", "2F Office"],
        "Lair":             ["B Server Room", "1F Hangar", "2F Living Quarters", "2F Office"],
        "Nighthaven Labs":  ["1F Armory", "1F Labs", "2F Server Room", "2F Office"],
        "Oregon":           ["B Laundry", "B Meeting Hall", "1F Kitchen", "2F Master Bedroom"],
        "Outback":          ["1F Bar", "1F Kitchen", "2F Office", "2F Bedroom"],
        "Plane":            ["1F Cargo Hold", "2F Cockpit", "2F Business Class", "2F Economy"],
        "Skyscraper":       ["1F Bedroom", "1F Tea Room", "2F Office", "2F Exhibition"],
        "Stadium Bravo":    ["1F Locker Room", "1F Concession", "2F CCTV Room", "2F Press Room"],
        "Theme Park":       ["1F Armory", "1F Bunk", "2F Office", "2F Day Care"],
        "Tower":            ["1F Bar", "2F Office", "3F Penthouse", "3F Game Room"],
        "Villa":            ["1F Aviator Room", "1F Games Room", "2F Master Bedroom", "2F Statuary"],
        "Yacht":            ["B Engine Room", "1F Galley", "2F State Room", "3F Cockpit"],
    }

    def __init__(self, parent=None):
        super().__init__(parent)
        self.controller = AppController()
        self.repo       = Repository()
        self.current_match_id = None
        self.players    = self.repo.get_team_players()[:5]
        self._secondary_handlers: dict = {}
        self._row_operator_cache: dict = {}
        self._updating  = False
        self.init_ui()

    # ============================================================
    # UI SETUP
    # ============================================================

    def setup_table(self, table: QTableWidget) -> None:
        table.setColumnCount(13)
        table.verticalHeader().setVisible(False)
        table.verticalHeader().setDefaultSectionSize(42)
        table.setHorizontalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
        table.setHorizontalHeaderLabels(self.HEADERS)

        hdr = table.horizontalHeader()
        hdr.setMinimumSectionSize(60)
        for col in (1, 7):
            table.setColumnWidth(col, 200)
            hdr.setSectionResizeMode(col, QHeaderView.ResizeMode.Fixed)
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
                padding: 6px 10px; font-weight: bold;
                background-color: #333; color: white;
            }
            QComboBox, QSpinBox  { padding: 4px; min-height: 26px; }
            QCheckBox            { margin-left: 6px; }
            QTabWidget::pane     { border: 1px solid #444; }
            QSpinBox::up-button, QSpinBox::down-button { width: 0px; border: none; }
            QScrollBar:horizontal { border: none; background: #222; height: 10px; }
            QScrollBar::handle:horizontal {
                background: #444; min-width: 30px; border-radius: 5px;
            }
            QScrollBar::handle:horizontal:hover { background: #666; }
            QGroupBox { font-weight: bold; margin-top: 6px; padding-top: 8px; }
        """)

        layout = QVBoxLayout()
        layout.setSpacing(10)
        layout.setContentsMargins(12, 12, 12, 12)

        # ── Match selector ────────────────────────────────────
        match_layout = QHBoxLayout()
        match_layout.addWidget(QLabel("Match:"))
        self.match_selector = QComboBox()
        self.match_selector.currentIndexChanged.connect(self.on_match_selected)
        self.load_matches()
        match_layout.addWidget(self.match_selector, stretch=1)
        layout.addLayout(match_layout)

        # ── Round metadata group ──────────────────────────────
        meta_group = QGroupBox("Round Info")
        meta_form  = QFormLayout(meta_group)
        meta_form.setSpacing(8)

        # Round number + Side on same row
        rn_side = QHBoxLayout()
        self.round_number_spin = QSpinBox()
        self.round_number_spin.setRange(1, 50)
        self.round_number_spin.setFixedWidth(60)
        rn_side.addWidget(self.round_number_spin)
        rn_side.addWidget(QLabel("Side:"))
        self.side_selector = QComboBox()
        self.side_selector.addItems(["attack", "defense"])
        self.side_selector.currentTextChanged.connect(self.populate_tables)
        self.side_selector.currentTextChanged.connect(self.update_objective_headers)
        self.side_selector.currentTextChanged.connect(self._update_resource_widgets)
        rn_side.addWidget(self.side_selector)
        rn_side.addStretch()
        meta_form.addRow("Round #:", rn_side)

        # Outcome
        self.outcome_selector = QComboBox()
        self.outcome_selector.addItems(["", "win", "loss"])
        meta_form.addRow("Outcome:", self.outcome_selector)

        # Site
        self.site_edit = QComboBox()
        self.site_edit.setEditable(True)
        self.site_edit.setMinimumWidth(180)
        meta_form.addRow("Site:", self.site_edit)

        # ── Resource spinboxes — always visible, relabel on side change ──
        res_row = QHBoxLayout()
        self.drones_label = QLabel("Drones Lost:")
        self.drones_spin = QComboBox()
        for i in range(11):
            self.drones_spin.addItem(str(i), i)

        
        self.drones_spin.setFixedWidth(55)
        res_row.addWidget(self.drones_label)
        res_row.addWidget(self.drones_spin)

        res_row.addSpacing(20)

        self.reinf_label = QLabel("Reinforcements Used:")
        self.reinf_spin = QComboBox()
        for i in range(11):
            self.reinf_spin.addItem(str(i), i)
        
        self.reinf_spin.setFixedWidth(55)
        res_row.addWidget(self.reinf_label)
        res_row.addWidget(self.reinf_spin)
        res_row.addStretch()
        meta_form.addRow("", res_row)

        layout.addWidget(meta_group)

        # ── Player tables ─────────────────────────────────────
        self.tabs = QTabWidget()

        self.team_table = QTableWidget()
        self.setup_table(self.team_table)
        self.team_table.setMinimumHeight(260)
        self.tabs.addTab(self.team_table, "Team")

        self.enemy_table = QTableWidget()
        self.setup_table(self.enemy_table)
        self.enemy_table.setMinimumHeight(260)
        self.tabs.addTab(self.enemy_table, "Enemies")

        self.tabs.currentChanged.connect(self._update_resource_widgets)

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
        self._update_resource_widgets()

    # ============================================================
    # RESOURCE WIDGETS
    # ============================================================

    def _update_resource_widgets(self) -> None:
        side = self.side_selector.currentText()
        # Get the current tab index: 0 is Team, 1 is Enemies
        current_tab = self.tabs.currentIndex()

        # Reset general enabling
        self.drones_spin.setEnabled(True)
        self.reinf_spin.setEnabled(True)

        if side == "attack":
            # If viewing Team (Attacking)
            if current_tab == 0:
                self.drones_label.setText("Team Drones Lost:")
                self.reinf_label.setText("Enemy Reinforcements:")
                self.reinf_spin.setCurrentIndex(0)
                self.reinf_spin.setEnabled(False)
            # If viewing Enemies (Defending)
            else:
                self.drones_label.setText("Team Drones Lost:")
                self.reinf_label.setText("Enemy Reinforcements:")
                self.drones_spin.setCurrentIndex(0)
                self.drones_spin.setEnabled(False)

        else: # side == "defense"
            # If viewing Team (Defending)
            if current_tab == 0:
                self.drones_label.setText("Enemy Drones:")
                self.reinf_label.setText("Team Reinforcements Used:")
                self.drones_spin.setCurrentIndex(0)
                self.drones_spin.setEnabled(False)
            # If viewing Enemies (Attacking)
            else:
                self.drones_label.setText("Enemy Drones:")
                self.reinf_label.setText("Team Reinforcements Used:")
                self.reinf_spin.setCurrentIndex(0)
                self.reinf_spin.setEnabled(False)

    # ============================================================
    # HEADER LABELS
    # ============================================================

    def update_objective_headers(self) -> None:
        team_side = self.side_selector.currentText()
        enemy_side = "defense" if team_side == "attack" else "attack"

        # Correct labels based on role
        t_atk_labels = ("Plant Attempted", "Plant Successful")
        t_def_labels = ("Defuse Attempted", "Defuse Successful")

        ta, ts = t_atk_labels if team_side == "attack" else t_def_labels
        ea, es = t_atk_labels if enemy_side == "attack" else t_def_labels

        self.team_table.setHorizontalHeaderItem(11, QTableWidgetItem(ta))
        self.team_table.setHorizontalHeaderItem(12, QTableWidgetItem(ts))
        self.enemy_table.setHorizontalHeaderItem(11, QTableWidgetItem(ea))
        self.enemy_table.setHorizontalHeaderItem(12, QTableWidgetItem(es))

    # ============================================================
    # MATCH SELECTOR
    # ============================================================

    def _update_sites_for_match(self, match_id: int | None) -> None:
        self.site_edit.blockSignals(True)
        self.site_edit.clear()
        self.site_edit.addItem("")
        if match_id is not None:
            try:
                match = self.repo.get_match(match_id)
                if match:
                    self.site_edit.addItems(self.MAP_SITES.get(match.map, []))
            except Exception:
                pass
        self.site_edit.blockSignals(False)

    def load_matches(self, select_match_id: int | None = None) -> None:
        self.match_selector.blockSignals(True)
        self.match_selector.clear()
        self.match_selector.addItem("➕ Create New Match", "NEW")
        self.match_selector.addItem("— Select a match —", None)

        matches      = self.repo.get_all_matches()
        target_index = 1

        for m in matches:
            idx = self.match_selector.count()
            self.match_selector.addItem(
                f"{m.match_id}: {m.opponent_name} ({m.map})", m.match_id
            )
            if select_match_id is not None and m.match_id == select_match_id:
                target_index = idx

        self.match_selector.blockSignals(False)
        self.match_selector.setCurrentIndex(target_index)

        if select_match_id is not None:
            self.current_match_id = select_match_id
            self.populate_tables()
            self._update_resource_widgets()
            self._update_sites_for_match(select_match_id)

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
        self._update_resource_widgets()
        self._update_sites_for_match(data)

    # ============================================================
    # TABLE POPULATION
    # ============================================================

    def clear_table_widgets(self, table: QTableWidget) -> None:
        for row in range(table.rowCount()):
            for col in range(table.columnCount()):
                widget = table.cellWidget(row, col)
                if widget:
                    self._secondary_handlers.pop(id(widget), None)
                    widget.deleteLater()
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
            self.populate_common_cells(self.team_table, row)

    def populate_enemy_table(self) -> None:
        for row in range(self.enemy_table.rowCount()):
            self.enemy_table.setItem(row, 0, QTableWidgetItem(""))
            self.populate_common_cells(self.enemy_table, row)

    def populate_common_cells(self, table: QTableWidget, row: int) -> None:
        if self._updating:
            return

        op_selector = QComboBox()
        table.setCellWidget(row, 1, op_selector)

        def on_op_change() -> None:
            if self._updating:
                return
            self.update_loadout(table, row)
            self.refresh_operator_dropdowns(table)
            self._enforce_single_success(table)

        op_selector.currentIndexChanged.connect(on_op_change)

        kills_spin = QSpinBox()
        kills_spin.setRange(0, 50)
        table.setCellWidget(row, 2, kills_spin)

        died_cb = QCheckBox()
        self._center_widget(table, row, 3, died_cb)

        assists_spin = QSpinBox()
        assists_spin.setRange(0, 50)
        table.setCellWidget(row, 4, assists_spin)

        eng_taken = QSpinBox()
        eng_taken.setRange(0, 50)
        table.setCellWidget(row, 5, eng_taken)

        eng_won = QSpinBox()
        eng_won.setRange(0, 50)
        table.setCellWidget(row, 6, eng_won)

        ability_label = QLabel("Ability")
        ability_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        table.setCellWidget(row, 7, ability_label)

        table.setCellWidget(row, 8, QComboBox())

        sec_selector = QComboBox()
        table.setCellWidget(row, 9, sec_selector)

        table.setCellWidget(row, 10, QComboBox())

        obj_attempt = QCheckBox()
        self._center_widget(table, row, 11, obj_attempt)

        obj_success = QCheckBox()
        obj_success.stateChanged.connect(
            lambda state, r=row, t=table: self._on_success_changed(state, r, t)
        )
        self._center_widget(table, row, 12, obj_success)

        self.update_loadout(table, row)
        self.refresh_operator_dropdowns(table)

    def _center_widget(self, table: QTableWidget, row: int, col: int, widget: QWidget) -> None:
        container = QWidget()
        lay = QHBoxLayout(container)
        lay.addWidget(widget)
        lay.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lay.setContentsMargins(0, 0, 0, 0)
        table.setCellWidget(row, col, container)

    # ============================================================
    # SINGLE-SUCCESS ENFORCEMENT
    # ============================================================

    def _on_success_changed(self, state: int, changed_row: int, table: QTableWidget) -> None:
        if state != Qt.CheckState.Checked.value:
            return
        self._enforce_single_success(table, checked_row=changed_row)

    def _enforce_single_success(self, table: QTableWidget, checked_row: int = -1) -> None:
        for row in range(table.rowCount()):
            container = table.cellWidget(row, 12)
            if container is None:
                continue
            cb = container.findChild(QCheckBox)
            if cb and row != checked_row and cb.isChecked():
                cb.blockSignals(True)
                cb.setChecked(False)
                cb.blockSignals(False)

    # ============================================================
    # LOADOUT
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
        row_key     = (id(table), row)

        if self._row_operator_cache.get(row_key) == operator_id:
            return
        self._row_operator_cache[row_key] = operator_id

        sec_widget.blockSignals(True)
        sec_widget.clear()
        sec_widget.addItem("None", None)

        if ability_label:
            ability_label.setText("Ability")

        old = table.cellWidget(row, 8)
        if old:
            old.deleteLater()
        table.setCellWidget(row, 8, QComboBox())

        old2 = table.cellWidget(row, 10)
        if old2:
            old2.deleteLater()
        table.setCellWidget(row, 10, QComboBox())

        if operator_id is None:
            sec_widget.blockSignals(False)
            return

        operator = self.repo.get_operator_by_id(operator_id)
        if operator is None:
            sec_widget.blockSignals(False)
            return

        if ability_label:
            ability_label.setText(operator.ability_name)

        if operator.ability_max_count <= 1:
            ab_widget: QWidget = QCheckBox()
        else:
            ab_dd = QComboBox()
            for i in range(operator.ability_max_count + 1):
                ab_dd.addItem(str(i), i)
            ab_widget = ab_dd
        table.setCellWidget(row, 8, ab_widget)

        gadget_map: dict[int, int] = {}
        for g in self.repo.get_gadgets_for_operator(operator_id):
            sec_widget.addItem(g.name, g.gadget_id)
            gadget_map[g.gadget_id] = g.max_count

        def update_secondary_uses() -> None:
            selected_id = sec_widget.currentData()
            max_count   = gadget_map.get(selected_id, 0)
            old_w = table.cellWidget(row, 10)
            if old_w:
                old_w.deleteLater()
            if max_count == 1 and (operator is None or operator.name != "Solid Snake"):
                nw: QWidget = QCheckBox()
            else:
                dd = QComboBox()
                for i in range(max_count + 1):
                    dd.addItem(str(i), i)
                nw = dd
            table.setCellWidget(row, 10, nw)

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
    # PREFILL
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
            r   = result.rounds[0]
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

        round_data: dict = {
            "match_id":     self.current_match_id,
            "round_number": self.round_number_spin.value(),
            "side":         side,
            "outcome":      outcome,
            "site":         site,
            "player_stats": [],
        }

        # Force strict 0-values to satisfy Database Check Constraints
        if side == "attack":
            round_data["team_drones_lost"] = self.drones_spin.currentData()
            round_data["team_reinforcements_used"] = 0 
        else:
            round_data["team_drones_lost"] = 0
            round_data["team_reinforcements_used"] = self.reinf_spin.currentData()

        for row, player in enumerate(self.players):
            table  = self.team_table
            op_box = cast(QComboBox, table.cellWidget(row, 1))
            kills  = cast(QSpinBox,  table.cellWidget(row, 2))

            died_container = table.cellWidget(row, 3)
            died_cb  = died_container.findChild(QCheckBox) if died_container else None
            deaths   = 1 if (died_cb and died_cb.isChecked()) else 0

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

            attempt_container = table.cellWidget(row, 11)
            attempt_cb = attempt_container.findChild(QCheckBox) if attempt_container else None

            success_container = table.cellWidget(row, 12)
            success_cb = success_container.findChild(QCheckBox) if success_container else None

            round_data["player_stats"].append({
                "player_id":           player.player_id,
                "operator_id":         op_box.currentData(),
                "kills":               kills.value(),
                "deaths":              deaths,
                "assists":             assists.value(),
                "engagements_taken":   eng_taken.value(),
                "engagements_won":     eng_won.value(),
                "ability_used":        ability_used,
                "secondary_gadget_id": sec_box.currentData(),
                "secondary_used":      secondary_used,
                "plant_attempted":     attempt_cb.isChecked() if attempt_cb else False,
                "plant_successful":    success_cb.isChecked() if success_cb else False,
            })

        try:
            self.controller.save_round(round_data)
            self.round_number_spin.setValue(self.round_number_spin.value() + 1)
            self.outcome_selector.setCurrentIndex(0)
            self.drones_spin.setCurrentIndex(0)
            self.reinf_spin.setCurrentIndex(0)
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