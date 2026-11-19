# Task briefs for code-only agents (Codex)

Each task is self-contained and needs no game data. Rules: `AGENTS.md`. One task per branch
(`codex/<id>`); the lead reviews before merge. Status: open unless marked otherwise.

## C1: README and usage guide (done, merged 2026-11-18)
- **Goal:** a README a new user can follow: what RRL is, what it does today, install, a walkthrough.
- **Files:** `README.md`; optionally a new `documentation/USAGE.md`.
- **Sources of truth:** `rrl --help` and each subcommand's `--help`, `ROADMAP.md`, `documentation/`
  (platforms/halo2_xbox.md, TECHNICAL_DECISIONS.md), `LEGAL.md`.
- **Must include:** the code-only/no-assets policy; "bring your own legally owned disc"; supported
  formats and known limits (copy them from the docs, mark nothing as working that the docs do not);
  the walkthrough `rrl doctor` → `rrl source add` → `rrl identify`/`inspect` → `rrl extract --only
  <maps> textures models levels animations --export DIR` → opening .glb in Blender
  (`scripts/blender/halo2_lightmaps.py`).
- **Never:** use the word Halo in a title, heading-as-branding or logo; only descriptively.
- **Done when:** every command shown exists with those flags (check `--help`), and the doc checks pass.

## C2: Packaging and clean install (done, merged 2026-11-18)
- **Goal:** `pipx install git+<repo>` and `uv tool install .` both give a working `rrl`.
- **Files:** `pyproject.toml`, `CHANGELOG.md`, possibly `src/rrl/__init__.py` (version source).
- **Do:** set version `0.1.0a1`; check extras (`packs` etc.) are optional; check package data (if any)
  is included; add a test that the installed entry point runs `rrl --help` and `rrl doctor`
  (doctor must not need a disc).
- **Done when:** a fresh venv install from a built wheel runs `rrl --help`; report the wheel contents.

## C3: CI on Linux and Windows (done, merged 2026-11-18)
- **Goal:** the test suite passes on ubuntu-latest, windows-latest and macos-latest, Python 3.12 and 3.13.
- **Files:** `.github/workflows/ci.yml`; source/test fixes for portability only (paths, line endings,
  file locking, `os.sep`).
- **Note:** the repo has no remote yet; validate the workflow syntax (e.g. `actionlint` if available
  via uv/pipx, else careful review) and make the code portable. Report anything you could not run.
- **Never:** skip or delete tests to make them pass; mark a skip only with a stated reason.

## C4: Release audit script (done, merged 2026-11-18)
- **Goal:** `scripts/release_audit.py`, a read-only check over the whole git history (all commits,
  not just HEAD) before going public.
- **Reports:** absolute home paths (`/Users/<name>`), e-mail addresses, files whose bytes match the
  game-data magic checks in `scripts/check_no_game_data.py` (reuse it), binaries over 1 MB, secrets
  patterns (keys, tokens). Output: a table per finding with commit, path, line; exit 1 on findings.
- **Tests:** synthetic temporary git repos built in the test.
- **Never:** rewrite history or change existing files' content outside the script and its test.

## C5: `rrl inject` command (done, merged 2026-11-18)
- **Goal:** CLI over `rrl.adapters.xbox_halo2.inject.inject_texture`:
  `rrl inject <source> --map M --tag T --index I --image PNG --out COPY.iso`.
- **Behaviour:** refuse if `--out` exists or equals the source path; make the copy (APFS clone via
  `cp -c` on macOS when available, else a normal copy), inject, run inside
  `store.provenance.operation(...)` with the parameters, report the Injection result. Never open the
  source for writing.
- **Files:** `src/rrl/cli/main.py` (+ a small helper module if needed), tests under `tests/unit/`
  using the synthetic disc from `tests/unit/test_halo2_inject.py`.
- **Done when:** tests cover success, existing `--out`, `--out` == source, wrong image size (no copy
  left behind or clearly reported), and provenance recorded.

## C6: ISO9660 container + PlayStation 1/2 identify/inspect adapters (large) (done, merged 2026-11-18)
- **Goal:** Phase 5/6 groundwork. RRL can register, identify and inspect PS1 and PS2 disc images,
  read-only, exactly like the Xbox path (`containers/xdvdfs.py`, `adapters/xbox_original/`).
