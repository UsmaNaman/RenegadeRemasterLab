# PlayStation 2 disc identification and inspection

[V] The `sony.ps2` adapter uses the root `SYSTEM.CNF` entry `BOOT2` with
the `cdrom0:` device. It reads `VER` and `VMODE` when present. Inspection
preserves these fields separately from the region inferred from the serial.
See the [SYSTEM.CNF field reference](https://www.psdevwiki.com/ps2/System.cnf).

[V] The synthetic DVD-layout fixture uses 2048-byte sectors. RRL reads the
ISO9660 primary directory tree and ignores UDF and Joliet. The adapter also
uses the shared CD reader for Mode 1 and Mode 2 XA Form 1. See
[ECMA-119](https://ecma-international.org/publications-and-standards/standards/ecma-119/)
and the [sector, cue and physical-offset conventions](ps1.md#shared-iso9660-reader).

[V] The CLI test registers the generated image, identifies its nested
`SLUS_123.45;1` executable as `SLUS-12345`, and inspects the boot size,
sector mode, configuration and file count. The serial supplies the title.
Only identify and inspect are declared. Commercial disc compatibility has
not been tested here.

[M] Both adapters use the same [serial-prefix region hints](ps1.md#region-hints).
Non-serial executable names and titles requiring a UDF-only directory tree
are outside the implemented subset.
