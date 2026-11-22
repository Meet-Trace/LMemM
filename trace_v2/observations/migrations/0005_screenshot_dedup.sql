DROP TRIGGER deletions_immutable_u;
DROP TRIGGER deletions_immutable_d;
ALTER TABLE screenshot_deletions RENAME TO screenshot_deletions_old;

CREATE TABLE screenshot_deletions(
    observation_id  TEXT PRIMARY KEY REFERENCES screenshots(observation_id),
    deleted_at      TEXT NOT NULL,
    reason          TEXT NOT NULL CHECK (reason IN ('retention', 'secret_on_screen_late', 'superseded_typing')),
    run_id          TEXT NOT NULL REFERENCES retention_runs(run_id),
    file_existed    INTEGER NOT NULL CHECK (file_existed IN (0, 1))
);
INSERT INTO screenshot_deletions SELECT observation_id, deleted_at, reason, run_id, file_existed FROM screenshot_deletions_old;
DROP TABLE screenshot_deletions_old;

CREATE TRIGGER deletions_immutable_u BEFORE UPDATE ON screenshot_deletions BEGIN SELECT RAISE(ABORT, 'immutable: screenshot_deletions'); END;
CREATE TRIGGER deletions_immutable_d BEFORE DELETE ON screenshot_deletions WHEN NOT EXISTS (SELECT 1 FROM deletion_grants g WHERE g.observation_id = OLD.observation_id) BEGIN SELECT RAISE(ABORT, 'immutable: screenshot_deletions'); END;
