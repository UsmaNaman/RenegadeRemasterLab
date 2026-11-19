import json
import os
from pathlib import Path

import pytest
from PIL import Image

from rrl.cli.main import main
from rrl.core.hashing import sha256_file
from rrl.emulators.pcsx2_pack import build_pack

SERIAL = "SLUS-12345"
NAME = "0123456789ABCDEF-0000000000000000-00000001-mip1.PNG"


def png(folder: Path, name: str, size: tuple[int, int]) -> Path:
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / name
    Image.new("RGBA", size, (10, 20, 30, 40)).save(path, format="PNG")
    return path


def test_pack(workspace: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    # PCSX2's dump can itself be under the eventual pack root.
    dump = tmp_path / "textures" / SERIAL / "dumps"
    edits = tmp_path / "edits"
    for name in (NAME, "AB-12.png", "CD-34.png", "CC-00.png"):
        png(dump, name, (3, 2))
    before = {p.name: sha256_file(p) for p in dump.iterdir()}
    large = png(edits, NAME, (12, 8))
    native = png(edits, "CC-00.png", (3, 2))
    png(edits, "CD-34.png", (5, 5))
    png(edits, "DEAD-BEEF.png", (8, 8))
    args = ["pack", "pcsx2", str(dump), str(edits), str(tmp_path), "--serial", SERIAL]
    assert main(args) == 0
    report = json.loads(capsys.readouterr().out)
    target = dump.parent / "replacements"
    assert report == {
        "packed": 2,
        "missing": ["AB-12.png"],
        "extra": ["DEAD-BEEF.png"],
        "size_mismatched": ["CD-34.png: dump 3x2, edited 5x5"],
        "replacements": str(target),
    }
    assert sorted(p.name for p in target.iterdir()) == sorted([large.name, native.name])
    for path in (native, large):
        assert (target / path.name).read_bytes() == path.read_bytes()
    assert before == {p.name: sha256_file(p) for p in dump.iterdir()}


@pytest.mark.parametrize("serial", ["../SLUS-12345", "SLUS_123.45", "SLUS-1234", "SLUS-１２３４５"])
def test_serial(tmp_path: Path, serial: str) -> None:
    with pytest.raises(ValueError, match="serial"):
        build_pack(tmp_path, tmp_path, tmp_path, serial)


def test_dump_overlap(tmp_path: Path) -> None:
    dump = tmp_path / "dump"
    original = png(dump, NAME, (2, 3))
    before = sha256_file(original)
    with pytest.raises(ValueError, match="overlap"):
        build_pack(dump, tmp_path / "edits", dump, SERIAL)
    assert sha256_file(original) == before and list(dump.iterdir()) == [original]


def test_malformed_edit_preserves_old_replacement(tmp_path: Path) -> None:
    dump, edits, pack = (tmp_path / name for name in ("dump", "edits", "pack"))
    png(dump, NAME, (2, 3))
    edit = png(edits, NAME, (4, 6))
    build_pack(dump, edits, pack, SERIAL)
    target = pack / "textures" / SERIAL / "replacements"
    before = (target / NAME).read_bytes()
    edit.write_bytes(b"broken PNG")
    with pytest.raises(ValueError, match="invalid PNG"):
        build_pack(dump, edits, pack, SERIAL)
    assert (target / NAME).read_bytes() == before
    assert list(target.iterdir()) == [target / NAME]


def test_unsafe_filename(tmp_path: Path) -> None:
    dump, edits, pack = (tmp_path / name for name in ("dump", "edits", "pack"))
    png(dump, NAME, (1, 1))
    # A backslash is a literal filename character on POSIX and a separator on Windows.
    unsafe = "AA..BB.png" if os.name == "nt" else r"..\outside.png"
    png(edits, unsafe, (1, 1))
    with pytest.raises(ValueError, match="unsafe PNG filename"):
        build_pack(dump, edits, pack, SERIAL)
    assert not pack.exists()


def test_replacement_symlink(tmp_path: Path) -> None:
    dump, edits, pack = (tmp_path / name for name in ("dump", "edits", "pack"))
    original = png(dump, NAME, (1, 2))
    png(edits, NAME, (2, 4))
    before = sha256_file(original)
    target = pack / "textures" / SERIAL / "replacements"
    target.mkdir(parents=True)
    try:
        (target / NAME).symlink_to(original)
    except OSError as exc:
        pytest.skip(f"symlink creation unavailable: {exc}")
    with pytest.raises(ValueError, match="symlink"):
        build_pack(dump, edits, pack, SERIAL)
    assert sha256_file(original) == before
