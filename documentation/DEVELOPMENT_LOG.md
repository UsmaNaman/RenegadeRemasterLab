# Development log

Newest first. Each entry covers what was done, what was decided, and what is next.

## 2026-11-19: C12: guest frame-timing reports
- **CLI.** `rrl perf report` summarizes flip timing, windows and hitches from patch 0103. Two captures can be compared side by side or exported together as versioned JSON. USAGE documents the launcher and report workflow.
- **Decisions.** Discard an unterminated final record. Compute rates from completed intervals, interpolate percentiles and assign each interval to the window of its ending flip. Hitch offsets identify the beginning of the wait.
- **Checks.** All 436 tests passed on macOS. Synthetic logs verified steady 30 fps, interpolation, exact thresholds, empty and partial windows, truncated records, invalid input and the JSON schema. Offline dependency sync, Ruff lint and format, mypy and the game-data check passed.
- **Open.** A live pack versus original session comparison remains unverified.
- **Review (lead).** On the real 196 s pack session log the report matched a hand analysis exactly. `--compare` and `--json` worked on two real logs.

## 2026-11-19: C11: resumable video remastering
- **CLI.** `rrl video remaster` deinterlaces flagged frames, upscales PNGs, and publishes an HEVC MP4 with source audio. The README documents tool lookup, runtime and resume.
- **Decisions.** Checkpoint each validated PNG. Match the input hash and enhancement settings on resume. Copy compatible audio streams and encode AAC when MP4 rejects the source codec.
- **Checks.** All 429 tests passed on macOS, including a synthetic interrupted encode workflow, fractional frame rate, audio packet comparison and software HEVC fallback. Offline dependency sync, Ruff lint and format, mypy and the game-data check passed.
- **Open.** Real-model throughput and visual quality on full-length FMVs remain unverified.
- **Review (lead).** Ran on the real Halo 2 `intro_60.bik`. The run was interrupted at 43 of 557 frames, then resumed to a 2560x1920 HEVC MP4 with audio and no combing. Per-frame backend launches reloaded the model each time, about 1 s each, so frames now go in batches of 32 (`FRAMES_PER_BATCH`).

## 2026-11-19: whole-game texture pack play test; Bink videos assessed
- **Same-size injection limit.** The C10 `--resize` batch looked almost unchanged in play: textures go back at native size. Real enhancement uses the ADR-0009 texture pack.
- **Whole-game pack.** `rrl upscale --packable-only` over all maps produced 2,302 unique 4x textures (17,135 PNGs). `rrl pack build` packed 2,313 textures (2.8 GB) with none missing. The pack plays in xemu-vulkan-dev on the original ISO.
- **Frame log.** The 196 s capture has a median frame of 38.6 ms (about 26 fps) and a 99th percentile of 83 ms. Four 0.9 to 2.4 s hitches fall in the first 80 s, likely first-use loads. The Cairo boarding scene drops to about 13 fps. No A/B run without the pack yet, so the pack's share of the cost is unknown.
- **Bink videos.** The disc holds only intro, attract and credits `.bik` files: 640x480, 29.97 fps, about 30% of frames combed. Story cutscenes are in-engine. A deinterlace + x4plus + HEVC script exists; the owner chose x4plus over animevideov3. The work is paused as low value, since reinjection needs RAD Bink tools under Wine and the Xbox likely decodes only about 640x480.

## 2026-11-19: C10: DXT1 cutouts and uncompressed texture injection
- **Code.** DXT1 now applies a 128 alpha cutoff per mip. Nine uncompressed 2D formats can be injected, including A8R8G8B8 and X8R8G8B8. Mip storage follows the bitmap's Morton and linear-pitch flags.
- **Verification.** A synthetic CLI batch injected both a DXT1 cutout and a swizzled A8R8G8B8 bitmap. Readback verified their mips, the separate LOD1 copy, chain padding and map signature. A linear fixture verified pitch changes and a short stored chain.
- **Checks.** The full suite had 421 passes and six failures. All six existing cases require a C9 refusal that C10 removes. Offline dependency sync, Ruff lint and format, mypy and the game-data check passed.
- **Open.** The handoff requires existing tests to stay unchanged. Updating the six conflicting cases needs the owner's decision. Retail rendering and emulator boot remain unverified.
- **Review (lead).** Retargeted the six obsolete C9 refusal tests at v8u8/p8; the full suite passes. On the real mainmenu, the `--resize` batch from the 4x ESRGAN export injected 306 of 307 textures; the one skip is the deliberate shared-LOD clash. The source ISO's mtime and size were unchanged. The result boots to the title screen in xemu-vulkan with no visible corruption.

## 2026-11-19: C9 review: batch injection proven on real data, three fixes
- **Real run:** `rrl inject 1 --from exports/h2-mainmenu-esrgan4x --resize`. 276 of 307 4x-upscaled mainmenu textures were scaled back to native size and injected into one copy, with mainmenu.map re-signed once and every write verified. xemu-vulkan boots cleanly to the title screen. The original is unchanged.
- **Skipped, with reasons:** 19 DXT1 textures with alpha (1-bit alpha not encoded yet), 11 A8R8G8B8 (not injectable yet), and 1 shared-raw clash.
- **Fixes found by the real run (lead):**
  1. **Stored chains can omit trailing tiny levels** [V: 2048x128 DXT5 stores 11 of its 12 levels]. Encode only the levels that fit.
  2. **Pillow's DXT3 alpha was wrong on real data** (Master Chief: max error 247, mean 55.8). DXT3 now takes Pillow's DXT5 colour blocks plus our own explicit 4-bit alpha: max 8, mean 2.8. The phase 4a green logo had passed only because its alpha is simple.
  3. **Shared raw data:** Halo 2 dedupes identical raw, so one bitmap's LOD can sit on another's bytes. All of a bitmap's LODs are now planned and checked for byte-range overlap (bisect over the spans already written) before any write. On a clash the bitmap is skipped and the first edit wins (it used to abort the batch after a partial write).
- **Tests:** a short stored chain; DXT3 noisy-alpha round trip within one 4-bit step; partial-overlap detection; a shared clash is skipped, not fatal.
- **Next:** DXT1 1-bit alpha and A8R8G8B8 injection (30 of 307 on mainmenu), then a whole-game enhanced copy and a play test.

## 2026-11-19: C9: batch texture injection
- **CLI.** `rrl inject --from DIR` matches edited PNGs to a source's extracted textures, accepts path prefixes and optional native-size resizing, and reports injected and skipped counts per map.
- **Decisions.** Stage one disc copy and re-sign each physical map once. Check signatures, bitmap locations and every encoded LOD before publication. Conflicting edits to shared pixels fail the batch.
- **Checks.** All 370 tests passed on macOS arm64, including eight new synthetic batch cases. Offline dependency sync, Ruff lint and format, mypy and the game-data check passed.
- **Open.** Real-disc injection and emulator boot remain unverified for C9.

