DROP TRIGGER extractions_immutable_u;
DROP TRIGGER extractions_immutable_d;
DROP TRIGGER extractions_revision_chain;
DROP TRIGGER deletions_immutable_u;
DROP TRIGGER deletions_immutable_d;
DROP INDEX extractions_observation;
ALTER TABLE extractions RENAME TO extractions_old;
ALTER TABLE screenshot_deletions RENAME TO screenshot_deletions_old;

CREATE TABLE extractions(
    extraction_id   TEXT PRIMARY KEY CHECK (length(extraction_id) = 36),
    observation_id  TEXT NOT NULL REFERENCES observations(observation_id),
    reader          TEXT NOT NULL CHECK (reader IN ('ocr', 'accessibility')),
    revision        INTEGER NOT NULL CHECK (revision >= 1),
    supersedes      TEXT REFERENCES extractions(extraction_id),
    reader_version  TEXT NOT NULL,
    status          TEXT NOT NULL CHECK (status IN ('pending', 'succeeded', 'failed', 'unavailable', 'skipped', 'withheld')),
    error_code      TEXT,
    error_message   TEXT,
    started_at_ns   INTEGER,
    finished_at_ns  INTEGER,
    text            TEXT,
    raw_encoding    TEXT CHECK (raw_encoding IN ('json+zlib')),
    raw             BLOB,
    raw_sha256      TEXT,
    raw_bytes       INTEGER,
    recorded_at     TEXT NOT NULL,
    privacy_json    TEXT,
    UNIQUE (observation_id, reader, revision),
    CHECK ((revision = 1) = (supersedes IS NULL)),
    CHECK ((raw IS NULL) = (raw_sha256 IS NULL)),
    CHECK (status != 'pending' OR (raw IS NULL AND text IS NULL))
);
INSERT INTO extractions(extraction_id, observation_id, reader, revision, supersedes, reader_version, status, error_code,
                        error_message, started_at_ns, finished_at_ns, text, raw_encoding, raw, raw_sha256, raw_bytes, recorded_at)
    SELECT extraction_id, observation_id, reader, revision, supersedes, reader_version, status, error_code,
           error_message, started_at_ns, finished_at_ns, text, raw_encoding, raw, raw_sha256, raw_bytes, recorded_at
    FROM extractions_old ORDER BY observation_id, reader, revision;
DROP TABLE extractions_old;
CREATE INDEX extractions_observation ON extractions(observation_id, reader, revision);

CREATE TABLE screenshot_deletions(
    observation_id  TEXT PRIMARY KEY REFERENCES screenshots(observation_id),
    deleted_at      TEXT NOT NULL,
    reason          TEXT NOT NULL CHECK (reason IN ('retention', 'secret_on_screen_late')),
    run_id          TEXT NOT NULL REFERENCES retention_runs(run_id),
    file_existed    INTEGER NOT NULL CHECK (file_existed IN (0, 1))
);
INSERT INTO screenshot_deletions SELECT observation_id, deleted_at, reason, run_id, file_existed FROM screenshot_deletions_old;
DROP TABLE screenshot_deletions_old;

CREATE TRIGGER extractions_immutable_u BEFORE UPDATE ON extractions BEGIN SELECT RAISE(ABORT, 'immutable: extractions'); END;
CREATE TRIGGER extractions_immutable_d BEFORE DELETE ON extractions WHEN NOT EXISTS (SELECT 1 FROM deletion_grants g WHERE g.observation_id = OLD.observation_id) BEGIN SELECT RAISE(ABORT, 'immutable: extractions'); END;
CREATE TRIGGER extractions_revision_chain BEFORE INSERT ON extractions WHEN NEW.revision > 1
BEGIN
    SELECT RAISE(ABORT, 'extraction revision must supersede the previous revision')
    WHERE NOT EXISTS (SELECT 1 FROM extractions p WHERE p.extraction_id = NEW.supersedes
                      AND p.observation_id = NEW.observation_id AND p.reader = NEW.reader
                      AND p.revision = NEW.revision - 1);
END;
CREATE TRIGGER deletions_immutable_u BEFORE UPDATE ON screenshot_deletions BEGIN SELECT RAISE(ABORT, 'immutable: screenshot_deletions'); END;
CREATE TRIGGER deletions_immutable_d BEFORE DELETE ON screenshot_deletions WHEN NOT EXISTS (SELECT 1 FROM deletion_grants g WHERE g.observation_id = OLD.observation_id) BEGIN SELECT RAISE(ABORT, 'immutable: screenshot_deletions'); END;

CREATE TABLE ocr_jobs(
    job_id           INTEGER PRIMARY KEY AUTOINCREMENT,
    observation_id   TEXT NOT NULL REFERENCES observations(observation_id),
    target_revision  INTEGER NOT NULL CHECK (target_revision >= 2),
    state            TEXT NOT NULL CHECK (state IN ('queued', 'processing', 'done', 'failed')),
    attempts         INTEGER NOT NULL DEFAULT 0,
    deferrals        INTEGER NOT NULL DEFAULT 0,
    max_attempts     INTEGER NOT NULL CHECK (max_attempts >= 1),
    claim_token      TEXT,
    claimed_at_ns    INTEGER,
    lease_expires_ns INTEGER,
    not_before_ns    INTEGER NOT NULL,
    enqueued_at_ns   INTEGER NOT NULL,
    finished_at_ns   INTEGER,
    last_error_code  TEXT,
    UNIQUE (observation_id, target_revision),
    CHECK ((state = 'processing') = (claim_token IS NOT NULL))
);
CREATE INDEX ocr_jobs_due ON ocr_jobs(state, not_before_ns, job_id);
