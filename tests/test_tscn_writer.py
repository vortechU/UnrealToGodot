"""Synthetic end-to-end test for tscn_writer.py (runs without Unreal)."""
import os
import re
import sys
import math

import os, sys
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                                "UnrealToGodot", "Content", "Python"))
import tscn_writer

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fake_godot_project")
MODELS = os.path.join(ROOT, "models")
TEXTURES = os.path.join(ROOT, "textures")
os.makedirs(MODELS, exist_ok=True)
os.makedirs(TEXTURES, exist_ok=True)

# Fake assets on disk so ext_resource existence checks pass
for f in ["SM_Rock.gltf", "SM_Crate.gltf", "SM_Grass.gltf"]:
    open(os.path.join(MODELS, f), "w").write("{}")
open(os.path.join(TEXTURES, "T_Blood.png"), "w").write("x")

IDENT = {"translation": [0.0, 0.0, 0.0], "rotation_quat": [0.0, 0.0, 0.0, 1.0], "scale": [1.0, 1.0, 1.0]}

def t(x, y, z, yaw_deg=0.0, s=1.0):
    half = math.radians(yaw_deg) / 2.0
    return {"translation": [x, y, z], "rotation_quat": [0.0, math.sin(half), 0.0, math.cos(half)], "scale": [s, s, s]}

layout = {
    "format_version": 2,
    "level_name": "TestLevel",
    "meshes": {
        "SM_Rock": {
            "path": "/Game/SM_Rock.SM_Rock", "export_name": "SM_Rock",
            "collision": {
                "boxes": [{"size": [100.0, 80.0, 60.0], "godot_local_transform": IDENT}],
                "spheres": [], "convex_hulls": [],
                "capsules": [{"radius": 30.0, "length": 100.0, "godot_local_transform": IDENT}],
            },
            "materials": [],
        },
        "SM_Crate": {"path": "/Game/SM_Crate.SM_Crate", "export_name": "SM_Crate", "collision": None, "materials": []},
        "SM_Grass": {"path": "/Game/SM_Grass.SM_Grass", "export_name": "SM_Grass", "collision": None, "materials": []},
        "SM_Missing": {"path": "/Game/SM_Missing.SM_Missing", "export_name": "SM_Missing", "collision": None, "materials": []},
    },
    "actors": [
        {
            # Plain StaticMeshActor: UE reports the ROOT component's relative
            # transform as the world transform, identical to the actor's. The
            # exporter emits both, and they match -- exactly the shape that used
            # to get double-composed into t(2, 4, 6).
            "name": "Rock_1", "class": "StaticMeshActor",
            "godot_transform": t(1, 2, 3, 45.0),
            "tags": ["cover"], "properties": {"health": 50, "is_interactable": True},
            "components": [{"name": "SM0", "mesh_key": "SM_Rock", "mesh_name": "SM_Rock",
                            "godot_relative_transform": t(1, 2, 3, 45.0),
                            "godot_world_transform": t(1, 2, 3, 45.0),
                            "material_overrides": []}],
        },
        {
            "name": "Blueprint_Multi", "class": "BP_Thing_C",
            "godot_transform": t(-5, 0, 2),
            "tags": [], "properties": {},
            "components": [
                {"name": "CrateA", "mesh_key": "SM_Crate", "mesh_name": "SM_Crate",
                 "godot_relative_transform": t(0.5, 0, 0),
                 "godot_world_transform": t(-4.5, 0, 2), "material_overrides": []},
                {"name": "CrateB", "mesh_key": "SM_Crate", "mesh_name": "SM_Crate",
                 "godot_relative_transform": t(-0.5, 0, 0, 90.0),
                 "godot_world_transform": t(-5.5, 0, 2, 90.0), "material_overrides": []},
            ],
        },
        {
            "name": "Missing_1", "class": "StaticMeshActor", "godot_transform": IDENT,
            "components": [{"name": "SM0", "mesh_key": "SM_Missing", "mesh_name": "SM_Missing",
                            "godot_relative_transform": IDENT, "material_overrides": []}],
        },
    ],
    "lights": [
        {"name": "Sun", "type": "directional", "godot_transform": t(0, 10, 0, 30.0),
         "color": [1.0, 0.95, 0.9], "godot_energy": 1.2, "cast_shadows": True,
         "use_temperature": False, "temperature_kelvin": None, "indirect_intensity": 1.0, "visible": True},
        {"name": "Spot_1", "type": "spot", "godot_transform": t(2, 3, 1),
         "color": [1.0, 0.5, 0.2], "godot_energy": 3.0, "cast_shadows": False,
         "attenuation_radius_m": 12.0, "inner_cone_angle_deg": 20.0, "outer_cone_angle_deg": 40.0,
         "use_temperature": False, "temperature_kelvin": None, "indirect_intensity": 1.0, "visible": True},
    ],
    "post_process": [
        {"name": "PP", "unbound": True, "priority": 0.0, "godot_transform": IDENT, "extent_m": [10, 10, 10],
         "settings": {"bloom_intensity": 0.675, "bloom_threshold": 1.0, "ao_intensity": 0.5,
                      "ao_radius": 200.0, "exposure_bias": 0.5, "exposure_method": "manual",
                      "white_temp": None, "saturation": None, "contrast": None, "vignette_intensity": None}},
    ],
    "height_fog": {"fog_density": 0.02, "fog_height_falloff": 0.2, "color": [0.6, 0.7, 0.8], "start_distance_m": 0.0},
    "sky_light": {"intensity": 1.0, "color": [1, 1, 1]},
    "has_sky_atmosphere": True,
    "decals": [
        {"name": "Blood_1", "godot_transform": t(0, 0.1, 0), "size_m": [2.0, 0.5, 2.0], "sort_order": 3,
         "material_name": "M_Blood", "material_path": "/Game/M_Blood",
         "textures": {"albedo": "T_Blood", "normal": None, "orm": None, "emission": None}},
    ],
    "landscapes": [
        {"name": "Landscape_0", "godot_transform": IDENT, "heightmap_file": "terrain/L0_height.exr",
         "heightmap_resolution": [513, 513], "world_size_m": [504, 504], "world_center_m": [0, 25, 0],
         "height_range_m": [0.0, 50.0], "height_encoding": "normalized",
         "layers": [{"name": "Grass", "weightmap_file": "terrain/L0_weight_Grass.exr"}]},
    ],
    "foliage": [
        {"name": "Foliage_SM_Grass", "mesh_key": "SM_Grass", "mesh_name": "SM_Grass",
         "instance_count": 3, "source": "foliage",
         "godot_transforms": [1,0,0, 0,1,0, 0,0,1, 1,0,1,
                              1,0,0, 0,1,0, 0,0,1, 2,0,2,
                              0.5,0,0, 0,0.5,0, 0,0,0.5, 3,0,3]},
    ],
    "navigation": {
        "bounds_volumes": [{"name": "NavVol_1", "godot_transform": IDENT, "extent_m": [20, 5, 20]}],
        "agent_radius_m": 0.35, "agent_height_m": 1.92, "max_slope_deg": 44.0,
        "agent_max_step_height_m": 0.35, "cell_size_m": 0.19,
    },
}