## 2026-11-19: C8: resumable texture upscaling
- **CLI.** `rrl upscale` mirrors eligible PNGs across maps, reports skipped texture kinds and records each enhanced artifact with its extracted input, effective scale and model. Doctor reports the optional local backend.
- **Decisions.** Batch unique source blobs at native 4×, resize RGB to 2× when requested or above the side threshold, and resize alpha separately. Matching outputs keep their timestamps. Missing mirrors can be restored from stored enhancements.
- **Checks.** On macOS arm64, all 362 tests passed, including seven synthetic C8 tests using a Pillow executable. Offline dependency sync, Ruff lint and format, mypy and the game-data check passed.
- **Open.** Real model output and GPU performance remain unverified.

## 2026-11-19: C7: Blender toolkit for RRL exports
- **Importer.** Added one Blender script for models, levels and first-person scenes. It preserves diffuse alpha on lightmapped instances, groups permutations by region, arranges clips on base and replacement NLA tracks, relinks PNGs and renders Workbench stills and turntables. The README covers the flags and JSON report.
- **Decisions.** Matching region names on separate skins get separate collections. Optional root motion translates at the previous heading, then applies wrapped yaw and retains the final delta at the end of the strip.
- **Checks.** On macOS arm64, 355 tests passed with zero skips. All 13 synthetic Blender 5.3.0 tests also passed separately, including Metal renders. Dependency sync, Ruff lint and format, mypy and the game-data check passed.
- **Open.** Root-motion accumulation order in the game remains [M]. Verification used synthetic exports on Blender 5.3.0.

## 2026-11-18: C3: Linux, Windows and macOS CI
- **CI:** added a six-job matrix for the three latest hosted runners and Python 3.12/3.13. The tracked-file check uses Bash on all three and passes NUL-delimited paths.
- **Portability:** rejected Windows drive and separator syntax in logical paths, closed workspace and image handles deterministically, and fixed read-only blob cleanup after concurrent writes. Read-only checks also work for privileged accounts. Only the symlink case can skip when Windows denies the privilege to create a link.
- **Checks:** on macOS arm64, 342 tests passed with zero skips on both Python 3.12.16 and 3.13.12. Dependency sync, Ruff lint and format, mypy, changed-file and tracked-file game-data checks, and actionlint 1.7.12 passed.
- **Open:** native Linux and Windows execution and the hosted matrix await a remote.

## 2026-11-18: C2: packaging and clean install
- **Packaging:** bumped to `0.1.0a1` and added a tox wheel-install test in `pyproject.toml`. Base installs include both SQL migrations and load all four adapters without the optional packs extra.
- **Checks:** fresh wheel, local Git pipx and uv tool installs passed help and disc-free doctor on macOS arm64. In a temporary checkout, dependency sync, 336 tests, Ruff lint and format, and mypy passed. The game-data check passed.
- **Open:** sync updates the project version in `uv.lock`. The lockfile falls outside C2's file list and needs that one-line update before locked CI.

## 2026-11-18: C6: ISO9660 and PlayStation disc inspection
- **Container:** read-only primary tree, cooked and raw sector windows, and local track-01 cue resolution. Physical spans include any raw-sector framing between file payloads.
- **Adapters:** PS2 and PS1 select their SYSTEM.CNF boot fields, resolve the executable, and report serial, inferred region and file summary through the existing CLI.
- **Checks:** 335 tests passed, including synthetic CLI runs, raw-sector boundaries, malformed images and Xbox adapter selection. Dependency sync, Ruff lint and format, mypy and the game-data check passed.
- **Open:** region prefixes remain [M]. Real-disc behaviour is unverified. Registering a cue hashes only the cue file under the existing source model.
## 2026-11-18: C1: README and first export walkthrough
- **Docs:** kept installation, source registration, map selection, export and Blender import together in the README. Added supported formats, limits and the code-only policy.
- **Checks:** verified CLI help, parsed the example RRL commands and checked README links and headings. All 321 tests passed. Dependency sync, Ruff lint and format, mypy and the game-data check passed.
- **Verification limits:** the export path and lightmap invocation were checked against source. No real disc or Blender session was used.
## 2026-11-18: C4: read-only release audit
- **Code:** `python scripts/release_audit.py` scans snapshots reachable from local refs and HEAD, including commit metadata. It reads Git objects directly, reuses the hook's magic constants and reports locations without printing matching lines.
- **Checks:** 322 tests passed, including a synthetic deleted file on a side branch, binary signatures, size, text findings and preservation of refs and untracked content. Dependency sync, Ruff lint and format, mypy and the game-data hook passed.
- **Findings before this commit:** 719 email and 7 home-path rows across snapshots. No game-magic, large-binary or secret-pattern rows. Matches are heuristic and may repeat across commits.

## 2026-11-18: C5: copy-only texture injection CLI
- **Code:** `rrl inject` accepts a source path or ID, records the parameters and outcome, and reports the validated texture writes and map signatures as JSON.
- **Decision:** stage the copy beside the output (try `cp -c` on macOS, otherwise copy normally). Publish with a hard link after validation so concurrent invocations cannot overwrite an output.
- **Checks:** 321 synthetic-suite tests passed, including CLI success, existing output, output equal to source, wrong dimensions and provenance. Dependency sync, Ruff lint and format, mypy and the game-data hook passed.
- **Limits:** publication requires hard-link support on the output filesystem. Real discs and emulator boot were outside this code-only task.

## 2026-11-18: Phase 4a start: real texture injection into a disc copy boots
- **Code:**
  - `processors.textures.encode_mip_chain(image, fmt, levels)`: a DXT1/3/5 mip chain via Pillow; DXT1 must be opaque.
  - `adapters/xbox_halo2/inject.py`: `inject_texture(disc_copy, map, tag, index, image)`.
    - Re-encodes LOD0 and every stored LOD1–5 copy, mip count from the tag, and writes them in place.
    - Re-signs every map it touched.
    - Validates before success: the signature holds and LOD0 decodes back close to the image.
    - Refuses before any write on a wrong size, a non-DXT or non-2D bitmap, or a chain that doesn't fit.
  - 6 synthetic tests on an in-code disc. They cover that only the pixels and the signature change, and that the 128-byte pad is kept.
- **Finding [V]:** stored raw chains are padded to 128 bytes (128×128 DXT1: 10,936 bytes of levels stored as 11,008). The injector leaves the pad untouched.
- **Real run:** an APFS clone in `experiments/h2-inject/`, original only read (mtime and size unchanged).
  - `polygon_blue` (128² DXT1, 3 LOD copies) was injected first. It boots, but it isn't the logo texture.
  - The title-screen textures were matched by hash from the earlier xemu dump. `ui\screens\game_shell\start_screen\start_screen` (512×64 DXT3) is the HALO 2 logo.
  - Injected with blue and green swapped, xemu-vulkan `-snapshot` boots to the title and the logo is green, detail intact (`title-70s.png`). `xemu.toml` is unchanged.
