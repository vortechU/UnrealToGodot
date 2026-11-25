"""Regression tests for the third-pass fixes on the Unreal side and in tscn_writer.
The Godot side of the same pass is in godot_harness/make_test_plugin.py (_test_sweep3).
"""
import json
import math
import os
import sys
import tempfile
import types

TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
PY_DIR = os.path.join(os.path.dirname(TESTS_DIR), "UnrealToGodot", "Content", "Python")
sys.path.insert(0, PY_DIR)


class _Stub(types.ModuleType):
    """Any attribute is an empty class, except the ones a test leaves out."""
    def __getattr__(self, name):
        if name.startswith("__") or name in ("GLTFExporter", "SystemLibrary"):
            raise AttributeError(name)
        cls = type(name, (), {})
        setattr(self, name, cls)
        return cls


u = _Stub("unreal")
u.log = u.log_warning = u.log_error = lambda *a: None
u.get_editor_subsystem = lambda c: None
sys.modules["unreal"] = u

import ue2g_common as C
import ue2g_diagnose as D
import export_environment as E
import export_landscape as L
import export_static_meshes_to_gltf as G
import tscn_writer as W

FAILS = []


def check(name, cond, detail=""):
    print(("  PASS  " if cond else "  FAIL  ") + name + ("" if cond else "  -> %r" % (detail,)))
    if not cond:
        FAILS.append(name)


def read(name):
    with open(os.path.join(PY_DIR, name), encoding="utf-8") as f:
        return f.read()


print("=== 1. Relocating baked textures ===")
d = tempfile.mkdtemp()
art, tex = os.path.join(d, "art"), os.path.join(d, "textures")
os.makedirs(art)
os.makedirs(tex)
for folder, text in ((art, "mine"), (tex, "other")):
    with open(os.path.join(folder, "my_photo.png"), "w") as f:
        f.write(text)
before = G._png_names(art)
open(os.path.join(art, "Baked.png"), "w").close()
G.relocate_baked_textures(art, tex, keep=before)
check("PNGs that were there before the export stay", os.listdir(art) == ["my_photo.png"], os.listdir(art))
with open(os.path.join(tex, "my_photo.png")) as f:
    check("and overwrite nothing", f.read() == "other")
check("a PNG the export wrote moves", os.path.exists(os.path.join(tex, "Baked.png")))
src = read("export_static_meshes_to_gltf.py")
check("both exports take the snapshot and pass it on",
      src.count("pngs_before = _png_names(export_dir)") == 2 and src.count("keep=pngs_before") == 2)

print("=== 3. Foliage chunks ===")
flat = []
for i in range(400):
    flat += [1, 0, 0, 0, 1, 0, 0, 0, 1, float(i), 0.0, 0.0]
chunks = W._cull_chunks(flat, 400, 50.0)
check("a culled group is split, losing nothing", len(chunks) == 16 and sorted(sum(chunks, [])) == list(range(400)))
check("an unculled one is not", W._cull_chunks(flat, 400, 0.0) == [list(range(400))])
huge = []
for i in range(2000):
    huge += [1, 0, 0, 0, 1, 0, 0, 0, 1, i * 50.0, 0.0, (i % 40) * 2500.0]
check("the chunk count is bounded", len(W._cull_chunks(huge, 2000, 1.0)) <= 33 * 33)
out = os.path.join(d, "f.tscn")
W.write_tscn({"foliage": [{"name": "Grass", "mesh_key": "SM", "cull_end_m": 50.0, "godot_transforms": flat}],
              "landscapes": [{"name": "Hid", "visible": False}, {"name": "Shown"}]},
             out, {}, {"godot_project_dir": d, "foliage": True, "landscape": True})
with open(out, encoding="utf-8") as f:
    text = f.read()
check("each chunk is its own node", text.count('type="MultiMeshInstance3D"') == 16 and 'name="Grass"' in text and 'name="Grass_16"' in text)
check("ranges reach past the cull distance by the chunk radius", "visibility_range_end = 62.0" in text)

print("=== 21. Hidden landscapes ===")
check("a hidden landscape is written hidden", text.count("visible = false") == 1)


class Actor:
    def __init__(self, ed=False, **props):
        self.ed, self.props = ed, props

    def is_hidden_ed(self):
        return self.ed

    def get_editor_property(self, name):
        return self.props[name]


check("hidden in editor or in game counts", L._is_hidden(Actor(ed=True)) and L._is_hidden(Actor(hidden=True))
      and not L._is_hidden(Actor(hidden=False)))

print("=== 8. Box reflection captures ===")
check("world scale is the half-extent in cm", "* 2.0 * ue2g_common.CM_TO_M) for i in range(3)" in read("export_environment.py")
      and "20.0) for i in range(3)" not in read("export_environment.py"))

