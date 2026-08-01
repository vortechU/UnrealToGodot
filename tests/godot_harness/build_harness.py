"""Builds a throwaway Godot project that exercises import_unreal_layout.gd for real.

Creates: project.godot, a copy of the addon, a minimal valid glTF triangle per
mesh, a level_layout.json shaped exactly like the exporter's output, and a
SceneTree test script that runs do_import() and asserts node placement.
"""
import base64
import json
import os
import shutil
import struct
import zlib

HARNESS = os.path.join(os.path.dirname(os.path.abspath(__file__)), "_project")
REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

if os.path.exists(HARNESS):
    shutil.rmtree(HARNESS)
os.makedirs(os.path.join(HARNESS, "models"))
os.makedirs(os.path.join(HARNESS, "textures"))
os.makedirs(os.path.join(HARNESS, "terrain"))


def _exr_writer():
    """The production float-EXR writer from export_landscape, imported with a
    stub `unreal` so the harness feeds Godot the exact bytes the exporter emits
    rather than a lookalike."""
    import sys
    import types
    if "unreal" not in sys.modules:
        stub = types.ModuleType("unreal")
        stub.log = stub.log_warning = stub.log_error = lambda *a: None
        for attr in ("Vector", "Name", "Transform", "Box2D", "LandscapeProxy", "Landscape",
                     "LandscapeStreamingProxy", "LandscapeComponent",
                     "LandscapeHeightfieldCollisionComponent", "UnrealEditorSubsystem"):
            setattr(stub, attr, type(attr, (), {}))
        stub.TextureRenderTargetFormat = types.SimpleNamespace()
        stub.get_editor_subsystem = lambda cls: None
        sys.modules["unreal"] = stub
    sys.path.insert(0, os.path.join(REPO, "UnrealToGodot", "Content", "Python"))
    import export_landscape
    return export_landscape._write_exr_r32


write_exr_r32 = _exr_writer()

# --- landscape fixture -------------------------------------------------------
# A 33x33 heightmap holding absolute Unreal centimetres, exactly as the CPU
# collision-tracing fallback writes it. Values ramp along image U (Unreal +X)
# so the importer's axis mapping is checkable from the resulting geometry:
# image U -> Godot -Z, so height must RISE towards -Z.
LS_N = 33
LS_MIN_CM, LS_MAX_CM = -500.0, 1500.0
_heights = []
for _py in range(LS_N):
    for _px in range(LS_N):
        _heights.append(LS_MIN_CM + (LS_MAX_CM - LS_MIN_CM) * (_px / float(LS_N - 1)))
write_exr_r32(os.path.join(HARNESS, "terrain", "LS_height.exr"), LS_N, LS_N, _heights)

