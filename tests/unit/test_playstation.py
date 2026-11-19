import json
from pathlib import Path

import pytest
from fixtures.iso_builder import PAYLOAD, cue, image
from fixtures.xbox_builder import build_partition, make_xbe

from rrl.adapters import registry
from rrl.api import Source
from rrl.cli.main import main


def assert_cli(
    path: Path, ps: int, serial: str, region: str, mode: str, capsys: pytest.CaptureFixture[str]
) -> None:
    adapters, errors = registry.load_adapters()
    assert not errors
    source = Source(path, "0" * 64, path.stat().st_size)
    assert [m.adapter_id for m in registry.identify(source, adapters)] == [f"sony.ps{ps}"]
    assert main(["source", "add", str(path)]) == 0
    assert main(["identify", "1"]) == 0
    result = capsys.readouterr().out
    for expected in [f"PlayStation {ps}", serial, region]:
        assert expected in result
    assert main(["inspect", "1"]) == 0
    report = json.loads(capsys.readouterr().out)
    assert (report["platform"], report["title_id"], report["region"]) == (
        f"PlayStation {ps}",
        serial,
        region,
    )
    assert report["layout"] == "ISO9660" and report["sector_mode"] == mode
    assert report["boot_size"] == len(PAYLOAD) and report["file_count"] == 2
    assert report["system_cnf"]["VER"] == "1.00" and report["system_cnf"]["VMODE"] == "PAL"


def test_ps2_cli(tmp_path: Path, workspace: Path, capsys: pytest.CaptureFixture[str]) -> None:
    path = tmp_path / "generated.iso"
    path.write_bytes(image())
    assert_cli(path, 2, "SLUS-12345", "NTSC-U", "MODE1/2048", capsys)


def test_ps1_cli(tmp_path: Path, workspace: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert_cli(cue(tmp_path), 1, "SLES-98765", "PAL", "MODE2/2352", capsys)


def test_xbox_priority(tmp_path: Path) -> None:
    path = tmp_path / "synthetic.iso"
    path.write_bytes(
        build_partition({"default.xbe": make_xbe(title="Halo 2", title_id=0x4D530064)})
    )
    adapters, errors = registry.load_adapters()
    assert not errors
    matches = registry.identify(Source(path, "0" * 64, path.stat().st_size), adapters)
    assert [m.adapter_id for m in matches] == ["xbox.halo2", "xbox.original"]
