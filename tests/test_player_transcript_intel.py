"""
Milestone 6 — named speakers + per-player comms lines feeding into the AI
prompts (analysis/intel_engine.py). Verifies:
  * get_player_intel() pulls that player's own tagged transcript lines
    into their prompt (via _build_player_prompt), when a speaker has been
    tagged as them (gui/speaker_tagging_dialog.py's persisted mapping).
  * analyze_match()'s comms section shows a real player name for a tagged
    speaker instead of the raw "Speaker_N" cluster label.
Captures prompts by intercepting generate() rather than requiring a real
Ollama/llama-cpp backend.
"""
import json
import tempfile
from pathlib import Path

import pytest

from database.db_manager import DatabaseManager
from database.migrations import run_migrations
from database.repositories import Repository
from database.seed_operators import seed_database
from models.player import Player
from models.round import Round
from models.round_resources import RoundResources
from models.player_round_stats import PlayerRoundStats
from analysis.intel_engine import IntelEngine


@pytest.fixture
def repo_with_match(tmp_path):
    db_path = tmp_path / "t.db"
    schema_path = Path("database/schema.sql")
    db = DatabaseManager(db_path=db_path, schema_path=schema_path)
    run_migrations(db)
    seed_database(db)
    repo = Repository(db_manager=db)

    p1 = repo.insert_player(Player(player_id=None, name="Ash_Main", is_team_member=True))

    match_id = repo.create_match("Opponent", "Bank")
    r = Round(
        round_id=None, match_id=match_id, round_number=1, side="attack",
        site="Site", outcome="win",
        resources=RoundResources(
            resource_id=None, round_id=None, side="attack",
            team_drones_start=10, team_drones_lost=0,
            team_reinforcements_start=10, team_reinforcements_used=0,
        ),
        player_stats=[],
    )
    round_id = repo.insert_round(r, match_id)
    repo.insert_round_resources(r.resources, round_id)
    op = repo.get_all_operators()[0]
    stat = PlayerRoundStats(
        stat_id=None, round_id=round_id, player_id=p1, player=None, operator=op,
        kills=3, deaths=1, assists=1, engagements_taken=4, engagements_won=3,
        ability_start=op.ability_max_count, ability_used=1,
        secondary_gadget=None, secondary_start=0, secondary_used=0,
        plant_attempted=False, plant_successful=False,
    )
    repo.insert_player_round_stats(stat, round_id, p1)

    processed = {
        "word_count": 10, "duration_sec": 30.0, "callouts": [],
        "location_freq": {"site": 2}, "action_freq": {}, "coordination_gaps": [],
        "silence_periods": [], "fight_silence_count": 0, "fight_silences": [],
        "speakers": {
            "Speaker_1": {
                "word_count": 6, "talk_time": 4.0, "top_words": ["planting"],
                "segments": [
                    {"start": 1.0, "end": 2.0, "text": "planting defuser on site"},
                    {"start": 5.0, "end": 6.0, "text": "watch that flank"},
                ],
            },
        },
    }
    with repo.db.get_connection() as conn:
        conn.execute(
            "INSERT INTO transcripts (match_id, raw_text, processed_segments_json) VALUES (?, ?, ?)",
            (match_id, "planting defuser on site watch that flank", json.dumps(processed)),
        )
        conn.commit()

    # Tag Speaker_1 as Ash_Main, exactly like the tagging dialog would.
    repo.set_speaker_label(match_id, "Speaker_1", p1)

    return repo, match_id, p1, db_path, schema_path


class _CapturingIntelEngine(IntelEngine):
    """Same IntelEngine, but generate() just records the prompt instead of
    calling a real AI backend."""
    def __init__(self, *a, **kw):
        super().__init__(*a, **kw)
        self.prompts: list[str] = []

    def generate(self, prompt: str, max_tokens: int = 900, progress_callback=None) -> str:
        self.prompts.append(prompt)
        return "STRENGTH: x\nFOCUS: y\nDRILL: z\n"


def test_player_intel_includes_own_tagged_comms(repo_with_match):
    repo, match_id, p1, db_path, schema_path = repo_with_match
    engine = _CapturingIntelEngine(db_path=db_path, schema_path=schema_path)

    results = engine.get_player_intel(match_id)

    assert "Ash_Main" in results
    assert len(engine.prompts) == 1
    prompt = engine.prompts[0]
    assert "COMMS" in prompt
    assert "planting defuser on site" in prompt
    assert "watch that flank" in prompt


def test_match_summary_shows_named_speaker_not_raw_tag(repo_with_match):
    repo, match_id, p1, db_path, schema_path = repo_with_match
    engine = _CapturingIntelEngine(db_path=db_path, schema_path=schema_path)

    engine.analyze_match(match_id)

    assert len(engine.prompts) == 1
    prompt = engine.prompts[0]
    assert "Ash_Main: 6 words" in prompt
    # Should not show the raw untagged label once a name is known.
    assert "Speaker_1:" not in prompt


def test_untagged_speaker_still_shows_with_untagged_marker(tmp_path):
    db_path = tmp_path / "t2.db"
    schema_path = Path("database/schema.sql")
    db = DatabaseManager(db_path=db_path, schema_path=schema_path)
    run_migrations(db)
    seed_database(db)
    repo = Repository(db_manager=db)
    match_id = repo.create_match("Opp", "Bank")

    processed = {
        "word_count": 3, "duration_sec": 5.0, "callouts": [],
        "location_freq": {}, "action_freq": {}, "coordination_gaps": [],
        "silence_periods": [], "fight_silence_count": 0, "fight_silences": [],
        "speakers": {
            "Speaker_1": {"word_count": 3, "talk_time": 2.0, "top_words": [],
                          "segments": [{"start": 0.0, "end": 1.0, "text": "go go go"}]},
        },
    }
    with repo.db.get_connection() as conn:
        conn.execute(
            "INSERT INTO transcripts (match_id, raw_text, processed_segments_json) VALUES (?, ?, ?)",
            (match_id, "go go go", json.dumps(processed)),
        )
        conn.commit()

    engine = _CapturingIntelEngine(db_path=db_path, schema_path=schema_path)
    engine.analyze_match(match_id)
    prompt = engine.prompts[0]
    assert "Speaker_1 (untagged): 3 words" in prompt
