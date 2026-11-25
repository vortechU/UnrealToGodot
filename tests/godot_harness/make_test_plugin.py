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
const EnvImporter = preload("res://addons/unreal_importer/import_environment.gd")
const Common = preload("res://addons/unreal_importer/import_common.gd")
const SplineHelper = preload("res://addons/unreal_importer/import_spline_mesh.gd")

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

	# 0. Mesh actors are grouped, the way lights and decals already are. A level is
	#    mostly static meshes, so loose at the root they bury everything else.
	var mesh_group := root.get_node_or_null("UnrealStaticMeshes")
	check("mesh actors are grouped under UnrealStaticMeshes", mesh_group != null,
		str(root.get_children()))
	if mesh_group and mesh_group is Node3D:
		check("the mesh container is at identity (it must not move anything)",
			(mesh_group as Node3D).transform.is_equal_approx(Transform3D.IDENTITY),
			str((mesh_group as Node3D).transform))
		check("mesh actors really live inside the container",
			mesh_group.get_child_count() > 0 and root.get_node_or_null("Buoy_1") == null,
			"%d children" % mesh_group.get_child_count())
	check("lights are still grouped separately", root.get_node_or_null("UnrealLights") != null)

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

	# 4b. Non-uniformly scaled actor WITH collision -- the Jolt case.
	#
	#     Jolt cannot apply a non-uniform scale to a sphere, a capsule, or a
	#     rotated box/hull. It does not fail loudly: it substitutes the MEAN of
	#     the three axes and logs an error per body on every single load, leaving
	#     every collider a few percent off the mesh it was exported to hug. So the
	#     body must carry rotation only, with the scale baked into the shapes.
	#
	#     Scaled_1's placement is diag(2,1,0.5); after the glTF axis fix Ry(+90)
	#     that is Ry(90) with a LOCAL scale of (0.5, 1, 2) -- so the shapes take
	#     (0.5, 1, 2) and the body keeps a bare Ry(90).
	var scaled := find_node(root, "Scaled_1")
	check("Scaled_1 exists", scaled != null)
	if scaled and scaled is StaticBody3D:
		var body := scaled as StaticBody3D
		var bb: Basis = body.global_transform.basis
		check("a scaled body carries NO scale (Jolt would reject it)",
			absf(bb.x.length() - 1.0) < 0.001
			and absf(bb.y.length() - 1.0) < 0.001
			and absf(bb.z.length() - 1.0) < 0.001,
			"column lengths %f/%f/%f" % [bb.x.length(),
				bb.y.length(), bb.z.length()])
		check("the scaled body is the bare Ry(90) placement",
			near(bb.x, Vector3(0, 0, -1)) and near(bb.y, Vector3(0, 1, 0))
			and near(bb.z, Vector3(1, 0, 0)),
			"x=%s y=%s z=%s" % [bb.x, bb.y, bb.z])

		# The mesh has to take the scale the body gave up, or the prop renders
		# unscaled -- a silent visual regression the collision fix must not cause.
		var mesh_child: Node3D = null
		for c in body.get_children():
			if c is Node3D and not (c is CollisionShape3D):
				mesh_child = c
		check("the mesh instance takes the scale the body gave up",
			mesh_child != null and near(mesh_child.transform.basis.get_scale(), Vector3(0.5, 1, 2)),
			str(mesh_child.transform.basis.get_scale()) if mesh_child else "no mesh child")

		var shapes := {}
		for c in body.get_children():
			if c is CollisionShape3D:
				shapes[(c as CollisionShape3D).shape.get_class()] = c

		# The box is the case that must come out EXACT. Its world centre and its
		# three world half-extent vectors are what the old (scaled-body) math
		# produced; if the bake moved any of them, the collider no longer matches
		# the mesh, which is the whole thing being fixed.
		var box_node: CollisionShape3D = shapes.get("BoxShape3D")
		check("box collision shape exists", box_node != null)
		if box_node:
			var box := box_node.shape as BoxShape3D
			check("box size absorbed the body scale",
				near(box.size, Vector3(0.5, 1.0, 2.0)), str(box.size))
			var wt: Transform3D = box_node.global_transform
			check("box world centre is unchanged by the bake",
				near(wt.origin, Vector3(30, 0, -0.5)), str(wt.origin))
			check("box world half-extents are unchanged by the bake",
				near(wt.basis.x * 0.5 * box.size.x, Vector3(0, 0, -0.25))
				and near(wt.basis.y * 0.5 * box.size.y, Vector3(0, 0.5, 0))
				and near(wt.basis.z * 0.5 * box.size.z, Vector3(1, 0, 0)),
				"%s / %s / %s" % [wt.basis.x * 0.5 * box.size.x,
					wt.basis.y * 0.5 * box.size.y,
					wt.basis.z * 0.5 * box.size.z])

		# A hull is a raw point cloud, so it takes the scale exactly too: the
		# unit-cube corner (1,1,1) has to become (0.5, 1, 2).
		var hull_node: CollisionShape3D = shapes.get("ConvexPolygonShape3D")
		check("convex collision shape exists", hull_node != null)
		if hull_node:
			var pts: PackedVector3Array = (hull_node.shape as ConvexPolygonShape3D).points
			var found := false
			for p in pts:
				if near(p, Vector3(0.5, 1.0, 2.0)):
					found = true
			check("hull points absorbed the body scale exactly",
				found, str(pts))
			var corner: Vector3 = hull_node.global_transform * Vector3(0.5, 1.0, 2.0)
			check("hull corner lands where the unscaled-body math put it",
				near(corner, Vector3(32, 1, -0.5)), str(corner))

		# A sphere has one radius, so it can only take the mean of (0.5, 1, 2).
		# Approximate by nature -- pinned so the approximation cannot drift.
		var sph_node: CollisionShape3D = shapes.get("SphereShape3D")
		check("sphere collision shape exists", sph_node != null)
		if sph_node:
			var sph := sph_node.shape as SphereShape3D
			check("sphere radius takes the mean of the three axes",
				absf(sph.radius - 0.5 * (0.5 + 1.0 + 2.0) / 3.0) < 0.001, str(sph.radius))

		# A capsule scales its cylinder along local Y and its radius by X/Z.
		var cap_node: CollisionShape3D = shapes.get("CapsuleShape3D")
		check("capsule collision shape exists", cap_node != null)
		if cap_node:
			var cap := cap_node.shape as CapsuleShape3D
			# radius 25 cm * (0.5 + 2)/2, cylinder 100 cm * 1.0
			check("capsule radius takes the radial (X/Z) scale",
				absf(cap.radius - 0.3125) < 0.001, str(cap.radius))
			check("capsule height takes the axial (Y) scale",
				absf(cap.height - (1.0 + 2.0 * 0.3125)) < 0.001, str(cap.height))
	else:
		check("Scaled_1 is a StaticBody3D", false, str(scaled))

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
		check("Decal width half-extent (UE +Z) == (0, 7.68, 0)", near(dw, Vector3(0, 7.68, 0)), str(dw))
		check("Decal projection depth half-extent == (-6.4, 0, 0)", near(dd, Vector3(-6.4, 0, 0)), str(dd))
		check("Decal height half-extent (UE +Y) == (0, 0, 5.12)", near(dh, Vector3(0, 0, 5.12)), str(dh))
		# Projection runs along local -Y, towards UE local +X.
		check("Decal projects along the converted UE +X",
			near((-db.y).normalized(), Vector3(1, 0, 0)), str((-db.y).normalized()))
		check("Decal depth NOT the pre-fix 2.56 m half-extent",
			not near(dd.abs(), Vector3(2.56, 0, 0)), str(dd))
		# Material tint / DecalColor / opacity all land in modulate, and UE's
		# screen-size fade arrives as a distance fade. These used to be dropped.
		check("Decal modulate carries the UE tint and opacity",
			d.modulate.is_equal_approx(Color(0.8, 0.2, 0.2, 0.5).linear_to_srgb()), str(d.modulate))
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
	await _test_lods_and_skeletal_physics(root)
	_test_phase3_scene_parity(root)
	_test_survives_save(root)
	await _test_phase4_workflow(root)
	_test_terrain(root)
	_test_sweep2()
	await _test_sweep3()

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
			tint.is_equal_approx(Color(0.2, 0.8, 0.3).linear_to_srgb()), str(tint))

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
			# plus the radius of the two instances, 3 m apart on X and Z
			absf(grass.visibility_range_end - 52.1213) < 0.001, str(grass.visibility_range_end))
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
		check("an inverse-square UE light gets Godot's inverse-square decay",
			absf(spot.spot_attenuation - 2.0) < 0.001, str(spot.spot_attenuation))
		check("spot angle comes from the UE outer cone",
			absf(spot.spot_angle - 40.0) < 0.001, str(spot.spot_angle))
		check("the spot reaches half brightness at UE's angle (20/40 degree cones)",
			absf(spot.spot_angle_attenuation - 1.0734) < 0.001, str(spot.spot_angle_attenuation))
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

	# D: Phase 1 -- masked alpha cutout, two-sided culling, emissive, and normal Y-flip.
	check("masked material uses TRANSPARENCY_ALPHA_SCISSOR",
		bm.transparency == BaseMaterial3D.TRANSPARENCY_ALPHA_SCISSOR, str(bm.transparency))
	check("masked material preserves UE opacity_mask_clip_value",
		absf(bm.alpha_scissor_threshold - 0.45) < 0.001, str(bm.alpha_scissor_threshold))
	var cutout := bm.albedo_texture.get_image().get_pixel(0, 0) if bm.albedo_texture else Color.BLACK
	check("opacity mask is read from its opacity_channel (blue)",
		absf(cutout.a - 128.0 / 255.0) < 0.01, str(cutout))

	# A decal crop running past the atlas edge wraps round, as Unreal's sampler does.
	var sheet := Image.create_empty(4, 1, false, Image.FORMAT_RGBA8)
	for x in 4:
		sheet.set_pixel(x, 0, Color8(x * 10, 0, 0))
	var wrapped: Texture2D = EnvImporter.new()._crop_decal_texture(
		"sheet", ImageTexture.create_from_image(sheet), [0.75, 0.0, 0.5, 1.0], false, false)
	var strip := wrapped.get_image()
	check("a decal crop past the edge wraps to the other side",
		strip.get_width() == 2 and strip.get_pixel(0, 0).r8 == 30
		and strip.get_pixel(1, 0).r8 == 0 and strip.get_pixel(1, 0).a8 == 255,
		"%s %s" % [strip.get_pixel(0, 0), strip.get_pixel(strip.get_width() - 1, 0)])
	# A decal's color grade is baked, clamped at white; a third of a turn is red -> green.
	var paint := Image.create_empty(2, 1, false, Image.FORMAT_RGBA8)
	paint.set_pixel(0, 0, Color8(255, 0, 0, 128))
	paint.set_pixel(1, 0, Color8(100, 100, 100))
	var graded: Image = EnvImporter.new()._crop_decal_texture(
		"paint", ImageTexture.create_from_image(paint), [0.0, 0.0, 1.0, 1.0], false, false, false, 1.5, 1.0 / 3.0).get_image()
	check("a decal's hue shift turns its color and keeps its alpha",
		graded.get_pixel(0, 0).is_equal_approx(Color8(0, 255, 0, 128)), str(graded.get_pixel(0, 0)))
	check("a decal's brightness multiplies linear light",
		absf(graded.get_pixel(1, 0).srgb_to_linear().r / Color8(100, 100, 100).srgb_to_linear().r - 1.5) < 0.1,
		str(graded.get_pixel(1, 0)))
	check("two-sided material disables backface culling",
		bm.cull_mode == BaseMaterial3D.CULL_DISABLED, str(bm.cull_mode))
	check("emissive material enables emission with multiply operator and energy",
		bm.emission_enabled and bm.emission_texture != null
		and bm.emission_operator == BaseMaterial3D.EMISSION_OP_MULTIPLY
		and absf(bm.emission_energy_multiplier - 2.5) < 0.001,
		"enabled=%s op=%s energy=%s" % [bm.emission_enabled, bm.emission_operator, bm.emission_energy_multiplier])

	# Normal map .import flag: DX normal gets process/normal_map_invert_y=true,
	# while OpenGL-authored (flip_green_channel=true) does not.
	var dx_imp := FileAccess.get_file_as_string("res://textures/TX_Test_NRM.png.import")
	check("DirectX normal map .import sets process/normal_map_invert_y=true",
		dx_imp.contains("process/normal_map_invert_y=true"), dx_imp)
	var gl_imp := FileAccess.get_file_as_string("res://textures/TX_Test_NRM_GL.png.import")
	check("OpenGL-authored normal map does NOT invert Y again",
		not gl_imp.contains("process/normal_map_invert_y=true"), gl_imp)


