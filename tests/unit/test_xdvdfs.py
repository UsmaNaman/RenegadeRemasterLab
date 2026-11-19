from __future__ import annotations

import io
import struct
from pathlib import Path

import pytest
from fixtures.xbox_builder import (
    REDUMP_OFFSET,
    SECTOR,
    build_partition,
    dir_table,
    sample_tree,
    volume_descriptor,
    write_image,
)

from rrl.api import Container
from rrl.containers.xdvdfs import XdvdfsError, XdvdfsImage, find_partition


@pytest.fixture
def image() -> XdvdfsImage:
    return XdvdfsImage(io.BytesIO(build_partition(sample_tree())))


def test_entries_parents_before_children(image: XdvdfsImage) -> None:
    assert isinstance(image, Container)
    listing = [(e.path, e.size, e.is_dir) for e in image.entries()]
    assert listing == [
        ("default.xbe", 0x400, False),
        ("empty", 0, True),
        ("maps", 0, True),
        ("maps/alpha.map", 5120, False),
        ("maps/beta.map", 100, False),
        ("maps/sub", 0, True),
        ("maps/sub/deep.bin", 4, False),
        ("Readme.TXT", 28, False),
        ("zero.dat", 0, False),
    ]


def test_open_is_case_insensitive_and_reads_through_window(image: XdvdfsImage) -> None:
    assert image.open("README.txt").read() == b"hello from a synthetic disc\n"
    assert image.open("/MAPS/Sub/DEEP.BIN").read() == b"deep"
    assert image.open("zero.dat").read() == b""
    expected = bytes(range(256)) * 20
    with image.open("maps\\alpha.map") as f:
        assert f.read(10) == expected[:10]
        f.seek(2040)
        assert f.read(20) == expected[2040:2060]  # crosses a sector boundary
        f.seek(-5, io.SEEK_END)
        assert f.read() == expected[-5:]
        assert f.read(10) == b""  # never reads past the file into the next one
        f.seek(0)
        assert f.read() == expected


def test_open_missing_and_directories(image: XdvdfsImage) -> None:
    for path in ("nope", "maps/nope", "readme.txt/x", ""):
        with pytest.raises(FileNotFoundError):
            image.open(path)
    with pytest.raises(IsADirectoryError):
        image.open("maps")


def test_find_partition_xiso_and_redump(tmp_path: Path) -> None:
    data = build_partition(sample_tree())
    assert find_partition(io.BytesIO(data)) == 0
    redump = write_image(tmp_path / "redump.iso", data, REDUMP_OFFSET)
    with redump.open("rb") as f:
        assert find_partition(f) == REDUMP_OFFSET
        with XdvdfsImage(f, REDUMP_OFFSET).open("maps/beta.map") as m:
            assert m.read() == b"B" * 100
    assert find_partition(io.BytesIO(b"\0" * 0x20000)) is None


def test_path_constructor_owns_and_closes_file(tmp_path: Path) -> None:
    path = write_image(tmp_path / "x.iso", build_partition(sample_tree()))
    with XdvdfsImage(path) as img:
        assert img.open("maps/sub/deep.bin").read() == b"deep"


def test_bad_descriptor_releases_owned_file(tmp_path: Path) -> None:
    path = tmp_path / "invalid.dat"
    path.write_bytes(b"invalid")
    with pytest.raises(XdvdfsError) as error:
        XdvdfsImage(path)
    # The traceback still holds the reader. Windows permits removal only after close().
    assert error.traceback
    path.unlink()


def test_trailing_magic_copy_is_required() -> None:
    data = bytearray(build_partition(sample_tree()))
    data[32 * SECTOR + 0x7EC] ^= 0xFF
    assert find_partition(io.BytesIO(bytes(data))) is None
    with pytest.raises(XdvdfsError):
        XdvdfsImage(io.BytesIO(bytes(data)))


def _raw_image(root_rows: list[tuple[str, int, int, int]], extra: bytes = b"") -> io.BytesIO:
    """Root table at sector 34, followed by `extra` sectors."""
    table = dir_table(root_rows)
    data = bytearray(34 * SECTOR) + table + extra
    data[32 * SECTOR : 33 * SECTOR] = volume_descriptor(34, len(table))
    return io.BytesIO(bytes(data))


def test_truncated_image() -> None:
    data = build_partition(sample_tree())
    with pytest.raises(XdvdfsError):
        list(XdvdfsImage(io.BytesIO(data[: len(data) - SECTOR])).entries())
    assert find_partition(io.BytesIO(data[: 32 * SECTOR + 100])) is None


def test_out_of_bounds_sector() -> None:
    img = XdvdfsImage(_raw_image([("big.bin", 0xFFFFFF, 10, 0x20)]))
    with pytest.raises(XdvdfsError):
        list(img.entries())
    with pytest.raises(XdvdfsError):
        img.open("big.bin")


def test_directory_cycle() -> None:
    img = XdvdfsImage(_raw_image([("loop", 34, SECTOR, 0x10)]))  # points at its own table
    with pytest.raises(XdvdfsError, match="cycle"):
        list(img.entries())


def test_loop_inside_a_table() -> None:
    buf = _raw_image([("a", 0, 0, 0x20), ("b", 0, 0, 0x20), ("c", 0, 0, 0x20)])
    data = bytearray(buf.getvalue())
    root = 34 * SECTOR
    child = struct.unpack_from("<H", data, root)[0] * 4  # root's left child
    struct.pack_into("<H", data, root + child + 2, child // 4)  # its right subtree: itself
    with pytest.raises(XdvdfsError, match="loop"):
        list(XdvdfsImage(io.BytesIO(bytes(data))).entries())


def test_unsafe_names_rejected() -> None:
    with pytest.raises(XdvdfsError, match="unsafe"):
        list(XdvdfsImage(_raw_image([("..", 0, 0, 0x10)])).entries())


def test_multi_sector_directory_table() -> None:
    tree = {f"file_with_a_longish_name_{i:03}.bin": bytes([i]) for i in range(200)}
    img = XdvdfsImage(io.BytesIO(build_partition(tree)))
    assert len(list(img.entries())) == 200
    assert img.open("FILE_WITH_A_LONGISH_NAME_199.BIN").read() == bytes([199])


def test_deep_tree_is_capped() -> None:
    # 70 nested directories, each table one sector, the innermost empty.
    depth = 70
    tables = b"".join(
        dir_table([("d", 36 + i, SECTOR if i < depth - 1 else 0, 0x10)]) for i in range(depth)
    )
    img = XdvdfsImage(_raw_image([("d", 35, SECTOR, 0x10)], tables))
    with pytest.raises(XdvdfsError, match="deeper"):
        list(img.entries())


def test_locate_points_at_file_bytes(image: XdvdfsImage) -> None:
    for entry in image.entries():
        if entry.is_dir:
            continue
        start, size = image.locate(entry.path)
        assert size == entry.size
        image._f.seek(start)
        assert image._f.read(size) == image.open(entry.path).read()
