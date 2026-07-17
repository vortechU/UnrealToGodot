"""Runs INSIDE Unreal. Evidence for docs/texture-sizing.md -- the record of why
the exporter cannot resize textures and Godot does it instead.

Exports one albedo, one normal map and one packed mask by every route Unreal
offers, so the claims in that document can be re-checked on a new engine version:

  A  TextureExporterPNG      -- what ships today (source art, full size)
  B  RenderingLibrary.export_texture2d
  C  RenderTarget2D + canvas, in both RGBA8 and RGBA8_SRGB
  D  TextureExporterTGA / BMP -- uncompressed, but check the bit depth

Needs a real RHI, which a commandlet lacks by default (FApp::CanEverRender() is
false, so BeginDrawCanvasToRenderTarget hands back a null canvas):

    UnrealEditor-Cmd.exe <project>.uproject -run=pythonscript \\
      -script="tests/probe_texture_export_routes.py" -unattended -nopause \\
      -nosplash -AllowCommandletRendering
    python tests/compare_texture_exports.py C:/scratch/texquality

Sets properties in memory only -- it never saves a user asset.
"""
import os
import struct
import unreal

OUT = "C:/scratch/texquality"
os.makedirs(OUT, exist_ok=True)

def log(msg):
    unreal.log("[ROUTES] %s" % msg)

def describe(path):
    """Identify a written file by magic bytes rather than by its extension --
    export_texture2d writes Radiance HDR to whatever name you hand it."""
    if not os.path.isfile(path):
        return "MISSING"
    size = os.path.getsize(path)
    with open(path, "rb") as f:
        head = f.read(64)
    if head[:8] == b"\x89PNG\r\n\x1a\n":
        w, h = struct.unpack(">II", head[16:24])
        return "PNG %dx%d %.2fMB" % (w, h, size / 1048576.0)
    if head[:2] == b"#?":
        return "Radiance HDR %.2fMB (no alpha)" % (size / 1048576.0)
    if head[:2] == b"BM":
        w, h = struct.unpack("<ii", head[18:26])
        bpp = struct.unpack("<H", head[28:30])[0]
        return "BMP %dx%d bpp=%d %.2fMB%s" % (w, h, bpp, size / 1048576.0,
                                              "  ALPHA DROPPED" if bpp < 32 else "")
    return "fmt? %.2fMB magic=%r" % (size / 1048576.0, head[:8])


def describe_tga(path):
    if not os.path.isfile(path):
        return "MISSING"
    size = os.path.getsize(path)
    with open(path, "rb") as f:
        head = f.read(18)
    imgtype = head[2]
    w, h, bpp = struct.unpack("<HHB", head[12:17])
    kind = {2: "uncompressed", 10: "RLE"}.get(imgtype, "type%d" % imgtype)
    return "TGA %dx%d bpp=%d %s %.2fMB%s" % (
        w, h, bpp, kind, size / 1048576.0, "  ALPHA DROPPED" if bpp < 32 else "")

log("=== TEXTURE EXPORT ROUTES START ===")
world = unreal.get_editor_subsystem(unreal.UnrealEditorSubsystem).get_editor_world()

# Kill texture streaming so the GPU has real mip 0, not the 32x32 resident mip.
for cmd in ["r.TextureStreaming 0", "r.Streaming.FullyLoadUsedTextures 1"]:
    unreal.SystemLibrary.execute_console_command(world, cmd)
    log("cmd: %s" % cmd)

# wait_for_completion() first: while the registry is still scanning,
# get_assets_by_class returns a partial list (engine content only) without
# saying so, and every size reported off it is a lie.
ar = unreal.AssetRegistryHelpers.get_asset_registry()
ar.wait_for_completion()
assets = ar.get_assets_by_class(unreal.TopLevelAssetPath("/Script/Engine", "Texture2D"), True)
by_name = {str(a.asset_name): a for a in assets}

# albedo (sRGB), normal map (BC5 -- the one every GPU route flattens),
# packed mask (linear, needs all channels)
PICKS = ["TX_BULLPUP_BC", "TX_BULLPUP_NMR", "TX_BULLPUP_ORM"]
CAP = 1024

