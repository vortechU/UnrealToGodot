"""Landscape exporter tests. Stubs `unreal` so export_landscape runs outside
the engine, and models the UE 5.7.4 API surface that was probed in the audit --
including the two shapes that used to break it silently:

  * landscape_export_heightmap_to_render_target returns True while leaving the
    render target constant (asset-pack landscapes with no edit layers),
  * landscape_export_weightmap_to_render_target does not exist at all.

The engine-truth numbers cited here come from probing UE 5.7.4 against
ModularSciFiStation's landscape; see export_landscape's module docstring.
"""
import os
import struct
import sys
import tempfile
import types

# ---------------------------------------------------------------- unreal stub
u = types.ModuleType("unreal")


class _Vec:
    def __init__(self, x=0.0, y=0.0, z=0.0):
        self.x, self.y, self.z = float(x), float(y), float(z)


class _Name(str):
    pass


class _Transform:
    def __init__(self):
        self.translation = _Vec()
        self.rotation = types.SimpleNamespace(x=0.0, y=0.0, z=0.0, w=1.0)
        self.scale3d = _Vec(1.0, 1.0, 1.0)


u.Vector = _Vec
u.Name = _Name
u.Transform = _Transform
u.Box2D = type("Box2D", (), {})
u.Quat = type("Quat", (), {})
u.Rotator = type("Rotator", (), {})
u.LinearColor = type("LinearColor", (), {})
u.Color = type("Color", (), {})
u.log = lambda *a: None
u.log_warning = lambda *a: None
u.log_error = lambda *a: None


class LandscapeProxy(object):
    pass


class Landscape(LandscapeProxy):
    pass


class LandscapeStreamingProxy(LandscapeProxy):
    pass


class LandscapeComponent(object):
    pass


class LandscapeHeightfieldCollisionComponent(object):
    pass


u.LandscapeProxy = LandscapeProxy
u.Landscape = Landscape
u.LandscapeStreamingProxy = LandscapeStreamingProxy
u.LandscapeComponent = LandscapeComponent
u.LandscapeHeightfieldCollisionComponent = LandscapeHeightfieldCollisionComponent
u.TextureRenderTargetFormat = types.SimpleNamespace(
    RTF_RGBA32F="RTF_RGBA32F", RTF_RGBA16F="RTF_RGBA16F", RTF_R32F="RTF_R32F")
u.UnrealEditorSubsystem = type("UnrealEditorSubsystem", (), {})
u.get_editor_subsystem = lambda cls: types.SimpleNamespace(get_editor_world=lambda: "WORLD")
sys.modules["unreal"] = u

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                                "UnrealToGodot", "Content", "Python"))
import export_landscape as EX  # noqa: E402

FAILS = []


def check(cond, msg):
    print(("  PASS  " if cond else "  FAIL  ") + msg)
    if not cond:
        FAILS.append(msg)


# ===========================================================================
print("1. _ComponentGrid: exact lattice binning")
# ===========================================================================
# The real landscape: 32x32 components on a 12600 cm pitch, origins running
# -201599.9 .. 189000.1 (probed). Origins are the component's MIN CORNER, so
# nearest-origin lookup is wrong -- that mistake dropped the collision-trace
# hit rate to 27%.
PITCH = 12600.0
X0, Y0 = -201599.9, -221998.4
entries = []
for iy in range(32):
    for ix in range(32):
        entries.append((X0 + ix * PITCH, Y0 + iy * PITCH, "c_%d_%d" % (ix, iy)))
grid = EX._ComponentGrid(entries)
check(grid.ok, "grid builds")
check(abs(grid.pitch_x - PITCH) < 0.01 and abs(grid.pitch_y - PITCH) < 0.01,
      "pitch derived as %.1f/%.1f (want %.1f)" % (grid.pitch_x, grid.pitch_y, PITCH))
