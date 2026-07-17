# Why texture sizing lives on the Godot side

A real level exports around 3.1 GB of textures — mostly 4K PNGs, and in the test
project 193 of 235 textures are 4096×4096 with three at 8192×8192. The obvious
place to fix that is the exporter, and the exporter used to have a **Max Texture
Resolution** dropdown that appeared to do it.

It did nothing. This note records what was measured, so the idea does not get
rebuilt from scratch every time someone notices the export is enormous.

## The original bug

```python
tex.set_editor_property("max_texture_size", 1024)   # then export with TextureExporterPNG
```

`max_texture_size` drives the **cooked** (platform) texture. `TextureExporterPNG`
writes the **source** art. They are unrelated, so the setting is a pure no-op for
the exported file. Measured on UE 5.7 with a 4096×4096 texture:

| | result |
| --- | --- |
| as-is export | 4096×4096, 24.0 MB |
| `max_texture_size=1024` | 4096×4096, 24.0 MB (byte-identical) |
| after save + rebuild | 4096×4096 |

`ResizeDuringBuildX/Y` is the same category of setting and fails the same way.

## Why no render-target route works either

The natural next idea is to render the texture into a `TextureRenderTarget2D` of
the capped size and export that. It genuinely produces a smaller PNG, and it is
still wrong, because **the GPU only ever holds the cooked texture**. The source
art never reaches it. Anything sampled from the GPU therefore inherits the cooked
texture's block compression, its streamed mip residency, and its size cap.

Measured against a PIL-downscaled reference built from the real export
(`tests/probe_texture_export_routes.py` + `tests/compare_texture_exports.py`):

| texture | reference mean | RT `RGBA8` | RT `RGBA8_SRGB` |
| --- | --- | --- | --- |
| albedo (sRGB) | `[83.1, 82.5, 82.5]` | `[25.2, 25.0, 25.0]` — far too dark | `[84.5, 84.0, 84.0]` — close |
| packed ORM (linear) | `[218.7, 119.7, 169.8]` | `[218.7, 119.6, 169.6]` — close | `[237.6, 181.6, 185.5]` — wrong |
| **normal map** | `[127.8, 127.7, 245.7]` | `[127.6, 127.4, **0.0**]` | `[187.4, 187.1, **0.0**]` |

Three independent problems:

1. **Gamma** is only right when the render-target format matches the texture's
   `srgb` flag — solvable, but it means picking a format per texture.
2. **Normal maps are destroyed.** `TC_NORMALMAP` cooks to BC5, which stores only
   X and Y; the shader reconstructs Z. A raw sample hands back a **constant 0**
   blue channel (extrema `(0, 0)` against the source's `(127, 255)`). 70 of 235
   textures here are normal maps. This is not a bug to fix — it is what BC5 is.
3. **Detail is lost.** Canvas draws whatever mip is resident. Texture streaming
   leaves textures at 32×32 until forced, so the result is a blurry upscale:
   high-pass detail score 0.4 against the reference's 4–8. Forcing mips resident
   (`set_force_mip_levels_to_be_resident` + `mip_load_options = ALL_MIPS`) fixes
   the residency but not points 1 or 2.

## Every other Unreal route, and why it is out

| route | result |
| --- | --- |
| `RenderingLibrary.export_texture2d` | Writes **Radiance HDR** (`#?RADIANCE`), not PNG. 64 MB for one 4K texture — *larger* than the PNG — and no alpha. |
| `TextureExporterTGA` / `TextureExporterBMP` | Read source art and are uncompressed, but write **24-bit** — alpha is dropped, which silently destroys packed masks. |
| Resize the PNG in Unreal's Python | No `numpy`, no `PIL` (`zlib` is available). Hand-rolled decode measures **~8 s per 4K texture**, ≈25 min per level, before resizing or re-encoding. |
| `TextureExporterDDS` | Exports cooked data, so it would honour the cap — but it is a pipeline-wide format change and still carries the BC5 normal-map problem. |

There is no Unreal Python API that resizes source art.

## What is done instead

Godot's `Image.resize` has none of these problems: it resamples the exported PNG's
raw channel values, so normal maps, packed masks and alpha all survive, and there
is no gamma reinterpretation. The importer dock owns texture sizing:

* **Texture size limit** caps what Godot imports (also fixes the WebP-packer OOM
  that takes the editor down on a real texture set).
* **Also shrink texture files on disk** rewrites oversized exported PNGs at the
  capped size — this is what actually reclaims the disk space.

Measured on the real 4K exports, capped to 1024 (`tests/test_texture_shrink.py`):

```
TX_BULLPUP_BC   4096x4096 -> 1024x1024   14.2MB -> 0.52MB
TX_BULLPUP_NMR  4096x4096 -> 1024x1024   16.0MB -> 0.82MB   blue extrema (108,255) -- intact
TX_BULLPUP_ORM  4096x4096 -> 1024x1024    7.6MB -> 0.83MB

total: 37.8 MB -> 2.2 MB (94.2% smaller)
```

The normal map's blue channel is the line to watch: `(108, 255)` here versus the
render-target route's constant `(0, 0)`.

Shrinking is destructive — the full-resolution art only exists back in Unreal —
so it is a visible, opt-out checkbox rather than silent behaviour.

## Reproducing this

The Unreal-side probe needs a **real RHI**, which a commandlet does not have by
default (`FApp::CanEverRender()` is false, so `BeginDrawCanvasToRenderTarget`
hands back a null canvas). Add `-AllowCommandletRendering`:

```bash
UnrealEditor-Cmd.exe <project>.uproject -run=pythonscript \
  -script="tests/probe_texture_export_routes.py" -unattended -nopause -nosplash \
  -AllowCommandletRendering

python tests/compare_texture_exports.py C:/scratch/texquality
```

Two traps worth knowing if you extend that probe:

* **`blueprint_get_size_x()` reports the resident mip, not the source.** Under
  streaming it returns 32 for a 4096 texture. The asset registry's `Dimensions`
  tag gives true source size without even loading the asset.
* **`get_assets_by_class` silently returns a partial list** while the registry is
  still scanning — call `wait_for_completion()` first, or a "largest texture"
  scan will confidently report engine content only.