func _test_lods_and_skeletal_physics(root: Node) -> void:
	print("\\n--- LOD chains & skeletal physics ---")
	var crate := find_node(root, "Crate_1")
	check("Crate_1 exists for LOD test", crate != null)
	if crate:
		var lod0_mi := find_node(crate, "TriNode") as MeshInstance3D
		check("Crate_1 LOD0 visibility_range_end == 12.0",
			lod0_mi != null and absf(lod0_mi.visibility_range_begin) < 0.001
			and absf(lod0_mi.visibility_range_end - 12.0) < 0.001,
			"begin=%s end=%s" % [lod0_mi.visibility_range_begin if lod0_mi else -1,
				lod0_mi.visibility_range_end if lod0_mi else -1])
		var lod1 := crate.get_node_or_null("SM_Crate_LOD1")
		var lod2 := crate.get_node_or_null("SM_Crate_LOD2")
		check("Crate_1 instanced LOD1 and LOD2 children", lod1 != null and lod2 != null)
		if lod1:
			var lod1_mi := find_node(lod1, "TriNode") as MeshInstance3D
			check("LOD1 visibility_range is 12.0 .. 28.0",
				lod1_mi != null and absf(lod1_mi.visibility_range_begin - 12.0) < 0.001
				and absf(lod1_mi.visibility_range_end - 28.0) < 0.001,
				"begin=%s end=%s" % [lod1_mi.visibility_range_begin if lod1_mi else -1,
					lod1_mi.visibility_range_end if lod1_mi else -1])
		if lod2:
			var lod2_mi := find_node(lod2, "TriNode") as MeshInstance3D
			check("LOD2 visibility_range is 28.0 .. unlimited (0.0)",
				lod2_mi != null and absf(lod2_mi.visibility_range_begin - 28.0) < 0.001
				and absf(lod2_mi.visibility_range_end) < 0.001,
				"begin=%s end=%s" % [lod2_mi.visibility_range_begin if lod2_mi else -1,
					lod2_mi.visibility_range_end if lod2_mi else -1])

	var hero := find_node(root, "Hero_1")
	check("Hero_1 skeletal actor exists", hero != null)
	if hero:
		var sim := find_node(hero, "UnrealPhysicalBoneSimulator")
		check("skeletal physics built UnrealPhysicalBoneSimulator under Skeleton3D",
			sim != null and sim.get_parent() is Skeleton3D,
			str(sim.get_parent() if sim else null))
		if sim:
			var pb_root := sim.get_node_or_null("PhysicalBone_root") as PhysicalBone3D
			var pb_spine := sim.get_node_or_null("PhysicalBone_spine") as PhysicalBone3D
			check("PhysicalBone3D created for root and case-insensitively matched spine",
				pb_root != null and pb_spine != null and pb_spine.bone_name == "spine",
				"root=%s spine=%s" % [pb_root, pb_spine])
			if pb_spine:
				var cap_node := pb_spine.get_node_or_null("CapsuleCollision") as CollisionShape3D
				check("spine PhysicalBone3D has CapsuleShape3D with converted height",
					cap_node != null and cap_node.shape is CapsuleShape3D
					and absf((cap_node.shape as CapsuleShape3D).radius - 0.15) < 0.001
					and absf((cap_node.shape as CapsuleShape3D).height - 0.80) < 0.001,
					str(cap_node.shape if cap_node else null))

	# Also test save_external_materials=true in a separate throwaway root.
	var ext_root := Node3D.new()
	ext_root.name = "ExtMatRoot"
	get_tree().get_root().add_child(ext_root)
	var imp2 = Importer.new()
	var ok2 = await imp2.do_import("res://level_layout.json", "res://models/",
		"res://textures/", ext_root, {"apply_metadata": false, "build_terrain": false,
			"apply_foliage": false, "apply_lights": false, "apply_decals": false,
			"save_external_materials": true})
	check("external .tres material import succeeded", ok2 == true)
	check("external .tres material file created on disk",
		FileAccess.file_exists("res://materials/MI_Test.tres"))
	ext_root.queue_free()


