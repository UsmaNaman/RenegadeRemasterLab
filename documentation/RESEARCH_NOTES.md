# RRL Research Notes

Phase 0 research, 2026-11-15. Five research agents produced these findings, and the lead agent combined them.
Tags: **[V]** = verified against a source in this session. **[M]** = from memory, not verified. **[?]** = unconfirmed, or sources conflict.
Check [M] and [?] items before building anything on them.

---

## 1. Architecture

- **Plugin discovery:** stdlib entry points (`importlib.metadata.entry_points(group="rrl.adapters")`) [V].
  - No extra dependency needed; fsspec uses the same mechanism.
  - pluggy is overkill while each adapter owns a separate game or format.
  - First-party adapters register the same way, so the third-party path gets tested all the time.
- **Capabilities:** each adapter explicitly declares a `capabilities: frozenset[str]`.
  - Each capability has one `runtime_checkable` Protocol.
  - A contract test checks that every declared capability is actually implemented.
- **Provenance:** a content-addressed blob store (`store/ab/cd/<sha256>`, write-once).
  - SQLite tables for artifacts, operations and input/output edges; lineage is walked with a recursive CTE.
  - Prior art: git-annex keys [V], DVC [M].
- **Virtual filesystem:**
  - PyFilesystem2 is effectively dead (it depends on `pkg_resources`) [V].
  - fsspec is fine but not needed yet.
  - Instead, a small in-house read-only interface: `list` / `stat` / `open`.
- **Pitfalls:**
  - Building the framework before one adapter works end to end.
  - An unversioned plugin API.
  - Importing every plugin eagerly.
  - Capability flags that lie.
  - Re-hashing big ISOs (cache by path + size + mtime instead).
  - Rebuilds without a validate step.

Sources: packaging.python.org/en/latest/guides/creating-and-discovering-plugins/ · pluggy.readthedocs.io · beets.readthedocs.io/en/latest/dev/plugins/ · filesystem-spec.readthedocs.io/en/latest/developer.html · git-annex.branchable.com/internals/key_format/

## 2. Console formats and tools

### Original Xbox / Halo 2
- **XDVDFS:**
  - 2048-byte sectors. The volume descriptor sits at sector 32 (0x10000) of the game partition, with magic `MICROSOFT*XBOX*MEDIA`, root directory sector at +0x14 and size at +0x18 [V].
  - Directory tables are binary trees [M].
  - A Redump image holds a video partition before the game partition; XGD1 game data starts at 0x18300000 [V].
  - xemu needs an XISO [V].
- **XBE identification:**
  - `XBEH` magic; base address at 0x104; certificate address at 0x118 [V].
  - Certificate fields: title ID at +0x08, UTF-16 name at +0x0C, region at +0xA0 [M].
- **Halo 2 `.map` files:**
  - 2048-byte header, version 8, tag index with magic-based offset translation [V; the source is the Iron_Forge whitepaper from 2004, which flags itself as possibly inaccurate].
  - Raw pointers use their top 2 bits to pick local, mainmenu, shared or single_player_shared, so **the extractor must open the shared maps** [V].
  - **The "signature" is the XOR of every dword after the header. It is an integrity check, not a cryptographic signature, and must be recomputed after an edit** [V].
  - Bitmaps: DXT is not swizzled; uncompressed formats are swizzled with Morton order [M].
  - Models use quantised int16 vertices [V].
  - Whether maps are compressed: probably not [M].
- **Tools:**
  - Reclaimer: C#, GPL-3, Windows; reads H1–H4 Xbox maps [V]. The best decoding reference.
  - Assembly: C#, Windows; retail H2 Xbox support unconfirmed [?].
  - Entity: inactive.
  - None of these run natively on macOS.
  - xdvdfs: Rust, MIT, packs, unpacks and builds images [V]. Best fit for repacking.
  - extract-xiso: C, custom licence, available through Homebrew [V].
- **Texture replacement:** no evidence that xemu has any texture replacement [?].

### PS2
- ISO9660 + UDF bridge; `SYSTEM.CNF` `BOOT2` line gives the serial [M].
- GS texture formats PSMCT32/16, PSMT8/4 with CLUT [M]. TIM2 format: wiki.vg-resource.com/TM2.
- **PCSX2 texture dump/replace by hash, no disc rebuild needed** [V, from community tutorials]. The hash naming scheme is undocumented.

### PS1
- Mode 2 / 2352-byte sectors, with 2048 bytes of user data at offset 24 [M].
- Formats: TIM, TMD, VAG, XA [M].
- **mkpsxiso / dumpsxiso:** rebuilds 1:1 and regenerates EDC/ECC [V].
- **DuckStation texture replacement** by hash; native ARM64 build [V].

### Game-ID databases
- **DuckStation gamedb:** licence is now **CC BY-NC-ND 4.0** [V]. **Do not bundle.**
- **Redump DATs:** redistribution terms unknown [?]. Have the user download them.
- **PCSX2 GameIndex.yaml:** GPL [M].
- **Primary key:** for Xbox, the title ID read from the XBE.

## 3. Emulation on macOS (Apple Silicon)