- **Research first (public specs only, cite URLs in the docs):** ECMA-119 (ISO9660) volume descriptors,
  path table/directory records, both-endian fields; Joliet not needed. CD sector modes: 2048-byte ISO
  sectors, 2352-byte raw sectors (Mode 1 and Mode 2 Form 1/XA, 16-byte sync+header, 8-byte subheader
  for Mode 2), and `.cue` sheets naming `.bin` tracks (track 1 data only). PS2 DVDs are plain 2048-byte
  ISO9660 (UDF bridge can be ignored). `SYSTEM.CNF`: PS1 `BOOT = cdrom:\\SLUS_xxx.xx;1`, PS2
  `BOOT2 = cdrom0:\\SLUS_xxx.xx;1` plus `VER`, `VMODE`. Serial prefixes -> region (SLUS/SCUS = NTSC-U,
  SLES/SCES = PAL, SLPS/SLPM/SCPS = NTSC-J; document the table as [M] unless sourced).
- **Build:**
  1. `src/rrl/containers/iso9660.py`: `Iso9660Image` with the same shape as `XdvdfsImage`
     (`entries()`, `open(path)`, `locate(path)` returning byte extents in the *image file*), sector
     layout detection (2048 / 2352 Mode 1 / 2352 Mode 2), and a `.cue` resolver. Bounds-check every
     field; malformed input raises a clear error; directory loops and absurd sizes cannot hang.
  2. `src/rrl/adapters/sony_ps1/` and `src/rrl/adapters/sony_ps2/`: `identify` (platform, serial as
     title id, region, title from serial if no name source) and `inspect` (layout, sector mode, file
     list summary, SYSTEM.CNF fields, boot executable size). Declare only the capabilities implemented.
     Register them in `pyproject.toml` under `rrl.adapters` (`sony.ps1`, `sony.ps2`).
  3. Make sure `rrl source add`, `rrl identify` and `rrl inspect` work with them (check `rrl/cli`).
  4. `documentation/platforms/ps1.md` and `ps2.md` with [V]/[M] facts and URLs.
- **Tests (synthetic only):** an in-code ISO9660 builder in `tests/fixtures/` (like `xbox_builder.py`)
  producing 2048-byte images, 2352-byte Mode 1 and Mode 2 raw images, and a cue+bin pair; nested
  directories; files spanning many sectors; both PS1 and PS2 SYSTEM.CNF variants; malformed cases
  (bad descriptor, extent past end, directory cycle, truncated cue) raise clear errors; the registry
  contract test accepts both adapters; the Xbox adapters still win on Xbox images and do not claim
  PS discs (and vice versa).
- **Never:** read any real disc image or file outside the repo; add dependencies (stdlib + struct);
  touch the Xbox/Halo 2 code beyond registry wiring.
- **Done when:** all AGENTS.md checks pass and `rrl identify`/`rrl inspect` on the synthetic PS1 and
  PS2 images (in a test) print the expected platform, serial and region.

## C7: Blender toolkit for RRL exports (large) (done, merged 2026-11-19)
- **Goal:** one headless-capable Blender importer that turns any RRL .glb (model, level, first-person
  scene) into a ready-to-use scene, replacing the ad-hoc `scripts/blender/halo2_lightmaps.py`.
- **Allowed:** running Blender (`blender -b --python ...`, /Applications/Blender.app, 5.x) on
  **synthetic** .glb files your tests generate with `rrl.processors.gltf.Gltf` and the existing test
  fixtures. Still never open real exports, `~/RRL-Workspace` or game data.
- **Inputs to support** (read the producers to match exactly: `adapters/xbox_halo2/render_model.py`,
  `structure_bsp.py`, `animation.py`, `documentation/platforms/halo2_xbox.md`):
  node extras `lightmap_uri` + TEXCOORD_1; material `alphaMode` MASK/BLEND; material extras
  (`template`, `bitmaps`, `diffuse_source`); nodes named `region:permutation`; animation extras
  (`type` base/replacement, `frames`, `fps`, `root_motion` {frame_info, deltas}).