func _test_survives_save(root: Node) -> void:
	# Godot drops changes to the children of an instanced model on save unless
	# the instance is editable; everything checked above lives on such children.
	print("\\n--- save & reload ---")
	var packed := PackedScene.new()
	check("imported scene packs", packed.pack(root) == OK)
	var again := packed.instantiate()
	var textured := find_node(again, "Textured_1")
	var meshes := textured.find_children("*", "MeshInstance3D", true, false) if textured else []
	if textured is MeshInstance3D:
		meshes.append(textured)
	check("material overrides survive save and reload",
		not meshes.is_empty() and meshes[0].get_surface_override_material(0) is BaseMaterial3D)
	var crate := find_node(again, "Crate_1")
	var lod := find_node(crate, "TriNode") as MeshInstance3D if crate else null
	check("LOD ranges survive save and reload",
		lod != null and absf(lod.visibility_range_end - 12.0) < 0.001)
	var decor := find_node(again, "Decor_Hidden")
	var dmi := find_node(decor, "TriNode") as MeshInstance3D if decor else null
	check("shadow flags survive save and reload",
		dmi != null and dmi.cast_shadow == GeometryInstance3D.SHADOW_CASTING_SETTING_OFF)
	var pipe := find_node(again, "Pipe_Spline")
	var pmi := find_node(pipe, "TriNode") as MeshInstance3D if pipe else null
	check("spline deformation survives save and reload",
		pmi != null and pmi.mesh != null and pmi.mesh.get_aabb().end.x > 3.5)
	var painted := find_node(again, "Painted_1")
	var paint_mi: MeshInstance3D = find_node(painted, "TriNode") as MeshInstance3D if painted else null
	var colors := PackedColorArray()
	if paint_mi and paint_mi.mesh is ArrayMesh:
		var arrays := (paint_mi.mesh as ArrayMesh).surface_get_arrays(0)
		if arrays[Mesh.ARRAY_COLOR] != null:
			for v in [Vector3(0, 0, 0), Vector3(1, 0, 0), Vector3(0, 1, 0)]:
				colors.append(arrays[Mesh.ARRAY_COLOR][(arrays[Mesh.ARRAY_VERTEX] as PackedVector3Array).find(v)])
	check("vertex paint lands on its own vertices and survives save and reload",
		colors == PackedColorArray([Color.RED, Color.GREEN, Color.BLUE]), str(colors))
	var puddle := paint_mi.get_surface_override_material(0) as ShaderMaterial if paint_mi else null
	check("a translated shader survives with its textures", puddle != null
		and puddle.shader.code.contains("COLOR.g")
		and puddle.get_shader_parameter("tex_0") is Texture2D
		and puddle.get_shader_parameter("tex_1") is Texture2D)
	check("other placements of the mesh stay unpainted",
		not meshes.is_empty() and meshes[0].mesh.surface_get_arrays(0)[Mesh.ARRAY_COLOR] == null)
	var hero := find_node(again, "Hero_1")
	check("skeletal physics survives save and reload",
		hero != null and find_node(hero, "PhysicalBone_spine") != null)
	again.free()


func _test_phase3_scene_parity(root: Node) -> void:
	print("\\n--- Phase 3: render flags, folders, splines, probes, cameras, audio ---")
	var mesh_group := root.get_node_or_null("UnrealStaticMeshes")
	check("Outliner folder Architecture/Interior created",
		mesh_group != null and mesh_group.get_node_or_null("Architecture/Interior/Decor_Hidden") != null)
	var decor := find_node(root, "Decor_Hidden")
	if decor and decor is Node3D:
		check("NoCollision component does NOT create a StaticBody3D", not (decor is StaticBody3D), decor.get_class())
		check("hidden component imports invisible", not (decor as Node3D).visible)
		var dmi := find_node(decor, "TriNode") as MeshInstance3D
		check("shadowless + max_draw_distance_m applied on static mesh",
			dmi != null and dmi.cast_shadow == GeometryInstance3D.SHADOW_CASTING_SETTING_OFF
			and absf(dmi.visibility_range_end - 45.0) < 0.001,
			"shadow=%s end=%s" % [dmi.cast_shadow if dmi else -1, dmi.visibility_range_end if dmi else -1])

	var pipe := find_node(root, "Pipe_Spline")
	check("SplineMesh Pipe_Spline exists under Architecture/Pipes",
		mesh_group != null and mesh_group.get_node_or_null("Architecture/Pipes/Pipe_Spline") != null)
	if pipe:
		check("Pipe_Spline stamped with unreal_spline_mesh metadata", bool(pipe.get_meta("unreal_spline_mesh", false)))
		var pmi := find_node(pipe, "TriNode") as MeshInstance3D
		if pmi and pmi.mesh:
			var aabb := pmi.mesh.get_aabb()
			check("SplineMesh vertices deformed towards end_pos (4, 2, 0)",
				aabb.end.x > 3.5 and aabb.end.y > 1.5, str(aabb))

	var probe := find_node(root, "BoxProbe_1") as ReflectionProbe
	check("BoxProbe_1 ReflectionProbe exists under UnrealReflectionProbes",
		probe != null and root.get_node_or_null("UnrealReflectionProbes/BoxProbe_1") != null)
	if probe:
		check("ReflectionProbe size, intensity and box_projection match",
			near(probe.size, Vector3(12, 6, 8)) and absf(probe.intensity - 1.5) < 0.001 and probe.box_projection,
			"size=%s int=%s box=%s" % [probe.size, probe.intensity, probe.box_projection])

	var cam := find_node(root, "ShowcaseCam") as Camera3D
	check("ShowcaseCam Camera3D exists under UnrealCameras",
		cam != null and root.get_node_or_null("UnrealCameras/ShowcaseCam") != null)
	if cam:
		check("Camera3D uses KEEP_WIDTH horizontal FOV (85 deg) and clip planes",
			cam.keep_aspect == Camera3D.KEEP_WIDTH and absf(cam.fov - 85.0) < 0.001
			and absf(cam.near - 0.1) < 0.001 and absf(cam.far - 2000.0) < 0.01,
			"aspect=%s fov=%s near=%s far=%s" % [cam.keep_aspect, cam.fov, cam.near, cam.far])

	var faded := find_node(root, "Decal_0") as Decal
	for d in root.find_children("*", "Decal", true, false):
		faded = d
	check("decals do not fade with depth, short of 0 (a NaN in Godot)",
		faded != null and is_equal_approx(faded.upper_fade, 0.01) and is_equal_approx(faded.lower_fade, 0.01))
	var cable := root.get_node_or_null("UnrealCables/Post_Cable") as MeshInstance3D
	check("a cable is rebuilt under UnrealCables", cable != null and cable.mesh != null)
	if cable and cable.mesh:
		var box := cable.transform * cable.mesh.get_aabb()
		check("...along its line and as thick as Unreal draws it",
			absf(box.position.z + 4.0) < 0.06 and absf(box.end.z) < 0.06 and absf(box.size.x - 0.1) < 0.02
			and absf(box.position.y - 4.45) < 0.03 and absf(box.end.y - 5.05) < 0.03, str(box))
		check("...with its material, shadow flag and draw distance",
			(cable.mesh.surface_get_material(0) as BaseMaterial3D) != null
			and is_equal_approx((cable.mesh.surface_get_material(0) as BaseMaterial3D).roughness, 0.4)
			and cable.cast_shadow == GeometryInstance3D.SHADOW_CASTING_SETTING_OFF
			and is_equal_approx(cable.visibility_range_end, 40.0))
		var arr := cable.mesh.surface_get_arrays(0)
		var vs: PackedVector3Array = arr[Mesh.ARRAY_VERTEX]
		var ns: PackedVector3Array = arr[Mesh.ARRAY_NORMAL]
		var ix: PackedInt32Array = arr[Mesh.ARRAY_INDEX]
		var inside_out := 0
		for i in range(0, ix.size(), 3):
			# Godot's front faces wind clockwise.
			if (vs[ix[i + 2]] - vs[ix[i]]).cross(vs[ix[i + 1]] - vs[ix[i]]).dot(ns[ix[i]]) <= 0.0:
				inside_out += 1
		check("...and faces outwards", ix.size() == 60 and inside_out == 0, "%d of %d" % [inside_out, ix.size() / 3])

	var aud := find_node(root, "AmbientWind") as AudioStreamPlayer3D
	check("AmbientWind AudioStreamPlayer3D exists under UnrealAudio",
		aud != null and root.get_node_or_null("UnrealAudio/AmbientWind") != null)
	if aud:
		check("AudioStreamPlayer3D volume_db, pitch, max_distance, autoplay match",
			absf(aud.volume_db + 6.0) < 0.01 and absf(aud.pitch_scale - 1.1) < 0.01
			and absf(aud.max_distance - 35.0) < 0.01 and aud.autoplay,
			"db=%s pitch=%s dist=%s auto=%s" % [aud.volume_db, aud.pitch_scale, aud.max_distance, aud.autoplay])


func node_names(n: Node) -> Array:
	var out := [str(n.name)]
	for c in n.get_children():
		out.append_array(node_names(c))
	out.sort()
	return out


