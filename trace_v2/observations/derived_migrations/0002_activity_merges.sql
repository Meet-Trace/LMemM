CREATE TABLE activity_revisions_new(
    activity_id    TEXT NOT NULL REFERENCES activities(activity_id),
    revision       INTEGER NOT NULL CHECK (revision >= 1),
    state          TEXT NOT NULL CHECK (state IN ('forming', 'active', 'archived', 'merged')),
    start_ns       INTEGER NOT NULL,
    end_ns         INTEGER NOT NULL,
    moment_count   INTEGER NOT NULL CHECK (moment_count >= 1),
    added_json     TEXT NOT NULL,
    evidence_json  TEXT NOT NULL,
    params_json    TEXT NOT NULL,
    recorded_at    TEXT NOT NULL,
    merged_into    TEXT,
    PRIMARY KEY (activity_id, revision),
    CHECK ((state = 'merged') = (merged_into IS NOT NULL))
);
INSERT INTO activity_revisions_new SELECT activity_id, revision, state, start_ns, end_ns, moment_count, added_json, evidence_json, params_json, recorded_at, NULL FROM activity_revisions ORDER BY activity_id, revision;
DROP TABLE activity_revisions;
ALTER TABLE activity_revisions_new RENAME TO activity_revisions;
CREATE INDEX activity_revisions_state ON activity_revisions(state, end_ns);

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
    SELECT RAISE(ABORT, 'activity is closed')
    WHERE EXISTS (SELECT 1 FROM activity_revisions p WHERE p.activity_id = NEW.activity_id AND p.revision = NEW.revision - 1 AND p.state IN ('archived', 'merged'));
END;
