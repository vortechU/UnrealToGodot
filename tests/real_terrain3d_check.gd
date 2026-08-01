extends SceneTree

# Drives the production terrain importer against a REAL exported layout in a
# project that has the Terrain3D plugin installed, then verifies the three
# things the throwaway harness cannot (tests/godot_harness/_project ships no
# Terrain3D, so import_terrain falls back to the mesh path there):
#
#   1. the regions are written to disk, not just held in memory
#      -- import_images only populates memory; without an explicit
#         save_directory() the data folder stays EMPTY and the terrain comes
#         back flat on the next scene open;
#   2. the saved .tscn does NOT contain Terrain3D's runtime children
#      -- add_child() runs Terrain3D's _ready() synchronously, so a following
#         set_owner_recursive() stamps ownership onto Labels/MMI and bakes them
#         into the scene, and loading then reports "An incoming node's name
#         clashes with .../Labels already in the scene";
#   3. the terrain has real relief at the Unreal world position.
#
# Run:
#   godot --headless --path <project-with-terrain3d> --script res://<this>.gd
# Configure via environment variables:
#   UE2G_LAYOUT    res:// path of the *_layout.json  (required)
#   UE2G_DATA_DIR  Terrain3D data root (default res://terrain_data_check)

var fails: Array[String] = []


func chk(cond: bool, msg: String) -> void:
	print(("  PASS  " if cond else "  FAIL  ") + msg)
	if not cond:
		fails.append(msg)


func _initialize() -> void:
	var layout := OS.get_environment("UE2G_LAYOUT")
	if layout == "":
		printerr("UE2G_LAYOUT must name the exported *_layout.json (res:// path)")
		quit(2)
		return
	var data_dir := OS.get_environment("UE2G_DATA_DIR")
	if data_dir == "":
		data_dir = "res://terrain_data_check"

	if not ClassDB.class_exists("Terrain3D"):
		print("Terrain3D is not installed in this project -- nothing to check.")
		quit(0)
		return

	var f := FileAccess.open(layout, FileAccess.READ)
	if f == null:
		printerr("cannot open ", layout)
		quit(2)
		return
	var data: Dictionary = JSON.parse_string(f.get_as_text())
	f.close()
	var landscapes: Array = data.get("landscapes", [])
	if landscapes.is_empty():
		print("layout has no landscapes -- nothing to check.")
		quit(0)
		return
	var ls: Dictionary = landscapes[0]
	var ls_name := str(ls.get("name", "Landscape"))
	print("=== Terrain3D check: %s (%s) ===" % [ls_name, layout])

	# Start from an empty data directory so this proves OUR write, not a leftover.
	var region_dir := data_dir.path_join(ls_name)
	if DirAccess.dir_exists_absolute(region_dir):
		for old in DirAccess.get_files_at(region_dir):
			DirAccess.remove_absolute(region_dir.path_join(old))
	chk(DirAccess.get_files_at(region_dir).is_empty(), "data directory starts empty")

	var Terrain = load("res://addons/unreal_importer/import_terrain.gd")
	var root := Node3D.new()
	root.name = "TerrainCheck"
	get_root().add_child(root)
	var res: Dictionary = await Terrain.new().apply(data, root, root, {
		"build_terrain": true,
		"json_dir": layout.get_base_dir(),
		"terrain_mode": "terrain3d",
		"terrain3d_data_dir": data_dir,
	})
	for w in res.get("warnings", []):
		print("     warn: ", w)
	chk(int(res.get("created", 0)) > 0, "terrain was created")

	var t3d := root.get_node_or_null(ls_name + "_Terrain3D")
	chk(t3d != null, "Terrain3D node exists")
	if t3d == null:
		_finish()
		return

	# 1. persisted?
	var written := DirAccess.get_files_at(region_dir)
	chk(written.size() > 0, "regions written to disk (%d file(s) in %s)" % [written.size(), region_dir])

	# 2. does a saved scene stay free of Terrain3D's runtime children?
	var ps := PackedScene.new()
	chk(ps.pack(root) == OK, "scene packs")
	var tmp_scene := data_dir.path_join("_terrain3d_check.tscn")
	chk(ResourceSaver.save(ps, tmp_scene) == OK, "scene saves")
	var text := FileAccess.get_file_as_string(tmp_scene)
	var node_lines: Array[String] = []
	for line in text.split("\n"):
		if line.begins_with("[node "):
			node_lines.append(line)
	print("     nodes in the saved scene: %d" % node_lines.size())
	for line in node_lines:
		print("        ", line.strip_edges())
	chk(node_lines.size() == 2,
		"only the root and the Terrain3D node are serialized (got %d)" % node_lines.size())
	for internal in ["Labels", "MMI", "MouseViewport", "MouseCamera"]:
		chk(not text.contains('name="%s' % internal),
			"Terrain3D's runtime child '%s' is not baked into the scene" % internal)

	# 3. reload from disk and confirm real relief where Unreal put it.
	var t2 = ClassDB.instantiate("Terrain3D")
	var r2 := Node3D.new()
	get_root().add_child(r2)
	r2.add_child(t2)
	t2.set_vertex_spacing(t3d.get_vertex_spacing())
	t2.set_data_directory(region_dir)
	await process_frame
	var d = t2.get_data()
	chk(d != null and d.get_region_count() > 0,
		"reloading the saved directory yields regions (%d)" % (d.get_region_count() if d else -1))
	if d != null and d.get_region_count() > 0:
		var b: Dictionary = ls.get("ue_bounds", {})
		var ce: Array = b.get("center", [0, 0, 0])
		var ex: Array = b.get("extent", [0, 0, 0])
		var min_x := (float(ce[1]) - float(ex[1])) * 0.01
		var min_z := -(float(ce[0]) + float(ex[0])) * 0.01
		var span_x := float(ex[1]) * 2.0 * 0.01
		var span_z := float(ex[0]) * 2.0 * 0.01
		var lo := INF
		var hi := -INF
		var nans := 0
		for iz in 16:
			for ix in 16:
				var hh: float = d.get_height(Vector3(
					min_x + span_x * ix / 15.0, 0.0, min_z + span_z * iz / 15.0))
				if is_nan(hh):
					nans += 1
				else:
					lo = minf(lo, hh)
					hi = maxf(hi, hh)
		chk(nans == 0, "every sample inside the Unreal footprint has terrain (%d NaN)" % nans)
		var expected: Array = ls.get("height_range_m", [0.0, 0.0])
		var expected_span: float = float(expected[1]) - float(expected[0])
		print("     relief %.1f .. %.1f m (exported range %.1f .. %.1f)"
			% [lo, hi, float(expected[0]), float(expected[1])])
		chk(hi - lo > expected_span * 0.5,
			"terrain has real relief, not a flat plane (%.1f m of %.1f m)" % [hi - lo, expected_span])

	DirAccess.remove_absolute(tmp_scene)
	_finish()


func _finish() -> void:
	print("")
	if fails.is_empty():
		print("ALL TERRAIN3D CHECKS PASSED")
		quit(0)
	else:
		print("FAILED %d check(s):" % fails.size())
		for m in fails:
			print("  - " + m)
		quit(1)
