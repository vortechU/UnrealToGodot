@tool
extends RefCounted

# ==============================================================================
# Shared static helpers for the Unreal Layout Importer feature modules.
# Preload this script; do not duplicate these helpers in feature modules.
# See docs/SCHEMA_V2.md for the JSON schema all modules consume.
# ==============================================================================

const IMAGE_EXTENSIONS: Array[String] = [".png", ".tga", ".jpg", ".jpeg", ".dds", ".exr", ".webp"]
const MODEL_EXTENSIONS: Array[String] = [".gltf", ".glb"]


static func get_transform_from_dict(t_dict: Dictionary) -> Transform3D:
	"""Constructs a Transform3D from a schema transform dictionary (Godot-space)."""
	var trans_arr: Array = t_dict.get("translation", [0.0, 0.0, 0.0])
	var quat_arr: Array = t_dict.get("rotation_quat", [0.0, 0.0, 0.0, 1.0])
	var scale_arr: Array = t_dict.get("scale", [1.0, 1.0, 1.0])

	var translation := vec3_from_array(trans_arr, Vector3.ZERO)
	var scale := vec3_from_array(scale_arr, Vector3.ONE)

	var qx: float = quat_arr[0] if quat_arr.size() > 0 else 0.0
	var qy: float = quat_arr[1] if quat_arr.size() > 1 else 0.0
	var qz: float = quat_arr[2] if quat_arr.size() > 2 else 0.0
	var qw: float = quat_arr[3] if quat_arr.size() > 3 else 1.0
	var quat := Quaternion(qx, qy, qz, qw)
	if not quat.is_normalized():
		quat = quat.normalized()

	# Unreal's FTransform applies scale BEFORE rotation (R * S). Basis.scaled()
	# is a GLOBAL scale (S * R), which lands the scale on the wrong axes for any
	# rotated actor: a piece scaled 0.875 along its local X and yawed 90 degrees
	# would get squashed along world Z instead of world X.
	var basis := Basis(quat) * Basis.from_scale(scale)
	return Transform3D(basis, translation)


static func vec3_from_array(arr, def := Vector3.ZERO) -> Vector3:
	if arr == null or not (arr is Array):
		return def
	var x: float = arr[0] if arr.size() > 0 else def.x
	var y: float = arr[1] if arr.size() > 1 else def.y
	var z: float = arr[2] if arr.size() > 2 else def.z
	return Vector3(x, y, z)


static func color_from_array(arr, def := Color.WHITE) -> Color:
	if arr == null or not (arr is Array):
		return def
	var r: float = arr[0] if arr.size() > 0 else def.r
	var g: float = arr[1] if arr.size() > 1 else def.g
	var b: float = arr[2] if arr.size() > 2 else def.b
	var a: float = arr[3] if arr.size() > 3 else 1.0
	return Color(r, g, b, a)


static func get_num(d: Dictionary, key: String, def: float = 0.0) -> float:
	"""Safe numeric getter: JSON values may arrive as int, float, or null."""
	var v = d.get(key, def)
	if v is float or v is int:
		return float(v)
	return def


static func get_str(d: Dictionary, key: String, def: String = "") -> String:
	var v = d.get(key, def)
	if v == null:
		return def
	return str(v)


static func find_file_case_insensitive(folder: String, base_name: String, extensions: Array) -> String:
	"""
	Looks for <base_name><ext> inside folder. Tries exact match first, then a
	case-insensitive directory scan (asset packs frequently mix filename casing).
	Returns "" when not found.
	"""
	if folder == "" or base_name == "":
		return ""
	for ext in extensions:
		var path: String = folder.path_join(base_name + ext)
		if FileAccess.file_exists(path):
			return path
	# Case-insensitive fallback scan
	var dir := DirAccess.open(folder)
	if dir == null:
		return ""
	var wanted: Array[String] = []
	for ext in extensions:
		wanted.append((base_name + ext).to_lower())
	dir.list_dir_begin()
	var fname := dir.get_next()
	while fname != "":
		if not dir.current_is_dir():
			if wanted.has(fname.to_lower()):
				dir.list_dir_end()
				return folder.path_join(fname)
		fname = dir.get_next()
	dir.list_dir_end()
	return ""


