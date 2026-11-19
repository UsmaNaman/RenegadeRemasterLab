from __future__ import annotations

import io
import json
from pathlib import Path

import pytest
from fixtures.xbox_builder import (
    REDUMP_OFFSET,
    SECTOR,
    build_partition,
    make_xbe,
    sample_tree,
    write_image,
)

from rrl.adapters.registry import check
from rrl.adapters.xbox_original import XboxOriginalAdapter
from rrl.adapters.xbox_original.xbe import Certificate, XbeError, read_certificate
from rrl.api import Source
from rrl.cli.main import main


def _source(path: Path) -> Source:
    return Source(path, "0" * 64, path.stat().st_size)  # adapters never need the hash


def test_read_certificate() -> None:
    cert = read_certificate(io.BytesIO(make_xbe()))
    assert cert == Certificate(0x524C0001, "RRL Test Disc", 0x2, 0x5, 3)
    assert cert.title_id_str == "RL-001"
    assert cert.title_id_hex == "524C0001"
    assert cert.region_str == "NTSC-U/PAL"
    assert cert.media_flags == ["DVD_X2"]


@pytest.mark.parametrize(
    ("region", "text"),
    [
        (0x1, "NTSC-U"),
        (0x7, "NTSC-U/NTSC-J/PAL"),
        (0x80000000, "DEBUG"),
        (0x0, "none"),
        (0x2 | 0x100, "NTSC-J/0x100"),
    ],
)
def test_region_rendering(region: int, text: str) -> None:
    assert Certificate(0, "", 0, region, 0).region_str == text


def test_title_id_rendering() -> None:
    assert Certificate(0x4D530064, "", 0, 0, 0).title_id_str == "MS-100"
    assert Certificate(0x0001FFFF, "", 0, 0, 0).title_id_str == "0001FFFF"


@pytest.mark.parametrize(
    "data",
    [b"", b"XBEH", b"MZ" + b"\0" * 0x400, make_xbe()[:0x200]],
)
def test_bad_xbe(data: bytes) -> None:
    with pytest.raises(XbeError):
        read_certificate(io.BytesIO(data))


def test_adapter_passes_registry_checks() -> None:
    check(XboxOriginalAdapter())


@pytest.mark.parametrize(("offset", "layout"), [(0, "xiso"), (REDUMP_OFFSET, "redump")])
def test_identify(tmp_path: Path, offset: int, layout: str) -> None:
    image = write_image(tmp_path / "disc.iso", build_partition(sample_tree()), offset)
    match = XboxOriginalAdapter().identify(_source(image))
    assert match is not None
    assert (match.platform, match.title, match.title_id, match.region) == (
        "Original Xbox",
        "RRL Test Disc",
        "RL-001",
        "NTSC-U/PAL",
    )
    assert match.confidence == 0.9
    assert match.details == {
        "partition_offset": offset,
        "layout": layout,
        "title_id_hex": "524C0001",
        "version": 3,
        "allowed_media": ["DVD_X2"],
    }


def test_inspect(tmp_path: Path) -> None:
    image = write_image(tmp_path / "disc.iso", build_partition(sample_tree()))
    report = XboxOriginalAdapter().inspect(_source(image))
    json.dumps(report)
    assert report["title"] == "RRL Test Disc"
    assert (report["file_count"], report["dir_count"]) == (6, 3)
    assert report["total_bytes"] == 0x400 + 28 + 5120 + 100 + 4
    assert report["largest_files"][0] == {"path": "maps/alpha.map", "size": 5120}
    assert {"path": "maps/sub/deep.bin", "size": 4} in report["files"]
    assert report["files_truncated"] is False


def test_inspect_truncates_long_listings(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("rrl.adapters.xbox_original.MAX_LISTED", 3)
    image = write_image(tmp_path / "disc.iso", build_partition(sample_tree()))
    report = XboxOriginalAdapter().inspect(_source(image))
    assert len(report["files"]) == 3 and report["files_truncated"] is True
    assert report["largest_files"][0]["path"] == "maps/alpha.map"


@pytest.mark.parametrize(
    "data",
    [
        b"",
        b"\x01" * 100_000,
        build_partition({"other.xbe": make_xbe()}),  # no default.xbe
        build_partition({"default.xbe": b"not an xbe"}),
        build_partition(sample_tree())[: 40 * SECTOR],  # truncated
    ],
)
def test_identify_rejects(tmp_path: Path, data: bytes) -> None:
    image = tmp_path / "bad.iso"
    image.write_bytes(data)
    assert XboxOriginalAdapter().identify(_source(image)) is None


def test_cli_identify_and_inspect(
    workspace: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    image = write_image(tmp_path / "disc.iso", build_partition(sample_tree()))
    assert main(["identify", str(image)]) == 0
    out = capsys.readouterr().out
    assert "Original Xbox · RRL Test Disc · RL-001 · NTSC-U/PAL" in out
    assert main(["inspect", str(image)]) == 0
    assert json.loads(capsys.readouterr().out)["file_count"] == 6
    assert main(["source", "list"]) == 0
    assert "RRL Test Disc" in capsys.readouterr().out