- **Not done yet:** a CLI (`rrl inject`, copy-only); A8R8G8B8 and swizzled formats; DXT1 with 1-bit alpha; cube maps; textures larger than the original (needs a map rebuild, not in place); xdvdfs repack.
- **Next:** the `rrl inject` CLI with provenance (copy, inject, validate, record), then a batch of upscaled textures at the original size.

## 2026-11-18: Halo 2 model/graph pairing via hlmt, first-person scenes
- **Pairing:** a model's graph is now the same-named jmad, else the hlmt's graph (hlmt +0x0 mode ref, +0x10 jmad ref) [V]. Across the 30 maps that covers 1,213 of 4,222 models (+12, e.g. elite driven by the dervish graph).
- **First-person scenes:**
  - weap +0x2A8 holds one entry per character (fp weapon model, fp graph) [V]. The arms model is `objects\characters\<char>\fp\fp` beside the graph; the rule holds for all 522 Master Chief entries.
  - `models_to_glb` puts several models in one .glb, one skin each; `ModelPart.attach` hangs the weapon root under the arms node the graph names (gun → r_hand).
  - `to_glb` is the single-part case, and its output is byte-identical (checked on all ascension models).
- **Result:**
  - shared.map with textures, models and animations: 1,919 assets, 0 skipped; 36 first-person scenes; 63 animated .glb files.
  - Blender: the fp_battle_rifle reload keeps the rifle in the right hand while the left arm goes to the magazine.
  - Tests cover attach, a missing attach target, hlmt pairing and the first-person lookup.
- **Left:** the dervish arms are absent from maps where he isn't playable (skipped); `camera_control` has no model.

## 2026-11-18: Halo 2 animation playback rate checked (30 fps)
- **Method (offline, no play needed):**
  - Halo 2 cinematic shots are animated per actor, so all of a shot's clips share one frame count.
  - x01 (00a_introduction) has a foley sound per shot or shot group.
  - The snd!/ugh! layout comes from Assembly's Halo2 plugin. On Xbox it matches the data: `maximum play time` is in ms (x01_01b_fol: 409,392 bytes of 44.1 kHz stereo Xbox ADPCM = 8.252 s; the field says 8,251).
- **Result:** shot 06 gives 29.6 fps; shots 07–10 against one 68.8 s foley track give 29.75 fps; 25 and 60 fps don't fit. Recorded as [V-weak]: foley doesn't map 1:1 to shots.
- **Tried first, not usable:** scene-cut detection on the 2026-11-17 play recordings. The cinematic uses slow dissolves, and the recording skipped part of it.

## 2026-11-18: Halo 2 animations (jmad) export to glTF (ADR-0013)
- **Research:** a 2-researcher workflow plus a critic, read-only (about 0.45M agent tokens).
  - Settled: the Xbox jmad layout (Assembly Halo2Xbox, not the Vista plugin), animation data in raw resource groups, node-flag bit arrays, and the codec layouts (full-frame 1/2/3/8, keyframe 4–7).
  - Settled: pose semantics (base, replacement, overlay deltas), root motion as object-space deltas, and the blend screen as a pose grid.
- **Code (inline):**
  - `adapters/xbox_halo2/animation.py` (graph reader, all codecs, validation, glTF mapping by node name).
  - `Gltf.animation`.
  - `to_glb(animate=)` hook; `rrl extract --only <maps> models animations`.
  - 16 synthetic tests: codecs 1/3/4/5/6/7, movement, every failure path, node mapping, base vs replacement channels, glTF checks, graph reader.
- **Real data:**
  - All 30 maps: 77,856 animations decode with 0 failures (after allowing scale 0, which the game uses to hide nodes, 751 animations).
  - shared.map export: 19 models animated, 1,355 clips; masterchief 482 clips, 10.7 MB, 3.5 s.
  - Model output without `animations` is byte-identical to the previous commit.
- **Blender 5.3:** masterchief `combat:rifle:walk_front` plays as a correct walk cycle.
- **Not done [M]:**
  - Overlays and blend screens are skipped.
  - fp graphs (arms + weapon skeleton) are not exported.
  - Pairing is by tag name (hlmt would be proper).
  - 30 fps, slerp vs nlerp and the root-motion accumulation order are unverified.
- **Next:** hlmt-based model/graph pairing plus fp scenes, or a Blender helper for root motion and overlays.

## 2026-11-18: Halo 2 first-person sections skinned
- **Finding (read-only, all 30 maps):** the 48 fp_* sections with out-of-range vertex node ids have no node map, and their section +0x24 hardware node count exceeds the skeleton: fp_body 35 vs 33, fp_beam_rifle 10 vs 4.
  - fp_body's extra ids 33 and 34 are 19-vertex groups at the left and right knee.
  - The beam rifle's ids 4–9 are 3-vertex groups along the barrel.
  - So these are engine-driven helper matrices (knee fix-ups, charge lights) [M], not another skeleton.
- **Fix:** `_skin` binds each extra id to the node nearest its vertices in the bind pose, with a warning. fp_body's land on l_calf/r_calf.
- **Result:** all 48 sections are now skinned (they used to export unskinned). A node map pointing past the skeleton still means bad data: exported unskinned with a warning.

## 2026-11-18: Halo 2 materials without a runtime diffuse get a base texture
- **Survey (read-only, all 30 maps):** 3,562 of 11,487 shad tags have no runtime diffuse. Their textures sit in the postprocess bitmap list, at a slot that depends on the template. Level materials were the ones hit: 4,315 untextured slots.
- **Code:** `BASE_SLOT_BY_TEMPLATE` in `render_model.py`, used by `read_shader` only when the runtime diffuse is missing.
  - The slots, read off the bitmap names: `one_alpha_env*` / `two_alpha_env_illum` slot 2; `waves` slot 1; overlays, decals, clouds and light volumes slot 0.
  - Material extras gain `diffuse_source`.
  - Synthetic tests cover the slot pick, an empty slot and a skipped template.
- **Result:** untextured level slots 4,315 → 269. What's left is all deliberate: water pools (slot 0 is a bump map), plasma, camera and env-only templates.
  - 01a + 03a + 05a: 8,405 assets, 0 skipped, all 3,184 texture links resolve; 549 materials now take their texture from a template slot.
  - Blender, 03a street view: the flat white overlay and panel patches are now textured.
- **Blend modes (same session):**
  - `alpha_mode(template)`: MASK for alpha-test templates, BLEND for the alpha, additive, overlay, decal, sky and water families, else OPAQUE.
  - `Gltf.material(alpha_mode=)` validates the value.
  - 01a + 03a levels: 992 opaque, 107 blend, 50 mask.
  - Blender EEVEE, 03a: overlays now blend over the walls and the sky band is see-through.
  - Additive effects are approximated as BLEND. Slot choices and modes are [M].
- **Next:** the fp_* skeleton, or animation (jmad).

