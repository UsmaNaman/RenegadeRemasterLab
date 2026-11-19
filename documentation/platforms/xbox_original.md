# Original Xbox: disc format facts used by RRL

Implemented in `src/rrl/containers/xdvdfs.py` and `src/rrl/adapters/xbox_original/`.
Tags: **[V]** = verified this session against the source given. **[M]** = from memory, not verified.

Sources read on 2026-11-15:
- XDVDFS wiki: https://xboxdevwiki.net/XDVDFS
- extract-xiso source: https://github.com/XboxDev/extract-xiso (`extract-xiso.c`, master)
- XBE wiki: https://xboxdevwiki.net/Xbe
- Caustik's XBE format doc: http://www.caustik.com/cxbx/download/xbe.htm

## XDVDFS

- Sectors are 2048 bytes [V xboxdevwiki, extract-xiso `XISO_SECTOR_SIZE`].
- The volume descriptor is sector 32 (offset 0x10000) of the game partition [V xboxdevwiki, extract-xiso].
  - 0x00: magic `MICROSOFT*XBOX*MEDIA` (20 bytes) [V].
  - 0x14: root directory table sector, u32 [V].
  - 0x18: root directory table size in bytes, u32, "always a multiple of 0x800" [V xboxdevwiki].
  - 0x1C: FILETIME timestamp, u64 [V]. Not read.
  - 0x7EC: magic again [V xboxdevwiki]. RRL requires both copies.
- Sector 33 starts with `XBOX_DVD_LAYOUT_TOOL_SIG` [V xboxdevwiki]. Not checked.
- Partition offsets [V extract-xiso: `XGD1_LSEEK_OFFSET` 0x18300000]:
  - 0: a plain XISO.
  - 0x18300000: a Redump-style full XGD1 dump, with the video partition in front.
  - extract-xiso also probes 0x0FD90000 (`GLOBAL_LSEEK_OFFSET`) and 0x02080000 (`XGD3_LSEEK_OFFSET`) [V]. These are XGD2/XGD3 (Xbox 360) layouts [M], so RRL does not probe them.
- Directory entry, in this order [V extract-xiso `traverse_xiso()`]: u16 left subtree offset, u16 right subtree offset, u32 start sector, u32 file size, u8 attributes, u8 name length, then the name.
  - Subtree offsets count 4-byte units (`XISO_DWORD_SIZE`), and 0 means none [V].
  - Little-endian [M; consistent with every field above, not stated explicitly].
  - Attribute 0x10 marks a directory [V xboxdevwiki, extract-xiso `XISO_ATTRIBUTE_DIR`]. Others: 0x01 read-only, 0x02 hidden, 0x04 system, 0x20 archive, 0x80 normal [V xboxdevwiki].
  - The table is a binary tree, and its root is at offset 0 [V extract-xiso traverses from offset 0].
  - Tables are padded with 0xFF (`XISO_PAD_BYTE`) [V]. A 0xFFFF where the first entry should be means an empty directory [V].
  - The writer never lets an entry cross a sector boundary [V extract-xiso `calculate_directory_size()`]. RRL follows tree pointers, so it does not depend on this.
  - A directory with size 0 is empty and is not read [V extract-xiso]. extract-xiso rejects the names `.` and `..` and names containing a path separator [V]. RRL also rejects them.
  - Entries are 4-byte aligned [M; implied by the 4-byte offset units].
- Lookups ignore case [M]. RRL matches names case-insensitively and does not rely on the tree's sort order.
- Name encoding: RRL decodes names as Latin-1 so that decoding never fails [M; real names are believed to be ASCII].

## XBE

- Image header [V xboxdevwiki and Caustik]: magic `XBEH` at 0x000; base address u32 at 0x104 (usually 0x10000); certificate address u32 at 0x118.
  - The certificate's file offset is its address minus the base address [M; it holds because the headers are mapped at the base address].
- Certificate fields [V Caustik; xboxdevwiki lists the names but gives no offsets]:
  - +0x00 size, +0x04 timestamp.
  - +0x08 title ID, u32.
  - +0x0C title name: 0x50 bytes, "room for 40 Unicode characters" [V]. UTF-16LE [M].
  - +0x5C alternate title IDs (16 x u32).
  - +0x9C allowed media, u32.
  - +0xA0 game region, u32.
  - +0xA4 ratings.
  - +0xA8 disk number.
  - +0xAC version.
  - +0xB0 onwards: keys. RRL never reads them.
- Region bits [V Caustik]: 0x1 NA (shown as NTSC-U), 0x2 Japan (NTSC-J), 0x4 rest of world (PAL), 0x80000000 manufacturing (shown as DEBUG).
- Allowed media bits [V Caustik and xboxdevwiki]: 0x1 HARD_DISK, 0x2 DVD_X2, 0x4 DVD_CD, 0x8 CD, 0x10 DVD_5_RO, 0x20 DVD_9_RO, 0x40 DVD_5_RW, 0x80 DVD_9_RW, 0x100 DONGLE, 0x200 MEDIA_BOARD, 0x40000000 NONSECURE_HARD_DISK, 0x80000000 NONSECURE_MODE.
- Title ID: "usually 2 ASCII letters for the publisher, followed by a u16 integer game number" [V xboxdevwiki].
  - RRL shows it as `XX-NNN` with the number zero-padded to three digits, e.g. 0x4D530064 → `MS-100` [M: this convention follows Redump and community lists].
  - If the high bytes are not letters or digits, RRL shows hex instead.
  - The hex form is always in `details.title_id_hex`.

## Still unverified

- Whether the tree's sort order is case-insensitive, and which collation it uses. RRL does not depend on it.
- The name encoding, and whether the title name is strictly UTF-16LE.
- Explicit endianness statements: everything is little-endian in practice.
- Redump images other than XGD1 (`redump` layout = 0x18300000 only).
- None of this has been checked against a real disc yet: the tests use synthetic images only. The first real check should be `rrl identify` on a disc the user owns.