check(grid.at(X0, Y0) == "c_0_0", "min corner -> c_0_0")
# A point 90% of the way through cell (5, 7) must still be cell (5, 7), even
# though its NEAREST origin is cell (6, 8).
px = X0 + 5 * PITCH + 0.9 * PITCH
py = Y0 + 7 * PITCH + 0.9 * PITCH
check(grid.at(px, py) == "c_5_7", "interior point -> owning cell, not nearest origin (got %s)" % grid.at(px, py))
# The far edge of the landscape belongs to the last cell, not to nothing.
check(grid.at(X0 + 32 * PITCH, Y0 + 32 * PITCH) == "c_31_31",
      "far edge clamps into the last cell (got %s)" % grid.at(X0 + 32 * PITCH, Y0 + 32 * PITCH))
single = EX._ComponentGrid([(0.0, 0.0, "only")])
check(single.ok and single.at(9e9, -9e9) == "only", "a single component answers everywhere")

# ===========================================================================
print("2. _write_exr_r32 round-trip")
# ===========================================================================
tmp = tempfile.mkdtemp(prefix="ue2g_ls_")
W, H = 7, 5
vals = [float(i) * 1.5 - 3.0 for i in range(W * H)]
path = os.path.join(tmp, "h.exr")
EX._write_exr_r32(path, W, H, vals)
raw = open(path, "rb").read()
check(raw[:4] == b"\x76\x2f\x31\x01", "EXR magic written")
check(EX._sniff_format(path) == "exr", "_sniff_format identifies it")
idx = raw.find(b"dataWindow\x00box2i\x00")
x0, y0, x1, y1 = struct.unpack_from("<4i", raw, idx + len(b"dataWindow\x00box2i\x00") + 4)
check((x1 - x0 + 1, y1 - y0 + 1) == (W, H), "dataWindow says %dx%d" % (x1 - x0 + 1, y1 - y0 + 1))
scanline = 8 + W * 4
start = len(raw) - H * scanline
decoded = []
for y in range(H):
    off = start + y * scanline
    ly, sz = struct.unpack_from("<ii", raw, off)
    assert ly == y and sz == W * 4, "scanline header %d/%d" % (ly, sz)
    decoded.extend(struct.unpack_from("<%df" % W, raw, off + 8))
check(all(abs(a - b) < 1e-6 for a, b in zip(vals, decoded)), "every float survives the round-trip")

# ===========================================================================
print("3. _sniff_format / _rename_to_actual_format")
# ===========================================================================
# ExportRenderTarget picks the format from the RT and ignores the extension:
# an RGBA32F target asked for .exr comes out as PNG bytes (measured on 5.7.4).
png_path = os.path.join(tmp, "lying.exr")
open(png_path, "wb").write(b"\x89PNG\r\n\x1a\n" + b"\x00" * 24)
check(EX._sniff_format(png_path) == "png", "PNG bytes under an .exr name are detected")
renamed = EX._rename_to_actual_format(tmp, "lying.exr")
check(renamed == "lying.png", "renamed to %s" % renamed)
check(os.path.exists(os.path.join(tmp, "lying.png")), "the renamed file is on disk")
check(EX._rename_to_actual_format(tmp, "h.exr") == "h.exr", "a truthfully named file is left alone")

# ===========================================================================
print("4. _discover_layers across the three engine API shapes")
# ===========================================================================


class _Settings(object):
    def __init__(self, info):
        self._info = info

    def get_editor_property(self, name):
        if name == "layer_info_obj":
            return self._info
        raise Exception("no %s" % name)


class _Info(object):
    def __init__(self, name, colour=None):
        self._name = name
        self._colour = colour

    def get_editor_property(self, name):
        if name == "layer_name":
            return _Name(self._name)
        if name == "layer_usage_debug_color" and self._colour is not None:
            return types.SimpleNamespace(r=self._colour[0], g=self._colour[1], b=self._colour[2])
        raise Exception("no %s" % name)

    def get_name(self):
        return self._name


class _Modern(Landscape):
    """UE 5.7: get_target_layer_names() + a target_layers map."""

    def __init__(self):
        self._map = {_Name("1"): _Settings(_Info("1", (0.83, 0.57, 0.29))),
                     _Name("2"): _Settings(_Info("2", (0.71, 0.77, 0.76)))}

    def get_target_layer_names(self):
        return [_Name("1"), _Name("2")]

    def get_editor_property(self, name):
        if name == "target_layers":
            return self._map
        raise Exception("no %s" % name)


