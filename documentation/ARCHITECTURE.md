# Architecture

```
          rrl CLI  (later: PySide6 GUI on the same core)
               │
        ┌──────┴──────── CORE ────────────────────────┐
        │ config · logging · SQLite db · sources      │
        │ blob store (sha256, write-once, read-only)  │
        │ provenance: every operation's inputs,       │
        │   outputs, params, versions, status         │
        └──┬──────────────┬──────────────┬────────────┘
   containers/       adapters/        processors/       emulators/
   read-only FS      per platform     platform-agnostic launch, screenshot,
   inside images     or game          enhancement       compat records
```

## Layers

| Layer | Responsibility | Must not |
|---|---|---|
| `core/` | Config (`<workspace>/rrl.toml`), logging, registering sources, hashing | Know any game format |
| `db/` | SQLite connection; migrations in `db/migrations/NNNN_*.sql`, tracked by `PRAGMA user_version` and each applied in one transaction | Use an ORM |
| `store/` | `blobs.py` stores files under `store/ab/cd/<sha256>` (read-only, written once by temp file + rename). `provenance.py` provides `operation()`, which records every action. | Overwrite anything |
| `containers/` | Read-only filesystems inside images (XDVDFS, ISO9660, STFS…), implementing `api.Container` | Write to images |
| `adapters/` | Recognise and process a platform or game, implementing `api.Adapter` and its declared capabilities | Contain generic image or audio algorithms |
| `processors/` | Texture, model, material and audio algorithms | Know about consoles or file containers |
| `emulators/` | Launch emulators, capture screenshots, record `test_run` rows | Bundle emulator binaries or firmware |

## Adapter contract (`rrl/api.py`, API_VERSION 1)

- Every adapter has `id`, `name`, `version`, `api_version` and `capabilities`, and implements `identify(source) -> Match | None`.
- **Capabilities available in v1:** `identify` and `inspect`.
- **Planned capabilities:** `extract`, `convert`, `validate`, `replace`, `rebuild`, `test`.
  - These names are reserved. Adapters cannot claim them until a Protocol for them ships in `api.py`.
  - Planned shapes: `extract(source, store) -> list[Artifact]` and `rebuild(manifest, out) -> Artifact`.
  - A rebuild always ends with `validate`.
- **Enforcement:** `registry.check()` runs at load time, and `tests/contract/` runs it against every installed adapter.
- **Discovery:** entry points in the `rrl.adapters` group. Failures are isolated: a broken plugin is logged and disabled.
- **Layering:** platform adapters (e.g. `xbox.original`) identify the disc. Game adapters (e.g. `xbox.halo2`) report higher confidence for their specific title and add format knowledge.

## Data model (`0001_initial.sql`)

| Table | Holds |
|---|---|
| `source` | Originals (path, sha256, size, mtime, identity), referenced but never copied |
| `blob` | Content-addressed files |
| `artifact` | Derived files of kind `extracted`, `converted`, `enhanced` or `rebuilt` |
| `operation` | Every action: op, adapter and version, rrl version, params, tool versions, status, error |
| `edge` | Links operations to the artifacts they read (input) and wrote (output). This is the lineage DAG, queryable with a recursive CTE. |
| `project`, `project_source` | Remaster projects |
| `test_run` | Emulator results, which feed the compatibility database |

## Getting changes back into a game (per platform)

| Platform | Primary route | Fallback |
|---|---|---|
| Original Xbox / Halo 2 | Patch `.map` files and recompute the XOR signature, then repack with xdvdfs | Hash-keyed texture replacement in the owner's xemu build |
| PS2 | PCSX2 texture-replacement packs (no disc rebuild) | ISO patching in place |
| PS1 | DuckStation texture-replacement packs | mkpsxiso rebuild |
| Xbox 360 | Emulation only for now (Xenia Edge / macXenos) | — |
