# Renegade Remaster Lab (RRL)

Renegade Remaster Lab is a pre-alpha toolkit for preserving and inspecting games you own.
It identifies original Xbox XISO and Redump disc images. For Halo 2 on the original Xbox,
it can inspect maps and tags, extract textures as PNGs, and export models, level geometry
and animations for Blender. macOS on Apple Silicon is the first target.

**Bring your own legally owned disc** and supply an image you dumped yourself. RRL ships code
and synthetic test fixtures only. Game images, executables, extracted assets, BIOS, firmware,
keys and links to them are excluded. RRL reads original images without modifying them.
See the [project policy](LEGAL.md).

## Install from a checkout

With uv installed, run the following from the repository root. The project supports Python
3.12 or newer and uses 3.13 for development. uv creates a local virtual environment.

```sh
uv sync
uv run rrl doctor
```

Doctor reports the workspace location, database schema, installed adapters and optional tools.
The default workspace is `~/RRL-Workspace`. To choose another folder outside the checkout,
set `RRL_WORKSPACE` before running doctor and keep it set for the remaining steps.
The workspace holds the database, write-once asset store and logs.

## From a disc image to Blender

1. Replace the example path with your own image. Registration computes its SHA-256 hash and
   prints a source ID. Use that ID in the following commands (the example uses `1`).

   ```sh
   uv run rrl source add "/path/to/your/game.iso"
   uv run rrl identify 1
   uv run rrl inspect 1
   ```

2. For the original Xbox release of Halo 2, select a map by name without its extension and
   list the asset kinds after it. This example exports Ascension. Set `EXPORT` to a folder
   outside the checkout and keep its directory structure intact so Blender can find the PNGs.

   ```sh
   EXPORT="$HOME/RRL-Exports"
   uv run rrl extract 1 --only ascension textures models levels animations --export "$EXPORT"
   ```

   Extraction reports stored and skipped assets. Look under `$EXPORT/ascension/` for
   PNGs, model files and one level file per structure BSP (a section of level geometry).
   Supported animation clips are embedded in their paired model's `.glb`.

3. Open a model, level or first-person scene with the bundled Blender 5.x importer. From
   the repository root, run this example to open the level in a new scene. Use **File → Save As**
   to keep the scene as a Blender project.

   ```sh
   blender --python scripts/blender/rrl_import.py -- "$EXPORT/ascension/ascension_bsp0.glb"
   ```

## Upscale exported textures

With a local `realesrgan-ncnn-vulkan` executable and its 4× model files in the adjacent
`models/` folder, run the following against the export from step 2.

```sh
OUT="$HOME/RRL-Upscaled"
uv run rrl upscale 1 --export "$EXPORT" --out "$OUT" \
  --upscaler "/path/to/realesrgan-ncnn-vulkan"
```

The executable path comes from `--upscaler`, then `RRL_UPSCALER`, then the workspace's
`rrl.toml`. To configure it once, add this setting and run `uv run rrl doctor` to check it.

```toml
[tools]
upscaler = "/path/to/realesrgan-ncnn-vulkan"
```

By default, the command processes eligible PNGs from every map in the export. Use
`--only ascension/ mainmenu/` to select logical path prefixes, or `--packable-only`
to require xemu pack hashes. It reports skipped bump and normal maps, Halo 2 P8
lightmaps and 3D slices by path and reason.

`--model` defaults to `realesrgan-x4plus`. The backend enhances RGB at 4×. Use
`--scale 2` for a 2× result. With `--scale 4` (the default), a result whose longer side
would exceed `--max-side` (default 4096 pixels) also gets resized to 2×.
The original alpha channel is resized separately to the final dimensions.

The output mirrors the export paths. Identical input blobs share one enhancement for
the same model and effective scale. Rerun the command to skip matching outputs and
restore missing copies from the workspace store. Use a fresh output folder for a
different model or scale. Each enhanced artifact records its extracted input, model and scale.

Pass `--textures "$OUT"` to the Blender importer, or use `"$OUT"` as the images folder
for `rrl pack build`. To put the edits into a disc copy, use batch injection.

## Remaster a pre-rendered video

Use an input movie that ffmpeg can decode and a local Real-ESRGAN installation. A full
movie can take hours. Allow space beside the output for the decoded PNG sequence and its upscaled copy.

```sh
uv run rrl video remaster intro.mkv --out intro-4x.mp4 --upscaler "/path/to/realesrgan-ncnn-vulkan"
```

RRL deinterlaces frames flagged as interlaced and decodes to a constant frame rate.
Use `--no-deinterlace` to disable the filter. The default model is `realesrgan-x4plus`
at 4×. Use `--model NAME` to select another native 4× model or `--scale 2` for a 2× result.
Tool paths come from `--upscaler` and `--ffmpeg`, then `RRL_UPSCALER` and `RRL_FFMPEG`,
then `upscaler` and `ffmpeg` under `[tools]` in the workspace's `rrl.toml`. ffmpeg
also defaults to the executable on PATH.

After an interruption, rerun the same command. Completed PNGs in `intro-4x.frames/out`
are reused, along with the decoded sequence in `intro-4x.frames/in`. The final MP4 uses
HEVC (VideoToolbox on macOS, with a libx265 fallback) and copies source audio tracks when
MP4 supports their codec. Other audio is encoded as AAC. RRL publishes the MP4 after
muxing all frames, then removes the frame directory unless you pass `--keep-frames`.

