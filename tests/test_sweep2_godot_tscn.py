"""Regression tests for the 15 second-pass fixes in the Godot addon and tscn_writer.py.

tscn_writer is exercised for real. The GDScript half is pinned by source markers here,
because this file runs without Godot; the same fixes are run inside the engine by
_test_sweep2 in godot_harness/make_test_plugin.py (run_tests.py --godot).
"""
import math
import os
import re
import sys
import tempfile

TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.dirname(TESTS_DIR)
ADDON_DIR = os.path.join(REPO_ROOT, "addons", "unreal_importer")
sys.path.insert(0, os.path.join(REPO_ROOT, "UnrealToGodot", "Content", "Python"))

import tscn_writer as W

FAIL = []


def check(name, cond, detail=""):
    print("  %s  %s%s" % ("PASS" if cond else "FAIL", name, "" if cond else "  %s" % (detail,)))
    if not cond:
        FAIL.append(name)


def read_gd(name):
    with open(os.path.join(ADDON_DIR, name), encoding="utf-8") as f:
        return f.read()


def func_body(src, name):
    m = re.search(r"^(?:static )?func %s\(.*?(?=^(?:static )?func |\Z)" % re.escape(name),
                  src, re.S | re.M)
    return m.group(0) if m else ""


GD = {n: read_gd("import_%s.gd" % n) for n in
      ("common", "environment", "gameplay", "terrain", "foliage", "spline_mesh",
       "skeletal_physics", "unreal_layout")}
DOCK = read_gd("importer_dock.gd")
LAYOUT = GD["unreal_layout"]


def T(x=0.0, y=0.0, z=0.0, quat=(0.0, 0.0, 0.0, 1.0), scale=(1.0, 1.0, 1.0)):
    return {"translation": [x, y, z], "rotation_quat": list(quat), "scale": list(scale)}


def write(layout, **options):
    """The .tscn text for a layout, with a models/ folder holding options['models']."""
    with tempfile.TemporaryDirectory() as root:
        os.makedirs(os.path.join(root, "models"))
        for model in options.pop("models", ()):
            open(os.path.join(root, "models", model + ".gltf"), "w").close()
        opts = {"godot_project_dir": root, "lights": True, "foliage": True, "navigation": True,
                "metadata": True, "landscape": True}
        opts.update(options)
        out = os.path.join(root, "out.tscn")
        assert W.write_tscn(layout, out, {}, opts), "write_tscn failed for %r" % (layout,)
        with open(out, encoding="utf-8") as f:
            return f.read()


def actor(**kw):
    out = {"name": "A", "actor_id": "ID_A", "class": "StaticMeshActor", "godot_transform": T(),
           "components": [{"name": "C", "mesh_key": "SM", "mesh_name": "SM",
                           "godot_world_transform": T()}]}
    out.update(kw)
    return out


def section(text, header):
    """The body of the first [section] whose header contains `header`."""
    m = re.search(r"^\[[^\n]*%s[^\n]*\]\n(.*?)(?=^\[|\Z)" % re.escape(header), text, re.S | re.M)
    return m.group(0) if m else ""


def floats(body, prop):
    m = re.search(r"%s = \w+\(([^)]*)\)" % re.escape(prop), body)
    return [float(v) for v in m.group(1).split(",")] if m else []


# =============================================================================
print("\n=== 1. AudioStreamWAV is reached dynamically ===")
check("no static has_method() on the native class", "AudioStreamWAV.has_method(" not in GD["environment"])
check("load_from_file is guarded by ClassDB and called through a Variant",
      'ClassDB.class_has_method(&"AudioStreamWAV", &"load_from_file")' in GD["environment"]
      and "var wav_cls: Variant = AudioStreamWAV" in GD["environment"]
      and "wav_cls.load_from_file(stream_path)" in GD["environment"])

# =============================================================================
print("\n=== 2. Navigation bakes sibling geometry ===")
group = re.search(r'const NAV_SOURCE_GROUP := &"([^"]+)"', GD["common"]).group(1)
check("addon and writer agree on the group", group == W.NAV_SOURCE_GROUP, (group, W.NAV_SOURCE_GROUP))
check("addon bakes from the group",
      "SOURCE_GEOMETRY_GROUPS_WITH_CHILDREN" in GD["gameplay"]
      and "geometry_source_group_name = Common.NAV_SOURCE_GROUP" in GD["gameplay"])
