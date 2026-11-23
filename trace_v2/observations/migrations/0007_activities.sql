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
