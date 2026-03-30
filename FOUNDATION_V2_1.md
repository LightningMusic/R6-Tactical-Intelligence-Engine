# R6 Tactical Intelligence Engine

# FOUNDATION V2.1 — FINAL LOCKED SPECIFICATION

---

## 1. System Overview

Local desktop application built in Python (PySide6) that:

* Controls recording through OBS Studio
* Stores all match data in SQLite
* Seeds and manages full operator database for Tom Clancy's Rainbow Six Siege
* Tracks structured player, operator, and resource stats
* Generates derived analytics
* Exports reports and raw data
* Supports future AI analysis layer
* Inputs data in set database template that will be used to make analysis and can be exported out for a CSV
* Runs as a single .exe, not needing admin rights

Single executable. Local database. No cloud.

---

# 2. Final File Structure (LOCKED)

```plaintext
R6Analyzer/
│
├── main.py
│
├── app/
│   ├── app_controller.py
│   ├── config.py
│
├── gui/
│   ├── main_window.py
│   ├── dashboard_view.py
│   ├── match_view.py
│   ├── recording_view.py
│   ├── analysis_view.py
│   ├── export_view.py
│   ├── settings_view.py
│   ├── db_editor_view.py
│
├── database/
│   ├── db_manager.py
│   ├── schema.sql
│   ├── repositories.py
│   ├── seed_operators.py
│   ├── migrations.py
│
├── integration/
│   ├── obs_controller.py
│   ├── whisper_transcriber.py
│
├── analysis/
│   ├── transcript_parser.py
│   ├── intel_engine.py
│   ├── metrics_engine.py
│   ├── report_generator.py
│
├── models/
│   ├── match.py
│   ├── round.py
│   ├── player.py
│   ├── operator.py
│   ├── gadget.py
│   ├── player_round_stats.py
│   ├── round_resources.py
│   ├── transcript.py
│
├── exports/
│
├── data/
│   ├── matches.db
│   ├── recordings/
│   ├── transcripts/
│   ├── reports/
│
└── resources/
    ├── icons/
```

No structural changes permitted in V2.1.

---

# 3. Database Schema (FINAL)

## matches

* match_id (PK)
* datetime
* opponent_name
* map
* result
* recording_path

---

## rounds

* round_id (PK)
* match_id (FK)
* round_number
* side (attack / defense)
* site
* outcome

---

## players

* player_id (PK)
* name
* is_team_member (boolean)

Team players are predefined in Settings.

---

## operators

* operator_id (PK)
* name
* side (attack / defense)
* ability_name
* ability_max_count

Seeded with all operators.

---

## gadgets

* gadget_id (PK)
* name
* category

---

## operator_gadget_options

* id (PK)
* operator_id (FK)
* gadget_id (FK)
* max_count

Defines valid secondary gadget choices.

---

## player_round_stats

Per player per round.

* stat_id (PK)
* round_id (FK)
* player_id (FK)
* operator_id (FK)

### Combat

* kills
* deaths
* assists
* engagements_taken
* engagements_won

### Ability

* ability_start (auto from operator)
* ability_used

### Secondary Gadget

* secondary_gadget_id (FK)
* secondary_start (auto from mapping)
* secondary_used

### Objective

* plant_attempted (boolean)
* plant_successful (boolean)

---

## round_resources

Per round.

* resource_id (PK)
* round_id (FK)

If attack:

* team_drones_start (always 10)
* team_drones_lost

If defense:

* team_reinforcements_start (always 10)
* team_reinforcements_used

Remaining values derived.

---

## transcripts

* transcript_id (PK)
* match_id (FK)
* raw_text
* processed_segments_json

---

## derived_metrics

* metric_id (PK)
* match_id (FK)
* metric_name
* metric_value

---

# 4. Manual Stat Model (LOCKED)

Per Player Per Round:

### Identity

* Operator (dropdown)

### Combat

* Kills
* Deaths
* Assists
* Engagements Taken
* Engagements Won

### Ability

* Ability Used

### Secondary Gadget

* Secondary Gadget Type (dropdown)
* Secondary Used

### Objective

* Plant Attempted
* Plant Successful

---

Per Round (Team Resources):

Attack:

* Team Drones Lost

Defense:

* Team Reinforcements Used

All starting values auto-filled.

---

# 5. Operator System

* All operators seeded at install
* All gadget relationships seeded
* Ability max counts predefined
* Secondary gadget max counts predefined

GUI Behavior:

1. Select operator
2. Ability_start auto-fills
3. Secondary gadget dropdown populates from valid options
4. Secondary_start auto-fills

Prevents invalid configurations.

---

# 6. Settings Architecture

## General Settings

* Team player names
* Default recording directory
* OBS WebSocket config
* Whisper model path

---

## Database Editor (Controlled Admin Panel)

Editable:

* Operator ability_max_count
* Gadget max_count
* Operator → Gadget relationships

Not editable:

* Primary keys
* Match data
* Historical stats

Used for future balance patches.

---

# 7. Migration Design

`migrations.py` handles:

* Schema version tracking
* Incremental schema updates
* Operator stat updates
* Gadget balance changes

Database version stored in metadata table.

Future updates:

* Increment version
* Apply migration script
* Preserve match history

No destructive updates allowed.

---

# 8. OBS Integration

Via obs-websocket.

Controlled through:

`integration/obs_controller.py`

Capabilities:

* Connect
* Start recording
* Stop recording
* Retrieve recording path
* Verify status

Recording file path stored in matches table.

Failure handling:

* Graceful UI warning
* No crash

---

# 9. Transcription Integration

Local Whisper model.

Process:

1. Retrieve recording
2. Generate transcript
3. Store raw_text
4. Store structured JSON segments

No cloud APIs.

---

# 10. AI Interaction (Future Layer)

AI will:

* Analyze transcripts
* Identify call patterns
* Classify intel types
* Detect hesitation or clutter
* Generate tactical summaries

AI never modifies raw stats.

AI reads from:

* transcripts
* player_round_stats
* round_resources

AI outputs:

* Additional derived_metrics
* Natural language analysis sections in reports

Optional module. Not required for core function.

---

# 11. Derived Metrics (Auto-Calculated)

Examples:

* Engagement win %
* Kill participation rate
* Utility efficiency %
* Ability usage rate
* Drone loss efficiency
* Reinforcement usage efficiency
* Plant conversion rate
* Operator performance by map
* Site win rate

No manual input required.

---

# 12. Export System

Exports generated from database only.

Available:

* CSV (match stats)
* TXT (transcript)
* HTML (report)
* MP3 (original recording)

Saved to `/exports/`

---

# 13. Non-Goals (V2.1 Locked)

* Real-time live analytics
* Video frame parsing
* Discord API scraping
* Cloud sync
* Multiplayer shared DB
* Predictive ML models

---

# SYSTEM STATE

Architecture is now:

* Operator-aware
* Gadget-aware
* Resource-aware
* Migration-ready
* AI-expandable
* OBS-integrated
* Fully normalized

Stable for implementation.