for name in ("unreal_layout", "terrain", "foliage"):
    check("import_%s.gd tags its geometry persistently" % name,
          "add_to_group(Common.NAV_SOURCE_GROUP, true)" in GD[name])

NAV = {"bounds_volumes": [{"name": "Nav", "godot_transform": T(), "extent_m": [-20, 5, -20]}]}
text = write({"actors": [actor()], "navigation": NAV,
              "foliage": [{"name": "F", "mesh_key": "SM", "godot_transforms": []}],
              "landscapes": [{"name": "LS"}]}, models=["SM"])
nav = section(text, 'type="NavigationMesh"')
check(".tscn bakes from the group",
      "geometry_source_geometry_mode = 1" in nav
      and 'geometry_source_group_name = &"%s"' % group in nav, nav)
for container in ("UnrealStaticMeshes", "UnrealFoliage", "LS"):
    check(".tscn %s is in the group" % container,
          'name="%s" type="Node3D" parent="." groups=["%s"]' % (container, group) in text)

# =============================================================================
print("\n=== 3. Mirrored collider rotation ===")
h = math.sqrt(0.5)
RZ45 = [[h, -h, 0.0], [h, h, 0.0], [0.0, 0.0, 1.0]]
rows, origin = W._scaled_shape_mat((RZ45, [1.0, 2.0, 3.0]), [-1.0, 1.0, 1.0])
check("+45 deg becomes -45 deg under an X mirror",
      all(abs(rows[i][j] - RZ45[j][i]) < 1e-9 for i in range(3) for j in range(3)), rows)
check("the offset is mirrored", origin == [-1.0, 2.0, 3.0], origin)
for scale in ([-2.0, 1.0, 0.5], [1.0, -1.0, 1.0], [-1.0, -1.0, 1.0], [-1.0, -1.0, -1.0]):
    det = W._rows_determinant(W._scaled_shape_mat((RZ45, [0.0] * 3), scale)[0])
    check("mirror %s stays a right-handed rotation" % scale, abs(det - 1.0) < 1e-9, det)
check("an unmirrored scale keeps the rotation",
      W._scaled_shape_mat((RZ45, [0.0] * 3), [2.0, 1.0, 0.5])[0] == RZ45)

quat45 = (0.0, 0.0, math.sin(math.pi / 8), math.cos(math.pi / 8))
hull = {"convex_hulls": [{"vertices": [[10, 20, 30]], "godot_local_transform": T(1, 0, 0, quat45)}]}
text = write({"meshes": {"SM": {"collision": hull}},
              "actors": [actor(components=[{"name": "C", "mesh_key": "SM", "godot_world_transform":
                                            T(scale=(-1.0, 1.0, 1.0))}])]}, models=["SM"])
pts = floats(section(text, 'type="ConvexPolygonShape3D"'), "points")
xf = floats(section(text, 'name="ConvexCollision"'), "transform")
got = [sum(xf[3 * i + k] * pts[k] for k in range(3)) + xf[9 + i] for i in range(3)]
body_rows = W._apply_mesh_axis_fix(W._transform_dict_to_mat(T(scale=(-1.0, 1.0, 1.0))))
scale = W._physics_body_scale(body_rows)
p = (0.1, 0.3, 0.2)  # glTF (X, Z, Y) of the vertex, in metres
want = [scale[i] * (sum(RZ45[i][k] * p[k] for k in range(3)) + (1.0 if i == 0 else 0.0)) for i in range(3)]
check("a rotated hull on a mirrored actor stays exact",
      all(abs(a - b) < 1e-9 for a, b in zip(got, want)), (got, want))
check("addon hulls keep their own basis too",
      "Transform3D(local.basis, local.origin * scale)" in func_body(LAYOUT, "_populate_collision_shapes"))

# =============================================================================
print("\n=== 4 + 12. Incremental update ===")
upd = func_body(LAYOUT, "_update_existing_body")
check("only imported meshes are rescaled", "_is_imported_mesh(child, comp_data)" in upd
      and 'has_meta("unreal_lod_index")' in func_body(LAYOUT, "_is_imported_mesh"))
