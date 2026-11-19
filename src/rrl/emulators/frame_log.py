"""Summarize the newline-terminated microsecond timestamps from xemu patch 0103."""

from __future__ import annotations

from collections.abc import Iterator, Sequence
from dataclasses import dataclass
from itertools import zip_longest
from pathlib import Path
from typing import BinaryIO


@dataclass(frozen=True)
class Window:
    start_s: float
    end_s: float
    flips_per_s: float
    worst_ms: float | None
    over_50_ms: int


@dataclass(frozen=True)
class Hitch:
    offset_s: float
    frame_ms: float


@dataclass(frozen=True)
class Report:
    log: str
    span_s: float
    flip_count: int
    avg_flips_per_s: float
    median_ms: float
    p95_ms: float
    p99_ms: float
    max_ms: float
    over_50_ms: int
    over_100_ms: int
    windows: list[Window]
    hitches: list[Hitch]


def _timestamps(log: Path, stream: BinaryIO) -> Iterator[int]:
    previous = 0
    for number, line in enumerate(stream, 1):
        # A partial decimal timestamp can still look like a valid integer.
        if not line.endswith(b"\n"):
            break
        digits = line.rstrip(b"\r\n")
        if not digits.isdigit() or len(digits) > 19:
            raise ValueError(f"{log}:{number}: expected a timestamp in microseconds")
        stamp = int(digits)
        if stamp < previous:
            raise ValueError(f"{log}:{number}: timestamp goes backwards")
        yield stamp
        previous = stamp


def _percentile_ms(ordered_us: list[int], percent: int) -> float:
    # Linear interpolation at (n - 1) * p. Integer ranks keep the endpoints exact.
    lower, weight = divmod((len(ordered_us) - 1) * percent, 100)
    value = ordered_us[lower]
    if weight:
        return (100 * value + weight * (ordered_us[lower + 1] - value)) / 100_000
    return value / 1_000


def summarize(log: Path, window_s: int = 10) -> Report:
    if window_s <= 0:
        raise ValueError("--window must be a positive number of seconds")
    with log.open("rb") as stream:
        return _summarize(log, _timestamps(log, stream), window_s)


def _summarize(log: Path, times: Iterator[int], window_s: int) -> Report:
    first = next(times, None)
    if first is None:
        raise ValueError(f"{log}: need at least two complete flip timestamps")
    previous = first
    window_us = window_s * 1_000_000
    durations: list[int] = []
    hitches: list[Hitch] = []
    windows: list[Window] = []
    start = count = worst = over_50 = 0

    def finish_window(end: int) -> None:
        windows.append(
            Window(
                start / 1e6,
                end / 1e6,
                count * 1e6 / (end - start),
                worst / 1000 if count else None,
                over_50,
            )
        )

    for stamp in times:
        offset = stamp - first
        delta = stamp - previous
        if offset > 1_000_000 * window_us:
            raise ValueError(f"{log}: more than a million report windows; increase --window")
        while offset > start + window_us:
            finish_window(start + window_us)
            start += window_us
            count = worst = over_50 = 0
        count += 1
        worst = max(worst, delta)
        over_50 += delta > 50_000
        if delta > 500_000:
            hitches.append(Hitch((previous - first) / 1e6, delta / 1000))
        durations.append(delta)
        previous = stamp

    if not durations:
        raise ValueError(f"{log}: need at least two complete flip timestamps")
    span = previous - first
    if not span:
        raise ValueError(f"{log}: timestamps have zero span")
    finish_window(span)
    durations.sort()
    return Report(
        log=str(log),
        span_s=span / 1e6,
        flip_count=len(durations) + 1,
        avg_flips_per_s=len(durations) * 1e6 / span,
        median_ms=_percentile_ms(durations, 50),
        p95_ms=_percentile_ms(durations, 95),
        p99_ms=_percentile_ms(durations, 99),
        max_ms=durations[-1] / 1000,
        over_50_ms=sum(d > 50_000 for d in durations),
        over_100_ms=sum(d > 100_000 for d in durations),
        windows=windows,
        hitches=hitches,
    )


def format_reports(reports: Sequence[Report]) -> str:
    """Align corresponding metrics, windows and hitches for one or two captures."""
    sections = []
    for report in reports:
        metrics = [
            f"Span (s)           {report.span_s:.6f}",
            f"Flips              {report.flip_count}",
            f"Average flips/s    {report.avg_flips_per_s:.2f}",
            f"Median (ms)        {report.median_ms:.3f}",
            f"p95 (ms)           {report.p95_ms:.3f}",
            f"p99 (ms)           {report.p99_ms:.3f}",
            f"Max (ms)           {report.max_ms:.3f}",
            f"Frames >50 ms      {report.over_50_ms}",
            f"Frames >100 ms     {report.over_100_ms}",
        ]
        windows = ["Start (s)      End (s)     Flips/s   Worst (ms)   >50 ms"]
        for w in report.windows:
            worst = "—" if w.worst_ms is None else f"{w.worst_ms:.3f}"
            windows.append(
                f"{w.start_s:9.3f}  {w.end_s:11.6f}  {w.flips_per_s:8.2f}  "
                f"{worst:>11}  {w.over_50_ms:6d}"
            )
        hitches = [f"{h.offset_s:.6f} s   {h.frame_ms:.3f} ms" for h in report.hitches]
        sections.append(
            (
                [report.log],
                metrics,
                windows,
                ["Hitches (>500 ms), offset and duration", *(hitches or ["none"])],
            )
        )
    width = max(len(line) for section in sections[0] for line in section)
    lines = []
    for group in zip(*sections, strict=True):
        for row in zip_longest(*group, fillvalue=""):
            lines.append("  |  ".join(cell.ljust(width) for cell in row).rstrip())
        lines.append("")
    return "\n".join(lines).rstrip()
