from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterator, Sequence
from pathlib import Path
from typing import Any, ClassVar

import pytest

from rrl.adapters.registry import check
from rrl.api import API_VERSION, Asset, Match, Skipped, Source
from rrl.core.extraction import ExtractionError, _safe_relpath, run_extract
from rrl.core.sources import add_source
from rrl.store.blobs import blob_path


class FakeExtractor:
    id: ClassVar[str] = "test.extract"
    name: ClassVar[str] = "Fake"
    version: ClassVar[str] = "1"
    api_version: ClassVar[int] = API_VERSION
    capabilities: ClassVar[frozenset[str]] = frozenset({"identify", "extract"})
    paths: ClassVar[list[str]] = ["a/one.png", "a/b/two.png"]

    def identify(self, source: Source) -> Match | None:
        return Match(self.id, "test", 1.0)

    def extract(
        self, source: Source, selection: Sequence[str] | None = None
    ) -> Iterator[Asset | Skipped]:
        for i, p in enumerate(self.paths):
            if selection and not p.startswith(tuple(selection)):
                continue
            yield Asset(p, f"data{i}".encode(), "image/png", {"i": i})
        yield Skipped("a/three.png", "unsupported format p8")


class EvilExtractor(FakeExtractor):
    paths = ["../escape.png"]


@pytest.fixture
def source(conn: sqlite3.Connection, tmp_path: Path) -> tuple[int, Source]:
    img = tmp_path / "disc.iso"
    img.write_bytes(b"x" * 100)
    return add_source(conn, img)


def test_fake_extractor_passes_contract() -> None:
    check(FakeExtractor())


@pytest.mark.parametrize("path", [r"maps\texture.png", "C:art.png", "C:/models/art.png"])
def test_logical_paths_reject_windows_separators_and_drives(path: str) -> None:
    with pytest.raises(ExtractionError):
        _safe_relpath(path)


def test_extract_stores_records_and_exports(
    conn: sqlite3.Connection, tmp_path: Path, source: tuple[int, Source]
) -> None:
    sid, src = source
    store, export = tmp_path / "store", tmp_path / "export"
    summary = run_extract(conn, store, sid, src, FakeExtractor(), None, export)  # type: ignore[arg-type]

    assert summary.stored == 2
    assert summary.skipped == [("a/three.png", "unsupported format p8")]
    assert (export / "a/b/two.png").read_bytes() == b"data1"
    rows = conn.execute(
        "SELECT a.logical_path, a.kind, a.blob_sha256, a.metadata_json, e.role, o.status"
        " FROM artifact a JOIN edge e ON e.artifact_id = a.id JOIN operation o"
        " ON o.id = e.operation_id ORDER BY a.id"
    ).fetchall()
    assert [r["logical_path"] for r in rows] == ["a/one.png", "a/b/two.png"]
    assert {(r["kind"], r["role"], r["status"]) for r in rows} == {
        ("extracted", "output", "succeeded")
    }
    assert json.loads(rows[0]["metadata_json"]) == {"i": 0}
    assert blob_path(store, rows[0]["blob_sha256"]).read_bytes() == b"data0"


def test_selection_is_passed_through(
    conn: sqlite3.Connection, tmp_path: Path, source: tuple[int, Source]
) -> None:
    sid, src = source
    summary = run_extract(conn, tmp_path, sid, src, FakeExtractor(), ["a/b"])  # type: ignore[arg-type]
    assert summary.stored == 1


def test_path_traversal_from_adapter_is_refused_and_recorded(
    conn: sqlite3.Connection, tmp_path: Path, source: tuple[int, Source]
) -> None:
    sid, src = source
    with pytest.raises(ExtractionError):
        run_extract(conn, tmp_path / "s", sid, src, EvilExtractor(), None, tmp_path / "out")  # type: ignore[arg-type]
    assert not (tmp_path / "escape.png").exists()
    status = conn.execute("SELECT status FROM operation WHERE op = 'extract'").fetchone()[0]
    assert status == "failed"


def test_adapter_without_extract_is_rejected(
    conn: sqlite3.Connection, tmp_path: Path, source: tuple[int, Source]
) -> None:
    class NoExtract:
        id, version, capabilities = "x", "1", frozenset({"identify"})

    sid, src = source
    any_adapter: Any = NoExtract()
    with pytest.raises(ExtractionError):
        run_extract(conn, tmp_path, sid, src, any_adapter)
