"""Run INSIDE Unreal (full editor boot with the map positional):

    UnrealEditor-Cmd.exe <project>.uproject <map> \
      -ExecutePythonScript="tests/probe_render_heightmap.py" -unattended -nopause -nosplash

Tests ALandscape.render_heightmap (UE 5.3+) as the replacement for the dead
landscape_export_heightmap_to_render_target: prints the Python docstrings for
the exact signatures, then tries transform/extents combinations and reads back
pixel stats to see which one actually draws, and in what value range.
"""
import unreal

unreal.log("=== RENDER_HEIGHTMAP PROBE START ===")

world = unreal.get_editor_subsystem(unreal.UnrealEditorSubsystem).get_editor_world()
sub = unreal.get_editor_subsystem(unreal.EditorActorSubsystem)
actors = sub.get_all_level_actors()
parent = None
for a in actors:
    if a is not None and isinstance(a, unreal.Landscape):
        parent = a
        break
if parent is None:
    unreal.log_error("no parent Landscape")
    raise SystemExit(1)

for fn in ("render_heightmap", "render_weightmap", "render_weightmaps"):
    doc = getattr(getattr(unreal.Landscape, fn, None), "__doc__", None)
    unreal.log("DOC %s: %s" % (fn, doc))

unreal.log("Box2D doc: %s" % getattr(getattr(unreal, "Box2D", None), "__doc__", "MISSING"))


def rt_stats(rt, size):
    mins = [1e30] * 4
    maxs = [-1e30] * 4
    for y in range(0, size, max(1, size // 6)):
        for x in range(0, size, max(1, size // 6)):
            c = unreal.RenderingLibrary.read_render_target_raw_pixel(world, rt, x, y)
            for i, v in enumerate((c.r, c.g, c.b, c.a)):
                mins[i] = min(mins[i], v)
                maxs[i] = max(maxs[i], v)
    return list(zip([round(v, 4) for v in mins], [round(v, 4) for v in maxs]))


SIZE = 505
combos = []
try:
    empty_box = unreal.Box2D()
    combos.append(("actor_transform + empty box", parent.get_actor_transform(), empty_box))
    combos.append(("identity + empty box", unreal.Transform(), unreal.Box2D()))
except Exception as e:
    unreal.log("Box2D() raised: %r" % e)
try:
    wb = unreal.Box2D()
    wb.min = unreal.Vector2D(-100800.0, -100800.0)
    wb.max = unreal.Vector2D(100800.0, 100800.0)
    try:
        wb.is_valid = 1
    except Exception:
        pass
    combos.append(("identity + world box", unreal.Transform(), wb))
except Exception as e:
    unreal.log("world Box2D raised: %r" % e)

for label, xform, box in combos:
    try:
        rt = unreal.RenderingLibrary.create_render_target2d(world, SIZE, SIZE, unreal.TextureRenderTargetFormat.RTF_R32F)
        parent.render_heightmap(xform, box, rt)
        unreal.log("RENDER %s -> %s" % (label, rt_stats(rt, SIZE)))
    except Exception as e:
        unreal.log("RENDER %s raised: %r" % (label, e))

unreal.log("=== RENDER_HEIGHTMAP PROBE END ===")
