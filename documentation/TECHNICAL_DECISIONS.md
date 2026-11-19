# Technical decisions (ADRs)

Short Nygard-style records: context → decision → consequences. Never edit an accepted ADR; supersede it with a new one.

## ADR-0001: Licence is GPL-3.0-or-later (2026-11-15, accepted provisionally)
- **Context:**
  - The ecosystem is mostly GPL: xemu, Dolphin, PCSX2, Reclaimer, Assembly.
  - We want to be able to port logic from those projects, and the owner's xemu patches are GPL-2.0.
  - Blender add-ons must be GPL.
- **Decision:** the core is GPL-3.0-or-later. Contributions use the DCO (`git commit -s`), not a CLA.
- **Consequences:**
  - Community adapters that load in-process are effectively GPL too.
  - Closed commercial forks are not allowed.
  - Relicensing later needs every contributor's consent, so confirm the licence before the first outside contribution.

## ADR-0002: Python 3.13 + uv, src layout, stdlib-first core (2026-11-15)
- **Context:**
  - The project needs a reproducible, low-dependency setup for contributors on an M1 Mac.
  - 3.13 is the newest Python with arm64 wheels for torch, onnxruntime and PySide6.
- **Decision:**
  - Use uv with a committed `uv.lock`, the hatchling build backend and a `src/rrl` package.
  - The core has no runtime dependencies: argparse, tomllib, sqlite3 and logging from the stdlib.
  - Optional extras later: `[ai]`, `[3d]`, `[audio]`, `[gui]`.
- **Consequences:** plain `pip install -e .` still works, and heavy libraries never land in the core install.

## ADR-0003: SQLite without ORM; migrations via PRAGMA user_version (2026-11-15)
- **Decision:** numbered `.sql` files, each applied in one transaction. Turn on WAL mode and foreign keys.
- **Consequences:** zero dependencies and a schema anyone can read. Complex migrations are written by hand.

## ADR-0004: Content-addressed write-once store + operation/edge provenance (2026-11-15)
- **Context:** the brief requires traceability, reproducibility and no overwriting.
- **Decision:**
  - Derived files go to `store/ab/cd/<sha256>`, made read-only.
  - Originals are referenced in place, never copied.
  - Every action is an `operation` row, and its `edge` rows link input and output artifacts.
- **Consequences:**
  - Deduplication comes for free.
  - The full original → rebuilt lineage can be queried.
  - Garbage-collecting unused blobs needs a future command.

## ADR-0005: Adapter discovery by entry points; declared, checked capabilities (2026-11-15)
- **Decision:**
  - Discover adapters with the stdlib `importlib.metadata` entry points (`rrl.adapters`); no pluggy.
  - Each capability is a Protocol.
  - Adapters may only claim capabilities whose Protocol exists in the current `API_VERSION`.
- **Consequences:** first-party and third-party adapters use the same path, and adapters can't claim capabilities they don't implement.

## ADR-0006: Own read-only container readers instead of PyFilesystem2/fsspec (2026-11-15)
- **Context:** PyFilesystem2 is effectively unmaintained, and fsspec is more than we need.
- **Decision:** a small `api.Container` protocol (`entries`, `open`), with one reader per format.
- **Consequences:** a few hundred lines per format, and no dependency risk. An fsspec bridge can be added later if needed.

## ADR-0007: Emulator texture replacement preferred over disc rebuilding where available (2026-11-15)
- **Context:** PCSX2 and DuckStation load hash-keyed replacement textures. xemu has no such feature.
- **Decision:**
  - PS1 and PS2 enhancements ship as emulator texture packs first.
  - Halo 2 pursues both `.map` patching and adding texture replacement to the owner's xemu fork.
- **Consequences:** less rebuild risk, but enhancements depend on the emulator.

## ADR-0008: Pillow + numpy become core dependencies (texture extraction) (2026-11-15)
- **Context:** texture extraction needs DXT1-5 decoding, Morton deswizzling and PNG output; pure-stdlib code would be slow (per-pixel Python loops) and duplicate mature codecs.
- **Decision:** `pillow>=11` (built-in BCn decoder, PNG writer) and `numpy>=2` (vectorised deswizzle and pixel unpacking) go in `[project] dependencies`, used by `rrl.processors.textures`.
- **Consequences:**
  - The first non-dev runtime dependencies; both ship Apple Silicon wheels.
  - A 1024x1024 swizzled A8R8G8B8 surface decodes in about 20 ms on the M1 Pro.
  - Heavier imaging libraries (AI upscalers) still go in optional extras.

