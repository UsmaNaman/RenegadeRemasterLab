from __future__ import annotations

import json
from dataclasses import asdict
from pathlib import Path

import pytest

from rrl.cli.main import main
from rrl.emulators.frame_log import Hitch, Window, summarize


def write_log(path: Path, offsets: list[int]) -> Path:
    first = 9_876_543_210_000
    path.write_bytes(b"".join(f"{first + offset}\n".encode("ascii") for offset in offsets))
    return path


def test_steady_30_fps(tmp_path: Path) -> None:
    path = write_log(tmp_path / "steady.log", [round(i * 1_000_000 / 30) for i in range(601)])
    report = summarize(path)
    assert (report.span_s, report.flip_count, report.avg_flips_per_s) == (20, 601, 30)
    assert report.median_ms == 33.333
    assert report.p95_ms == report.p99_ms == report.max_ms == 33.334
    assert report.over_50_ms == report.over_100_ms == 0
    assert report.hitches == []
    assert report.windows == [Window(0, 10, 30, 33.334, 0), Window(10, 20, 30, 33.334, 0)]


def test_report_json_with_thresholds_empty_windows_and_a_partial_tail(
    workspace: Path,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    log = write_log(
        tmp_path / "spikes.log", [0, 50_000, 150_000, 300_000, 500_000, 1_000_000, 3_500_000]
    )
    with log.open("ab") as stream:
        stream.write(b"987")  # Syntactically valid digits, but only part of the next timestamp.
    assert main(["perf", "report", str(log), "--window", "1", "--json"]) == 0
    assert json.loads(capsys.readouterr().out) == {
        "schema_version": 1,
        "window_s": 1,
        "reports": [
            {
                "log": str(log),
                "span_s": 3.5,
                "flip_count": 7,
                "avg_flips_per_s": 12 / 7,
                "median_ms": 175,
                "p95_ms": 2000,
                "p99_ms": 2400,
                "max_ms": 2500,
                "over_50_ms": 5,
                "over_100_ms": 4,
                "windows": [
                    {"start_s": 0, "end_s": 1, "flips_per_s": 5, "worst_ms": 500, "over_50_ms": 4},
                    {"start_s": 1, "end_s": 2, "flips_per_s": 0, "worst_ms": None, "over_50_ms": 0},
                    {"start_s": 2, "end_s": 3, "flips_per_s": 0, "worst_ms": None, "over_50_ms": 0},
                    {
                        "start_s": 3,
                        "end_s": 3.5,
                        "flips_per_s": 2,
                        "worst_ms": 2500,
                        "over_50_ms": 1,
                    },
                ],
                "hitches": [{"offset_s": 1, "frame_ms": 2500}],
            }
        ],
    }


def test_compare_reports_in_input_order(
    workspace: Path,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    left = write_log(tmp_path / "pack.log", [0, 50_000, 1_000_000])
    right = write_log(tmp_path / "original.log", [0, 100_000, 200_000])
    args = ["perf", "report", str(left), "--compare", str(right)]
    assert main(args) == 0
    text = capsys.readouterr().out
    assert text.splitlines()[0].split("|")[0].strip() == str(left)
    assert text.splitlines()[0].split("|")[1].strip() == str(right)
    medians = next(line for line in text.splitlines() if "Median" in line)
    assert medians.split("|")[0].rstrip().endswith("500.000")
    assert medians.split("|")[1].endswith("100.000")
    assert any(
        "0.050000 s" in line and "950.000 ms" in line and "none" in line
        for line in text.splitlines()
    )
    assert main([*args, "--json"]) == 0
    assert json.loads(capsys.readouterr().out) == {
        "schema_version": 1,
        "window_s": 10,
        "reports": [asdict(summarize(left)), asdict(summarize(right))],
    }


@pytest.mark.parametrize(
    ("data", "detail"),
    [
        (b"", "two complete"),
        (b"123\n", "two complete"),
        (b"12\n34", "two complete"),
        (b"100\n100\n", "zero span"),
        (b"12\noops\n15\n", ":2: expected"),
        (b"100\n99\n", ":2: timestamp goes backwards"),
    ],
)
def test_invalid_capture(
    data: bytes,
    detail: str,
    tmp_path: Path,
    workspace: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    log = tmp_path / "bad.log"
    log.write_bytes(data)
    assert main(["perf", "report", str(log), "--json"]) == 1
    output = capsys.readouterr()
    assert detail in output.err and output.out == ""


def test_window_and_hitch_cutoffs(tmp_path: Path) -> None:
    path = write_log(tmp_path / "thresholds.log", [0, 500_000, 1_000_001])
    assert summarize(path).hitches == [Hitch(0.5, 500.001)]
    with pytest.raises(ValueError, match="--window must be a positive"):
        summarize(path, 0)