check("scales are compared with basis_local_scale",
      "Common.basis_local_scale(" in upd and "get_scale().is_equal_approx" not in LAYOUT)
upd = func_body(LAYOUT, "_update_existing_actor_node")
check("component lookup uses the sanitized name", "NodePath(comp_name.validate_node_name())" in upd)
check("prefab instances are not edited inside", 'elif n3d.scene_file_path != "":' in upd)

# =============================================================================
print("\n=== 5 + 10. Dock ===")
reown = func_body(DOCK, "_reown_subtree")
check("redo does not enter Terrain3D", 'is_class("Terrain3D")' in reown)
added = func_body(DOCK, "_reown_instance_additions")
check("redo restores what was added inside models",
      "_reown_instance_additions(node, scene_root)" in reown and "unreal_lod_index" in added
      and "UnrealPhysicalBoneSimulator" in added and "child.owner == null" in added)
enter = func_body(DOCK, "_enter_tree")
check("the UI is built once",
      0 < enter.find("get_child_count() > 0") < enter.find("VBoxContainer.new()"))

# =============================================================================
print("\n=== 6. Shared prefabs ===")
per = func_body(LAYOUT, "_has_per_instance_geometry")
check("per-placement data keeps an actor out of the prefab",
      "and not _has_per_instance_geometry(components)" in LAYOUT
      and all('"%s"' % k in per for k in ("spline_mesh", "vertex_paint", "material_overrides")))
for src, name in ((LAYOUT, "_get_or_create_blueprint_prefab"), (LAYOUT, "_populate_collision_shapes"),
                  (GD["skeletal_physics"], "_build_simulator")):
    code = re.sub(r"#.*", "", func_body(src, name))
    check("%s frees orphans at once" % name, ".free()" in code and "queue_free()" not in code)

# =============================================================================
print("\n=== 7. Spline roll ===")
check("roll sign is flipped", "right * cr - up * sr" in GD["spline_mesh"]
      and "right * sr + up * cr" in GD["spline_mesh"])
check("the z frame, whose right axis is reversed, is compensated",
      'sin(-roll if fwd_axis == "z" else roll)' in GD["spline_mesh"])


def cross(a, b):
    return [a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0]]


# Unreal: X = up ^ dir, Y = dir ^ X, XVec = X cos - Y sin. The addon's frame must be
# that, put through the (x, z, y) axis swap.
def swap(v):
    return [v[0], v[2], v[1]]


roll, c, s = 0.5, math.cos(0.5), math.sin(0.5)
ue_dir, ue_up = [1.0, 0.0, 0.0], [0.0, 0.0, 1.0]
ue_x = cross(ue_up, ue_dir)
ue_y = cross(ue_dir, ue_x)
ue_right = swap([ue_x[i] * c - ue_y[i] * s for i in range(3)])
ue_upv = swap([ue_y[i] * c + ue_x[i] * s for i in range(3)])
fwd, ref = swap(ue_dir), swap(ue_up)
right = cross(fwd, ref)
up = cross(right, fwd)
check("the rolled frame matches Unreal's",
      all(abs(right[i] * c - up[i] * s - ue_right[i]) < 1e-9
          and abs(right[i] * s + up[i] * c - ue_upv[i]) < 1e-9 for i in range(3)))

# =============================================================================
print("\n=== 8. Duplicate material names ===")
find = func_body(GD["common"], "find_material_entry")
check("a shared matcher prefers the surface's own unclaimed slot",
      "rank = 4 if slot_matches else 3" in find and "claimed[best] = true" in find)
check("layout and foliage both use it",
      "Common.find_material_entry(" in LAYOUT and "Common.find_material_entry(" in GD["foliage"])

# =============================================================================
print("\n=== 9. Compressed textures ===")
check("images are decompressed before pixel access",
      "img.is_compressed() and img.decompress() != OK" in func_body(GD["common"], "_decompressed"))
check(".dds goes through the buffer decoder",
      'actual == claimed and actual != "dds"' in GD["common"])

