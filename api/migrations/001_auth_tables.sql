-- Auth tables for the mkts-api Cloudflare Worker.
-- Run against the PRIMARY Turso database (where auth data lives).
--
-- Usage:
--   turso db shell <your-primary-db> < api/migrations/001_auth_tables.sql

-- API keys linked to Eve characters
CREATE TABLE IF NOT EXISTS api_keys (
    api_key     TEXT PRIMARY KEY,
    character_id INTEGER NOT NULL,
    character_name TEXT NOT NULL,
    created_at  TEXT NOT NULL DEFAULT (datetime('now')),
    active      INTEGER NOT NULL DEFAULT 1
);

-- Alliance/corporation standings list
-- entity_type: 'alliance' or 'corporation'
-- standing: integer -10 to 10
CREATE TABLE IF NOT EXISTS standings (
    entity_id   INTEGER PRIMARY KEY,
    entity_type TEXT NOT NULL CHECK (entity_type IN ('alliance', 'corporation')),
    entity_name TEXT NOT NULL,
    standing    INTEGER NOT NULL CHECK (standing BETWEEN -10 AND 10)
);

-- Cached character → corporation/alliance affiliations (refreshed by cron)
CREATE TABLE IF NOT EXISTS character_affiliations (
    character_id  INTEGER PRIMARY KEY,
    corporation_id INTEGER NOT NULL,
    alliance_id   INTEGER,
    last_checked  TEXT NOT NULL DEFAULT (datetime('now'))
);

-- Indexes
CREATE INDEX IF NOT EXISTS idx_api_keys_character ON api_keys(character_id);
CREATE INDEX IF NOT EXISTS idx_standings_type ON standings(entity_type);