class _MapOnly(Landscape):
    """target_layers present, accessor absent."""

    def __init__(self):
        self._map = {_Name("Grass"): _Settings(_Info("Grass"))}

    def get_editor_property(self, name):
        if name == "target_layers":
            return self._map
        raise Exception("no %s" % name)


class _Legacy(Landscape):
    """UE4 / early UE5: editor_layer_settings only."""

    def get_editor_property(self, name):
        if name == "editor_layer_settings":
            return [_Settings(_Info("Dirt")), _Settings(_Info("Sand"))]
        raise Exception("no %s" % name)


modern = EX._discover_layers(_Modern())
check([n for n, _ in modern] == ["1", "2"], "5.7 accessor path -> %s" % [n for n, _ in modern])
check(EX._layer_debug_color(modern[0][1]) == [0.83, 0.57, 0.29],
      "layer_usage_debug_color read as linear RGB -> %s" % (EX._layer_debug_color(modern[0][1]),))
check([n for n, _ in EX._discover_layers(_MapOnly())] == ["Grass"], "target_layers map path")
check([n for n, _ in EX._discover_layers(_Legacy())] == ["Dirt", "Sand"], "legacy editor_layer_settings path")


class _NoLayers(Landscape):
    def get_editor_property(self, name):
        raise Exception("nothing readable")


check(EX._discover_layers(_NoLayers()) == [], "a landscape with no readable layers yields none, not a crash")

# ===========================================================================
print("5. proxy grouping uses get_landscape_actor()")
# ===========================================================================
# `landscape_guid` is NOT a readable property on 5.7, so GUID matching silently
# lumped every proxy onto the first parent. get_landscape_actor() is the live
# API and it was probed to work.


class _Parent(Landscape):
    def __init__(self, tag):
        self.tag = tag

    def get_path_name(self):
        return "/Game/Map." + self.tag


class _Child(LandscapeStreamingProxy):
    def __init__(self, parent):
        self._parent = parent

    def get_landscape_actor(self):
        return self._parent

    def get_path_name(self):
        return "/Game/Map.child"


pa, pb = _Parent("A"), _Parent("B")
ca, cb = _Child(pa), _Child(pb)
check(EX._parent_landscape(ca, Landscape) is pa, "child A resolves to parent A")
check(EX._parent_landscape(cb, Landscape) is pb, "child B resolves to parent B")


class _Orphan(LandscapeStreamingProxy):
    def get_landscape_actor(self):
        raise Exception("unloaded")

    def get_path_name(self):
        return "/Game/Map.orphan"


check(EX._parent_landscape(_Orphan(), Landscape) is None, "an unresolvable proxy returns None, not a crash")

# ===========================================================================
print("6. _union_actor_bounds ignores the empty parent")
# ===========================================================================
# The parent of a proxy-based landscape owns no components: its own bounds come
# back empty headless and DOUBLE the real extent in the GUI (that shipped a
# 4 km entry for a 2 km terrain). The proxies' union is ground truth.


class _Bounded(object):
    def __init__(self, c, e):
        self._c, self._e = c, e

    def get_actor_bounds(self, only_colliding):
        return _Vec(*self._c), _Vec(*self._e)


empty_parent = _Bounded((0, 0, 0), (0, 0, 0))
p1 = _Bounded((-5000, 0, 100), (5000, 5000, 300))
p2 = _Bounded((5000, 0, 100), (5000, 5000, 300))
c, e = EX._union_actor_bounds([empty_parent, p1, p2])
check(abs(c.x) < 1e-6 and abs(e.x - 10000.0) < 1e-6,
      "union spans both proxies: centre x=%.1f extent x=%.1f" % (c.x, e.x))
check(abs(e.y - 5000.0) < 1e-6, "the empty parent contributes nothing (extent y=%.1f)" % e.y)
c2, e2 = EX._union_actor_bounds([empty_parent])
check(c2 is None, "an all-empty group returns (None, None) so the caller can fall back")

