# PlayStation 1 disc identification and inspection

[V] The `sony.ps1` adapter recognises a root `SYSTEM.CNF` with a `BOOT` entry
such as `cdrom:\BIN\SLES_987.65;1`. It resolves the executable in the primary ISO
tree. The synthetic CLI test reports serial and title `SLES-98765`, region
`PAL`, the parsed configuration, boot file size and a bounded file listing.
Identification requires a serial-shaped executable name with no trailing boot arguments. See the public
[PlayStation boot configuration reference](https://psx-spx.consoledev.net/cdromfileformats/#cdrom-file-playstation-exe-and-systemcnf).

## Shared ISO9660 reader

[V] Volume descriptors begin at logical sector 16. The reader finds the primary
descriptor (type 1) and terminator (255), checks the identifier and version,
and takes the root directory record from offset 156. Extent location, byte
length and logical block size use paired little- and big-endian values. The
reader checks both copies. Path tables index directories by extent and parent
number, with separate little- and big-endian tables. RRL traverses directory
records directly. A zero-length record pads the remainder of a block.
See clauses 8 and 9 of [ECMA-119](https://ecma-international.org/publications-and-standards/standards/ecma-119/).

[V] The supported logical block size is 2048 bytes. In a 2352-byte raw sector,
Mode 1 payload begins at byte 16, after sync and header. Mode 2 XA Form 1
adds an eight-byte duplicated subheader, moving the payload to byte 24.
RRL checks framing on sectors it reads. It omits error correction and rejects
Form 2. See [PSX-SPX CD-ROM sector formats](https://psx-spx.consoledev.net/cdromformat/).

[V] A cue sheet names the binary track file with FILE and declares its mode
with TRACK. INDEX 01 gives its data start in minutes, seconds and frames at
75 frames per second. RRL resolves a local relative BIN and reads track 01.
A later track in the same file bounds the first track at its pregap or start.
Audio playback and subsequent tracks are outside scope. See
[GNU ccd2cue, Appendix C](https://www.gnu.org/software/ccd2cue/manual/ccd2cue.pdf).

[V] In synthetic tests, `locate(path)` returns the physical start and byte span
in the backing image or BIN. For a multi-sector raw file, the span includes
framing between payloads. Use `open(path)` for seekable logical bytes and
`entries()` for logical sizes. The reader supports the primary tree only.
It rejects multi-extent and interleaved files, repeated directory extents,
and ambiguous names after version suffix removal.

## Region hints

[M] This prefix table is a convention used by the adapters. Synthetic tests
exercise the mapping but do not establish its accuracy for released titles.

| Serial prefix | Reported region |
|---|---|
| SLUS, SCUS | NTSC-U |
| SLES, SCES | PAL |
| SLPS, SLPM, SCPS | NTSC-J |

Unknown prefixes produce a null region. Registration hashes the selected
source file. When registering a cue, that hash covers the cue text only.
