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
	var ok = importer.do_import("res://level_layout.json", "res://models/",
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

	_test_scaled_rotated_body(root)
	_test_mislabeled_heightmap(root)
	_test_materials(root)

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

func _test_scaled_rotated_body(root: Node) -> void:
	print("\\n--- rotated + non-uniformly scaled actor ---")
	var tunnel := find_node(root, "Tunnel_1")
	check("Tunnel_1 exists", tunnel != null)
	if tunnel == null or not (tunnel is StaticBody3D):
		check("Tunnel_1 is a StaticBody3D", tunnel is StaticBody3D,
			tunnel.get_class() if tunnel else "missing")
		return
	var body := tunnel as StaticBody3D

	# Jolt refuses non-uniform scale on bodies: the body node must be unscaled.
	check("body basis carries no scale (Jolt-safe)",
		body.global_transform.basis.get_scale().is_equal_approx(Vector3.ONE),
		str(body.global_transform.basis.get_scale()))

	var mesh_child: Node3D = null
	var shape_child: CollisionShape3D = null
	for c in body.get_children():
		if c is CollisionShape3D:
			shape_child = c
		elif c is Node3D:
			mesh_child = c

	check("mesh child carries the scale instead",
		mesh_child != null and mesh_child.transform.basis.get_scale().is_equal_approx(Vector3(0.875, 1.125, 1.0)),
		str(mesh_child.transform.basis.get_scale()) if mesh_child else "no mesh child")

	if mesh_child:
		# FTransform applies scale BEFORE rotation: under a +90deg yaw the local X
		# axis lands on world -Z and must keep ITS scale, 0.875. The old global-
		# scale composition (S*R) left world -Z at 1.0 and squashed world X instead.
		var wx: Vector3 = mesh_child.global_transform.basis.x
		check("scale composes before rotation (local X -> world -Z at 0.875)",
			near(wx, Vector3(0, 0, -0.875)), str(wx))

	check("collision shape present", shape_child != null)
	if shape_child:
		check("box shape absorbed the scale: (0.875, 1.125, 1.0) m",
			shape_child.shape is BoxShape3D
				and (shape_child.shape as BoxShape3D).size.is_equal_approx(Vector3(0.875, 1.125, 1.0)),
			str((shape_child.shape as BoxShape3D).size) if shape_child.shape is BoxShape3D else str(shape_child.shape))
		check("shape node basis is orthonormal",
			shape_child.transform.basis.get_scale().is_equal_approx(Vector3.ONE),
			str(shape_child.transform.basis.get_scale()))


func _test_mislabeled_heightmap(root: Node) -> void:
	print("\\n--- landscape with PNG bytes under an .exr name ---")
	var terrain := find_node(root, "FakeLandscape")
	check("FakeLandscape terrain node exists (mislabeled .exr loaded by content)", terrain != null)
	if terrain == null:
		return
	check("terrain mesh built", find_node(terrain, "TerrainMesh") != null)
	check("terrain collision built", find_node(terrain, "TerrainCollision") != null)
	var mesh_node := find_node(terrain, "TerrainMesh")
	if mesh_node and mesh_node is MeshInstance3D:
		var aabb := (mesh_node as MeshInstance3D).mesh.get_aabb()
		# ue_bounds: 40x40 m footprint, 10 m height span, gradient heightmap.
		check("terrain spans the ue_bounds footprint (40 m)",
			absf(aabb.size.x - 40.0) < 1.0 and absf(aabb.size.z - 40.0) < 1.0, str(aabb))
		check("terrain has vertical relief from the gradient",
			aabb.size.y > 5.0, str(aabb.size.y))


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
# Jolt is selected to match real projects: it is the engine that REJECTS scaled
# bodies, so a regression there prints its errors in this harness run too.
with open(os.path.join(HARNESS, "project.godot"), "w", encoding="utf-8") as f:
    f.write('config_version=5\n\n[application]\n\nconfig/name="ImporterHarness"\n'
            'config/features=PackedStringArray("4.6")\n\n'
            '[editor_plugins]\n\nenabled=PackedStringArray("res://addons/test_runner/plugin.cfg")\n\n'
            '[physics]\n\n3d/physics_engine="Jolt Physics"\n')

print("test plugin written to", PLUGIN_DIR)