# ===========================================================================
print("7. the GPU heightmap path is not believed on its word")
# ===========================================================================


class _RT(object):
    def __init__(self, values):
        self.values = values


class _FakeRendering(object):
    """read_render_target_raw_pixel returns linear RED (1,0,0,1) as its own
    failure sentinel -- constant red must count as 'no data', not as data."""

    current = None

    @staticmethod
    def create_render_target2d(world, w, h, fmt):
        return _FakeRendering.current

    @staticmethod
    def read_render_target_raw_pixel(world, rt, x, y):
        return rt.values(x, y)

    @staticmethod
    def export_render_target(world, rt, directory, filename):
        open(os.path.join(directory, filename), "wb").write(b"\x76\x2f\x31\x01" + b"\x00" * 8)


u.RenderingLibrary = _FakeRendering


class _LyingLandscape(Landscape):
    """UE 5.7 asset-pack landscape: the legacy call reports success and leaves
    the target cleared; render_heightmap honestly returns False."""

    def __init__(self):
        self.legacy_calls = []

    def get_actor_transform(self):
        return _Transform()

    def render_heightmap(self, xform, extents, rt):
        return False

    def landscape_export_heightmap_to_render_target(self, rt, rg, proxies=True):
        self.legacy_calls.append((rg, proxies))
        return True


zero = lambda x, y: types.SimpleNamespace(r=0.0, g=0.0, b=0.0, a=0.0)
red = lambda x, y: types.SimpleNamespace(r=1.0, g=0.0, b=0.0, a=1.0)
varying = lambda x, y: types.SimpleNamespace(r=float(x) * 0.01, g=0.0, b=0.0, a=1.0)

lying = _LyingLandscape()
_FakeRendering.current = _RT(zero)
out = EX._gpu_heightmap_to_file("WORLD", lying, "L", tmp, "gpu.exr", 64, 64)
check(out is None, "a constant-zero render target is rejected (got %r)" % out)
_FakeRendering.current = _RT(red)
check(EX._gpu_heightmap_to_file("WORLD", lying, "L", tmp, "gpu.exr", 64, 64) is None,
      "the red readback sentinel is rejected too")
# The legacy call must ask for the height recombined into a single R channel;
# with the flag False the importer reads only the high half of a 16-bit height.
check(lying.legacy_calls and all(rg is True for rg, _ in lying.legacy_calls),
      "export_height_into_rg_channel=True is passed (got %r)" % (lying.legacy_calls,))
_FakeRendering.current = _RT(varying)
check(EX._gpu_heightmap_to_file("WORLD", lying, "L", tmp, "gpu.exr", 64, 64) == "gpu.exr",
      "a render target with real variation IS accepted")

# RTF_RGBA32F must be preferred: it is the only format both the export call
# accepts and read_render_target_raw_pixel can read back.
tried = []


class _RecordingRendering(_FakeRendering):
    @staticmethod
    def create_render_target2d(world, w, h, fmt):
        tried.append(fmt)
        return _FakeRendering.current


u.RenderingLibrary = _RecordingRendering
EX._create_render_target("WORLD", 64, 64)
check(tried and tried[0] == "RTF_RGBA32F", "RTF_RGBA32F is tried first (order: %s)" % tried)

# ===========================================================================
print("8. weightmaps: the 5.7 method is gone, so it must be hasattr-guarded")
# ===========================================================================
u.RenderingLibrary = _FakeRendering
_FakeRendering.current = _RT(zero)


class _NoWeightmapAPI(Landscape):
    """Exactly UE 5.7: neither render_weightmap nor the removed legacy call."""

    def get_actor_transform(self):
        return _Transform()


check(EX._gpu_weightmap_to_file("WORLD", _NoWeightmapAPI(), "1", tmp, "w.exr", 64, 64) is None,
      "a landscape with no weightmap API returns None instead of raising")

print("")
if FAILS:
    print("FAILED %d check(s):" % len(FAILS))
    for f in FAILS:
        print("  - " + f)
    sys.exit(1)
print("landscape exporter: all checks passed")