static func find_texture_path(folders: Array, tex_name: String) -> String:
	"""Searches an ordered list of folders for a texture file matching tex_name."""
	if tex_name == "":
		return ""
	for folder in folders:
		if folder == null or str(folder) == "":
			continue
		var found := find_file_case_insensitive(str(folder), tex_name, IMAGE_EXTENSIONS)
		if found != "":
			return found
	return ""


static func find_model_path(folder: String, base_name: String) -> String:
	"""Searches for a .gltf/.glb model file matching base_name (case-insensitive)."""
	return find_file_case_insensitive(folder, base_name, MODEL_EXTENSIONS)


static func load_image_file(path: String, notes = null) -> Image:
	"""Loads an Image from res:// or an absolute filesystem path.

	Used for heightmaps, where precision matters: Godot's texture importer may
	VRAM-compress or reformat an .exr, which would quantise the height data and
	produce terraced terrain. So for res:// paths we read the raw file off disk
	first (globalize_path is reliable here — this is an editor-only tool) and
	only fall back to the imported resource if that fails.

	The file's CONTENT picks the decoder, not its name: Unreal's
	ExportRenderTarget chooses the output format from the render-target format
	and keeps whatever filename it was given, so a "height.exr" on disk is
	frequently PNG bytes. Godot picks its loader by extension and would fail on
	such a file. `notes` (optional Array) receives human-readable diagnostics
	about mismatches and precision loss."""
	if path == "":
		return null
	var raw_path := path
	if path.begins_with("res://"):
		raw_path = ProjectSettings.globalize_path(path)
	if raw_path != "" and FileAccess.file_exists(raw_path):
		var direct := _load_image_by_content(raw_path, path.get_extension().to_lower(), notes)
		if direct != null:
			return direct
	if path.begins_with("res://"):
		var tex := load(path)
		if tex is Texture2D:
			var img := (tex as Texture2D).get_image()
			if img != null and img.is_compressed():
				img.decompress()
			return img
	return null


static func _image_format_from_magic(header: PackedByteArray) -> String:
	"""Identifies an image file format from its leading bytes; "" when unknown.
	TGA has no magic and stays extension-driven."""
	if header.size() >= 4 and header[0] == 0x89 and header[1] == 0x50 and header[2] == 0x4E and header[3] == 0x47:
		return "png"
	if header.size() >= 4 and header[0] == 0x76 and header[1] == 0x2F and header[2] == 0x31 and header[3] == 0x01:
		return "exr"
	if header.size() >= 3 and header[0] == 0xFF and header[1] == 0xD8 and header[2] == 0xFF:
		return "jpg"
	if header.size() >= 12 and header[0] == 0x52 and header[1] == 0x49 and header[2] == 0x46 and header[3] == 0x46 \
			and header[8] == 0x57 and header[9] == 0x45 and header[10] == 0x42 and header[11] == 0x50:
		return "webp"
	if header.size() >= 4 and header[0] == 0x44 and header[1] == 0x44 and header[2] == 0x53 and header[3] == 0x20:
		return "dds"
	if header.size() >= 2 and header[0] == 0x42 and header[1] == 0x4D:
		return "bmp"
	return ""


static func _load_image_by_content(raw_path: String, claimed_ext: String, notes) -> Image:
	"""Loads an image, decoding by the file's actual content when the extension
	lies about it. raw_path must be a plain filesystem path."""
	var f := FileAccess.open(raw_path, FileAccess.READ)
	if f == null:
		return null
	var header := f.get_buffer(32)
	f.close()

	var actual := _image_format_from_magic(header)
	var claimed := "jpg" if claimed_ext == "jpeg" else claimed_ext
	if actual == "" or actual == claimed:
		return Image.load_from_file(raw_path)

	if notes is Array:
		notes.append("'%s' is actually a %s file mislabeled as .%s (Unreal's ExportRenderTarget picks the format itself); loaded by content instead."
			% [raw_path.get_file(), actual.to_upper(), claimed_ext])
	if actual == "png" and header.size() >= 25 and header[24] == 16 and notes is Array:
		notes.append("16-bit PNG: Godot decodes PNG at 8 bits/channel, so height precision is reduced (terracing possible). Re-export with the updated exporter to get a float EXR.")

	var buf := FileAccess.get_file_as_bytes(raw_path)
	if buf.is_empty():
		return null
	var img := Image.new()
	var err := ERR_UNAVAILABLE
	match actual:
		"png":
			err = img.load_png_from_buffer(buf)
		"jpg":
			err = img.load_jpg_from_buffer(buf)
		"webp":
			err = img.load_webp_from_buffer(buf)
		"bmp":
			err = img.load_bmp_from_buffer(buf)
		"dds":
			err = img.load_dds_from_buffer(buf)
		"exr":
			# No buffer API for EXR: route through a correctly-named temp file.
			var tmp := OS.get_cache_dir().path_join("ue2g_sniffed_image.exr")
			var out := FileAccess.open(tmp, FileAccess.WRITE)
			if out != null:
				out.store_buffer(buf)
				out.close()
				var via_tmp := Image.load_from_file(tmp)
				DirAccess.remove_absolute(tmp)
				return via_tmp
	if err != OK:
		return null
	return img