## 2026-11-18: Halo 2 level lightmaps wired up (Blender)
- **Approach:** core glTF has no lightmap slot. occlusionTexture reads only the red channel, and the pages are coloured, so RRL puts each cluster and instance page as `lightmap_uri` (relative to the .glb) in the node extras, next to `lightmap_bitmap`/`lightmap_palette`. The .glb stays valid and no meshes are duplicated.
- **`scripts/blender/halo2_lightmaps.py`** (`blender --python ... -- <level.glb> [--strength]`):
  - Imports the .glb, then gives each lit object its own material copies (slots linked to the object, so meshes stay shared).
  - Each copy emits diffuse x lightmap on UV set 1, unlit, as the game shades lightmapped geometry.
- **Result (ascension, EEVEE):** 34 objects lit, 108 materials, 0 missing pages. Cast shadows on the pillar top line up with the rocks and the landing pad.
- **Shortcuts:**
  - The emission drops diffuse alpha (alpha-tested cutouts are lost).
  - Intensity scale is [M] (default 1).
  - Generic glTF viewers show no lighting.
- **Next:** non-diffuse shader templates (glass, decals, water), or the fp_* skeleton.

## 2026-11-18: Halo 2 level geometry (sbsp) export to glTF (ADR-0012)
- **Research:** a 2-researcher workflow plus a critic, read-only. Independent decode on all 30 maps: 80 BSPs, 4,096 clusters, 2,909 instance definitions, 12,923 instances, 0 errors.
  - Reach: scnr +0x210 → BSP area ('psbs' header) → sbsp.
  - Blocks: clusters 0x9C (0xB0), materials 0xA4, definitions 0x138 (0xC8), instances 0x140 (0x58).
  - Sections use the same hklb framing as mode but a 0x44 header. Positions and UVs are float. A section is a list iff index count == 3 x triangle count (holds on 7,005/7,005).
  - Stream 3 = lightmap UV (i16/32767): no overlap across 434 pages, and 99.9–100% of covered texels land on lit lightmap texels; a V flip is ruled out.
  - Instance frame: F/L/U are columns. The transformed bounding box hits the stored sphere centre with a median error of 3.5e-6 wu; the row reading is ruled out.
- **Code:**
  - `adapters/xbox_halo2/structure_bsp.py`.
  - `decode_section(header_size=, level=)` is reused, not copied; render-model .glb files are byte-identical (120/120).
  - The scnr/BSP walk is shared in `ltmp.py`. `Gltf.mesh(texcoords1=)`.
  - `rrl extract --only <maps> levels`.
  - One .glb per BSP: clusters as meshes; one mesh per definition, reused by one node per instance (glTF mesh reuse); lightmap UVs as TEXCOORD_1. The lightmap page and palette indices go in node extras; they are not bound as textures, because pages are per instance while meshes are shared.
- **Review fixes (lead):** TEXCOORD_1 without TEXCOORD_0 is now rejected (glTF needs contiguous sets); the placed-instance count in metadata now matches the .glb; tests for every `instance_problem` branch.
- **Real runs:** read-only, original unchanged.
  - ascension + 01a + 03a (6 BSPs, textures + levels): 7,252 assets, 0 skipped, 17.8 s.
  - All 30 maps in memory: 80 BSPs, 0 skipped, 3.55 M triangles, 38 warnings (instances of empty definitions).
- **Blender 5.3 renders:**
  - Ascension: the central rock pillar, the suspended walkways and the antenna tower.
  - 01a: Cairo station interior.
  - 03a: Old Mombasa streets and waterfront.
  - Textures correct, instances placed, no holes with culling on.
- **Known gaps:**
  - Non-diffuse shaders (glass, decals, water, clouds) export untextured: 1–52 materials per BSP.
  - Ascension's bounding box is about 2 km because of the cloud plane.
  - Lightmaps are not bound.
  - Stream 4 (type 48) is unknown.
  - The lightmap UV divisor (32767 vs 32768) is [M].
- **Next:** bind lightmaps (one mesh copy per lit instance, or a baked vertex-colour pass), or shader templates for non-diffuse materials, or the fp_* skeleton.

## 2026-11-18: Halo 2 render model (mode) export to glTF (ADR-0011)
- **Research:** a 3-researcher workflow plus a critic, read-only, checked on ascension, 01a_tutorial, shared and single_player_shared.
  - Covered: the mode layout (Assembly Halo2/mode.xml, also used for Xbox retail), the hklb/fklb section raw framing with its resource table, 6 vertex layouts chosen by the descriptor type byte, compressed int16 positions and UVs, 11:11:10 normals, strips with global parity (a list only when a locator-16 resource is present), node maps, and string IDs.
  - Checked on real data: all 3,749 sections' triangle counts; inverse-bind matrices equal to inverse(world) on masterchief.
- **Code:**
  - `adapters/xbox_halo2/render_model.py` (parse, decode, validation, shader to diffuse).
  - `processors/gltf.py`, a stdlib + numpy .glb writer with no Halo knowledge.
  - `StringIds` and a shared `block_at` in `cachefile.py`.
  - `rrl extract --only <maps> models` (textures stay the default). API unchanged.
  - Root node `halo_to_gltf`: -90° about X, ×3.048 m per world unit. Highest LOD only; one node per region:permutation.
- **Review fixes (lead):** NaN/inf now raises instead of writing invalid JSON; texture URIs are percent-encoded; a test now pins strip parity across degenerates; the dangling-texture-link limit is documented.
- **Real runs:** read-only, original unchanged (mtime, size, permissions).
  - ascension: 120 models, 0 skipped, 0.8 s.
  - 4 maps (textures + models): 8,181 assets, 505 .glb, 20.6 s; all 1,384 texture links resolve.
  - All 30 maps in memory: 4,221 models, 1 skipped (06a `assault_rifle_ammo`: no compression bounds).
  - Default texture output is byte-identical to before (1,802/1,802 blobs).
- **Visual check (offline renders, untextured):** masterchief 2.15 m tall and upright, battle rifle 0.9 m, dining chair, warthog: all correct shapes.
- **Known gaps:**
  - The fp_* arms and beam rifle (48 sections game-wide) use node ids past their own skeleton, so they export unskinned with a warning.
  - 26 material slots lose their texture because the shad has no meta in that map.
  - Forward is glTF +X.
  - All permutations are drawn at once.
  - No tangents, transparent sections, extra vertex streams, other LODs or sbsp level geometry.
