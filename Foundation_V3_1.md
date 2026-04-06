# R6 Tactical Intelligence Engine

# FOUNDATION V3.2 — PRE-BUILD SPECIFICATION

---

## 1. System Overview

Local desktop application built in Python (PySide6) designed for high-speed USB portability that:

* **Controls recording** through OBS Studio (Continuous session audio from Discord/System).
* **Stores all match data** in a local SQLite database (No cloud).
* **Seeds and manages** a full operator/gadget database for Tom Clancy's Rainbow Six Siege.
* **Automates Data Entry** by importing `.rec` files via `r6-dissect`.
* **Transcribes Comms** locally using Whisper, synced to match timestamps.
* **Analyzes Performance** via a local, lightweight AI layer (Intel Engine).
* **Generates Analytics** and exports reports (CSV, HTML, TXT).
* **Operates as a single .exe**, requiring no administrator rights.

---

## 2. Final File Structure (V3.2)

```plaintext
R6Analyzer/
│
├── main.py
│
├── app/
│   ├── app_controller.py
│   ├── config.py                <-- PATH AUTHORITY (sys.executable resolution)
│   ├── session_manager.py       <-- NEW (Snapshot & Diff logic)
│
├── gui/
│   ├── main_window.py
│   ├── dashboard_view.py
│   ├── match_view.py            <-- (Manual Fallback Entry)
│   ├── recording_view.py        <-- (Session Status & Control)
│   ├── analysis_view.py         <-- (Primary Landing Page)
│   ├── export_view.py
│   ├── settings_view.py
│   ├── db_editor_view.py
│
├── database/
│   ├── db_manager.py
│   ├── schema.sql
│   ├── repositories.py
│   ├── seed_operators.py
│   ├── migrations.py            <-- Handles Map FK migration
│
├── integration/
│   ├── bin/
│   │   └── r6-dissect.exe       <-- Bundled for portability
│   ├── obs_controller.py
│   ├── whisper_transcriber.py
│   ├── rec_importer.py          <-- Returns ImportResult object
│
├── analysis/
│   ├── transcript_parser.py
│   ├── intel_engine.py          <-- ACTIVE (Local AI Inference)
│   ├── metrics_engine.py
│   ├── report_generator.py
│   ├── timeline_aligner.py      <-- NEW (Match/Audio sync)
│
├── models/
│   ├── match.py                 <-- Updated for Map FK
│   ├── round.py
│   ├── player.py
│   ├── operator.py
│   ├── gadget.py
│   ├── map.py                   <-- NEW
│   ├── import_result.py         <-- NEW (Status & Partial data)
│   ├── derived_metric.py        <-- NEW (Typed AI output)
│   ├── player_round_stats.py
│   ├── round_resources.py
│   ├── transcript.py
│
├── exports/                     <-- Relative to USB Root
│
├── data/                        <-- Relative to USB Root
│   ├── matches.db
│   ├── recordings/
│   ├── transcripts/
│   ├── reports/
│
└── resources/
    ├── icons/
```

---

## 3. Database Schema (LOCKED SOURCE OF TRUTH)

The schema is normalized to ensure the AI can parse structured data.

### Core Tables
* **matches**: ID, datetime, opponent, map_id (FK), result, recording_path.
* **rounds**: ID, match_id, round_number, side, site, outcome.
* **players**: ID, name, is_team_member.
* **maps**: ID, name, is_active_pool.

### Operator & Equipment
* **operators**: ID, name, side, ability_name, ability_max_count.
* **gadgets**: ID, name, category.
* **operator_gadget_options**: ID, operator_id (FK), gadget_id (FK), max_count.

### Stats & Resources
* **player_round_stats**: Kills, deaths, assists, engagements, ability/gadget usage, plant status.
* **round_resources**: Drones lost (Attack) or Reinforcements used (Defense).

### Intelligence Layer
* **transcripts**: ID, match_id, raw_text, processed_segments_json (Time-aligned).
* **derived_metrics**: ID, match_id, metric_name, metric_value, is_ai_generated.

---

## 4. The Build Order (LOCKED SEQUENCE)

