-- Originals: referenced by path, hashed, never copied or modified.
CREATE TABLE source (
    id          INTEGER PRIMARY KEY,
    path        TEXT    NOT NULL,
    sha256      TEXT    NOT NULL UNIQUE CHECK (length(sha256) = 64),
    size        INTEGER NOT NULL CHECK (size >= 0),
    mtime_ns    INTEGER NOT NULL,
    platform    TEXT,
    title       TEXT,
    title_id    TEXT,
    region      TEXT,
    adapter_id  TEXT,
    added_at    TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
);
CREATE INDEX source_path ON source (path);

-- Content-addressed, write-once files in <workspace>/store/ab/cd/<sha256>.
CREATE TABLE blob (
    sha256      TEXT    PRIMARY KEY CHECK (length(sha256) = 64),
    size        INTEGER NOT NULL CHECK (size >= 0),
    media_type  TEXT
);

-- Derived files. Originals live in `source`, so 'original' is not a kind here.
CREATE TABLE artifact (
    id           INTEGER PRIMARY KEY,
    blob_sha256  TEXT    NOT NULL REFERENCES blob (sha256),
    source_id    INTEGER REFERENCES source (id),
    kind         TEXT    NOT NULL CHECK (kind IN ('extracted', 'converted', 'enhanced', 'rebuilt')),
    logical_path TEXT    NOT NULL,
    format       TEXT,
    created_at   TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
);
CREATE INDEX artifact_blob ON artifact (blob_sha256);
CREATE INDEX artifact_source ON artifact (source_id);

-- Every action RRL takes, with enough detail to reproduce it.
CREATE TABLE operation (
    id               INTEGER PRIMARY KEY,
    op               TEXT    NOT NULL,
    source_id        INTEGER REFERENCES source (id),
    adapter_id       TEXT,
    adapter_version  TEXT,
    rrl_version      TEXT    NOT NULL,
    params_json      TEXT    NOT NULL DEFAULT '{}',
    tools_json       TEXT    NOT NULL DEFAULT '{}',
    status           TEXT    NOT NULL CHECK (status IN ('running', 'succeeded', 'failed')),
    error            TEXT,
    started_at       TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    finished_at      TEXT
);
CREATE INDEX operation_source ON operation (source_id);

-- Lineage: which artifacts each operation read and wrote.
CREATE TABLE edge (
    operation_id INTEGER NOT NULL REFERENCES operation (id),
    artifact_id  INTEGER NOT NULL REFERENCES artifact (id),
    role         TEXT    NOT NULL CHECK (role IN ('input', 'output')),
    PRIMARY KEY (operation_id, artifact_id, role)
);
CREATE INDEX edge_artifact ON edge (artifact_id);

CREATE TABLE project (
    id          INTEGER PRIMARY KEY,
    name        TEXT    NOT NULL UNIQUE,
    notes       TEXT,
    created_at  TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
);

CREATE TABLE project_source (
    project_id  INTEGER NOT NULL REFERENCES project (id) ON DELETE CASCADE,
    source_id   INTEGER NOT NULL REFERENCES source (id),
    PRIMARY KEY (project_id, source_id)
);

-- Emulator test results; feeds the compatibility database.
CREATE TABLE test_run (
    id                INTEGER PRIMARY KEY,
    emulator          TEXT    NOT NULL,
    emulator_version  TEXT,
    source_id         INTEGER REFERENCES source (id),
    artifact_id       INTEGER REFERENCES artifact (id),
    result            TEXT    NOT NULL
                      CHECK (result IN ('nothing', 'boots', 'menus', 'ingame', 'playable', 'crash', 'hang')),
    notes             TEXT,
    started_at        TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    CHECK (source_id IS NOT NULL OR artifact_id IS NOT NULL)
);