- **Build** `scripts/blender/rrl_import.py` (plain bpy script, optionally also an add-on wrapper):
  1. Import the .glb; lightmaps as today (object-linked material copies, diffuse x lightmap), but keep
     alpha: MASK -> alpha clip, BLEND -> blended, via the diffuse alpha.
  2. Variants: one collection per region; only the first permutation of each region visible.
  3. Animations: every clip as an NLA strip on its armature (muted except one chosen with `--clip`);
     replacement clips on a second NLA track with blend type REPLACE; optional `--root-motion` bakes
     the extras deltas onto the armature object (dx forward, dy left, dz up, dyaw radians; wrap dyaw to
     (-pi, pi]; per halo2_xbox.md) and documents the [M] accumulation order.
  4. `--textures DIR`: relink every image whose relative path exists under DIR (for upscaled PNGs),
     reporting how many were swapped.
  5. `--preview OUT.png`: frame the visible geometry and render one Workbench texture still (and
     `--turntable N` for N frames).
  6. Print a JSON report (objects, materials, images relinked, clips, warnings) as the last stdout line.
- **Tests:** `tests/blender/` run Blender headless on synthetic glbs and assert on the JSON report;
  skip cleanly (pytest.skip with reason) when no `blender` executable is found, so CI still passes.
- **Never:** change the exporters' output format (if something is missing from the glb, report it);
  add Python dependencies.
- **Done when:** all AGENTS.md checks pass, the Blender tests pass locally, and `documentation/USAGE.md`
  (or README) documents the importer's flags.

## C8: `rrl upscale` command (promote the upscale experiment) (done, merged 2026-11-19)
- **Goal:** turn `scripts/experiments/h2_upscale_export.py` into a real CLI step that upscales any
  `rrl extract --export` tree (all maps, not only the xemu-packable subset), so the Blender importer's
  `--textures` and the pack/inject steps get full coverage.
- **CLI:** `rrl upscale <source> --export DIR --out DIR [--scale 2|4] [--model NAME] [--only PREFIX...]
  [--packable-only] [--max-side N]`. Backend: the `realesrgan-ncnn-vulkan` executable, found via
  `--upscaler PATH`, `$RRL_UPSCALER`, or `rrl.toml` (document it in `rrl doctor` output as optional).
- **Behaviour (keep from the experiment):** resumable (skip finished outputs), each unique texture
  upscaled once (dedupe by blob sha256 via the artifact table), mirrored relative paths, `--max-side`
  fallback to 2x. Add: alpha preserved (upscale RGB and alpha separately, or verify the backend keeps
  alpha; never drop it); skip kinds that must not be AI-upscaled: bump/normal (`p8_bump`, `v8u8`,
  `g8b8`), lightmaps (P8 from ltmp) and 3D slices, listing what was skipped and why; run inside
  `store.provenance.operation(...)` and record outputs as `enhanced` artifacts (blob store) with the
  source artifact, scale and model in metadata.
- **Files:** a new module (e.g. `src/rrl/processors/upscale.py` for the backend runner, platform-agnostic),
  `src/rrl/cli/main.py`, tests. Leave the experiment script in place.
- **Tests (synthetic):** a fake upscaler executable written by the test (a tiny Python script that
  resizes with Pillow) so no real model or GPU is needed; cover resume, dedupe, alpha kept, skip
  kinds, max-side fallback, missing backend (clear error), provenance and artifact rows.
- **Never:** download models or binaries; run the real upscaler on real data; read outside the repo.
- **Done when:** all AGENTS.md checks pass and README/USAGE documents the command.

## C9: batch texture injection (`rrl inject --from DIR`) (done, merged 2026-11-19)
- **Goal:** inject a whole edited export tree into ONE disc copy, the disc-side twin of `rrl pack build`.
- **CLI:** `rrl inject <source> --from DIR --out COPY.iso [--only PREFIX...] [--resize]`. For every PNG
  in DIR whose relative path matches an extracted texture artifact of that source (as `pack build` maps
  them), inject it into the right map/tag/index. `--resize` scales an image to the bitmap's exact size
  (e.g. 4x upscales back to native: supersampled cleanup); without it, wrong sizes are skipped.
