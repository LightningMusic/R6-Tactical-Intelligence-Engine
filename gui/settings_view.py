from pathlib import Path

from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QFormLayout,
    QLabel, QLineEdit, QPushButton, QSpinBox, QCheckBox,
    QGroupBox, QTableWidget, QTableWidgetItem, QHeaderView,
    QMessageBox, QFileDialog, QTabWidget, QComboBox, QAbstractItemView,
    QScrollArea, QFrame
)
from PySide6.QtCore import Qt, QThread, QObject, Signal
from app.config import settings


class _TestConnectionWorker(QObject):
    """Runs SessionUploader.test_connection() off the UI thread. Pure Qt
    plumbing — the actual HTTP logic lives in app/uploader.py, which has
    no Qt dependency and is unit-tested headlessly."""
    finished = Signal(bool, str)

    def run(self) -> None:
        from app.uploader import SessionUploader
        result = SessionUploader().test_connection()
        if result.success:
            self.finished.emit(True, "Connected and authenticated.")
        else:
            self.finished.emit(False, result.error or "Connection failed.")


class SettingsView(QWidget):

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        # Internal state — initialised before _build_ui so callbacks are safe
        self._obs_profiles: list[dict] = []
        self._obs_active_idx: int = 0
        self._player_edits: list[dict] = []  # {"player_id", "name": QLineEdit, "aliases": QLineEdit}
        self._test_conn_thread: QThread | None = None
        self._test_conn_worker: _TestConnectionWorker | None = None
        self._build_ui()
        self._load_all()

    # =========================================================
    # UI BUILD
    # =========================================================

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 20, 20, 20)
        layout.setSpacing(16)

        header = QLabel("Settings")
        header.setStyleSheet("font-size: 22px; font-weight: bold;")
        header.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(header)

        tabs = QTabWidget()
        tabs.addTab(self._scrollable(self._build_general_tab()),  "⚙  General")
        tabs.addTab(self._scrollable(self._build_obs_tab()),       "🎙  OBS")
        tabs.addTab(self._scrollable(self._build_players_tab()),   "👥  Players")
        tabs.addTab(self._scrollable(self._build_maps_tab()),      "🗺  Maps")
        tabs.addTab(self._scrollable(self._build_matches_tab()),   "📋  Match Manager")
        tabs.addTab(self._scrollable(self._build_ai_tab()),        "🤖  AI / Models")
        tabs.addTab(self._scrollable(self._build_discord_tab()),   "🎙  Discord")
        tabs.addTab(self._scrollable(self._build_twitch_tab()),    "📡  Twitch")
        tabs.addTab(self._scrollable(self._build_sync_tab()),      "🔄  Remote Sync")
        layout.addWidget(tabs)

    def _scrollable(self, content: QWidget) -> QWidget:
        """Wraps a settings tab's content widget in a QScrollArea so it
        scrolls instead of clipping when its content is taller than the
        visible tab area — e.g. on a laptop screen shorter than
        MainWindow's 750px minimum height, or a tab (like Sync) whose
        notes/rows add up to more height than a tab page ever got before.
        setWidgetResizable(True) lets the content still expand to fill the
        scroll area's width; only vertical overflow scrolls."""
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setWidget(content)
        return scroll

    # ---------------------------------------------------------
    # General tab
    # ---------------------------------------------------------

    def _build_general_tab(self) -> QWidget:
        w = QWidget()
        layout = QVBoxLayout(w)
        layout.setSpacing(16)

        replay_group = QGroupBox("R6 Replay Folder")
        replay_layout = QHBoxLayout(replay_group)
        self._replay_folder_edit = QLineEdit()
        self._replay_folder_edit.setPlaceholderText("Path to R6 MatchReplay folder...")
        browse_btn = QPushButton("Browse")
        browse_btn.clicked.connect(self._browse_replay_folder)
        replay_layout.addWidget(self._replay_folder_edit, stretch=1)
        replay_layout.addWidget(browse_btn)
        layout.addWidget(replay_group)

        stab_group = QGroupBox("File Stability (Import)")
        stab_form = QFormLayout(stab_group)
        self._stability_wait_spin = QSpinBox()
        self._stability_wait_spin.setRange(1, 30)
        self._stability_wait_spin.setSuffix("  seconds")
        self._stability_checks_spin = QSpinBox()
        self._stability_checks_spin.setRange(1, 10)
        stab_form.addRow("Wait between checks:", self._stability_wait_spin)
        stab_form.addRow("Number of checks:",    self._stability_checks_spin)
        layout.addWidget(stab_group)

        trans_group = QGroupBox("Transcription")
        trans_layout = QVBoxLayout(trans_group)
        self._transcribe_checkbox = QCheckBox("Auto-transcribe session audio after import")
        trans_layout.addWidget(self._transcribe_checkbox)
        layout.addWidget(trans_group)

        save_btn = QPushButton("Save General Settings")
        save_btn.clicked.connect(self._save_general)
        layout.addWidget(save_btn)
        layout.addStretch()
        return w

    # ---------------------------------------------------------
    # OBS tab
    # ---------------------------------------------------------

    def _build_obs_tab(self) -> QWidget:
        w = QWidget()
        layout = QVBoxLayout(w)
        layout.setSpacing(12)

        # Profile selector
        profile_row = QHBoxLayout()
        profile_row.addWidget(QLabel("Profile:"))
        self._obs_profile_combo = QComboBox()
        self._obs_profile_combo.setMinimumWidth(160)
        self._obs_profile_combo.currentIndexChanged.connect(self._on_obs_profile_selected)
        profile_row.addWidget(self._obs_profile_combo, stretch=1)

        add_btn = QPushButton("➕ Add")
        add_btn.setFixedWidth(60)
        add_btn.clicked.connect(self._add_obs_profile)
        profile_row.addWidget(add_btn)

        del_btn = QPushButton("🗑")
        del_btn.setFixedWidth(40)
        del_btn.clicked.connect(self._delete_obs_profile)
        profile_row.addWidget(del_btn)
        layout.addLayout(profile_row)

        # Profile fields
        obs_group = QGroupBox("Connection Settings")
        form = QFormLayout(obs_group)

        self._obs_profile_name_edit = QLineEdit()
        self._obs_profile_name_edit.setPlaceholderText("e.g. Lab PC, Home PC")
        form.addRow("Profile Name:", self._obs_profile_name_edit)

        self._obs_host_edit = QLineEdit()
        form.addRow("Host:", self._obs_host_edit)

        self._obs_port_spin = QSpinBox()
        self._obs_port_spin.setRange(1, 65535)
        form.addRow("Port:", self._obs_port_spin)

        self._obs_password_edit = QLineEdit()
        self._obs_password_edit.setEchoMode(QLineEdit.EchoMode.Password)
        show_pw_cb = QCheckBox("Show")
        show_pw_cb.toggled.connect(
            lambda checked: self._obs_password_edit.setEchoMode(
                QLineEdit.EchoMode.Normal if checked else QLineEdit.EchoMode.Password
            )
        )
        pw_row = QHBoxLayout()
        pw_row.addWidget(self._obs_password_edit)
        pw_row.addWidget(show_pw_cb)
        form.addRow("Password:", pw_row)

        self._obs_scene_edit = QLineEdit()
        form.addRow("Scene Name:", self._obs_scene_edit)

        layout.addWidget(obs_group)

        # Buttons
        btn_row = QHBoxLayout()
        test_btn = QPushButton("🔌 Test Connection")
        test_btn.clicked.connect(self._test_obs)
        btn_row.addWidget(test_btn)

        activate_btn = QPushButton("✅ Use This Profile")
        activate_btn.clicked.connect(self._activate_obs_profile)
        btn_row.addWidget(activate_btn)

        save_btn = QPushButton("💾 Save All Profiles")
        save_btn.clicked.connect(self._save_obs)
        btn_row.addWidget(save_btn)
        layout.addLayout(btn_row)

        self._obs_active_label = QLabel("")
        self._obs_active_label.setStyleSheet("color: #55e07a; font-size: 11px;")
        self._obs_active_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._obs_active_label.setWordWrap(True)
        layout.addWidget(self._obs_active_label)

        layout.addStretch()
        return w

    # ---------------------------------------------------------
    # Players tab
    # ---------------------------------------------------------

    def _build_players_tab(self) -> QWidget:
        w = QWidget()
        layout = QVBoxLayout(w)
        layout.setSpacing(12)

        intro = QLabel(
            "Team player names (5 players). \"Aliases\" are other in-game "
            "usernames/tags this same person has played under — list them "
            "comma-separated and imports will tie that username to this "
            "player automatically instead of creating a duplicate."
        )
        intro.setWordWrap(True)
        layout.addWidget(intro)

        grid = QFormLayout()
        grid.setSpacing(8)
        for i in range(5):
            name_edit = QLineEdit()
            name_edit.setPlaceholderText(f"Player {i+1} name")
            alias_edit = QLineEdit()
            alias_edit.setPlaceholderText("aliases, comma, separated (optional)")

            pair = QVBoxLayout()
            pair.addWidget(name_edit)
            pair.addWidget(alias_edit)
            pair_widget = QWidget()
            pair_widget.setLayout(pair)

            grid.addRow(f"Player {i+1}:", pair_widget)
            self._player_edits.append({
                "player_id": None,
                "name": name_edit,
                "aliases": alias_edit,
            })
        layout.addLayout(grid)

        save_btn = QPushButton("Save Players")
        save_btn.clicked.connect(self._save_players)
        layout.addWidget(save_btn)

        # ---------------------------------------------------------
        # Merge duplicate ("ghost") player into a real team player
        # ---------------------------------------------------------
        merge_box = QGroupBox("Merge a duplicate player")
        merge_layout = QVBoxLayout(merge_box)
        merge_note = QLabel(
            "If a username didn't match anyone (a typo, a new tag, an alt "
            "account) the app saved it as its own separate player. Pick it "
            "here and fold it into the correct team player — their stats "
            "move over and the username is remembered as an alias."
        )
        merge_note.setWordWrap(True)
        merge_layout.addWidget(merge_note)

        merge_row = QHBoxLayout()
        merge_row.addWidget(QLabel("Duplicate player:"))
        self._merge_ghost_combo = QComboBox()
        merge_row.addWidget(self._merge_ghost_combo, 1)
        merge_row.addWidget(QLabel("Merge into:"))
        self._merge_target_combo = QComboBox()
        merge_row.addWidget(self._merge_target_combo, 1)
        merge_layout.addLayout(merge_row)

        merge_btn_row = QHBoxLayout()
        refresh_btn = QPushButton("Refresh List")
        refresh_btn.clicked.connect(self._refresh_merge_dropdowns)
        merge_btn_row.addWidget(refresh_btn)
        merge_btn = QPushButton("Merge")
        merge_btn.clicked.connect(self._merge_players)
        merge_btn_row.addWidget(merge_btn)
        merge_layout.addLayout(merge_btn_row)

        layout.addWidget(merge_box)
        layout.addStretch()
        return w

    # ---------------------------------------------------------
    # Maps tab
    # ---------------------------------------------------------

    def _build_maps_tab(self) -> QWidget:
        w = QWidget()
        layout = QVBoxLayout(w)
        layout.setSpacing(12)
        layout.addWidget(QLabel("Toggle maps in/out of the active pool:"))

        self._maps_table = QTableWidget(0, 2)
        self._maps_table.setHorizontalHeaderLabels(["Map", "Active Pool"])
        self._maps_table.horizontalHeader().setSectionResizeMode(
            0, QHeaderView.ResizeMode.Stretch)
        self._maps_table.horizontalHeader().setSectionResizeMode(
            1, QHeaderView.ResizeMode.ResizeToContents)
        self._maps_table.verticalHeader().setVisible(False)
        self._maps_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        layout.addWidget(self._maps_table)

        save_btn = QPushButton("Save Map Pool")
        save_btn.clicked.connect(self._save_maps)
        layout.addWidget(save_btn)
        return w

    # ---------------------------------------------------------
    # Matches tab
    # ---------------------------------------------------------

    def _build_matches_tab(self) -> QWidget:
        w = QWidget()
        layout = QVBoxLayout(w)
        layout.setSpacing(12)
        layout.addWidget(QLabel("Manage existing matches:"))

        self._matches_table = QTableWidget(0, 5)
        self._matches_table.setHorizontalHeaderLabels(
            ["ID", "Opponent", "Map", "Result", "Date"])
        for col in range(5):
            self._matches_table.horizontalHeader().setSectionResizeMode(
                col, QHeaderView.ResizeMode.ResizeToContents)
        self._matches_table.horizontalHeader().setSectionResizeMode(
            1, QHeaderView.ResizeMode.Stretch)
        self._matches_table.verticalHeader().setVisible(False)
        self._matches_table.setSelectionBehavior(
            QAbstractItemView.SelectionBehavior.SelectRows)
        self._matches_table.setEditTriggers(
            QAbstractItemView.EditTrigger.NoEditTriggers)
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

    # ---------------------------------------------------------
    # AI tab
    # ---------------------------------------------------------

    def _build_ai_tab(self) -> QWidget:
        w = QWidget()
        layout = QVBoxLayout(w)
        layout.setSpacing(16)

        status_group = QGroupBox("Model Status")
        status_layout = QFormLayout(status_group)
        self._llm_status_label     = QLabel("Checking...")
        self._whisper_status_label = QLabel("Checking...")
        self._llm_status_label.setWordWrap(True)
        self._whisper_status_label.setWordWrap(True)
        status_layout.addRow("LLM (Ollama / llama-cpp):", self._llm_status_label)
        status_layout.addRow("Whisper:",                  self._whisper_status_label)
        layout.addWidget(status_group)

        llm_group = QGroupBox("LLM Settings")
        llm_form  = QFormLayout(llm_group)

        self._ollama_model_edit = QLineEdit()
        self._ollama_model_edit.setPlaceholderText("e.g. llama3.2:3b")
        llm_form.addRow("Ollama Model:", self._ollama_model_edit)

        self._gpu_layers_spin = QSpinBox()
        self._gpu_layers_spin.setRange(0, 100)
        self._gpu_layers_spin.setSpecialValueText("0 (CPU only)")
        llm_form.addRow("GPU Layers:", self._gpu_layers_spin)

        self._ctx_spin = QSpinBox()
        self._ctx_spin.setRange(1024, 16384)
        llm_form.addRow("Context Size:", self._ctx_spin)

        self._threads_spin = QSpinBox()
        self._threads_spin.setRange(1, 32)
        llm_form.addRow("CPU Threads:", self._threads_spin)

        layout.addWidget(llm_group)

        whisper_group = QGroupBox("Whisper Settings")
        whisper_form  = QFormLayout(whisper_group)
        self._whisper_size_combo = QComboBox()
        self._whisper_size_combo.addItems(["tiny", "base", "small", "medium"])
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

    # ---------------------------------------------------------
    # Discord tab
    # ---------------------------------------------------------

    def _build_discord_tab(self) -> QWidget:
        w = QWidget()
        layout = QVBoxLayout(w)
        layout.setSpacing(12)

        dep_label = QLabel(
            'Required: pip install "discord.py[voice]" discord-ext-sinks PyNaCl'
        )
        dep_label.setStyleSheet("color: #e0a830; font-size: 10px;")
        layout.addWidget(dep_label)

        token_group = QGroupBox("Bot Token")
        token_form  = QFormLayout(token_group)
        self._discord_token_edit = QLineEdit()
        self._discord_token_edit.setEchoMode(QLineEdit.EchoMode.Password)
        self._discord_token_edit.setPlaceholderText("Bot token from discord.com/developers")
        show_cb = QCheckBox("Show")
        show_cb.toggled.connect(
            lambda checked: self._discord_token_edit.setEchoMode(
                QLineEdit.EchoMode.Normal if checked else QLineEdit.EchoMode.Password
            )
        )
        token_row = QHBoxLayout()
        token_row.addWidget(self._discord_token_edit)
        token_row.addWidget(show_cb)
        token_form.addRow("Token:", token_row)
        layout.addWidget(token_group)

        ch_group = QGroupBox("Voice Channels  (one entry per server/room you use)")
        ch_layout = QVBoxLayout(ch_group)

        self._discord_channels_table = QTableWidget(0, 2)
        self._discord_channels_table.setHorizontalHeaderLabels(["Label", "Channel ID"])
        self._discord_channels_table.horizontalHeader().setSectionResizeMode(
            0, QHeaderView.ResizeMode.Stretch)
        self._discord_channels_table.horizontalHeader().setSectionResizeMode(
            1, QHeaderView.ResizeMode.ResizeToContents)
        self._discord_channels_table.verticalHeader().setVisible(False)
        self._discord_channels_table.setMaximumHeight(160)
        ch_layout.addWidget(self._discord_channels_table)

        ch_btn_row = QHBoxLayout()
        add_ch_btn = QPushButton("➕ Add Channel")
        add_ch_btn.clicked.connect(self._add_discord_channel)
        ch_btn_row.addWidget(add_ch_btn)
        del_ch_btn = QPushButton("🗑 Remove Selected")
        del_ch_btn.clicked.connect(self._remove_discord_channel)
        ch_btn_row.addWidget(del_ch_btn)
        ch_layout.addLayout(ch_btn_row)
        layout.addWidget(ch_group)

        active_row = QHBoxLayout()
        active_row.addWidget(QLabel("Active channel for sessions:"))
        self._discord_active_combo = QComboBox()
        self._discord_active_combo.setMinimumWidth(200)
        active_row.addWidget(self._discord_active_combo, stretch=1)
        layout.addLayout(active_row)

        btn_row = QHBoxLayout()
        test_btn = QPushButton("🔌 Test Bot Connection")
        test_btn.clicked.connect(self._test_discord)
        btn_row.addWidget(test_btn)
        save_btn = QPushButton("💾 Save Discord Settings")
        save_btn.clicked.connect(self._save_discord)
        btn_row.addWidget(save_btn)
        layout.addLayout(btn_row)

        layout.addStretch()
        return w

    # ---------------------------------------------------------
    # Twitch tab
    # ---------------------------------------------------------

    def _build_twitch_tab(self) -> QWidget:
        w = QWidget()
        layout = QVBoxLayout(w)
        layout.setSpacing(16)

        info = QLabel(
            "Configure Twitch streaming settings.\n"
            "Set your stream key in OBS: Settings → Stream → Service: Twitch → Stream Key.\n"
            "Use '📡 Start Stream' in the Recording tab to go live."
        )
        info.setWordWrap(True)
        info.setStyleSheet("color: #aaa; font-size: 11px;")
        layout.addWidget(info)

        stream_group = QGroupBox("Twitch Stream Settings")
        form = QFormLayout(stream_group)

        self._twitch_channel_edit = QLineEdit()
        self._twitch_channel_edit.setPlaceholderText("Your Twitch channel name (e.g. shroud)")
        form.addRow("Channel Name:", self._twitch_channel_edit)

        self._twitch_title_edit = QLineEdit()
        self._twitch_title_edit.setPlaceholderText("Stream title (optional)")
        form.addRow("Stream Title:", self._twitch_title_edit)

        self._twitch_auto_start_cb = QCheckBox("Auto-start stream when session starts")
        form.addRow("", self._twitch_auto_start_cb)

        self._twitch_auto_stop_cb = QCheckBox("Auto-stop stream when session ends")
        form.addRow("", self._twitch_auto_stop_cb)

        layout.addWidget(stream_group)

        save_btn = QPushButton("Save Twitch Settings")
        save_btn.clicked.connect(self._save_twitch)
        layout.addWidget(save_btn)
        layout.addStretch()
        return w

    # ---------------------------------------------------------
    # Remote Sync tab (Milestone 3 client uploader)
    # ---------------------------------------------------------

    def _build_sync_tab(self) -> QWidget:
        w = QWidget()
        layout = QVBoxLayout(w)
        layout.setSpacing(16)

        info = QLabel(
            "Configure whether match sessions are analyzed locally, uploaded to a "
            "remote server, or both. This never affects local recording or local "
            "analysis — it only controls the optional upload leg."
        )
        info.setWordWrap(True)
        info.setStyleSheet("color: #aaa; font-size: 11px;")
        layout.addWidget(info)

        mode_group = QGroupBox("Analysis Mode")
        mode_form  = QFormLayout(mode_group)

        self._sync_mode_combo = QComboBox()
        self._sync_mode_combo.addItems(["local", "remote", "automatic"])
        mode_form.addRow("Mode:", self._sync_mode_combo)

        self._sync_client_name_edit = QLineEdit()
        self._sync_client_name_edit.setPlaceholderText("e.g. USB_Client, Elijah_Laptop")
        mode_form.addRow("Client Name:", self._sync_client_name_edit)

        layout.addWidget(mode_group)

        conn_group = QGroupBox("Server Connection")
        conn_outer = QVBoxLayout(conn_group)

        # This note is added directly to conn_outer (a QVBoxLayout) rather
        # than as a QFormLayout row: a word-wrapped QLabel inside
        # QFormLayout.addRow(widget) doesn't reliably reserve enough height
        # for its own text before the layout knows its final width, and
        # visibly clips the top/bottom of multi-line notes as a result.
        # QVBoxLayout doesn't have this problem — it correctly consults the
        # label's heightForWidth() — which is also why every other
        # multi-line note elsewhere in this file already uses addWidget
        # into a QVBoxLayout instead of a form row.
        conn_note = QLabel(
            "Leave both fields blank to use the address and key this build was "
            "made with (see build_and_deploy.bat) — set one here only to point "
            "this copy at a different server, e.g. for local testing."
        )
        conn_note.setWordWrap(True)
        conn_note.setStyleSheet("color: #999;")
        conn_outer.addWidget(conn_note)

        conn_form = QFormLayout()
        conn_outer.addLayout(conn_form)

        self._sync_server_url_edit = QLineEdit()
        self._sync_server_url_edit.setPlaceholderText("Blank = use this build's address, or http://100.x.x.x:8000")
        conn_form.addRow("Server URL:", self._sync_server_url_edit)

        self._sync_api_key_edit = QLineEdit()
        self._sync_api_key_edit.setEchoMode(QLineEdit.EchoMode.Password)
        self._sync_api_key_edit.setPlaceholderText("Blank = use this build's key, or paste one from R6Server.exe")
        show_key_cb = QCheckBox("Show")
        show_key_cb.toggled.connect(
            lambda checked: self._sync_api_key_edit.setEchoMode(
                QLineEdit.EchoMode.Normal if checked else QLineEdit.EchoMode.Password
            )
        )
        key_row = QHBoxLayout()
        key_row.addWidget(self._sync_api_key_edit)
        key_row.addWidget(show_key_cb)
        conn_form.addRow("API Key:", key_row)

        self._sync_timeout_spin = QSpinBox()
        self._sync_timeout_spin.setRange(1, 300)
        self._sync_timeout_spin.setSuffix(" sec")
        conn_form.addRow("Request Timeout:", self._sync_timeout_spin)

        self._sync_max_retries_spin = QSpinBox()
        self._sync_max_retries_spin.setRange(0, 50)
        conn_form.addRow("Max Upload Retries:", self._sync_max_retries_spin)

        test_row = QHBoxLayout()
        self._sync_test_btn = QPushButton("🔌 Test Connection")
        self._sync_test_btn.clicked.connect(self._test_sync_connection)
        self._sync_test_status_label = QLabel("")
        self._sync_test_status_label.setWordWrap(True)
        test_row.addWidget(self._sync_test_btn)
        test_row.addWidget(self._sync_test_status_label, stretch=1)
        conn_form.addRow("", test_row)

        layout.addWidget(conn_group)

        behavior_group = QGroupBox("Upload Behavior")
        behavior_layout = QVBoxLayout(behavior_group)

        self._sync_upload_replays_cb = QCheckBox("Include replay (.rec) files in uploaded packages")
        self._sync_upload_voice_cb = QCheckBox(
            "Include voice/audio recordings in uploaded packages — required for "
            "the server to transcribe for you (see below)"
        )
        self._sync_upload_auto_cb = QCheckBox("Upload automatically after each session (automatic mode)")
        self._sync_upload_offline_cb = QCheckBox("Queue uploads for later when the server is unreachable")
        self._sync_fallback_local_cb = QCheckBox(
            "Fall back to local transcription if the server can't be reached when a session ends"
        )

        for cb in (
            self._sync_upload_replays_cb, self._sync_upload_voice_cb,
            self._sync_upload_auto_cb, self._sync_upload_offline_cb,
            self._sync_fallback_local_cb,
        ):
            behavior_layout.addWidget(cb)

        sync_transcription_note = QLabel(
            "When mode is Remote or Automatic, voice upload is on, and the "
            "server is reachable when a session ends, transcription runs on "
            "the server instead of here — nothing to wait for before you "
            "disconnect the USB. Otherwise (or if unreachable), it runs "
            "locally as usual, per the fallback setting above."
        )
        sync_transcription_note.setWordWrap(True)
        sync_transcription_note.setStyleSheet("color: gray; font-size: 11px;")
        behavior_layout.addWidget(sync_transcription_note)

        layout.addWidget(behavior_group)

        save_btn = QPushButton("Save Remote Sync Settings")
        save_btn.clicked.connect(self._save_sync_settings)
        layout.addWidget(save_btn)
        layout.addStretch()
        return w

    # =========================================================
    # LOAD ALL
    # =========================================================

    def _load_all(self) -> None:
        self._load_general_settings()
        self._load_obs_settings()
        self._load_players()
        self._load_maps()
        self._load_matches()
        self._load_ai_settings()
        self._check_model_status()
        self._load_discord_settings()
        self._load_twitch_settings()
        self._load_sync_settings()

    # =========================================================
    # GENERAL — load / save / browse
    # =========================================================

    def _load_general_settings(self) -> None:
        folder = settings.R6_REPLAY_FOLDER
        if folder:
            self._replay_folder_edit.setText(str(folder))
        self._stability_wait_spin.setValue(int(settings.STABILITY_WAIT))
        self._stability_checks_spin.setValue(settings.STABILITY_CHECKS)
        self._transcribe_checkbox.setChecked(settings.TRANSCRIBE_AUTO)

    def _save_general(self) -> None:
        folder = self._replay_folder_edit.text().strip()
        settings.set_many({
            "stability_wait":   self._stability_wait_spin.value(),
            "stability_checks": self._stability_checks_spin.value(),
            "transcribe_auto":  self._transcribe_checkbox.isChecked(),
            "r6_replay_folder": folder if folder else None,
        })
        settings.save()
        QMessageBox.information(self, "Saved", "General settings saved.")

    def _browse_replay_folder(self) -> None:
        folder = QFileDialog.getExistingDirectory(
            self, "Select R6 Replay Folder", str(Path.home())
        )
        if folder:
            self._replay_folder_edit.setText(folder)

    # =========================================================
    # OBS PROFILES — load / save / CRUD
    # =========================================================

    def _load_obs_settings(self) -> None:
        self._obs_profiles = list(settings.get_obs_profiles())
        self._obs_active_idx = int(settings.get("obs_active_profile") or 0)

        if not self._obs_profiles:
            # Migrate legacy flat keys
            self._obs_profiles = [{
                "name":       "Default",
                "host":       str(settings.get("obs_host") or "localhost"),
                "port":       int(settings.get("obs_port") or 4455),
                "password":   str(settings.get("obs_password") or ""),
                "scene_name": str(settings.get("obs_scene_name") or "R6_Comms"),
            }]
            self._obs_active_idx = 0

        self._obs_profile_combo.blockSignals(True)
        self._obs_profile_combo.clear()
        for p in self._obs_profiles:
            self._obs_profile_combo.addItem(p.get("name", "Unnamed"))
        self._obs_profile_combo.blockSignals(False)

        idx = min(self._obs_active_idx, len(self._obs_profiles) - 1)
        self._obs_profile_combo.setCurrentIndex(idx)
        self._on_obs_profile_selected(idx)
        self._update_obs_active_label()

    def _on_obs_profile_selected(self, idx: int) -> None:
        if idx < 0 or idx >= len(self._obs_profiles):
            return
        p = self._obs_profiles[idx]
        self._obs_profile_name_edit.setText(p.get("name", ""))
        self._obs_host_edit.setText(p.get("host", "localhost"))
        self._obs_port_spin.setValue(int(p.get("port", 4455)))
        self._obs_password_edit.setText(p.get("password", ""))
        self._obs_scene_edit.setText(p.get("scene_name", "R6_Comms"))

    def _save_current_profile_fields(self) -> None:
        idx = self._obs_profile_combo.currentIndex()
        if 0 <= idx < len(self._obs_profiles):
            self._obs_profiles[idx] = {
                "name":       self._obs_profile_name_edit.text().strip() or "Unnamed",
                "host":       self._obs_host_edit.text().strip() or "localhost",
                "port":       self._obs_port_spin.value(),
                "password":   self._obs_password_edit.text(),
                "scene_name": self._obs_scene_edit.text().strip() or "R6_Comms",
            }
            self._obs_profile_combo.blockSignals(True)
            self._obs_profile_combo.setItemText(idx, self._obs_profiles[idx]["name"])
            self._obs_profile_combo.blockSignals(False)

    def _add_obs_profile(self) -> None:
        self._save_current_profile_fields()
        new_p = {
            "name":       f"PC {len(self._obs_profiles) + 1}",
            "host":       "localhost",
            "port":       4455,
            "password":   "",
            "scene_name": "R6_Comms",
        }
        self._obs_profiles.append(new_p)
        self._obs_profile_combo.addItem(new_p["name"])
        self._obs_profile_combo.setCurrentIndex(len(self._obs_profiles) - 1)

    def _delete_obs_profile(self) -> None:
        if len(self._obs_profiles) <= 1:
            QMessageBox.warning(self, "Cannot Delete", "You need at least one profile.")
            return
        idx = self._obs_profile_combo.currentIndex()
        self._obs_profiles.pop(idx)
        self._obs_profile_combo.removeItem(idx)
        if self._obs_active_idx >= len(self._obs_profiles):
            self._obs_active_idx = len(self._obs_profiles) - 1
        self._update_obs_active_label()

    def _activate_obs_profile(self) -> None:
        self._save_current_profile_fields()
        self._obs_active_idx = self._obs_profile_combo.currentIndex()
        self._save_obs()
        self._update_obs_active_label()
        name = self._obs_profiles[self._obs_active_idx].get("name", "?")
        QMessageBox.information(self, "Profile Activated", f"Now using: {name}")

    def _update_obs_active_label(self) -> None:
        if 0 <= self._obs_active_idx < len(self._obs_profiles):
            name = self._obs_profiles[self._obs_active_idx].get("name", "?")
            self._obs_active_label.setText(f"Active profile: {name}")

    def _save_obs(self) -> None:
        self._save_current_profile_fields()
        settings.set_obs_profiles(self._obs_profiles, self._obs_active_idx)
        settings.save()
        QMessageBox.information(self, "Saved", "OBS profiles saved.")

    def _test_obs(self) -> None:
        self._save_current_profile_fields()
        idx = self._obs_profile_combo.currentIndex()
        if idx < 0 or idx >= len(self._obs_profiles):
            return
        p = self._obs_profiles[idx]
        try:
            import obswebsocket
            client = obswebsocket.obsws(
                p.get("host", "localhost"),
                int(p.get("port", 4455)),
                p.get("password", ""),
            )
            client.connect()
            client.disconnect()
            QMessageBox.information(
                self, "OBS",
                f"✅ Connected to {p['name']} ({p['host']}:{p['port']})"
            )
        except Exception as e:
            QMessageBox.warning(self, "OBS", f"❌ Failed: {e}")

    # =========================================================
    # PLAYERS
    # =========================================================

    def _load_players(self) -> None:
        try:
            from database.repositories import Repository
            players = Repository().get_team_players_with_aliases()
            for i, row in enumerate(self._player_edits):
                if i < len(players):
                    row["player_id"] = players[i]["player_id"]
                    row["name"].setText(players[i]["name"])
                    row["aliases"].setText(", ".join(players[i]["aliases"]))
                else:
                    row["player_id"] = None
                    row["name"].clear()
                    row["aliases"].clear()
        except Exception as e:
            print(f"[Settings] Failed to load players: {e}")
        self._refresh_merge_dropdowns()

    def _save_players(self) -> None:
        # Updates existing players in place (by player_id) instead of
        # deleting and recreating the team — deleting a player with
        # existing match stats would violate the player_round_stats
        # foreign key and fail outright once a team has played matches.
        # A blank name field just leaves that slot's existing player (if
        # any) untouched, so this can never silently delete match history.
        try:
            from database.repositories import Repository
            from models.player import Player
            repo = Repository()
            for row in self._player_edits:
                name = row["name"].text().strip()
                aliases = [a for a in row["aliases"].text().split(",")]
                player_id = row["player_id"]

                if not name:
                    continue

                if player_id is None:
                    player_id = repo.insert_player(
                        Player(player_id=None, name=name, is_team_member=True)
                    )
                    row["player_id"] = player_id
                else:
                    repo.update_player_name(player_id, name)

                repo.set_player_aliases(player_id, aliases)

            QMessageBox.information(self, "Saved", "Players updated.")
        except Exception as e:
            QMessageBox.critical(self, "Error", str(e))
        self._refresh_merge_dropdowns()

    def _refresh_merge_dropdowns(self) -> None:
        try:
            from database.repositories import Repository
            repo = Repository()
            ghosts = repo.get_non_team_players()
            team = repo.get_team_players()

            self._merge_ghost_combo.clear()
            for p in ghosts:
                self._merge_ghost_combo.addItem(p.name, p.player_id)

            self._merge_target_combo.clear()
            for p in team:
                self._merge_target_combo.addItem(p.name, p.player_id)
        except Exception as e:
            print(f"[Settings] Failed to refresh merge dropdowns: {e}")

    def _merge_players(self) -> None:
        ghost_id = self._merge_ghost_combo.currentData()
        target_id = self._merge_target_combo.currentData()
        if ghost_id is None or target_id is None:
            QMessageBox.warning(self, "Merge", "Nothing to merge — no duplicate or target player.")
            return

        ghost_name = self._merge_ghost_combo.currentText()
        target_name = self._merge_target_combo.currentText()
        confirm = QMessageBox.question(
            self, "Merge Players",
            f"Merge '{ghost_name}' into '{target_name}'? All of "
            f"'{ghost_name}'s stats will move to '{target_name}', and "
            f"'{ghost_name}' will be remembered as one of their aliases. "
            f"This cannot be undone.",
        )
        if confirm != QMessageBox.StandardButton.Yes:
            return

        try:
            from database.repositories import Repository
            Repository().merge_player(ghost_id, target_id)
            QMessageBox.information(self, "Merged", f"'{ghost_name}' merged into '{target_name}'.")
            self._load_players()
        except Exception as e:
            QMessageBox.critical(self, "Error", str(e))

    # =========================================================
    # MAPS
    # =========================================================

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
                cell = QWidget()
                cell_layout = QHBoxLayout(cell)
                cell_layout.addWidget(cb)
                cell_layout.setAlignment(Qt.AlignmentFlag.AlignCenter)
                cell_layout.setContentsMargins(0, 0, 0, 0)
                self._maps_table.setCellWidget(r, 1, cell)
        except Exception as e:
            print(f"[Settings] Failed to load maps: {e}")

    def _save_maps(self) -> None:
        try:
            from database.repositories import Repository
            repo = Repository()
            with repo.db.get_connection() as conn:
                for row in range(self._maps_table.rowCount()):
                    cell = self._maps_table.cellWidget(row, 1)
                    if cell is None:
                        continue
                    cb = cell.findChild(QCheckBox)
                    if cb is None:
                        continue
                    conn.execute(
                        "UPDATE maps SET is_active_pool = ? WHERE map_id = ?",
                        (1 if cb.isChecked() else 0, cb.property("map_id")),
                    )
                conn.commit()
            QMessageBox.information(self, "Saved", "Map pool updated.")
        except Exception as e:
            QMessageBox.critical(self, "Error", str(e))

    # =========================================================
    # MATCHES
    # =========================================================

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
                    r, 4, QTableWidgetItem(
                        m.datetime_played.strftime("%Y-%m-%d %H:%M")
                    )
                )
        except Exception as e:
            print(f"[Settings] Failed to load matches: {e}")

    def _set_match_result(self) -> None:
        selected = self._matches_table.currentRow()
        if selected < 0:
            QMessageBox.warning(self, "Warning", "Select a match first.")
            return
        item = self._matches_table.item(selected, 0)
        if item is None:
            return
        match_id = int(item.text())

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
            QMessageBox.information(self, "Updated", f"Match {match_id} → '{result}'.")
        except Exception as e:
            QMessageBox.critical(self, "Error", str(e))

    def _delete_match(self) -> None:
        selected = self._matches_table.currentRow()
        if selected < 0:
            QMessageBox.warning(self, "Warning", "Select a match first.")
            return
        item = self._matches_table.item(selected, 0)
        if item is None:
            return
        match_id = int(item.text())

        confirm = QMessageBox.question(
            self, "Confirm Delete",
            f"Permanently delete match {match_id} and all its rounds?\nThis cannot be undone.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if confirm != QMessageBox.StandardButton.Yes:
            return
        try:
            from database.repositories import Repository
            with Repository().db.get_connection() as conn:
                conn.execute("DELETE FROM matches WHERE match_id = ?", (match_id,))
                conn.commit()
            self._load_matches()
            QMessageBox.information(self, "Deleted", f"Match {match_id} deleted.")
        except Exception as e:
            QMessageBox.critical(self, "Error", str(e))

    # =========================================================
    # AI / MODELS
    # =========================================================

    def _load_ai_settings(self) -> None:
        self._gpu_layers_spin.setValue(settings.LLM_GPU_LAYERS)
        self._ctx_spin.setValue(settings.LLM_N_CTX)
        self._threads_spin.setValue(settings.LLM_N_THREADS)
        self._ollama_model_edit.setText(str(settings.get("ollama_model") or "llama3.2:3b"))

        whisper_size = settings.WHISPER_MODEL_SIZE
        idx = self._whisper_size_combo.findText(whisper_size)
        if idx >= 0:
            self._whisper_size_combo.setCurrentIndex(idx)

    def _check_model_status(self) -> None:
        from app.config import get_llm_model_path, get_whisper_model_path

        model_path   = get_llm_model_path()
        whisper_path = get_whisper_model_path()

        if model_path.exists():
            mb = model_path.stat().st_size // (1024 * 1024)
            self._llm_status_label.setText(f"✅ {model_path.name} ({mb} MB)")
            self._llm_status_label.setStyleSheet("color: #55e07a;")
        else:
            self._llm_status_label.setText("❌ Not found — place a .gguf in data/models/")
            self._llm_status_label.setStyleSheet("color: #e05555;")

        if whisper_path.exists():
            mb = whisper_path.stat().st_size // (1024 * 1024)
            self._whisper_status_label.setText(f"✅ {whisper_path.name} ({mb} MB)")
            self._whisper_status_label.setStyleSheet("color: #55e07a;")
        else:
            self._whisper_status_label.setText("❌ Not found — place Whisper model in data/models/")
            self._whisper_status_label.setStyleSheet("color: #e05555;")

    def _save_ai_settings(self) -> None:
        settings.set_many({
            "llm_gpu_layers":     self._gpu_layers_spin.value(),
            "llm_n_ctx":          self._ctx_spin.value(),
            "llm_n_threads":      self._threads_spin.value(),
            "whisper_model_size": self._whisper_size_combo.currentText(),
            "ollama_model":       self._ollama_model_edit.text().strip() or "llama3.2:3b",
        })
        settings.save()
        QMessageBox.information(self, "Saved", "AI settings saved.")

    # =========================================================
    # DISCORD
    # =========================================================

    def _load_discord_settings(self) -> None:
        self._discord_token_edit.setText(str(settings.get("discord_bot_token") or ""))
        self._refresh_discord_channel_table()

    def _refresh_discord_channel_table(self) -> None:
        channels = settings.get_discord_channels()
        self._discord_channels_table.setRowCount(0)
        self._discord_active_combo.clear()
        for ch in channels:
            r = self._discord_channels_table.rowCount()
            self._discord_channels_table.insertRow(r)
            self._discord_channels_table.setItem(r, 0, QTableWidgetItem(ch.get("name", "")))
            self._discord_channels_table.setItem(r, 1, QTableWidgetItem(str(ch.get("id", ""))))
            self._discord_active_combo.addItem(ch.get("name", ""), ch.get("id", ""))

        active_id = str(settings.get("discord_channel_id") or "")
        for i in range(self._discord_active_combo.count()):
            if str(self._discord_active_combo.itemData(i)) == active_id:
                self._discord_active_combo.setCurrentIndex(i)
                break

    def _add_discord_channel(self) -> None:
        from PySide6.QtWidgets import QInputDialog
        name, ok1 = QInputDialog.getText(
            self, "Add Channel", "Label (e.g. 'Main Server Comms'):"
        )
        if not ok1 or not name.strip():
            return
        ch_id, ok2 = QInputDialog.getText(
            self, "Add Channel", "Channel ID (right-click channel → Copy ID):"
        )
        if not ok2 or not ch_id.strip():
            return
        r = self._discord_channels_table.rowCount()
        self._discord_channels_table.insertRow(r)
        self._discord_channels_table.setItem(r, 0, QTableWidgetItem(name.strip()))
        self._discord_channels_table.setItem(r, 1, QTableWidgetItem(ch_id.strip()))
        self._discord_active_combo.addItem(name.strip(), ch_id.strip())

    def _remove_discord_channel(self) -> None:
        row = self._discord_channels_table.currentRow()
        if row < 0:
            return
        self._discord_channels_table.removeRow(row)
        self._discord_active_combo.removeItem(row)

    def _save_discord(self) -> None:
        token = self._discord_token_edit.text().strip()
        channels: list[dict] = []
        for r in range(self._discord_channels_table.rowCount()):
            name_item = self._discord_channels_table.item(r, 0)
            id_item   = self._discord_channels_table.item(r, 1)
            if name_item and id_item:
                channels.append({
                    "name": name_item.text().strip(),
                    "id":   id_item.text().strip(),
                })
        active_id = self._discord_active_combo.currentData() or ""
        settings.set_many({
            "discord_bot_token":   token,
            "discord_channel_ids": channels,
            "discord_channel_id":  active_id,
        })
        settings.save()
        QMessageBox.information(self, "Saved", "Discord settings saved.")

    def _test_discord(self) -> None:
        try:
            from integration.discord_capture import DiscordCapture
            if not DiscordCapture.is_available():
                QMessageBox.warning(
                    self, "Missing Dependencies",
                    DiscordCapture.install_instructions()
                )
                return
            QMessageBox.information(
                self, "Discord",
                "Dependencies found ✅\n"
                "Bot will connect to the active channel when you start a session."
            )
        except Exception as e:
            QMessageBox.critical(self, "Error", str(e))

    # =========================================================
    # TWITCH
    # =========================================================

    def _load_twitch_settings(self) -> None:
        self._twitch_channel_edit.setText(str(settings.get("twitch_channel") or ""))
        self._twitch_title_edit.setText(str(settings.get("twitch_title") or ""))
        self._twitch_auto_start_cb.setChecked(bool(settings.get("twitch_auto_start")))
        self._twitch_auto_stop_cb.setChecked(bool(settings.get("twitch_auto_stop")))

    def _save_twitch(self) -> None:
        settings.set_many({
            "twitch_channel":    self._twitch_channel_edit.text().strip(),
            "twitch_title":      self._twitch_title_edit.text().strip(),
            "twitch_auto_start": self._twitch_auto_start_cb.isChecked(),
            "twitch_auto_stop":  self._twitch_auto_stop_cb.isChecked(),
        })
        settings.save()
        QMessageBox.information(self, "Saved", "Twitch settings saved.")

    # =========================================================
    # REMOTE SYNC — load / save / test connection
    # =========================================================

    def _load_sync_settings(self) -> None:
        idx = self._sync_mode_combo.findText(settings.ANALYSIS_MODE)
        self._sync_mode_combo.setCurrentIndex(idx if idx >= 0 else 0)
        self._sync_client_name_edit.setText(settings.CLIENT_NAME)
        self._sync_server_url_edit.setText(settings.SERVER_URL)
        self._sync_api_key_edit.setText(settings.API_KEY)
        self._sync_timeout_spin.setValue(settings.REQUEST_TIMEOUT_SECONDS)
        self._sync_max_retries_spin.setValue(settings.MAX_UPLOAD_RETRIES)
        self._sync_upload_replays_cb.setChecked(settings.UPLOAD_REPLAYS)
        self._sync_upload_voice_cb.setChecked(settings.UPLOAD_VOICE)
        self._sync_upload_auto_cb.setChecked(settings.UPLOAD_AUTOMATICALLY)
        self._sync_upload_offline_cb.setChecked(settings.UPLOAD_LATER_WHEN_OFFLINE)
        self._sync_fallback_local_cb.setChecked(settings.FALLBACK_TO_LOCAL_ANALYSIS)

    def _save_sync_settings(self) -> None:
        settings.set_many({
            "analysis_mode":              self._sync_mode_combo.currentText(),
            "client_name":                self._sync_client_name_edit.text().strip() or "USB_Client",
            "server_url":                 self._sync_server_url_edit.text().strip(),
            "api_key":                    self._sync_api_key_edit.text().strip(),
            "request_timeout_seconds":    self._sync_timeout_spin.value(),
            "max_upload_retries":         self._sync_max_retries_spin.value(),
            "upload_replays":             self._sync_upload_replays_cb.isChecked(),
            "upload_voice":               self._sync_upload_voice_cb.isChecked(),
            "upload_automatically":       self._sync_upload_auto_cb.isChecked(),
            "upload_later_when_offline":  self._sync_upload_offline_cb.isChecked(),
            "fallback_to_local_analysis": self._sync_fallback_local_cb.isChecked(),
        })
        settings.save()
        QMessageBox.information(self, "Saved", "Remote sync settings saved.")

    def _test_sync_connection(self) -> None:
        # Save first so the test hits whatever the user just typed, not
        # whatever was loaded when the tab opened.
        self._save_sync_settings_quiet()

        if not settings.SERVER_URL:
            QMessageBox.warning(self, "No Server URL", "Enter a server URL before testing the connection.")
            return

        if self._test_conn_thread is not None and self._test_conn_thread.isRunning():
            return  # Already testing — ignore duplicate clicks.

        self._sync_test_btn.setEnabled(False)
        self._sync_test_status_label.setText("Testing...")
        self._sync_test_status_label.setStyleSheet("color: #999;")

        self._test_conn_thread = QThread()
        self._test_conn_worker = _TestConnectionWorker()
        self._test_conn_worker.moveToThread(self._test_conn_thread)

        self._test_conn_thread.started.connect(self._test_conn_worker.run)
        self._test_conn_worker.finished.connect(self._on_test_connection_finished)
        self._test_conn_worker.finished.connect(self._test_conn_thread.quit)
        self._test_conn_thread.finished.connect(self._test_conn_cleanup)

        self._test_conn_thread.start()

    def _save_sync_settings_quiet(self) -> None:
        """Same as _save_sync_settings() but without the confirmation dialog
        — used before Test Connection so it always tests current field values."""
        settings.set_many({
            "analysis_mode":              self._sync_mode_combo.currentText(),
            "client_name":                self._sync_client_name_edit.text().strip() or "USB_Client",
            "server_url":                 self._sync_server_url_edit.text().strip(),
            "api_key":                    self._sync_api_key_edit.text().strip(),
            "request_timeout_seconds":    self._sync_timeout_spin.value(),
            "max_upload_retries":         self._sync_max_retries_spin.value(),
        })
        settings.save()

    def _on_test_connection_finished(self, success: bool, message: str) -> None:
        self._sync_test_btn.setEnabled(True)
        if success:
            self._sync_test_status_label.setText(f"✅ {message}")
            self._sync_test_status_label.setStyleSheet("color: #55e07a;")
        else:
            self._sync_test_status_label.setText(f"❌ {message}")
            self._sync_test_status_label.setStyleSheet("color: #e05555;")

    def _test_conn_cleanup(self) -> None:
        if self._test_conn_thread is not None:
            self._test_conn_thread.deleteLater()
        self._test_conn_thread = None
        self._test_conn_worker = None