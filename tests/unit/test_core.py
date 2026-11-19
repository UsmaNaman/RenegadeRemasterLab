from __future__ import annotations

import hashlib
import sqlite3
from pathlib import Path

import pytest

from rrl import db
from rrl.core.config import ConfigError, load_config
from rrl.core.sources import SourceError, add_source, get_source
from rrl.store.blobs import blob_path, put_bytes
from rrl.store.provenance import operation


def test_migrations_apply_once(conn: sqlite3.Connection) -> None:
    version = conn.execute("PRAGMA user_version").fetchone()[0]
    assert version >= 1
    assert db.migrate(conn) == version  # idempotent


def test_schema_rejects_bad_artifact_kind(conn: sqlite3.Connection) -> None:
    conn.execute("INSERT INTO blob (sha256, size) VALUES (?, 1)", ("a" * 64,))
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO artifact (blob_sha256, kind, logical_path) VALUES (?, 'original', 'x')",
            ("a" * 64,),
        )


def test_blob_store_dedupes_and_locks(conn: sqlite3.Connection, tmp_path: Path) -> None:
    a = put_bytes(conn, tmp_path, b"hello")
    b = put_bytes(conn, tmp_path, b"hello")
    assert a == b
    path = blob_path(tmp_path, a)
    assert path.read_bytes() == b"hello"
    assert path.stat().st_mode & 0o222 == 0
    assert conn.execute("SELECT count(*) FROM blob").fetchone()[0] == 1


def test_blob_store_cleans_up_after_concurrent_publish(
    conn: sqlite3.Connection, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def publish_first(staged: str, dest: Path) -> None:
        dest.write_bytes(Path(staged).read_bytes())
        dest.chmod(0o444)
        raise FileExistsError(dest)

    monkeypatch.setattr("rrl.store.blobs.os.rename", publish_first)
    path = blob_path(tmp_path, put_bytes(conn, tmp_path, b"hello"))
    assert path.read_bytes() == b"hello"
    assert path.stat().st_mode & 0o222 == 0
    assert list(path.parent.iterdir()) == [path]


def test_add_source_hashes_without_modifying(conn: sqlite3.Connection, tmp_path: Path) -> None:
    image = tmp_path / "game.iso"
    image.write_bytes(b"\x00" * 4096)
    before = image.stat()

    sid, source = add_source(conn, image)
    assert source.sha256 == hashlib.sha256(b"\x00" * 4096).hexdigest()
    assert source.size == 4096
    after = image.stat()
    assert (before.st_mtime_ns, before.st_size) == (after.st_mtime_ns, after.st_size)

    # Re-adding is idempotent and resolvable by id.
    assert add_source(conn, image)[0] == sid
    assert get_source(conn, str(sid))[1] == source
    ops = conn.execute("SELECT op, status FROM operation").fetchall()
    assert [tuple(r) for r in ops] == [("source.add", "succeeded")]


def test_add_source_rejects_missing(conn: sqlite3.Connection, tmp_path: Path) -> None:
    with pytest.raises(SourceError):
        add_source(conn, tmp_path / "nope.iso")


def test_failed_operation_is_recorded(conn: sqlite3.Connection) -> None:
    with pytest.raises(RuntimeError), operation(conn, "demo"):
        raise RuntimeError("boom")
    row = conn.execute("SELECT status, error FROM operation").fetchone()
    assert row["status"] == "failed" and "boom" in row["error"]


def test_config_reads_workspace_toml(workspace: Path) -> None:
    workspace.mkdir()
    (workspace / "rrl.toml").write_text('[emulators]\nxemu = "~/xemu.app"\n')
    config = load_config()
    assert config.workspace == workspace.resolve()
    assert config.xemu == Path("~/xemu.app").expanduser()


def test_config_rejects_bad_types(workspace: Path) -> None:
    workspace.mkdir()
    (workspace / "rrl.toml").write_text("[emulators]\nxemu = 3\n")
    with pytest.raises(ConfigError):
        load_config()
