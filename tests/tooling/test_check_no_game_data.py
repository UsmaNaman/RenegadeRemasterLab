"""Tests for scripts/check_no_game_data.py using tiny synthetic blobs (no real data)."""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

SCRIPT = Path(__file__).parents[2] / "scripts" / "check_no_game_data.py"
_spec = importlib.util.spec_from_file_location("check_no_game_data", SCRIPT)
assert _spec and _spec.loader
cngd = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(cngd)


def blob(offset: int, magic: bytes, size: int | None = None) -> bytes:
    data = bytearray(size or offset + len(magic) + 16)
    data[offset : offset + len(magic)] = magic
    return bytes(data)


def check(tmp_path: Path, name: str, data: bytes, allow: set[str] | None = None) -> list[str]:
    p = tmp_path / name
    p.write_bytes(data)
    return cngd.problems(p, allow or set())


@pytest.mark.parametrize(
    ("offset", "magic"),
    [
        (0x0, cngd.XDVDFS_MAGIC),
        (0x10000, cngd.XDVDFS_MAGIC),
        (0x107EC, cngd.XDVDFS_MAGIC),
        (0x8001, b"CD001"),
        (0x9319, b"CD001"),
        (0x0, b"XBEH"),
        (0x0, b"XEX2"),
        (0x0, b"CON "),
        (0x0, b"LIVE"),
        (0x0, b"PIRS"),
        (0x0, b"daeh"),
        (0x0, b"head"),
        (0x0, b"TIM2"),
        (0x0, b"VAGp"),
    ],
)
def test_signatures_blocked(tmp_path: Path, offset: int, magic: bytes) -> None:
    found = check(tmp_path, "innocent.dat", blob(offset, magic))
    assert any("signature" in p for p in found), found


def test_clean_files_pass(tmp_path: Path) -> None:
    assert check(tmp_path, "notes.txt", b"hello\n") == []
    assert check(tmp_path, "zeros.dat", bytes(0x11000)) == []


def test_text_starting_with_fourcc_word_passes(tmp_path: Path) -> None:
    assert check(tmp_path, "readme.txt", b"head of the project\n") == []
    assert check(tmp_path, "log.txt", b"LIVE output follows\n") == []


def test_magic_string_in_docs_not_flagged(tmp_path: Path) -> None:
    doc = b"# Notes\nThe hook blocks " + cngd.XDVDFS_MAGIC + b" and XBEH.\n"
    assert check(tmp_path, "notes.md", doc) == []


def test_tim_only_checked_for_tim_extension(tmp_path: Path) -> None:
    data = blob(0, b"\x10\x00\x00\x00")
    assert check(tmp_path, "a.dat", data) == []
    assert check(tmp_path, "a.tim", data) == ["Sony TIM texture signature at offset 0x0"]


def test_large_file_blocked(tmp_path: Path) -> None:
    found = check(tmp_path, "big.txt", b"a" * (cngd.MAX_BYTES + 1))
    assert found == [f"larger than 1 MiB ({cngd.MAX_BYTES + 1} bytes)"]
    assert check(tmp_path, "limit.txt", b"a" * cngd.MAX_BYTES) == []


@pytest.mark.parametrize("name", ["game.iso", "default.XBE", "track.bin", "x.map", "eeprom.key"])
def test_blocked_extensions(tmp_path: Path, name: str) -> None:
    assert any("blocked extension" in p for p in check(tmp_path, name, b"x"))


def test_allowlist_exempts_extension_only(tmp_path: Path) -> None:
    p = tmp_path / "app.js.map"
    p.write_bytes(b"{}")
    assert cngd.problems(p, {p.as_posix()}) == []
    p.write_bytes(blob(0, b"XBEH"))
    assert cngd.problems(p, {p.as_posix()}) == ["Xbox executable (XBE) signature at offset 0x0"]


def test_load_allowlist_ignores_comments(tmp_path: Path) -> None:
    f = tmp_path / "allow.txt"
    f.write_text("# header\n\ndocs/a.map  # reviewed\n")
    assert cngd.load_allowlist(f) == {"docs/a.map"}
    assert cngd.load_allowlist(tmp_path / "missing.txt") == set()


def test_main_exit_codes(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    good = tmp_path / "ok.txt"
    good.write_text("ok\n")
    bad = tmp_path / "default.xbe"
    bad.write_bytes(blob(0, b"XBEH"))
    assert cngd.main([str(good), str(tmp_path / "missing")]) == 0
    assert cngd.main([str(good), str(bad)]) == 1
    err = capsys.readouterr().err
    assert "default.xbe: blocked extension .xbe" in err
    assert "Xbox executable (XBE) signature" in err
