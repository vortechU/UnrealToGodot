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

const Common = preload("res://addons/unreal_importer/import_common.gd")

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
		# The list is Common.IMAGE_EXTENSIONS deliberately: anything the material
		# binder can resolve must also be cappable, or a 4K .dds slips through and
		# the import-time OOM this class exists to prevent comes straight back.
		var lower := f.to_lower()
		for ext in Common.IMAGE_EXTENSIONS:
			if lower.ends_with(ext):
				out.append(folder.path_join(f))
				break
	return out


static func _rewrite_import_param(import_path: String, param_key: String, value_str: String,
		add_if_missing: bool = true) -> bool:
	## Sets `<param_key>=<value_str>` inside the [params] section of an existing
	## Godot .import file.
	##
	## Rewritten as text rather than through ConfigFile: a .import holds a
	## [remap] section whose keys Godot regenerates, and round-tripping the
	## whole file risks dropping fields this code does not know about.
	if not FileAccess.file_exists(import_path):
		return false
	var text := FileAccess.get_file_as_string(import_path)
	if text == "":
		return false

	var prefix := param_key + "="
	var target_line := "%s=%s" % [param_key, value_str]
	var lines := text.split("\n")
	var out: PackedStringArray = []
	var in_params := false
	var written := false
	var changed := false
	for line in lines:
		var stripped := line.strip_edges()
		if stripped.begins_with("["):
			if in_params and not written:
				if add_if_missing:
					out.append(target_line)
					changed = true
				written = true
			in_params = stripped == "[params]"
			out.append(line)
			continue
		if in_params and stripped.begins_with(prefix):
			if stripped != target_line:
				changed = true
			out.append(target_line)
			written = true
			continue
		out.append(line)

	if in_params and not written:
		if add_if_missing:
			out.append(target_line)
			changed = true
		written = true
	if not written or not changed:
		return false

	var f := FileAccess.open(import_path, FileAccess.WRITE)
	if f == null:
		return false
	f.store_string("\n".join(out))
	f.close()
	return true


static func _rewrite_import_file(import_path: String, size_limit: int) -> bool:
	## Sets process/size_limit inside an existing .import file.
	## Godot defaults an absent size_limit to 0, so only insert when > 0.
	return _rewrite_import_param(import_path, "process/size_limit", str(size_limit), size_limit > 0)


static func apply_normal_map_flip(folders: Array, normal_targets: Dictionary,
		flip_enabled: bool = true) -> Dictionary:
	## Sets process/normal_map_invert_y on imported normal maps inside `folders`.
	##
	## `normal_targets` maps lowercase texture filename stem -> bool (true when
	## the Unreal source art is DirectX -Y and needs Y inversion in Godot, false
	## when Unreal's own flip_green_channel was already true so the source PNG is
	## already OpenGL +Y).
	##
	## Non-destructive and idempotent: the source PNG bytes on disk are never
	## mutated, so re-running import never double-flips a normal map, and
	## unticking the dock option cleanly sets process/normal_map_invert_y=false.
	var result := {"total": 0, "changed": 0, "reimported": 0}
	if normal_targets.is_empty():
		return result

	var to_reimport := PackedStringArray()
	var seen_paths := {}
	for folder in folders:
		var folder_str := str(folder)
		if folder_str == "":
			continue
		for tex_path in find_textures(folder_str):
			if seen_paths.has(tex_path):
				continue
			seen_paths[tex_path] = true
			var stem := tex_path.get_file().get_basename().to_lower()
			if not normal_targets.has(stem):
				continue
			result["total"] += 1
			var want_invert: bool = flip_enabled and bool(normal_targets[stem])
			var val_str := "true" if want_invert else "false"
			if _rewrite_import_param(tex_path + ".import", "process/normal_map_invert_y",
					val_str, want_invert):
				to_reimport.append(tex_path)
	result["changed"] = to_reimport.size()

	var res_reimport := PackedStringArray()
	for p in to_reimport:
		if p.begins_with("res://"):
			res_reimport.append(p)
	if not res_reimport.is_empty() and Engine.is_editor_hint():
		var fs := EditorInterface.get_resource_filesystem()
		if fs:
			fs.reimport_files(res_reimport)
			result["reimported"] = res_reimport.size()
	return result


static func invert_green_channel(image: Image) -> Image:
	## In-memory DirectX -> OpenGL Y-flip for external (non-res://) normal maps
	## that have no Godot .import file. Returns a new Image with G = 1.0 - G.
	if image == null:
		return null
	var out: Image = image.duplicate()
	if out.is_compressed():
		out.decompress()
	for y in out.get_height():
		for x in out.get_width():
			var c := out.get_pixel(x, y)
			c.g = 1.0 - c.g
			out.set_pixel(x, y, c)
	return out


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
	# EditorFileSystem.reimport_files only accepts res:// paths (external export
	# folders outside res:// are loaded directly off disk and need no reimport).
	var res_changed := PackedStringArray()
	for p in changed:
		if p.begins_with("res://"):
			res_changed.append(p)
	if not res_changed.is_empty() and Engine.is_editor_hint():
		var fs := EditorInterface.get_resource_filesystem()
		if fs:
			fs.reimport_files(res_changed)
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

	var res_reimport := PackedStringArray()
	for p in to_reimport:
		if p.begins_with("res://"):
			res_reimport.append(p)
	if not res_reimport.is_empty() and Engine.is_editor_hint():
		var fs := EditorInterface.get_resource_filesystem()
		if fs:
			fs.reimport_files(res_reimport)
			result["reimported"] = res_reimport.size()
	return result
