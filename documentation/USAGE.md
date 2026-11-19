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

## Prepare normal maps for Blender

Use a PNG-only input tree and a separate output directory. Relative paths are preserved.
Identical generated files keep their timestamps. Changed maps replace their previous output atomically.

```sh
uv run rrl normals fix edited-normals fixed-normals
uv run rrl normals from-height height-maps generated-normals --strength 2.0 --wrap
```

`fix` restores unit length after external editing or upscaling. RGB and RGBA encode XYZ
on the outward hemisphere. Negative Z is clamped to zero and RGBA keeps its alpha.
For a two-channel grayscale-plus-alpha PNG, the two channels encode X and Y. RRL
reconstructs positive Z and writes RGB.

`from-height` uses normalized luminance and a Sobel derivative per input pixel. Strength
scales the slope. Edges clamp by default. Use `--wrap` for a tiling height map.
The result uses OpenGL tangent space (+Y up). In Blender, set the Image Texture to
**Non-Color**, connect its Color to a **Normal Map** node set to **Tangent Space**,
and connect that node's Normal output to the shader's Normal input.

## Build a PCSX2 texture pack

1. In PCSX2, enable texture dumping and play the scenes you want to edit. Keep the
   original dump folder. Use the game serial shown by PCSX2 (for example, `SLUS-12345`).
2. Edit copies of the dumped PNGs or run the folder upscaler. It uses the same backend,
   alpha handling, model and side-length settings as source-based upscaling.

   ```sh
   uv run rrl upscale --pngs dump edited --scale 4 --model realesrgan-x4plus --max-side 4096
   ```

3. Point the pack command at the flat dump and edited folders. Use the PCSX2 data
   directory as the pack root, or build into an empty staging directory and copy its
   `textures` tree into the PCSX2 data directory.

   ```sh
   uv run rrl pack pcsx2 dump edited my-pack --serial SLUS-12345
   ```

   This copies matching PNGs to `my-pack/textures/SLUS-12345/replacements/`. Keep
   the exact dumped filenames. Images may be native size or uniformly enlarged by
   an integer factor. The JSON report names missing edits, extra edits and incompatible
   sizes. Each matching replacement is updated individually. Existing unrelated files
   are retained, so use an empty staging directory for a pack containing only this run.
   RRL writes PNG only.
4. Disable dumping, enable **Load Textures** in PCSX2's texture replacement settings
   and revisit the scene.
