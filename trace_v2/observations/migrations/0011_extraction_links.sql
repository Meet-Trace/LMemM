-- An unchanged screen reuses the earlier frame's OCR. The new extraction row keeps its own text but not a second copy of
-- the raw output: it points at the extraction that holds it. When that extraction is forgotten, the raw output is first
-- copied to one of the pointing rows (extraction_raw_copies) and the others are repointed.
CREATE TABLE extraction_links(
    extraction_id        TEXT PRIMARY KEY,
    target_extraction_id TEXT NOT NULL
);
CREATE INDEX extraction_links_target ON extraction_links(target_extraction_id);
CREATE TABLE extraction_raw_copies(
    extraction_id TEXT PRIMARY KEY,
    raw_encoding  TEXT NOT NULL CHECK (raw_encoding IN ('json+zlib')),
    raw           BLOB NOT NULL
);
