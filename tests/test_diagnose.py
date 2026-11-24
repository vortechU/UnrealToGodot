"""Regression tests for ue2g_diagnose.py and export_gameplay.py."""
import json
import os
import shutil
import sys
import tempfile
import time
import types

TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
PY_DIR = os.path.join(os.path.dirname(TESTS_DIR), "UnrealToGodot", "Content", "Python")
sys.path.insert(0, PY_DIR)

import ue2g_diagnose as D

FAIL = []


def check(name, cond, detail=""):
    print(("  PASS  " if cond else "  FAIL  ") + name + ("  " + str(detail) if not cond else ""))
    if not cond:
        FAIL.append(name)


print("\n=== 1. find_layout_json picks the newest *_layout.json ===")
tmp = tempfile.mkdtemp(prefix="ue2g_diag_")
try:
    old_default = os.path.join(tmp, "level_layout.json")
    newer_map = os.path.join(tmp, "MyMap_layout.json")
    with open(old_default, "w", encoding="utf-8") as f:
        f.write("{}")
    os.utime(old_default, (1000000, 1000000))
    with open(newer_map, "w", encoding="utf-8") as f:
        f.write("{}")
    os.utime(newer_map, (2000000, 2000000))

    picked = D.find_layout_json(tmp)
    check("newer <Map>_layout.json wins over stale level_layout.json",
          os.path.basename(picked) == "MyMap_layout.json", picked)

    print("\n=== 2. audit_models and layout_texture_names cover foliage & landscapes ===")
    models_dir = os.path.join(tmp, "GLTF")
    os.makedirs(models_dir, exist_ok=True)
    with open(os.path.join(models_dir, "SM_Box.gltf"), "w", encoding="utf-8") as f:
        json.dump({"asset": {"version": "2.0"}}, f)

    layout = {
        "format_version": 2,
        "actors": [{"name": "Box", "components": [{"mesh_key": "SM_Box", "godot_world_transform": {}}]}],
        "meshes": {},
        "foliage": [{
            "name": "Foliage_SM_Grass",
            "mesh_key": "SM_Grass_Missing",
            "material_overrides": [{"parameters": {"albedo_texture": "T_Foliage_Override"}}],
        }],
        "landscapes": [{
            "material": {"textures": [{"texture": "T_Terrain_Grass", "role": "albedo"}]},
        }],
    }
    rep = D.Report()
    D.audit_models(rep, models_dir, layout, strict=True)
    check("missing foliage .gltf is caught by audit_models",
          any("SM_Grass_Missing" in e for e in rep.errors), rep.errors)

    tex_names = D.layout_texture_names(layout)
    check("foliage override texture is included in layout_texture_names",
          "T_Foliage_Override" in tex_names, tex_names)
    check("landscape material texture is included in layout_texture_names",
          "T_Terrain_Grass" in tex_names, tex_names)
finally:
    shutil.rmtree(tmp, ignore_errors=True)

print("\n=== 3. export_gameplay.collect_navigation uses world AABB origin ===")
u = types.ModuleType("unreal")


class _Vec:
    def __init__(self, x=0.0, y=0.0, z=0.0):
        self.x, self.y, self.z = float(x), float(y), float(z)


class _NavVol:
    def __init__(self, label, origin, extent):
        self._label = label
        self._origin = origin
        self._extent = extent

    def get_actor_label(self):
        return self._label

    def get_actor_bounds(self, _only_colliding):
        return self._origin, self._extent

    def get_actor_transform(self):
        # Deliberately different from the AABB origin/rotation to catch drift.
        return types.SimpleNamespace(
            translation=_Vec(9999.0, 9999.0, 9999.0),
            rotation=types.SimpleNamespace(x=0.0, y=0.0, z=0.70710678, w=0.70710678),
            scale3d=_Vec(5.0, 5.0, 5.0),
        )


u.Vector = _Vec
u.NavMeshBoundsVolume = _NavVol
u.RecastNavMesh = type("RecastNavMesh", (), {})
u.Color = type("Color", (), {})
u.LinearColor = type("LinearColor", (), {})
u.log = u.log_warning = u.log_error = lambda *a: None
sys.modules["unreal"] = u

import export_gameplay as EG

nav = EG.collect_navigation([_NavVol("NavVol_Offset", _Vec(100.0, 200.0, 300.0), _Vec(500.0, 600.0, 400.0))])
check("navigation bounds volume exported", nav is not None and len(nav["bounds_volumes"]) == 1, nav)
vol = nav["bounds_volumes"][0]
check("region placed at AABB origin (converted to Godot m), not displaced actor pivot",
      vol["godot_transform"]["translation"] == [2.0, 3.0, -1.0],
      vol["godot_transform"]["translation"])
check("region uses identity rotation and unit scale to match world AABB extent",
      vol["godot_transform"]["rotation_quat"] == [0.0, 0.0, 0.0, 1.0]
      and vol["godot_transform"]["scale"] == [1.0, 1.0, 1.0],
      vol["godot_transform"])
check("extent_m matches converted half-extents",
      vol["extent_m"] == [6.0, 4.0, 5.0], vol["extent_m"])

print("\n" + "=" * 60)
if FAIL:
    print("FAILURES (%d): %s" % (len(FAIL), FAIL))
    sys.exit(1)
print("ALL DIAGNOSE & GAMEPLAY CHECKS PASSED")
