from __future__ import annotations

from pathlib import Path

import pytest

from rrl.cli.main import main


def test_source_add_and_list(
    workspace: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    image = tmp_path / "thing.iso"
    image.write_bytes(b"not a real disc")
    assert main(["source", "add", str(image)]) == 0
    assert main(["source", "list"]) == 0
    out = capsys.readouterr().out
    assert "thing.iso" in out and "(not identified)" in out
    assert (workspace / "rrl.db").exists()
    (workspace / "logs" / "rrl.log").unlink()  # Windows requires the CLI to release the handle.


def test_identify_unknown_file_fails_cleanly(
    workspace: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    image = tmp_path / "random.bin"
    image.write_bytes(b"\x01" * 100_000)
    assert main(["identify", str(image)]) == 1
    assert "not recognised" in capsys.readouterr().out


def test_missing_source_is_an_error_not_a_crash(
    workspace: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert main(["identify", "42"]) == 1
    assert "no source with id 42" in capsys.readouterr().err