## ADR-0009: Halo 2 high-resolution textures via xemu texture packs, keyed by xemu's XXH3 hash (2026-11-16)
- **Context:**
  - The owner wants upscaled, higher-resolution textures across the game.
  - In-place `.map` patching is same-size only.
  - Rebuilding maps with larger textures would probably exceed the game's 64 MB memory and streaming budgets.
  - xemu-vulkan's `create_texture` already hashes each guest texture with XXH3-64 over its raw guest bytes. The span is `pgraph_get_texture_length`.
- **Decision:**
  - Supersedes the `.map`-patching half of ADR-0007 for this goal: Halo 2 enhancement ships as a texture pack for the owner's xemu fork.
  - Packs are keyed by xemu's content hash. RRL computes it offline from the bitm raw bytes using the same span rules.
  - The xemu work happens on branch `texture-dump` (later `texture-pack`) in `~/Coding/xemu-vulkan` and is built as a separate `~/Applications/xemu-vulkan-dev.app`. The `mac-vulkan` branch and the working app stay untouched.
  - The `xxhash` Python package (XXH3) goes in the optional extra `packs`.
- **Milestone 1 (dump-only):** xemu writes every hashed texture to `$XEMU_TEXTURE_DUMP_DIR`. RRL must reproduce those hashes for mainmenu before any replacement code is written.
- **Milestone 2 (replacement), 2026-11-16:**
  - Pack file `<hash>.rgba`: `RRT1`, little-endian u32 width, height and levels, then RGBA8 levels tightly packed. Raw RGBA keeps xemu free of a PNG decoder; BCn compression can come later if pack size or upload time hurts.
  - xemu (branch `texture-pack`) replaces only non-linear, non-indexed, non-cube 2D textures. The size must be the guest size times a power of two; sampler LOD clamps shift by that power. A replaced binding whose guest content changes is rebuilt.
  - Packs are BC-compressed (2026-11-16): `RRB1` (BC1) for fully opaque images, `RRB3` (BC3) otherwise, encoded with Pillow's DDS writer, so no new dependency. A 4x pack is a quarter of raw RGBA or less, on disk and in GPU memory. On mainmenu it shows no visible loss against RGBA. BC7 would be higher quality but needs another encoder.
  - RRL writes the files with `rrl pack build`: the owner edits or upscales an `rrl extract --export` folder into a new one with the same paths, and the build keys each PNG by the `xemu_hashes` the adapter stored at extract time.
- **Consequences:**
  - The disc is never modified, and one pack covers all maps.
  - Enhancements require the patched emulator.
  - The hash depends on the levels and pitch the game programs, so mismatches are possible; the dump is the check.

## ADR-0010: Halo 2 P8-bump palette read from the owner's default.xbe, identified by sha256 (2026-11-18)
- **Context:** P8-bump textures index a fixed 256-entry palette that lives in `default.xbe`, not in any map.
  Reclaimer ships a copy (`Halo2BumpPalette.bin`), but the table is game data.
- **Decision:**
  - The palette is never committed. At extract time `bump.py` reads it from the owner's disc (read-only),
    first at the known VA and then with a 4-aligned scan. It is accepted only if its sha256 equals
    `d077fbea...38cc`; that hash is the only thing in the repo.
  - The palette stays in memory for the run and is not stored as a blob. Each P8-bump PNG records
    `palette_sha256` in its metadata as provenance.
  - If the palette is not found, the P8-bump bitmaps are skipped with a reason; this is never fatal.
- **Consequences:** No game data in the repo or tests: tests patch the expected hash to a synthetic table.
  Other XBE revisions work if the table bytes are identical; otherwise P8 bump stays skipped until checked.

## ADR-0011: Halo 2 render models exported as glTF 2.0 (.glb) by a stdlib writer (2026-11-18)
- **Context:** The `mode` tags need a portable 3D format that Blender and web viewers open. trimesh/pygltflib
  would add dependencies for what is a JSON document plus one binary buffer. The layouts are documented by
  Assembly and Reclaimer (both GPL-3.0).
