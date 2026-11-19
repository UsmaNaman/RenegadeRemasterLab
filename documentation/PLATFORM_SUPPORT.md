# Platform support

Legend: ✅ works and is tested · 🧪 experimental · 🔬 researching · ⛔ not feasible or out of scope · — not started

| Platform | Identify | Inspect | Extract | Convert | Enhance | Replace/Rebuild | Emulator test |
|---|---|---|---|---|---|---|---|
| Original Xbox (XISO / Redump) | ✅ | ✅ | — | — | — | 🔬 | 🔬 xemu (custom Vulkan build) |
| Halo 2 (Xbox) | ✅ xbox.halo2 | ✅ maps, tags, signatures | ✅ textures → PNG (no P8/3D yet) | — | — | 🧪 same-size in-place edits + resign boot in xemu | 🧪 xemu-vulkan |
| PS2 | — | — | — | — | — | 🔬 via PCSX2 texture packs | — |
| PS1 | — | — | — | — | — | 🔬 via DuckStation / mkpsxiso | — |
| Xbox 360 | — | — | — | — | — | ⛔ for now | 🧪 macXenos 1.0 on M1 Pro: Halo Reach main menu at 30 fps; Halo 3 campaign cutscene plus in-game menus at ~44 fps |

Known limitations:
- RRL never handles keys, BIOS or encrypted content. You supply dumps of your own hardware and games.
- Halo 2 map edits that keep data the same size boot in xemu once RRL recomputes the signature (tested 2026-11-15). Edits that resize data need offset fixups, which aren't implemented yet.
- All macOS Xbox 360 emulators are young community forks. First M1 Pro result (2026-11-15): Halo: Reach in macXenos 1.0 (Metal) reaches the main menu at 30 fps with a 1.7 GB footprint. Gameplay is untested.