1.  **`app/config.py`**: Establish `BASE_DIR` and path resolution logic.
2.  **Schema Migration**: Create `maps` table and migrate `matches.map` (string) to `matches.map_id` (FK).
3.  **`models/import_result.py`**: Define the contract (See Section 9).
4.  **`integration/rec_importer.py`**: Implementation of `r6-dissect` wrapper returning `ImportResult`.
5.  **`app/session_manager.py`**: Implementation of Folder Snapshot + Stability Lock + Handoff.
6.  **UI Integration**: Connect `recording_view` signals to `analysis_view` (Success) or `match_view` (Partial/Fail).

---

## 5. Operational Modes & Handoff

### Primary Flow (Automated)
1. **Start Recording**: `session_manager` snapshots the current R6 Replay folder and starts OBS Discord recording.
2. **Stop Recording**: `session_manager` stops OBS, performs a folder "Diff," and checks file stability (lock check).
3. **Processing**: System triggers `rec_importer`, transcribes audio, and runs AI analysis.
4. **Handoff**: `rec_importer` emits `ImportResult` with `SUCCESS`. User lands on **Analysis View**.

### Fallback Flow (Manual)
* **Trigger**: If `ImportResult` status is `CRITICAL_FAILURE` or `PARTIAL_FAILURE`.
* **Behavior**: UI routes to **Match View** (Manual Entry).
* **Data Recovery**: Partial data (recovered Map IDs or scores) is pre-filled into the Manual Entry UI to minimize user friction.

---

## 6. Local AI & Analytics (Intel Engine)

The `intel_engine.py` acts as a local inference layer.
* **Inputs**: Transcripts, `.rec` extracted stats, and resource usage.
* **Processing**: Identifies callout patterns, coordination gaps, and utility efficiency.
* **Output**: Injects natural language tactical summaries into the `derived_metrics` table via the `DerivedMetric` model.

---

## 7. USB Deployment & Path Authority

* **Path Resolution**: `app/config.py` uses `sys.executable` to resolve all paths relative to the USB root.
* **Strict Enforcement**: No module may use `os.getcwd()` or hardcoded strings. All DB and file operations must pull paths from `config.py`.
* **Portability**: High-speed USB 3.0+ required. All data stays within the `/data/` root.

---

## 8. Integration Specifications

### OBS Studio
* Controlled via `obs-websocket`.
* Records a single continuous audio file per session.

### Transcription (Whisper)
* Runs locally. Uses match start/end timestamps from `.rec` files to clip the session transcript.

### Replay Parsing (r6-dissect)
* Bundled binary integrated via `rec_importer.py`.
* Parsed data is structured into the `ImportResult` dataclass for the UI handoff.

---

## 9. Non-Goals (V3.2 Locked)

* No Cloud or API dependencies.
* No real-time/live overlay.
* No video frame/OCR parsing (Replays only).
* No Discord/Third-party scraping.

---
## 10. The Import Contract (NEW - CRITICAL)

This dataclass defines the communication between the automated importer and the UI.

### **ImportStatus (Enum)**
* `SUCCESS`: All data parsed.
* `PARTIAL_FAILURE`: Replay found, but some rounds or stats are missing/corrupted.
* `CRITICAL_FAILURE`: No replay found or `r6-dissect` crashed.

### **ImportResult (Dataclass)**
| Field | Type | Description |
| :--- | :--- | :--- |
| `status` | `ImportStatus` | The overall result of the operation. |
| `map_id` | `Optional[int]` | ID of the detected map (for pre-filling UI). |
| `match_id` | `Optional[int]` | ID of the match if already created in DB. |
| `score_us` | `Optional[int]` | Detected team score. |
| `score_them` | `Optional[int]` | Detected opponent score. |
| `rounds` | `List[RoundModel]` | List of successfully parsed round objects. |
| `error_message`| `Optional[str]` | Human-readable error for UI Toasts. |

---

### SYSTEM STATE: V3.2 PRE-BUILD
The system is now **Replay-integrated**, **Path-locked**, and **AI-Ready**. Internal models are expanded to cover all database tables to prevent Pylance friction.