# =============================================================================
print("\n=== 11. Merging feature containers ===")
merge = func_body(LAYOUT, "_merge_feature_containers")
check("a queued container is freed before its name is reused",
      re.search(r"is_queued_for_deletion\(\):\s*(#[^\n]*\s*)?fresh\.free\(\)\s*fresh = null", merge) is not None)

# =============================================================================
print("\n=== 14. LOD0 only yields to LODs that loaded ===")
lod = func_body(LAYOUT, "_apply_lod_chain")
check("LODs are loaded before LOD0 gets a range",
      0 < lod.find("instance_gltf(") < lod.find("_set_visibility_range_recursive(lod0_node"))

# =============================================================================
print("\n=== 15. Skeletal physics in saved scenes ===")
attach = func_body(GD["skeletal_physics"], "attach_in_scene")
check("instances are made editable before bones are added",
      0 < attach.find("_make_instances_editable(") < attach.find("_build_simulator(")
      and "set_editable_instance(node, true)" in GD["skeletal_physics"])

# =============================================================================
print("\n=== 13. tscn_writer parity and validity ===")
check("navmesh parses both geometry kinds", "geometry_parsed_geometry_type = 2" in nav)
for line in ("agent_radius = 0.35", "agent_height = 1.92", "agent_max_climb = 0.35", "agent_max_slope = 44.0"):
    check("navmesh default %s" % line, line in nav, nav)
check("extents are made positive", floats(nav, "filter_baking_aabb") == [-20, -5, -20, 40, 10, 40], nav)
nav = section(write({"navigation": {
    "agent_radius_m": 0, "agent_height_m": 0, "agent_max_step_height_m": -1, "max_slope_deg": 120,
    "cell_size_m": 9, "bounds_volumes": [{"extent_m": [0, 0, 0]}]}}), 'type="NavigationMesh"')
for line in ("agent_radius = 0.05", "agent_height = 0.1", "agent_max_climb = 0.0",
             "agent_max_slope = 89.9", "cell_size = 1.0"):
    check("navmesh clamp %s" % line, line in nav, nav)
check("a degenerate extent sets no baking box", "filter_baking_aabb" not in nav, nav)
for bad in (None, "x", [], [3]):
    write({"navigation": {"bounds_volumes": [{"extent_m": bad}]}})

collision = {
    "boxes": [{"size": [-100, 200, -300]}, {"size": [1]}, {}, None],
    "spheres": [{"radius": -50}, {"radius": 0}, {}, "x"],
    "capsules": [{"radius": -50, "length": -100}, {"radius": 0, "length": 5}, None],
    "convex_hulls": [
        {"vertices": []}, {"vertices": "bad"}, {"vertices": [[1], None]}, {}, None,
        {"vertices": [[100, 100, 100], [1, 2], None], "godot_local_transform": T(scale=(0.0, 1.0, 1.0))}],
}
text = write({"meshes": {"SM": {"collision": collision}},
              "actors": [actor(components=[{"name": "C", "mesh_key": "SM", "godot_world_transform":
                                            T(scale=(2.0, 1.0, 1.0))}])]}, models=["SM"])
for kind in ("Box", "Sphere", "Capsule"):
    check("only the valid %s survives" % kind, text.count('[sub_resource type="%sShape3D"' % kind) == 1)
check("only the valid hull survives", text.count('type="ConvexPolygonShape3D"') == 1)
check("box size is positive", all(v > 0 for v in floats(section(text, 'type="BoxShape3D"'), "size")))
capsule = section(text, 'type="CapsuleShape3D"')
check("capsule and sphere dimensions are positive",
      "-" not in capsule.split("]", 1)[1] and "-" not in section(text, 'type="SphereShape3D"').split("]", 1)[1], capsule)
check("a singular hull basis falls back to the plain scale",
      floats(section(text, 'type="ConvexPolygonShape3D"'), "points")
      == [v * f for v, f in zip([1.0, 1.0, 1.0], W._physics_body_scale(
          W._apply_mesh_axis_fix(W._transform_dict_to_mat(T(scale=(2.0, 1.0, 1.0))))))])

for gradient in (True, "x", [1]):
    check("gradient %r is ignored" % (gradient,),
          "ProceduralSkyMaterial" in write({"sky_light": {"gradient": gradient}}))
