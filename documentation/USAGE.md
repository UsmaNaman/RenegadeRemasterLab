# Usage

## Record a play session and report frame timing

The macOS session launcher uses the local xemu build with patch 0103. It sets
`XEMU_FRAME_LOG` and writes `frames.txt` in the timestamped session directory it prints
on exit. Each complete line records one guest flip in microseconds. For a texture-pack
comparison, play the same scene twice. Start once with the pack environment variable
unset, then pass your pack directory to the launcher for the second run.

```sh
unset XEMU_TEXTURE_PACK_DIR
scripts/experiments/h2_play_session.sh
scripts/experiments/h2_play_session.sh "/path/to/your/pack"
```

Give the resulting log paths to the report command. Each run starts at its own first flip.

```sh
uv run rrl perf report original/frames.txt
uv run rrl perf report original/frames.txt --compare pack/frames.txt --window 5
uv run rrl perf report original/frames.txt --json
```

The summary gives the elapsed span, flip count and average flips/s. The first flip starts
the clock, so the rate is the number of completed intervals divided by elapsed seconds.
Frame times are differences between consecutive timestamps. Median, p95 and p99 use
linear interpolation at `(interval_count - 1) × percentile / 100` in the sorted intervals.
The summary also gives the longest frame and counts strictly above 50 and 100 ms.

Windows default to 10 seconds. Each interval counts in the window where its ending flip
arrives, including a flip exactly on the right boundary. Rates use the actual duration of
the final window. An empty window has zero flips/s and no worst frame. Hitches over 500 ms
are listed with their duration and the time at which the wait began. The parser ignores
an unterminated last line, since a partially written timestamp can still look like a number.
It needs at least two complete timestamps spanning a positive duration.

`--compare` places the two reports side by side. With `--json`, schema version 1 returns
`schema_version`, `window_s` and a `reports` array in command-line order. Each report
includes summary values, `windows` and `hitches`. Time fields ending in `_s` use seconds
and those ending in `_ms` use milliseconds. An empty window has `"worst_ms": null`.
