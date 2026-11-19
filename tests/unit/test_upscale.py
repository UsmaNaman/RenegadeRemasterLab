from __future__ import annotations

import io
import json
import os
import sys
from collections.abc import Iterator, Sequence
from contextlib import closing
from pathlib import Path

import pytest
from PIL import Image

from rrl import db
from rrl.api import Asset, Source
from rrl.cli.main import main
from rrl.core.extraction import run_extract
from rrl.core.sources import add_source
from rrl.processors.textures import to_png
from rrl.processors.upscale import Texture, upscale_batch
from rrl.store.blobs import blob_path


@pytest.fixture
def backend(tmp_path: Path) -> Path:
    script = tmp_path / "fake_upscaler.py"
    script.write_text(
        """import argparse
from collections.abc import Iterator, Sequence
from contextlib import closing
from pathlib import Path
from PIL import Image

p = argparse.ArgumentParser()
for flag in ("-i", "-o", "-s", "-n", "-m", "-f"):
    p.add_argument(flag, required=True)
args = p.parse_args()
assert args.s == "4" and args.f == "png"
assert Path(args.m) == Path(__file__).parent / "models"
for path in Path(args.i).glob("*.png"):
    with Image.open(path) as image:
        assert image.mode == "RGB"
        with Path(__file__).with_name("calls").open("a") as log:
            print(args.n, image.size, file=log)
        factor = 1 if args.n == "bad-size" else 4
        image.resize((image.width * factor, image.height * factor)).save(Path(args.o) / path.name)
""",
        encoding="utf-8",
    )
    if os.name == "nt":
        launcher = tmp_path / "fake_upscaler.cmd"
        launcher.write_text(f'@"{sys.executable}" "%~dp0fake_upscaler.py" %*\n')
    else:
        launcher = tmp_path / "fake_upscaler"
        launcher.write_text(f'#!/bin/sh\nexec "{sys.executable}" "{script}" "$@"\n')
        launcher.chmod(0o755)
    return launcher


def test_backend_preserves_palette_transparency(backend: Path, tmp_path: Path) -> None:
    path = tmp_path / "palette.png"
    image = Image.new("P", (2, 1))
    image.putpalette([255, 0, 0, 0, 255, 0])
    image.putpixel((1, 0), 1)
    image.save(path, transparency=0)
    (data,) = upscale_batch(backend, "test", [Texture(path, 2)], tmp_path)
    with Image.open(io.BytesIO(data)) as result:
        assert result.size == (4, 2)
        assert result.getchannel("A").getextrema() == (0, 255)


def test_backend_rejects_wrong_dimensions(backend: Path, tmp_path: Path) -> None:
    path = tmp_path / "rgb.png"
    Image.new("RGB", (2, 2)).save(path)
    with pytest.raises(ValueError, match="expected 8x8"):
        list(upscale_batch(backend, "bad-size", [Texture(path, 4)], tmp_path))


class SyntheticTextures:
    id, version, capabilities = "xbox.halo2", "1", frozenset({"extract"})

    def extract(self, source: Source, selection: Sequence[str] | None = None) -> Iterator[Asset]:
        cutout = Image.new("RGBA", (2, 2), (30, 60, 90, 255))
        cutout.putalpha(Image.frombytes("L", (2, 2), bytes([0, 64, 155, 255])))
        data = to_png(cutout)
        yield Asset("a/wall.png", data, "image/png", {"format": "dxt5", "xemu_hashes": ["01"]})
        yield Asset("b/wall.png", data, "image/png", {"format": "dxt5"})
        yield Asset("c/cube_face0.png", to_png(Image.new("RGB", (3, 1))), "image/png", {"face": 0})
        for fmt in ("p8_bump", "v8u8", "g8b8", "p8"):
            yield Asset(f"a/{fmt}.png", data, "image/png", {"format": fmt})
        yield Asset("a/volume.png", data, "image/png", {"type": "3d", "slice": 0})
        yield Asset("a/scene.glb", b"synthetic model", "model/gltf-binary")


