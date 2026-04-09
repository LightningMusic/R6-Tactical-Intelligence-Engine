from pathlib import Path

from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QFormLayout,
    QLabel, QLineEdit, QPushButton, QSpinBox, QCheckBox,
    QGroupBox, QTableWidget, QTableWidgetItem, QHeaderView,
    QMessageBox, QFileDialog, QTabWidget, QComboBox, QAbstractItemView
)
from PySide6.QtCore import Qt


class SettingsView(QWidget):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._build_ui()
        self._load_all()

    # =====================================================
    # UI BUILD
    # =====================================================

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 20, 20, 20)
        layout.setSpacing(16)

        header = QLabel("Settings")
        header.setStyleSheet("font-size: 22px; font-weight: bold;")
        header.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(header)

        tabs = QTabWidget()
        tabs.addTab(self._build_general_tab(),  "⚙  General")
        tabs.addTab(self._build_obs_tab(),       "🎙  OBS")
        tabs.addTab(self._build_players_tab(),   "👥  Players")
        tabs.addTab(self._build_maps_tab(),      "🗺  Maps")
        tabs.addTab(self._build_matches_tab(),   "📋  Match Manager")
        tabs.addTab(self._build_ai_tab(),        "🤖  AI / Models")
        layout.addWidget(tabs)

    # ─────────────────────────────────────────────────────
    # TAB: GENERAL
    # ─────────────────────────────────────────────────────

    def _build_general_tab(self) -> QWidget:
        w = QWidget()
        layout = QVBoxLayout(w)
        layout.setSpacing(16)

        # Replay folder
        replay_group = QGroupBox("R6 Replay Folder")
        replay_layout = QHBoxLayout(replay_group)
        self._replay_folder_edit = QLineEdit()
        self._replay_folder_edit.setPlaceholderText("Path to R6 MatchReplay folder...")
        browse_btn = QPushButton("Browse")
        browse_btn.clicked.connect(self._browse_replay_folder)
        replay_layout.addWidget(self._replay_folder_edit, stretch=1)
        replay_layout.addWidget(browse_btn)
        layout.addWidget(replay_group)

        # Stability settings
        stab_group = QGroupBox("File Stability (Import)")
        stab_form = QFormLayout(stab_group)

        self._stability_wait_spin = QSpinBox()
        self._stability_wait_spin.setRange(1, 30)
        self._stability_wait_spin.setSuffix("  seconds")
        self._stability_wait_spin.setValue(5)

        self._stability_checks_spin = QSpinBox()
        self._stability_checks_spin.setRange(1, 10)
        self._stability_checks_spin.setValue(4)

        stab_form.addRow("Wait between checks:", self._stability_wait_spin)
        stab_form.addRow("Number of checks:", self._stability_checks_spin)
        layout.addWidget(stab_group)

        # Transcription toggle
        trans_group = QGroupBox("Transcription")
        trans_layout = QVBoxLayout(trans_group)
        self._transcribe_checkbox = QCheckBox("Auto-transcribe session audio after import")
        self._transcribe_checkbox.setChecked(True)
        trans_layout.addWidget(self._transcribe_checkbox)
        layout.addWidget(trans_group)

        save_btn = QPushButton("Save General Settings")
        save_btn.clicked.connect(self._save_general)
        layout.addWidget(save_btn)
        layout.addStretch()
        return w

    # ─────────────────────────────────────────────────────
    # TAB: OBS
    # ─────────────────────────────────────────────────────

    def _build_obs_tab(self) -> QWidget:
        w = QWidget()
        layout = QVBoxLayout(w)
        layout.setSpacing(16)

        obs_group = QGroupBox("OBS WebSocket Connection")
        form = QFormLayout(obs_group)

        self._obs_host_edit = QLineEdit("localhost")
        self._obs_port_spin = QSpinBox()
        self._obs_port_spin.setRange(1, 65535)
        self._obs_port_spin.setValue(4455)
        self._obs_password_edit = QLineEdit()
        self._obs_password_edit.setEchoMode(QLineEdit.EchoMode.Password)
        self._obs_scene_edit = QLineEdit("R6_Intelligence")

        form.addRow("Host:",     self._obs_host_edit)
        form.addRow("Port:",     self._obs_port_spin)
        form.addRow("Password:", self._obs_password_edit)
        form.addRow("Scene:",    self._obs_scene_edit)
        layout.addWidget(obs_group)

        test_btn = QPushButton("Test Connection")
        test_btn.clicked.connect(self._test_obs)
        layout.addWidget(test_btn)

        save_btn = QPushButton("Save OBS Settings")
        save_btn.clicked.connect(self._save_obs)
        layout.addWidget(save_btn)
        layout.addStretch()
        return w

    # ─────────────────────────────────────────────────────
    # TAB: PLAYERS
    # ─────────────────────────────────────────────────────

    def _build_players_tab(self) -> QWidget:
        w = QWidget()
        layout = QVBoxLayout(w)
        layout.setSpacing(12)

        layout.addWidget(QLabel("Team player names (5 players):"))

        self._player_edits: list[QLineEdit] = []
        for i in range(5):
            row = QHBoxLayout()
            row.addWidget(QLabel(f"Player {i+1}:"))
            edit = QLineEdit()
            self._player_edits.append(edit)
            row.addWidget(edit)
            layout.addLayout(row)

        save_btn = QPushButton("Save Players")
        save_btn.clicked.connect(self._save_players)
        layout.addWidget(save_btn)
        layout.addStretch()
        return w

    # ─────────────────────────────────────────────────────
    # TAB: MAPS
    # ─────────────────────────────────────────────────────

    def _build_maps_tab(self) -> QWidget:
        w = QWidget()
        layout = QVBoxLayout(w)
        layout.setSpacing(12)

        layout.addWidget(QLabel("Toggle maps in/out of the active pool:"))

        self._maps_table = QTableWidget(0, 2)
        self._maps_table.setHorizontalHeaderLabels(["Map", "Active Pool"])
        self._maps_table.horizontalHeader().setSectionResizeMode(
            0, QHeaderView.ResizeMode.Stretch
        )
        self._maps_table.horizontalHeader().setSectionResizeMode(
            1, QHeaderView.ResizeMode.ResizeToContents
        )
        self._maps_table.verticalHeader().setVisible(False)
        self._maps_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        layout.addWidget(self._maps_table)

        save_btn = QPushButton("Save Map Pool")
        save_btn.clicked.connect(self._save_maps)
        layout.addWidget(save_btn)
        return w

    # ─────────────────────────────────────────────────────
    # TAB: MATCH MANAGER
    # ─────────────────────────────────────────────────────

    def _build_matches_tab(self) -> QWidget:
        w = QWidget()
        layout = QVBoxLayout(w)
        layout.setSpacing(12)

        layout.addWidget(QLabel("Manage existing matches:"))

        self._matches_table = QTableWidget(0, 5)
        self._matches_table.setHorizontalHeaderLabels(
            ["ID", "Opponent", "Map", "Result", "Date"]
        )
        for col in range(5):
            self._matches_table.horizontalHeader().setSectionResizeMode(
                col, QHeaderView.ResizeMode.ResizeToContents
            )
        self._matches_table.horizontalHeader().setSectionResizeMode(
            1, QHeaderView.ResizeMode.Stretch
        )
        self._matches_table.verticalHeader().setVisible(False)
        self._matches_table.setSelectionBehavior(
            QAbstractItemView.SelectionBehavior.SelectRows
        )
        self._matches_table.setEditTriggers(
            QAbstractItemView.EditTrigger.NoEditTriggers
        )
        layout.addWidget(self._matches_table)

        btn_row = QHBoxLayout()

        set_result_btn = QPushButton("Set Result (Win/Loss)")
        set_result_btn.clicked.connect(self._set_match_result)
        btn_row.addWidget(set_result_btn)

        delete_btn = QPushButton("🗑  Delete Selected Match")
        delete_btn.setStyleSheet("color: #e05555;")
        delete_btn.clicked.connect(self._delete_match)
        btn_row.addWidget(delete_btn)

        refresh_btn = QPushButton("↺  Refresh")
        refresh_btn.clicked.connect(self._load_matches)
        btn_row.addWidget(refresh_btn)

        layout.addLayout(btn_row)
        return w

    # ─────────────────────────────────────────────────────
    # TAB: AI / MODELS
    # ─────────────────────────────────────────────────────

    def _build_ai_tab(self) -> QWidget:
        w = QWidget()
        layout = QVBoxLayout(w)
        layout.setSpacing(16)

        # Model status
        status_group = QGroupBox("Model Status")
        status_layout = QFormLayout(status_group)

        self._llm_status_label  = QLabel("Checking...")
        self._whisper_status_label = QLabel("Checking...")
        status_layout.addRow("LLM (llama-cpp):", self._llm_status_label)
        status_layout.addRow("Whisper:",         self._whisper_status_label)
        layout.addWidget(status_group)

        # LLM settings
        llm_group = QGroupBox("LLM Settings")
        llm_form = QFormLayout(llm_group)

        self._gpu_layers_spin = QSpinBox()
        self._gpu_layers_spin.setRange(0, 100)
        self._gpu_layers_spin.setValue(0)
        self._gpu_layers_spin.setSpecialValueText("0 (CPU only)")

        self._ctx_spin = QSpinBox()
        self._ctx_spin.setRange(512, 16384)
        self._ctx_spin.setSingleStep(512)
        self._ctx_spin.setValue(4096)

        self._threads_spin = QSpinBox()
        self._threads_spin.setRange(1, 32)
        self._threads_spin.setValue(6)

        llm_form.addRow("GPU Layers:",    self._gpu_layers_spin)
        llm_form.addRow("Context Size:",  self._ctx_spin)
        llm_form.addRow("CPU Threads:",   self._threads_spin)
        layout.addWidget(llm_group)

        # Whisper settings
        whisper_group = QGroupBox("Whisper Settings")
        whisper_form = QFormLayout(whisper_group)

        self._whisper_size_combo = QComboBox()
        self._whisper_size_combo.addItems(["tiny", "base", "small", "medium"])
        self._whisper_size_combo.setCurrentText("base")
        whisper_form.addRow("Model Size:", self._whisper_size_combo)
        layout.addWidget(whisper_group)

        check_btn = QPushButton("Re-check Model Status")
        check_btn.clicked.connect(self._check_model_status)
        layout.addWidget(check_btn)

        save_btn = QPushButton("Save AI Settings")
        save_btn.clicked.connect(self._save_ai_settings)
        layout.addWidget(save_btn)
        layout.addStretch()
        return w