# Two paint layers: "Grass" covers the left half, "Rock" the right.
write_exr_r32(os.path.join(HARNESS, "terrain", "LS_weight_Grass.exr"), LS_N, LS_N,
              [1.0 if (i % LS_N) < LS_N // 2 else 0.0 for i in range(LS_N * LS_N)])
write_exr_r32(os.path.join(HARNESS, "terrain", "LS_weight_Rock.exr"), LS_N, LS_N,
              [0.0 if (i % LS_N) < LS_N // 2 else 1.0 for i in range(LS_N * LS_N)])

# 3200 x 3200 cm footprint centred on the Unreal origin => 32 x 32 m in Godot.
LANDSCAPE_FIXTURE = {
    "name": "LS",
    "godot_transform": {"translation": [0.0, 0.0, 0.0],
                        "rotation_quat": [0.0, 0.0, 0.0, 1.0], "scale": [1.0, 1.0, 1.0]},
    "heightmap_file": "terrain/LS_height.exr",
    "heightmap_resolution": [LS_N, LS_N],
    "world_size_m": [32.0, 32.0],
    "world_center_m": [0.0, 5.0, 0.0],
    "height_range_m": [LS_MIN_CM * 0.01, LS_MAX_CM * 0.01],
    "height_encoding": "normalized",
    "vertex_spacing_m": 1.0,
    "ue_bounds": {"center": [0.0, 0.0, 500.0], "extent": [1600.0, 1600.0, 1000.0]},
    "layers": [
        {"name": "Grass", "weightmap_file": "terrain/LS_weight_Grass.exr",
         "debug_color": [0.2, 0.8, 0.3]},
        {"name": "Rock", "weightmap_file": "terrain/LS_weight_Rock.exr",
         "debug_color": [0.6, 0.6, 0.6]},
    ],
}


def write_png(path, rgb):
    """Writes a 2x2 solid-colour PNG. Hand-rolled so tests need no image library."""
    width = height = 2
    raw = b"".join(b"\x00" + bytes(rgb) * width for _ in range(height))

    def chunk(tag, data):
        body = tag + data
        return (struct.pack(">I", len(data)) + body
                + struct.pack(">I", zlib.crc32(body) & 0xFFFFFFFF))

    png = (b"\x89PNG\r\n\x1a\n"
           + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
           + chunk(b"IDAT", zlib.compress(raw))
           + chunk(b"IEND", b""))
    with open(path, "wb") as f:
        f.write(png)


# Source textures live in a SIBLING folder, exactly as the exporter lays them out.
write_png(os.path.join(HARNESS, "textures", "TX_Test_ALB.png"), (200, 40, 40))
write_png(os.path.join(HARNESS, "textures", "TX_Test_NRM.png"), (128, 128, 255))
write_png(os.path.join(HARNESS, "textures", "TX_Test_RMA.png"), (60, 200, 90))

# --- minimal valid glTF: one triangle with UVs, buffer as a data URI ----------
positions = [(0.0, 0.0, 0.0), (1.0, 0.0, 0.0), (0.0, 1.0, 0.0)]
uvs = [(0.0, 0.0), (1.0, 0.0), (0.0, 1.0)]
pos_bytes = b"".join(struct.pack("<3f", *p) for p in positions)
uv_bytes = b"".join(struct.pack("<2f", *t) for t in uvs)
idx_bytes = struct.pack("<3H", 0, 1, 2) + b"\x00\x00"  # padded to 4-byte alignment
blob = pos_bytes + uv_bytes + idx_bytes


def make_gltf(with_texture):
    doc = {
        "asset": {"version": "2.0", "generator": "harness"},
        "scene": 0,
        "scenes": [{"nodes": [0]}],
        "nodes": [{"mesh": 0, "name": "TriNode"}],
        "meshes": [{"name": "Tri", "primitives": [
            {"attributes": {"POSITION": 0, "TEXCOORD_0": 1}, "indices": 2}]}],
        "buffers": [{
            "byteLength": len(blob),
            "uri": "data:application/octet-stream;base64," + base64.b64encode(blob).decode("ascii"),
        }],
        "bufferViews": [
            {"buffer": 0, "byteOffset": 0, "byteLength": len(pos_bytes), "target": 34962},
            {"buffer": 0, "byteOffset": len(pos_bytes), "byteLength": len(uv_bytes), "target": 34962},
            {"buffer": 0, "byteOffset": len(pos_bytes) + len(uv_bytes), "byteLength": 6,
             "target": 34963},
        ],
        "accessors": [
            {"bufferView": 0, "componentType": 5126, "count": 3, "type": "VEC3",
             "min": [0.0, 0.0, 0.0], "max": [1.0, 1.0, 0.0]},
            {"bufferView": 1, "componentType": 5126, "count": 3, "type": "VEC2",
             "min": [0.0, 0.0], "max": [1.0, 1.0]},
            {"bufferView": 2, "componentType": 5123, "count": 3, "type": "SCALAR"},
        ],
    }
    if with_texture:
        # The whole point: a SIBLING textures/ folder reached by a relative uri.
        # This is what inject_texture_references() writes.
        doc["images"] = [{"uri": "../textures/TX_Test_ALB.png", "mimeType": "image/png"}]
        doc["textures"] = [{"source": 0}]
        doc["materials"] = [{"name": "MI_Test",
                             "pbrMetallicRoughness": {"baseColorTexture": {"index": 0}}}]
        doc["meshes"][0]["primitives"][0]["material"] = 0
    return doc


for name in ["SM_Rock", "SM_Crate"]:
    with open(os.path.join(HARNESS, "models", name + ".gltf"), "w", encoding="utf-8") as f:
        json.dump(make_gltf(with_texture=False), f)

# SM_Textured proves Godot resolves "../textures/..." out of a sibling folder.
with open(os.path.join(HARNESS, "models", "SM_Textured.gltf"), "w", encoding="utf-8") as f:
    json.dump(make_gltf(with_texture=True), f)

# --- project + addon ---------------------------------------------------------
with open(os.path.join(HARNESS, "project.godot"), "w", encoding="utf-8") as f:
    f.write('config_version=5\n\n[application]\n\nconfig/name="ImporterHarness"\n'
            'config/features=PackedStringArray("4.6")\n')

shutil.copytree(os.path.join(REPO, "addons"), os.path.join(HARNESS, "addons"))

# --- layout fixture: exactly the shape export_level_to_json.py emits ----------
def t(x, y, z, s=1.0):
    return {"translation": [x, y, z], "rotation_quat": [0.0, 0.0, 0.0, 1.0], "scale": [s, s, s]}


SQRT_HALF = 0.7071067811865476


def ry90(sx, sy, sz):
    """Origin-centred transform rotated +90 deg about Godot's Y, non-uniformly scaled."""
    return {"translation": [0.0, 0.0, 0.0],
            "rotation_quat": [0.0, SQRT_HALF, 0.0, SQRT_HALF],
            "scale": [sx, sy, sz]}

layout = {
    "format_version": 2,
    "level_name": "HarnessLevel",
    "meshes": {
        # Rock has collision -> exercises the StaticBody3D path.
        "SM_Rock": {"path": "/Game/SM_Rock", "export_name": "SM_Rock", "materials": [],
                    "collision": {"boxes": [{"size": [100.0, 100.0, 100.0],
                                             "godot_local_transform": t(0, 0, 0)}],
                                  "spheres": [], "capsules": [], "convex_hulls": []}},
        # Crate has no collision -> exercises the plain-instance path.
        "SM_Crate": {"path": "/Game/SM_Crate", "export_name": "SM_Crate",
                     "collision": None, "materials": []},
        # Carries a packed RMA map, as the Old_Buoys pack does. Channel indices
        # match Godot's BaseMaterial3D.TextureChannel (0=RED, 1=GREEN, 2=BLUE).
        "SM_Textured": {
            "path": "/Game/SM_Textured", "export_name": "SM_Textured", "collision": None,
            "materials": [{
                "slot_index": 0,
                "material_name": "MI_Test",
                "material_path": "/Game/MI_Test",
                "parameters": {
                    "albedo_color": [2.0, 2.0, 2.0, 1.0],
                    "roughness": 1.0,
                    "metallic": 1.0,
                    "albedo_texture": "TX_Test_ALB",
                    "normal_texture": "TX_Test_NRM",
                    "roughness_texture": None,
                    "metallic_texture": None,
                    "packed_texture": "TX_Test_RMA",
                    "packed_channels": {"roughness": 0, "metallic": 1, "ao": 2},
                    "tiling": [1.0, 1.0],
                },
            }],
        },
    },
    "actors": [
        {
            # Plain StaticMeshActor. UE reports the root component's relative
            # transform as the WORLD transform -- identical to the actor's.
            # The old importer composed them into (40, 6, -100).
            "name": "Buoy_1", "class": "StaticMeshActor",
            "godot_transform": t(20, 3, -50),
            "components": [{"name": "SM0", "mesh_key": "SM_Rock", "mesh_name": "SM_Rock",
                            "godot_relative_transform": t(20, 3, -50),
                            "godot_world_transform": t(20, 3, -50),
                            "unreal_relative_transform": t(20, 3, -50),
                            "unreal_world_transform": t(20, 3, -50),
                            "material_overrides": []}],
        },
        {
            # Same, but no collision -> visual-only branch.
            "name": "Crate_1", "class": "StaticMeshActor",
            "godot_transform": t(7, 1, 2),
            "components": [{"name": "SM0", "mesh_key": "SM_Crate", "mesh_name": "SM_Crate",
                            "godot_relative_transform": t(7, 1, 2),
                            "godot_world_transform": t(7, 1, 2),
                            "material_overrides": []}],
        },
        {
            # Blueprint with a component nested TWO levels deep: its relative
            # transform (1,0,0) is measured against an intermediate component,
            # so only godot_world_transform reaches the true (16, 0, 0).
            "name": "BP_Pier", "class": "BP_Pier_C",
            "godot_transform": t(10, 0, 0),
            "components": [
                {"name": "Deck", "mesh_key": "SM_Crate", "mesh_name": "SM_Crate",
                 "godot_relative_transform": t(5, 0, 0),
                 "godot_world_transform": t(15, 0, 0), "material_overrides": []},
                {"name": "Plank", "mesh_key": "SM_Crate", "mesh_name": "SM_Crate",
                 "godot_relative_transform": t(1, 0, 0),
                 "godot_world_transform": t(16, 0, 0), "material_overrides": []},
            ],
        },
        {
            # Rotated (UE yaw +90) AND non-uniformly scaled. Guards that the
            # schema's `scale` is treated as a LOCAL scale: the basis COLUMNS get
            # scaled (Basis.scaled_local), not the rows (Basis.scaled), which
            # would apply the scale in the parent frame and shear the actor.
            # Godot quat for the converted yaw is Ry(+90) = (0, sin45, 0, cos45).
            "name": "Skewed_1", "class": "StaticMeshActor",
            "godot_transform": ry90(2.0, 3.0, 5.0),
            "components": [{"name": "SM0", "mesh_key": "SM_Crate", "mesh_name": "SM_Crate",
                            "godot_relative_transform": ry90(2.0, 3.0, 5.0),
                            "godot_world_transform": ry90(2.0, 3.0, 5.0),
                            "material_overrides": []}],
        },
        {
            # Drives the material path: packed RMA map + sibling-folder textures.
            "name": "Textured_1", "class": "StaticMeshActor",
            "godot_transform": t(0, 5, 0),
            "components": [{"name": "SM0", "mesh_key": "SM_Textured", "mesh_name": "SM_Textured",
                            "godot_relative_transform": t(0, 5, 0),
                            "godot_world_transform": t(0, 5, 0),
                            "material_overrides": []}],
        },
    ],
    # One decal, written exactly as the exporter emits it for a UE DecalActor at
    # the origin with yaw +90 and scale (X=5, Y=2, Z=3), keeping UE's default
    # decal_size half-extents (128, 256, 256) cm = (depth, width, height).
    #
    #   size_m          = [Y*2, X*2, Z*2] * 0.01           = [5.12, 2.56, 5.12]
    #   rotation_quat   = R_std . Rx(-90), R_std = Ry(-90) => (-.5, -.5, -.5, .5)
    #   scale           = [usy, usx, usz]                  = [2, 5, 3]
    #                     (Y/Z swapped vs the standard [usy, usz, usx] because the
    #                      Rx(-90) fix-up re-labels the node's local Y and Z)
    #
    # Rendered half-extents are 0.5 * size[j] * basis column j, so the Decal must
    # come out width (0,0,5.12), projection depth (6.4,0,0), height (0,7.68,0) --
    # which is the UE box (256*2, 128*5, 256*3 cm along its local Y/X/Z) taken
    # through the layout conversion. See test_math.py section 11.
    "decals": [
        {
            "name": "Decal_Skewed",
            "godot_transform": {"translation": [0.0, 0.0, 0.0],
                                "rotation_quat": [-0.5, -0.5, -0.5, 0.5],
                                "scale": [2.0, 5.0, 3.0]},
            "size_m": [5.12, 2.56, 5.12],
            "sort_order": 0,
            "visible": True,
            "modulate": [0.8, 0.2, 0.2, 0.5],
            "fade_screen_size": 0.05,
            "distance_fade_begin_m": 9.0,
            "distance_fade_length_m": 3.0,
            "material_name": "MI_Decal", "material_path": "/Game/MI_Decal",
            "textures": {"albedo": "TX_Test_ALB", "normal": None, "orm": None, "emission": None},
        },
        {
            # Hidden in Unreal, no screen-size fade: the importer must honour
            # both rather than defaulting the node visible and un-faded.
            "name": "Decal_Hidden",
            "godot_transform": {"translation": [10.0, 0.0, 0.0],
                                "rotation_quat": [0.0, 0.0, 0.0, 1.0],
                                "scale": [1.0, 1.0, 1.0]},
            "size_m": [1.0, 1.0, 1.0],
            "sort_order": 0,
            "visible": False,
            "modulate": [1.0, 1.0, 1.0, 1.0],
            "fade_screen_size": 0.0,
            "distance_fade_begin_m": None,
            "distance_fade_length_m": None,
            "material_name": "MI_Decal", "material_path": "/Game/MI_Decal",
            "textures": {"albedo": "TX_Test_ALB", "normal": None, "orm": None, "emission": None},
        },
    ],
    # One of each light type, carrying the properties the importer is supposed
    # to map. Godot itself is the only thing that can confirm these property
    # names exist and accept these values on the real Light3D classes.
    "lights": [
        {
            "name": "Sun_Test", "type": "directional",
            "godot_transform": {"translation": [0.0, 10.0, 0.0],
                                "rotation_quat": [0.0, 0.0, 0.0, 1.0],
                                "scale": [1.0, 1.0, 1.0]},
            "color": [1.0, 0.95, 0.9], "intensity": 10.0, "intensity_units": "lux",
            "intensity_candelas": None, "inverse_squared_falloff": True,
            "godot_energy": 1.0, "temperature_kelvin": None, "use_temperature": False,
            "cast_shadows": True, "attenuation_radius_m": None, "source_radius_m": None,
            "source_angle_deg": 0.5357, "shadow_distance_m": 400.0, "rect_size_m": None,
            "inner_cone_angle_deg": None, "outer_cone_angle_deg": None,
            "indirect_intensity": 1.0, "specular_scale": 1.0, "volumetric_scattering": 1.0,
            "distance_fade_begin_m": None, "distance_fade_length_m": None,
            "mobility": "movable", "visible": True,
        },
        {
            "name": "Spot_Test", "type": "spot",
            "godot_transform": {"translation": [3.0, 2.0, 0.0],
                                "rotation_quat": [0.0, 0.0, 0.0, 1.0],
                                "scale": [1.0, 1.0, 1.0]},
            "color": [1.0, 0.5, 0.2], "intensity": 5000.0, "intensity_units": "unitless",
            "intensity_candelas": 8.0, "inverse_squared_falloff": True,
            "godot_energy": 1.0, "temperature_kelvin": None, "use_temperature": False,
            "cast_shadows": True, "attenuation_radius_m": 12.0, "source_radius_m": 0.1,
            "source_angle_deg": None, "shadow_distance_m": None, "rect_size_m": None,
            "inner_cone_angle_deg": 20.0, "outer_cone_angle_deg": 40.0,
            "indirect_intensity": 0.25, "specular_scale": 0.5, "volumetric_scattering": 2.0,
            "distance_fade_begin_m": 40.0, "distance_fade_length_m": 10.0,
            "mobility": "static", "visible": True,
        },
        {
            # Rect lights have no Godot node; the panel size must still survive.
            # Also switched off in Unreal.
            "name": "Rect_Test", "type": "rect",
            "godot_transform": {"translation": [-3.0, 2.0, 0.0],
                                "rotation_quat": [0.0, 0.0, 0.0, 1.0],
                                "scale": [1.0, 1.0, 1.0]},
            "color": [1.0, 1.0, 1.0], "intensity": 5000.0, "intensity_units": "unitless",
            "intensity_candelas": 8.0, "inverse_squared_falloff": True,
            "godot_energy": 2.0, "temperature_kelvin": None, "use_temperature": False,
            "cast_shadows": True, "attenuation_radius_m": 8.0, "source_radius_m": 0.0,
            "source_angle_deg": None, "shadow_distance_m": None, "rect_size_m": [0.64, 1.28],
            "inner_cone_angle_deg": None, "outer_cone_angle_deg": None,
            "indirect_intensity": 1.0, "specular_scale": 1.0, "volumetric_scattering": 1.0,
            "distance_fade_begin_m": None, "distance_fade_length_m": None,
            "mobility": "stationary", "visible": False,
        },
    ],
    "post_process": [], "landscapes": [LANDSCAPE_FIXTURE],
    # Only the real engine can confirm MultiMeshInstance3D accepts these
    # GeometryInstance3D property names and enum values.
    "foliage": [
        {
            "name": "Foliage_Grass", "mesh_key": "SM_Rock", "mesh_name": "SM_Rock",
            "instance_count": 2, "source": "foliage",
            "visible": True, "cast_shadow": False,
            "cull_begin_m": 30.0, "cull_end_m": 50.0, "material_overrides": [],
            "godot_transforms": [1, 0, 0, 0, 1, 0, 0, 0, 1, 1, 0, 1,
                                 2, 0, 0, 0, 2, 0, 0, 0, 2, 4, 0, 4],
        },
        {
            # Hidden in Unreal, shadow-casting, never culled.
            "name": "Instances_Hidden", "mesh_key": "SM_Crate", "mesh_name": "SM_Crate",
            "instance_count": 1, "source": "ism",
            "visible": False, "cast_shadow": True,
            "cull_begin_m": None, "cull_end_m": None, "material_overrides": [],
            "godot_transforms": [1, 0, 0, 0, 1, 0, 0, 0, 1, 9, 0, 9],
        },
    ],
    "height_fog": None, "sky_light": None, "has_sky_atmosphere": False, "navigation": None,
}

with open(os.path.join(HARNESS, "level_layout.json"), "w", encoding="utf-8") as f:
    json.dump(layout, f, indent=2)

# --- the test script Godot will run ------------------------------------------
TEST_GD = '''extends SceneTree

const Importer = preload("res://addons/unreal_importer/import_unreal_layout.gd")

var failures: Array[String] = []

func check(name: String, cond: bool, detail: String = "") -> void:
	if cond:
		print("  PASS  ", name)
	else:
		print("  FAIL  ", name, "  ", detail)
		failures.append(name)

func near(a: Vector3, b: Vector3, eps: float = 0.001) -> bool:
	return a.distance_to(b) < eps

func find_node(root: Node, n: String) -> Node:
	if root.name == n:
		return root
	for c in root.get_children():
		var f := find_node(c, n)
		if f:
			return f
	return null

func _init() -> void:
	var root := Node3D.new()
	root.name = "Root"
	get_root().add_child(root)

	var importer = Importer.new()
	var ok = await importer.do_import("res://level_layout.json", "res://models/",
		"res://textures/", root, {"apply_metadata": false})
	check("do_import returned true", ok == true)

	print("\\n--- scene tree ---")
	_dump(root, 0)
	print("")

	# 1. StaticMeshActor WITH collision -> StaticBody3D at the true world transform.
	var buoy := find_node(root, "Buoy_1")
	check("Buoy_1 exists", buoy != null)
	if buoy and buoy is Node3D:
		var p: Vector3 = (buoy as Node3D).global_transform.origin
		check("Buoy_1 at (20, 3, -50) not doubled", near(p, Vector3(20, 3, -50)), str(p))
		check("Buoy_1 is NOT at the pre-fix (40, 6, -100)",
			not near(p, Vector3(40, 6, -100)), str(p))
		check("Buoy_1 is a StaticBody3D (collision present)", buoy is StaticBody3D,
			buoy.get_class())
		# The mesh must sit at identity under the body so it lines up with the shapes.
		var mesh_child: Node3D = null
		for c in buoy.get_children():
			if c is Node3D and not (c is CollisionShape3D):
				mesh_child = c
		check("Buoy_1 mesh child sits at identity under the body",
			mesh_child != null and near(mesh_child.transform.origin, Vector3.ZERO),
			str(mesh_child.transform.origin) if mesh_child else "no mesh child")

	# 2. StaticMeshActor WITHOUT collision -> plain instance branch.
	var crate := find_node(root, "Crate_1")
	check("Crate_1 exists", crate != null)
	if crate and crate is Node3D:
		var p2: Vector3 = (crate as Node3D).global_transform.origin
		check("Crate_1 at (7, 1, 2) not doubled", near(p2, Vector3(7, 1, 2)), str(p2))

	# 3. Blueprint: the deeply nested component must reach its true world spot.
	var plank := find_node(root, "Plank")
	check("BP_Pier/Plank exists", plank != null)
	if plank and plank is Node3D:
		var p3: Vector3 = (plank as Node3D).global_transform.origin
		check("Plank resolves to world (16, 0, 0)", near(p3, Vector3(16, 0, 0)), str(p3))
		check("Plank is NOT at the pre-fix (11, 0, 0)", not near(p3, Vector3(11, 0, 0)), str(p3))
	var deck := find_node(root, "Deck")
	if deck and deck is Node3D:
		var p4: Vector3 = (deck as Node3D).global_transform.origin
		check("Deck resolves to world (15, 0, 0)", near(p4, Vector3(15, 0, 0)), str(p4))

	print("\\n============================================")
	if failures.size() > 0:
		print("FAILURES (", failures.size(), "): ", failures)
		quit(1)
	else:
		print("ALL GODOT IMPORT CHECKS PASSED")
		quit(0)

func _dump(n: Node, depth: int) -> void:
	var pos := ""
	if n is Node3D:
		pos = " @ " + str((n as Node3D).global_transform.origin)
	print("  ".repeat(depth), n.name, " [", n.get_class(), "]", pos)
	for c in n.get_children():
		_dump(c, depth + 1)
'''

with open(os.path.join(HARNESS, "test_import.gd"), "w", encoding="utf-8") as f:
    f.write(TEST_GD)

# The environment/exposure checks are a standalone SceneTree script (they need no
# EditorScript and no imported assets), kept as a real file rather than a string
# so it stays editable. run_tests.py runs it as its own Godot invocation.
shutil.copy(os.path.join(os.path.dirname(os.path.abspath(__file__)), "env_test.gd"),
            os.path.join(HARNESS, "env_test.gd"))

print("harness built at:", HARNESS)
