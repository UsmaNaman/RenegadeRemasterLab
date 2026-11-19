# CLAUDE.md: Renegade Remaster Lab (RRL)

**PRESERVE. ENHANCE. REIMAGINE.** A modular toolkit for preserving, analysing, enhancing and remastering
video games the owner legally owns. Developed privately for now; open source (GPL-3.0-or-later) once the
foundation is stable.

## Current phase
**Phase 1: Foundation (milestone M1).** Status and history live in `documentation/DEVELOPMENT_LOG.md`.
Roadmap: `ROADMAP.md`. Read both at the start of every session.

## Hardware and environment
- MacBook Pro 14" (2021), M1 Pro, 16 GB, macOS. Design for Apple Silicon first.
- Python 3.13 managed by **uv**, in the project `.venv`. Never install into the conda base or the system Python.
- Commands: `uv sync`, `uv run pytest`, `uv run ruff check src tests`, `uv run ruff format src tests`,
  `uv run mypy`, `uv run rrl doctor`.
- Emulator for original Xbox: the owner's **custom Vulkan/MoltenVK xemu** at `~/Applications/xemu-vulkan.app`.
  - Source is in `~/Coding/xemu-vulkan`, branch `mac-vulkan`. The patch set is mirrored in `patches/xemu/`.
  - Never launch stock `/Applications/xemu.app` silently: it rewrites `xemu.toml` and drops the
    `renderer = 'VULKAN'` setting.

## Architecture (details in documentation/ARCHITECTURE.md)
- One package, `src/rrl/`.
  - `core/`: config, logging, sources, hashing.
  - `db/`: SQLite plus numbered migrations.
  - `store/`: content-addressed blobs and provenance.
  - `containers/`: read-only filesystems inside images.
  - `adapters/`: one per platform or game.
  - `processors/`: platform-agnostic enhancement code.
  - `emulators/`.
  - `cli/`.
- `rrl/api.py` is the **only** public surface for adapter authors. Any incompatible change bumps `API_VERSION`.
- Adapters are discovered through the `rrl.adapters` entry-point group.
  - They declare `capabilities`. The registry and contract test reject adapters whose declared
    capabilities don't match what they implement.
  - Adapters only claim capabilities they genuinely implement.
- Enhancement algorithms never contain platform-specific file handling.
- The workspace (`~/RRL-Workspace` or `$RRL_WORKSPACE`) holds the database, store, logs and `rrl.toml`.
  It lives **outside the repo**.

## Data protection (mandatory)
1. Never modify, move or overwrite original game files. Open them with `"rb"` only.
2. Hash every source (sha256). Derived files go to the write-once blob store.
3. Every action runs inside `store.provenance.operation(...)`, so it is recorded with its params and status.
4. Artifact kinds stay distinct: original (`source` table), then extracted, converted, enhanced, rebuilt.
5. Validate output before declaring a build successful.
6. Never commit game images, XBE/XEX files, extracted assets, BIOS/MCPX/firmware, keys, or links to any of them.
   - Tests use **synthetic fixtures** generated in code.
   - The `scripts/check_no_game_data.py` hook and CI enforce this.
7. Never upload proprietary assets to GitHub or any AI service. No DRM circumvention.
   No console-hacking guides.
8. Do not download, extract or modify the owner's game files without asking first.

## Coding conventions
- Stdlib first. Add a dependency only with a clear need, and put optional ones in extras.
- Binary formats are parsed with `struct` + dataclasses.
- Bounds-check every offset read from a file. Malformed input raises a clear error and never hangs.
- ruff (line length 100), mypy strict, type hints everywhere.
- Comments explain *why*, not *what*.
- Errors are explicit. Third-party adapter failures are isolated, logged, and never crash RRL.
- Tests are required for parsers, the store, the database and the CLI.
  - A format fact counts as verified only with a source URL or a passing test against known data.
  - Mark format facts [V] (verified) or [M] (from memory) in `documentation/platforms/*.md`.

## Working with the owner
- The owner has a CS background. Explain unfamiliar tools and decisions; be technically honest.
- Never claim something works without testing it. Report failures plainly.
- Ask before installing software, making system changes, or downloading anything.
- Record decisions as ADRs in `documentation/TECHNICAL_DECISIONS.md`.
- Update `documentation/DEVELOPMENT_LOG.md` at the end of each work session.

## Sub-agents (definitions in .claude/agents/)
The lead agent (the main session) owns architecture, integration, reviews and docs.

| Agent | Owns |
|---|---|
| `core-dev` | `core/`, `db/`, `store/`, `cli/` |
| `console-re` | `containers/`, `adapters/`, `documentation/platforms/` (research first) |
| `graphics-assets` | `processors/` |
| `emulator-compat` | `emulators/`, `patches/xemu/` |
| `qa` | `tests/`, CI, hooks, community files |

Rules for using sub-agents:
- Run substantial parallel work in git worktrees.
- Only one agent edits a given file at a time.
- Agents report changed files, decisions, tests and open problems.
- The lead reviews every change before merging.
