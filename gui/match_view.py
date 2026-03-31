from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QComboBox,
    QTableWidget, QTableWidgetItem, QPushButton, QSpinBox,
    QCheckBox, QMessageBox, QHeaderView, QTabWidget, QAbstractScrollArea
)
from PySide6.QtCore import QTimer, Qt
from typing import cast

from app.app_controller import AppController
from database.repositories import Repository


class MatchView(QWidget):

    def __init__(self, parent=None):
        super().__init__(parent)

        self.controller = AppController()
        self.repo = Repository()
        self.currently_selected_ids = set()
        self.current_match_id = None
        self.players = self.repo.get_team_players()[:5]
        self.operators = self.repo.get_all_operators()
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
        table.setColumnCount(12)
        table.verticalHeader().setDefaultSectionSize(42)

        headers = [
            "Player", "Operator", "Kills", "Deaths", "Assists",
            "Eng Taken", "Eng Won",
            "Ability Used", "Secondary", "Secondary Used",
            "Plant Attempted", "Plant Successful"
        ]

        table.setHorizontalHeaderLabels(headers)

        header = table.horizontalHeader()

        header.setSectionResizeMode(QHeaderView.ResizeMode.Interactive)

        table.setColumnWidth(0, 140)  # Player
        table.setColumnWidth(1, 180)  # Operator (BIG FIX)
        table.setColumnWidth(8, 180)  # Secondary (BIG FIX)

        for col in range(12):
            if col not in (0, 1, 8):
                header.setSectionResizeMode(col, QHeaderView.ResizeMode.Stretch)

        table.setAlternatingRowColors(True)

        table.setRowCount(5)

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
        """)

        
        
        layout = QVBoxLayout()
        layout.setSpacing(18)
        layout.setContentsMargins(12, 12, 12, 12)

        # Match selector
        match_layout = QHBoxLayout()
        match_layout.addWidget(QLabel("Select Match:"))

        self.match_selector = QComboBox()
        self.load_matches()
        self.match_selector.currentIndexChanged.connect(self.on_match_selected)

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
        self.update_resource_label()

    def refresh_all_gadgets(self):
        for table in (self.team_table, self.enemy_table):
            for row in range(table.rowCount()):
                self.update_gadgets(table, row)
    # ============================================================
    # MATCH
    # ============================================================

    def load_matches(self):
        matches = self.repo.get_all_matches()
        self.match_selector.clear()

        for m in matches:
            self.match_selector.addItem(
                f"{m.match_id}: {m.opponent_name} ({m.map_name})",
                m.match_id
            )

        if matches:
            self.current_match_id = matches[0].match_id

    def on_match_selected(self, index):
        self.current_match_id = self.match_selector.currentData()
        self.populate_tables()

    def clear_table_widgets(self, table):
        for row in range(table.rowCount()):
            for col in range(table.columnCount()):
                widget = table.cellWidget(row, col)
                if widget:
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
            table.setCellWidget(row, 8, sec_selector)

            # The Signal: When one changes, refresh everyone else to hide that operator
            def on_change():
                if self._updating: return
                self.update_gadgets(table, row)
                self.refresh_operator_dropdowns(table)

            op_selector.currentIndexChanged.connect(on_change)

            # Populate other columns
            for col in range(2, 7):
                spin = self.create_safe_widget(QSpinBox)
                spin.setRange(0, 50)
                table.setCellWidget(row, col, spin)

            ability_spin = self.create_safe_widget(QSpinBox)
            ability_spin.setRange(0, 10)
            table.setCellWidget(row, 7, ability_spin)

            sec_used = self.create_safe_widget(QSpinBox)
            sec_used.setRange(0, 10)
            table.setCellWidget(row, 9, sec_used)

            table.setCellWidget(row, 10, QCheckBox())
            table.setCellWidget(row, 11, QCheckBox())

            # Final step: Force a refresh to hide already-picked ops from this new row
            self.update_gadgets(table, row)
            self.refresh_operator_dropdowns(table)
    def update_gadgets(self, table, row):
        op_widget = cast(QComboBox, table.cellWidget(row, 1))
        sec_widget = cast(QComboBox, table.cellWidget(row, 8))

        if not op_widget or not sec_widget:
            return

        operator_id = op_widget.currentData()

        sec_widget.blockSignals(True)
        sec_widget.clear()
        sec_widget.addItem("None", None)

        if operator_id is not None:
            gadgets = self.repo.get_gadgets_for_operator(operator_id)

            for g in gadgets:
                sec_widget.addItem(g.name, g.gadget_id)

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

            for op in self.operators:
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

        if not self.current_match_id:
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

            # 🔥 CAST EVERYTHING PROPERLY
            op_box = cast(QComboBox, table.cellWidget(row, 1))

            kills = cast(QSpinBox, table.cellWidget(row, 2))
            deaths = cast(QSpinBox, table.cellWidget(row, 3))
            assists = cast(QSpinBox, table.cellWidget(row, 4))

            eng_taken = cast(QSpinBox, table.cellWidget(row, 5))
            eng_won = cast(QSpinBox, table.cellWidget(row, 6))

            ability = cast(QSpinBox, table.cellWidget(row, 7))

            sec_box = cast(QComboBox, table.cellWidget(row, 8))
            sec_used = cast(QSpinBox, table.cellWidget(row, 9))

            plant_attempt = cast(QCheckBox, table.cellWidget(row, 10))
            plant_success = cast(QCheckBox, table.cellWidget(row, 11))

            stats = {
                "player_id": player.player_id,
                "operator_id": op_box.currentData(),

                "kills": kills.value(),
                "deaths": deaths.value(),
                "assists": assists.value(),

                "engagements_taken": eng_taken.value(),
                "engagements_won": eng_won.value(),

                "ability_used": ability.value(),

                "secondary_gadget_id": sec_box.currentData(),
                "secondary_used": sec_used.value(),

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