out_path = os.path.join(ROOT, "test_level.tscn")
options = {
    "scene_name": "test_level", "godot_project_dir": ROOT, "light_energy_scale": 1.0,
    "lights": True, "decals": True, "foliage": True, "navigation": True, "metadata": True, "landscape": True,
}
res_paths = {"models": "res://models/", "textures": "res://textures/", "terrain": "res://terrain/"}

ok = tscn_writer.write_tscn(layout, out_path, res_paths, options)
assert ok, "write_tscn returned False"
assert os.path.exists(out_path), "output file missing"

text = open(out_path, encoding="utf-8").read()

# --- structural validation ---
m = re.search(r"\[gd_scene load_steps=(\d+) format=3", text)
assert m, "missing gd_scene header"
load_steps = int(m.group(1))
ext_count = len(re.findall(r"\n\[ext_resource ", "\n" + text))
sub_count = len(re.findall(r"\n\[sub_resource ", "\n" + text))
assert load_steps == 1 + ext_count + sub_count, f"load_steps {load_steps} != 1 + {ext_count} ext + {sub_count} sub"

ext_ids = set(re.findall(r'\[ext_resource[^\]]*id="([^"]+)"', text))
sub_ids = set(re.findall(r'\[sub_resource[^\]]*id="([^"]+)"', text))
for ref in re.findall(r'ExtResource\("([^"]+)"\)', text):
    assert ref in ext_ids, f"dangling ExtResource {ref}"
for ref in re.findall(r'SubResource\("([^"]+)"\)', text):
    assert ref in sub_ids, f"dangling SubResource {ref}"

# unique node names per parent
nodes = re.findall(r'\[node name="([^"]+)"(?:[^\]]*parent="([^"]*)")?', text)
seen = set()
for name, parent in nodes:
    key = (parent, name)
    assert key not in seen, f"duplicate node {key}"
    seen.add(key)

# missing mesh handled as placeholder, not ext_resource
assert "SM_Missing" not in ext_ids and "res://models/SM_Missing.gltf" not in text.replace("MISSING", ""), "missing mesh must not be referenced"
assert text.count('"') % 2 == 0, "unbalanced quotes"

# --- placement validation (regression guard for the double-transform bug) ---
def node_block(name):
    m = re.search(r'\[node name="%s"[^\]]*\](.*?)(?=\n\[|\Z)' % re.escape(name), text, re.S)
    assert m, "node %s not found" % name
    return m.group(1)

def origin_of(name):
    blk = node_block(name)
    m = re.search(r"transform = Transform3D\(([^)]*)\)", blk)
    assert m, "node %s has no transform" % name
    vals = [float(v) for v in m.group(1).split(",")]
    return vals[9:12]

def near(a, b, eps=1e-4):
    return all(abs(x - y) < eps for x, y in zip(a, b))

# Rock_1 has collision -> StaticBody3D carries the component's WORLD transform.
# Pre-fix this was actor*relative = (2, 4, 6): the bug the user saw in-editor.
rock = origin_of("Rock_1")
assert near(rock, [1.0, 2.0, 3.0]), f"Rock_1 must sit at its world transform, got {rock}"
assert not near(rock, [2.0, 4.0, 6.0]), "Rock_1 is double-transformed"

# Blueprint children are re-expressed relative to the actor node at (-5, 0, 2),
# so CrateA's world (-4.5, 0, 2) must serialize as local (+0.5, 0, 0).
crate_a = origin_of("CrateA")
assert near(crate_a, [0.5, 0.0, 0.0]), f"CrateA local should be (0.5,0,0), got {crate_a}"
crate_b = origin_of("CrateB")
assert near(crate_b, [-0.5, 0.0, 0.0]), f"CrateB local should be (-0.5,0,0), got {crate_b}"
print("placement OK: Rock_1 at", rock, "| CrateA local", crate_a, "| CrateB local", crate_b)

print(f"OK: {ext_count} ext_resources, {sub_count} sub_resources, {len(nodes)} nodes, load_steps={load_steps}")
print("--- excerpt ---")
print("\n".join(text.splitlines()[:40]))