- **Viewer check (Blender 5.3.0, installed via Homebrew at the owner's request):** headless import and Workbench texture render of masterchief, battle_rifle and warthog. All textures resolved, models upright, textures correctly placed with no UV v flip, and no holes with backface culling on. Masterchief's diffuse is grey: the green comes from the shader's change colour, which v1 does not export.
- **Still untested:** animation (only the bind pose is exported).
- **Next:** sbsp level geometry, or the fp_* skeleton, or change colours.

## 2026-11-18: Halo 2 3D (volume) textures and G8B8 decode
- **3D textures:**
  - There are 7 distinct volumes (194 bitmap entries): X8R8G8B8 4³, A8Y8 32³, AY8 32³. All are swizzled; none are DXT or linear.
  - The swizzle interleaves x/y/z bits across the whole volume. `processors.textures.deswizzle_box` ports xemu's `unswizzle_box`, and `deswizzle_morton` is now `deswizzle_box` with depth 1.
  - The reviewer compiled xemu's own C code and compared: 1,695 shapes, 0 mismatches.
  - Export is one PNG per slice, `<path>_slice<n>.png`, with `slice`/`depth` metadata (like cube faces).
  - DXT volumes, linear volumes and non-power-of-two volumes are skipped with a clear reason; failure-path tests cover each.
- **G8B8** (51 bitmaps, the water ripple maps in 03a, 05a and 05b): two signed bytes. On real data they are smooth as int8 and noisy as uint8. Shown like V8U8. Byte order B/G is [M].
- **Real runs:** read-only, 0 skips, original unchanged.
  - ascension + mainmenu: 3,108 PNGs incl. 192 slices, 5.7 s.
  - 03a + 05a: 5,937 PNGs, 13.9 s.
- **Whole-game run** (owner approved; operation 20, `exports/h2-all-v2`, 2 min 20 s): **70,672 assets, 0 skipped**.
  - The new formats came out at exactly the expected counts: P8 13,658, P8 bump 4,005, G8B8 51, plus 194 volumes giving 2,496 slices.
  - All 54,738 previously exported paths are byte-identical (0 changed, 0 missing). Original unchanged.
- **Next:** the xemu comparison (lightmap palette assignment, scale, bump handedness, G8B8 byte order).

## 2026-11-18: Halo 2 P8 lightmaps and P8-bump decode (ADR-0010)
- **Research:** a 3-researcher workflow plus a critic, read-only, checked against all 34 maps and default.xbe.
  - Every plain P8 bitmap (13,658) is a BSP lightmap. Its palette lives in the ltmp tag: scnr +0x210 → BSP header → ltmp +0x80 group → section palettes + cluster/instance render info (bitmap index → palette index).
  - On all 75 BSPs: 0 conflicts, 100% coverage.
  - P8-bump uses one fixed 1 KiB normal palette inside default.xbe (VA 0x468848). It is byte-identical to Reclaimer's Halo2BumpPalette.bin.
  - Details and URLs: `platforms/halo2_xbox.md`.
- **Code:**
  - `adapters/xbox_halo2/ltmp.py` reads the lightmap palettes. A malformed BSP loses only its own lightmaps, with the reason recorded.
  - `adapters/xbox_halo2/bump.py` reads the bump palette from the owner's XBE at run time. It is accepted only on a sha256 match, and the lookup is lazy (only when a map has bump bitmaps). Only the hash is committed (ADR-0010).
  - `processors.textures.decode_p8` does the generic palette lookup. Tests use synthetic data only.
- **Review fixes:**
  - A research agent had left the real bump palette as untracked `T.npy` in the repo root. It was deleted before any commit, and the game-data hook now blocks `.npy`/`.npz`.
  - The other fixes: errors are now isolated per BSP, and the XBE scan still runs when the header is bad.
- **Real runs:**
  - 01a_tutorial: 1,902 PNGs (was 1,402); only the 6 3D textures still skipped; 5 s.
  - 07a_highcharity: 3,476 (+1,531 P8, +164 P8-bump), and all 1,781 previously exported textures are byte-identical. Original image unchanged.
  - Visual check: the bump maps look like correct tangent-space normal maps. The lightmaps look like plausible chart atlases.
- **Not verified [M]:**
  - Whether each lightmap gets the right one of its ~31 near-identical palette ramps (an offline test can't tell).
  - Any in-game lightmap intensity scale, the bump handedness, and what bump index 255 means.
  - Other XBE revisions.
- **Next:**
  - Check one lightmap and one bump map against xemu (the texture dump), which settles the channel order and scale.
  - Packing P8 needs xemu support for paletted textures.
  - Remaining skips: 3D textures (194) and G8B8 (51).

## 2026-11-17: First A/B play sessions (no pack vs full 4x pack)
- **Setup:** owner played New Game on Normal: the opening cutscene, then early Cairo Station. Recorded with `h2_play_session.sh`, about 7.5 min each, sessions `20261117-194155` (no pack) and `20261117-195022` (full pack, 17,301 textures loaded).

| | No pack | Full pack |
|---|---|---|
| Frames | 12,191 in 455 s | 12,449 in 452 s |
| Median frame time | 33.3 ms | 33.4 ms |
| p99 frame time | 91.4 ms | 78.6 ms |
| Hitches over 100 ms | 99 | 47 |
| Worst frame | 2.7 s | 2.5 s (load) |

- **Result:** the pack adds no measurable stutter, so caching pack files in memory isn't needed for now. The baseline is jittery on its own; the owner noticed it too.
- **Possible confound:** the lower hitch count with the pack may come from run order rather than the pack. if xemu keeps a shader and pipeline cache on disk (unverified), it was warm for run 2, and the runs drift apart after about 1 min. To confirm, repeat the no-pack run now that the cache is warm.
- **Stills:** side-by-side images at 40, 70, 100, 200 and 300 s are in `sessions/compare/`. They show no wrong colours, missing alpha or corrupt textures in the cutscene, HUD, Master Chief, crew or Cairo interiors. The runs aren't frame-aligned, so a per-texture sharpness comparison still needs matched shots.
- **xemu.toml:** only `last_viewed_menu_index` changed; the renderer is still Vulkan.
- **Runs 3–4 (same day):**

| Run | Session | Pack | Hitches over 100 ms | p99 | Note |
|---|---|---|---|---|---|
| 3 | `195929` | no | 71 in 466 s | 84.3 ms | 31 of the hitches fall in one window at about 5 min |
| 4 | `200743` | yes | 31 in 328 s | 81.5 ms | |

  - Across the four runs, the pack runs average 6.0 hitches/min and the no-pack runs 11.1/min. The pack adds no stutter, so the order effect doesn't change the conclusion.
  - Run 4 held still on a crate stencil (`compare/hold-pack-317.png`), and the text is crisp. There is no no-pack frame from the same position, so the sharpness A/B is still open.
  - A repeatable shot for next time: from the SMG pickup checkpoint, choose Revert to Last Checkpoint and don't touch the controls.
  - **Open bug:** the owner saw squares around NPCs in run 4. Run 2 used the same pack and the owner rated it the best run, which suggests a nondeterministic xemu path (rebuild on content change, untested) rather than bad pack textures.
    - Offline alpha check: all 1,595 upscaled textures with alpha kept it, and the few edge changes (1–5 out of 255) are on non-character textures.
    - No squares were found in sampled frames; the time and place are still unknown.
  - **Update: not a pack bug.** The owner's screenshots of the squares (a lighter square patch on the floor under each character, where the shadow is drawn) were taken at 20:27 during checkpoint run A (`202426`, no pack).
    - Lead (unverified): `vk: approximating border color 0x46000000 / 0x00ffffff` in xemu.log. MoltenVK lacks `VK_EXT_custom_border_color`, so `texture.c` snaps border colours to transparent or opaque black or opaque white.
    - Transparent white (0x00ffffff) becomes transparent black. If the shadow projection samples outside its texture with that border, the quad edge would show as a square.
  - **Confirmed (test session `204310`, no pack):** with `XEMU_BORDER_WHITE_TEST=1` (patch 0104), which turns border 0x00ffffff into opaque white, the owner reports the squares gone. The video agrees, and the HUD looks normal. The cause is MoltenVK's missing custom border colours, not RRL.
  - **Made permanent (same evening):** 0x00ffffff becomes opaque white unconditionally.
    - xemu `mac-vulkan` 673804d (patch 0002) is installed as the everyday `xemu-vulkan.app`; the old app is kept as `xemu-vulkan.app.bak-20261117`.
    - xemu `texture-pack` ba78a7a (patch 0104, replaces the experiment) is installed as the dev app.
    - A two-agent review agreed on the mapping. The rejected alternative was nearest colour over all four channels, which is broader and untested.
    - Remaining risk: a pass that blends with this border's alpha now sees alpha 1. Real fix: border emulation in the shader (FIXME in `texture.c`).
    - The new builds have not been played yet.
- **Sharpness A/B (answered):**
  - Checkpoint run B (`205751`, pack) never reached the hold, so the comparison was done offline instead. Script: `scripts/experiments/session_ab.py` (tests in `tests/tooling/test_session_ab.py`).
  - Method: the opening cutscene plays the same in every run. For each of 5 pack-run (run 2) frames, the script finds the closest no-pack frame in run 1 (mean diff 0.8–4.9 on 0–255) and measures edge variance on each.
  - Result: the pack frame is sharper in all 5 pairs, 9–108% higher (e.g. 73 vs 136, 79 vs 165).
  - Zoomed crops agree: `compare/cutscene-ab-40.png` (title-screen line art and glyph bands) and `cutscene-ab-70.png` (High Charity surface veins, ship hulls).
  - Caveats:
    - The recordings are half resolution.
    - The matched frames are close but not identical; the 70 s pair is a few frames apart.
    - Edge variance also rises with any extra on-screen content.
- **Next (superseded):** test the border-colour lead: a dev build that maps 0x00ffffff to opaque white, or emulates the border in the shader (the existing FIXME). Then checkpoint run B with the pack for the sharpness A/B. The source of the baseline stutter: shader compilation, probably; check whether hitches cluster on first-seen scenes.

## 2026-11-16: xemu loads RRL texture packs (ADR-0009 milestone 2)
- **xemu** (branch `texture-pack`, commit 829ea6d, patch `patches/xemu/0101-texture-pack.patch`), built as `~/Applications/xemu-vulkan-dev.app`. The working app and `mac-vulkan` are untouched.
  - With `XEMU_TEXTURE_PACK_DIR` set, a texture whose content hash has `<hash>.rgba` there is created as an RGBA8 image of the pack size with the pack's mip chain.
  - Limits: non-linear, non-indexed, 2D only; size = guest size x 2^n; the file must fit the 64 MiB staging buffer. Bad files are logged once and ignored.
  - Sampler min/max LOD are shifted by n. A replaced binding whose content changes is released and rebuilt (after finishing the command buffer if it is in use).
- **RRL:** `pack_texture` / `write_pack_texture` in `rrl.emulators.xemu_textures` (Lanczos mips down to 1x1, atomic write), with tests.
- **Experiment** `scripts/experiments/h2_xemu_pack.py`: mainmenu's 2D swizzled DXT1/3/5 and A8R8G8B8 bitmaps, 4x Lanczos upscale with red and blue swapped, one file per plausible level count (hard links). 1,273 distinct hashes, 1.3 GB, in `~/RRL-Workspace/experiments/h2-pack/pack`. 61 of them occur in the milestone-1 title-screen dump.
- **Result:** dev xemu with `-snapshot` boots to the title screen. The HALO 2 logo and glyph bands turn copper/orange (normally blue) and the "Press Start" text changes tint. No pack errors, no crash in about 90 s.
  - Screenshot `~/RRL-Workspace/experiments/h2-pack/title-90s.png`; compare with `h2-texture-dump/title-90s.png`.
  - `xemu.toml` unchanged; the original's mtime and size unchanged.
- **Not verified:** that the higher resolution is visible (the swap proves the replacement, not the sharpness); gameplay maps; the rebuild-on-content-change path under streaming; performance of reading packs from disk on every (re)creation.
- **Pack workflow (same day):**
  - `rrl extract` now records `xemu_hashes` (every plausible level count) in the artifact metadata for swizzled 2D DXT1/3/5 and A8R8G8B8 textures, if the `packs` extra is installed.
  - `rrl pack build <source> <edited_pngs> <pack_dir>` packs every PNG found at its export path; missing ones keep the game's texture; images without alpha are reported and packed opaque.
  - The real run (mainmenu, operation 8) packed 307 textures into 1,273 hashes, the same set as the experiment.
  - A 1x pack built from the unedited export boots and renders like the original, so decode and channel order are faithful. Screenshot `h2-pack/smoke-1x-90s.png` (the xemu window is partly covered).
- **First real enhancement:**
  - Real-ESRGAN `realesrgan-ncnn-vulkan` v0.2.5.0 (universal binary, native arm64, runs on the M1 Pro through MoltenVK) installed with the owner's OK in `~/Applications/realesrgan-ncnn-vulkan`. Zip sha256 e0ad0558…c333a. It needs `-m <models dir>` when run from elsewhere (otherwise SIGSEGV), and it keeps the alpha channel.
  - `scripts/experiments/h2_upscale_export.py` upscaled mainmenu's 307 packable textures 4x with `realesrgan-x4plus` in 7 minutes. Two 2048-wide textures were dropped to 2x to fit xemu's staging buffer. `rrl pack build` made a 1.2 GB pack.
  - Boots to the title screen with no pack errors. A/B crop `h2-pack/ab-logo.png`: the logo's diagonals and bevels lose their stair-stepping and the edges are clean.
- **Full-game size estimate** (bitmap headers of all maps, each texture once): 23,192 unique textures, 5,225 packable now. At 4x that is 27.6 GiB of raw RGBA, so packs are now BC-compressed.
- **BC packs:** `rrl pack build` writes BC1 (opaque) or BC3, and xemu (patch 0102) uploads them as BC images, checking GPU support. The mainmenu pack went from 1.2 GB to 281 MB (472 BC1, 801 BC3 files) in 18 s. It boots, and the 3-way crop `h2-pack/ab-logo-bc.png` (original / RGBA / BC) shows no visible loss.
- **Full-game pack:**
  - `rrl extract 1` of all maps stored 50,110 assets. Skipped 17,908: 13,658 p8, 4,005 p8_bump, 194 3D, 51 g8b8.
  - 2,308 unique packable textures were upscaled 4x in about 1 h 10 min (3 kept at 2x). Shared textures were upscaled once, and the script is resumable.
  - `rrl pack build` now gives a texture without its own PNG the PNG of a texture with the same top-level hash. That recovered 35 hashes from copies with a different mip count.
  - The pack is 17,301 hashes, 2.8 GB (BC1/BC3); the upscaled PNGs are 3.0 GB. The real unique count is well below the 5,225 header estimate, because identical textures sit at different offsets in different maps.
- **Play-session recorder** `scripts/experiments/h2_play_session.sh [pack]`:
  - Records the screen (ffmpeg avfoundation, hevc_videotoolbox, half resolution), xemu's log and `frames.txt` into `~/RRL-Workspace/sessions/<timestamp>/`, and prints a frame-time summary on exit.
  - MoltenVK's performance logging printed nothing (xemu on macOS probably never calls a Vulkan present), so xemu patch 0103 adds `XEMU_FRAME_LOG`, one timestamp per guest flip.
  - 43 s test with the full pack, title screen: 1,063 frames, median 33.6 ms (30 fps cap), p99 92.9 ms, worst 2.6 s (boot), 8 hitches over 100 ms. There is no no-pack baseline yet.
  - No game audio: the owner approved BlackHole for later.
- **Next:** the owner records a no-pack baseline and a pack session of the same campaign stretch. Then review the frames for texture faults and compare stutter. Cache pack files in memory if pack loading causes hitches.

## 2026-11-16: xemu texture fingerprints reproduced offline (ADR-0009 milestone 1)
- **Direction change:** the owner wants upscaled, higher-resolution textures across the game, so in-place `.map` injection (same-size only) was dropped in favour of xemu texture packs (ADR-0009).
- **xemu (emulator-compat):**
  - Branch `texture-dump` in `~/Coding/xemu-vulkan`, built as `~/Applications/xemu-vulkan-dev.app`. The `mac-vulkan` branch and the working app are untouched.
  - When `XEMU_TEXTURE_DUMP_DIR` is set, `create_texture` writes every hashed guest texture (`<hash>_fmtXX_WxH_lN.bin` plus `index.tsv`).
  - Patch mirrored in `patches/xemu/0100-texture-dump.patch`.
- **Bug found in xemu's build:**
  - xemu's `configure` passes `auto_features=disabled` to meson, which reaches the xxHash subproject as `-DXXH_CPU_LITTLE_ENDIAN=0`. On Apple Silicon, `fast_hash` therefore computes a non-standard XXH3.
  - Proven by rebuilding the test program with and without the define: the result flips between Python's hash and the recorded one.
  - Fixed on `texture-dump` (`util/fast-hash.c`, commit b9290d1). It is probably upstream too; whether to report it is the owner's call.
- **RRL:**
  - `rrl.emulators.xemu_textures` mirrors `pgraph_get_texture_length`, plus XXH3 through the new optional extra `packs` (`xxhash`). CI now installs all extras.
  - `Bitmap.lods` exposes all six LOD copies.
  - Experiment script `scripts/experiments/h2_xemu_hashes.py`.
- **Result:**
  - Dump of the Halo 2 title screen: 823 textures, and all 823 files hash to their recorded value (`dump2/`).
  - **86 of them match RRL's offline hashes** of mainmenu bitm data, all from LOD0. In 77 the game programs the stored level count. In 9 it programs fewer: DXT chains stop at 4x4, e.g. 256² DXT5 uses 7 of 9 levels.
  - Byte check: a dumped 256² DXT5 equals the LOD0 chain truncated to 7 levels, byte for byte.
  - Unmatched:
    - 620 are 640x480 linear render buffers (runtime, not from disc).
    - About 60 are tiny 8x8/4x4 textures, origin unknown.
    - P8 (format 11) is not supported by RRL yet.
    - A few swizzled A8R8G8B8 shapes still need checking.
- **Next:** milestone 2, replacement in xemu: look up `content_hash` in a pack folder and upload the larger image. The level/mip clamps, the texScale for linear textures and the 64 MiB staging limit need care (see the research notes in ADR-0009). RRL writes the pack with an index from hash to PNG.

## 2026-11-16: Max Payne 3 (Xbox 360) on macXenos
- Disc 1 ISO (title ID 5454086B, 8.7 GB), launched as `macXenos <image.iso>` with no config.
- Within 90 s it reached the opening bathroom scene, rendered correctly. Steady 33 fps (the log's MetalFrameStats), about 1.65 GB footprint, no faults.
- Disc 2 is still a `.7z` in Downloads and has not been extracted. Not registered as an RRL source; this is a compatibility observation only.
- Not tested: controller input, audio, sustained gameplay, the disc swap.

## 2026-11-16: First visible texture replacement boots (Phase 4a preview)
- **Experiment** `scripts/experiments/h2_texture_swap.py`, run on an APFS clone; the original was only read:
  - In `mainmenu.map`, every DXT block of the 309 bitmaps whose data is stored locally had its two RGB565 colour endpoints set to magenta.
  - The edit is in place and same-size. Alpha blocks are untouched. For DXT1, the endpoint order is kept so 1-bit transparency survives.
  - The map was then re-signed.
  - Self-check: a changed texture decodes to a mean RGB of 255,0,249. Signature stored = computed.
- **Result:** xemu-vulkan with `-snapshot` boots to the title screen. The HALO 2 logo and the glyph bands render magenta; the background scene is still tinted blue.
  - Stored as test_run row 3. Screenshot: `~/RRL-Workspace/experiments/h2-texture-swap/title-60s.png`.
  - `xemu.toml` is unchanged. The original's sha256, mtime and size are unchanged.
- **What it proves:** the bitmap raw pointers, the in-map offsets and the re-signing are correct end to end. The game takes edited texture data from the disc image (no HDD cache in the way, at least for mainmenu).
- **Not done yet:** a real image swap, which needs a DXT encoder and a regenerated mip chain. Raw data stored in shared maps (needs those maps re-signed too, untested). Any map besides mainmenu.
- **Next:** `rrl` texture injection from a PNG (encode + mips + resign), behind an explicit "build a copy" command.

## 2026-11-15: Halo 2 texture extraction works
- **Streams:**
  - Lead: the `extract` capability (`Asset`, `Skipped`, `SelectionError`, `Extractor`), `core/extraction.py` (stores to the blob store, writes artifact and edge provenance, refuses unsafe paths), the `rrl extract` CLI, and migration 0002 (`artifact.metadata_json`).
  - graphics-assets: `rrl.processors.textures`, covering DXT1–5 via Pillow BCn, the NV2A Morton swizzle ported from xemu (LGPL-2.1+), and 13 D3D formats. ADR-0008 makes Pillow and numpy dependencies.
  - console-re: `bitm` tag parsing, cross-map raw pointers, and `Halo2Adapter.extract`. Details in `platforms/halo2_xbox.md`.
- **Real run:** `rrl extract 1 --only ascension --export ~/RRL-Workspace/exports/halo2` stored 1,566 PNGs (1,498 unique blobs) in 4.8 s.
  - Skipped 131: 90 p8_bump, 34 p8, 7 3D.
  - Visual check of the Banshee, Heretic Banshee and Spectre atlases: correct.
  - The original is still read-only.
- **Known gaps:**
  - P8 and P8-bump palettes are not supported. That's 1,531 bitmaps on 07a_highcharity, probably lightmaps.
  - 3D textures are not supported. Cube face orientation is unverified.
  - No cross-map deduplication during extraction; the store deduplicates the bytes.
  - Only 5 of the 30 maps have been run.
- **Next:** a visible texture swap on a copy (Phase 4a preview), or the enhancement engine (Phase 3).

## 2026-11-15: First Xbox 360 result on the M1 Pro
- **Setup:** macXenos 1.0 (notarised, native Metal), launched as `macXenos <image.iso>` with no other config. It ships a per-title config for Reach (`4D53085B.config.toml`, `metal_memexport_raster = true`).
- **Result:** Halo: Reach (title ID 4D53085B) boots to the main menu in under 60 s and holds 30 fps, Reach's native cap.
  - It ran 3 minutes with no crash.
  - Footprint 1.67 GB, of which 991 MB is GPU allocation. 57 draws per frame in the menu.
- **Halo 3** (title ID 4D5307E6, v0.0.0.42, same setup):
  - Ran the Sierra 117 jungle cutscene, then the in-game Button Layout settings screen with a signed-in profile.
  - About 44 fps, 991 draws per frame, 2.44 GB footprint, no crash in 3 minutes.
  - The game resolved a `\crash_report` path at startup. Probably a normal check for its crash folder; unverified.
  - The owner reports Reach "seems to be running fine" from their own play.
- **Not tested:** sustained gameplay performance, audio quality, and Xenia Edge. Launching Xenia Edge was blocked by the auto-mode permission check.
- **Kept out of RRL:** the images are not registered as RRL sources and are not used for asset work. This is a compatibility observation only.
- **macXenos CLI** (verified): the image path is the first argument. Logs go to stdout and include per-frame Metal stats (fps, draws, memory), which can be parsed for automated benchmarking later.

## 2026-11-15: Phase 2 kick-off; the map-patching risk is resolved
- **`xbox.halo2` adapter** (console-re):
  - Covers the cache-file header, the tag index and the signature. Details are in `platforms/halo2_xbox.md`.
  - The signature is the XOR of every u32 from 0x800 to EOF, stored at header offset 0x2D0. Stored and computed values **matched on all 30 retail maps**.
  - Full verification of all maps takes 8.1 s.
- **`XdvdfsImage.locate()`** returns a file's byte extent within the image, for in-place patching of copies.
- **Experiment** `scripts/experiments/h2_signature_test.py`:
  - Setup: two APFS clones of the original. One padding byte was changed in `mainmenu.map` (offset 0xe9bf24, in the zero gap after the tag-name table).
  - Each copy was booted in xemu-vulkan with `-snapshot`. `xemu.toml` was backed up and left unchanged; the original's sha256 was unchanged.
  - **Control (signature left stale):** the game shows "There's a problem with the disc you're using. It may be dirty or damaged."
  - **Resigned (signature recomputed):** boots to the title screen and plays the attract video.
  - **Conclusion:** the signature is the only integrity gate on map data, and RRL can fix it. Same-size, in-place map edits are viable.
  - Results are stored as test_run rows 1–2. Screenshots are in `~/RRL-Workspace/experiments/h2-signature/`.
- **Xbox 360:**
  - macXenos 1.0 (notarised) is installed in `~/Applications`.
  - Xenia Edge build c628a23 (2026-11-15) is installed in `~/Applications/Xenia-edge.app` at the owner's request. It is ad-hoc signed and not notarised; the signature is intact. It has not been launched yet: the owner opens it first.
  - No 360 game image is available yet.
- **Next:**
  - Bitmap tag parsing and texture extraction: DXT decode and Xbox unswizzle.
  - Then a visible texture swap on a copy, as proof of a real asset replacement.

## 2026-11-15: M1 complete
- **Merged work:**
  - QA: CI, pre-commit, the game-data hook (fixed-offset magic checks), community files, LEGAL.md.
  - console-re: the `XdvdfsImage` container and the `xbox.original` adapter.
  - 79 tests pass; mypy strict is clean.
- **First real run, against the owner's Halo 2 image** (read-only; mtime and size unchanged afterwards):
  - Identified as "Original Xbox · Halo 2 · MS-100 · NTSC-U/NTSC-J/PAL", XBE version 3, layout XISO.
  - 81 files in 4 directories, 4.72 GB, 30 `.map` files. The largest is `single_player_shared.map` at 429 MB.
  - Timing: identify plus the first sha256 took 8.3 s; inspect took 0.3 s.
- **Noted:**
  - The image is 7.4 GB but holds only 4.72 GB of files. XISO padding is the likely cause; unverified.
  - The image is writable, so RRL warned and suggests `chmod a-w`.
- **Next (Phase 2):**
  - Research the Halo 2 map header and tag index (console-re).
  - `rrl inspect` at map level.
  - An early experiment on a COPY of a map: a trivial edit plus signature recompute, to test whether it boots in xemu.

## 2026-11-15: Phase 0 research, Phase 1 foundation
- **Research:** five parallel research agents covered architecture, console formats and tools, Xbox 360 on macOS, the Python stack, and licensing. The consolidated results are in `RESEARCH_NOTES.md`.
- **Environment:**
  - macOS 27.2.1, M1 Pro 16 GB, Python 3.13.12 (miniforge; RRL uses a uv `.venv`), uv 0.13.7, git 2.52, ffmpeg 9.0.2.
  - xemu 0.8.139 is installed, both stock and the custom Vulkan build.
  - Missing for now: vgmstream, Blender, Rust.
- **xemu-vulkan:** the local patches (14 files, +365/−29) were committed to branch `mac-vulkan` in `~/Coding/xemu-vulkan` (commit e9d428e). A copy is in `patches/xemu/`.
- **Foundation commit:**
  - Core package, schema v1, blob store, provenance, adapter registry with capability checks, and a CLI with `doctor`, `source add/list`, `identify`, `inspect`.
  - 17 tests pass; ruff and mypy strict are clean.
- **Decisions:** ADR-0001 to ADR-0007 (licence provisionally GPL-3.0-or-later; workspace `~/RRL-Workspace`).
- **Next:**
  - Merge the `xbox.original` adapter and the QA tooling.
  - First read-only identify of the owner's Halo 2 image.
  - Xbox 360 smoke test, which needs the owner's OK to download Xenia Edge and macXenos.
