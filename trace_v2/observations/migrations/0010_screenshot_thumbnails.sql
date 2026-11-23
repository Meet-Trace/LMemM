-- One small JPEG kept per stable Moment when its full screenshots are thinned out (see retention.py).
CREATE TABLE screenshot_thumbnails(
    observation_id TEXT PRIMARY KEY REFERENCES observations(observation_id),
    moment_id      TEXT NOT NULL,
    relative_path  TEXT NOT NULL,
    format         TEXT NOT NULL CHECK (format = 'jpeg'),
    width          INTEGER NOT NULL,
    height         INTEGER NOT NULL,
    byte_size      INTEGER NOT NULL,
    sha256         TEXT NOT NULL,
    created_at     TEXT NOT NULL
);
CREATE UNIQUE INDEX screenshot_thumbnails_moment ON screenshot_thumbnails(moment_id);
ALTER TABLE forget_tombstones ADD COLUMN thumbnail_path TEXT;

CREATE TRIGGER thumbnails_immutable_u BEFORE UPDATE ON screenshot_thumbnails BEGIN SELECT RAISE(ABORT, 'immutable: screenshot_thumbnails'); END;
CREATE TRIGGER thumbnails_immutable_d BEFORE DELETE ON screenshot_thumbnails WHEN NOT EXISTS (SELECT 1 FROM deletion_grants g WHERE g.observation_id = OLD.observation_id) BEGIN SELECT RAISE(ABORT, 'immutable: screenshot_thumbnails'); END;
