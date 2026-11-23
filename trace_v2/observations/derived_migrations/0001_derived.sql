-- Everything derived from Observations lives in this file, with its own connection and lock, so a slow derived write
-- never blocks capture. Observation ids are plain text here: the evidence file is the only place they must exist.
CREATE TABLE deletion_grants(
    observation_id TEXT PRIMARY KEY,
    request_id     TEXT NOT NULL
);

CREATE TABLE observation_features(
    observation_id      TEXT NOT NULL,
    algorithm           TEXT NOT NULL,
    features_json       TEXT NOT NULL,
    ocr_extraction_id   TEXT,
    structure_extraction_id TEXT,
    recorded_at         TEXT NOT NULL,
    PRIMARY KEY (observation_id, algorithm)
);

CREATE TABLE moment_skips(
    observation_id  TEXT NOT NULL,
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
    observation_id  TEXT NOT NULL,
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

CREATE TABLE activity_interpretations(
    moment_id       TEXT NOT NULL REFERENCES moments(moment_id),
    model           TEXT NOT NULL,
    prompt_version  TEXT NOT NULL,
    status          TEXT NOT NULL CHECK (status IN ('clear', 'unclear', 'failed')),
    action          TEXT NOT NULL,
    purpose         TEXT NOT NULL,
    why             TEXT NOT NULL,
    vector          BLOB,
    recorded_at     TEXT NOT NULL,
    PRIMARY KEY (moment_id, model, prompt_version)
);

CREATE TABLE activity_verdicts(
    moment_a        TEXT NOT NULL REFERENCES moments(moment_id),
    moment_b        TEXT NOT NULL REFERENCES moments(moment_id),
    model           TEXT NOT NULL,
    prompt_version  TEXT NOT NULL,
    verdict         TEXT NOT NULL CHECK (verdict IN ('SAME', 'DIFFERENT', 'UNKNOWN')),
    reason          TEXT NOT NULL,
    recorded_at     TEXT NOT NULL,
    PRIMARY KEY (moment_a, moment_b, model, prompt_version),
    CHECK (moment_a < moment_b)
);

CREATE TABLE activities(
    activity_id    TEXT PRIMARY KEY CHECK (length(activity_id) = 36),
    algorithm      TEXT NOT NULL,
    created_at_ns  INTEGER NOT NULL
);

CREATE TABLE activity_revisions(
    activity_id    TEXT NOT NULL REFERENCES activities(activity_id),
    revision       INTEGER NOT NULL CHECK (revision >= 1),
    state          TEXT NOT NULL CHECK (state IN ('forming', 'active', 'archived')),
    start_ns       INTEGER NOT NULL,
    end_ns         INTEGER NOT NULL,
    moment_count   INTEGER NOT NULL CHECK (moment_count >= 1),
    added_json     TEXT NOT NULL,
    evidence_json  TEXT NOT NULL,
    params_json    TEXT NOT NULL,
    recorded_at    TEXT NOT NULL,
    PRIMARY KEY (activity_id, revision)
);

CREATE TABLE activity_member_index(
    activity_id  TEXT NOT NULL,
    moment_id    TEXT NOT NULL REFERENCES moments(moment_id),
    PRIMARY KEY (activity_id, moment_id)
) WITHOUT ROWID;
CREATE INDEX activity_member_moment ON activity_member_index(moment_id);
CREATE INDEX activity_revisions_state ON activity_revisions(state, end_ns);
CREATE INDEX activity_interpretations_status ON activity_interpretations(status);

CREATE TRIGGER activity_interpretations_immutable_u BEFORE UPDATE ON activity_interpretations BEGIN SELECT RAISE(ABORT, 'immutable: activity_interpretations'); END;
CREATE TRIGGER activity_interpretations_immutable_d BEFORE DELETE ON activity_interpretations
WHEN NOT EXISTS (SELECT 1 FROM moment_member_index i JOIN deletion_grants g ON g.observation_id = i.observation_id WHERE i.moment_id = OLD.moment_id)
BEGIN SELECT RAISE(ABORT, 'immutable: activity_interpretations'); END;

CREATE TRIGGER activity_verdicts_immutable_u BEFORE UPDATE ON activity_verdicts BEGIN SELECT RAISE(ABORT, 'immutable: activity_verdicts'); END;
CREATE TRIGGER activity_verdicts_immutable_d BEFORE DELETE ON activity_verdicts
WHEN NOT EXISTS (SELECT 1 FROM moment_member_index i JOIN deletion_grants g ON g.observation_id = i.observation_id WHERE i.moment_id IN (OLD.moment_a, OLD.moment_b))
BEGIN SELECT RAISE(ABORT, 'immutable: activity_verdicts'); END;

CREATE TRIGGER activities_immutable_u BEFORE UPDATE ON activities BEGIN SELECT RAISE(ABORT, 'immutable: activities'); END;
CREATE TRIGGER activities_immutable_d BEFORE DELETE ON activities
WHEN NOT EXISTS (SELECT 1 FROM activity_member_index a JOIN moment_member_index i ON i.moment_id = a.moment_id JOIN deletion_grants g ON g.observation_id = i.observation_id WHERE a.activity_id = OLD.activity_id)
BEGIN SELECT RAISE(ABORT, 'immutable: activities'); END;

CREATE TRIGGER activity_revisions_immutable_u BEFORE UPDATE ON activity_revisions BEGIN SELECT RAISE(ABORT, 'immutable: activity_revisions'); END;
CREATE TRIGGER activity_revisions_immutable_d BEFORE DELETE ON activity_revisions
WHEN NOT EXISTS (SELECT 1 FROM activity_member_index a JOIN moment_member_index i ON i.moment_id = a.moment_id JOIN deletion_grants g ON g.observation_id = i.observation_id WHERE a.activity_id = OLD.activity_id)
BEGIN SELECT RAISE(ABORT, 'immutable: activity_revisions'); END;
CREATE TRIGGER activity_revisions_chain BEFORE INSERT ON activity_revisions WHEN NEW.revision > 1
BEGIN
    SELECT RAISE(ABORT, 'activity revision must follow the previous revision')
    WHERE NOT EXISTS (SELECT 1 FROM activity_revisions p WHERE p.activity_id = NEW.activity_id AND p.revision = NEW.revision - 1);
END;
CREATE TRIGGER activity_revisions_terminal BEFORE INSERT ON activity_revisions
BEGIN
    SELECT RAISE(ABORT, 'activity is archived')
    WHERE EXISTS (SELECT 1 FROM activity_revisions p WHERE p.activity_id = NEW.activity_id AND p.revision = NEW.revision - 1 AND p.state = 'archived');
END;

CREATE TRIGGER activity_member_index_immutable_u BEFORE UPDATE ON activity_member_index BEGIN SELECT RAISE(ABORT, 'immutable: activity_member_index'); END;
CREATE TRIGGER activity_member_index_immutable_d BEFORE DELETE ON activity_member_index
WHEN EXISTS (SELECT 1 FROM activities a WHERE a.activity_id = OLD.activity_id)
 AND NOT EXISTS (SELECT 1 FROM moment_member_index i JOIN deletion_grants g ON g.observation_id = i.observation_id WHERE i.moment_id = OLD.moment_id)
BEGIN SELECT RAISE(ABORT, 'immutable: activity_member_index'); END;

CREATE TABLE moment_descriptions(
    moment_id         TEXT NOT NULL REFERENCES moments(moment_id),
    model             TEXT NOT NULL,
    prompt_version    TEXT NOT NULL,
    status            TEXT NOT NULL CHECK (status IN ('ok', 'insufficient', 'failed')),
    description       TEXT,
    confidence        REAL NOT NULL,
    alternative       TEXT,
    evidence_json     TEXT NOT NULL,
    observation_ids   TEXT NOT NULL,
    images_used       INTEGER NOT NULL,
    seconds           REAL,
    errors_json       TEXT NOT NULL,
    recorded_at       TEXT NOT NULL,
    PRIMARY KEY (moment_id, model, prompt_version),
    CHECK ((status = 'ok') = (description IS NOT NULL))
);
CREATE INDEX moment_descriptions_status ON moment_descriptions(status);

CREATE TRIGGER moment_descriptions_immutable_u BEFORE UPDATE ON moment_descriptions BEGIN SELECT RAISE(ABORT, 'immutable: moment_descriptions'); END;
CREATE TRIGGER moment_descriptions_immutable_d BEFORE DELETE ON moment_descriptions
WHEN NOT EXISTS (SELECT 1 FROM moment_member_index i JOIN deletion_grants g ON g.observation_id = i.observation_id WHERE i.moment_id = OLD.moment_id)
BEGIN SELECT RAISE(ABORT, 'immutable: moment_descriptions'); END;