- **Behaviour:** one copy, all writes, then re-sign each touched map ONCE (refactor `inject_texture` so
  per-texture writes and the final re-sign/validation can be batched; keep the single-texture API and
  its tests working). Skip non-injectable formats with reasons; report counts per map; the copy is only
  published (hard link, as C5 does) after every map validates. Provenance as C5.
- **Tests (synthetic disc):** several bitmaps across two maps (one raw in a shared map), resize on/off,
  skips, one re-sign per map, failure leaves no output, single-texture `rrl inject` unchanged.
- **Never:** touch the source; read outside the repo.
- **Done when:** all AGENTS.md checks pass and README/USAGE documents the command.

## C10: inject DXT1-with-alpha and uncompressed textures (large) (done, merged 2026-11-19)
- **Goal:** close the two skip reasons left by C9's real run (mainmenu: 19 "image has alpha; DXT1 here is
  opaque only", 11 "2d a8r8g8b8 is not injectable yet"), so a batch injection covers them too.
- **Read first:** `src/rrl/processors/textures/__init__.py` (decoders, `deswizzle_morton`/`deswizzle_box`,
  `bytes_needed`, `encode_mip_chain`, `_dxt3_from_dxt5`), `src/rrl/adapters/xbox_halo2/inject.py`
  (`DiscInjector`), `src/rrl/adapters/xbox_halo2/__init__.py` (`_convert`, `LINEAR_PITCH`: how each format
  is decoded, swizzled vs linear, pitch), `documentation/platforms/halo2_xbox.md` (Bitmaps).
- **Part A, DXT1 1-bit alpha:** encode blocks that contain transparent pixels (alpha < 128) in DXT1's
  3-colour + transparent mode (c0 <= c1, index 3 = transparent black); opaque blocks stay 4-colour (keep
  Pillow for those or write the encoder yourself, numpy-vectorised). Remove the DXT1 alpha refusal.
  Decode-back must keep alpha exactly at the 128 threshold and RGB close on opaque texels.
- **Part B, uncompressed formats:** inject at least `a8r8g8b8` and `x8r8g8b8`, and any of
  `r5g6b5`, `a1r5g5b5`, `a4r4g4b4`, `a8`, `y8`, `ay8`, `a8y8` that are simple to add. Each mip level is
  packed in the format's byte order (match the decoder exactly) and, for swizzled bitmaps, swizzled per
  level with a new `swizzle_morton` that is the exact inverse of `deswizzle_morton`; linear bitmaps use the
  row pitch the extractor uses. Same chain-fit and 128-byte padding rules as DXT. Never touch
  `p8`, `p8_bump`, `v8u8`, `g8b8`, 3D or cube bitmaps (keep them skipped with reasons).
- **Files:** `src/rrl/processors/textures/__init__.py`, `src/rrl/adapters/xbox_halo2/inject.py`, tests in
  `tests/unit/` (extend `test_textures.py` and `test_halo2_inject.py`), `documentation/platforms/halo2_xbox.md`
  (mark what is [V] by test, [M] otherwise).
- **Tests (synthetic):** swizzle/deswizzle exact round trip for square and non-square power-of-two sizes;
  every new format encode -> decode round trip; DXT1 alpha blocks (mixed opaque/transparent, fully
  transparent, fully opaque) decode with correct alpha; a synthetic disc batch that injects one DXT1-alpha
  and one swizzled A8R8G8B8 bitmap with mips and validates; existing tests unchanged.
- **Never:** change the extractor's output; add dependencies; read outside the repo.
- **Done when:** all AGENTS.md checks pass and the docs list the newly injectable formats.

## C11: `rrl video remaster` (promote the Bink experiment) (done, merged 2026-11-19)
- **Goal:** a resumable CLI for cleaning and upscaling pre-rendered game video (FMV) into a watchable MP4.
  The experiment it replaces is a shell script: ffmpeg decode with `bwdif=mode=send_frame:deint=interlaced`
  (deinterlace only combed frames) -> Real-ESRGAN 4x on PNG frames -> ffmpeg encode (HEVC
  `hevc_videotoolbox` on macOS, `libx265` fallback) with the source audio re-muxed. A full movie takes hours,
  so an interrupted run must resume at the first missing upscaled frame, not restart.
