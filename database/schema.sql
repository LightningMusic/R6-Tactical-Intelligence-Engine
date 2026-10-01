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
    map_game_id   INTEGER,
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

-- Known in-game usernames/tags tied to a persistent player identity (added
-- Milestone 6). Lets a team player's canonical name stay stable in the UI
-- and in analysis while their raw Ubisoft username varies (tag changes,
-- typos, alt accounts) — see database/repositories.py resolve_player_by_username()
-- and the Settings > Players "aliases" field. One alias maps to exactly one
-- player, enforced case-insensitively.
CREATE TABLE IF NOT EXISTS player_aliases (
    alias_id  INTEGER PRIMARY KEY AUTOINCREMENT,
    player_id INTEGER NOT NULL,
    alias     TEXT NOT NULL COLLATE NOCASE,
    UNIQUE(alias),
    FOREIGN KEY (player_id) REFERENCES players(player_id) ON DELETE CASCADE
);

-- Per-match mapping from a diarized speaker cluster (e.g. "Speaker_1") to a
-- team player, filled in via the manual speaker-tagging screen (Milestone 6).
-- player_id is nullable until the user assigns it (or if the cluster turns
-- out to be an opponent/background voice with no team-player mapping).
CREATE TABLE IF NOT EXISTS transcript_speaker_labels (
    label_id    INTEGER PRIMARY KEY AUTOINCREMENT,
    match_id    INTEGER NOT NULL,
    speaker_tag TEXT NOT NULL,
    player_id   INTEGER,
    UNIQUE(match_id, speaker_tag),
    FOREIGN KEY (match_id) REFERENCES matches(match_id) ON DELETE CASCADE,
    FOREIGN KEY (player_id) REFERENCES players(player_id) ON DELETE SET NULL
);

CREATE TABLE IF NOT EXISTS operators (
    operator_id       INTEGER PRIMARY KEY AUTOINCREMENT,
    name              TEXT NOT NULL UNIQUE,
    side              TEXT CHECK(side IN ('attack', 'defense')) NOT NULL,
    ability_name      TEXT NOT NULL,
    ability_max_count INTEGER NOT NULL CHECK(ability_max_count >= 0),
    source            TEXT NOT NULL DEFAULT 'seed'
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

-- ── Game catalog: what the game itself calls things ────────────────────
-- Replays identify operators and maps by numeric in-game IDs, not names.
-- These tables let the database learn those IDs from imported matches
-- (database/game_catalog.py) and from Ubisoft's official operator pages
-- (integration/ubisoft_catalog.py), instead of relying on hand-maintained
-- lookup tables that went stale every time the game added content.

-- One operator can have more than one ID over the game's lifetime.
CREATE TABLE IF NOT EXISTS operator_game_ids (
    game_id     INTEGER PRIMARY KEY,
    operator_id INTEGER NOT NULL,
    source      TEXT NOT NULL DEFAULT 'seed',
    FOREIGN KEY (operator_id) REFERENCES operators(operator_id) ON DELETE CASCADE
);

-- One map routinely has several IDs: every rework ships under a new one.
CREATE TABLE IF NOT EXISTS map_game_ids (
    game_id INTEGER PRIMARY KEY,
    map_id  INTEGER NOT NULL,
    source  TEXT NOT NULL DEFAULT 'seed',
    FOREIGN KEY (map_id) REFERENCES maps(map_id) ON DELETE CASCADE
);

-- Bomb-site names seen on each map. Reworks keep their site names, which
-- is how a brand-new map ID gets recognized as a known map automatically.
CREATE TABLE IF NOT EXISTS map_sites (
    map_id INTEGER NOT NULL,
    site   TEXT NOT NULL COLLATE NOCASE,
    UNIQUE(map_id, site),
    FOREIGN KEY (map_id) REFERENCES maps(map_id) ON DELETE CASCADE
);

-- Map IDs the catalog could not identify, waiting for a human to name
-- them once. Matches played on them record map_game_id and are backfilled
-- the moment the map is named.
CREATE TABLE IF NOT EXISTS unknown_maps (
    game_id      INTEGER PRIMARY KEY,
    dissect_name TEXT,
    sites        TEXT NOT NULL DEFAULT '',
    first_seen   TEXT NOT NULL,
    times_seen   INTEGER NOT NULL DEFAULT 1
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