"""Adds a temporary EditorPlugin that imports a REAL exported layout (built by
build_real_project.py) and dumps a generic diagnostic report -- not tied to any
fixture's node names, since real actor/mesh names vary per level.

Reports, per imported mesh instance: whether each material slot has a real
albedo/normal/roughness/metallic/AO texture bound, the resolved channel indices
for packed maps, and flags common failure signatures (doubled transforms,
MISSING_ placeholders, textures that failed to resolve).
"""
import os

HARNESS = os.path.join(os.path.dirname(os.path.abspath(__file__)), "_project_real")
PLUGIN_DIR = os.path.join(HARNESS, "addons", "real_inspector")
os.makedirs(PLUGIN_DIR, exist_ok=True)

with open(os.path.join(PLUGIN_DIR, "plugin.cfg"), "w", encoding="utf-8") as f:
    f.write('[plugin]\n\nname="RealInspector"\ndescription="real data diagnostic"\n'
            'author="harness"\nversion="1.0"\nscript="real_inspector.gd"\n')

RUNNER = r'''@tool
extends EditorPlugin

const Importer = preload("res://addons/unreal_importer/import_unreal_layout.gd")

var lines: Array[String] = []

func _enter_tree() -> void:
	call_deferred("_run")

func log_line(s: String) -> void:
	print(s)
	lines.append(s)

func _wait_for_import() -> void:
	"""The editor imports assets on a background thread while the first frames
	run. Without this wait, load() on an as-yet-unimported .gltf returns null and
	every mesh silently vanishes -- the report then blames the importer for what
	is really a race against the asset scan."""
	var fs := EditorInterface.get_resource_filesystem()
	var waited := 0
	while fs.is_scanning():
		await get_tree().process_frame
		waited += 1
		if waited > 120000:
			log_line("WARNING: filesystem still scanning after 120k frames; continuing anyway")
			break
	# Scanning finished, but reimports are queued separately; settle a few frames.
	for _i in range(30):
		await get_tree().process_frame
	log_line("asset scan finished after %d frames" % waited)

func _run() -> void:
	log_line("=========== REAL DATA IMPORT REPORT ===========")
	await _wait_for_import()

	# Prove the .gltf resources are actually importable before drawing any
	# conclusions from the scene contents.
	var probe_ok := 0
	var probe_fail := 0
	var probe_fail_names: Array[String] = []
	for f in DirAccess.get_files_at("res://models"):
		if not f.ends_with(".gltf"):
			continue
		if ResourceLoader.exists("res://models/" + f) and load("res://models/" + f) != null:
			probe_ok += 1
		else:
			probe_fail += 1
			if probe_fail_names.size() < 5:
				probe_fail_names.append(f)
	log_line("glTF resources loadable: %d ok, %d FAILED %s"
		% [probe_ok, probe_fail, str(probe_fail_names)])

	var raw := FileAccess.get_file_as_string("res://level_layout.json")
	var data = JSON.parse_string(raw)
	var n_actors: int = (data.get("actors", []) as Array).size() if data else 0
	var n_meshes: int = (data.get("meshes", {}) as Dictionary).size() if data else 0
	log_line("layout JSON: %d actors, %d mesh entries" % [n_actors, n_meshes])

	# How many mesh entries actually carry texture parameters at all?
	var meshes_with_tex := 0
	var meshes_with_packed := 0
	for mesh_key in (data.get("meshes", {}) as Dictionary).keys():
		var mdata: Dictionary = data["meshes"][mesh_key]
		for mat in (mdata.get("materials", []) as Array):
			var p: Dictionary = mat.get("parameters", {})
			if p.get("albedo_texture") or p.get("packed_texture"):
				meshes_with_tex += 1
			if p.get("packed_texture"):
				meshes_with_packed += 1
			break
	log_line("mesh entries with any texture reference: %d, with a packed map: %d"
		% [meshes_with_tex, meshes_with_packed])

	var root := Node3D.new()
	root.name = "Root"
	get_tree().get_root().add_child(root)

	var importer = Importer.new()
	var t0 := Time.get_ticks_msec()
	var ok = importer.do_import("res://level_layout.json", "res://models/",
		"res://textures/", root, {"apply_metadata": false})
	log_line("do_import returned %s (%d ms)" % [ok, Time.get_ticks_msec() - t0])

	var mesh_instances: Array[MeshInstance3D] = []
	_collect(root, mesh_instances)
	log_line("MeshInstance3D count in resulting scene: %d" % mesh_instances.size())

	var placeholders := _count_placeholders(root, 0)

	var slots_total := 0
	var slots_with_albedo := 0
	var slots_with_normal := 0
	var slots_with_roughness := 0
	var slots_with_metallic := 0
	var slots_with_ao := 0
	var slots_shared_packed := 0
	var sample_reports: Array[String] = []

	for mi in mesh_instances:
		if mi.mesh == null:
			continue
		for slot in range(mi.mesh.get_surface_count()):
			var mat = mi.get_active_material(slot)
			if mat == null or not (mat is BaseMaterial3D):
				continue
			var bm := mat as BaseMaterial3D
			slots_total += 1
			if bm.albedo_texture != null:
				slots_with_albedo += 1
			if bm.normal_texture != null:
				slots_with_normal += 1
			if bm.roughness_texture != null:
				slots_with_roughness += 1
			if bm.metallic_texture != null:
				slots_with_metallic += 1
			if bm.ao_enabled and bm.ao_texture != null:
				slots_with_ao += 1
			if bm.roughness_texture != null and bm.roughness_texture == bm.metallic_texture:
				slots_shared_packed += 1
			if sample_reports.size() < 8:
				sample_reports.append(
					"    %s slot %d: albedo=%s normal=%s rough=%s(ch%d) metal=%s(ch%d) ao=%s(ch%d) albedo_color=%s roughness=%.2f metallic=%.2f"
					% [root.get_path_to(mi), slot,
					   bm.albedo_texture != null, bm.normal_texture != null,
					   bm.roughness_texture != null, bm.roughness_texture_channel,
					   bm.metallic_texture != null, bm.metallic_texture_channel,
					   bm.ao_enabled and bm.ao_texture != null, bm.ao_texture_channel,
					   str(bm.albedo_color), bm.roughness, bm.metallic])

	log_line("\n--- material slot summary (%d slots) ---" % slots_total)
	log_line("  albedo texture bound:    %d / %d" % [slots_with_albedo, slots_total])
	log_line("  normal texture bound:    %d / %d" % [slots_with_normal, slots_total])
	log_line("  roughness texture bound: %d / %d" % [slots_with_roughness, slots_total])
	log_line("  metallic texture bound:  %d / %d" % [slots_with_metallic, slots_total])
	log_line("  AO texture bound:        %d / %d" % [slots_with_ao, slots_total])
	log_line("  roughness+metallic share one texture (packed map applied correctly): %d / %d"
		% [slots_shared_packed, slots_total])
	log_line("  MISSING_ placeholders created: %d" % placeholders)

	log_line("\n--- sample material slots ---")
	for s in sample_reports:
		log_line(s)

	_write_results()
	get_tree().quit(0)

func _collect(n: Node, out: Array[MeshInstance3D]) -> void:
	if n is MeshInstance3D:
		out.append(n)
	for c in n.get_children():
		_collect(c, out)

func _count_placeholders(n: Node, count: int) -> int:
	if n is Marker3D and n.name.contains("MISSING_"):
		count += 1
	for c in n.get_children():
		count = _count_placeholders(c, count)
	return count

func _write_results() -> void:
	var f := FileAccess.open("res://real_report.txt", FileAccess.WRITE)
	if f == null:
		printerr("could not write real_report.txt")
		return
	for line in lines:
		f.store_line(line)
	f.close()
'''

with open(os.path.join(PLUGIN_DIR, "real_inspector.gd"), "w", encoding="utf-8") as f:
    f.write(RUNNER)

with open(os.path.join(HARNESS, "project.godot"), "w", encoding="utf-8") as f:
    f.write('config_version=5\n\n[application]\n\nconfig/name="RealImportHarness"\n'
            'config/features=PackedStringArray("4.6")\n\n'
            # A real export is gigabytes of 4K RGBA PNGs. Importing them at full
            # size makes Godot's WebP packer exhaust memory and segfault
            # (modules/webp/webp_common.cpp:110 -> alloc_static: "mem" is null),
            # which kills the run before any material can be inspected. This
            # harness checks that textures are WIRED UP correctly, not how they
            # look, so cap the imported size and skip mipmap generation.
            '[importer_defaults]\n\n'
            'texture={\n'
            '"compress/mode": 0,\n'
            '"mipmaps/generate": false,\n'
            '"process/size_limit": 512\n'
            '}\n\n'
            '[editor_plugins]\n\nenabled=PackedStringArray("res://addons/real_inspector/plugin.cfg")\n')

print("real inspector written to", PLUGIN_DIR)
