-- Trace V2, migration 0001: the Observation evidence store.
-- Evidence tables are append-only (enforced by triggers below). Mutable configuration lives only in `settings`.
-- Times are UTC nanoseconds since the epoch (*_ns) plus an ISO-8601 copy where people read them.

CREATE TABLE observations(
    observation_id        TEXT PRIMARY KEY CHECK (length(observation_id) = 36),
    captured_at_ns        INTEGER NOT NULL,
    captured_at           TEXT NOT NULL,
    capture_key           TEXT NOT NULL UNIQUE,      -- idempotency key from the capture source
    content_sha256        TEXT NOT NULL,             -- canonical hash of the submitted capture
    app_bundle_id         TEXT,
    app_name              TEXT,
    app_pid               INTEGER,
    window_title          TEXT,
    display_id            TEXT,
    context_json          TEXT NOT NULL,             -- {application, window, display}
    events_window_start_ns INTEGER,                  -- interaction events attributed to this observation
    events_window_end_ns   INTEGER,                  --   happened in (start, end]
    capture_status_json   TEXT NOT NULL,             -- context status and interaction-event counts
    privacy_json          TEXT NOT NULL,             -- {withheld, reason, redactions{kind:n}, policy_version}
    capture_version       TEXT NOT NULL,
    recorded_at           TEXT NOT NULL
);
CREATE INDEX observations_time ON observations(captured_at_ns);
CREATE INDEX observations_app_time ON observations(app_bundle_id, captured_at_ns);

CREATE TABLE screenshots(
    observation_id  TEXT PRIMARY KEY REFERENCES observations(observation_id),
    status          TEXT NOT NULL CHECK (status IN ('stored', 'failed', 'withheld')),
    relative_path   TEXT UNIQUE,
    format          TEXT CHECK (format IN ('png', 'jpeg', 'heic', 'tiff')),
    width           INTEGER CHECK (width > 0),
    height          INTEGER CHECK (height > 0),
    byte_size       INTEGER CHECK (byte_size > 0),
    sha256          TEXT,
    captured_at_ns  INTEGER NOT NULL,
    error_code      TEXT,
    error_message   TEXT,
    CHECK ((status = 'stored') = (relative_path IS NOT NULL AND sha256 IS NOT NULL))
);

-- One row per reader run. revision 1 is written with the observation; a re-run appends revision n+1.
CREATE TABLE extractions(
    extraction_id   TEXT PRIMARY KEY CHECK (length(extraction_id) = 36),
    observation_id  TEXT NOT NULL REFERENCES observations(observation_id),
    reader          TEXT NOT NULL CHECK (reader IN ('ocr', 'accessibility')),
    revision        INTEGER NOT NULL CHECK (revision >= 1),
    supersedes      TEXT REFERENCES extractions(extraction_id),
    reader_version  TEXT NOT NULL,
    status          TEXT NOT NULL CHECK (status IN ('succeeded', 'failed', 'unavailable', 'skipped', 'withheld')),
    error_code      TEXT,
    error_message   TEXT,
    started_at_ns   INTEGER,
    finished_at_ns  INTEGER,
    text            TEXT,                            -- plain extracted text, for retrieval
    raw_encoding    TEXT CHECK (raw_encoding IN ('json+zlib')),
    raw             BLOB,                            -- the reader's complete output (positions, hierarchy)
    raw_sha256      TEXT,
    raw_bytes       INTEGER,
    recorded_at     TEXT NOT NULL,
    UNIQUE (observation_id, reader, revision),
    CHECK ((revision = 1) = (supersedes IS NULL)),
    CHECK ((raw IS NULL) = (raw_sha256 IS NULL))
);
CREATE INDEX extractions_observation ON extractions(observation_id, reader, revision);

CREATE TABLE interaction_events(
    observation_id  TEXT NOT NULL REFERENCES observations(observation_id),
    seq             INTEGER NOT NULL CHECK (seq >= 0),   -- arrival order, never re-sorted
    event_id        TEXT NOT NULL UNIQUE CHECK (length(event_id) = 36),
    occurred_at_ns  INTEGER NOT NULL,
    type            TEXT NOT NULL CHECK (type IN ('cursor_move', 'click', 'keypress', 'scroll')),
    payload_json    TEXT NOT NULL,
    PRIMARY KEY (observation_id, seq)
) WITHOUT ROWID;
CREATE INDEX interaction_events_time ON interaction_events(occurred_at_ns);