@pytest.fixture
def export_args(workspace: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> list[str]:
    monkeypatch.delenv("RRL_UPSCALER", raising=False)
    workspace.mkdir()
    source = tmp_path / "synthetic-source"
    source.write_bytes(b"synthetic")
    export = tmp_path / "export"
    with closing(db.connect(workspace / "rrl.db")) as conn:
        sid, src = add_source(conn, source)
        run_extract(conn, workspace / "store", sid, src, SyntheticTextures(), export_dir=export)  # type: ignore[arg-type]
    source.unlink()  # Upscaling a registered export works with its original offline.
    return ["upscale", str(sid), "--export", str(export), "--out", str(tmp_path / "out")]


def test_full_export_resume_and_lineage(
    workspace: Path,
    tmp_path: Path,
    export_args: list[str],
    backend: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    args = [*export_args, "--upscaler", str(backend), "--model", "local-4x", "--max-side", "8"]
    assert main(args) == 0
    report = capsys.readouterr().out
    for fmt in ("p8_bump", "v8u8", "g8b8"):
        assert f"skipped a/{fmt}.png: {fmt} bump/normal map" in report
    assert "skipped a/p8.png: P8 lightmap" in report
    assert "skipped a/volume.png: 3D texture slice" in report
    out, export = tmp_path / "out", tmp_path / "export"
    paths = {"a/wall.png", "b/wall.png", "c/cube_face0.png"}
    assert {p.relative_to(out).as_posix() for p in out.rglob("*") if p.is_file()} == paths
    with Image.open(out / "a/wall.png") as image, Image.open(export / "a/wall.png") as original:
        assert image.size == (8, 8)
        assert (
            image.getchannel("A").tobytes()
            == original.getchannel("A").resize((8, 8), Image.Resampling.LANCZOS).tobytes()
        )
    with Image.open(out / "c/cube_face0.png") as image:
        assert image.size == (6, 2)
        assert image.mode == "RGB"
    assert sorted((tmp_path / "calls").read_text().splitlines()) == [
        "local-4x (2, 2)",
        "local-4x (3, 1)",
    ]

    stamp = (out / "a/wall.png").stat().st_mtime_ns
    (out / "b/wall.png").unlink()
    assert main(args) == 0
    assert "upscaled 0 unique textures" in capsys.readouterr().out
    assert (out / "a/wall.png").stat().st_mtime_ns == stamp
    assert (out / "b/wall.png").read_bytes() == (out / "a/wall.png").read_bytes()
    assert len((tmp_path / "calls").read_text().splitlines()) == 2

    with closing(db.connect(workspace / "rrl.db")) as conn:
        ops = conn.execute("SELECT * FROM operation WHERE op = 'upscale'").fetchall()
        assert len(ops) == 2
        for op in ops:
            assert op["source_id"] == 1 and op["status"] == "succeeded"
            assert json.loads(op["params_json"])["max_side"] == 8
            assert json.loads(op["tools_json"])["upscaler"] == str(backend.resolve())
            edges = dict(
                conn.execute(
                    "SELECT role, count(*) FROM edge WHERE operation_id = ? GROUP BY role",
                    (op["id"],),
                )
            )
            assert edges == {"input": 3, "output": 3}
        rows = conn.execute("SELECT * FROM artifact WHERE kind = 'enhanced'").fetchall()
        assert len(rows) == 3
        for row in rows:
            name = row["logical_path"]
            meta = json.loads(row["metadata_json"])
            (original_id,) = conn.execute(
                "SELECT id FROM artifact WHERE kind = 'extracted' AND logical_path = ?", (name,)
            ).fetchone()
            assert meta == {
                "source_artifact_id": original_id,
                "scale": 2 if name.startswith("c/") else 4,
                "model": "local-4x",
            }
            assert (out / name).read_bytes() == blob_path(
                workspace / "store", row["blob_sha256"]
            ).read_bytes()


def test_prefix_packable_and_backend_precedence(
    workspace: Path,
    tmp_path: Path,
    export_args: list[str],
    backend: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    (workspace / "rrl.toml").write_text(f"[tools]\nupscaler = '{backend}'\n")
    assert main(["doctor"]) == 0
    assert str(backend) in capsys.readouterr().out
    args = [*export_args, "--scale", "2", "--only", "a/wall", "b/", "--packable-only"]
    args[1] = str(tmp_path / "synthetic-source")
    assert main(args) == 0
    assert "skipped b/wall.png: no xemu pack hashes" in capsys.readouterr().out
    assert list((tmp_path / "out").rglob("*.png")) == [tmp_path / "out/a/wall.png"]
    with Image.open(tmp_path / "out/a/wall.png") as result:
        assert result.size == (4, 4)
    monkeypatch.setenv("RRL_UPSCALER", str(tmp_path / "missing"))
    assert main(args) == 1  # Environment overrides TOML.
    assert "missing or not executable" in capsys.readouterr().err
    assert main([*args, "--upscaler", str(backend)]) == 0  # Explicit path overrides both.


def test_missing_backend_and_failed_inference_recorded(
    workspace: Path,
    tmp_path: Path,
    export_args: list[str],
    backend: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert main(["doctor"]) == 0
    assert "optional: realesrgan-ncnn-vulkan" in capsys.readouterr().out
    assert main(export_args) == 1
    assert "--upscaler PATH, RRL_UPSCALER" in capsys.readouterr().err
    monkeypatch.setenv("RRL_UPSCALER", str(backend))
    assert main([*export_args, "--model", "bad-size"]) == 1
    assert "expected 8x8" in capsys.readouterr().err
    with closing(db.connect(workspace / "rrl.db")) as conn:
        assert [
            r[0] for r in conn.execute("SELECT status FROM operation WHERE op = 'upscale'")
        ] == [
            "failed",
            "failed",
        ]
        assert (
            conn.execute("SELECT count(*) FROM artifact WHERE kind = 'enhanced'").fetchone()[0] == 0
        )
    assert not list((tmp_path / "out").rglob("*.png"))


def test_rejects_edited_source_or_output(
    tmp_path: Path, export_args: list[str], backend: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    args = [*export_args, "--only", "a/wall", "--upscaler", str(backend)]
    assert main(args) == 0
    dest = tmp_path / "out/a/wall.png"
    dest.write_bytes(b"edited")
    assert main(args) == 1
    assert "existing output differs" in capsys.readouterr().err and dest.read_bytes() == b"edited"
    (tmp_path / "export/a/wall.png").write_bytes(b"different export")
    assert main(args) == 1
    assert "does not match an extracted blob" in capsys.readouterr().err
    assert len((tmp_path / "calls").read_text().splitlines()) == 1


def test_p8_from_another_adapter_is_eligible(
    workspace: Path,
    tmp_path: Path,
    export_args: list[str],
    backend: Path,
) -> None:
    with closing(db.connect(workspace / "rrl.db")) as conn, conn:
        conn.execute("UPDATE operation SET adapter_id = 'test.palette' WHERE op = 'extract'")
    assert main([*export_args, "--only", "a/p8.png", "--upscaler", str(backend)]) == 0
    assert (tmp_path / "out/a/p8.png").is_file()
