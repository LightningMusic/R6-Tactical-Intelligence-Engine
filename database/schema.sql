-- ==========================================
-- R6 Tactical Intelligence Engine
-- FOUNDATION V3.2 — SCHEMA
-- ==========================================

PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS metadata (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS maps (
    map_id         INTEGER PRIMARY KEY AUTOINCREMENT,
    name           TEXT NOT NULL UNIQUE,
    is_active_pool INTEGER NOT NULL DEFAULT 1 CHECK(is_active_pool IN (0, 1))
);

CREATE TABLE IF NOT EXISTS matches (
    match_id      INTEGER PRIMARY KEY AUTOINCREMENT,
    datetime      TEXT NOT NULL,
    opponent_name TEXT NOT NULL,
    map           TEXT NOT NULL,
    map_id        INTEGER,
    result        TEXT CHECK(result IN ('win', 'loss') OR result IS NULL),
    recording_path TEXT,
    FOREIGN KEY (map_id) REFERENCES maps(map_id)
);

CREATE TABLE IF NOT EXISTS rounds (
    round_id     INTEGER PRIMARY KEY AUTOINCREMENT,
    match_id     INTEGER NOT NULL,
    round_number INTEGER NOT NULL,
    side         TEXT CHECK(side IN ('attack', 'defense')) NOT NULL,
    site         TEXT NOT NULL,
    outcome      TEXT CHECK(outcome IN ('win', 'loss')) NOT NULL,
    FOREIGN KEY (match_id) REFERENCES matches(match_id) ON DELETE CASCADE,
    UNIQUE(match_id, round_number)
);

CREATE TABLE IF NOT EXISTS players (
    player_id      INTEGER PRIMARY KEY AUTOINCREMENT,
    name           TEXT NOT NULL UNIQUE,
    is_team_member INTEGER NOT NULL CHECK(is_team_member IN (0, 1)),
    UNIQUE(name, is_team_member)
);

CREATE TABLE IF NOT EXISTS operators (
    operator_id       INTEGER PRIMARY KEY AUTOINCREMENT,
    name              TEXT NOT NULL UNIQUE,
    side              TEXT CHECK(side IN ('attack', 'defense')) NOT NULL,
    ability_name      TEXT NOT NULL,
    ability_max_count INTEGER NOT NULL CHECK(ability_max_count >= 0)
);

CREATE TABLE IF NOT EXISTS gadgets (
    gadget_id INTEGER PRIMARY KEY AUTOINCREMENT,
    name      TEXT NOT NULL UNIQUE,
    category  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS operator_gadget_options (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    operator_id INTEGER NOT NULL,
    gadget_id   INTEGER NOT NULL,
    max_count   INTEGER NOT NULL CHECK(max_count >= 0),
    UNIQUE(operator_id, gadget_id),
    FOREIGN KEY (operator_id) REFERENCES operators(operator_id) ON DELETE CASCADE,
    FOREIGN KEY (gadget_id)   REFERENCES gadgets(gadget_id)     ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS player_round_stats (
    stat_id             INTEGER PRIMARY KEY AUTOINCREMENT,
    round_id            INTEGER NOT NULL,
    player_id           INTEGER NOT NULL,
    operator_id         INTEGER NOT NULL,
    kills               INTEGER NOT NULL CHECK(kills >= 0),
    deaths              INTEGER NOT NULL CHECK(deaths >= 0),
    assists             INTEGER NOT NULL CHECK(assists >= 0),
    engagements_taken   INTEGER NOT NULL CHECK(engagements_taken >= 0),
    engagements_won     INTEGER NOT NULL CHECK(engagements_won >= 0),
    ability_start       INTEGER NOT NULL CHECK(ability_start >= 0),
    ability_used        INTEGER NOT NULL CHECK(ability_used >= 0),
    secondary_gadget_id INTEGER,
    secondary_start     INTEGER NOT NULL CHECK(secondary_start >= 0),
    secondary_used      INTEGER NOT NULL CHECK(secondary_used >= 0),
    plant_attempted     INTEGER NOT NULL CHECK(plant_attempted IN (0, 1)),
    plant_successful    INTEGER NOT NULL CHECK(plant_successful IN (0, 1)),
    FOREIGN KEY (round_id)            REFERENCES rounds(round_id) ON DELETE CASCADE,
    FOREIGN KEY (player_id)           REFERENCES players(player_id),
    FOREIGN KEY (operator_id)         REFERENCES operators(operator_id),
    FOREIGN KEY (secondary_gadget_id) REFERENCES gadgets(gadget_id),
    UNIQUE(round_id, player_id)
);

CREATE TABLE IF NOT EXISTS round_resources (
    resource_id               INTEGER PRIMARY KEY AUTOINCREMENT,
    round_id                  INTEGER NOT NULL UNIQUE,
    team_drones_start         INTEGER NOT NULL DEFAULT 10 CHECK(team_drones_start = 10),
    team_drones_lost          INTEGER NOT NULL DEFAULT 0  CHECK(team_drones_lost >= 0),
    team_reinforcements_start INTEGER NOT NULL DEFAULT 10 CHECK(team_reinforcements_start = 10),
    team_reinforcements_used  INTEGER NOT NULL DEFAULT 0  CHECK(team_reinforcements_used >= 0),
    FOREIGN KEY (round_id) REFERENCES rounds(round_id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS transcripts (
    transcript_id           INTEGER PRIMARY KEY AUTOINCREMENT,
    match_id                INTEGER NOT NULL,
    raw_text                TEXT NOT NULL,
    processed_segments_json TEXT,
    FOREIGN KEY (match_id) REFERENCES matches(match_id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS derived_metrics (
    metric_id       INTEGER PRIMARY KEY AUTOINCREMENT,
    match_id        INTEGER NOT NULL,
    metric_name     TEXT NOT NULL,
    metric_value    REAL NOT NULL,
    is_ai_generated INTEGER NOT NULL DEFAULT 0 CHECK(is_ai_generated IN (0, 1)),
    FOREIGN KEY (match_id) REFERENCES matches(match_id) ON DELETE CASCADE
);