-- Retention audit. A run row is written when it starts and a finish row when it ends,
-- so an interrupted run is visible as a start with no finish.
CREATE TABLE retention_runs(
    run_id       TEXT PRIMARY KEY CHECK (length(run_id) = 36),
    started_at   TEXT NOT NULL,
    now_ns       INTEGER NOT NULL,
    policy_json  TEXT NOT NULL,
    cutoff_ns    INTEGER,                             -- NULL when the policy is permanent
    dry_run      INTEGER NOT NULL CHECK (dry_run IN (0, 1))
);
CREATE TABLE retention_run_finishes(
    run_id         TEXT PRIMARY KEY REFERENCES retention_runs(run_id),
    finished_at    TEXT NOT NULL,
    examined       INTEGER NOT NULL,
    deleted        INTEGER NOT NULL,
    already_missing INTEGER NOT NULL,
    failed         INTEGER NOT NULL,
    bytes_freed    INTEGER NOT NULL
);
-- A screenshot file removed from disk. The screenshots row itself never changes.
CREATE TABLE screenshot_deletions(
    observation_id  TEXT PRIMARY KEY REFERENCES screenshots(observation_id),
    deleted_at      TEXT NOT NULL,
    reason          TEXT NOT NULL CHECK (reason IN ('retention')),
    run_id          TEXT NOT NULL REFERENCES retention_runs(run_id),
    file_existed    INTEGER NOT NULL CHECK (file_existed IN (0, 1))
);

-- Mutable configuration, kept apart from evidence; every change is also appended to settings_history.
CREATE TABLE settings(
    key         TEXT PRIMARY KEY,
    value_json  TEXT NOT NULL,
    updated_at  TEXT NOT NULL
);
CREATE TABLE settings_history(
    change_id   TEXT PRIMARY KEY CHECK (length(change_id) = 36),
    key         TEXT NOT NULL,
    value_json  TEXT NOT NULL,
    changed_at  TEXT NOT NULL
);

-- User-controlled forgetting. The only way evidence rows can be deleted: a forget request grants deletion of
-- named observations inside one transaction; the DELETE triggers below refuse any row without a grant.
CREATE TABLE forget_requests(
    request_id     TEXT PRIMARY KEY CHECK (length(request_id) = 36),
    requested_at   TEXT NOT NULL,
    requested_by   TEXT NOT NULL CHECK (requested_by IN ('user')),
    selector_json  TEXT NOT NULL,                    -- what was asked for (time range, app, ids, or everything)
    matched        INTEGER NOT NULL
);
CREATE TABLE deletion_grants(
    observation_id TEXT PRIMARY KEY,
    request_id     TEXT NOT NULL REFERENCES forget_requests(request_id)
);
-- Proof that an observation existed and was forgotten on request; holds no evidence.
CREATE TABLE forget_tombstones(
    observation_id  TEXT PRIMARY KEY,
    request_id      TEXT NOT NULL REFERENCES forget_requests(request_id),
    screenshot_path TEXT                              -- file still to remove, until forget_finishes says done
);
CREATE TABLE forget_finishes(
    request_id     TEXT PRIMARY KEY REFERENCES forget_requests(request_id),
    finished_at    TEXT NOT NULL,
    files_removed  INTEGER NOT NULL,
    files_missing  INTEGER NOT NULL
);

