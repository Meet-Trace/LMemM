-- Raw reader output (OCR positions, accessibility tree, layout) is bulky and is only read while an observation is being
-- processed. After a retention period it is emptied; the extraction row, its status and its plain text stay. Each
-- emptied row is recorded in raw_trims first, and the update trigger allows exactly that change and nothing else.
CREATE TABLE raw_trims(
    extraction_id TEXT PRIMARY KEY,
    trimmed_at    TEXT NOT NULL,
    raw_bytes     INTEGER,
    raw_sha256    TEXT
);
DROP TRIGGER extractions_immutable_u;
CREATE TRIGGER extractions_immutable_u BEFORE UPDATE ON extractions
WHEN NOT (
    EXISTS (SELECT 1 FROM raw_trims t WHERE t.extraction_id = OLD.extraction_id)
    AND NEW.raw IS NULL AND NEW.raw_encoding IS NULL AND NEW.raw_sha256 IS NULL AND NEW.raw_bytes IS NULL
    AND NEW.extraction_id IS OLD.extraction_id AND NEW.observation_id IS OLD.observation_id AND NEW.reader IS OLD.reader
    AND NEW.revision IS OLD.revision AND NEW.supersedes IS OLD.supersedes AND NEW.reader_version IS OLD.reader_version
    AND NEW.status IS OLD.status AND NEW.error_code IS OLD.error_code AND NEW.error_message IS OLD.error_message
    AND NEW.started_at_ns IS OLD.started_at_ns AND NEW.finished_at_ns IS OLD.finished_at_ns AND NEW.text IS OLD.text
    AND NEW.recorded_at IS OLD.recorded_at AND NEW.privacy_json IS OLD.privacy_json
    AND NEW.source_extraction_id IS OLD.source_extraction_id)
BEGIN SELECT RAISE(ABORT, 'immutable: extractions'); END;
CREATE TRIGGER raw_trims_immutable_u BEFORE UPDATE ON raw_trims BEGIN SELECT RAISE(ABORT, 'immutable: raw_trims'); END;
CREATE TRIGGER raw_trims_immutable_d BEFORE DELETE ON raw_trims
WHEN NOT EXISTS (SELECT 1 FROM deletion_grants g JOIN extractions e ON e.observation_id = g.observation_id
                 WHERE e.extraction_id = OLD.extraction_id)
BEGIN SELECT RAISE(ABORT, 'immutable: raw_trims'); END;
