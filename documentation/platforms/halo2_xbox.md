# Halo 2 (original Xbox, retail): cache file (.map) facts used by RRL

Implemented in `src/rrl/adapters/xbox_halo2/`. Tags: **[V]** = verified this session against the
source given (and, where noted, against the owner's retail disc). **[M]** = from memory, not verified.

Sources read on 2026-11-15:
- Assembly (XboxChaos), Halo 2 Xbox header and index layouts:
  https://github.com/XboxChaos/Assembly/blob/master/src/Blamite/Formats/Halo2Xbox/Layouts/H2X_Layouts_Core.xml
- Assembly engine list (Halo 2 retail = version 8, build `02.09.27.09809`, little-endian):
  https://github.com/XboxChaos/Assembly/blob/master/src/Blamite/Formats/Engines.xml
- Assembly loader (address translation): https://github.com/XboxChaos/Assembly/blob/master/src/Blamite/Blam/SecondGen/SecondGenCacheFile.cs
- Assembly checksum (`GenerateChecksum`): https://github.com/XboxChaos/Assembly/blob/master/src/Blamite/Blam/ICacheFile.cs
- Assembly map types: https://github.com/XboxChaos/Assembly/blob/master/src/Blamite/Blam/CacheFileType.cs
- Iron_Forge whitepaper (2004, self-described as possibly inaccurate): https://xboxdevwiki.net/Halo_2

"Disc" below means the owner's retail disc, read-only, via `XdvdfsImage`; only field values were recorded.

## Header (0x800 bytes, little-endian)

| Offset | Type | Field | Status |
|---|---|---|---|
| 0x000 | char[4] | `daeh` ("head" as LE fourcc) | [V Assembly, disc] |
| 0x004 | u32 | version = 8 | [V Assembly Engines.xml, disc] |
| 0x008 | u32 | file size | [V Assembly; disc: equals the real size on all 30 maps] |
| 0x00C | u32 | zero | [V whitepaper, disc: 0] |
| 0x010 | u32 | tag index offset (file offset of the index header) | [V Assembly "meta offset", disc] |
| 0x014 | u32 | index size: bytes from the index header to the first tag's meta | [V Assembly "tag data offset", disc] |
| 0x018 | u32 | tag meta data size | [V Assembly] |
| 0x01C | u32 | meta size | [V Assembly]; RRL does not use it |
| 0x120 | char[32] | build string (`02.09.27.09809` on disc) | [V Assembly, disc] |
| 0x140 | u32 | map type: 0 singleplayer, 1 multiplayer, 2 mainmenu, 3 shared, 4 singleplayer_shared | [V Assembly CacheFileType, disc: each map's type matches its name] |
| 0x144 | u32 | "crc checksum" (Assembly name) | [V Assembly name only]; meaning unknown, not used |
| 0x164–0x170 | u32 | string ids: count, table size, index offset, table offset | [V Assembly; disc: shared.map masterchief node/region names resolve]. See "Render models" |
| 0x198 | char[36] | internal name (e.g. `ascension`) | [V Assembly, disc] |
| 0x1BC | char[256] | scenario path (e.g. `scenarios\multi\ascension\ascension`) | [V Assembly, disc]; length up to 0x2C0 is [M] |
| 0x2C0 | u32 | tag name count (= tag count on disc) | [V Assembly "file table count", disc] |
| 0x2C4 | u32 | tag name table offset (NUL-terminated strings) | [V Assembly, disc] |
| 0x2C8 | u32 | tag name table size | [V Assembly, disc] |
| 0x2CC | u32 | tag name index offset (u32 per tag: offset into the name table) | [V Assembly, disc] |
| 0x2D0 | u32 | **signature** | [V Assembly "checksum", disc: 30/30 maps] |
| 0x7FC | char[4] | `toof` ("foot") | [V whitepaper names it; disc] |

All other words between 0x020–0x11F and 0x2D4–0x7FB are zero on the disc maps checked [V disc].
Maps are not compressed: the signature and the tag index read directly from the plain file [V disc].

## Signature

`XOR` of every little-endian u32 from offset 0x800 to end of file, starting from 0; a trailing
partial word is zero-padded [V Assembly `GenerateChecksum`; disc: matches on all 30 maps]. It is an
integrity check, not cryptography: after editing a map copy, recompute and write it at 0x2D0.

## Tag index (at header 0x010)

Index header, 0x20 bytes [V Assembly "meta header" layout, disc]:

| Offset | Field |
|---|---|
| 0x00 | tag group table address (memory address; the group table follows this header) |
| 0x04 | tag group count (118 on every disc map checked) |
| 0x08 | tag table address |
| 0x0C | scenario datum index |
| 0x10 | globals (matg) datum index |
| 0x18 | tag count |
| 0x1C | `sgat` ("tags") |

- Group table entry, 0xC bytes: group, parent, grandparent fourccs [V Assembly, disc].
- Tag entry, 0x10 bytes: group fourcc (u32, so reversed in file), datum index, meta memory address, meta size [V Assembly, disc].
- Index address translation [V Assembly `LoadHeader`]: `primary_mask = group_table_address - 0x20`;
  file offset = `address - primary_mask + index_offset`.
- Meta address translation [V Assembly]: file offset = `address - first_tag_address + index_offset + index_size`.
  On the disc the first tag is always `matg` (`globals\globals`).
- BSP tags (`sbsp`, `ltmp`) have address 0 and size 0 in the tag table: their data lives in separate
  BSP areas reached through the scenario [V disc; Assembly `FixupStructureTags`]. RRL reports their offset as `None`.
- Raw (resource) pointers: see "Bitmaps" below.

## Bitmaps (`bitm`)

Implemented in `bitmaps.py` (parsing, raw reads) and `Halo2Adapter.extract` (PNG export).
Sources read on 2026-11-15:
- Assembly Halo 2 Xbox `bitm` plugin: https://github.com/XboxChaos/Assembly/blob/master/src/Assembly/Plugins/Halo2Xbox/bitm.xml
- Reclaimer Halo 2 bitmap tag: https://github.com/Gravemind2401/Reclaimer/blob/master/Reclaimer.Blam/Blam/Halo2/BitmapTag.cs
- Reclaimer Halo 2 raw pointer: https://github.com/Gravemind2401/Reclaimer/blob/master/Reclaimer.Blam/Blam/Halo2/DataPointer.cs

Tag blocks (reflexives) in meta are `count` (i32) and `address` (u32). The address uses the same
translation as tag meta: `address - first_tag_address + index_offset + index_size` [V Assembly; disc:
every bitm block on 6 maps lands inside the meta area and decodes].

bitm meta header (0x4C bytes in Assembly): RRL only reads the **bitmaps block at 0x44**
[V Assembly, Reclaimer (offset 68), disc]. The sequences/sprites block sits at 0x3C [V Assembly, Reclaimer]; it is not used.

Bitmap entry, 0x74 bytes:

| Offset | Type | Field | Status |
|---|---|---|---|
| 0x00 | char[4] | `mtib` ("bitm" as LE fourcc) | [V Assembly, disc] |
| 0x04 | u16 | width | [V Assembly, Reclaimer, disc] |
| 0x06 | u16 | height | [V Assembly, Reclaimer, disc] |
| 0x08 | u8 | depth: slice count of a 3D texture, 1 for 2D and cube maps | [V Assembly; Reclaimer reads a u16; disc: 3D LOD0 sizes match w*h*d chains] |
| 0x09 | u8 | more flags (bit 0 "delete from cache file") | [V Assembly name only] |
| 0x0A | u16 | type: 0 2D, 1 3D, 2 cube map | [V Assembly, Reclaimer, disc] |
| 0x0C | u16 | format (table below) | [V Assembly, Reclaimer, disc] |
| 0x0E | u16 | flags: bit 0 power of two, 1 compressed, 2 palettised, 3 swizzled, 4 linear | [V Assembly; disc: DXT = 0x203 (never swizzled), swizzled = 0x1209, linear = 0x290] |
| 0x10 | i16 x2 | registration point | [V Assembly, Reclaimer] |
| 0x14 | u16 | mip count: levels *below* the full-resolution one (512 px -> 9) | [V disc: sizes match] |
| 0x18 | u32 | 0 on disc (runtime pixel pointer?) | [V disc: 0], meaning [M] |
| 0x1C | u32 x6 | LOD0-LOD5 raw pointers; 0xFFFFFFFF = unused | [V Reclaimer (LOD0-2), Assembly offsets, disc] |
| 0x34 | u32 x6 | LOD0-LOD5 sizes in bytes | [V Reclaimer (LOD0-2), disc] |
| 0x4C | u32 | datum (self tag reference) | [V Assembly name only] |
| 0x60–0x6E | | low-detail offset/size/format/w/h/d | [V Assembly names only]; not used |

LODs [V disc]: LOD0 is the **full-resolution** mip chain. LOD *n* is a separate copy of the
chain starting at mip *n* (its pointer is a different address). Each stored chain is padded to 128
bytes; for DXT1 512x512 with 9 mips, LOD0 = 349,568 = align128(sum of mips). Cube maps store
each face's full chain (padded to 128) one after another: `face_stride = lod0_size / 6`
[V Reclaimer comment, disc: all cube sizes match]. **Linear** (flag bit 4, non-swizzled) uncompressed
textures pad each row to a 64-byte pitch (123x121 A8R8G8B8: 512 x 121 = 61,952) [V disc; matches the NV2A pitch rule, [M]].
**3D (volume) textures** [V disc, all 7 distinct ones on the disc: `rasterizer\default_3d` 0–3
(X8R8G8B8 4x4x4), `rasterizer\plasma_shield_noise` and `objects\bitmaps\effects\shield_noise` (A8Y8 32x32x32),
`rasterizer\distance_attenuation` (AY8 32x32x32)]: all swizzled (flags 0x1209/0x1289), none DXT, none linear.
Each mip halves width, height *and* depth; LOD0 = align128(sum of w*h*d*bpp over the chain)
(A8Y8 32^3, 5 mips: 74,898 -> 75,008; AY8: 37,449 -> 37,504; X8R8G8B8 4^3, 2 mips: 292 -> 384).
The swizzle is NV2A's 3D Z-order: bits handed out x, y, z from the LSB while each dimension still needs
them (xemu `hw/xbox/nv2a/pgraph/swizzle.c:48-70` `generate_swizzle_masks`, `:118-150` `unswizzle_box_internal`;
applied to whole volume levels in `pgraph/gl/texture.c:566-626`). Disc check: decoded with the 3D swizzle,
the mean absolute neighbour difference of `plasma_shield_noise` is ~9.6 along x, y and z alike; decoding each
slice as a separate 2D Morton surface gives 43 / 17 / 14, so the slices are not stored separately [V disc].
NV2A DXT volumes are *not* stacked 2D slices: xemu `pgraph/s3tc.c:168-208` (`s3tc_decompress_3d`) reads
blocks in groups of 4 slices (for each 4x4 block position, the blocks of up to 4 consecutive slices). Halo 2
has none, so RRL rejects DXT volumes with a clear error instead of guessing [V xemu source].
xemu asserts that 3D textures are never linear (`pgraph/gl/texture.c:572`).

Raw bitmap data is **not compressed** on Xbox (Reclaimer only decompresses for Vista/MCC) [V Reclaimer, disc decodes].

Raw pointer encoding [V Reclaimer `DataPointer`, whitepaper; disc]: top 2 bits pick the file,
low 30 bits are the byte offset in that file.

| Bits 31–30 | File |
|---|---|
| 00 | the map itself |
| 01 | mainmenu.map |
| 10 | shared.map |
| 11 | single_player_shared.map |

Disc cross-check: for bitmaps pointing into another map, the same tag name and index in that map
has a *local* pointer with the same offset and size in 6,534 of 6,563 cases across ascension,
beavercreek, 01a_tutorial, 07a_highcharity and shared; the other 29 still read in bounds (probably a
renamed or duplicated tag). Multiplayer maps point mostly into **mainmenu.map** (827 of 1,582 bitmaps
on ascension) and shared.map (655); shared.map itself points into mainmenu.map.

Format enum [V Assembly, Reclaimer]; "RRL" = decoded by `rrl.processors.textures`:

| Value | Format | RRL | Value | Format | RRL |
|---|---|---|---|---|---|
| 0 | A8 | yes | 14 | DXT1 | yes |
| 1 | Y8 | yes | 15 | DXT3 | yes |
| 2 | AY8 | yes | 16 | DXT5 | yes |
| 3 | A8Y8 | yes | 17 | P8 bump | yes (palette from default.xbe, see below) |
| 6 | R5G6B5 | yes | 18 | P8 | yes (lightmaps; palette from ltmp, see below) |
| 8 | A1R5G5B5 | yes | 19 | ARGBFP32 | no (not seen) |
| 9 | A4R4G4B4 | yes | 20 | RGBFP32 | no (not seen) |
| 10 | X8R8G8B8 | yes | 21 | RGBFP16 | no (not seen) |
| 11 | A8R8G8B8 | yes | 22 | V8U8 | yes |
| 4, 5, 7, 12, 13 | unused | | 23 | G8B8 | yes (shown like V8U8, see below) |

**G8B8** [V disc]: 51 bitmaps, all 128x128 swizzled (flags 0x1209) water ripple maps,
`scenarios\bitmaps\water\water_earthcity_beach` (17, 03a_oldmombasa) and `..._deltatemple` (17 each in
05a_deltaapproach and 05b_deltatowers), all stored locally in their map. Two bytes per texel; the NV2A format is
`NV097_SET_TEXTURE_FORMAT_COLOR_SZ_G8B8` = 0x28 (`hw/xbox/nv2a/nv2a_regs.h:1223`), which xemu uploads as
unsigned RG8 (`pgraph/vk/constants.h:252-255`). Signedness is not part of the format: NV2A applies it per sampler
(`NV_PGRAPH_TEXFILTER0_GSIGNED`/`BSIGNED`, unimplemented in xemu, `pgraph/vk/texture.c:1608-1611`). The disc data
is signed: read as int8 both bytes have mean 0.0 and range about -39..39 and are smooth (neighbour diff ~6–9);
read as uint8 they cluster at 0x00–0x1F and 0xE0–0xFF (neighbour diff 67–106). By D3D naming byte 0 is B and
byte 1 is G [M]. RRL therefore shows G8B8 exactly like V8U8: R = byte 0 + 128, G = byte 1 + 128, B = 255.
The decoded PNG looks like a blue tangent-space normal map of water ripples [V disc, visual].

Export rules (`extract`): one PNG per bitmap from LOD0's first mip:
`<map>/<tag path with / separators>_<index:02d>.png`, with cube faces as `_face0`–`_face5` in stored order and 3D slices as `_slice0`–`_slice<depth-1>`
(metadata `slice`, `depth`).
Reclaimer maps Xbox faces to right, left, back, front, top, bottom and rotates some of them; RRL does
**not** rotate faces yet. Swizzled uncompressed textures are Morton-deswizzled; DXT is never swizzled.
DXT 3D textures (none on the disc), P8 without a validated palette, a missing LOD0, dimensions over 4096, more than 12 mips, or a LOD0 too small
for one surface are reported as `Skipped` with a reason, and one bad bitmap or tag never stops the run.
Deduplication: none across maps. A texture in shared.map or mainmenu.map is exported again under
every map that references it (and under shared/mainmenu themselves); the content-addressed store keeps
only one blob of identical PNG bytes.

### Injecting edited 2D bitmaps

`rrl inject` accepts these native pixel formats for single edits and batches.
The encoding and decode-back checks below are [V] against synthetic surfaces and disc copies.

| Format | What is stored |
|---|---|
| DXT1 | Each mip uses a 128 alpha cutoff. A block containing any texel below the cutoff uses three colours and transparent black (index 3). Fully opaque blocks use four colours. |
| DXT3, DXT5 | Compressed RGB with explicit 4-bit alpha (DXT3) or interpolated alpha (DXT5). |
| A8R8G8B8, X8R8G8B8 | Blue, green, red, then alpha or an ignored byte. X8R8G8B8 decodes as opaque. |
| R5G6B5, A1R5G5B5, A4R4G4B4 | Little-endian 16-bit texels. Each channel is rounded to its field width. A1 uses the same 128 cutoff. |
| A8, Y8, AY8, A8Y8 | A8 stores alpha and decodes as white. Y8 stores luminance. AY8 stores luminance once and uses it for both luminance and alpha on decode. A8Y8 stores luminance followed by alpha. |

Uncompressed mip levels use the tag flags. Swizzled levels use the inverse of the extractor's
Morton permutation at each mip's own dimensions. Linear levels round each row up to a multiple
of 64 bytes, including at smaller mips. With neither flag, rows are contiguous. DXT levels use
whole 4×4 blocks down to 1×1. The injector fits each LOD copy separately and preserves the
remaining bytes of its 128-byte chain padding. [V: synthetic layout, short-chain and padding tests]

P8, P8 bump, V8U8, G8B8, 3D textures and cube maps are skipped with the type and format in the
reason. [V: synthetic injection tests] Retail rendering of the newly encoded DXT1 cutouts and
uncompressed replacements still needs a disc-copy play test [M].

### Bitmaps verified on retail disc

`extract` per map (M1 Pro, image on internal SSD). "PNGs" counts cube faces separately (6 per cube map).
Every bitmap in a supported 2D or cube format decoded; every skip is one of the expected reasons:

| Map | PNGs | Skipped: P8 / P8 bump / 3D | Time |
|---|---|---|---|
| ascension | 1,566 | 34 / 90 / 7 | 1.7 s |
| mainmenu | 1,193 | 0 / 33 / 6 | 1.4 s |
| shared | 1,554 | 0 / 84 / 7 | 1.7 s |
| 01a_tutorial | 1,402 | 400 / 100 / 6 | 1.9 s |
| 07a_highcharity | 1,781 | 1,531 / 164 / 6 | 5.9 s |

`rrl extract <iso> --only ascension` end to end (hash, store, export): 10.7 s wall.

Format histogram on ascension (bitmaps, type): A8R8G8B8 571, A4R4G4B4 247, DXT1 197 (+16 cube),
DXT5 163, DXT3 100, P8 bump 90, A1R5G5B5 49, X8R8G8B8 37 (+7 cube, +4 3D), P8 34, A8Y8 29 (+2 3D),
Y8 22, A8 4, AY8 4 (+1 3D), R5G6B5 3, V8U8 2. Formats 19–21 and 23 were not seen on ascension;
G8B8 (23) appears on 03a_oldmombasa and 05a_deltaapproach (see above).

With 3D and G8B8 support (2026-11-18): `rrl extract 1 --only ascension mainmenu` stored 3,108 PNGs
(ascension 1,802 incl. 112 slices, mainmenu 1,306 incl. 80 slices), 0 skipped, 5.7 s wall;
`--only 03a_oldmombasa 05a_deltaapproach` stored 5,937 PNGs, 0 skipped, 13.9 s wall.

Sizes: every LOD0 size is >= one full-resolution surface (times 6 for cube maps) on ascension,
mainmenu, 01a_tutorial and shared. Most equal the computed mip chain exactly or after 128-byte alignment. The
exceptions are about 70 non-square DXT bitmaps whose stored chain is 16 bytes *shorter* than
RRL's estimate, which concerns the tiny tail mips only (not used).

Visual check (PNGs written only to `~/RRL-Workspace/scratch/`): a DXT1 character atlas, a swizzled
A8R8G8B8 sky gradient, a linear non-power-of-two HUD mask (123x121) and a DXT1 cube reflection map
face all look correct.

## Lightmaps (P8, format 18)

Implemented in `ltmp.py` (palette walk) and `Halo2Adapter.extract` (decode).
Sources: Assembly Halo 2 Xbox `scnr` and `ltmp` plugins,
https://github.com/XboxChaos/Assembly/blob/master/src/Assembly/Plugins/Halo2Xbox/scnr.xml and
https://github.com/XboxChaos/Assembly/blob/master/src/Assembly/Plugins/Halo2Xbox/ltmp.xml;
Reclaimer https://github.com/Gravemind2401/Reclaimer/blob/master/Reclaimer.Blam/Blam/Halo2/BitmapTag.cs.
"disc" = checked on the owner's retail disc (all 75 BSPs).

- P8 = BSP lightmaps only: 13,658 P8 bitmaps on the disc, all in bitm tags referenced by an ltmp,
  and every lightmap bitm is 100% P8 [V disc]. shared, single_player_shared and mainmenu hold no P8 [V disc].
  P8 bitmaps: 2D, depth 1, power of two, mips 0, flags 0x1209 (swizzled), LOD0 local [V disc].
- scnr meta +0x210: Structure BSPs block, element 0x44, translated like any meta block [V Assembly scnr.xml, disc].
  Element: +0x00 BSP file offset, +0x04 BSP size, +0x08 BSP base address, +0x10 sbsp tagref,
  +0x18 ltmp tagref (fourcc, datum; datum 0xFFFFFFFF = none) [V Assembly, disc]. shared and
  single_player_shared have one BSP without an ltmp; RRL skips it [V disc].
- BSP area header at the BSP file offset, 16 bytes: u32 size, u32 sbsp address, u32 ltmp address,
  `psbs` [V disc]. Pointers inside the area translate as
  `bsp_file_off + (address - bsp_base_addr)` and must stay inside `[bsp_file_off, +bsp_size)`;
  `TagEntry.offset` is **not** usable for sbsp/ltmp [V disc].
- ltmp +0x80: Lightmap Groups block, element 0x68, one group on every map [V Assembly ltmp.xml, disc].
  Group: +0x00 i16 type; +0x08 Section Palettes (N x 0x400, N = 1..32); +0x10 Writable Palettes (always 0);
  +0x18 bitm tagref; +0x20 clusters; +0x28 Cluster Render Info; +0x48 Instance Render Info [V Assembly, disc].
  mainmenu: one group, no palettes, empty bitm tagref; nothing to export [V disc].
- Palette: 256 x u32 A8R8G8B8 little-endian, i.e. bytes B, G, R, A, alpha 0xFF [V disc].
- Render info entry, 4 bytes `<hBB`: bitmap index, palette index, pad 0 [V Assembly, disc]. `(-1, 255)` = no
  lightmap. The palette of bitmap *i* is the one named by any cluster or instance entry with index *i*;
  on all 75 BSPs there are 0 conflicts, palette index < N, and 100% coverage [V disc]. RRL skips (never
  guesses) a bitmap whose entries conflict, point past N, or are missing.
- Pixels: first `w*h` bytes of LOD0 (LOD0 = `w*h` rounded up to 128), Morton-deswizzled at 1 byte per
  pixel, then palette lookup to RGBA [V disc: deswizzled indices are about 2x smoother than linear;
  decoded atlases show smooth charts].

## P8 bump palette (format 17)

- The 256-entry bump palette (1,024 bytes, B, G, R, A per entry) is in `default.xbe`, not in any map [V disc].
  sha256 `d077fbea214db2ecde0fcd58967f777a7b7c63f9f5ed1796969b95511c3e38cc`, identical to Reclaimer's
  `Halo2BumpPalette.bin` (GPL-3.0, https://github.com/Gravemind2401/Reclaimer) [V disc + hash compare].
  RRL commits only the hash (ADR-0010).
- Owner's XBE (4,861,952 bytes): `.data` at VA 0x461180, raw 0x45D000, raw size 0x1EF7C; table at
  VA 0x468848 (file 0x4646C8), unique in the file [V disc]. Entry 0 = ff7e7eff, 10 = ff8080ff (flat),
  255 = ff808000 (alpha 0) [V disc].
- XBE section headers [V disc]: base at 0x104, section count and header address at 0x11C/0x120,
  0x38-byte entries (flags, VA, virtual size, raw address, raw size, name address, ...).
  `bump.py` tries the known VA first, then scans every 4-aligned 1 KiB window for the hash, else reports
  "unsupported default.xbe revision" and P8 bump stays skipped. There is no "zeros before the table" heuristic,
  because that pattern is not true of the real file.
- Pixels: flags 0x120D (swizzled), 2D, power of two; LOD0 = full mip chain of 8-bit indices padded to
  128; RRL decodes the first `w*h` bytes like P8 and keeps alpha [V disc]. Raw data is often in
  mainmenu/shared/single_player_shared (pointer files 1-3) [V disc]. LOD1-5 are ignored.
- 01a_tutorial with P8 support (2026-11-18): 1,902 PNGs, only the six 3D textures skipped, 4.9 s wall
  end to end. All 400 P8 used 31 distinct palettes; all 100 P8-bump PNGs carry the bump hash. The bump PNGs look
  bluish like a tangent-space normal map [V disc, visual].

## Render models (`mode`) -> glTF

Implemented in `render_model.py` (parse, decode, glTF assembly) and `processors/gltf.py` (format-agnostic
.glb writer); `rrl extract --only <maps> models` (ADR-0011). Sources (read 2026-11-18, facts reimplemented,
no code copied): Assembly https://github.com/XboxChaos/Assembly/blob/master/src/Assembly/Plugins/Halo2/mode.xml
and https://github.com/XboxChaos/Assembly/blob/master/src/Assembly/Plugins/Halo2Xbox/shad.xml; Reclaimer
https://github.com/Gravemind2401/Reclaimer/blob/master/Reclaimer.Blam/Blam/Halo2/RenderModelTag.cs,
https://github.com/Gravemind2401/Reclaimer/blob/master/Reclaimer.Blam/Blam/Halo2/ShaderTag.cs and
https://github.com/Gravemind2401/Reclaimer/blob/master/Reclaimer.Blam/Blam/Common/BlamConstants.cs.
"disc" = decoded read-only on the owner's disc (topology/skin/winding on ascension, 01a_tutorial, shared,
single_player_shared: 3,749 sections; the RRL exporter then ran over all 30 maps).

- **String ids** [V Assembly, disc]: index = sid & 0xFFFFFF -> u32 offset at index_offset + 4*index -> NUL-terminated
  string in the table; its length must equal sid >> 24 (RRL checks it).
- **mode meta** (0x84 bytes; blocks are (i32 count, u32 address) and must lie inside the tag's own meta) [V Assembly, Reclaimer, disc]:
  0x00 sid name; 0x14 compression bounds (0x38: 7 f32 (min, max) pairs x, y, z, u, v, u2, v2; element 0 used,
  exactly one on all 505 models checked); 0x1C regions (0x10: sid, +8 permutations); permutation (0x10: sid,
  +4 six i16 section indices, lowest LOD first, -1 = none); 0x24 sections (0x5C); 0x48 nodes (0x60);
  0x58 marker groups (0xC: sid, +4 markers of 0x24); 0x60 materials (0x20, shad tagref at +8, resolved by datum).
- **Node** [V disc, masterchief 33 nodes, max error 0.0]: +0 sid; +4 i16 parent; +0xC f32x3 translation;
  +0x18 quaternion x, y, z, w used as stored; +0x28 inverse scale (RRL requires 1 ± 1e-3 [M that it is always 1]);
  +0x2C 12 f32 = inverse(world) with forward, left, up as the 3x3's COLUMNS, then position.
  World = parent_world · T · R. RRL recomputes it and fails the model if it disagrees.
- **Marker** [V Assembly]: +0 u8 region, +1 u8 permutation (255 = all), +2 u8 node, +4 f32x3, +0x10 quaternion,
  +0x20 scale. Exported as `marker:<group>` child nodes of their joint.
- **Section** (0x5C) [V Assembly, disc]: +0 i16 class (0 world, 1 rigid, 2 rigid boned, 3 skinned); +4 u16 vertex
  count; +6 u16 triangle count; +8 u16 part count; +0x2C i16 rigid node; +0x38 raw pointer (top 2 bits = file,
  like bitmaps); +0x3C raw size; +0x40 header size (0x70); +0x44 resource data size; +0x48 resources (0x10: u8 kind,
  +4 i16 locator, +6 i16 sub-locator, +8 u32 size, +0xC u32 offset). Sections with 0 vertices are skipped.
  The per-vertex node count at +0x14 does not decide the vertex layout (22 skinned sections say 0 but carry
  type-6 data) [V disc]; the vertex type byte does.
- **Raw blob** [V disc, all sections]: `hklb` ... `fklb`; u32 at +4 = resource data size; size = header +
  data + 12; resources at `size - data - 4 + offset`; u32 index count at +40. Keys (kind, locator, sub):
  (0,0,72) parts, (0,32,2) u16 indices, (0,56,32) vertex descriptors (byte 0 type, byte 1 stride, one per
  stream), (2,56,n) vertex stream n, (0,100,1) u8 node map (optional), any locator 16 = triangle list.
- **Parts** (0x48) [V disc]: +4 i16 material, +6 u16 index start, +8 u16 index count; they tile the index buffer.
- **Topology** [V disc, 3,741 strip + 8 list sections, 0 count mismatches]: each part is its own strip;
  triangle i is (a, b, c) for even i and (b, a, c) for odd i, parity counted from the part start and NOT reset
  after degenerates (that agrees with the stored normals on 99.85% of 434,461 triangles; reset gives 62%).
  Triangles with a repeated index are dropped. Emitted order is CCW about the stored normal, so no flip.
- **Stream 0** [V disc]: type 1 (stride 12, 3 f32, raw); 2 (6, 3 i16); 4 (8, +6 u8 node); 6 (12, +8 two
  nodes, +10 two weights); 8 (12, +6 three nodes, +9 three weights); 10 (16, +8 four nodes, +12 four weights).
  i16 decompress: lo + (s + 32768) / 65535 · (hi - lo). Weights are divided by their sum (stored sums 254–256).
  Node ids go through the node map when present; class 1 binds everything to the rigid node.
  Vertices are in model (bind) space.
- **Stream 1**: type 25 = 2 i16 compressed with the u/v bounds; type 24 = 2 f32 [V disc].
- **Stream 2**: type 27, 3 u32 of 11:11:10 signed (x, y / 1023, z / 511). Word 0 = normal [V disc, |n| 0.999–1.003].
  Words 1/2 = binormal/tangent [M, only 3 models checked]: not exported (no TANGENT in v1).
- **Materials**: shad +0x0C runtime properties [0] +0x00 diffuse bitm tagref (by datum) -> `baseColorTexture`;
  shad +0x20 postprocess [0] +4 bitmaps (0xC, datum at +0) go to material extras; their roles depend on the
  template (shad +0x00) [M]. shad blocks point outside the shad's own meta (anywhere in the meta area) [V disc].
- **Base colour without a runtime diffuse** (3,562 of 11,487 shad tags; glass, overlays, clouds, light volumes,
  waterfalls): `render_model.BASE_SLOT_BY_TEMPLATE` picks the postprocess slot by template name, e.g.
  `one_alpha_env*`/`two_alpha_env_illum` slot 2 (env cube, specular, base, ...), `waves` slot 1, overlays, decals,
  clouds and light volumes slot 0. [M] Slots are read off the bitmap names on the owner's disc; material extras
  record `diffuse_source` ("runtime" or "postprocess[n]"). Untextured level material slots across all 30 maps
  fell from 4,315 to 269, all in deliberately skipped templates: `water_pool*` (slot 0 is a water bump map),
  plasma, camera and env-only templates.
- **Blend modes** [M, from template names]: `render_model.alpha_mode` sets glTF `alphaMode` MASK for templates
  containing `alpha_test`, BLEND for `one_alpha*`, `two_alpha*`, `sky_*`, `plasma_alpha`, `overlay`, `decal*`,
  `one_add*`, `two_add*`, `waves`, `water*` (glTF has no additive mode; BLEND is the nearest), else OPAQUE.
  01a + 03a levels: 992 opaque, 107 blend, 50 mask materials.
- **Units/axes** [V, Blender 5.3 import of masterchief/battle_rifle/warthog: upright, textured, culling clean]: Halo is right-handed Z-up; RRL adds a root node `halo_to_gltf`
  with rotation -90° about X and scale 3.048 (1 wu = 10 ft, Reclaimer BlamConstants). UV v is not flipped.
  masterchief comes out 2.15 m tall [V disc, from the exported POSITION bounds].

### Render models: verified on the owner's disc (2026-11-18)

- `rrl extract 1 --only ascension models`: 120 .glb, 0 skipped, 0.9 s wall (CLI, including identify);
  with textures: 1,922 assets in 5.0 s. All 120 parse back; skins' inverse bind matrices match the exported
  joint hierarchy (error < 1e-3); every texture URI resolves to an exported PNG.
- All 30 maps (adapter in memory): 4,221 models exported, 1 skipped (`no compression bounds`,
  06a_sentinelwalls `assault_rifle_ammo`), 8.2 s. 48 fp_* sections have no node map and vertex ids
  beyond the model's nodes. Section +0x24 (hardware node count) is larger than the node count (fp_body 35 vs 33,
  fp_beam_rifle 10 vs 4): the extra palette entries are engine-driven helpers [M]. On disc the extra ids are small
  vertex groups: fp_body 33/34 = 19 vertices each at the left/right knee; beam rifle 4-9 = 3-vertex groups along the
  barrel (charge lights). RRL binds each extra id to the node nearest its vertices in the bind pose (fp_body ->
  l_calf/r_calf) with a warning, so all 48 sections stay skinned (was: exported unskinned).
  26 material slots lose their texture because their shad tag has no meta in that map (a warning, not a skip).

## Level geometry (`sbsp`) -> glTF

Implemented in `structure_bsp.py` (parse, glTF assembly), `ltmp.py` (`bsp_entries`, `lightmap_groups`, shared with
the lightmap palettes) and `render_model.decode_section(..., header_size=0x44, level=True)`;
`rrl extract --only <maps> levels` (ADR-0012). Source (read 2026-11-18, facts reimplemented, no code copied):
Assembly https://github.com/XboxChaos/Assembly/blob/master/src/Assembly/Plugins/Halo2Xbox/sbsp.xml (block and field
names/offsets). "all BSPs" = decoded read-only on the owner's disc: 80 BSPs, 4,096 non-empty clusters, 2,909 non-empty
instanced definitions, 12,923 placed instances, 0 decode errors.

- **Reaching a BSP** [V all BSPs]: scnr +0x210 entries (see "Lightmaps"); +0x10 sbsp tagref (datum at +0x14),
  +0x18 ltmp tagref (NONE allowed: the BSP still has geometry). Area header (size, sbsp address, ltmp address,
  `psbs`) on 80/80; sbsp = translate(header sbsp address), which is base + 0x10 on 80/80 (RRL reads the header).
  A block with count 0 may carry an address outside the area (01b_spacestation BSP 4): count is checked first.
- **sbsp blocks** [V Assembly, all BSPs]: +0x9C clusters (0xB0); +0xA4 materials (0x20, shad tagref at +8, datum
  +0xC); +0x138 instanced geometry definitions (0xC8); +0x140 instanced geometry instances (0x58). +0x34/+0x3C/+0x44
  world bounds [M, not used].
- **Section header** (first 0x40 bytes of a cluster and of a definition) [V Assembly names, all BSPs]: +0 u16 vertex
  count, +2 u16 triangle count, +4 u16 part count; +0x14 i16 classification (0 on all clusters, 1 on all definitions);
  +0x16 u16 compression flags (4 exactly on lit sections); +0x18 compression info (count 0 on clusters, 1 on
  definitions; ignored: positions are f32, 29 definitions exceed those bounds); +0x26 u16 lighting flags; +0x28 raw
  pointer (top 2 bits = file; one cluster on disc uses shared.map); +0x2C raw size; +0x30 raw header size (0x44 on
  7,005/7,005); +0x34 resource data size; +0x38 resources (same 0x10 entries as `mode`). vc 0 = empty, skipped.
- **Raw blob**: same `hklb`/`fklb` framing, resource table and checks as `mode`, header 0x44 [V all BSPs].
  Topology: a triangle list iff the u32 index count at +40 == 3 x triangles, else strips with global parity
  [V 7,005/7,005 sections, no locator-16 resource on any of them]. Roles of resources (0,8,8), (0,48,2), (1,40,-1)
  and of the other 0x44 header fields [M, unknown; ignored].
- **Vertex streams** (type, stride), exhaustive over all BSPs [V]: 0 = (1, 12) f32 xyz (clusters: world space;
  definitions: definition-local); 1 = (24, 8) f32 uv, no V flip; 2 = (27, 12) packed normal (as `mode`);
  3 = (31, 4) 2 x i16 lightmap UV, present iff lighting flags bit 0; 4 = (48, 4) clusters only, present iff bit 1,
  meaning unknown, not exported [M]. RRL fails a section whose flags disagree with its streams.
- **Lightmap UV** (stream 3) [V texel coverage on ascension + 01a_tutorial]: TEXCOORD_1 = i16 / 32767, no V flip, no
  offset; values 0..32751 on the disc. Coordinates are in the section's own lightmap page: cluster *i* uses ltmp
  Cluster Render Info[*i*].bitmap, instance *j* Instance Render Info[*j*].bitmap (render-info counts = cluster and
  instance counts). 0 overlapping texels across 434 pages; 99.9 % / 100 % of covered texels are non-background
  (V-flipped: 51 % / 74 %). The divisor 32767 vs 32768 is sub-texel and not distinguishable [M].
  Instances with lightmapping policy 1 (per vertex) have no page (bitmap -1 on 38/38 and 276/276); some of their
  definitions still carry stream 3, exported as is. ltmp Geometry Buckets (per-vertex lighting?) are not decoded [M].
- **Instance** (0x58) [V Assembly, all BSPs]: +0 f32 scale; +4 forward, +0x10 left, +0x1C up, +0x28 position (f32x3);
  +0x34 i16 definition index; +0x3C bounding sphere centre, +0x48 radius; +0x50 sid name; +0x56 i16 lightmapping
  policy. world = position + scale x (x·forward + y·left + z·up): forward/left/up are the COLUMNS of the rotation
  [V 12,923 instances: definition bbox centre lands on the stored sphere centre, median error 3.5e-6 wu, max 4.3
  (bbox vs sphere, not inspected); the row reading gives median 0.07, max 7,440]. The frame is orthonormal with
  det > 0 on all, scale 1.0 everywhere [V].
  38 instances point at an empty definition (06a_sentinelwalls, 06b_floodzone, midship): RRL warns and skips them.
- **glTF mapping**: one .glb per BSP (`<map>/<map>_bsp<i>.glb`) under the `halo_to_gltf` root; a mesh and node per
  non-empty cluster; one mesh per used definition, shared by one node per instance (TRS from the frame); materials
  as for `mode`. Lightmap page and palette indices plus `lightmap_uri` (the page PNG, relative to the .glb) go in node
  extras. Core glTF has no lightmap slot (occlusionTexture is red-only; the pages are coloured), and one definition
  mesh is shared by instances with different pages (59 of 73 lightmapped definitions on 01a_tutorial).
  `scripts/blender/halo2_lightmaps.py` wires them in Blender: per-object material copies emitting
  diffuse x lightmap (TEXCOORD_1). [V] Blender 5.3, ascension: cast shadows on the pillar top line up with the rocks
  and the pad. Intensity scale (`--strength`, default 1) is [M].

### Level geometry: verified on the owner's disc (2026-11-18)

- `rrl extract 1 --only ascension 01a_tutorial textures levels`: 3,786 assets, 0 skipped, 8.5 s wall (CLI).
  ascension: 1 BSP, 7 clusters, 66 instances; 01a_tutorial: 1 BSP, 102 clusters, 574 instances; 0 warnings; every
  diffuse texture URI in both .glb resolves to an exported PNG.
- All 30 maps (adapter in memory, 5.3 s): 80 BSPs, 0 skipped; 4,096 clusters (1,826,274 triangles), 2,909
  definitions, 12,923 instances placed (1,724,898 triangles); 38 warnings (the empty-definition instances above).
- Not yet checked in Blender: instance rotations on a visibly rotated instance, TEXCOORD_1 against the decoded
  lightmap PNG, and whether ascension's z ~ -52 plane / far rocks are real scenery or a sky/kill plane.

## Animations (`jmad`) -> glTF

Sources (facts only, nothing copied): Assembly `Plugins/Halo2Xbox/jmad.xml` (GPL-3.0,
https://github.com/XboxChaos/Assembly/blob/dev/src/Assembly/Plugins/Halo2Xbox/jmad.xml); coffeecutie
`blam/dimeter/animation.h`, `anim_codec.h` (MIT, https://github.com/hbirchtree/coffeecutie); HaloMods
`h2_anim_extractor` MAXScript (no licence file). All checked on the owner's disc.

- **Tag** (Xbox, base 0xBC) [V]: +0x0C skeleton nodes (0x20: sid, +4 next sibling, +6 first child, +8 i16
  parent); +0x2C animations (0x6C); +0xAC tag resource groups (0x14: +0 owner datum, +4 i32 size, +8 u32 raw
  pointer, same file rule as bitmaps). The Halo2 (Vista) plugin layout does NOT match Xbox.
- **Animation** (0x6C) [V]: +0 sid; +0x10 u8 type (0 base, 1 overlay, 2 replacement); +0x11 frame info type
  (0 none, 1 dx dy, 2 dx dy dyaw, 3 dx dy dz dyaw); +0x13 node count; +0x14 i16 frames; +0x24 i16 resource group
  (-1 none); +0x28 i32 offset in the group; +0x2E loop frame; +0x3C sizes: u8 static flags, u8 animated flags,
  i16 movement, i16 extra, i16 static stream, i32 (0), i32 animated stream.
- **Blob** at group[offset], 16-byte aligned, no framing [V]: static codec stream, animated codec stream,
  static node flags, animated node flags, movement (f32 per frame), extra.
- **Node flags** [V]: three bit arrays (rotation, translation, scale) of ceil(nodes/32) LE u32 words; the n-th
  set bit is the node of the codec's n-th track. Static and animated are disjoint.
- **Codecs** (first byte of a stream; header u8 codec, u8 rot/trans/scale counts, 2 x f32, u32 trans offset,
  u32 scale offset) [V]:
  - 1 (static, always the static stream), 3 (per frame): i16x4 /32767 quaternions (x,y,z,w); 2, 8: f32x4.
    Header 0x20 (+ three strides); tracks node-major. Codec 8 is a blend screen (yaw/pitch pose grid).
  - 4/6 (u8 key times), 5/7 (u16 key times): header 0x30; per track a u32 (count = w & 0xFFF, first = w >> 12)
    into the time and value tables (i16x4 rotations, f32x3 translations, f32 scales). 6/7 lay tables out in
    reverse track order; following each track's own word needs no special case.
  - Disc, all 30 maps: 77,856 animations decode with 0 failures (static/animated codecs (1,3) 13,817,
    (1,6) 21,921, (1,4) 18,965, (0,3) 9,238, (0,8) 5,505, (0,6) 4,362, (0,4) 3,599, (1,7) 384, (1,5) 63,
    (0,5) 2). Codec 2 never occurs. Scale 0 occurs (751 animations, e.g. bugger) and is legal.
- **Pose** [V]: local = animated > static > model rest; replacement animations carry full local poses for
  flagged nodes only; overlays are deltas (R = R_base * R_delta, T = T_base + T_delta) [V-weak].
- **Root motion** [V]: per-frame object-space deltas (dx forward, dy left, dz up, dyaw radians); not in the
  pelvis. Exported as animation extras `root_motion`, not baked.
- **glTF**: `rrl extract --only <maps> models animations` adds one glTF animation per base and replacement clip
  to the model's .glb, mapping jmad nodes to model nodes by name (unmatched or re-parented nodes are dropped
  with a warning); base clips key unflagged nodes at rest. Overlays and blend screens are skipped.
- **Playback rate 30 fps** [V-weak, 2026-11-18]: no rate field in the tag. Checked against the x01 cinematic's
  per-shot foley sounds (00a_introduction): shot 06 (526 frames) vs `x01_06_fol` 17.744 s = 29.6 fps; shots 07-10
  (2,047 frames) vs `x01_07_fol` 68.806 s = 29.75 fps; 25 or 60 fps do not fit. Foley does not map 1:1 to shots,
  so this is consistency, not a measurement. Sound facts used: snd! (Halo2 plugin layout, base 0x14) +0x3 rate
  (1 = 44.1 kHz), +0x4 encoding (1 stereo), +0x5 compression (1 Xbox ADPCM), +0x8 ugh! pitch range index, +0x10
  maximum play time in ms [V: x01_01b_fol 409,392 ADPCM bytes = 8.252 s at 72 bytes per 64 stereo frames, field
  says 8251]; ugh! (base 0x58) +0x20 pitch ranges (0xC, +8 first permutation, +0xA count), +0x28 permutations
  (0x10, +8 sample bytes).
  Pairing [V]: the same-named jmad, else the hlmt's graph (hlmt +0x0 render model tagref, +0x10 animation
  graph tagref): 1,213 of 4,222 models across the 30 maps (hlmt adds 12, e.g. elite <- dervish graph).
- **First-person scenes** [V]: weap +0x2A8 block (0x10 per character): +0x0 fp weapon mode, +0x8 fp jmad. The
  arms are `objects\characters\<char>\fp\fp` beside the graph (found for all 522 Master Chief entries; the
  dervish arms only exist where he is playable). The jmad covers arms + weapon nodes + `camera_control` (no
  model; dropped). RRL writes one .glb per fp graph at the graph's path: arms and weapon models, one skin each,
  the weapon root (`gun`) hung under the node the graph names (`r_hand`). shared.map: 36 scenes. Blender:
  fp_battle_rifle `reload_empty` keeps the rifle in the right hand while the left arm reaches the magazine.
- **Checked in Blender 5.3**: masterchief `combat:rifle:walk_front` plays as a walk cycle (alternating stride,
  rifle pose). shared.map: 19 models animated, 1,355 clips; masterchief.glb 482 clips, 10.7 MB.

## Verified against owner's retail disc

Disc: XBE title `Halo 2`, title ID 0x4D530064 (MS-100), regions NTSC-U/NTSC-J/PAL. All 30 `.map`
files parsed, and **computed signature == stored signature on all 30**. Selected values:

| Map | Size | Type | Tags | Signature |
|---|---|---|---|---|
| mainmenu.map | 59,670,016 | mainmenu | 1364 | 67324f9d |
| shared.map | 183,370,752 | shared | 4609 | d707a507 |
| single_player_shared.map | 429,017,088 | singleplayer_shared | 8771 | 302b2a9e |
| 01a_tutorial.map | 69,169,664 | singleplayer | 2830 | 0e32b1be |
| ascension.map | 29,735,936 | multiplayer | 4505 | c99f9a3d |
| beavercreek.map | 28,942,336 | multiplayer | 4574 | df0ff20f |
| 07a_highcharity.map | 272,911,872 | singleplayer | 7675 | 58b737ce |

Most common tag classes: `snd!`, `bitm`, `effe`, `shad` (mainmenu: `bitm`, `shad`, `unic`, `sily`).

Timing (M1 Pro, image on internal SSD, Python 3.13):
- `compute_signature` on single_player_shared.map (429 MB): 0.73 s, using 4 MiB chunks folded as
  one big int. The `array('I')` + `functools.reduce(xor)` approach measured ~5x slower.
- `inspect(verify_all=True)` over all 30 maps (~4.7 GB): 8.1 s, with the file likely in page cache.
- Default `inspect` (verifies maps up to 64 MiB, 13 maps): 1.2 s.

## Not verified / open

- Animations: 30 fps only checked against foley lengths; slerp vs nlerp in the engine; overlay composition beyond one aim test; root-motion
  accumulation order; the "extra" per-frame block; codecs 5/7 not rendered; codec 2 unseen; jmad inheritance
  (parent graph) and node data (+0xA4) unused; hlmt pairing of model and graph.
- Render models: animated skinning checked on one walk cycle; what drives the fp_* extra palette
  entries in game; tangent/binormal order; shader-template bitmap roles; transparent sections; streams
  of type 47/48 (ignored). Level geometry: see "Level geometry" for its [M] items.

- Meaning of 0x144 ("crc checksum") and whether the game or xemu checks the signature at all; the
  experiment of booting a re-signed edited copy is still to be run.
- Fields at 0x018/0x01C are taken from Assembly's names; only 0x014 is exercised by the parser.
- Bitmaps: cube face orientation (faces are exported unrotated, in stored order); the meaning of entry 0x18 and of flag bits 7, 9 and 12; whether LOD3–5 are
  ever used (only LOD0–2 seen in the samples printed).
- P8 lightmaps [M]: whether the palette channel order matches what xemu shows (only B, G, R, A is assumed);
  whether a lightmap intensity scale applies (the ltmp header floats are unexplained); what a bitmap
  index of -1 means beyond "none"; how Instance Render Info is indexed (RRL only uses its bitmap index).
- P8 bump [M]: handedness (green up or down); the meaning of index 255 (alpha 0); the table location in
  other XBE revisions (only the owner's XBE was checked).