# =====================================================
    # LOAD
    # =====================================================

    def _load_all(self) -> None:
        self._load_general_settings()
        self._load_obs_settings()
        self._load_players()
        self._load_maps()
        self._load_matches()
        self._check_model_status()

    def _load_general_settings(self) -> None:
        import app.config as cfg
        if cfg.R6_REPLAY_FOLDER_OVERRIDE:
            self._replay_folder_edit.setText(cfg.R6_REPLAY_FOLDER_OVERRIDE)
        else:
            folder = cfg.get_replay_folder()
            if folder:
                self._replay_folder_edit.setText(str(folder))
        self._stability_wait_spin.setValue(cfg.STABILITY_WAIT)
        self._stability_checks_spin.setValue(cfg.STABILITY_CHECKS)
        self._transcribe_checkbox.setChecked(cfg.TRANSCRIBE_AUTO)

    def _load_obs_settings(self) -> None:
        import app.config as cfg
        self._obs_host_edit.setText(cfg.OBS_HOST)
        self._obs_port_spin.setValue(cfg.OBS_PORT)
        self._obs_password_edit.setText(cfg.OBS_PASSWORD)
        self._obs_scene_edit.setText(cfg.OBS_SCENE_NAME)

    def _load_players(self) -> None:
        try:
            from database.repositories import Repository
            players = Repository().get_team_players()
            for i, edit in enumerate(self._player_edits):
                if i < len(players):
                    edit.setText(players[i].name)
        except Exception as e:
            print(f"[Settings] Failed to load players: {e}")

    def _load_maps(self) -> None:
        try:
            from database.repositories import Repository
            repo = Repository()
            with repo.db.get_connection() as conn:
                rows = conn.execute(
                    "SELECT map_id, name, is_active_pool FROM maps ORDER BY name"
                ).fetchall()

            self._maps_table.setRowCount(0)
            for row in rows:
                r = self._maps_table.rowCount()
                self._maps_table.insertRow(r)
                self._maps_table.setItem(r, 0, QTableWidgetItem(row["name"]))

                cb = QCheckBox()
                cb.setChecked(bool(row["is_active_pool"]))
                cb.setProperty("map_id", row["map_id"])

                cell_widget = QWidget()
                cell_layout = QHBoxLayout(cell_widget)
                cell_layout.addWidget(cb)
                cell_layout.setAlignment(Qt.AlignmentFlag.AlignCenter)
                cell_layout.setContentsMargins(0, 0, 0, 0)
                self._maps_table.setCellWidget(r, 1, cell_widget)

        except Exception as e:
            print(f"[Settings] Failed to load maps: {e}")

    def _load_matches(self) -> None:
        try:
            from database.repositories import Repository
            matches = Repository().get_all_matches()
            self._matches_table.setRowCount(0)
            for m in matches:
                r = self._matches_table.rowCount()
                self._matches_table.insertRow(r)
                self._matches_table.setItem(r, 0, QTableWidgetItem(str(m.match_id)))
                self._matches_table.setItem(r, 1, QTableWidgetItem(m.opponent_name))
                self._matches_table.setItem(r, 2, QTableWidgetItem(m.map))
                self._matches_table.setItem(r, 3, QTableWidgetItem(m.result or "—"))
                self._matches_table.setItem(
                    r, 4,
                    QTableWidgetItem(m.datetime_played.strftime("%Y-%m-%d %H:%M"))
                )
        except Exception as e:
            print(f"[Settings] Failed to load matches: {e}")

    def _check_model_status(self) -> None:
        from app.config import MODEL_PATH, WHISPER_MODEL_PATH

        if MODEL_PATH.exists():
            size_mb = MODEL_PATH.stat().st_size // (1024 * 1024)
            self._llm_status_label.setText(f"✅ Found ({size_mb} MB)")
            self._llm_status_label.setStyleSheet("color: #55e07a;")
        else:
            self._llm_status_label.setText("❌ Not found — place model.gguf in data/models/")
            self._llm_status_label.setStyleSheet("color: #e05555;")

        if WHISPER_MODEL_PATH.exists():
            size_mb = WHISPER_MODEL_PATH.stat().st_size // (1024 * 1024)
            self._whisper_status_label.setText(f"✅ Found ({size_mb} MB)")
            self._whisper_status_label.setStyleSheet("color: #55e07a;")
        else:
            self._whisper_status_label.setText(
                "❌ Not found — place whisper-base.pt in data/models/"
            )
            self._whisper_status_label.setStyleSheet("color: #e05555;")

    # =====================================================
    # SAVE
    # =====================================================

    def _save_general(self) -> None:
        import app.config as cfg
        from app.config import save_settings
        cfg.STABILITY_WAIT   = self._stability_wait_spin.value()
        cfg.STABILITY_CHECKS = self._stability_checks_spin.value()
        cfg.TRANSCRIBE_AUTO  = self._transcribe_checkbox.isChecked()
        folder = self._replay_folder_edit.text().strip()
        cfg.R6_REPLAY_FOLDER_OVERRIDE = folder if folder else None
        save_settings()
        QMessageBox.information(self, "Saved", "General settings saved.")

    def _save_obs(self) -> None:
        import app.config as cfg
        from app.config import save_settings
        cfg.OBS_HOST       = self._obs_host_edit.text().strip()
        cfg.OBS_PORT       = self._obs_port_spin.value()
        cfg.OBS_PASSWORD   = self._obs_password_edit.text()
        cfg.OBS_SCENE_NAME = self._obs_scene_edit.text().strip()
        save_settings()
        QMessageBox.information(self, "Saved", "OBS settings saved.")

    def _save_players(self) -> None:
        try:
            from database.repositories import Repository
            from models.player import Player
            repo = Repository()
            repo.clear_team_players()
            for edit in self._player_edits:
                name = edit.text().strip()
                if name:
                    repo.insert_player(Player(
                        player_id=None,
                        name=name,
                        is_team_member=True,
                    ))
            QMessageBox.information(self, "Saved", "Players updated.")
        except Exception as e:
            QMessageBox.critical(self, "Error", str(e))

    def _save_maps(self) -> None:
        try:
            from database.repositories import Repository
            repo = Repository()
            with repo.db.get_connection() as conn:
                for row in range(self._maps_table.rowCount()):
                    cell_widget = self._maps_table.cellWidget(row, 1)
                    if cell_widget is None:
                        continue
                    cb = cell_widget.findChild(QCheckBox)
                    if cb is None:
                        continue
                    map_id = cb.property("map_id")
                    active = 1 if cb.isChecked() else 0
                    conn.execute(
                        "UPDATE maps SET is_active_pool = ? WHERE map_id = ?",
                        (active, map_id),
                    )
                conn.commit()
            QMessageBox.information(self, "Saved", "Map pool updated.")
        except Exception as e:
            QMessageBox.critical(self, "Error", str(e))

    def _save_ai_settings(self) -> None:
        import app.config as cfg
        from app.config import save_settings
        cfg.LLM_GPU_LAYERS     = self._gpu_layers_spin.value()
        cfg.LLM_N_CTX          = self._ctx_spin.value()
        cfg.LLM_N_THREADS      = self._threads_spin.value()
        cfg.WHISPER_MODEL_SIZE  = self._whisper_size_combo.currentText()
        save_settings()
        QMessageBox.information(self, "Saved", "AI settings saved.")

    # =====================================================
    # MATCH MANAGEMENT
    # =====================================================

    def _set_match_result(self) -> None:
        selected = self._matches_table.currentRow()
        if selected < 0:
            QMessageBox.warning(self, "Warning", "Select a match first.")
            return

        match_id_item = self._matches_table.item(selected, 0)
        if match_id_item is None:
            return
        match_id = int(match_id_item.text())

        from PySide6.QtWidgets import QInputDialog
        result, ok = QInputDialog.getItem(
            self, "Set Result", "Result:", ["win", "loss"], 0, False
        )
        if not ok:
            return

        try:
            from database.repositories import Repository
            with Repository().db.get_connection() as conn:
                conn.execute(
                    "UPDATE matches SET result = ? WHERE match_id = ?",
                    (result, match_id),
                )
                conn.commit()
            self._load_matches()
            QMessageBox.information(self, "Updated", f"Match {match_id} set to '{result}'.")
        except Exception as e:
            QMessageBox.critical(self, "Error", str(e))

    def _delete_match(self) -> None:
        selected = self._matches_table.currentRow()
        if selected < 0:
            QMessageBox.warning(self, "Warning", "Select a match first.")
            return

        match_id_item = self._matches_table.item(selected, 0)
        if match_id_item is None:
            return
        match_id = int(match_id_item.text())

        confirm = QMessageBox.question(
            self,
            "Confirm Delete",
            f"Permanently delete match {match_id} and all its rounds?\n"
            f"This cannot be undone.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if confirm != QMessageBox.StandardButton.Yes:
            return

        try:
            from database.repositories import Repository
            with Repository().db.get_connection() as conn:
                conn.execute(
                    "DELETE FROM matches WHERE match_id = ?", (match_id,)
                )
                conn.commit()
            self._load_matches()
            QMessageBox.information(self, "Deleted", f"Match {match_id} deleted.")
        except Exception as e:
            QMessageBox.critical(self, "Error", str(e))

    # =====================================================
    # OBS TEST
    # =====================================================

    def _test_obs(self) -> None:
        try:
            from integration.obs_controller import OBSController
            obs = OBSController()
            if obs.connect():
                obs.disconnect()
                QMessageBox.information(self, "OBS", "Connection successful ✅")
            else:
                QMessageBox.warning(
                    self, "OBS",
                    "Connection failed ❌\nCheck OBS is open and WebSocket is enabled."
                )
        except Exception as e:
            QMessageBox.critical(self, "OBS Error", str(e))

    # =====================================================
    # BROWSE
    # =====================================================

    def _browse_replay_folder(self) -> None:
        folder = QFileDialog.getExistingDirectory(
            self, "Select R6 Replay Folder", str(Path.home())
        )
        if folder:
            self._replay_folder_edit.setText(folder)