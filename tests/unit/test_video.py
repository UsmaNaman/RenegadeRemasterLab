from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from contextlib import closing
from pathlib import Path

import pytest

from rrl import db
from rrl.cli.main import main
from rrl.processors import video


@pytest.fixture
def ffmpeg() -> str:
    tool = shutil.which("ffmpeg")
    if tool is None:
        pytest.skip("ffmpeg is required to generate and inspect the synthetic movie")
    return tool


@pytest.fixture
def movie(tmp_path: Path, ffmpeg: str) -> Path:
    path = tmp_path / "colour bars.mp4"
    subprocess.run(
        [
            ffmpeg,
            "-loglevel",
            "error",
            "-f",
            "lavfi",
            "-i",
            "testsrc=s=64x48:r=24000/1001",
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=330",
            "-t",
            "0.25",
            "-c:v",
            "mpeg4",
            "-c:a",
            "aac",
            str(path),
        ],
        check=True,
    )
    return path


@pytest.fixture
def backend(tmp_path: Path) -> Path:
    script = tmp_path / "backend.py"
    script.write_text(
        """import sys
from pathlib import Path
from PIL import Image

root = Path(__file__).parent
with (root / "calls").open("a") as log:
    print("upscale", file=log)
if (root / "interrupt").exists() and len((root / "calls").read_text().splitlines()) == 3:
    sys.exit(1)
src = Path(sys.argv[sys.argv.index("-i") + 1])
dest = Path(sys.argv[sys.argv.index("-o") + 1])
for frame in src.glob("*.png"):
    with Image.open(frame) as im:
        im.resize((4 * im.width, 4 * im.height)).save(dest / frame.name)
""",
        encoding="utf-8",
    )
    if os.name == "nt":
        tool = tmp_path / "upscale.cmd"
        tool.write_text(f'@"{sys.executable}" "%~dp0backend.py" %*\n')
    else:
        tool = tmp_path / "upscale"
        tool.write_text(f'#!/bin/sh\nexec "{sys.executable}" "{script}" "$@"\n')
        tool.chmod(0o755)
    return tool