sky = section(write({"sky_light": {"gradient": {"top": [1, 2, 3], "ground": 5}, "color": [0.5], "intensity": 2.0}}),
              'type="ShaderMaterial"')
check("missing gradient values get defaults",
      "shader_parameter/falloff = 6.67" in sky
      and floats(sky, "shader_parameter/horizon") == [0, 0, 0], sky)
check("a short sky color is padded with white",
      floats(sky, "shader_parameter/light_scale") == [1.0, 2.0, 2.0], sky)


def light_color(**kw):
    entry = {"name": "L", "type": "point", "color": [1, 1, 1], "use_temperature": True}
    entry.update(kw)
    return floats(section(write({"lights": [entry]}), 'name="L"'), "light_color")[:3]


white = light_color(use_temperature=False)
for k in (0, -5):
    check("temperature %s does not tint" % k, light_color(temperature_kelvin=k) == white)
check("a missing temperature is 6500 K, as in the addon",
      light_color() == light_color(temperature_kelvin=6500) != white)
check("a real temperature still tints", light_color(temperature_kelvin=2000)[2] < 0.5)

comp = {"name": "C", "mesh_key": "SM_1a2b", "mesh_name": "SM", "godot_world_transform": T()}
check("a single-mesh actor falls back to mesh_name",
      "res://models/SM.gltf" in write({"actors": [actor(components=[comp])]}, models=["SM"]))
check("a multi-mesh actor falls back to mesh_name",
      "MISSING_" not in write({"actors": [actor(components=[comp, dict(comp, name="D")])]}, models=["SM"]))
check("foliage falls back to mesh_name", 'source_model = "res://models/SM.gltf"' in write(
    {"foliage": [{"name": "F", "mesh_key": "SM_1a2b", "mesh_name": "SM", "godot_transforms": []}]}, models=["SM"]))
check("the key still wins over the name",
      "res://models/SM_1a2b.gltf" in write({"actors": [actor(components=[comp])]}, models=["SM", "SM_1a2b"]))

RESERVED = ("unreal_actor_id", "missing_mesh", "unreal_mesh_key", "unreal_physics_file",
            "unreal_lod_files", "unreal_spline_mesh")
props = {k: "from_blueprint" for k in RESERVED}
props["Health"] = 5
meshes = {"SM": {"physics_file": "SM_physics.json",
                 "lods": [{"lod_index": 0}, {"lod_index": 1, "export_name": "SM_LOD1"}]}}
spline_comp = {"name": "C", "mesh_key": "SM", "godot_world_transform": T(), "spline_mesh": {"a": 1}}
for models in (["SM", "SM_LOD1"], []):
    text = write({"meshes": meshes, "actors": [actor(properties=props, components=[spline_comp])]}, models=models)
    for block in text.split("\n\n"):
        keys = re.findall(r"^(metadata/\w+) =", block, re.M)
        check("no metadata key is emitted twice (%d models)" % len(models), len(keys) == len(set(keys)), keys)
    check("reserved keys are not taken from Blueprint variables", "from_blueprint" not in text)
    check("ordinary Blueprint variables still are", "metadata/Health = 5" in text)

for components in ([comp], [comp, dict(comp, name="D")]):
    for collides in (None, {"boxes": [{"size": [1, 1, 1]}]}):
        for models in (["SM_1a2b"], []):
            text = write({"meshes": {"SM_1a2b": {"collision": collides}},
                          "actors": [actor(components=components, tags=["t"], properties={"P": 1})]},
                         models=models, metadata=False)
            check("actor id survives metadata=False (%d comps, collision=%s, %d models)"
                  % (len(components), bool(collides), len(models)),
                  text.count('metadata/unreal_actor_id = "ID_A"') == 1
                  and "unreal_class" not in text and "unreal_tags" not in text and "metadata/P" not in text, text)

# =============================================================================
print("\n" + "=" * 60)
if FAIL:
    print("FAILED (%d): %s" % (len(FAIL), ", ".join(FAIL)))
    sys.exit(1)
print("ALL SWEEP 2 GODOT / TSCN CHECKS PASSED")
