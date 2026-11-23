CREATE TABLE moment_identities(
    moment_id  TEXT NOT NULL REFERENCES moments(moment_id),
    kind       TEXT NOT NULL CHECK (kind IN ('url', 'document')),
    value      TEXT NOT NULL,
    PRIMARY KEY (moment_id, kind, value)
) WITHOUT ROWID;
CREATE INDEX moment_identities_value ON moment_identities(kind, value);

CREATE TRIGGER moment_identities_immutable_u BEFORE UPDATE ON moment_identities BEGIN SELECT RAISE(ABORT, 'immutable: moment_identities'); END;
CREATE TRIGGER moment_identities_immutable_d BEFORE DELETE ON moment_identities
WHEN NOT EXISTS (SELECT 1 FROM moment_member_index i JOIN deletion_grants g ON g.observation_id = i.observation_id WHERE i.moment_id = OLD.moment_id)
BEGIN SELECT RAISE(ABORT, 'immutable: moment_identities'); END;
