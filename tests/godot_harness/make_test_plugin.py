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
	await _test_lods_and_skeletal_physics(root)
	_test_phase3_scene_parity(root)
	await _test_phase4_workflow(root)
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
	await Importer.new().do_import("res://level_layout.json", "res://models/", "res://textures/", pref_root, pref_opts)
	var bp_inst := find_node(pref_root, "BP_Pier")
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