- **Decision:**
  - `rrl.processors.gltf` writes .glb with json + struct + numpy only. It knows nodes, meshes, skins and
    materials, nothing about Halo; the Halo-to-glTF axis and unit conversion is a root node the adapter adds.
  - `render_model.py` is reimplemented from the documented facts in `documentation/platforms/halo2_xbox.md`;
    Assembly and Reclaimer are credited as references there, and no code was copied.
  - Root node `halo_to_gltf`: rotation -90° about X (Halo Z-up -> glTF Y-up) and scale 3.048 (1 world unit = 10 ft,
    Reclaimer's constant). Vertices and inverse bind matrices stay in raw Halo units under it.
  - Export LOD 6 (highest) only; one mesh per section, shared by every region:permutation node using it. Skins
    use the stored inverse bind matrices after checking them against the node hierarchy.
  - Materials reference the diffuse PNG by a relative URI to where `rrl extract` writes it; textures are never
    embedded. Models are opt-in (`--only ... models`); the default extract output (textures) is unchanged and
    `API_VERSION` stays 1 (selection is already adapter-defined).
- **Consequences:**
  - No new dependency. No tangents in v1.
  - Checked in Blender 5.3 (2026-11-18): masterchief, battle_rifle and warthog import with all textures
    found, upright, correctly textured with no UV flip, and no holes with backface culling. Masterchief is 2.14 m.
    Animated skinning is untested (bind pose only).
  - A models-only export has dangling texture URIs until textures are exported into the same folder.
  - fp_* sections whose node ids exceed the model's skeleton are exported unskinned with a warning.

## ADR-0012: Halo 2 level geometry (sbsp) exported as one glTF per BSP, reusing the mode decoder (2026-11-18)
- **Context:** Structure BSP clusters and instanced geometry use the same `hklb` raw framing, resource table, vertex
  streams and strips as render models, with a 0x44 raw header, a different list rule and an extra lightmap-UV
  stream. Instances place one shared definition many times with a rotation, position and scale.
- **Decision:**
  - `render_model.decode_section` takes `header_size` and `level` keyword arguments instead of being copied. `level`
    selects f32-only streams, the list rule (index count == 3 x triangles) and stream 3 (lightmap UV,
    i16 / 32767 -> TEXCOORD_1). Defaults keep `mode` behaviour; the 120 ascension model .glb files are byte-identical
    before and after (sha256 compared).
  - The scnr -> BSP area walk moved into `ltmp.bsp_entries` / `BspEntry.tr` / `lightmap_groups`, shared by the
    lightmap palettes and the level export.
  - One .glb per BSP: a mesh per cluster (world space); one mesh per instanced definition reused by one node per
    instance (glTF TRS from the forward/left/up columns, position and scale); materials through the same
    `add_materials` as models; the `halo_to_gltf` root (-90° X, 3.048 m/wu).
  - `processors.gltf.Gltf.mesh` gains a generic `texcoords1` (TEXCOORD_1).
  - Lightmap pages are recorded in node extras (`lightmap_bitmap`, `lightmap_palette`) and the root's
    `lightmap_bitm`, not bound as textures: glTF materials are per primitive, but pages are per instance and the
    meshes are shared.
  - Opt-in kind `levels` for `rrl extract`; the default (textures) is unchanged. A malformed BSP becomes `Skipped`;
    an instance that cannot be placed (empty or missing definition, bad frame) is a warning.
- **Consequences:**
  - No new dependency.
  - Update (same day): nodes also carry `lightmap_uri`, and `scripts/blender/halo2_lightmaps.py` binds the pages in
    Blender with object-linked material copies, so shared meshes stay shared. Generic glTF viewers show no lighting.
  - Stream 4 (type 48), several resources and ltmp Geometry Buckets stay undecoded [M].
  - Blender check of instance rotations and lightmap UVs still pending.

## ADR-0013: Halo 2 animations exported as glTF animations on the model's .glb (2026-11-18)
- **Context:** jmad tags hold the skeleton's animations in raw resource groups, compressed with codecs 1-8.
  glTF animations target nodes by index, have LINEAR/STEP/CUBIC samplers and no additive layers.
- **Decision:**
  - `adapters/xbox_halo2/animation.py` decodes jmad layout and every codec seen on disc, reimplemented from
    documented facts (Assembly GPL-3.0, coffeecutie MIT, a MAXScript), nothing copied.
  - Opt-in kind `animations`: clips are added to the paired model's .glb (`to_glb(animate=...)` hook), mapped
    by node name. Without the kind, model output is byte-identical.
  - Base and replacement clips are exported; overlays (additive deltas) and blend screens (pose grids) are
    skipped with a warning. Keyframe codecs keep their key times; LINEAR samplers (glTF slerps rotations).
  - Root motion goes to animation extras, not into the pelvis. 30 fps is assumed [M].
  - `Gltf.animation` is generic (validates targets, shapes and increasing times; shares equal input accessors).
- **Consequences:** model .glb files grow with animations (masterchief 10.7 MB, 482 clips). Pairing by tag name
  covers 1,201 of 4,222 models; the hlmt tag would pair properly, and fp graphs need a combined arms + weapon
  scene. Overlays need a Blender-side additive layer to be useful.
