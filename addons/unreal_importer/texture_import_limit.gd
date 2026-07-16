@tool
extends RefCounted

## Caps the size Godot imports textures at.
##
## An Unreal export ships its source art: a real level is gigabytes of 4K PNGs.
## Godot imports those at full resolution, which is slow, costs a lot of VRAM,
## and can exhaust memory in the WebP packer badly enough to take the editor
## down partway through -- leaving a half-populated .godot cache that looks like
## a broken export rather than an out-of-memory crash.
##
## Unreal's own "Max Texture Resolution" setting cannot help here: it drives the
## cooked texture, while the PNG exporter writes the source art, so the exported
## files are full-size no matter what it is set to. Capping on the Godot side is
## what actually makes the imported project lightweight.
##
## Two things have to happen, and doing only the first is the trap: setting the
## importer default only affects textures imported from now on. Textures already
## in the project keep whatever their .import file says, so those are rewritten
## and reimported explicitly.

const IMPORTER_DEFAULTS := "importer_defaults/texture"


static func set_default_limit(size_limit: int) -> void:
	## Applies to every texture imported from here on.
	var defaults: Dictionary = {}
	if ProjectSettings.has_setting(IMPORTER_DEFAULTS):
		var existing = ProjectSettings.get_setting(IMPORTER_DEFAULTS)
		if existing is Dictionary:
			defaults = (existing as Dictionary).duplicate()
	if size_limit > 0:
		defaults["process/size_limit"] = size_limit
	else:
		defaults.erase("process/size_limit")
	ProjectSettings.set_setting(IMPORTER_DEFAULTS, defaults)
	ProjectSettings.save()


static func find_textures(folder: String) -> PackedStringArray:
	var out := PackedStringArray()
	var dir := DirAccess.open(folder)
	if dir == null:
		return out
	for f in dir.get_files():
		# .import is Godot's own metadata; the source image is what we reimport.
		var lower := f.to_lower()
		if lower.ends_with(".png") or lower.ends_with(".jpg") or lower.ends_with(".jpeg") \
				or lower.ends_with(".tga") or lower.ends_with(".webp") or lower.ends_with(".bmp"):
			out.append(folder.path_join(f))
	return out


static func _rewrite_import_file(import_path: String, size_limit: int) -> bool:
	## Sets process/size_limit inside an existing .import file.
	##
	## Rewritten as text rather than through ConfigFile: a .import holds a
	## [remap] section whose keys Godot regenerates, and round-tripping the
	## whole file risks dropping fields this code does not know about.
	if not FileAccess.file_exists(import_path):
		return false
	var text := FileAccess.get_file_as_string(import_path)
	if text == "":
		return false

	var lines := text.split("\n")
	var out: PackedStringArray = []
	var in_params := false
	var written := false
	for line in lines:
		var stripped := line.strip_edges()
		if stripped.begins_with("["):
			# Leaving [params] without having seen the key? Add it before moving on.
			if in_params and not written:
				out.append("process/size_limit=%d" % size_limit)
				written = true
			in_params = stripped == "[params]"
			out.append(line)
			continue
		if in_params and stripped.begins_with("process/size_limit"):
			out.append("process/size_limit=%d" % size_limit)
			written = true
			continue
		out.append(line)

	if in_params and not written:
		out.append("process/size_limit=%d" % size_limit)
		written = true
	if not written:
		return false

	var f := FileAccess.open(import_path, FileAccess.WRITE)
	if f == null:
		return false
	f.store_string("\n".join(out))
	f.close()
	return true


static func apply_to_folder(folder: String, size_limit: int) -> Dictionary:
	## Caps textures already in the project and reimports them.
	## Returns {"total": int, "changed": int, "reimported": int}.
	var result := {"total": 0, "changed": 0, "reimported": 0}
	var textures := find_textures(folder)
	result["total"] = textures.size()
	if textures.is_empty():
		return result

	var to_reimport := PackedStringArray()
	for tex_path in textures:
		if _rewrite_import_file(tex_path + ".import", size_limit):
			to_reimport.append(tex_path)
	result["changed"] = to_reimport.size()

	if not to_reimport.is_empty() and Engine.is_editor_hint():
		var fs := EditorInterface.get_resource_filesystem()
		if fs:
			fs.reimport_files(to_reimport)
			result["reimported"] = to_reimport.size()
	return result
