@tool
extends RefCounted

## Writes a self-contained report of what an import actually produced.
##
## The console scrolls, gets truncated, and loses the errors that matter among
## the ones that don't -- so a screenshot of it rarely explains a failed import.
## This writes one file instead: res://ue2g_import_report.txt, describing what
## resolved, what did not, and what the resulting materials look like. That file
## is enough to diagnose an import without the person who ran it having to
## explain what they saw.
##
## Nothing here may break an import: every entry point is guarded, and a failure
## to write the report is reported but never raised.

const REPORT_PATH := "res://ue2g_import_report.txt"

var _lines: PackedStringArray = []
var _errors: PackedStringArray = []
var _warnings: PackedStringArray = []


func line(text: String) -> void:
	_lines.append(text)


func fact(key: String, value) -> void:
	_lines.append("  %-36s %s" % [key + ":", str(value)])


func error(text: String) -> void:
	_errors.append(text)
	_lines.append("  ERROR   " + text)


func warn(text: String) -> void:
	_warnings.append(text)
	_lines.append("  WARNING " + text)


func ok(text: String) -> void:
	_lines.append("  ok      " + text)


func section(title: String) -> void:
	_lines.append("")
	_lines.append("--- %s ---" % title)


func header(json_path: String, models_folder: String, textures_folder: String) -> void:
	_lines.append("=" .repeat(72))
	_lines.append("UNREAL -> GODOT IMPORT REPORT")
	_lines.append("=".repeat(72))
	section("Environment")
	fact("generated", Time.get_datetime_string_from_system())
	fact("godot", Engine.get_version_info().get("string", "?"))
	fact("layout json", json_path)
	fact("models folder", models_folder)
	fact("textures folder", textures_folder)


func audit_scene(root: Node) -> void:
	## Walks the imported scene and reports what the materials actually got.
	## Counts are the point: "388/389 have albedo" localises a problem far
	## faster than any single node's inspector does.
	section("Result")
	if root == null:
		error("no scene root -- nothing was imported.")
		return

	var meshes: Array[MeshInstance3D] = []
	_collect(root, meshes)
	var placeholders := _count_placeholders(root)
	fact("MeshInstance3D nodes", meshes.size())
	fact("MISSING_ placeholders", placeholders)

	if meshes.is_empty():
		error("the scene contains no meshes. If the layout had actors, the "
			+ "models folder is wrong, or Godot had not finished importing "
			+ "the .gltf files when the import ran.")
		return
	if placeholders > 0:
		error("%d mesh(es) were not found on disk and became placeholders. The "
			% placeholders + "mesh export and the layout export are out of "
			+ "sync -- re-run the mesh export.")

	var slots := 0
	var albedo := 0
	var normal := 0
	var rough := 0
	var metal := 0
	var ao := 0
	var packed := 0
	var out_of_range := 0
	var out_of_range_example := ""

	for mi in meshes:
		if mi.mesh == null:
			continue
		for slot in range(mi.mesh.get_surface_count()):
			var mat = mi.get_active_material(slot)
			if mat == null or not (mat is BaseMaterial3D):
				continue
			var bm := mat as BaseMaterial3D
			slots += 1
			if bm.albedo_texture != null:
				albedo += 1
			if bm.normal_texture != null:
				normal += 1
			if bm.roughness_texture != null:
				rough += 1
			if bm.metallic_texture != null:
				metal += 1
			if bm.ao_enabled and bm.ao_texture != null:
				ao += 1
			# A packed RMA map is one texture serving roughness and metallic on
			# different channels. Sharing the resource is what proves it landed.
			if bm.roughness_texture != null and bm.roughness_texture == bm.metallic_texture:
				packed += 1
			if bm.roughness < 0.0 or bm.roughness > 1.0:
				out_of_range += 1
				if out_of_range_example == "":
					out_of_range_example = "%s roughness=%.2f" % [root.get_path_to(mi), bm.roughness]

	section("Material slots (%d total)" % slots)
	_ratio("albedo texture", albedo, slots)
	_ratio("normal texture", normal, slots)
	_ratio("roughness texture", rough, slots)
	_ratio("metallic texture", metal, slots)
	_ratio("AO texture", ao, slots)
	_ratio("packed map shared rough+metal", packed, slots)

	if slots > 0 and albedo == 0:
		error("not one material slot got an albedo texture. If the layout JSON's "
			+ "material parameters say \"albedo_texture\": null, the exporter "
			+ "could not classify the material's base-color parameter -- update "
			+ "the Unreal-side exporter and re-export. Otherwise the textures "
			+ "folder is wrong, or the referenced files are not on disk.")
	if out_of_range > 0:
		error("%d slot(s) have roughness outside 0..1 (e.g. %s). Godot "
			% [out_of_range, out_of_range_example]
			+ "multiplies this with the roughness texture, so those surfaces "
			+ "render fully flat.")


func _ratio(label: String, count: int, total: int) -> void:
	var pct := 0.0 if total == 0 else (float(count) / float(total)) * 100.0
	_lines.append("  %-32s %4d / %-4d (%5.1f%%)" % [label, count, total, pct])


func note_missing_texture(texture_name: String) -> void:
	warn("texture not found on disk: " + texture_name)


func note_missing_meshes(missing: Dictionary, models_folder: String) -> void:
	if missing.is_empty():
		return
	section("Missing meshes")
	fact("looked in", models_folder)
	var shown := 0
	for key in missing.keys():
		if shown >= 20:
			line("  ... and %d more" % (missing.size() - shown))
			break
		line("  - " + str(key))
		shown += 1


func write() -> void:
	_lines.append("")
	_lines.append("=".repeat(72))
	_lines.append("VERDICT: %d error(s), %d warning(s)" % [_errors.size(), _warnings.size()])
	_lines.append("=".repeat(72))
	if not _errors.is_empty():
		_lines.append("")
		_lines.append("ERRORS:")
		for e in _errors:
			_lines.append("  * " + e)
	if not _warnings.is_empty():
		_lines.append("")
		_lines.append("WARNINGS:")
		var shown := 0
		for w in _warnings:
			if shown >= 30:
				_lines.append("  ... and %d more" % (_warnings.size() - shown))
				break
			_lines.append("  * " + w)
			shown += 1
	if _errors.is_empty() and _warnings.is_empty():
		_lines.append("")
		_lines.append("No problems found in this import.")

	var f := FileAccess.open(REPORT_PATH, FileAccess.WRITE)
	if f == null:
		printerr("Unreal Importer: could not write ", REPORT_PATH,
			" (", error_string(FileAccess.get_open_error()), ")")
		return
	for l in _lines:
		f.store_line(l)
	f.close()
	print("Unreal Importer: wrote ", REPORT_PATH, " -- send this file to describe the import.")


func _collect(n: Node, out: Array[MeshInstance3D]) -> void:
	if n is MeshInstance3D:
		out.append(n)
	for c in n.get_children():
		_collect(c, out)


func _count_placeholders(n: Node) -> int:
	var count := 0
	if n is Marker3D and n.name.contains("MISSING_"):
		count += 1
	for c in n.get_children():
		count += _count_placeholders(c)
	return count