func _test_phase4_workflow(root: Node) -> void:
	print("\\n--- Phase 4: incremental update, Blueprint prefabs, dry-run preview & dock config ---")
	# 1. Pre-import JSON dry-run summary
	var summary: Dictionary = Importer.summarize_layout_json(
		"res://level_layout.json", "res://models/", "res://textures/")
	check("dry-run JSON summary parses cleanly", bool(summary.get("ok", false)))
	check("dry-run summary counts actors, probes, cameras, audio accurately",
		int(summary.get("actors", 0)) >= 8 and int(summary.get("reflection_probes", 0)) == 1
		and int(summary.get("cameras", 0)) == 1 and int(summary.get("audio", 0)) == 1
		and int(summary.get("missing_meshes", -1)) == 0, str(summary))

	# 2. Incremental non-destructive re-import on a fresh scene root
	var inc_root := Node3D.new()
	inc_root.name = "IncRoot"
	get_tree().get_root().add_child(inc_root)
	var imp_inc = Importer.new()
	var base_opts := {
		"apply_metadata": false, "build_terrain": false, "apply_foliage": false,
		"apply_lights": false, "apply_decals": false, "apply_environment": false,
		"apply_reflection_probes": false, "apply_cameras": false, "apply_audio": false,
	}
	await imp_inc.do_import("res://level_layout.json", "res://models/", "res://textures/", inc_root, base_opts)
	var buoy_before := find_node(inc_root, "Buoy_1")
	check("incremental setup imported Buoy_1 with unreal_actor_id",
		buoy_before != null and str(buoy_before.get_meta("unreal_actor_id", "")) == "ID_BUOY_1")
	if buoy_before:
		var custom := Node3D.new()
		custom.name = "UserCustomScriptNode"
		buoy_before.add_child(custom)

	var upd_opts := base_opts.duplicate()
	upd_opts["update_existing"] = true
	upd_opts["prune_removed"] = true
	await imp_inc.do_import("res://level_layout_v2.json", "res://models/", "res://textures/", inc_root, upd_opts)
	await get_tree().process_frame

	var buoy_after := find_node(inc_root, "Buoy_1")
	check("incremental update preserved existing Buoy_1 node instance and custom child",
		buoy_after == buoy_before and buoy_after.get_node_or_null("UserCustomScriptNode") != null)
	check("incremental update moved Buoy_1 in place to (99, 5, -10)",
		buoy_after is Node3D and near((buoy_after as Node3D).global_transform.origin, Vector3(99, 5, -10)),
		str((buoy_after as Node3D).global_transform.origin) if buoy_after is Node3D else "none")
	check("incremental update added Fresh_Actor", find_node(inc_root, "Fresh_Actor") != null)
	check("incremental update with prune_removed removed deleted Crate_1",
		find_node(inc_root, "Crate_1") == null)
	inc_root.queue_free()

	# 2b. Feature nodes (lights, decals, foliage, terrain...) must not duplicate on update.
	var feat_root := Node3D.new()
	feat_root.name = "FeatRoot"
	get_tree().get_root().add_child(feat_root)
	var feat_opts := {"apply_metadata": false, "update_existing": true, "terrain_mode": "mesh"}
	await Importer.new().do_import("res://level_layout.json", "res://models/", "res://textures/", feat_root, feat_opts)
	var feat_before := node_names(feat_root)
	var lights_before := feat_root.get_node("UnrealLights")
	var edited := lights_before.get_child(0)
	var edited_child := Node3D.new()
	edited_child.name = "UserChild"
	edited.add_child(edited_child)
	var user_light := OmniLight3D.new()
	user_light.name = "UserLight"
	lights_before.add_child(user_light)
	await Importer.new().do_import("res://level_layout.json", "res://models/", "res://textures/", feat_root, feat_opts)
	await get_tree().process_frame
	check("incremental update keeps user nodes in feature containers",
		is_instance_valid(user_light) and is_instance_valid(edited)
		and edited.get_node_or_null("UserChild") != null)
	if is_instance_valid(user_light):
		user_light.free()
	edited_child.free()
	var feat_after := node_names(feat_root)
	check("incremental update does not duplicate feature nodes",
		feat_after == feat_before and feat_root.get_node("UnrealLights") == lights_before,
		"before %s after %s" % [feat_before, feat_after])
	var unowned := 0
	for c in lights_before.get_children():
		if c.owner != feat_root:
			unowned += 1
	check("re-imported feature nodes stay owned by the scene root", unowned == 0, str(unowned))
	feat_root.queue_free()

	# 2c. A missing-mesh placeholder is not duplicated, and gives way to its mesh.
	var miss := FileAccess.open("res://level_layout_missing.json", FileAccess.WRITE)
	miss.store_string(FileAccess.get_file_as_string("res://level_layout_v2.json").replace("SM_Crate", "SM_NotHere"))
	miss.close()
	var miss_root := Node3D.new()
	get_tree().get_root().add_child(miss_root)
	for _i in range(2):
		await Importer.new().do_import("res://level_layout_missing.json", "res://models/", "res://textures/", miss_root, upd_opts)
		await get_tree().process_frame
	var markers := node_names(miss_root).filter(func(x): return "_MISSING_" in x)
	check("incremental update keeps a single missing-mesh placeholder", markers.size() == 1, str(markers))
	await Importer.new().do_import("res://level_layout_v2.json", "res://models/", "res://textures/", miss_root, upd_opts)
	await get_tree().process_frame
	markers = node_names(miss_root).filter(func(x): return "_MISSING_" in x)
	check("placeholder is replaced once its mesh exists",
		markers.is_empty() and find_node(miss_root, "Fresh_Actor") != null, str(markers))
	miss_root.queue_free()

	# 3. Blueprint prefab extraction (.tscn sub-scene)
	var pref_root := Node3D.new()
	pref_root.name = "PrefabRoot"
	get_tree().get_root().add_child(pref_root)
	var pref_opts := base_opts.duplicate()
	pref_opts["extract_prefabs"] = true
	# Only uniformly scaled actors share a prefab, and only without per-placement data:
	# the first, overridden copy must neither use the prefab nor leak into it.
	var pref_layout: Dictionary = JSON.parse_string(FileAccess.get_file_as_string("res://level_layout.json"))
	var piers: Array = []
	for a in pref_layout["actors"]:
		if a["name"] == "BP_Pier":
			a["godot_transform"]["scale"] = [1.0, 1.0, 1.0]
			var painted: Dictionary = a.duplicate(true)
			painted["name"] = "BP_Pier_Override"
			painted["actor_id"] = "ID_BP_PIER_OVERRIDE"
			painted["components"][0]["material_overrides"] = [{"slot_index": 0, "material_name": "MI_Leak",
				"parameters": {"base_color": [1, 0, 0, 1]}}]
			piers = [painted, a]
	pref_layout["actors"] = piers
	var pref_file := FileAccess.open("res://level_layout_prefab.json", FileAccess.WRITE)
	pref_file.store_string(JSON.stringify(pref_layout))
	pref_file.close()
	await Importer.new().do_import("res://level_layout_prefab.json", "res://models/", "res://textures/", pref_root, pref_opts)
	var bp_inst := find_node(pref_root, "BP_Pier")
	var bp_override := find_node(pref_root, "BP_Pier_Override")
	check("an actor with material overrides does not use the shared prefab",
		bp_override != null and bp_override.scene_file_path == "")
	check("per-placement overrides do not leak into the shared prefab",
		not FileAccess.get_file_as_string("res://prefabs/BP_Pier_C.tscn").contains("MI_Leak"))
	check("Blueprint prefab file res://prefabs/BP_Pier_C.tscn saved on disk",
		FileAccess.file_exists("res://prefabs/BP_Pier_C.tscn"))
	check("BP_Pier instantiated from shared .tscn prefab",
		bp_inst != null and bp_inst.scene_file_path == "res://prefabs/BP_Pier_C.tscn",
		bp_inst.scene_file_path if bp_inst else "null")
	pref_root.queue_free()

	# 4. Dock settings persistence round-trip
	const DockClass = preload("res://addons/unreal_importer/importer_dock.gd")
	var cfg_path := "res://test_dock_settings.cfg"
	var d1 = DockClass.new()
	get_tree().get_root().add_child(d1)
	d1.json_edit.text = "res://custom_map_layout.json"
	d1.energy_scale_spin.value = 2.25
	d1.update_existing_check.button_pressed = true
	d1.extract_prefabs_check.button_pressed = true
	d1._save_dock_settings(cfg_path)
	d1.queue_free()

	var d2 = DockClass.new()
	get_tree().get_root().add_child(d2)
	d2._load_dock_settings(cfg_path)
	check("dock settings persist paths, spinboxes, and toggles across reload",
		d2.json_edit.text == "res://custom_map_layout.json"
		and absf(d2.energy_scale_spin.value - 2.25) < 0.001
		and d2.update_existing_check.button_pressed
		and d2.extract_prefabs_check.button_pressed)
	d2.queue_free()


