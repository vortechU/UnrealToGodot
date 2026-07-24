"""Run INSIDE Unreal (-run=pythonscript, WITH -AllowCommandletRendering) to find
out why an exported landscape heightmap came out all-zero, and to verify the
EXR write path end to end.

    UnrealEditor-Cmd.exe <project>.uproject -run=pythonscript \
      -script="tests/probe_landscape_heightmap.py" -unattended -nopause \
      -nosplash -AllowCommandletRendering \
      -MapPath="/Game/ModularSciFiStation/Level/SciFiStationExampleMap" \
      -OutDir="C:/scratch/landscape_probe"

For each render-target format x export-flag combination it draws the landscape
heightmap into a small RT, reads pixels back, and logs per-channel min/max --
an all-zero readback means that combination produces no data. It then runs the
production _export_render_target on the working combination and sniffs what
landed on disk.
"""
import os
import sys
import unreal

MAP_PATH = os.environ.get("UE2G_MAP_PATH")
OUT_DIR = os.environ.get("UE2G_OUT_DIR")
# -run=pythonscript does not forward custom -Foo= args into sys.argv, so also
# scan the raw engine command line.
try:
    for arg in unreal.SystemLibrary.get_command_line().split():
        if arg.startswith("-MapPath="):
            MAP_PATH = arg.split("=", 1)[1].strip('"')
        elif arg.startswith("-OutDir="):
            OUT_DIR = arg.split("=", 1)[1].strip('"')
except Exception:
    pass

if not MAP_PATH or not OUT_DIR:
    unreal.log_error("probe requires UE2G_MAP_PATH/UE2G_OUT_DIR env vars or -MapPath=/-OutDir= args")
    sys.exit(2)
os.makedirs(OUT_DIR, exist_ok=True)

unreal.log("=== LANDSCAPE HEIGHTMAP PROBE START ===")

def _load_map(path):
    """Map loading differs between GUI and commandlet contexts; probe each API."""
    try:
        les = unreal.get_editor_subsystem(unreal.LevelEditorSubsystem)
        if les and les.load_level(path):
            return "LevelEditorSubsystem.load_level"
    except Exception as e:
        unreal.log("load_level raised: %s" % e)
    try:
        if unreal.EditorLoadingAndSavingUtils.load_map(path):
            return "EditorLoadingAndSavingUtils.load_map"
    except Exception as e:
        unreal.log("load_map raised: %s" % e)
    try:
        unreal.EditorLevelLibrary.load_level(path)
        return "EditorLevelLibrary.load_level"
    except Exception as e:
        unreal.log("EditorLevelLibrary.load_level raised: %s" % e)
    return None


loader = _load_map(MAP_PATH)
unreal.log("map loaded via: %s" % loader)

world = unreal.get_editor_subsystem(unreal.UnrealEditorSubsystem).get_editor_world()
unreal.log("editor world: %s" % (world.get_name() if world else None))
sub = unreal.get_editor_subsystem(unreal.EditorActorSubsystem)
actors = sub.get_all_level_actors()

proxy_class = getattr(unreal, "LandscapeProxy", None)
landscapes = [a for a in actors if a is not None and isinstance(a, proxy_class)]
unreal.log("landscape actors: %s" % [(a.get_actor_label(), type(a).__name__) for a in landscapes])
if not landscapes:
    unreal.log_error("no landscape in the map")
    sys.exit(1)
landscape = landscapes[0]
parent_class = getattr(unreal, "Landscape", None)
for a in landscapes:
    if parent_class is not None and isinstance(a, parent_class):
        landscape = a
        break
unreal.log("probing landscape '%s' (%s)" % (landscape.get_actor_label(), type(landscape).__name__))


def read_stats(rt, size):
    """Samples a grid of pixels, returns per-channel (min, max)."""
    mins = [1e30] * 4
    maxs = [-1e30] * 4
    step = max(1, size // 8)
    for y in range(0, size, step):
        for x in range(0, size, step):
            c = None
            try:
                c = unreal.RenderingLibrary.read_render_target_raw_pixel(world, rt, x, y)
            except Exception:
                pass
            if c is None:
                try:
                    c8 = unreal.RenderingLibrary.read_render_target_pixel(world, rt, x, y)
                    c = unreal.LinearColor(c8.r / 255.0, c8.g / 255.0, c8.b / 255.0, c8.a / 255.0)
                except Exception:
                    return None
            vals = (c.r, c.g, c.b, c.a)
            for i in range(4):
                mins[i] = min(mins[i], vals[i])
                maxs[i] = max(maxs[i], vals[i])
    return list(zip([round(v, 6) for v in mins], [round(v, 6) for v in maxs]))


SIZE = 505
results = {}
for fmt_name in ("RTF_R32F", "RTF_RGBA16F", "RTF_RGBA8"):
    fmt = getattr(unreal.TextureRenderTargetFormat, fmt_name, None)
    if fmt is None:
        unreal.log("SKIP %s (format not in this engine)" % fmt_name)
        continue
    for flag16 in (False, True):
        label = "%s/16bitRG=%s" % (fmt_name, flag16)
        try:
            rt = unreal.RenderingLibrary.create_render_target2d(world, SIZE, SIZE, fmt)
        except Exception as e:
            unreal.log("SKIP %s: create failed: %s" % (label, e))
            continue
        try:
            try:
                landscape.landscape_export_heightmap_to_render_target(rt, flag16, True)
            except TypeError:
                landscape.landscape_export_heightmap_to_render_target(rt, flag16)
        except Exception as e:
            unreal.log("FAIL %s: export raised: %s" % (label, e))
            continue
        stats = read_stats(rt, SIZE)
        results[label] = (rt, stats)
        unreal.log("STATS %s -> %s" % (label, stats))

# Pick a combination whose R channel actually varies and push it through the
# production writer to see what lands on disk.
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                                "UnrealToGodot", "Content", "Python"))
import export_landscape

chosen = None
for label, (rt, stats) in results.items():
    if stats and stats[0][1] - stats[0][0] > 1e-6:
        chosen = (label, rt)
        break
if chosen is None:
    unreal.log_error("NO combination produced height data -- rendering path is dead here")
else:
    label, rt = chosen
    unreal.log("writing '%s' via production _export_render_target..." % label)
    actual = export_landscape._export_render_target(world, rt, OUT_DIR, "probe_height.exr")
    unreal.log("written file: %s" % actual)
    if actual:
        path = os.path.join(OUT_DIR, actual)
        unreal.log("on-disk sniff: %s (%d bytes)" % (export_landscape._sniff_format(path), os.path.getsize(path)))

unreal.log("=== LANDSCAPE HEIGHTMAP PROBE END ===")