static func load_texture_file(tex_path: String) -> Texture2D:
	"""Loads a texture from res:// (imported resource) or from the external filesystem."""
	if tex_path == "":
		return null
	if tex_path.begins_with("res://"):
		return load(tex_path) as Texture2D
	var img := Image.load_from_file(tex_path)
	if img:
		return ImageTexture.create_from_image(img)
	return null


static func instantiate_model(model_path: String) -> Node3D:
	"""Loads and instances a glTF scene from res:// or an external path."""
	if model_path == "":
		return null
	if model_path.begins_with("res://"):
		var packed = load(model_path)
		if packed and packed is PackedScene:
			return packed.instantiate() as Node3D
	var doc := GLTFDocument.new()
	var state := GLTFState.new()
	var err := doc.append_from_file(model_path, state)
	if err == OK:
		return doc.generate_scene(state) as Node3D
	printerr("Unreal Importer: failed to load glTF model: ", model_path, " (error ", err, ")")
	return null


static func find_first_mesh(node: Node) -> MeshInstance3D:
	"""Depth-first search for the first MeshInstance3D inside a node tree."""
	if node is MeshInstance3D:
		return node
	for child in node.get_children():
		var found := find_first_mesh(child)
		if found:
			return found
	return null


static func set_owner_recursive(node: Node, owner_node: Node) -> void:
	"""Recursively sets owner on descendants so they persist in the saved scene.
	Does not traverse into instanced sub-scenes (they serialize as instances)."""
	if node != owner_node and node.scene_file_path != "":
		return
	for child in node.get_children():
		if child.owner == owner_node:
			continue
		child.owner = owner_node
		set_owner_recursive(child, owner_node)


static func add_owned_child(parent: Node, child: Node, scene_owner: Node) -> void:
	"""Adds child to parent and marks it (and its subtree) as owned by the scene root."""
	parent.add_child(child)
	child.owner = scene_owner
	set_owner_recursive(child, scene_owner)


static func kelvin_to_color(kelvin: float) -> Color:
	"""Approximate blackbody color temperature (Kelvin) to linear RGB (Tanner Helland fit)."""
	var t := clampf(kelvin, 1000.0, 15000.0) / 100.0
	var r: float
	var g: float
	var b: float
	if t <= 66.0:
		r = 255.0
		g = clampf(99.4708025861 * log(t) - 161.1195681661, 0.0, 255.0)
		b = 255.0 if t >= 66.0 else (0.0 if t <= 19.0 else clampf(138.5177312231 * log(t - 10.0) - 305.0447927307, 0.0, 255.0))
	else:
		r = clampf(329.698727446 * pow(t - 60.0, -0.1332047592), 0.0, 255.0)
		g = clampf(288.1221695283 * pow(t - 60.0, -0.0755148492), 0.0, 255.0)
		b = 255.0
	return Color(r / 255.0, g / 255.0, b / 255.0)


static func resolve_json_relative(json_dir: String, rel_path: String) -> String:
	"""Resolves a schema file reference (e.g. 'terrain/height.exr') against the JSON dir."""
	if rel_path == "":
		return ""
	if rel_path.begins_with("res://") or rel_path.is_absolute_path():
		return rel_path
	return json_dir.path_join(rel_path)