- **CLI:** `rrl video remaster INPUT --out OUT.mp4 [--model realesrgan-x4plus] [--scale 2|4]
  [--no-deinterlace] [--keep-frames] [--upscaler PATH] [--ffmpeg PATH]`. INPUT is any file ffmpeg reads.
  Reuse `processors/upscale.py`'s backend lookup (`[tools] upscaler`), add `[tools] ffmpeg` the same way
  (default: `ffmpeg` on PATH). Work dir next to OUT (`OUT.frames/in`, `OUT.frames/out`); delete it on
  success unless `--keep-frames`. Write OUT via a temp name, then rename.
- **Placement:** platform-agnostic code in `src/rrl/processors/video.py`; CLI wiring in `cli/main.py`;
  record a provenance `operation("video-remaster", params=...)` without a source id when INPUT is not
  a registered source.
- **Tests (synthetic, no game data):** generate a tiny clip with ffmpeg `testsrc`/`sine` in tmp (skip if
  ffmpeg is absent); fake upscaler (a Python script that resizes PNGs with Pillow, like the C8 tests);
  check frame count, output size, audio stream present, resume skips frames already upscaled, failure
  leaves no OUT.
- **Never:** read outside the repo; add Python dependencies; touch `.bik` or other game files in tests.
- **Done when:** all AGENTS.md checks pass and README/USAGE documents the command and its time cost.

## C12: `rrl perf report` (frame-timing log summary) (done, merged 2026-11-19)
- **Goal:** turn the xemu frame log into stutter numbers. Patch 0103 writes one line per guest flip: a
  monotonic timestamp in **microseconds** (`XEMU_FRAME_LOG=<file>`). The output may lose its last few
  lines because the file is block-buffered, so tolerate a truncated final line.
- **CLI:** `rrl perf report LOG [--window 10] [--json]`. Print: span, flip count, average flips/s, median /
  p95 / p99 / max frame time (ms), counts over 50 ms and 100 ms, and a per-window table (flips/s, worst
  frame, >50 ms count). Flag "hitches" (frames > 500 ms) with their time offset. Optional
  `--compare OTHER.log` prints both summaries side by side (for pack vs no-pack runs).
- **Placement:** `src/rrl/emulators/frame_log.py` plus CLI wiring; stdlib only.
- **Tests:** synthetic logs (steady 30 fps, injected spikes, truncated last line, empty/one-line file ->
  clear error), percentiles and windows checked exactly; `--json` schema stable.
- **Never:** read outside the repo; depend on a real session log.
- **Done when:** all AGENTS.md checks pass and USAGE documents it next to the launcher workflow.

## C13: `scripts/publish.py` (safe updates to the public repo) (done, merged 2026-11-19)
- **Goal:** the public repo (`origin`, branch `main`) is one squashed history authored by the GitHub
  no-reply address. Local `master` has a private author email in every commit and must never be pushed.
  Make publishing new `master` work a single safe command.
- **Behaviour:** `python scripts/publish.py [--message MSG] [--dry-run] [--push]`:
  1. Fetch `origin/main`; refuse if the working tree is dirty or `origin/main`'s tree is not an ancestor
     state of `master` it can diff against (clear error).
  2. Build one new commit with `git commit-tree master^{tree} -p origin/main`, author and committer from
     `--identity` (default: read `rrl.publishIdentity` from `git config`, format `Name <email>`). Refuse
     unless the email ends in `@users.noreply.github.com`.
  3. Point local branch `release` at it (create if missing). Never touch `master`.
  4. Run the release audit (`scripts/release_audit.py`) restricted to `release` (add a `--ref` option to
     the audit if it lacks one, keeping its current default behaviour). Any `email` finding that is not a
     `noreply` address, `you@example.com`, or a `From:` line under `patches/xemu/` fails the publish.
  5. Only with `--push`: `git push origin release:refs/heads/main` (never `--force`, never other refs).
     `--dry-run` prints the plan and audit result and creates nothing.
  6. Optional `--tag vX.Y.Z` makes an annotated tag on the new commit with the same identity and pushes
     only that tag.
- **Also:** a `.githooks/pre-push` script (documented, opt-in via `git config core.hooksPath`) that
  rejects pushing any commit whose author or committer email is not a noreply address.