func _strip(fwd: Vector3, lateral: Vector3) -> ArrayMesh:
	var st := SurfaceTool.new()
	st.begin(Mesh.PRIMITIVE_TRIANGLES)
	for v in [Vector3.ZERO, fwd, lateral, fwd, fwd + lateral, lateral]:
		st.set_normal(Vector3.UP)
		st.add_vertex(v)
	return st.commit()


func _rolled(mesh: ArrayMesh, axis: String, roll: float) -> PackedVector3Array:
	var mi := MeshInstance3D.new()
	mi.mesh = mesh
	var dir := [0.0, 0.0, 1.0] if axis == "z" else [1.0, 0.0, 0.0]
	SplineHelper.deform_instance(mi, {"start_pos": [0.0, 0.0, 0.0], "end_pos": dir,
		"start_tangent": dir, "end_tangent": dir, "forward_axis": axis,
		"start_roll_rad": roll, "end_roll_rad": roll, "up_dir": [0.0, 1.0, 0.0]})
	var out: PackedVector3Array = mi.mesh.surface_get_arrays(0)[Mesh.ARRAY_VERTEX]
	mi.free()
	return out


func _test_sweep2() -> void:
	print("\\n--- second code sweep ---")
	var holder := Node3D.new()
	holder.name = "Sweep2"
	get_tree().get_root().add_child(holder)
	var tmp := OS.get_cache_dir().path_join("ue2g_harness")
	for sub in ["audio", "models"]:
		DirAccess.make_dir_recursive_absolute(tmp.path_join(sub))

	# 1. An external .wav really loads through the dynamic static call.
	var wav := AudioStreamWAV.new()
	wav.format = AudioStreamWAV.FORMAT_16_BITS
	wav.data = PackedByteArray([0, 0, 1, 0, 2, 0, 3, 0])
	wav.save_to_wav(tmp.path_join("audio/Sweep2Hum.wav"))
	EnvImporter.new()._apply_audio([{"name": "Hum", "sound_file": "Sweep2Hum.wav"}],
		holder, holder, {"json_dir": tmp})
	var hum := find_node(holder, "Hum")
	check("external WAV stream loads", hum != null and hum.get("stream") is AudioStreamWAV, str(hum))

	# 2. A bake sees geometry beside the region.
	var geo := Node3D.new()
	geo.add_to_group(Common.NAV_SOURCE_GROUP, true)
	holder.add_child(geo)
	var floor_mi := MeshInstance3D.new()
	var box_mesh := BoxMesh.new()
	box_mesh.size = Vector3(10, 0.2, 10)
	floor_mi.mesh = box_mesh
	geo.add_child(floor_mi)
	load("res://addons/unreal_importer/import_gameplay.gd").new().apply({"navigation": {"bounds_volumes": [
		{"name": "Nav", "extent_m": [6, 3, 6]}]}}, holder, holder, {"navigation_bake": true})
	var region := find_node(holder, "Nav") as NavigationRegion3D
	check("navigation bake finds sibling geometry", region != null
		and region.navigation_mesh.get_polygon_count() > 0,
		str(region.navigation_mesh.get_polygon_count()) if region else "no region")

	# 3. A rotated box under a mirror is the reflection of the original box.
	var local := Transform3D(Basis(Vector3(1, 2, 3).normalized(), 0.8), Vector3(1, 2, 3))
	var mirrored := Common.scaled_shape_transform(local, Vector3(-1, 1, 1))
	var reflected := Basis.from_scale(Vector3(-1, 1, 1)) * local.basis
	var same := absf(mirrored.basis.determinant() - 1.0) < 0.0001
	for i in 3:
		same = same and absf(absf(mirrored.basis[i].dot(reflected[i])) - 1.0) < 0.0001
	check("mirrored box collider is a right-handed reflection", same, str(mirrored))
	check("unmirrored scale leaves the shape rotation alone",
		Common.scaled_shape_transform(local, Vector3(2, 1, 1)).basis.is_equal_approx(local.basis))

	# A mirrored hull must land exactly where S * local puts every point.
	var hull_quat := [0.0, 0.0, 0.38268343, 0.92387953]
	var hull_lib := {"SM_Hull": {"collision": {"convex_hulls": [{
		"vertices": [[100, 0, 0], [0, 100, 0], [0, 0, 100], [50, 60, 70]],
		"godot_local_transform": {"translation": [1, 0, 0], "rotation_quat": hull_quat, "scale": [1, 1, 1]}}]}}}
	var imp = Importer.new()
	imp.active_scene_root = holder
	var hull_body := StaticBody3D.new()
	holder.add_child(hull_body)
	imp._populate_collision_shapes(hull_body, Transform3D(Basis.from_scale(Vector3(-1, 1, 1)), Vector3.ZERO), "SM_Hull", hull_lib)
	var hull_node := hull_body.get_child(0) as CollisionShape3D
	var hull_local := Transform3D(Basis(Vector3.BACK, PI / 4.0), Vector3(1, 0, 0))
	var got: Vector3 = hull_node.transform * (hull_node.shape as ConvexPolygonShape3D).points[3]
	var want: Vector3 = Vector3(-1, 1, 1) * (hull_local * (Vector3(50, 70, 60) * 0.01))
	check("mirrored convex hull stays exact", near(got, want), "%s vs %s" % [got, want])

	# 4. Incremental update: no false rebuild on a mirror, and user nodes untouched.
	var upd := StaticBody3D.new()
	holder.add_child(upd)
	var upd_mesh := load("res://models/SM_Rock.gltf").instantiate() as Node3D
	upd.add_child(upd_mesh)
	upd_mesh.transform = Transform3D(Basis.from_scale(Vector3(-1, 1, 1)), Vector3.ZERO)
	var user_area := Area3D.new()
	user_area.position = Vector3(4, 5, 6)
	upd.add_child(user_area)
	var user_model := load("res://models/SM_Crate.gltf").instantiate() as Node3D
	user_model.position = Vector3(7, 8, 9)
	upd.add_child(user_model)
	var kept := CollisionShape3D.new()
	kept.shape = BoxShape3D.new()
	upd.add_child(kept)
	var rock := {"mesh_key": "SM_Rock", "mesh_name": "SM_Rock"}
	var rock_lib := {"SM_Rock": {"collision": {"boxes": [{"size": [100, 100, 100]}]}}}
	imp._update_existing_body(upd, Transform3D(Basis.from_scale(Vector3(-1, 1, 1)), Vector3.ZERO), rock, rock_lib)
	check("a mirrored actor does not rebuild collision when nothing changed", is_instance_valid(kept))
	imp._update_existing_body(upd, Transform3D(Basis.from_scale(Vector3(2, 1, 1)), Vector3.ZERO), rock, rock_lib)
	check("a real scale change rescales the mesh and rebuilds collision",
		not is_instance_valid(kept) and near(upd_mesh.transform.basis.get_scale(), Vector3(2, 1, 1)))
	check("user nodes and models under an imported body keep their transform",
		near(user_area.position, Vector3(4, 5, 6)) and near(user_model.position, Vector3(7, 8, 9)))

	# 5 + 15. Nodes added inside instances survive undo/redo and save.
	var scene := Node3D.new()
	scene.name = "Scene"
	holder.add_child(scene)
	var hero := load("res://models/SK_Dummy.gltf").instantiate() as Node3D
	scene.add_child(hero)
	hero.owner = scene
	var bones: int = load("res://addons/unreal_importer/import_skeletal_physics.gd").new().attach_in_scene(scene, "res://models/", scene)
	check("attach_in_scene builds bones and makes the instance editable",
		bones == 2 and scene.is_editable_instance(hero), str(bones))
	var lod := load("res://models/SM_Crate_LOD1.gltf").instantiate() as Node3D
	lod.name = "TestLOD1"
	lod.set_meta("unreal_lod_index", 1)
	hero.add_child(lod)
	lod.owner = scene
	var inner_owner := find_node(hero, "SkMesh").owner
	scene.remove_child(hero)
	check("undo clears the added nodes' owner (fixture is real)", lod.owner == null)
	scene.add_child(hero)
	var dock = load("res://addons/unreal_importer/importer_dock.gd").new()
	dock._reown_subtree(hero, scene)
	check("redo leaves the model's own nodes owned by the model",
		inner_owner == hero and find_node(hero, "SkMesh").owner == hero)
	var packed := PackedScene.new()
	packed.pack(scene)
	var again := packed.instantiate()
	var spine := find_node(again, "PhysicalBone_spine")
	check("LODs and skeletal physics survive undo, redo and save",
		find_node(again, "TestLOD1") != null and spine != null and spine.get_child_count() == 1)
	check("no node is duplicated by the round trip", node_names(again) == node_names(scene),
		"%s vs %s" % [node_names(again), node_names(scene)])
	again.free()

	# 10. Re-docking keeps one UI.
	holder.add_child(dock)
	var ui_children: int = dock.get_child_count()
	holder.remove_child(dock)
	holder.add_child(dock)
	check("re-docking does not build the UI twice", ui_children == 1 and dock.get_child_count() == 1)
	dock.free()

	# 6. Per-placement data keeps an actor out of the shared prefab.
	check("per-instance geometry is detected",
		imp._has_per_instance_geometry([{"vertex_paint": "AAAA"}])
		and imp._has_per_instance_geometry([{}, {"material_overrides": [{"slot_index": 0}]}])
		and imp._has_per_instance_geometry([{"spline_mesh": {"roll": 1}}])
		and not imp._has_per_instance_geometry([{"vertex_paint": null, "material_overrides": [], "spline_mesh": {}}]))

	# 7. Positive roll turns right-handed about the direction of travel on every axis.
	for axis in ["x", "z"]:
		var fwd := Vector3.BACK if axis == "z" else Vector3.RIGHT
		var lateral := Vector3.RIGHT if axis == "z" else Vector3.UP
		var flat := _rolled(_strip(fwd, lateral), axis, 0.0)
		var turned := _rolled(_strip(fwd, lateral), axis, 0.5)
		var ok := flat.size() > 0
		for i in flat.size():
			ok = ok and near(flat[i].rotated(fwd, 0.5), turned[i])
		check("spline roll direction on the %s axis" % axis, ok, "%s -> %s" % [flat, turned])

	# 8. Same-named slots each get their own entry; shifted slots still match by name.
	var mats := [{"material_name": "M", "slot_index": 0}, {"material_name": "M", "slot_index": 1}]
	var claimed := {}
	check("duplicate material names resolve to their own slots",
		Common.find_material_entry(mats, "M", 0, claimed)["slot_index"] == 0
		and Common.find_material_entry(mats, "M", 1, claimed)["slot_index"] == 1)
	mats = [{"material_name": "X", "slot_index": 0}, {"material_name": "M", "slot_index": 1}, {"material_name": "M", "slot_index": 2}]
	claimed = {}
	check("duplicate names survive a dropped slot",
		Common.find_material_entry(mats, "M", 0, claimed)["slot_index"] == 1
		and Common.find_material_entry(mats, "M", 1, claimed)["slot_index"] == 2)

	# 9. A block-compressed opacity map is usable.
	var src := Image.create_empty(4, 4, false, Image.FORMAT_RGBA8)
	src.fill(Color(0.25, 0.5, 0.75, 1.0))
	src.compress(Image.COMPRESS_S3TC)
	var dds := tmp.path_join("op.dds")
	src.save_dds(dds)
	var raw_dds := Image.new()
	raw_dds.load_dds_from_buffer(FileAccess.get_file_as_bytes(dds))
	check("fixture .dds is really compressed", raw_dds.is_compressed())
	var merged: Texture2D = imp._combine_albedo_and_opacity("", dds, 0)
	check("compressed opacity packs into alpha", merged != null
		and absf(merged.get_image().get_pixel(1, 1).a - 0.25) < 0.05)

	# 11. A merged-away empty container does not push the kept one to "<Name>2".
	var merge_root := Node3D.new()
	holder.add_child(merge_root)
	var old_audio := Node3D.new()
	old_audio.name = "UnrealAudio"
	merge_root.add_child(old_audio)
	old_audio.add_child(Node3D.new())
	imp.active_scene_root = merge_root
	var previous: Dictionary = imp._set_aside_feature_containers()
	var empty := Node3D.new()
	empty.name = "UnrealAudio"
	merge_root.add_child(empty)
	empty.queue_free()
	imp._merge_feature_containers(previous)
	check("kept container gets its own name back", String(old_audio.name) == "UnrealAudio", old_audio.name)

	# 12. Component names are looked up the way Godot stored them; prefabs are not edited.
	var multi := Node3D.new()
	holder.add_child(multi)
	var part := Node3D.new()
	part.name = "Part.A"
	multi.add_child(part)
	var comps := [{"name": "Part.A", "godot_world_transform": {"translation": [3, 0, 0]}}, {"name": "B"}]
	imp._update_existing_actor_node(multi, holder, Transform3D.IDENTITY, {}, comps, null)
	check("a component with a sanitized name is updated", near(part.position, Vector3(3, 0, 0)), str(part.position))
	multi.scene_file_path = "res://prefabs/fake.tscn"
	part.position = Vector3.ZERO
	imp._update_existing_actor_node(multi, holder, Transform3D(Basis.IDENTITY, Vector3(1, 0, 0)), {}, comps, null)
	check("a prefab instance moves but its children are left alone",
		near(part.position, Vector3.ZERO) and near(multi.position, Vector3(1, 0, 0)))

	# 14. A LOD that cannot load must not cull LOD0.
	var broken := FileAccess.open(tmp.path_join("models/SM_Bad_LOD1.gltf"), FileAccess.WRITE)
	broken.store_string("not a gltf")
	broken.close()
	imp.active_scene_root = holder
	imp.active_models_folder = tmp.path_join("models")
	var lod0 := load("res://models/SM_Rock.gltf").instantiate() as Node3D
	holder.add_child(lod0)
	var lod_lib := {"SM_Bad": {"lods": [{"lod_index": 0}, {"lod_index": 1, "switch_distance_m": 12.0}]}}
	var wired: int = imp._apply_lod_chain(lod0, lod0, Transform3D.IDENTITY, "SM_Bad", "SM_Bad", [], lod_lib, {})
	var lod0_mi := find_node(lod0, "TriNode") as MeshInstance3D
	check("a broken LOD1 leaves LOD0 visible at any distance",
		wired == 0 and lod0_mi.visibility_range_end == 0.0, str(lod0_mi.visibility_range_end))
	holder.queue_free()


