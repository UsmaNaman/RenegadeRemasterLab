from pathlib import Path

import numpy as np
import pytest
from PIL import Image

from rrl.cli.main import main
from rrl.processors.normals import fix, from_height


def test_height_flat_and_ramp() -> None:
    flat = from_height(Image.new("RGB", (1, 7), "red"), 2, True)
    np.testing.assert_equal(np.asarray(flat), np.tile([128, 128, 255], (7, 1, 1)))
    # Height increases 40/255 to the right and 10/255 down each pixel.
    y, x = np.indices((5, 5))
    ramp = Image.fromarray((40 * x + 10 * y).astype(np.uint8))
    image = np.asarray(from_height(ramp, 2, False))
    normal = np.array([-80, 20, 255])
    encoded = np.rint((normal / np.linalg.norm(normal) + 1) * 127.5)
    np.testing.assert_equal(image[1:-1, 1:-1], np.tile(encoded, (3, 3, 1)))
    wrapped = np.asarray(from_height(ramp, 2, True))
    np.testing.assert_array_equal(wrapped[1:-1, 1:-1], image[1:-1, 1:-1])
    assert np.all(np.any(wrapped[0] != image[0], axis=1))


def test_fix_length_and_alpha() -> None:
    pixels = np.random.default_rng(23).integers(0, 256, (37, 51, 4), dtype=np.uint8)
    result = np.asarray(fix(Image.fromarray(pixels)))
    normals = result[:, :, :3] / 127.5 - 1
    assert np.all(normals[:, :, 2] >= 0)
    assert np.max(np.abs(np.linalg.norm(normals, axis=2) - 1)) <= 1 / 127
    np.testing.assert_array_equal(result[:, :, 3], pixels[:, :, 3])


def test_two_channel() -> None:
    image = Image.new("LA", (3, 1))
    image.putdata([(128, 128), (204, 128), (255, 255)])
    output = np.asarray(fix(image))
    np.testing.assert_array_equal(output[0], [[128, 128, 255], [204, 128, 229], [218, 218, 128]])


def test_cli_tree(workspace: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    source, out = tmp_path / "source", tmp_path / "out"
    nested = source / "odd"
    nested.mkdir(parents=True)
    path = nested / "Example.PNG"
    Image.new("L", (3, 5), 12).save(path)
    args = ["normals", "from-height", str(source), str(out), "--wrap", "--strength", "3.5"]
    assert main(args) == 0
    dest = out / path.relative_to(source)
    before = dest.stat().st_mtime_ns
    assert main(args) == 0
    assert "skipped 1 identical" in capsys.readouterr().out
    assert dest.stat().st_mtime_ns == before
    # A subsequent edit replaces the complete old result.
    Image.new("RGB", (1, 3), (255, 128, 128)).save(path)
    assert main(["normals", "fix", str(source), str(out)]) == 0
    with Image.open(dest) as check:
        assert check.size == (1, 3)
    assert list(dest.parent.iterdir()) == [dest]


@pytest.mark.parametrize("name", ["wrong.jpg", "broken.png"])
def test_bad_input(
    workspace: Path, tmp_path: Path, name: str, capsys: pytest.CaptureFixture[str]
) -> None:
    source, out = tmp_path / "src", tmp_path / "out"
    source.mkdir()
    (source / name).write_bytes(b"bad image")
    assert main(["normals", "fix", str(source), str(out)]) == 1
    error = capsys.readouterr().err
    assert name in error and "PNG" in error
    assert not out.exists()


def test_input_protected(workspace: Path, tmp_path: Path) -> None:
    source = tmp_path / "src"
    source.mkdir()
    assert main(["normals", "fix", str(source), str(source / "nested")]) == 1
    assert not (source / "nested").exists()