- **Tests:** `tests/tooling/test_publish.py` with throwaway repos in `tmp_path` and a bare repo as
  `origin`: happy path (one commit on top of origin/main, tree equals master), non-noreply identity
  refused, dirty tree refused, audit failure blocks the push, `--dry-run` creates no refs, tag identity,
  pre-push hook rejects a private-email commit. Use fake addresses only (built at runtime, never literal in docs).
- **Never:** push anywhere in tests except the temp bare repo; read the real repo's remote; use force.
- **Done when:** all AGENTS.md checks pass; CONTRIBUTING.md has a short "Publishing" section.

## C14: normal-map processor (`rrl normals`) (done, merged 2026-11-19)
- **Goal:** `rrl upscale` skips bump/normal maps (`_skip_reason` in `processors/upscale.py`) because a
  generic upscaler breaks unit-length normals. Add a processor that makes them usable at high resolution.
- **CLI:** `rrl normals fix IN_DIR OUT_DIR` and `rrl normals from-height IN_DIR OUT_DIR [--strength 2.0]
  [--wrap]`. Mirrors the relative PNG tree, skips existing identical outputs, writes atomically.
  - `fix`: decode RGB as `n = rgb/127.5 - 1`, renormalize to unit length (z >= 0, rebuild z from x,y if
    the input is two-channel), re-encode. For maps upscaled outside RRL.
  - `from-height`: luminance as height, Sobel gradients scaled by `--strength`, tangent-space normal
    (OpenGL convention, +Y up; document it), `--wrap` for tiling textures (wrap edges instead of clamp).
- **Placement:** `src/rrl/processors/normals.py` (numpy + Pillow, already dependencies; no platform
  code) and CLI wiring. Leave `rrl upscale` behaviour unchanged.
- **Tests:** synthetic images only: flat height -> (128,128,255); a linear ramp gives the exact expected
  constant normal; `fix` output has |n| within 1/127 of 1 for random inputs; wrap vs clamp differ only at
  edges; two-channel input; odd sizes; non-PNG and malformed files give clear errors.
- **Never:** read outside the repo; touch adapters.
- **Done when:** all AGENTS.md checks pass; USAGE documents both subcommands and the Blender use.

## C15: PCSX2 texture-pack writer (PS2 groundwork) (done, merged 2026-11-19)
- **Goal:** the PS2 counterpart of `rrl pack build`. PCSX2 loads replacement textures from
  `textures/<SERIAL>/replacements/` with filenames derived from its texture hash (research PCSX2's
  documented dump/replace naming, e.g. `<hash>-<format>.png` / `.dds`, and cite the source URL in
  `documentation/platforms/ps2.md`, marked [V] only if verified from PCSX2 source or docs).
- **CLI:** `rrl pack pcsx2 DUMP_DIR IMAGES_DIR PACK_DIR --serial SLUS-12345`. Input is a PCSX2 *dump*
  folder (the user dumps textures in PCSX2 themselves) and a folder of edited/upscaled PNGs with the same
  filenames. Output: the replacement folder layout PCSX2 expects, PNGs copied or optionally re-encoded as
  DDS (BC7 only if a stdlib/numpy path exists; otherwise PNG only, and say so). Report missing/extra/
  size-mismatched files; never modify the dump folder. Serial validated against the PS2 serial pattern.
- **Reuse:** `rrl upscale` needs a registered source today. Add a plain folder mode,
  `rrl upscale --pngs IN OUT [--scale] [--model] [--max-side]`, reusing `upscale_batch`, so the PS2 flow
  is dump -> upscale -> pack. Existing source-mode behaviour and tests stay unchanged.
- **Placement:** `src/rrl/emulators/pcsx2_pack.py` plus CLI wiring.
- **Tests:** synthetic dump folders with fake hash names: layout, serial validation, missing/extra
  reporting, dump folder unchanged (hash it before/after), filename edge cases (path traversal names
  rejected).
- **Never:** read outside the repo; ship or fetch real dumps; download PCSX2.
- **Done when:** all AGENTS.md checks pass; USAGE has a short PS2 section (dump in PCSX2 -> upscale ->
  pack -> enable "Load Textures" in PCSX2).
