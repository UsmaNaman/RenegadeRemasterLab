# Roadmap

| Phase | Goal | Status |
|---|---|---|
| 1 Foundation | Core, db, store, provenance, adapter registry, CLI, CI, docs, agents | **Done (M1, 2026-11-15)** |
| 2 Original Xbox | Halo 2: map/tag reading ✅, map-edit boot test ✅, texture extraction ✅ P8/P8-bump, 3D and G8B8 ✅, model extraction ✅ (render models, level geometry and animations to glTF; lightmaps via a Blender script) | **In progress** |
| 3 Enhancement | DXT/swizzle codecs ✅, upscaling ✅ (Real-ESRGAN 4x over the whole game), normal maps, glTF models | **In progress** |
| 4a Rebuild (maps) | Halo 2 asset injection, signature fix, xdvdfs repack, validation | Preview ✅ (in-place recolour boots, 2026-11-16); real DXT texture injection into a re-signed copy boots ✅ (2026-11-18) |
| 4b Rebuild (emulator) | Hash-keyed texture dump/replace in the owner's xemu-vulkan build | **Done for Halo 2 (2026-11-17)**: full-game 4x BC pack boots, adds no stutter, measurably sharper |
| 5 PS2 | Adapter; PCSX2 texture packs first | |
| 6 PS1 | Adapter; DuckStation texture packs; mkpsxiso rebuild | |
| 7 Xbox 360 on macOS | Evaluate Xenia Edge / macXenos on the M1 Pro; launcher; compat database | **Smoke test early (alongside M1)** |
| 8 Desktop UI | PySide6 app over the same core | |
| 9 Open-source release | Audit for assets/secrets, confirm licence, install docs, first public release (checklist below) | Planned |

## M1: "Foundation + first contact"
Done when `rrl doctor`, `rrl source add`, `rrl identify` and `rrl inspect` work against the owner's
Halo 2 image (read-only), with CI and the game-data safety hook in place.

- [x] Core package, SQLite schema v1, blob store, provenance, adapter registry, CLI skeleton
- [x] xemu-vulkan patches committed to a branch (`~/Coding/xemu-vulkan`, `mac-vulkan`)
- [x] XDVDFS reader + XBE identification (`xbox.original` adapter)
- [x] CI, pre-commit, game-data hook, community files
- [x] First real run against the owner's Halo 2 image

## Phase 9: first public alpha (v0.1.0) checklist
Scope: Halo 2 (original Xbox) asset extraction and the texture workflows. Roughly 2-3 sessions after
the disc-injection milestone. RRL ships code only: no textures, packs, extracted assets or links.

Owner decisions:
- [ ] Confirm the licence (GPL-3.0-or-later, provisional in ADR-0007)
- [ ] Naming/branding: "Halo" only descriptive, never in the name or logo (Microsoft trademark; Game
      Content Usage Rules)
- [ ] v0.1 scope: what ships, what stays private; whether the xemu patch fork (GPL-2) is published
- [ ] Public hosting (GitHub repo, no remote yet)

Work:
- [x] Finish disc injection: `rrl inject` (copy-only, provenance, validation), batch over a map (C5, C9, C10)
- [x] Promote experiment scripts to CLI or documented entry points: texture-pack writer, Blender
      lightmap script, upscale export (`rrl pack build`, C7, C8)
- [x] README: what it does, install, usage walkthrough (identify -> extract -> Blender -> pack/inject),
      supported formats and known limits
- [x] Packaging: real version, `pipx install` from git (PyPI later); clean-machine install test;
      Linux and Windows checks (only macOS tested so far)
- [ ] Release audit over the full git history: game data, local paths, personal details in docs/logs
      (C4 script; 2026-11-19 run: tree clean, only blocker is the owner's email as commit author ->
      rewrite authors to a GitHub no-reply address or publish a squashed history)
- [ ] CI green on the public repo; tag v0.1.0; CHANGELOG entry