CREATE TRIGGER observations_immutable_u BEFORE UPDATE ON observations BEGIN SELECT RAISE(ABORT, 'immutable: observations'); END;
CREATE TRIGGER observations_immutable_d BEFORE DELETE ON observations WHEN NOT EXISTS (SELECT 1 FROM deletion_grants g WHERE g.observation_id = OLD.observation_id) BEGIN SELECT RAISE(ABORT, 'immutable: observations'); END;
CREATE TRIGGER screenshots_immutable_u BEFORE UPDATE ON screenshots BEGIN SELECT RAISE(ABORT, 'immutable: screenshots'); END;
CREATE TRIGGER screenshots_immutable_d BEFORE DELETE ON screenshots WHEN NOT EXISTS (SELECT 1 FROM deletion_grants g WHERE g.observation_id = OLD.observation_id) BEGIN SELECT RAISE(ABORT, 'immutable: screenshots'); END;
CREATE TRIGGER extractions_immutable_u BEFORE UPDATE ON extractions BEGIN SELECT RAISE(ABORT, 'immutable: extractions'); END;
CREATE TRIGGER extractions_immutable_d BEFORE DELETE ON extractions WHEN NOT EXISTS (SELECT 1 FROM deletion_grants g WHERE g.observation_id = OLD.observation_id) BEGIN SELECT RAISE(ABORT, 'immutable: extractions'); END;
CREATE TRIGGER events_immutable_u BEFORE UPDATE ON interaction_events BEGIN SELECT RAISE(ABORT, 'immutable: interaction_events'); END;
CREATE TRIGGER events_immutable_d BEFORE DELETE ON interaction_events WHEN NOT EXISTS (SELECT 1 FROM deletion_grants g WHERE g.observation_id = OLD.observation_id) BEGIN SELECT RAISE(ABORT, 'immutable: interaction_events'); END;
CREATE TRIGGER deletions_immutable_u BEFORE UPDATE ON screenshot_deletions BEGIN SELECT RAISE(ABORT, 'immutable: screenshot_deletions'); END;
CREATE TRIGGER deletions_immutable_d BEFORE DELETE ON screenshot_deletions WHEN NOT EXISTS (SELECT 1 FROM deletion_grants g WHERE g.observation_id = OLD.observation_id) BEGIN SELECT RAISE(ABORT, 'immutable: screenshot_deletions'); END;
CREATE TRIGGER runs_immutable_u BEFORE UPDATE ON retention_runs BEGIN SELECT RAISE(ABORT, 'immutable: retention_runs'); END;
CREATE TRIGGER runs_immutable_d BEFORE DELETE ON retention_runs BEGIN SELECT RAISE(ABORT, 'immutable: retention_runs'); END;
CREATE TRIGGER finishes_immutable_u BEFORE UPDATE ON retention_run_finishes BEGIN SELECT RAISE(ABORT, 'immutable: retention_run_finishes'); END;
CREATE TRIGGER finishes_immutable_d BEFORE DELETE ON retention_run_finishes BEGIN SELECT RAISE(ABORT, 'immutable: retention_run_finishes'); END;
CREATE TRIGGER history_immutable_u BEFORE UPDATE ON settings_history BEGIN SELECT RAISE(ABORT, 'immutable: settings_history'); END;
CREATE TRIGGER history_immutable_d BEFORE DELETE ON settings_history BEGIN SELECT RAISE(ABORT, 'immutable: settings_history'); END;
CREATE TRIGGER requests_immutable_u BEFORE UPDATE ON forget_requests BEGIN SELECT RAISE(ABORT, 'immutable: forget_requests'); END;
CREATE TRIGGER requests_immutable_d BEFORE DELETE ON forget_requests BEGIN SELECT RAISE(ABORT, 'immutable: forget_requests'); END;
CREATE TRIGGER tombstones_immutable_u BEFORE UPDATE ON forget_tombstones BEGIN SELECT RAISE(ABORT, 'immutable: forget_tombstones'); END;
CREATE TRIGGER tombstones_immutable_d BEFORE DELETE ON forget_tombstones BEGIN SELECT RAISE(ABORT, 'immutable: forget_tombstones'); END;
CREATE TRIGGER forget_finishes_immutable_u BEFORE UPDATE ON forget_finishes BEGIN SELECT RAISE(ABORT, 'immutable: forget_finishes'); END;
CREATE TRIGGER forget_finishes_immutable_d BEFORE DELETE ON forget_finishes BEGIN SELECT RAISE(ABORT, 'immutable: forget_finishes'); END;
-- A forgotten observation id can never be reused.
CREATE TRIGGER observations_not_forgotten BEFORE INSERT ON observations WHEN EXISTS (SELECT 1 FROM forget_tombstones t WHERE t.observation_id = NEW.observation_id) BEGIN SELECT RAISE(ABORT, 'observation id was forgotten'); END;
-- A re-run must point at the newest earlier revision of the same reader for the same observation.
CREATE TRIGGER extractions_revision_chain BEFORE INSERT ON extractions WHEN NEW.revision > 1
BEGIN
    SELECT RAISE(ABORT, 'extraction revision must supersede the previous revision')
    WHERE NOT EXISTS (SELECT 1 FROM extractions p WHERE p.extraction_id = NEW.supersedes
                      AND p.observation_id = NEW.observation_id AND p.reader = NEW.reader
                      AND p.revision = NEW.revision - 1);
END;