print("=== 9. Rotated landscapes ===")
for yaw in (0.0, 180.0, 90.0, 37.0):
    cos, sin = math.cos(math.radians(yaw)), math.sin(math.radians(yaw))
    # 2 m of drift in world space: component origins are min corners in the local frame.
    entries = [(x * 100 * cos - y * 100 * sin, x * 100 * sin + y * 100 * cos, (x, y)) for x in range(4) for y in range(4)]
    grid = L._ComponentGrid(entries, yaw)
    bad = [(a, b) for a in range(5, 400, 10) for b in range(5, 400, 10)
           if grid.at(a * cos - b * sin, a * sin + b * cos) != (a // 100, b // 100)]
    check("yaw %g finds the right component everywhere" % yaw, not bad, bad[:3])


class Comp:
    def __init__(self, x, y):
        self.x, self.y = x, y

    def get_world_location(self):
        return types.SimpleNamespace(x=-self.x * 100.0, y=-self.y * 100.0)

    def get_world_rotation(self):
        return types.SimpleNamespace(yaw=180.0)

    def get_world_scale(self):
        return types.SimpleNamespace(x=100.0, y=100.0, z=100.0)


comps = [Comp(x, y) for x in range(3) for y in range(3)]
grid = L._build_component_grid([types.SimpleNamespace(get_components_by_class=lambda c: comps)], None)
hit = grid.at(-150.0, -250.0)
check("the grid takes its rotation from the components", (hit.x, hit.y) == (1, 2), (hit.x, hit.y))

print("=== 12 + 16. GUI ===")
gui = read("unreal_to_godot_gui.py")
check("an export of nothing is not a success", gui.count("elif exported == 0:") == 3)
poll = gui[gui.index("def poll_state"):]
check("a scene name does not follow into the next level", poll.index("self.tscn_name_entry.delete(0, tk.END)")
      < poll.index("self._last_auto_level_name = latest_level_name"))

print("=== 17. Diagnostic ===")
for layout, want in (({"actors": [], "foliage": [{}], "landscapes": [{}]}, 0), ({"actors": [], "lights": []}, 1)):
    path = os.path.join(d, "x_layout.json")
    with open(path, "w") as f:
        json.dump(layout, f)
    errors = [e for e in D.diagnose(d, layout_path=path, strict=False)[1].errors if "zero actors" in e]
    check("zero actors is an error only when nothing else is there (%s)" % sorted(layout), len(errors) == want, errors)

path = os.path.join(d, "lods_layout.json")
with open(path, "w") as f:
    json.dump({"actors": [], "lights": [], "meshes": {"SM_A": {
        "physics_file": "SM_A_physics.json",
        "lods": [{"lod_index": 0, "export_name": "SM_A"}, {"lod_index": 1, "export_name": "SM_A_LOD1"}]}}}, f)
for lods, want in ((True, "2 referenced"), (False, "1 referenced")):
    warned = [w for w in D.diagnose(d, layout_path=path, strict=False, lods=lods)[1].warnings if "sidecar" in w]
    check("missing LOD models are reported only when asked for (%s)" % lods,
          len(warned) == 1 and warned[0].startswith(want) and ("LOD1" in warned[0]) == lods, warned)
check("every export passes the LOD checkbox on", gui.count("lods=export_lods)") == gui.count("rep = _run_diagnostic("),
      gui.count("lods=export_lods)"))

print("=== 18. LOD name clash ===")


class Asset:
    def __init__(self, name, path):
        self.name, self.path = name, path

    def get_name(self):
        return self.name

    def get_path_name(self):
        return self.path


foo, lod, lone = Asset("SM_Foo", "/a"), Asset("SM_Foo_LOD1", "/b"), Asset("SM_Bar_LOD1", "/c")
names = C.build_export_name_map([foo, lod, lone], kind="mesh")
check("a mesh named like another's LOD file is renamed", names[foo] == "SM_Foo" and names[lod].startswith("SM_Foo_LOD1_")
      and names[lone] == "SM_Bar_LOD1", list(names.values()))
check("textures are not", C.build_export_name_map([foo, lod], kind="texture")[lod] == "SM_Foo_LOD1")

print("=== 19. Export count ===")
check("meshes are counted, not LOD files", src.count("exported_count += 1") == 2
      and src.count("exported_count += 1\n                exported_meshes.append(mesh)") == 2)

print("=== 21. Manual exposure ===")


class Settings(dict):
    def get_editor_property(self, name):
        return self[name]


def settings(**kw):
    out = Settings()
    for k, v in kw.items():
        out[k] = v
        out["override_" + k] = True
    return out


check("auto exposure is left alone", E._manual_exposure_luminance(settings(auto_exposure_method="AEM_HISTOGRAM")) is None)
off = E._manual_exposure_luminance(settings(auto_exposure_method="AEM_MANUAL", auto_exposure_apply_physical_camera_exposure=False))
check("without the physical camera EV100 is 0", abs(0.18 / off - 1 / 1.2) < 1e-9, off)
cam = E._manual_exposure_luminance(settings(auto_exposure_method="AEM_MANUAL", depth_of_field_fstop=2.0, camera_shutter_speed=100.0, camera_iso=400.0))
check("with it, EV100 is log2(N^2 * shutter * 100 / ISO)", abs(0.18 / cam - 1 / (1.2 * 100.0)) < 1e-9, cam)

print("=== 21. White balance ===")
warned = []
W._warn = warned.append
for temp in (6500.0, 4000.0):
    W.write_tscn({"post_process": [{"unbound": True, "settings": {"white_temp": temp}}]}, out, {}, {"godot_project_dir": d})
check("a white balance Godot cannot show is reported once", len([m for m in warned if "white balance (4000 K)" in m]) == 1
      and not [m for m in warned if "6500" in m], warned)

print()
if FAILS:
    print("FAILED:", FAILS)
    sys.exit(1)
print("ALL PASSED")