## Inject edited textures into a disc copy

Keep each edited PNG at its original relative path, such as `ascension/objects/wall_00.png`.
Use the same source ID or image path you extracted from and choose a new output filename.
This example takes two maps from the upscaled tree and resizes their replacements to the
dimensions stored on disc.

```sh
uv run rrl inject 1 --from "$OUT" --out edited.iso --only ascension/ mainmenu/ --resize
```

Omit `--only` to include every matching PNG. Prefixes match relative export paths and can
select individual textures. For native-size edits, omit `--resize`. The command skips
size mismatches in that mode. Injection supports 2D DXT1 (opaque), DXT3 and DXT5.
The JSON report gives injected and skipped counts by the map that owns the tag, a reason
for each skipped PNG, and signatures for the map files that hold the edited pixels.

RRL stages one copy, re-signs each touched map once and publishes the copy after validation.
If two edits require different bytes at the same shared location, the batch fails and the
output path stays unused. Keep one edit for that location or make both edits agree.
If all matching PNGs are skipped, the output is an unchanged copy. An empty selection
reports an error.

## Blender importer

For a headless still, put `--quiet` before `-b` so the JSON report is the final stdout line.
`--python-exit-code 1` gives shell scripts a failure status on a Python error.

```sh
blender --quiet -b --factory-startup --python-exit-code 1 \
  --python scripts/blender/rrl_import.py -- "$EXPORT/ascension/ascension_bsp0.glb" \
  --preview preview.png
```

| Importer option (after `--`) | Effect |
|---|---|
| `--strength S` | Scale lightmap emission. The default is 1.0. Each lit instance multiplies its diffuse colour by the lightmap on its second UV set and retains diffuse alpha for cutouts and blending. |
| `--clip NAME` | Enable the named animation and set the playback range to its strip. The first exported clip plays by default. The JSON report lists available names and frame ranges. |
| `--root-motion` | Bake per-frame movement into the armature object's location and rotation for clips with movement extras. |
| `--textures DIR` | Load matching replacement PNGs, including lightmaps. Point this at the upscaler output root that mirrors the export tree (`DIR/<map>/…`), or use paths relative to the GLB directory (`DIR/textures/…`). Missing replacements fall back to the export. |
| `--preview OUT.png` | Frame the visible meshes in the selected pose and render a 512 × 512 Workbench texture still. Workbench shows the diffuse texture. Use Material Preview or Rendered view to inspect the lightmap shader. |
| `--turntable N` | With `--preview`, render N equally spaced views of the same pose. For `--preview model.png --turntable 24`, the sequence runs from `model_0001.png` to `model_0024.png`, and `model.png` holds the first view. |

Each model region gets a collection. Arms and weapons with the same region name get separate
collections when their skins identify the parts. The first exported permutation in each
collection is visible in both the viewport and renders. To switch variants, toggle both visibility settings in the Outliner.
Shader template, bitmap slots and diffuse-source metadata are available in the material's
custom properties. Bitmap lists with empty slots use JSON text to preserve the slot indices.

In the NLA Editor, base and replacement clips occupy separate tracks on the armature.
Replacement strips use Blender's REPLACE blend mode. To switch clips manually, mute the
current strip, unmute another and move the playhead into its frame range.

Root motion uses the exported clip rate and object-space deltas. **[M]** The importer starts
at rest, translates forward/left/up using the previous heading, then applies the yaw delta
wrapped to `(-π, π]`. The last delta ends one frame after the last pose sample. The game's
accumulation order remains unverified. See the [animation format notes](documentation/platforms/halo2_xbox.md).

The JSON report counts imported objects, distinct assigned materials, lightmapped objects
and relinked images. It also lists the visible permutation per region, clips and warnings
such as unavailable lightmap pages. [Importer source](scripts/blender/rrl_import.py)

## Formats and limits

| Workflow | Supported today | Known limits |
|---|---|---|
| Texture extraction | DXT1/3/5, A8, Y8, AY8, A8Y8, R5G6B5, A1R5G5B5, A4R4G4B4, X8R8G8B8, A8R8G8B8, V8U8, G8B8, P8 lightmaps and P8 bump maps | PNGs contain the first mip of LOD0. Cube faces keep their stored orientation. Supported volumes export one PNG per slice. DXT volumes and floating-point ARGBFP32, RGBFP32 and RGBFP16 are unsupported. P8 needs a validated palette, and P8 bump needs a recognised executable revision. |
| Geometry and animation | Render models, skins and level geometry as glTF 2.0 binary files | Overlay animations and blend screens are skipped. Root motion is stored as metadata. |
| Texture replacement | Emulator texture packs for the custom xemu build, or injection of same-size 2D DXT1/3/5 textures into a new disc copy | DXT1 injection requires opaque pixels. Cube-map injection, larger replacements and general disc repacking are outside the current implementation. |

The [platform notes](documentation/platforms/halo2_xbox.md) document individual formats,
validation and skip conditions. The [technical decisions](documentation/TECHNICAL_DECISIONS.md)
explain texture packs and glTF export. See the [roadmap](ROADMAP.md) for planned platforms.

## Licence

GPL-3.0-or-later. See [LICENSE](LICENSE).
