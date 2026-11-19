-- Per-artifact metadata (format, dimensions, tag ids...) as JSON.
ALTER TABLE artifact ADD COLUMN metadata_json TEXT NOT NULL DEFAULT '{}';
-- One logical path per extract operation; re-running extract creates new rows under a new op.
CREATE INDEX artifact_logical_path ON artifact (logical_path);
