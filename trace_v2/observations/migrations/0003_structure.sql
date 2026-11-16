DROP TRIGGER extractions_immutable_u;
DROP TRIGGER extractions_immutable_d;
DROP TRIGGER extractions_revision_chain;
DROP INDEX extractions_observation;
ALTER TABLE extractions RENAME TO extractions_old;

CREATE TABLE extractions(
    extraction_id         TEXT PRIMARY KEY CHECK (length(extraction_id) = 36),
    observation_id        TEXT NOT NULL REFERENCES observations(observation_id),
    reader                TEXT NOT NULL CHECK (reader IN ('ocr', 'accessibility', 'structure')),
    revision              INTEGER NOT NULL CHECK (revision >= 1),
    supersedes            TEXT REFERENCES extractions(extraction_id),
    reader_version        TEXT NOT NULL,
    status                TEXT NOT NULL CHECK (status IN ('pending', 'succeeded', 'failed', 'unavailable', 'skipped', 'withheld')),
    error_code            TEXT,
    error_message         TEXT,
    started_at_ns         INTEGER,
    finished_at_ns        INTEGER,
    text                  TEXT,
    raw_encoding          TEXT CHECK (raw_encoding IN ('json+zlib')),
    raw                   BLOB,
    raw_sha256            TEXT,
    raw_bytes             INTEGER,
    recorded_at           TEXT NOT NULL,
    privacy_json          TEXT,
    source_extraction_id  TEXT REFERENCES extractions(extraction_id),
    UNIQUE (observation_id, reader, revision),
    CHECK ((revision = 1) = (supersedes IS NULL)),
    CHECK ((raw IS NULL) = (raw_sha256 IS NULL)),
    CHECK (status != 'pending' OR (raw IS NULL AND text IS NULL)),
    CHECK (source_extraction_id IS NULL OR reader = 'structure')
);
INSERT INTO extractions(extraction_id, observation_id, reader, revision, supersedes, reader_version, status, error_code,
                        error_message, started_at_ns, finished_at_ns, text, raw_encoding, raw, raw_sha256, raw_bytes,
                        recorded_at, privacy_json)
    SELECT extraction_id, observation_id, reader, revision, supersedes, reader_version, status, error_code,
           error_message, started_at_ns, finished_at_ns, text, raw_encoding, raw, raw_sha256, raw_bytes,
           recorded_at, privacy_json
    FROM extractions_old ORDER BY observation_id, reader, revision;
DROP TABLE extractions_old;
CREATE INDEX extractions_observation ON extractions(observation_id, reader, revision);

CREATE TRIGGER extractions_immutable_u BEFORE UPDATE ON extractions BEGIN SELECT RAISE(ABORT, 'immutable: extractions'); END;
CREATE TRIGGER extractions_immutable_d BEFORE DELETE ON extractions WHEN NOT EXISTS (SELECT 1 FROM deletion_grants g WHERE g.observation_id = OLD.observation_id) BEGIN SELECT RAISE(ABORT, 'immutable: extractions'); END;
CREATE TRIGGER extractions_revision_chain BEFORE INSERT ON extractions WHEN NEW.revision > 1
BEGIN
    SELECT RAISE(ABORT, 'extraction revision must supersede the previous revision')
    WHERE NOT EXISTS (SELECT 1 FROM extractions p WHERE p.extraction_id = NEW.supersedes
                      AND p.observation_id = NEW.observation_id AND p.reader = NEW.reader
                      AND p.revision = NEW.revision - 1);
END;
CREATE TRIGGER extractions_structure_source BEFORE INSERT ON extractions WHEN NEW.source_extraction_id IS NOT NULL
BEGIN
    SELECT RAISE(ABORT, 'structure source must be an ocr extraction of the same observation')
    WHERE NOT EXISTS (SELECT 1 FROM extractions p WHERE p.extraction_id = NEW.source_extraction_id
                      AND p.observation_id = NEW.observation_id AND p.reader = 'ocr');
END;
