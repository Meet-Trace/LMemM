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