for name in PICKS:
    entry = by_name.get(name)
    if entry is None:
        log("!! %s not found" % name)
        continue
    tex = entry.get_asset()
    dims = entry.get_tag_value("Dimensions")
    log("--- %s  sourceDims=%s srgb=%s comp=%s resident=%sx%s" % (
        name, dims, tex.get_editor_property("srgb"),
        str(tex.get_editor_property("compression_settings")).split(".")[-1],
        tex.blueprint_get_size_x(), tex.blueprint_get_size_y()))

    # force mips resident for this texture
    try:
        tex.set_force_mip_levels_to_be_resident(60.0)
        tex.set_editor_property("mip_load_options", unreal.TextureMipLoadOptions.ALL_MIPS)
    except Exception as e:
        log("  force mips: %s" % str(e).splitlines()[0])
    log("  resident after force: %sx%s" % (tex.blueprint_get_size_x(), tex.blueprint_get_size_y()))

    def run_export(exporter, path):
        task = unreal.AssetExportTask()
        task.object = tex
        task.filename = path
        task.automated = True
        task.prompt = False
        task.replace_identical = True
        task.exporter = exporter
        unreal.Exporter.run_asset_export_tasks([task])
        return path

    # A: baseline -- full-res source PNG, what ships today. The comparison
    # script downscales this with PIL to build its reference.
    log("  A baseline PNG    -> %s" % describe(
        run_export(unreal.TextureExporterPNG(), os.path.join(OUT, "%s__baseline.png" % name))))

    # B: reads platform data and writes Radiance HDR, whatever you name it.
    try:
        unreal.RenderingLibrary.export_texture2d(world, tex, OUT, "%s__b.bin" % name)
        log("  B export_texture2d -> %s" % describe(os.path.join(OUT, "%s__b.bin" % name)))
    except Exception as e:
        log("  B export_texture2d FAILED: %s" % str(e).splitlines()[0])

    # D: uncompressed source art -- but check the bit depth before trusting it.
    log("  D TGA             -> %s" % describe_tga(
        run_export(unreal.TextureExporterTGA(), os.path.join(OUT, "%s__d.tga" % name))))
    log("  D BMP             -> %s" % describe(
        run_export(unreal.TextureExporterBMP(), os.path.join(OUT, "%s__d.bmp" % name))))

    # C: the render-target route. Both formats, because the right one depends on
    # the texture's srgb flag -- and neither can save a BC5 normal map.
    for fmt_name, fmt in [("RGBA8", unreal.TextureRenderTargetFormat.RTF_RGBA8),
                          ("RGBA8_SRGB", unreal.TextureRenderTargetFormat.RTF_RGBA8_SRGB)]:
        try:
            rt = unreal.RenderingLibrary.create_render_target2d(world, CAP, CAP, fmt)
            canvas, size, ctx = unreal.RenderingLibrary.begin_draw_canvas_to_render_target(world, rt)
            if canvas is None:
                log("  %s: canvas None" % fmt_name)
                continue
            canvas.draw_texture(tex, unreal.Vector2D(0, 0), unreal.Vector2D(CAP, CAP),
                                unreal.Vector2D(0, 0), unreal.Vector2D(1, 1),
                                unreal.LinearColor(1, 1, 1, 1),
                                unreal.BlendMode.BLEND_OPAQUE, 0.0, unreal.Vector2D(0, 0))
            unreal.RenderingLibrary.end_draw_canvas_to_render_target(world, ctx)
            fn = "%s__rt_%s.png" % (name, fmt_name)
            unreal.RenderingLibrary.export_render_target(world, rt, OUT, fn)
            log("  rt %-10s -> %s" % (fmt_name, describe(os.path.join(OUT, fn))))
        except Exception as e:
            log("  rt %s FAILED: %s" % (fmt_name, str(e).splitlines()[0]))

log("=== TEXTURE EXPORT ROUTES END ===")