func _t3(x := 0.0, s := 1.0) -> Dictionary:
	return {"translation": [x, 0, 0], "rotation_quat": [0, 0, 0, 1], "scale": [s, s, s]}


func _comp3(n: String, key: String, x := 0.0, s := 1.0) -> Dictionary:
	return {"name": n, "mesh_key": key, "mesh_name": key, "godot_world_transform": _t3(x, s), "material_overrides": []}


func _actor3(n: String, id: String, comps: Array, x := 0.0) -> Dictionary:
	return {"name": n, "actor_id": id, "class": "BP_Sweep3_C" if comps.size() > 1 else "StaticMeshActor",
		"godot_transform": _t3(x), "components": comps}


func _import3(actors, root: Node, opts := {}, meshes := {}, extra := {}, models := "res://models/") -> bool:
	var layout := {"format_version": 2, "actors": actors, "meshes": meshes}
	layout.merge(extra)
	var f := FileAccess.open("res://sweep3.json", FileAccess.WRITE)
	f.store_string(JSON.stringify(layout))
	f.close()
	return await Importer.new().do_import("res://sweep3.json", models, "res://textures/", root, opts)


func _root3() -> Node3D:
	var r := Node3D.new()
	r.name = "Sweep3Root"
	get_tree().get_root().add_child(r, true)
	return r


