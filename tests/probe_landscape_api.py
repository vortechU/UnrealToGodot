"""Run INSIDE Unreal (full editor boot with the map positional):

    UnrealEditor-Cmd.exe <project>.uproject <map> \
      -ExecutePythonScript="tests/probe_landscape_api.py" -unattended -nopause -nosplash

Wide probe of the landscape export surface on a streaming-proxy landscape:
per-actor bounds and components, height/export-related API names, per-proxy
render-target export, and collision-component line traces.
"""
import unreal

unreal.log("=== LANDSCAPE API PROBE START ===")

world = unreal.get_editor_subsystem(unreal.UnrealEditorSubsystem).get_editor_world()
sub = unreal.get_editor_subsystem(unreal.EditorActorSubsystem)
actors = sub.get_all_level_actors()
proxy_class = getattr(unreal, "LandscapeProxy", None)
parent_class = getattr(unreal, "Landscape", None)
landscapes = [a for a in actors if a is not None and isinstance(a, proxy_class)]

parent = None
proxies = []
for a in landscapes:
    if parent_class is not None and isinstance(a, parent_class):
        parent = a
    else:
        proxies.append(a)

# 1. Bounds and component census
def describe(a):
    o, e = a.get_actor_bounds(False)
    lc = a.get_components_by_class(unreal.LandscapeComponent) if hasattr(unreal, "LandscapeComponent") else []
    hc = a.get_components_by_class(unreal.LandscapeHeightfieldCollisionComponent) if hasattr(unreal, "LandscapeHeightfieldCollisionComponent") else []
    return "label=%s cls=%s bounds_extent=(%.0f,%.0f,%.0f) landscape_comps=%d collision_comps=%d" % (
        a.get_actor_label(), type(a).__name__, e.x, e.y, e.z, len(lc), len(hc))

unreal.log("parent: %s" % (describe(parent) if parent else None))
for p in proxies[:3]:
    unreal.log("proxy:  %s" % describe(p))
unreal.log("proxy count: %d" % len(proxies))

# 2. Union bounds across proxies (candidate replacement for parent bounds)
if proxies:
    mn = [1e30] * 3
    mx = [-1e30] * 3
    for p in proxies + ([parent] if parent else []):
        o, e = p.get_actor_bounds(False)
        if e.x <= 0 and e.y <= 0:
            continue
        for i, (c, ext) in enumerate(((o.x, e.x), (o.y, e.y), (o.z, e.z))):
            mn[i] = min(mn[i], c - ext)
            mx[i] = max(mx[i], c + ext)
    unreal.log("union bounds: min=%s max=%s" % (mn, mx))

# 3. API surface: names that could replace the dead merge-render export
target = parent if parent else (proxies[0] if proxies else None)
if target:
    names = [m for m in dir(target) if any(k in m.lower() for k in ("height", "export", "sample", "render"))]
    unreal.log("parent api candidates: %s" % names)
if hasattr(unreal, "LandscapeHeightfieldCollisionComponent"):
    names = [m for m in dir(unreal.LandscapeHeightfieldCollisionComponent)
             if any(k in m.lower() for k in ("height", "trace", "sample"))]
    unreal.log("collision comp api candidates: %s" % names)

# 4. Per-proxy render-target export + readback
def rt_stats(rt, size):
    mins = [1e30] * 4
    maxs = [-1e30] * 4
    for y in range(0, size, max(1, size // 6)):
        for x in range(0, size, max(1, size // 6)):
            c = unreal.RenderingLibrary.read_render_target_raw_pixel(world, rt, x, y)
            for i, v in enumerate((c.r, c.g, c.b, c.a)):
                mins[i] = min(mins[i], v)
                maxs[i] = max(maxs[i], v)
    return list(zip([round(v, 5) for v in mins], [round(v, 5) for v in maxs]))

if proxies:
    p = proxies[len(proxies) // 2]
    try:
        rt = unreal.RenderingLibrary.create_render_target2d(world, 127, 127, unreal.TextureRenderTargetFormat.RTF_R32F)
        try:
            p.landscape_export_heightmap_to_render_target(rt, False, False)
        except TypeError:
            p.landscape_export_heightmap_to_render_target(rt, False)
        unreal.log("PROXY RT export stats (%s): %s" % (p.get_actor_label(), rt_stats(rt, 127)))
    except Exception as e:
        unreal.log("PROXY RT export raised: %r" % e)

# 5. Line trace against a proxy's collision component
if proxies:
    p = proxies[len(proxies) // 2]
    comps = p.get_components_by_class(unreal.LandscapeHeightfieldCollisionComponent) if hasattr(unreal, "LandscapeHeightfieldCollisionComponent") else []
    if comps:
        c = comps[0]
        o = c.get_world_location() if hasattr(c, "get_world_location") else None
        try:
            b = c.get_local_bounds()
            unreal.log("comp local bounds: %s" % (b,))
        except Exception as e:
            unreal.log("get_local_bounds raised: %r" % e)
        try:
            origin = c.k2_get_component_location()
            start = unreal.Vector(origin.x + 100.0, origin.y + 100.0, origin.z + 100000.0)
            end = unreal.Vector(origin.x + 100.0, origin.y + 100.0, origin.z - 100000.0)
            hit = c.line_trace_component(start, end, True, False)
            unreal.log("line_trace_component -> %s" % (hit,))
        except Exception as e:
            unreal.log("line_trace_component raised: %r" % e)
    else:
        unreal.log("no collision components on proxy")

# 6. World-level line trace by channel (buildings interfere in general, but
#    verifies traces work at all in this context)
try:
    start = unreal.Vector(0.0, -20000.0, 100000.0)
    end = unreal.Vector(0.0, -20000.0, -100000.0)
    hit = unreal.SystemLibrary.line_trace_single(
        world, start, end, unreal.TraceTypeQuery.TRACE_TYPE_QUERY1,
        False, [], unreal.DrawDebugTrace.NONE, True, unreal.LinearColor.RED, unreal.LinearColor.GREEN, 0.0)
    if hit:
        unreal.log("world trace hit: actor=%s z=%.1f" % (
            hit.hit_object_handle if hasattr(hit, "hit_object_handle") else "?",
            hit.location.z if hasattr(hit, "location") else -1))
    else:
        unreal.log("world trace: no hit")
except Exception as e:
    unreal.log("world trace raised: %r" % e)

unreal.log("=== LANDSCAPE API PROBE END ===")
