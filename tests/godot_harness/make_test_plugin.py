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

func _enter_tree() -> void:
	call_deferred("_run_tests")

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

	print("\\n============================================")
	if failures.size() > 0:
		print("FAILURES (", failures.size(), "): ", failures)
		get_tree().quit(1)
	else:
		print("ALL GODOT IMPORT CHECKS PASSED")
		get_tree().quit(0)

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
