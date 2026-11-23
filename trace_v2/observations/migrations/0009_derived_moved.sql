-- Moments, descriptions and Activities moved to derived.sqlite3 (see derived_migrations). The rows were copied there
-- when the store opened, before this migration ran; the tables here are dropped so forget cannot miss them.
DROP TABLE IF EXISTS activity_member_index;
DROP TABLE IF EXISTS activity_revisions;
DROP TABLE IF EXISTS activities;
DROP TABLE IF EXISTS activity_verdicts;
DROP TABLE IF EXISTS activity_interpretations;
DROP TABLE IF EXISTS moment_descriptions;
DROP TABLE IF EXISTS moment_member_index;
DROP TABLE IF EXISTS moment_revisions;
DROP TABLE IF EXISTS moments;
DROP TABLE IF EXISTS moment_skips;
DROP TABLE IF EXISTS observation_features;
