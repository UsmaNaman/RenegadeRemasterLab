# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [0.1.0a1] - 2026-11-19

First public alpha.

### Added

- Halo 2 (original Xbox) extraction with `rrl extract`: bitmaps to PNG, render models, level geometry
  with lightmaps, and animations to glTF 2.0. A Blender import toolkit lives in `scripts/blender/`.
- `rrl upscale`: Real-ESRGAN enhancement of an extraction export, deduplicated and resumable.
- `rrl pack build`: xemu texture packs keyed by xemu's texture hash, for the patched xemu in
  `patches/xemu/`.
- `rrl inject`: writes replacement textures into a new disc copy. It handles one texture or a whole
  export, covers DXT1/3/5 (including DXT1 cutout alpha) and uncompressed 2D formats, re-signs maps
  and validates them. The original image is never modified.
- `rrl video remaster`: deinterlaces, upscales and re-encodes pre-rendered video (resumable).
- `rrl perf report`: summarises frame timing from the patched xemu, with run-to-run comparison.
- Identify and inspect support for PS1/PS2 ISO9660 images.
- `scripts/release_audit.py`: scans git history for game data, local paths and personal details.
- Installable `rrl` CLI with four built-in adapters and bundled SQLite migrations.
  The optional `packs` extra adds xxhash for xemu texture packs.
- Core package: workspace config, hashing, logging and read-only source registration.
- SQLite schema with migrations, content-addressed blob store and provenance records.
- Adapter API (`rrl.api`, API version 1) and entry-point registry (`rrl.adapters`) with
  capability checks and failure isolation.
- CLI: `rrl doctor`, `rrl source add|list`, `rrl identify`, `rrl inspect`.
- Pre-commit hooks and CI on macOS 15 (arm64) and Ubuntu, including a check that blocks
  game data, firmware and keys.
- Contributing guide, Code of Conduct (Contributor Covenant 3.0), security policy,
  legal stance, issue forms and pull request template.