- **xemu 0.8.139** (2026-10-21): universal macOS build [V].
  - CLI: `-dvd_path`, `-config_path`, `-full-screen`, `-snapshot` [V].
  - Config lives at `~/Library/Application Support/xemu/xemu/xemu.toml` [V].
  - QMP `screendump` for automated screenshots is likely but unverified [?].
  - Required files must be dumped from the user's own console (MCPX ROM, BIOS); xemu supplies a copyright-free HDD image [V].
  - **The user has a custom Vulkan/MoltenVK build:** `~/Applications/xemu-vulkan.app`, source in `~/Coding/xemu-vulkan` (uncommitted patches).
- **Xbox 360:**
  - Official Xenia Canary: now has an ARM64 CPU backend (since 2026-03), but **no Metal backend and no macOS release** [V].
  - **Xenia Edge:** BSD-3; daily `xenia_edge_macos.dmg` builds; Metal backend [V].
  - **macXenos 1.0:** released 2026-11-12; BSD-3; native Metal plus MoltenVK fallback; macOS 15+. It self-reports 151 games tested on an M4 / M4 Pro, **none on M1**, and notes one hang specific to M1 [V].
  - Virtualisation is a dead end: Parallels gives Windows on Arm guests only DX11.1 [V].
  - Wine/CrossOver is obsolete for this. Whisky was discontinued in April 2025 [V].
  - Compatibility database: github.com/xenia-canary/game-compatibility (issues titled `TITLEID - Name`, state labels) [V].
  - Formats: XEX2 header, STFS (`CON `/`LIVE`/`PIRS`), GOD [M].
  - Tools: iso2god-rs (MIT) [V]; py360 is stale.
- **RPCS3:** native Apple Silicon build, GPL-2 [V]. Relevant later, for PS3.

Sources: xemu.app/docs/cli/ · xemu.app/docs/required-files/ · github.com/xenia-canary/xenia-canary · github.com/has207/xenia-edge · github.com/bourbonicfisky/macXenos (docs/compatibility.md) · kb.parallels.com/en/129497

## 4. Technology stack

- **Python and project tooling:**
  - Python 3.13, uv + pyproject (src layout), ruff, mypy, pytest.
  - CLI: argparse. Config: tomllib + dataclasses. Database: stdlib sqlite3 with `PRAGMA user_version` migrations.
- **Binary parsing:** struct + dataclasses. Kaitai `.ksy` files serve as format documentation (the runtime is MIT).
- **Textures:**
  - Pillow + numpy. Pillow reads DXT/BC1-7 and writes DXT1/3/5, but **cannot write BC7** [V].
  - Swizzle code: write it ourselves in numpy. No maintained Python library covers PS2 GS formats [V].
- **AI upscaling (optional):**
  - Main route: torch MPS + spandrel (≥0.3 is MIT).
  - Lighter route: onnxruntime with the CoreML EP.
  - Upscayl's ncnn fork is AGPL, so do not bundle it.
  - Model weight licences vary (many are non-commercial). Record each model's licence and never ship weights.
- **3D:** pygltflib, with trimesh optional. glTF is the interchange format.
  - Blender runs as a headless subprocess: the bpy wheel is cp311-only, which clashes with 3.13 [V].
- **Audio:** vgmstream-cli (via Homebrew) for decoding, plus ffmpeg.
  - The vgmstream licence still needs checking before release [?].
- **GUI:** PySide6 (LGPL; macOS 14+ for 6.12).

## 5. Open source and legal

- **What comparable projects use** [V]:
  - GPL: xemu (2.0), Dolphin (2+), PCSX2 (3.0), RPCS3 (2.0), Reclaimer (3.0), Assembly (3.0), chaiNNer (3.0).
  - Permissive: Xenia (BSD-3), vgmstream (ISC-like), extract-xiso (custom BSD).
  - Non-free: DuckStation (CC BY-NC-ND).
- **Licence tradeoffs:**
  - GPL-3.0 allows porting from GPL tools and covers the Blender add-on.
  - Under the GPL, in-process plugins are likely derivative works.
  - Apache-2.0 includes a patent grant and allows closed forks.
  - Decide before the first outside contribution.
- **Never ship:**
  - Keys of any kind.
  - BIOS, MCPX or firmware dumps.
  - Game images, XBE/XEX files or extracted assets (including in test fixtures).
  - Links to ROM, BIOS or key sites.
  - Softmod, JTAG or RGH guides.
  - Lessons: yuzu settled for $2.4M over §1201 [V]; Dolphin was blocked from Steam over an embedded key [V].
- **Repo setup:**
  - Contributor Covenant 3.0, DCO (not a CLA), Keep a Changelog 1.1.0, MADR for ADRs.
  - **GitHub retired the macos-14 runner on 2026-11-02 [V]**, so use `macos-15` or `macos-latest`.
  - pre-commit: `check-added-large-files --enforce-all`, plus a magic-byte hook that blocks `MICROSOFT*XBOX*MEDIA`, `XBEH` and `XEX2`.

## Open questions to verify by testing

1. ~~Does a Halo 2 `.map` that was modified and had its signature fixed boot in xemu?~~ **Yes** (2026-11-15). A stale signature gives the dirty-disc error; a recomputed one boots.
2. ~~Is Halo 2 `.map` data compressed?~~ **No** (verified on the retail disc).
3. Does xemu QMP `screendump` work in the macOS build?
4. Do Xenia Edge and macXenos run on an M1 Pro, and what CLI and config paths do they take?
5. What hash naming do PCSX2 and DuckStation use for texture packs?
