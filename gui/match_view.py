from email import header

from PySide6.QtWidgets import (
    QAbstractItemView, QInputDialog, QWidget, QVBoxLayout, QHBoxLayout, QLabel, QComboBox,
    QTableWidget, QTableWidgetItem, QPushButton, QSpinBox,
    QCheckBox, QMessageBox, QHeaderView, QTabWidget, QAbstractScrollArea
)
from PySide6.QtCore import QTimer, Qt
from typing import cast

from app.app_controller import AppController
from database.db_manager import DatabaseManager
from database.repositories import Repository
from models import operator


class MatchView(QWidget):

    def __init__(self, parent=None):
        super().__init__(parent)

        self.controller = AppController()
        self.repo = Repository()
        self.currently_selected_ids = set()
        self.current_match_id = None
        self.players = self.repo.get_team_players()[:5]
        self.operators = self.repo.get_all_operators()
        self._secondary_handlers = {}  # Maps id(sec_widget) -> handler function
        self._row_operator_cache = {}
        self._updating = False
        self.init_ui()

    # ============================================================
    # UI SETUP
    # ============================================================
    def create_safe_widget(self, widget_class):
            """
            Creates a widget. 
            Inherits the global 10pt font from main.py automatically.
            """
            return widget_class()
    
    def setup_table(self, table: QTableWidget, is_team: bool):
        table.setColumnCount(13)
        
        # NEW: Hide the numbers on the left side
        table.verticalHeader().setVisible(False)
        table.verticalHeader().setDefaultSectionSize(42)

        # NEW: Enable smooth, pixel-based scrolling
        table.setHorizontalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)

        headers = [
            "Player", "Operator", "Kills", "Deaths", "Assists",
            "Eng Taken", "Eng Won",
            "Ability", "Ability Uses", "Secondary", "Secondary Used",
            "Plant Attempted", "Plant Successful"
        ]

        table.setHorizontalHeaderLabels(headers)
        header = table.horizontalHeader()
        
        # Prevent columns from squishing too much
        header.setMinimumSectionSize(100)

        Fixed_width_cols = [1, 7]  # Operator and Ability
        for col in Fixed_width_cols:
            table.setColumnWidth(col, 200)
            header.setSectionResizeMode(col, QHeaderView.ResizeMode.Fixed)

        header.setStretchLastSection(True)
        
        content_cols = [0, 2, 3, 4, 5, 6, 8, 9, 10, 11, 12]
        for col in content_cols:
            header.setSectionResizeMode(col, QHeaderView.ResizeMode.ResizeToContents)

        table.setAlternatingRowColors(True)
        table.setRowCount(5)
        
        # Scrollbar visibility settings
        table.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        table.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        table.setSizeAdjustPolicy(QAbstractScrollArea.SizeAdjustPolicy.AdjustToContents)
    def init_ui(self):
        # Using a wildcard '*' ensures all sub-widgets (labels, combo boxes) 
        # inside MatchView follow the same rules.
        self.setStyleSheet("""
            /* Global settings for all children */
            QWidget {
                /* font-size: 13px; */ /* Removed to rely on global font set in main.py */
            }

            /* Table specific styling */
            QTableWidget { 
                gridline-color: #444;
                /* font-size: 14px; */
            }

            QTableWidget::item { 
                padding: 6px;
            }

            /* Header styling */
            QHeaderView::section { 
                padding: 8px; 
                padding-left: 12px;
                padding-right: 12px;
                font-weight: bold;
                /* font-size: 12px; */
                background-color: #333; /* Optional: makes headers stand out */
                color: white;
            }

            /* Inputs and Controls */
            QComboBox, QSpinBox {
                padding: 4px;
                min-height: 26px;
            }

            QCheckBox {
                margin-left: 8px;
            }
            
            /* Tab styling to match the dark grid */
            QTabWidget::pane { 
                border: 1px solid #444;
            }
            /* This removes the up/down arrows from all SpinBoxes */
            QSpinBox::up-button, QSpinBox::down-button {
                width: 0px;
                border: none;
            }

            /* Optional: Center the text since the arrows are gone */
            QSpinBox {
                padding-right: 2px;
                alignment: Qt.AlignCenter;
            }
            /* Custom Scrollbar Styling */
            QScrollBar:horizontal {
                border: none;
                background: #222;
                height: 12px; /* Slightly taller for easier clicking */
                margin: 0px;
            }

            QScrollBar::handle:horizontal {
                background: #444; /* Dimmer by default */
                min-width: 30px;
                border-radius: 6px;
            }

            QScrollBar::handle:horizontal:hover {
                background: #666; /* Brightens when you hover */
            }
        """)

        
        
        layout = QVBoxLayout()
        layout.setSpacing(18)
        layout.setContentsMargins(12, 12, 12, 12)

        # Match selector
        match_layout = QHBoxLayout()
        match_layout.addWidget(QLabel("Select Match:"))
        self.match_selector = QComboBox()
        self.match_selector.addItem("Select a match...", None)
        self.match_selector.currentIndexChanged.connect(self.on_match_selected)
        self.load_matches()


        match_layout.addWidget(self.match_selector)
        layout.addLayout(match_layout)

        # Round controls
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
        round_layout.addWidget(self.side_selector)

        layout.addLayout(round_layout)

        # Tabs
        self.tabs = QTabWidget()

        self.team_table = QTableWidget()
        self.setup_table(self.team_table, True)
        self.team_table.setMinimumHeight(320)
        self.tabs.addTab(self.team_table, "Team")
        
        self.enemy_table = QTableWidget()
        self.setup_table(self.enemy_table, False)
        
        self.enemy_table.setMinimumHeight(320)
        self.tabs.addTab(self.enemy_table, "Enemies")

        layout.addWidget(self.tabs)

        # Resource label
        self.resource_label = QLabel()
        layout.addWidget(self.resource_label)

        # Buttons
        btn_layout = QHBoxLayout()

        save_btn = QPushButton("Save Round")
        save_btn.clicked.connect(self.save_round)
        btn_layout.addWidget(save_btn)

        report_btn = QPushButton("Generate Report")
        report_btn.clicked.connect(self.generate_report)
        btn_layout.addWidget(report_btn)

        layout.addLayout(btn_layout)

        self.setLayout(layout)

        # Style polish

        self.populate_tables()
        self.update_objective_headers()
        self.update_resource_label()

    def refresh_all_loadouts(self):
        for table in (self.team_table, self.enemy_table):
            for row in range(table.rowCount()):
                self.update_loadout(table, row)
    def update_objective_headers(self):

        def get_labels(side: str):
            if side == "attack":
                return "Plant Attempted", "Plant Successful"
            else:
                return "Defuse Attempted", "Defuse Successful"

        team_side = self.side_selector.currentText()
        enemy_side = "defense" if team_side == "attack" else "attack"

        # Team table headers
        team_attempt, team_success = get_labels(team_side)
        self.team_table.setHorizontalHeaderItem(11, QTableWidgetItem(team_attempt))
        self.team_table.setHorizontalHeaderItem(12, QTableWidgetItem(team_success))

        # Enemy table headers
        enemy_attempt, enemy_success = get_labels(enemy_side)
        self.enemy_table.setHorizontalHeaderItem(11, QTableWidgetItem(enemy_attempt))
        self.enemy_table.setHorizontalHeaderItem(12, QTableWidgetItem(enemy_success))
    # ============================================================
    # MATCH
    # ============================================================

    def load_matches(self, select_match_id: int | None = None):
        """Repopulate the match selector. Optionally auto-select a match by ID."""
        self.match_selector.blockSignals(True)
        self.match_selector.clear()
        self.match_selector.addItem("➕ Create New Match", "NEW")
        self.match_selector.addItem("— Select a match —", None)

        matches = self.repo.get_all_matches()
        target_index = 1  # default to placeholder

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

        # Manually trigger selection for the pre-selected match
        if select_match_id is not None:
            self.current_match_id = select_match_id
            self.populate_tables()
            self.update_resource_label()

    def on_match_selected(self, index):
        data = self.match_selector.currentData()

        # ── Create New Match ─────────────────────────────────────
        if data == "NEW":
            opponent, ok1 = QInputDialog.getText(self, "New Match", "Opponent Name:")
            if not ok1 or not opponent.strip():
                # User cancelled — snap back to placeholder without triggering again
                self.match_selector.blockSignals(True)
                self.match_selector.setCurrentIndex(1)
                self.match_selector.blockSignals(False)
                return

            maps = self.repo.get_all_maps()
            if not maps:
                QMessageBox.critical(self, "Error", "No maps in database. Run the seeder first.")
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
                self.load_matches(select_match_id=match_id)  # reload AND auto-select
            except Exception as e:
                QMessageBox.critical(self, "Error", str(e))
            return

        # ── Placeholder ──────────────────────────────────────────
        if data is None:
            self.current_match_id = None
            return

        # ── Normal match selected ────────────────────────────────
        self.current_match_id = data
        self.round_number_spin.setValue(1)
        self.populate_tables()
        self.update_resource_label()

    def clear_table_widgets(self, table):
        for row in range(table.rowCount()):
            for col in range(table.columnCount()):
                widget = table.cellWidget(row, col)
                if widget:
                    self._secondary_handlers.pop(id(widget), None)  # clean up stale entries
                    widget.deleteLater()
                    table.setCellWidget(row, col, None)
    # ============================================================
    # TABLE POPULATION
    # ============================================================

    def populate_tables(self):
        self.team_table.blockSignals(True)
        self.enemy_table.blockSignals(True)
        print("Rebuilding tables...")
        # 🔥 CLEAR OLD WIDGETS FIRST
        self.clear_table_widgets(self.team_table)
        self.clear_table_widgets(self.enemy_table)

        self.populate_team_table()
        self.populate_enemy_table()

        self.team_table.blockSignals(False)
        self.enemy_table.blockSignals(False)

        self.refresh_operator_dropdowns(self.team_table)
        self.refresh_operator_dropdowns(self.enemy_table)

        self.refresh_all_loadouts()
    def populate_team_table(self):
        self.team_table.setRowCount(5)

        for row, player in enumerate(self.players):
            item = QTableWidgetItem(player.name)
            item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEditable)
            self.team_table.setItem(row, 0, item)

            self.populate_common_cells(self.team_table, row, True)

    def populate_enemy_table(self):
        for row in range(self.enemy_table.rowCount()):
            self.enemy_table.setItem(row, 0, QTableWidgetItem(""))
            self.populate_common_cells(self.enemy_table, row, False)

    def populate_common_cells(self, table, row, is_team):
            if getattr(self, "_updating", False):
                return
                
            side = self.side_selector.currentText()
            if not is_team:
                side = "defense" if side == "attack" else "attack"

            # Create widgets using the simplified helper
            op_selector = self.create_safe_widget(QComboBox)
            sec_selector = self.create_safe_widget(QComboBox)

            # Set them into the table FIRST so the refresh logic can find them
            table.setCellWidget(row, 1, op_selector)
            table.setCellWidget(row, 9, sec_selector)

            # The Signal: When one changes, refresh everyone else to hide that operator
            def on_change():
                if self._updating: return
                self.update_loadout(table, row)
                self.refresh_operator_dropdowns(table)

            op_selector.currentIndexChanged.connect(on_change)

            # Populate other columns
            for col in range(2, 7):
                spin = self.create_safe_widget(QSpinBox)
                spin.setRange(0, 50)
                table.setCellWidget(row, col, spin)


            # Ability NAME (column 7)
            ability_label = QLabel("Ability")
            ability_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
            table.setCellWidget(row, 7, ability_label)

            # Ability USES (column 8)
            ability_dropdown = self.create_safe_widget(QComboBox)
            table.setCellWidget(row, 8, ability_dropdown)

            sec_used_dropdown = self.create_safe_widget(QComboBox)
            table.setCellWidget(row, 10, sec_used_dropdown)

            table.setCellWidget(row, 11, QCheckBox())
            table.setCellWidget(row, 12, QCheckBox())

            # Final step: Force a refresh to hide already-picked ops from this new row
            self.update_loadout(table, row)
            self.refresh_operator_dropdowns(table)
    def update_loadout(self, table, row):
        op_widget = cast(QComboBox, table.cellWidget(row, 1))
        sec_widget = cast(QComboBox, table.cellWidget(row, 9))
        ability_label = cast(QLabel, table.cellWidget(row, 7))
        ability_widget = table.cellWidget(row, 8)
        sec_used_dropdown = cast(QComboBox, table.cellWidget(row, 10))

        if not op_widget or not sec_widget:
            return

        operator_id = op_widget.currentData()
        row_key = (id(table), row)
        previous_operator = self._row_operator_cache.get(row_key)
        operator_changed = operator_id != previous_operator
        self._row_operator_cache[row_key] = operator_id

        # ✅ Only do ANYTHING if operator actually changed
        if not operator_changed:
            return

        # Reset
        sec_widget.blockSignals(True)
        sec_widget.clear()
        sec_widget.addItem("None", None)

        ability_widget = table.cellWidget(row, 8)

        if ability_label:
            ability_label.setText("Ability")

        if ability_widget:
            ability_widget.deleteLater()
            table.setCellWidget(row, 8, None)

        if sec_used_dropdown:
            sec_used_dropdown.blockSignals(True)
            sec_used_dropdown.clear()
            sec_used_dropdown.blockSignals(False)

        # Populate if an operator is selected
        if operator_id is not None:
            operator = self.repo.get_operator_by_id(operator_id)

            # -------------------------
            # ABILITY HANDLING
            # -------------------------
            if operator and ability_label:

                ability_label.setText(operator.ability_name)

                # Remove existing widget first
                if ability_widget:
                    ability_widget.deleteLater()

                # CASE 1: Checkbox (binary abilities or toggles)
                if operator.ability_max_count <= 1:
                    ability_checkbox = QCheckBox()
                    table.setCellWidget(row, 8, ability_checkbox)

                # CASE 2: Numeric dropdown
                else:
                    ability_dropdown = QComboBox()
                    table.setCellWidget(row, 8, ability_dropdown)

                    ability_dropdown.blockSignals(True)
                    for i in range(operator.ability_max_count + 1):
                        ability_dropdown.addItem(str(i), i)
                    ability_dropdown.setCurrentIndex(0)
                    ability_dropdown.blockSignals(False)

            gadget_map = {}
            gadgets = self.repo.get_gadgets_for_operator(operator_id)
            for g in gadgets:
                sec_widget.addItem(g.name, g.gadget_id)
                gadget_map[g.gadget_id] = g.max_count

            def update_secondary_uses():
                if not sec_used_dropdown:
                    return

                selected_id = sec_widget.currentData()
                max_count = gadget_map.get(selected_id, 0)

                operator_name = operator.name if operator else ""

                # Remove existing widget first
                existing_widget = table.cellWidget(row, 10)
                if existing_widget:
                    existing_widget.deleteLater()

                # --------------------------------------
                # CASE 1: Checkbox (max = 1)
                # EXCEPT: Solid Snake
                # --------------------------------------
                if max_count == 1 and operator_name != "Solid Snake":
                    checkbox = QCheckBox()
                    table.setCellWidget(row, 10, checkbox)

                # --------------------------------------
                # CASE 2: Dropdown (normal behavior)
                # --------------------------------------
                else:
                    dropdown = QComboBox()
                    table.setCellWidget(row, 10, dropdown)

                    dropdown.blockSignals(True)
                    for i in range(max_count + 1):
                        dropdown.addItem(str(i), i)
                    dropdown.setCurrentIndex(0)
                    dropdown.blockSignals(False)

            # Disconnect previous handler if one exists
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
    def update_resource_label(self):
        if self.side_selector.currentText() == "attack":
            self.resource_label.setText("Team Drones Lost (Start = 10)")
        else:
            self.resource_label.setText("Team Reinforcements Used (Start = 10)")


    def refresh_operator_dropdowns(self, table):
        if self._updating:
            return
        self._updating = True

        # 🔥 STEP 1: Build a FULL snapshot (do NOT mutate this later)
        full_selected_ids = set()

        for row in range(table.rowCount()):
            op_widget = cast(QComboBox, table.cellWidget(row, 1))
            if op_widget:
                op_id = op_widget.currentData()
                if op_id is not None:
                    full_selected_ids.add(op_id)

        # 🔥 STEP 2: Rebuild each dropdown independently
        for row in range(table.rowCount()):
            op_widget = cast(QComboBox, table.cellWidget(row, 1))
            if not op_widget:
                continue

            # 🔥 CRITICAL: capture BEFORE any mutation happens
            current_id = op_widget.currentData()
            if current_id is None and op_widget.count() > 0:
                current_id = op_widget.itemData(op_widget.currentIndex())

            # Create a COPY so we don't mutate global snapshot
            selected_ids = full_selected_ids.copy()

            # Remove this row’s current selection so it can keep it
            if current_id in selected_ids:
                selected_ids.remove(current_id)

            op_widget.blockSignals(True)

            # Store current text too (extra safety)
            current_text = op_widget.currentText()

            op_widget.clear()
            side = self.side_selector.currentText()
            if table is self.enemy_table:
                side = "defense" if side == "attack" else "attack"

            operators = self.repo.get_all_operators()

            for op in operators:
                if op.side != side:
                    continue

                # 🔥 ONLY allow operators NOT selected elsewhere
                if op.operator_id not in selected_ids:
                    op_widget.addItem(op.name, op.operator_id)

            # Restore selection by ID FIRST
            if current_id is not None:
                index = op_widget.findData(current_id)
                if index >= 0:
                    op_widget.setCurrentIndex(index)
                else:
                    # fallback to text match (rare edge case)
                    index = op_widget.findText(current_text)
                    if index >= 0:
                        op_widget.setCurrentIndex(index)

            op_widget.blockSignals(False)

        self._updating = False

    # ============================================================
    # SAVE
    # ============================================================

    def save_round(self):

        if self.current_match_id is None:
            QMessageBox.warning(self, "Error", "No match selected.")
            return

        round_data = {
            "match_id": self.current_match_id,
            "round_number": self.round_number_spin.value(),
            "side": self.side_selector.currentText(),
            "player_stats": []
        }

        for row, player in enumerate(self.players):

            table = self.team_table

            op_box = cast(QComboBox, table.cellWidget(row, 1))

            kills = cast(QSpinBox, table.cellWidget(row, 2))
            deaths = cast(QSpinBox, table.cellWidget(row, 3))
            assists = cast(QSpinBox, table.cellWidget(row, 4))

            eng_taken = cast(QSpinBox, table.cellWidget(row, 5))
            eng_won = cast(QSpinBox, table.cellWidget(row, 6))

            ability_widget = table.cellWidget(row, 8)  # ✅ defined here

            sec_box = cast(QComboBox, table.cellWidget(row, 9))
            sec_widget = table.cellWidget(row, 10)

            if isinstance(sec_widget, QCheckBox):
                secondary_used = 1 if sec_widget.isChecked() else 0
            elif isinstance(sec_widget, QComboBox):
                secondary_used = sec_widget.currentData()
            else:
                secondary_used = 0

            plant_attempt = cast(QCheckBox, table.cellWidget(row, 11))
            plant_success = cast(QCheckBox, table.cellWidget(row, 12))

            # ----------------------------
            # Ability Used (FIXED PROPERLY)
            # ----------------------------
            if isinstance(ability_widget, QCheckBox):
                ability_used = 1 if ability_widget.isChecked() else 0
            elif isinstance(ability_widget, QComboBox):
                ability_used = ability_widget.currentData()
            else:
                ability_used = 0

            stats = {
                "player_id": player.player_id,
                "operator_id": op_box.currentData(),

                "kills": kills.value(),
                "deaths": deaths.value(),
                "assists": assists.value(),

                "engagements_taken": eng_taken.value(),
                "engagements_won": eng_won.value(),

                "ability_used": ability_used,

                "secondary_gadget_id": sec_box.currentData(),
                "secondary_used": secondary_used,

                "plant_attempted": plant_attempt.isChecked(),
                "plant_successful": plant_success.isChecked(),
            }

            round_data["player_stats"].append(stats)

        try:
            self.controller.save_round(round_data)
            QMessageBox.information(self, "Success", "Round saved!")
        except Exception as e:
            QMessageBox.critical(self, "Error", str(e))


    # ============================================================
    # REPORT
    # ============================================================

    def generate_report(self):
        if not self.current_match_id:
            QMessageBox.warning(self, "Error", "No match selected.")
            return

        try:
            self.controller.process_completed_match(self.current_match_id)
            QMessageBox.information(self, "Done", "Report generated!")
        except Exception as e:
            QMessageBox.critical(self, "Error", str(e))