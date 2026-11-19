# xemu patches (GPL-2.0, same as xemu)

Local patch set that makes xemu's Vulkan renderer work on macOS via MoltenVK. Official macOS
builds only support OpenGL, and Apple's OpenGL renders Halo 2 badly.

| Patch | Base | Notes |
|---|---|---|
| `0001-macos-vulkan-moltenvk.patch` | xemu v0.8.139 (`aa54c34`) | Darwin Vulkan in meson; no external memory on Apple; optional geometry shaders with a fallback path; forced 16x anisotropic filtering; alpha-aware border colour; rebuild script |
| `0002-border-transparent-white.patch` | `0001` (branch `mac-vulkan`) | Same border fix for the everyday app |
| `0100-texture-dump.patch` | `0001` (branch `texture-dump`) | `$XEMU_TEXTURE_DUMP_DIR` texture dump keyed by content hash; standard XXH3 on little-endian hosts |
| `0101-texture-pack.patch` | `0100` (branch `texture-pack`) | `$XEMU_TEXTURE_PACK_DIR` replacement from RRL `.rgba` pack files (ADR-0009 milestone 2) |
| `0102-texture-pack-bc.patch` | `0101` (branch `texture-pack`) | Pack files may hold BC1/BC3 levels (`RRB1`/`RRB3`) |
| `0103-frame-log.patch` | `0102` (branch `texture-pack`) | `$XEMU_FRAME_LOG`: one timestamp per guest flip |
| `0104-border-transparent-white.patch` | `0103` (branch `texture-pack`) | Border colour 0x00ffffff maps to opaque white: no square shadows under Halo 2 characters |

To apply it to a clean xemu checkout:

```sh
git checkout aa54c34
git am /path/to/0001-macos-vulkan-moltenvk.patch
```

The working copy is `~/Coding/xemu-vulkan`, branch `mac-vulkan`. Known approximations in the
no-geometry-shader path: the flat-shading provoking vertex and LINE_LOOP. Border colours are
snapped to Vulkan's three fixed ones (MoltenVK has no custom border colour); 0x46000000 is still
approximated. The real fix is border emulation in the shader.
Upstream to watch: xemu PR #2799 (macOS/MoltenVK Vulkan renderer).
