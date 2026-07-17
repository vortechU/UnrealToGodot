@tool
extends RefCounted

## Caps the size Godot imports textures at, and can shrink the files themselves.
##
## An Unreal export ships its source art: a real level is gigabytes of 4K PNGs.
## Godot imports those at full resolution, which is slow, costs a lot of VRAM,
## and can exhaust memory in the WebP packer badly enough to take the editor
## down partway through -- leaving a half-populated .godot cache that looks like
## a broken export rather than an out-of-memory crash.
##
## Texture sizing lives on this side of the pipeline because Unreal cannot do it.
## Its Python API exposes no way to resize the source art an export writes:
## max_texture_size and ResizeDuringBuild drive the *cooked* texture while
## TextureExporterPNG writes the *source*, and every route to the cooked pixels
## (render targets, ExportTexture2D) reads block-compressed data -- which returns
## a constant 0 blue channel for BC5 normal maps, silently flattening them. See
## docs/texture-sizing.md. Godot's Image.resize has none of those problems.
##
## Three things have to happen, and doing only the first is the trap:
##   1. The importer default only affects textures imported from now on.
##   2. Textures already in the project keep whatever their .import file says,
##      so those are rewritten and reimported explicitly.
##   3. Both of the above only change what Godot *loads*. The exported PNG on
##      disk stays 4K, so the project keeps carrying gigabytes it never uses --
##      shrink_source_files() is what actually reclaims the disk space.

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


static func target_size(width: int, height: int, size_limit: int) -> Vector2i:
	## Longest edge capped to size_limit, aspect ratio kept. Never upscales.
	var longest := maxi(width, height)
	if size_limit <= 0 or longest <= size_limit:
		return Vector2i(width, height)
	var scale := float(size_limit) / float(longest)
	return Vector2i(maxi(1, roundi(width * scale)), maxi(1, roundi(height * scale)))


static func shrink_source_files(folder: String, size_limit: int) -> Dictionary:
	## Rewrites oversized exported PNGs on disk at the capped size.
	##
	## This is destructive and cannot be undone from inside Godot -- the source
	## art only exists back in Unreal. It is deliberately limited to .png, the
	## only thing the exporter writes: anything else in the folder was put there
	## by hand, and quietly rewriting someone's own art would be a nasty way to
	## learn what this option does.
	##
	## Returns {"total", "shrunk", "skipped", "failed", "bytes_before",
	## "bytes_after"}. Sizes are read from the file header via Image, so an
	## already-capped folder costs a load per texture and no writes.
	var result := {
		"total": 0, "shrunk": 0, "skipped": 0, "failed": 0,
		"bytes_before": 0, "bytes_after": 0,
	}
	if size_limit <= 0:
		return result

	var changed := PackedStringArray()
	for tex_path in find_textures(folder):
		result["total"] += 1
		if not tex_path.to_lower().ends_with(".png"):
			result["skipped"] += 1
			continue

		var image := Image.new()
		if image.load(tex_path) != OK:
			result["failed"] += 1
			push_warning("Unreal Importer: could not read %s to shrink it" % tex_path)
			continue

		var target := target_size(image.get_width(), image.get_height(), size_limit)
		if target == Vector2i(image.get_width(), image.get_height()):
			continue

		var before := _file_size(tex_path)
		# Lanczos is the sharpest of Godot's filters for downscaling. It resamples
		# raw channel values, so packed masks and normal maps come through intact
		# -- no gamma reinterpretation, no channel dropped.
		image.resize(target.x, target.y, Image.INTERPOLATE_LANCZOS)
		if image.save_png(tex_path) != OK:
			result["failed"] += 1
			push_warning("Unreal Importer: could not write %s at %dx%d" % [tex_path, target.x, target.y])
			continue

		result["shrunk"] += 1
		result["bytes_before"] += before
		result["bytes_after"] += _file_size(tex_path)
		changed.append(tex_path)

	# The bytes behind every rewritten path just changed; without a reimport the
	# editor keeps serving whatever the stale .godot cache holds.
	if not changed.is_empty() and Engine.is_editor_hint():
		var fs := EditorInterface.get_resource_filesystem()
		if fs:
			fs.reimport_files(changed)
	return result


static func _file_size(path: String) -> int:
	var f := FileAccess.open(path, FileAccess.READ)
	if f == null:
		return 0
	var size := f.get_length()
	f.close()
	return size


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