func _test_sweep3() -> void:
	var update := {"update_existing": true}
	var prune := {"update_existing": true, "prune_removed": true}
	var cube := {"collision": {"boxes": [{"size": [100, 100, 100], "godot_local_transform": _t3()}]}}
	var lib := {"SM_Rock": cube, "SM_Crate": cube}
	var holder3 := _root3()

	# 5. Level Instance copies share actor guids.
	var r := _root3()
	await _import3([_actor3("A", "DUP", [_comp3("c", "SM_Rock", 1.0)], 1), _actor3("B", "DUP", [_comp3("c", "SM_Rock", 50.0)], 50)], r)
	await _import3([_actor3("A", "DUP", [_comp3("c", "SM_Rock", 2.0)], 2), _actor3("B", "DUP", [_comp3("c", "SM_Rock", 52.0)], 52)], r, prune)
	var box: Node = r.get_node("UnrealStaticMeshes")
	check("actors sharing an id are each updated", box.get_child_count() == 2
		and near(box.get_node("A").position, Vector3(2, 0, 0)) and near(box.get_node("B").position, Vector3(52, 0, 0)))
	await _import3([_actor3("A", "DUP", [_comp3("c", "SM_Rock", 2.0)], 2)], r, prune)
	check("a copy that could be the user's is not pruned", box.get_child_count() == 2)
	await _import3([_actor3("Z", "OTHER", [_comp3("c", "SM_Rock", 2.0)], 2)], r, prune)
	await get_tree().process_frame
	check("actors gone from the level are pruned", box.get_child_count() == 1, str(node_names(box)))
	r.queue_free()

	# 15. Duplicate labels stay readable.
	r = _root3()
	await _import3([_actor3("Same", "1", [_comp3("c", "SM_Rock")]), _actor3("Same", "2", [_comp3("c", "SM_Rock")])], r, {}, lib)
	check("duplicate labels get readable names", not node_names(r).any(func(n): return "@" in n), str(node_names(r)))
	r.queue_free()

	# 4. Structural changes arrive, and the user's work stays.
	var script := GDScript.new()
	script.source_code = "@tool\\nextends Node3D\\n@export var hp := 1\\nsignal hurt\\nfunc on_hurt() -> void:\\n\\tpass\\n"
	script.reload()
	r = _root3()
	var rock := _comp3("c", "SM_Rock", 3.0)
	await _import3([_actor3("A", "X", [rock], 3)], r, {}, lib)
	var node: Node3D = r.get_node("UnrealStaticMeshes/A")
	node.name = "Renamed"
	node.set_script(script)
	node.set("hp", 7)
	node.add_to_group("enemies", true)
	var mine := Marker3D.new()
	mine.name = "Mine"
	node.add_child(mine)
	mine.owner = r
	mine.position = Vector3(0, 4, 0)
	var deep := Marker3D.new()
	deep.name = "Deep"
	node.get_node("SM_Rock").add_child(deep)
	deep.owner = r
	node.connect("hurt", Callable(node, "on_hurt"), CONNECT_PERSIST)
	var unchanged: Node = node.get_node("SM_Rock")
	await _import3([_actor3("A", "X", [_comp3("c", "SM_Rock", 5.0)], 5)], r, update, lib)
	check("a move alone rebuilds nothing", node.get_node_or_null("SM_Rock") == unchanged and near(node.position, Vector3(5, 0, 0)))

	await _import3([_actor3("A", "X", [_comp3("c", "SM_Crate", 5.0)], 5)], r, update, lib)
	check("a swapped mesh arrives in the same node", is_instance_valid(node)
		and node.has_node("SM_Crate") and not node.has_node("SM_Rock") and node.name == &"Renamed")
	check("the swap keeps the script, its values, groups and child nodes",
		node.get_script() == script and node.get("hp") == 7 and node.is_in_group("enemies")
		and mine.get_parent() == node and near(mine.position, Vector3(0, 4, 0)) and is_instance_valid(deep))
	check("the swap has one collision shape", node.find_children("*", "CollisionShape3D", true, false).size() == 1)
	check("nothing is left unowned", node.find_children("*", "", true, true).all(func(n): return n.owner != null) and mine.owner == r and deep.owner == r)

	await _import3([_actor3("A", "X", [_comp3("c", "SM_Crate", 5.0), _comp3("d", "SM_Rock", 8.0)], 5)], r, update, lib)
	box = r.get_node("UnrealStaticMeshes")
	var grown: Node3D = box.get_node_or_null("Renamed")
	check("a second component arrives", box.get_child_count() == 1 and grown != null
		and grown.has_node("c") and grown.has_node("d"), str(node_names(box)))
	check("a root of another type keeps the script, values, groups and connections",
		grown.get_script() == script and grown.get("hp") == 7 and grown.is_in_group("enemies")
		and grown.is_connected("hurt", Callable(grown, "on_hurt")))
	check("the user's child stays where it was in the world", mine.get_parent() == grown
		and near(grown.transform * mine.position, Vector3(5, 4, 0)), str(grown.transform * mine.position))
	await _import3([_actor3("A", "X", [_comp3("c", "SM_Crate", 5.0)], 5)], r, update, lib)
	var shrunk: Node3D = box.get_node_or_null("Renamed")
	check("a removed component goes", shrunk is StaticBody3D and not shrunk.has_node("d") and mine.get_parent() == shrunk)
	await _import3([_actor3("A", "X", [_comp3("c", "SM_NotHere", 5.0)], 5)], r, update, lib)
	check("a mesh with no model file does not replace one on screen", box.get_node_or_null("Renamed") == shrunk and shrunk.has_node("SM_Crate"))
	var no_body := _comp3("c", "SM_Crate", 5.0)
	no_body["collision_enabled"] = false
	await _import3([_actor3("A", "X", [no_body], 5)], r, update, lib)
	check("switching collision off removes it", r.find_children("*", "CollisionShape3D", true, false).is_empty()
		and mine.get_parent() == box.get_node("Renamed"))
	var saved := PackedScene.new()
	saved.pack(r)
	var again := saved.instantiate()
	check("a rebuilt actor survives saving", node_names(again) == node_names(r), "%s vs %s" % [node_names(again), node_names(r)])
	again.free()
	r.queue_free()

	# 4. A scene imported before structures were recorded.
	r = _root3()
	await _import3([_actor3("A", "X", [rock], 3)], r, {}, lib)
	node = r.get_node("UnrealStaticMeshes/A")
	for key in ["unreal_structure", "unreal_nodes"]:
		node.remove_meta(key)
	mine = Marker3D.new()
	node.add_child(mine)
	mine.owner = r
	await _import3([_actor3("A", "X", [_comp3("c", "SM_Crate", 3.0)], 3)], r, update, lib)
	check("an older scene is rebuilt too", node.has_node("SM_Crate") and not node.has_node("SM_Rock") and mine.get_parent() == node)
	r.queue_free()

	# 4. A prefab on disk follows its Blueprint.
	var prefabs := {"update_existing": true, "extract_prefabs": true}
	DirAccess.remove_absolute("res://prefabs/BP_Sweep3_C.tscn")
	var two := [_comp3("c", "SM_Rock", 1.0), _comp3("d", "SM_Crate", 2.0)]
	r = _root3()
	await _import3([_actor3("P", "P1", two, 1), _actor3("Q", "P2", two, 1)], r, prefabs, lib)
	var stale := (load("res://prefabs/BP_Sweep3_C.tscn") as PackedScene).instantiate(PackedScene.GEN_EDIT_STATE_MAIN)
	mine = Marker3D.new()
	mine.name = "Mine"
	stale.add_child(mine)
	mine.owner = stale
	saved = PackedScene.new()
	saved.pack(stale)
	ResourceSaver.save(saved, "res://prefabs/BP_Sweep3_C.tscn")
	saved.take_over_path("res://prefabs/BP_Sweep3_C.tscn")
	stale.free()
	var swapped := [_comp3("c", "SM_Rock", 1.0), _comp3("d", "SM_Rock", 2.0)]
	await _import3([_actor3("P", "P1", swapped, 1), _actor3("Q", "P2", two, 1)], r, prefabs, lib)
	var p: Node = r.get_node("UnrealStaticMeshes/P")
	var on_disk := (load("res://prefabs/BP_Sweep3_C.tscn") as PackedScene).instantiate()
	check("one changed instance leaves the shared prefab alone", p.scene_file_path == "" and p.has_node("d/SM_Rock")
		and on_disk.has_node("d/SM_Crate"), str(node_names(p)))
	on_disk.free()
	await _import3([_actor3("P", "P1", swapped, 1), _actor3("Q", "P2", swapped, 1), _actor3("N", "P3", swapped, 1)], r, prefabs, lib)
	p = r.get_node("UnrealStaticMeshes/N")
	check("a changed Blueprint rebuilds its prefab around the user's nodes", p.scene_file_path == "res://prefabs/BP_Sweep3_C.tscn"
		and p.has_node("d/SM_Rock") and not p.has_node("d/SM_Crate") and p.has_node("Mine"), str(node_names(p)))
	var q: Node = r.get_node("UnrealStaticMeshes/Q")
	check("an existing instance follows", q.scene_file_path == "res://prefabs/BP_Sweep3_C.tscn" and q.has_node("d/SM_Rock")
		and not q.has_node("d/SM_Crate") and q.has_node("Mine"), str(node_names(q)))
	on_disk = (load("res://prefabs/BP_Sweep3_C.tscn") as PackedScene).instantiate()
	check("and so does the file", on_disk.has_node("d/SM_Rock") and not on_disk.has_node("d/SM_Crate") and on_disk.has_node("Mine"))
	on_disk.free()
	r.queue_free()
	DirAccess.remove_absolute("res://prefabs/BP_Sweep3_C.tscn")

	# 10. A model that will not load leaves a reminder, not a hole.
	DirAccess.make_dir_recursive_absolute("res://models_bad")
	var bad := FileAccess.open("res://models_bad/SM_Bad.gltf", FileAccess.WRITE)
	bad.store_string("not a gltf")
	bad.close()
	r = _root3()
	await _import3([_actor3("One", "1", [_comp3("c", "SM_Bad")]), _actor3("Two", "2", [_comp3("c", "SM_Bad"), _comp3("d", "SM_Bad")])],
		r, {}, {"SM_Bad": cube}, {}, "res://models_bad/")
	var markers := r.find_children("*", "Marker3D", true, false)
	check("an unreadable model leaves placeholders", markers.size() == 3
		and r.find_children("*", "StaticBody3D", true, false).is_empty(), str(node_names(r)))
	check("the report names an unreadable model", FileAccess.get_file_as_string("res://ue2g_import_report.txt").contains("SM_Bad"))
	DirAccess.remove_absolute("res://models_bad/SM_Bad.gltf")
	DirAccess.remove_absolute("res://models_bad")
	r.queue_free()

	# 7. Unreal's LOD switch is a screen size, so it moves out with scale.
	r = _root3()
	var lods := {"SM_Crate": {"lods": [{"lod_index": 0}, {"lod_index": 1, "export_name": "SM_Crate_LOD1", "switch_distance_m": 10.0}]}}
	await _import3([_actor3("S1", "1", [_comp3("c", "SM_Crate")]), _actor3("S4", "2", [_comp3("c", "SM_Crate", 0.0, 4.0)])], r, {}, lods)
	var ends := []
	for n in ["S1", "S4"]:
		ends.append(r.get_node("UnrealStaticMeshes/" + n).find_children("*", "MeshInstance3D", true, false)[0].visibility_range_end)
	check("LOD distances follow the actor's scale", is_equal_approx(ends[0], 10.0) and is_equal_approx(ends[1], 40.0), str(ends))
	r.queue_free()

	# 3. Culled foliage is split, and no instance within the cull distance is hidden.
	r = _root3()
	var floats := []
	for i in 400:
		floats.append_array([1, 0, 0, 0, 1, 0, 0, 0, 1, i, 0, 0])
	await _import3([], r, {}, {}, {"foliage": [{"name": "Grass", "mesh_key": "SM_Rock", "mesh_name": "SM_Rock",
		"instance_count": 400, "godot_transforms": floats, "cull_end_m": 50.0}]})
	var chunks := r.find_children("*", "MultiMeshInstance3D", true, false)
	var total := 0
	var tight := true
	for mmi in chunks:
		total += mmi.multimesh.instance_count
		tight = tight and mmi.visibility_range_end >= 50.0 + (mmi.multimesh.instance_count - 1) * 0.5 and mmi.visibility_range_end < 75.0
	check("culled foliage is split into chunks of every instance", chunks.size() == 16 and total == 400 and tight, "%d chunks, %d instances" % [chunks.size(), total])
	r.queue_free()

	# 13. JSON that is not an object fails cleanly.
	var arr := FileAccess.open("res://sweep3_array.json", FileAccess.WRITE)
	arr.store_string("[1, 2]")
	arr.close()
	check("a JSON array is refused", not await Importer.new().do_import("res://sweep3_array.json", "res://models/", "res://textures/", holder3, {}))

	# 11. A non-square Terrain3D heightmap keeps its proportions.
	var Terrain = load("res://addons/unreal_importer/import_terrain.gd")
	var shape: Vector2i = Terrain.new()._to_terrain3d_image(Image.create(2017, 1009, false, Image.FORMAT_RF), Vector3.ZERO, Vector3(100800, 50400, 100)).get_size()
	check("a capped heightmap keeps square texels", shape == Vector2i(513, 1025), str(shape))

	# 6. Every WAV format gets a loop end (QOA is Godot's import default).
	var wav := AudioStreamWAV.new()
	wav.mix_rate = 22050
	wav.format = AudioStreamWAV.FORMAT_16_BITS
	var pcm := PackedByteArray()
	pcm.resize(22050 * 2)
	wav.data = pcm
	DirAccess.make_dir_recursive_absolute("res://audio")
	wav.save_to_wav("res://audio/Sweep3Hum.wav")
	var loaded: AudioStreamWAV = AudioStreamWAV.load_from_file("res://audio/Sweep3Hum.wav", {"compress/mode": 2})
	check("fixture WAV is QOA with no loop end", loaded.format == AudioStreamWAV.FORMAT_QOA and loaded.loop_end <= 0)
	ResourceSaver.save(loaded, "res://audio/Sweep3Hum.res")
	var sound_root := _root3()
	var by_path := ResourceLoader.load("res://audio/Sweep3Hum.res") as AudioStreamWAV
	by_path.take_over_path("res://audio/Sweep3Hum.wav")
	EnvImporter.new()._apply_audio([{"name": "Hum", "sound_file": "Sweep3Hum.wav", "looping": true}],
		sound_root, sound_root, {"json_dir": "res://"})
	var players := sound_root.find_children("Hum", "", true, false)
	check("a looping sound has a loop end", players.size() == 1 and players[0].stream != null
		and absi(players[0].stream.loop_end - 22050) < 600 and players[0].stream.loop_mode == AudioStreamWAV.LOOP_FORWARD,
		str(players[0].stream.loop_end if players.size() == 1 and players[0].stream else -1))
	sound_root.queue_free()

	# 14. Textures outside the project decode by content and get mipmaps.
	var dir := OS.get_cache_dir().path_join("ue2g_sweep3")
	DirAccess.make_dir_recursive_absolute(dir)
	var img := Image.create(16, 16, false, Image.FORMAT_RGBA8)
	img.fill(Color.RED)
	img.save_jpg(dir.path_join("mislabeled.png"))
	var tex: Texture2D = Common.load_texture_file(dir.path_join("mislabeled.png"))
	check("a mislabeled external texture loads with mipmaps", tex != null and tex.get_image().has_mipmaps())

	# 2. Only the layout's own textures are shrunk.
	var Limit = load("res://addons/unreal_importer/texture_import_limit.gd")
	for n in ["T_Layout", "my_photo"]:
		img.save_png(dir.path_join(n + ".png"))
	var lf := FileAccess.open("res://sweep3.json", FileAccess.WRITE)
	lf.store_string(JSON.stringify({"meshes": {"M": {"materials": [{"parameters": {"albedo_texture": "T_Layout"}}]}},
		"decals": [{"textures": {"albedo": "T_Decal", "uv_rect": [0, 0, 1, 1]}}], "texture_metadata": {"T_Meta": {}},
		"landscapes": [{"material": {"textures": [{"texture": "T_Land"}]}}],
		"foliage": [{"material_overrides": [{"parameters": {"shader": {"tex_0": "T_Shader", "texture_paths": {"tex_0": "/Game/T"}}}}]}]}))
	lf.close()
	var names: Dictionary = Limit.layout_texture_names("res://sweep3.json")
	check("every texture the layout names is found", names.keys().all(func(k): return k in ["t_layout", "t_decal", "t_meta", "t_land", "t_shader"]) and names.size() == 5, str(names.keys()))
	Limit.shrink_source_files(dir, 8, names)
	check("shrinking leaves the user's own art alone", Image.load_from_file(dir.path_join("T_Layout.png")).get_width() == 8
		and Image.load_from_file(dir.path_join("my_photo.png")).get_width() == 16)

	# 20. A saved material follows Unreal, and another material never borrows its file.
	DirAccess.make_dir_recursive_absolute("res://materials3")
	for f in DirAccess.get_files_at("res://materials3"):
		DirAccess.remove_absolute("res://materials3/" + f)
	var make := func(path: String, rough: float) -> Material:
		var imp = Importer.new()
		imp.import_options = {"save_external_materials": true, "materials_folder": "res://materials3/"}
		return imp.get_or_create_material("M_S3", path, {"roughness": rough})
	var in_scene: StandardMaterial3D = make.call("/Game/A/M_S3", 0.25)
	make.call("/Game/A/M_S3", 0.75)
	check("a changed material updates what the scene already uses", is_equal_approx(in_scene.roughness, 0.75)
		and in_scene.resource_path == "res://materials3/M_S3.tres")
	check("and its file", FileAccess.get_file_as_string("res://materials3/M_S3.tres").contains("roughness = 0.75"))
	var tweaked: StandardMaterial3D = load("res://materials3/M_S3.tres")
	tweaked.metallic = 0.5
	ResourceSaver.save(tweaked, "res://materials3/M_S3.tres")
	check("an unchanged one keeps the user's edits", is_equal_approx(make.call("/Game/A/M_S3", 0.75).metallic, 0.5))
	check("a same-named material gets its own file", make.call("/Game/B/M_S3", 0.1).resource_path != "res://materials3/M_S3.tres"
		and is_equal_approx(make.call("/Game/A/M_S3", 0.75).roughness, 0.75))
	holder3.queue_free()


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
