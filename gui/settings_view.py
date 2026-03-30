from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QLabel, QPushButton,
    QListWidget, QLineEdit, QMessageBox, QHBoxLayout
)

from database.repositories import Repository
from models.player import Player

class SettingsView(QWidget):
    """
    Settings panel for configuring team players.
    """

    def __init__(self):
        super().__init__()

        self.repo = Repository()

        self.init_ui()
        self.load_players()

    def init_ui(self):
        layout = QVBoxLayout()

        layout.addWidget(QLabel("Team Players"))

        # Player list
        self.player_list = QListWidget()
        layout.addWidget(self.player_list)

        # Input field
        input_layout = QHBoxLayout()

        self.player_input = QLineEdit()
        self.player_input.setPlaceholderText("Enter player name")
        input_layout.addWidget(self.player_input)

        add_btn = QPushButton("Add Player")
        add_btn.clicked.connect(self.add_player)
        input_layout.addWidget(add_btn)

        layout.addLayout(input_layout)

        # Remove button
        remove_btn = QPushButton("Remove Selected Player")
        remove_btn.clicked.connect(self.remove_player)
        layout.addWidget(remove_btn)

        # Save button
        save_btn = QPushButton("Save Settings")
        save_btn.clicked.connect(self.save_players)
        layout.addWidget(save_btn)

        self.setLayout(layout)

    # -------------------------
    # Load Players
    # -------------------------
    def load_players(self):
        self.player_list.clear()
        players = self.repo.get_team_players()
        for p in players:
            self.player_list.addItem(p.name)

    # -------------------------
    # Add Player
    # -------------------------
    def add_player(self):
        name = self.player_input.text().strip()
        if not name:
            return

        self.player_list.addItem(name)
        self.player_input.clear()

    # -------------------------
    # Remove Player
    # -------------------------
    def remove_player(self):
        row = self.player_list.currentRow()
        if row >= 0:
            self.player_list.takeItem(row)

    # -------------------------
    # Save to DB
    # -------------------------
    def save_players(self):
        try:
            self.repo.clear_team_players()

            for i in range(self.player_list.count()):
                name = self.player_list.item(i).text()

                player = Player(
                    player_id=None,
                    name=name,
                    is_team_member=True
                )

                self.repo.insert_player(player)

            QMessageBox.information(self, "Saved", "Team updated successfully!")

        except Exception as e:
            QMessageBox.critical(self, "Error", str(e))