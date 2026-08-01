"""Adds a temporary EditorPlugin to the harness project.

import_unreal_layout.gd extends EditorScript, which Godot refuses to instantiate
outside the editor -- so `--script` (non-editor main loop) cannot drive it. An
EditorPlugin's _enter_tree() DOES run under `--headless -e`, where EditorScript
is instantiable, so the test runs there and quits with an exit code.
"""
import os

HARNESS = os.path.join(os.path.dirname(os.path.abspath(__file__)), "_project")
PLUGIN_DIR = os.path.join(HARNESS, "addons", "test_runner")
os.makedirs(PLUGIN_DIR, exist_ok=True)

with open(os.path.join(PLUGIN_DIR, "plugin.cfg"), "w", encoding="utf-8") as f:
    f.write('[plugin]\n\nname="TestRunner"\ndescription="headless test"\n'
            'author="harness"\nversion="1.0"\nscript="test_runner.gd"\n')

RUNNER = '''@tool
extends EditorPlugin

const Importer = preload("res://addons/unreal_importer/import_unreal_layout.gd")

var failures: Array[String] = []
var results: Array[String] = []

func _enter_tree() -> void:
	call_deferred("_run_tests")

func check(name: String, cond: bool, detail: String = "") -> void:
	if cond:
		print("  PASS  ", name)
		results.append("PASS  " + name)
	else:
		print("  FAIL  ", name, "  ", detail)
		results.append("FAIL  " + name + "  " + detail)
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

func _run_tests() -> void:
	print("\\n=========== HEADLESS IMPORT TEST ===========")
	var root := Node3D.new()
	root.name = "Root"
	get_tree().get_root().add_child(root)

	var importer = Importer.new()
	var ok = await importer.do_import("res://level_layout.json", "res://models/",
		"res://textures/", root, {"apply_metadata": false})
	check("do_import returned true", ok == true)

	print("\\n--- resulting scene tree ---")
	_dump(root, 0)
	print("")

	# 1. StaticMeshActor WITH collision -> StaticBody3D at the true world transform.
	var buoy := find_node(root, "Buoy_1")
	check("Buoy_1 exists", buoy != null)
	if buoy and buoy is Node3D:
		var p: Vector3 = (buoy as Node3D).global_transform.origin
		check("Buoy_1 at (20, 3, -50)", near(p, Vector3(20, 3, -50)), str(p))
		check("Buoy_1 NOT at pre-fix doubled (40, 6, -100)",
			not near(p, Vector3(40, 6, -100)), str(p))
		check("Buoy_1 is a StaticBody3D", buoy is StaticBody3D, buoy.get_class())
		var mesh_child: Node3D = null
		for c in buoy.get_children():
			if c is Node3D and not (c is CollisionShape3D):
				mesh_child = c
		check("Buoy_1 mesh sits at identity under the body",
			mesh_child != null and near(mesh_child.transform.origin, Vector3.ZERO),
			str(mesh_child.transform.origin) if mesh_child else "no mesh child")

	# 2. StaticMeshActor WITHOUT collision -> plain instance branch.
	var crate := find_node(root, "Crate_1")
	check("Crate_1 exists", crate != null)
	if crate and crate is Node3D:
		var p2: Vector3 = (crate as Node3D).global_transform.origin
		check("Crate_1 at (7, 1, 2)", near(p2, Vector3(7, 1, 2)), str(p2))

	# 3. Blueprint component nested two levels deep.
	var plank := find_node(root, "Plank")
	check("BP_Pier/Plank exists", plank != null)
	if plank and plank is Node3D:
		var p3: Vector3 = (plank as Node3D).global_transform.origin
		check("Plank resolves to world (16, 0, 0)", near(p3, Vector3(16, 0, 0)), str(p3))
		check("Plank NOT at pre-fix (11, 0, 0)", not near(p3, Vector3(11, 0, 0)), str(p3))
	var deck := find_node(root, "Deck")
	if deck and deck is Node3D:
		var p4: Vector3 = (deck as Node3D).global_transform.origin
		check("Deck resolves to world (15, 0, 0)", near(p4, Vector3(15, 0, 0)), str(p4))

	# 4. Rotated + non-uniformly scaled actor. The schema's `scale` is a LOCAL
	#    scale, so the basis COLUMNS get scaled (Basis.scaled_local). Godot's
	#    Basis.scaled() scales rows instead -- that applies the scale in the
	#    parent frame and stretches rotated actors along the wrong world axes.
	#    Ry(+90) with scale (2,3,5), then the glTF placement fix Ry(+90), gives
	#    columns (-5,0,0)/(0,3,0)/(0,0,-2); row scaling gives (-2,0,0)/../(0,0,-5).
	var skewed := find_node(root, "Skewed_1")
	check("Skewed_1 exists", skewed != null)
	if skewed and skewed is Node3D:
		var b: Basis = (skewed as Node3D).global_transform.basis
		check("Skewed_1 basis columns carry the LOCAL scale",
			near(b.x, Vector3(-5, 0, 0)) and near(b.y, Vector3(0, 3, 0))
			and near(b.z, Vector3(0, 0, -2)),
			"x=%s y=%s z=%s" % [b.x, b.y, b.z])
		check("Skewed_1 NOT row-scaled (scale applied in the parent frame)",
			not near(b.x, Vector3(-2, 0, 0)), str(b.x))

	# 5. Decal box. Godot renders the half-extent along local axis j as
	#    0.5 * size[j] * basis column j, so the exported scale has to be in the
	#    SAME frame as the exported (fix-up-folded) rotation. See the fixture in
	#    build_harness.py for how these numbers come off the UE decal.
	var decal := find_node(root, "Decal_Skewed")
	check("Decal_Skewed exists", decal != null)
	if decal and decal is Decal:
		var d := decal as Decal
		var db: Basis = d.global_transform.basis
		check("Decal size survives the import", near(d.size, Vector3(5.12, 2.56, 5.12)), str(d.size))
		var dw: Vector3 = db.x * 0.5 * d.size.x
		var dd: Vector3 = db.y * 0.5 * d.size.y
		var dh: Vector3 = db.z * 0.5 * d.size.z
		check("Decal width half-extent == (0, 0, 5.12)", near(dw, Vector3(0, 0, 5.12)), str(dw))
		check("Decal projection depth half-extent == (6.4, 0, 0)", near(dd, Vector3(6.4, 0, 0)), str(dd))
		check("Decal height half-extent == (0, 7.68, 0)", near(dh, Vector3(0, 7.68, 0)), str(dh))
		# Projection runs along local -Y and must land where UE local -X did.
		check("Decal projects along the converted UE -X",
			near((-db.y).normalized(), Vector3(-1, 0, 0)), str((-db.y).normalized()))
		check("Decal depth NOT the pre-fix 2.56 m half-extent",
			not near(dd, Vector3(2.56, 0, 0)), str(dd))
		# Material tint / DecalColor / opacity all land in modulate, and UE's
		# screen-size fade arrives as a distance fade. These used to be dropped.
		check("Decal modulate carries the UE tint and opacity",
			d.modulate.is_equal_approx(Color(0.8, 0.2, 0.2, 0.5)), str(d.modulate))
		check("Decal distance fade comes from UE FadeScreenSize",
			d.distance_fade_enabled and absf(d.distance_fade_begin - 9.0) < 0.001
			and absf(d.distance_fade_length - 3.0) < 0.001,
			"%s begin=%s len=%s" % [d.distance_fade_enabled, d.distance_fade_begin,
				d.distance_fade_length])
		check("Decal albedo texture is bound", d.get_texture(Decal.TEXTURE_ALBEDO) != null)

	# A decal the level hid must not come back visible, and it must not pick up
	# a distance fade UE never asked for.
	var hidden_decal := find_node(root, "Decal_Hidden")
	check("Decal_Hidden exists", hidden_decal != null)
	if hidden_decal and hidden_decal is Decal:
		var hd := hidden_decal as Decal
		check("a decal hidden in Unreal imports invisible", not hd.visible)
		check("a decal with no UE fade gets no distance fade", not hd.distance_fade_enabled)

	_test_lights(root)
	_test_foliage(root)
	_test_materials(root)
	_test_terrain(root)

	print("\\n============================================")
	_write_results()
	if failures.size() > 0:
		print("FAILURES (", failures.size(), "): ", failures)
		get_tree().quit(1)
	else:
		print("ALL GODOT IMPORT CHECKS PASSED")
		get_tree().quit(0)


func _write_results() -> void:
	"""Writes results where run_tests.py can read them.

	Godot's GUI-subsystem executable on Windows has no stdout to inherit when the
	parent redirects to a file, so the runner cannot scrape the console. It reads
	this file instead, and treats a missing file as a failure -- otherwise a
	plugin that never ran would look identical to one that passed."""
	var f := FileAccess.open("res://test_result.txt", FileAccess.WRITE)
	if f == null:
		printerr("could not write test_result.txt")
		return
	for line in results:
		f.store_line(line)
	f.store_line("TOTAL %d" % results.size())
	f.store_line("VERDICT %s" % ("FAIL" if failures.size() > 0 else "PASS"))
	f.close()

func _test_terrain(root: Node) -> void:
	"""Only the real engine proves the exported float EXR decodes, that the
	generated splat shader COMPILES, and that the terrain lands where Unreal
	had it. The fixture heightmap ramps along image U (Unreal +X), which the
	schema maps to Godot -Z, so height must rise towards -Z."""
	var ls := find_node(root, "LS")
	check("landscape node LS exists", ls != null)
	if ls == null:
		return
	check("landscape carries its Unreal marker", bool(ls.get_meta("unreal_landscape", false)))
	check("weightmap layers survive as metadata",
		Array(ls.get_meta("weightmap_layers", PackedStringArray())) == ["Grass", "Rock"],
		str(ls.get_meta("weightmap_layers", null)))

	var mi := find_node(ls, "TerrainMesh") as MeshInstance3D
	check("TerrainMesh built", mi != null)
	if mi == null or mi.mesh == null:
		return
	var aabb: AABB = mi.mesh.get_aabb()
	# 3200 cm of Unreal extent each way -> a 32 x 32 m footprint centred on 0.
	check("terrain footprint is 32 x 32 m",
		absf(aabb.size.x - 32.0) < 0.01 and absf(aabb.size.z - 32.0) < 0.01, str(aabb.size))
	check("terrain is centred on the origin",
		absf(aabb.position.x + 16.0) < 0.01 and absf(aabb.position.z + 16.0) < 0.01, str(aabb.position))
	# height_range_m says -5 .. 15 m, and the heightmap spans the full range.
	check("terrain spans its exported height range (-5 .. 15 m)",
		absf(aabb.position.y + 5.0) < 0.01 and absf(aabb.size.y - 20.0) < 0.01,
		"pos.y=%f size.y=%f" % [aabb.position.y, aabb.size.y])

	# Axis check: the exporter's contract is image U (Unreal +X) -> Godot -Z.
	# The fixture ramps height up with U, so the -Z edge must be the HIGH one.
	var arrays: Array = (mi.mesh as ArrayMesh).surface_get_arrays(0)
	var verts: PackedVector3Array = arrays[Mesh.ARRAY_VERTEX]
	var high_z := 0.0
	var low_z := 0.0
	var high_y := -INF
	var low_y := INF
	for v in verts:
		if v.y > high_y:
			high_y = v.y
			high_z = v.z
		if v.y < low_y:
			low_y = v.y
			low_z = v.z
	check("image U maps to Godot -Z (highest ground sits at negative Z)",
		high_z < low_z, "high at z=%f, low at z=%f" % [high_z, low_z])

	# The splat material must be a real, COMPILED shader — a shader with a
	# syntax error still assigns fine and only fails at render time.
	var mat := mi.mesh.surface_get_material(0)
	check("terrain uses the splat ShaderMaterial", mat is ShaderMaterial,
		mat.get_class() if mat else "null")
	if mat is ShaderMaterial:
		var sm := mat as ShaderMaterial
		var uniforms: Array = (sm.shader as Shader).get_shader_uniform_list()
		check("splat shader compiles (has uniforms)", uniforms.size() > 0)
		var names: Array = []
		for un in uniforms:
			names.append(un["name"])
		for want in ["splatmap", "layer_count", "layer0_tint", "layer0_albedo", "layer_tiling"]:
			check("splat shader exposes %s" % want, names.has(want), str(names))
		check("layer_count == 2", int(sm.get_shader_parameter("layer_count")) == 2,
			str(sm.get_shader_parameter("layer_count")))
		check("splatmap texture assigned", sm.get_shader_parameter("splatmap") is Texture2D)
		var tint: Color = sm.get_shader_parameter("layer0_tint")
		check("layer0 tint is the exported Unreal debug colour",
			absf(tint.g - 0.8) < 0.01 and absf(tint.r - 0.2) < 0.01, str(tint))

	var col := find_node(ls, "TerrainCollision")
	check("terrain collision body built", col is StaticBody3D)
	if col:
		var shape: CollisionShape3D = null
		for c in col.get_children():
			if c is CollisionShape3D:
				shape = c
		check("terrain has a concave collision shape",
			shape != null and shape.shape is ConcavePolygonShape3D,
			str(shape.shape) if shape else "no shape")


func _test_foliage(root: Node) -> void:
	var grass_node := find_node(root, "Foliage_Grass")
	check("Foliage_Grass exists", grass_node != null)
	if grass_node and grass_node is MultiMeshInstance3D:
		var grass := grass_node as MultiMeshInstance3D
		check("foliage rebuilt as a MultiMesh with every instance",
			grass.multimesh != null and grass.multimesh.instance_count == 2,
			str(grass.multimesh.instance_count if grass.multimesh else -1))
		check("foliage MultiMesh got a mesh bound",
			grass.multimesh != null and grass.multimesh.mesh != null)
		# Shadowless grass is the common Unreal authoring; importing it ON is a
		# visual and performance regression.
		check("a shadowless foliage component imports shadowless",
			grass.cast_shadow == GeometryInstance3D.SHADOW_CASTING_SETTING_OFF,
			str(grass.cast_shadow))
		check("UE cull distance becomes the Godot visibility range",
			absf(grass.visibility_range_end - 50.0) < 0.001, str(grass.visibility_range_end))
		check("UE cull fade becomes the visibility range end margin",
			absf(grass.visibility_range_end_margin - 20.0) < 0.001,
			str(grass.visibility_range_end_margin))
		check("a faded cull range uses the self fade mode",
			grass.visibility_range_fade_mode == GeometryInstance3D.VISIBILITY_RANGE_FADE_SELF,
			str(grass.visibility_range_fade_mode))
		check("foliage source rides along as metadata",
			grass.get_meta("unreal_foliage_source", "") == "foliage",
			str(grass.get_meta("unreal_foliage_source", "")))
		# NB: the per-instance transforms CANNOT be checked here. Under --headless
		# Godot uses the dummy renderer, which stores no MultiMesh instance data:
		# get_instance_transform() hands back identity and .buffer comes back
		# empty no matter what was written (re-verified on 4.7.1). instance_count
		# is a plain property and does survive, which is why it is checked above.
		# The placement math is proven offline instead -- test_math.py section 9
		# (300-case Ry(+90) glTF axis-fix proof) and test_tscn_writer.py, which
		# reads the written floats straight out of the .tscn text.
	else:
		check("Foliage_Grass is a MultiMeshInstance3D", false, str(grass_node))

	var hidden_node := find_node(root, "Instances_Hidden")
	check("Instances_Hidden exists", hidden_node != null)
	if hidden_node and hidden_node is MultiMeshInstance3D:
		var hidden := hidden_node as MultiMeshInstance3D
		check("an instanced component hidden in Unreal imports invisible", not hidden.visible)
		check("a shadow-casting component keeps Godot's default",
			hidden.cast_shadow == GeometryInstance3D.SHADOW_CASTING_SETTING_ON,
			str(hidden.cast_shadow))
		check("an unculled component gets no visibility range",
			absf(hidden.visibility_range_end) < 0.001, str(hidden.visibility_range_end))


func _test_lights(root: Node) -> void:
	# Only the real engine can confirm these property names exist on the real
	# Light3D classes and survive being set -- the unit tests stub everything.
	var sun_node := find_node(root, "Sun_Test")
	check("Sun_Test exists", sun_node != null)
	if sun_node and sun_node is DirectionalLight3D:
		var sun := sun_node as DirectionalLight3D
		check("directional light energy survives the import",
			absf(sun.light_energy - 1.0) < 0.001, str(sun.light_energy))
		check("UE LightSourceAngle becomes light_angular_distance",
			absf(sun.light_angular_distance - 0.5357) < 0.001, str(sun.light_angular_distance))
		check("UE dynamic shadow distance becomes directional_shadow_max_distance",
			absf(sun.directional_shadow_max_distance - 400.0) < 0.001,
			str(sun.directional_shadow_max_distance))
		check("a directional light keeps Godot's 1.0 specular at UE's default scale",
			absf(sun.light_specular - 1.0) < 0.001, str(sun.light_specular))
		check("a light UE never culls gets no distance fade", not sun.distance_fade_enabled)
	else:
		check("Sun_Test is a DirectionalLight3D", false, str(sun_node))

	var spot_node := find_node(root, "Spot_Test")
	check("Spot_Test exists", spot_node != null)
	if spot_node and spot_node is SpotLight3D:
		var spot := spot_node as SpotLight3D
		check("spot range comes from the UE attenuation radius",
			absf(spot.spot_range - 12.0) < 0.001, str(spot.spot_range))
		check("spot angle comes from the UE outer cone",
			absf(spot.spot_angle - 40.0) < 0.001, str(spot.spot_angle))
		check("the UE inner cone sharpens the falloff",
			absf(spot.spot_angle_attenuation - 2.0) < 0.001, str(spot.spot_angle_attenuation))
		check("UE IndirectLightingIntensity becomes light_indirect_energy",
			absf(spot.light_indirect_energy - 0.25) < 0.001, str(spot.light_indirect_energy))
		check("UE VolumetricScatteringIntensity becomes light_volumetric_fog_energy",
			absf(spot.light_volumetric_fog_energy - 2.0) < 0.001,
			str(spot.light_volumetric_fog_energy))
		# Godot ships omni/spot at 0.5 specular; UE's 0.5 SpecularScale halves it.
		check("UE SpecularScale scales Godot's own 0.5 default",
			absf(spot.light_specular - 0.25) < 0.001, str(spot.light_specular))
		check("UE SourceRadius becomes light_size for soft shadows",
			absf(spot.light_size - 0.1) < 0.001, str(spot.light_size))
		check("UE MaxDrawDistance becomes a light distance fade",
			spot.distance_fade_enabled and absf(spot.distance_fade_begin - 40.0) < 0.001
			and absf(spot.distance_fade_length - 10.0) < 0.001,
			"%s begin=%s len=%s" % [spot.distance_fade_enabled, spot.distance_fade_begin,
				spot.distance_fade_length])
		check("light mobility rides along as metadata",
			spot.get_meta("unreal_mobility", "") == "static", str(spot.get_meta("unreal_mobility", "")))
	else:
		check("Spot_Test is a SpotLight3D", false, str(spot_node))

	var rect_node := find_node(root, "Rect_Test")
	check("Rect_Test exists", rect_node != null)
	if rect_node and rect_node is OmniLight3D:
		var rect := rect_node as OmniLight3D
		check("a rect light is approximated by an omni and marked as such",
			rect.get_meta("unreal_rect_light", false), str(rect.get_meta("unreal_rect_light", false)))
		check("the rect panel size survives as metadata",
			rect.get_meta("unreal_rect_size_m", Vector2.ZERO).is_equal_approx(Vector2(0.64, 1.28)),
			str(rect.get_meta("unreal_rect_size_m", Vector2.ZERO)))
		check("the rect panel softens the shadow via light_size",
			absf(rect.light_size - 0.64) < 0.001, str(rect.light_size))
		check("a light switched off in Unreal imports invisible", not rect.visible)
	else:
		check("Rect_Test is an OmniLight3D", false, str(rect_node))


func _test_materials(root: Node) -> void:
	print("\\n--- materials ---")
	var textured := find_node(root, "Textured_1")
	check("Textured_1 exists", textured != null)
	if textured == null:
		return

	var mi: MeshInstance3D = null
	for c in textured.get_children():
		if c is MeshInstance3D:
			mi = c
		else:
			for g in c.get_children():
				if g is MeshInstance3D:
					mi = g
	if mi == null and textured is MeshInstance3D:
		mi = textured
	check("found a MeshInstance3D under Textured_1", mi != null)
	if mi == null:
		return

	# A: does Godot resolve "../textures/TX_Test_ALB.png" out of a SIBLING folder?
	# This is what inject_texture_references() writes into every exported .gltf.
	var gltf_mat = mi.mesh.surface_get_material(0) if mi.mesh and mi.mesh.get_surface_count() > 0 else null
	var gltf_albedo = null
	if gltf_mat and gltf_mat is BaseMaterial3D:
		gltf_albedo = (gltf_mat as BaseMaterial3D).albedo_texture
	check("glTF resolved a ../textures/ sibling uri into a real texture",
		gltf_albedo != null,
		"gltf material=%s" % [gltf_mat])

	# B: the importer's own material, built from the layout JSON.
	var m = mi.get_surface_override_material(0)
	check("importer applied a surface override material", m != null)
	if m == null or not (m is BaseMaterial3D):
		return
	var bm := m as BaseMaterial3D

	check("albedo texture bound from the sibling textures/ folder",
		bm.albedo_texture != null)
	check("normal map bound and enabled",
		bm.normal_texture != null and bm.normal_enabled)

	# C: the packed RMA map -- the actual bug. One texture, three channels.
	check("roughness driven by the packed map", bm.roughness_texture != null)
	check("roughness reads the RED channel",
		bm.roughness_texture_channel == BaseMaterial3D.TEXTURE_CHANNEL_RED,
		str(bm.roughness_texture_channel))
	check("metallic driven by the packed map", bm.metallic_texture != null)
	check("metallic reads the GREEN channel (not RED -- that would be roughness)",
		bm.metallic_texture_channel == BaseMaterial3D.TEXTURE_CHANNEL_GREEN,
		str(bm.metallic_texture_channel))
	check("ambient occlusion enabled", bm.ao_enabled)
	check("AO reads the BLUE channel",
		bm.ao_texture_channel == BaseMaterial3D.TEXTURE_CHANNEL_BLUE,
		str(bm.ao_texture_channel))
	check("roughness/metallic/AO share one texture resource",
		bm.roughness_texture == bm.metallic_texture and bm.metallic_texture == bm.ao_texture)

	# Scalars must stay at 1.0 or they would cancel the packed map out.
	check("roughness scalar passes the map through (1.0)",
		is_equal_approx(bm.roughness, 1.0), str(bm.roughness))
	check("metallic scalar passes the map through (1.0, not the 0.0 default)",
		is_equal_approx(bm.metallic, 1.0), str(bm.metallic))


func _dump(n: Node, depth: int) -> void:
	var pos := ""
	if n is Node3D:
		pos = " @ " + str((n as Node3D).global_transform.origin)
	print("  ".repeat(depth), n.name, " [", n.get_class(), "]", pos)
	for c in n.get_children():
		_dump(c, depth + 1)
'''

with open(os.path.join(PLUGIN_DIR, "test_runner.gd"), "w", encoding="utf-8") as f:
    f.write(RUNNER)

# Enable only the test runner (the importer addon is preloaded directly, not enabled).
with open(os.path.join(HARNESS, "project.godot"), "w", encoding="utf-8") as f:
    f.write('config_version=5\n\n[application]\n\nconfig/name="ImporterHarness"\n'
            'config/features=PackedStringArray("4.6")\n\n'
            '[editor_plugins]\n\nenabled=PackedStringArray("res://addons/test_runner/plugin.cfg")\n')

print("test plugin written to", PLUGIN_DIR)
