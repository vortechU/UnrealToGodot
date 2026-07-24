"""Run INSIDE Unreal (full editor boot with the map positional). Probes the two
non-GPU landscape height sources on a no-edit-layers landscape where every
render path fails:
  1. line_trace_component against LandscapeHeightfieldCollisionComponents
     (timing + hit rate + height range over one proxy)
  2. direct LandscapeComponent heightmap texture property access
"""
import time
import unreal

unreal.log("=== TRACE HEIGHTS PROBE START ===")

sub = unreal.get_editor_subsystem(unreal.EditorActorSubsystem)
proxies = [a for a in sub.get_all_level_actors()
           if a is not None and isinstance(a, unreal.LandscapeProxy) and not isinstance(a, unreal.Landscape)]
if not proxies:
    unreal.log_error("no proxies")
    raise SystemExit(1)

p = proxies[len(proxies) // 2]
o, e = p.get_actor_bounds(False)
comps = list(p.get_components_by_class(unreal.LandscapeHeightfieldCollisionComponent))
unreal.log("proxy %s bounds=(%.0f,%.0f,%.0f)+-(%.0f,%.0f,%.0f) collision comps=%d"
           % (p.get_actor_label(), o.x, o.y, o.z, e.x, e.y, e.z, len(comps)))

unreal.log("line_trace_component doc: %s" % unreal.PrimitiveComponent.line_trace_component.__doc__)

# --- 1. trace grid over the proxy ---
n = 48
t0 = time.time()
hits = 0
mn, mx = 1e30, -1e30
first_logged = False
for iy in range(n):
    wy = o.y - e.y + (2.0 * e.y) * (iy + 0.5) / n
    for ix in range(n):
        wx = o.x - e.x + (2.0 * e.x) * (ix + 0.5) / n
        start = unreal.Vector(wx, wy, o.z + e.z + 10000.0)
        end = unreal.Vector(wx, wy, o.z - e.z - 10000.0)
        for c in comps:
            try:
                res = c.line_trace_component(start, end, True, False)
            except Exception as ex:
                if not first_logged:
                    unreal.log("line_trace_component raised: %r" % ex)
                    first_logged = True
                res = None
            if not first_logged and res is not None:
                unreal.log("first trace result: %r" % (res,))
                first_logged = True
            hit_loc = None
            if isinstance(res, tuple) and len(res) >= 2 and res[0]:
                hit_loc = res[1].location if hasattr(res[1], "location") else None
            elif res is not None and not isinstance(res, tuple) and hasattr(res, "location"):
                hit_loc = res.location
            if hit_loc is not None:
                hits += 1
                mn = min(mn, hit_loc.z)
                mx = max(mx, hit_loc.z)
                break
dt = time.time() - t0
unreal.log("trace grid %dx%d: hits=%d/%d height=(%.1f .. %.1f)cm in %.2fs (%.0f traces/s)"
           % (n, n, hits, n * n, mn, mx, dt, (n * n) / dt if dt > 0 else 0))

# --- 2. LandscapeComponent heightmap texture introspection ---
lcomps = list(p.get_components_by_class(unreal.LandscapeComponent))
unreal.log("landscape comps=%d" % len(lcomps))
if lcomps:
    lc = lcomps[0]
    for prop in ("heightmap_texture", "heightmap_scale_bias", "section_base_x", "section_base_y",
                 "component_size_quads", "subsection_size_quads", "num_subsections"):
        try:
            unreal.log("lc.%s = %s" % (prop, lc.get_editor_property(prop)))
        except Exception as ex:
            unreal.log("lc.%s -> %r" % (prop, ex))

unreal.log("=== TRACE HEIGHTS PROBE END ===")
