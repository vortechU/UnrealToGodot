"""Run INSIDE Unreal (full editor boot with the map positional):

    UnrealEditor-Cmd.exe <project>.uproject <map> \
      -ExecutePythonScript="tests/probe_height_sampling.py" -unattended -nopause -nosplash

Probes the CPU-side landscape height API (get_height_at_location) as a
replacement for the dead GPU merge-render export: existence, call rate,
and whether the returned heights actually vary across the landscape.
Also checks ImageWriteBlueprintLibrary availability for EXR writes.
"""
import time
import unreal

unreal.log("=== HEIGHT SAMPLING PROBE START ===")

sub = unreal.get_editor_subsystem(unreal.EditorActorSubsystem)
actors = sub.get_all_level_actors()
proxy_class = getattr(unreal, "LandscapeProxy", None)
parent_class = getattr(unreal, "Landscape", None)
landscapes = [a for a in actors if a is not None and isinstance(a, proxy_class)]
landscape = None
for a in landscapes:
    if parent_class is not None and isinstance(a, parent_class):
        landscape = a
        break
if landscape is None and landscapes:
    landscape = landscapes[0]
if landscape is None:
    unreal.log_error("no landscape found")
else:
    unreal.log("landscape: %s" % landscape.get_actor_label())
    origin, extent = landscape.get_actor_bounds(False)
    unreal.log("bounds center=%s extent=%s" % (origin, extent))

    has_api = hasattr(landscape, "get_height_at_location")
    unreal.log("get_height_at_location exists: %s" % has_api)
    if has_api:
        # single probe at the center
        try:
            r = landscape.get_height_at_location(unreal.Vector(origin.x, origin.y, 0.0))
            unreal.log("center sample -> %s" % (r,))
        except Exception as e:
            unreal.log("center sample raised: %r" % e)
        # timed grid: 64 x 64 across the bounds
        n = 64
        t0 = time.time()
        got = 0
        mn = 1e30
        mx = -1e30
        for iy in range(n):
            wy = origin.y - extent.y + (2.0 * extent.y) * iy / (n - 1)
            for ix in range(n):
                wx = origin.x - extent.x + (2.0 * extent.x) * ix / (n - 1)
                try:
                    r = landscape.get_height_at_location(unreal.Vector(wx, wy, 0.0))
                except Exception:
                    continue
                ok, h = (r[0], r[1]) if isinstance(r, (tuple, list)) else (bool(r), 0.0)
                if ok:
                    got += 1
                    mn = min(mn, h)
                    mx = max(mx, h)
        dt = time.time() - t0
        unreal.log("grid %dx%d: hits=%d/%d, height range=(%.2f .. %.2f) cm, %.2fs total, %.0f calls/s"
                   % (n, n, got, n * n, mn, mx, dt, (n * n) / dt if dt > 0 else 0))

unreal.log("ImageWriteBlueprintLibrary: %s, ImageWriteOptions: %s, DesiredImageFormat.EXR: %s" % (
    getattr(unreal, "ImageWriteBlueprintLibrary", None) is not None,
    getattr(unreal, "ImageWriteOptions", None) is not None,
    getattr(getattr(unreal, "DesiredImageFormat", None), "EXR", None) is not None,
))
unreal.log("=== HEIGHT SAMPLING PROBE END ===")