def packets(ffmpeg: str, path: Path, stream: str) -> list[str]:
    proc = subprocess.run(
        [
            ffmpeg,
            "-loglevel",
            "error",
            "-i",
            str(path),
            "-map",
            f"0:{stream}:0",
            "-c",
            "copy",
            "-f",
            "framecrc",
            "-",
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    return proc.stdout.splitlines()


def test_interrupted_movie_resumes(
    tmp_path: Path,
    workspace: Path,
    movie: Path,
    ffmpeg: str,
    backend: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace.mkdir()
    for name in ("RRL_FFMPEG", "RRL_UPSCALER"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(
        "rrl.processors.video.FRAMES_PER_BATCH", 1
    )  # one launch per frame: exact resume points
    (workspace / "rrl.toml").write_text(f"[tools]\nffmpeg = '{ffmpeg}'\nupscaler = '{backend}'\n")
    out = tmp_path / "movie.mp4"
    args = ["video", "remaster", str(movie), "--out", str(out), "--scale", "2"]
    interrupt = tmp_path / "interrupt"
    interrupt.touch()
    assert main(args) == 1
    work = out.with_suffix(".frames")
    frames = sorted((work / "out").glob("*.png"))
    assert len(frames) == 2 and not out.exists()
    stamps = [f.stat().st_mtime_ns for f in frames]
    interrupt.unlink()
    assert main([*args, "--scale", "4"]) == 1  # Incompatible cache stays intact.
    assert main(["source", "add", str(movie)]) == 0
    assert main([*args, "--keep-frames"]) == 0
    assert [f.stat().st_mtime_ns for f in frames] == stamps
    count = 6
    assert len((tmp_path / "calls").read_text().splitlines()) == count + 1
    video = packets(ffmpeg, out, "v")
    assert "#dimensions 0: 128x96" in video
    assert len([line for line in video if not line.startswith("#")]) == count
    assert packets(ffmpeg, out, "a") == packets(ffmpeg, movie, "a")
    # Decode at the source cadence; duplicating or dropping frames would change the byte count.
    raw = subprocess.check_output(
        [
            ffmpeg,
            "-loglevel",
            "error",
            "-i",
            str(out),
            "-vf",
            "fps=24000/1001",
            "-f",
            "rawvideo",
            "-pix_fmt",
            "rgb24",
            "-an",
            "-",
        ]
    )
    assert len(raw) == 128 * 96 * 3 * count
    with closing(db.connect(workspace / "rrl.db")) as conn:
        ops = conn.execute(
            "SELECT * FROM operation WHERE op='video-remaster' ORDER BY id"
        ).fetchall()
        assert [(r["source_id"], r["status"]) for r in ops] == [
            (None, "failed"),
            (None, "failed"),
            (1, "succeeded"),
        ]
        assert json.loads(ops[-1]["params_json"])["scale"] == 2
    assert main(args) == 0
    assert not work.exists()
    assert len((tmp_path / "calls").read_text().splitlines()) == count + 1
    monkeypatch.setattr("rrl.processors.video.FRAMES_PER_BATCH", 4)
    out.unlink()
    assert main(args) == 0
    assert (
        len((tmp_path / "calls").read_text().splitlines()) == count + 1 + 2
    )  # 6 frames, 2 launches


def test_movie_rejects_foreign_frames_and_bad_input(
    tmp_path: Path,
    workspace: Path,
    movie: Path,
    ffmpeg: str,
    backend: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    out = tmp_path / "unpublished.mp4"
    args = [
        "video",
        "remaster",
        str(movie),
        "--out",
        str(out),
        "--upscaler",
        str(backend),
        "--no-deinterlace",
    ]
    work = out.with_suffix(".frames")
    work.mkdir()
    marker = work / "my-file"
    marker.touch()
    assert main([*args, "--ffmpeg", ffmpeg]) == 1
    assert marker.exists() and not out.exists()
    monkeypatch.setenv("RRL_FFMPEG", str(tmp_path / "missing-ffmpeg"))
    assert main(args) == 1
    marker.unlink()
    work.rmdir()
    # A decode error must not publish an output either.
    movie.write_bytes(b"invalid synthetic input")
    assert main([*args, "--ffmpeg", ffmpeg]) == 1
    assert not out.exists()


def test_encode_failure_then_software_hevc_and_incompatible_audio(
    tmp_path: Path,
    workspace: Path,
    movie: Path,
    ffmpeg: str,
    backend: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = tmp_path / "clip.avi"
    subprocess.run(
        [
            ffmpeg,
            "-loglevel",
            "error",
            "-i",
            str(movie),
            "-c:v",
            "copy",
            "-c:a",
            "adpcm_ima_wav",
            str(source),
        ],
        check=True,
    )
    out = tmp_path / "clip.mp4"
    args = [
        "video",
        "remaster",
        str(source),
        "--out",
        str(out),
        "--upscaler",
        str(backend),
        "--ffmpeg",
        ffmpeg,
        "--no-deinterlace",
    ]
    real_ffmpeg = video._ffmpeg
    fail_software = True

    def encode(tool: Path, args: list[str], cwd: Path) -> bytes:
        if "hevc_videotoolbox" in args or (fail_software and "libx265" in args):
            raise ValueError("synthetic encoder failure")
        return real_ffmpeg(tool, args, cwd)

    monkeypatch.setattr(video, "_ffmpeg", encode)
    assert main(args) == 1
    assert not out.exists()
    calls = (tmp_path / "calls").read_bytes()
    fail_software = False
    assert main(args) == 0
    assert (tmp_path / "calls").read_bytes() == calls
    assert "#codec_id 0: aac" in packets(ffmpeg, out, "a")
    assert "#codec_id 0: hevc" in packets(ffmpeg, out, "v")
    assert "#dimensions 0: 256x192" in packets(ffmpeg, out, "v")
