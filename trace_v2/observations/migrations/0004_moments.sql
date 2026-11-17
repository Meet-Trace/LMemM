CREATE TABLE observation_features(
    observation_id      TEXT NOT NULL REFERENCES observations(observation_id),
    algorithm           TEXT NOT NULL,
    features_json       TEXT NOT NULL,
    ocr_extraction_id   TEXT,
    structure_extraction_id TEXT,
    recorded_at         TEXT NOT NULL,
    PRIMARY KEY (observation_id, algorithm)
);

CREATE TABLE moment_skips(
    observation_id  TEXT NOT NULL REFERENCES observations(observation_id),
    algorithm       TEXT NOT NULL,
    reason          TEXT NOT NULL,
    recorded_at     TEXT NOT NULL,
    PRIMARY KEY (observation_id, algorithm)
);

CREATE TABLE moments(
    moment_id         TEXT PRIMARY KEY CHECK (length(moment_id) = 36),
    algorithm         TEXT NOT NULL,
    origin            TEXT NOT NULL CHECK (origin IN ('formed', 'split')),
    parent_moment_id  TEXT,
    created_at_ns     INTEGER NOT NULL
);

CREATE TABLE moment_revisions(
    moment_id          TEXT NOT NULL REFERENCES moments(moment_id),
    revision           INTEGER NOT NULL CHECK (revision >= 1),
    state              TEXT NOT NULL CHECK (state IN ('provisional', 'stable', 'insufficient', 'merged')),
    merged_into        TEXT,
    start_ns           INTEGER NOT NULL,
    end_ns             INTEGER NOT NULL,
    observation_count  INTEGER NOT NULL CHECK (observation_count >= 0),
    params_json        TEXT NOT NULL,
    summary_json       TEXT NOT NULL,
    members_encoding   TEXT NOT NULL CHECK (members_encoding IN ('json+zlib')),
    members            BLOB NOT NULL,
    members_sha256     TEXT NOT NULL,
    recorded_at        TEXT NOT NULL,
    PRIMARY KEY (moment_id, revision),
    CHECK ((state = 'merged') = (merged_into IS NOT NULL))
);

CREATE TABLE moment_member_index(
    moment_id       TEXT NOT NULL,
    observation_id  TEXT NOT NULL REFERENCES observations(observation_id),
    PRIMARY KEY (moment_id, observation_id)
) WITHOUT ROWID;
CREATE INDEX moment_member_observation ON moment_member_index(observation_id);
CREATE INDEX moment_revisions_state ON moment_revisions(state, end_ns);

CREATE TRIGGER features_immutable_u BEFORE UPDATE ON observation_features BEGIN SELECT RAISE(ABORT, 'immutable: observation_features'); END;
CREATE TRIGGER features_immutable_d BEFORE DELETE ON observation_features WHEN NOT EXISTS (SELECT 1 FROM deletion_grants g WHERE g.observation_id = OLD.observation_id) AND EXISTS (SELECT 1 FROM moment_member_index i JOIN moments m ON m.moment_id = i.moment_id WHERE i.observation_id = OLD.observation_id) BEGIN SELECT RAISE(ABORT, 'immutable: observation_features'); END;
CREATE TRIGGER skips_immutable_u BEFORE UPDATE ON moment_skips BEGIN SELECT RAISE(ABORT, 'immutable: moment_skips'); END;
CREATE TRIGGER skips_immutable_d BEFORE DELETE ON moment_skips WHEN NOT EXISTS (SELECT 1 FROM deletion_grants g WHERE g.observation_id = OLD.observation_id) BEGIN SELECT RAISE(ABORT, 'immutable: moment_skips'); END;
CREATE TRIGGER moments_immutable_u BEFORE UPDATE ON moments BEGIN SELECT RAISE(ABORT, 'immutable: moments'); END;
CREATE TRIGGER moments_immutable_d BEFORE DELETE ON moments WHEN NOT EXISTS (SELECT 1 FROM moment_member_index i JOIN deletion_grants g ON g.observation_id = i.observation_id WHERE i.moment_id = OLD.moment_id) BEGIN SELECT RAISE(ABORT, 'immutable: moments'); END;
CREATE TRIGGER moment_revisions_immutable_u BEFORE UPDATE ON moment_revisions BEGIN SELECT RAISE(ABORT, 'immutable: moment_revisions'); END;
CREATE TRIGGER moment_revisions_immutable_d BEFORE DELETE ON moment_revisions WHEN NOT EXISTS (SELECT 1 FROM moment_member_index i JOIN deletion_grants g ON g.observation_id = i.observation_id WHERE i.moment_id = OLD.moment_id) BEGIN SELECT RAISE(ABORT, 'immutable: moment_revisions'); END;
CREATE TRIGGER moment_revisions_chain BEFORE INSERT ON moment_revisions WHEN NEW.revision > 1
BEGIN
    SELECT RAISE(ABORT, 'moment revision must follow the previous revision')
    WHERE NOT EXISTS (SELECT 1 FROM moment_revisions p WHERE p.moment_id = NEW.moment_id AND p.revision = NEW.revision - 1);
END;
CREATE TRIGGER moment_revisions_terminal BEFORE INSERT ON moment_revisions
BEGIN
    SELECT RAISE(ABORT, 'moment is closed')
    WHERE EXISTS (SELECT 1 FROM moment_revisions p WHERE p.moment_id = NEW.moment_id AND p.revision = NEW.revision - 1
                  AND p.state IN ('stable', 'insufficient', 'merged'));
END;
CREATE TRIGGER member_index_immutable_u BEFORE UPDATE ON moment_member_index BEGIN SELECT RAISE(ABORT, 'immutable: moment_member_index'); END;
CREATE TRIGGER member_index_immutable_d BEFORE DELETE ON moment_member_index
WHEN NOT EXISTS (SELECT 1 FROM deletion_grants g WHERE g.observation_id = OLD.observation_id)
 AND EXISTS (SELECT 1 FROM moments m WHERE m.moment_id = OLD.moment_id)
BEGIN SELECT RAISE(ABORT, 'immutable: moment_member_index'); END;
