"""
Milestone 6 — manual speaker tagging.

diarize_speakers() (integration/whisper_transcriber.py) groups transcript
segments into "Speaker_N" clusters — automatically, either by real voice
similarity (if resemblyzer is installed) or by silence gaps as a fallback.
Either way, "Speaker_N" only ever means "probably the same person" — it
has no idea which teammate that actually is. This dialog is the one-time,
per-match step that closes that gap: review a few representative lines
from each Speaker_N cluster and assign it to a team player (or leave it
unassigned, e.g. for an opponent's voice bleeding through comms).

The mapping is stored in transcript_speaker_labels (match_id, speaker_tag,
player_id) and is what lets analysis/intel_engine.py show real names in
the AI match summary's comms section and per-player intel instead of
anonymous "Speaker_1, Speaker_2..." labels.
"""
from __future__ import annotations

from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QComboBox, QPushButton,
    QGroupBox, QTextEdit, QScrollArea, QFrame, QWidget, QMessageBox,
    QDialogButtonBox,
)
from PySide6.QtCore import Qt

UNASSIGNED = "— Unassigned / not a teammate —"


class SpeakerTaggingDialog(QDialog):
    def __init__(self, match_id: int, parent=None) -> None:
        super().__init__(parent)
        self.match_id = match_id
        self.setWindowTitle("Tag Speakers")
        self.resize(720, 600)

        self._combos: dict[str, QComboBox] = {}
        self._team_player_ids: dict[str, int] = {}  # display name -> player_id

        self._build_ui()
        self._load()

    # =====================================================
    # UI
    # =====================================================

    def _build_ui(self) -> None:
        outer = QVBoxLayout(self)

        intro = QLabel(
            "Whisper + voice clustering grouped this match's comms into the "
            "speaker clusters below, based on how similar their voices sound "
            "(or, without that, how the pauses fall). Skim a few of each "
            "speaker's lines and say who it actually is — this only has to "
            "be done once per match."
        )
        intro.setWordWrap(True)
        outer.addWidget(intro)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        self._content = QWidget()
        self._content_layout = QVBoxLayout(self._content)
        self._content_layout.setSpacing(12)
        scroll.setWidget(self._content)
        outer.addWidget(scroll, 1)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self._save)
        buttons.rejected.connect(self.reject)
        outer.addWidget(buttons)

    def _load(self) -> None:
        from database.repositories import Repository
        repo = Repository()

        team_players = repo.get_team_players()
        self._team_player_ids = {p.name: p.player_id for p in team_players}

        data = repo.get_transcript_processed_data(self.match_id) or {}
        speakers: dict = data.get("speakers") or {}
        existing_labels = repo.get_speaker_labels(self.match_id)

        if not speakers:
            empty = QLabel(
                "No speaker data found for this match yet — run/re-run "
                "transcription first (Recording tab), or this match had no "
                "usable audio."
            )
            empty.setWordWrap(True)
            self._content_layout.addWidget(empty)
            return

        for tag in sorted(speakers.keys(), key=lambda t: t.split("_")[-1].zfill(3)):
            spk = speakers[tag]
            box = self._build_speaker_box(tag, spk, existing_labels.get(tag))
            self._content_layout.addWidget(box)

        self._content_layout.addStretch()

    def _build_speaker_box(self, tag: str, spk: dict, current_player_id) -> QGroupBox:
        word_count = int(spk.get("word_count", 0))
        talk_time  = float(spk.get("talk_time", 0.0))
        segments   = spk.get("segments") or []

        box = QGroupBox(f"{tag}  —  {word_count} words, {talk_time:.0f}s talk time")
        layout = QVBoxLayout(box)

        preview_lines = [
            f"[{s.get('start', 0):.0f}s] {str(s.get('text', '')).strip()}"
            for s in segments[:6] if str(s.get("text", "")).strip()
        ]
        preview = QTextEdit()
        preview.setReadOnly(True)
        preview.setPlainText(
            "\n".join(preview_lines) if preview_lines else "(no transcribed lines)"
        )
        preview.setMaximumHeight(110)
        layout.addWidget(preview)

        row = QHBoxLayout()
        row.addWidget(QLabel("This speaker is:"))
        combo = QComboBox()
        combo.addItem(UNASSIGNED, None)
        for name, pid in self._team_player_ids.items():
            combo.addItem(name, pid)
        if current_player_id is not None:
            idx = combo.findData(current_player_id)
            if idx >= 0:
                combo.setCurrentIndex(idx)
        row.addWidget(combo, 1)
        layout.addLayout(row)

        self._combos[tag] = combo
        return box

    # =====================================================
    # SAVE
    # =====================================================

    def _save(self) -> None:
        try:
            from database.repositories import Repository
            repo = Repository()
            for tag, combo in self._combos.items():
                player_id = combo.currentData()
                repo.set_speaker_label(self.match_id, tag, player_id)
            self.accept()
        except Exception as e:
            QMessageBox.critical(self, "Error", f"Could not save speaker tags: {e}")
