@tool
extends RefCounted

# ==============================================================================
# Shared static helpers for the Unreal Layout Importer feature modules.
# Preload this script; do not duplicate these helpers in feature modules.
# See docs/SCHEMA_V2.md for the JSON schema all modules consume.
# ==============================================================================

const IMAGE_EXTENSIONS: Array[String] = [".png", ".tga", ".jpg", ".jpeg", ".dds", ".exr", ".webp"]
const MODEL_EXTENSIONS: Array[String] = [".gltf", ".glb"]

# Unreal's glTF exporter bakes mesh geometry in the (X, Z, Y) axis order, but the
# layout places every actor/component with the (Y, Z, -X) convention
# (ue2g_common.unreal_to_godot_transform). Those two right-handed maps differ by
# exactly a +90 deg yaw about the up axis, so a mesh dropped in at its layout
# transform ends up rotated about its OWN pivot. That is invisible on a prop whose
# geometry is centred on its pivot, but it flings every off-centre modular piece
# (walls, floors, stairs, containers) metres away -- the "scattered building" bug.
#
# gltf_mesh_placement() re-seats the mesh by post-multiplying the placement basis
# with this yaw. Applied at the StaticBody (which carries both the mesh at identity
# and the mesh-local collision shapes), it rotates mesh and collision together, so
# the collision stays hugging the mesh while the whole unit lands where it belongs.
# The quaternion (0, sin45, 0, cos45) is +90 deg about Y; proof in tests/test_math.py
# section 9 and verified in-engine.
const GLTF_MESH_AXIS_FIX := Basis(Quaternion(0.0, 0.70710678118654752, 0.0, 0.70710678118654752))


# Group the navigation bake reads its source geometry from: a NavigationRegion3D is
# a sibling of the imported geometry, so baking its own children finds nothing.
# Kept in step with tscn_writer.py's NAV_SOURCE_GROUP.
const NAV_SOURCE_GROUP := &"unreal_nav_source"


static func gltf_mesh_placement(placement: Transform3D) -> Transform3D:
	"""Corrects a layout-convention placement so a glTF mesh (baked in the glTF
	axis order) sitting at identity beneath it renders aligned with its neighbours.
	See GLTF_MESH_AXIS_FIX. Origin is preserved; only the basis is rotated."""
	return Transform3D(placement.basis * GLTF_MESH_AXIS_FIX, placement.origin)


static func get_transform_from_dict(t_dict) -> Transform3D:
	"""Constructs a Transform3D from a schema transform dictionary (Godot-space)."""
	if not (t_dict is Dictionary):
		return Transform3D.IDENTITY
	var trans_raw = t_dict.get("translation", [0.0, 0.0, 0.0])
	var quat_raw = t_dict.get("rotation_quat", [0.0, 0.0, 0.0, 1.0])
	var scale_raw = t_dict.get("scale", [1.0, 1.0, 1.0])

	var translation := vec3_from_array(trans_raw, Vector3.ZERO)
	var scale := vec3_from_array(scale_raw, Vector3.ONE)

	var quat_arr: Array = quat_raw if quat_raw is Array else [0.0, 0.0, 0.0, 1.0]
	var qx: float = float(quat_arr[0]) if (quat_arr.size() > 0 and (quat_arr[0] is float or quat_arr[0] is int)) else 0.0
	var qy: float = float(quat_arr[1]) if (quat_arr.size() > 1 and (quat_arr[1] is float or quat_arr[1] is int)) else 0.0
	var qz: float = float(quat_arr[2]) if (quat_arr.size() > 2 and (quat_arr[2] is float or quat_arr[2] is int)) else 0.0
	var qw: float = float(quat_arr[3]) if (quat_arr.size() > 3 and (quat_arr[3] is float or quat_arr[3] is int)) else 1.0
	var quat := Quaternion(qx, qy, qz, qw)
	if quat.length_squared() < 1e-12:
		quat = Quaternion.IDENTITY
	elif not quat.is_normalized():
		quat = quat.normalized()

	# scaled_local(), NOT scaled(): the schema's scale is a LOCAL scale (it names
	# the node's own axes), and Godot's Basis.scaled() scales rows -- i.e. it
	# applies the scale in the PARENT frame (S * R). Rotated actors with a
	# non-uniform scale then come out stretched along the wrong world axes.
	# scaled_local() is R * S, which reproduces C * (A * S_unreal) * C^T exactly.
	var basis := Basis(quat).scaled_local(scale)
	return Transform3D(basis, translation)


# ==============================================================================
# Physics-body scale handling.
#
# Jolt (Godot's default 3D physics since 4.4) cannot apply an arbitrary scale to
# an arbitrary shape. A SphereShape3D and a CapsuleShape3D accept only a uniform
# scale; a BoxShape3D or ConvexPolygonShape3D accepts a non-uniform one only when
# the shape itself is not rotated inside the body. Handed anything else, Jolt does
# NOT fail loudly and does not keep the scale either -- it substitutes the
# ARITHMETIC MEAN of the three axes and logs one error per body, per load:
#
#   Failed to correctly scale body '...'. A scale of (1, 1, 0.9346) is not
#   supported ... The scale will instead be treated as (0.9782, 0.9782, 0.9782).
#
# The collider then no longer matches the mesh it was exported to hug (~5 cm per
# metre in that example), which is what a player feels as floating above a prop or
# bumping into air beside it.
#
# Unreal level designers scale props non-uniformly all the time, so this is the
# normal case, not an exotic one. The fix is to keep the scale OFF the physics
# body -- the body carries rotation and translation only -- and bake it into the
# shapes and the mesh instance instead. See physics_body_transform().
# ==============================================================================

# Scales whose spread is under this (relative to the largest axis) are treated as
# uniform and left on the body untouched -- the overwhelmingly common case, and
# the one where doing nothing is exactly right.
const SCALE_UNIFORM_EPSILON: float = 1e-4


static func basis_local_scale(basis: Basis) -> Vector3:
	"""The node's LOCAL scale: the length of each basis COLUMN (Basis.x/y/z are the
	columns; Godot exposes no get_column() to GDScript), with a mirrored basis's
	handedness folded into X. Chosen so that
	basis_without_scale(b) * Basis.from_scale(basis_local_scale(b)) == b."""
	var s := Vector3(basis.x.length(), basis.y.length(), basis.z.length())
	if basis.determinant() < 0.0:
		s.x = -s.x
	return s


static func basis_without_scale(basis: Basis) -> Basis:
	"""The rotation half of a rotation*scale basis (a proper rotation: any mirror
	stays behind in basis_local_scale). A degenerate (zero-scale) axis is left as
	the unit axis rather than dividing by zero."""
	var s := basis_local_scale(basis)
	return Basis(
		basis.x / s.x if absf(s.x) > 1e-9 else Vector3.RIGHT,
		basis.y / s.y if absf(s.y) > 1e-9 else Vector3.UP,
		basis.z / s.z if absf(s.z) > 1e-9 else Vector3.BACK)


static func scale_is_body_safe(scale: Vector3) -> bool:
	"""True when a physics body may carry this scale directly. Jolt accepts a
	uniform, non-mirrored scale on every shape type; anything else it silently
	replaces with the mean of the three axes."""
	if scale.x < 0.0 or scale.y < 0.0 or scale.z < 0.0:
		return false
	var hi := maxf(scale.x, maxf(scale.y, scale.z))
	if hi <= 0.0:
		return true
	return (hi - minf(scale.x, minf(scale.y, scale.z))) / hi <= SCALE_UNIFORM_EPSILON


static func physics_body_scale(placement: Transform3D) -> Vector3:
	"""The part of a placement's scale that must be baked into the shapes instead
	of left on the StaticBody3D. Vector3.ONE means "nothing to bake"."""
	var s := basis_local_scale(placement.basis)
	if scale_is_body_safe(s):
		return Vector3.ONE
	return s


static func physics_body_transform(placement: Transform3D) -> Transform3D:
	"""A placement with the Jolt-unsafe scale stripped off. Uniform scales are
	returned untouched, so ordinary props keep exactly the transform they had."""
	if physics_body_scale(placement) == Vector3.ONE:
		return placement
	return Transform3D(basis_without_scale(placement.basis), placement.origin)


static func scaled_shape_transform(local: Transform3D, scale: Vector3) -> Transform3D:
	"""A box/sphere/capsule's local transform once `scale` has been taken off the
	body: the shape's OFFSET moves with the scale and its rotation is mirrored by
	the scale's signs, but never sheared. Convex hulls carry the mirror in their
	points instead and must keep their own basis."""
	if scale == Vector3.ONE:
		return local
	var mirror := Vector3(
		-1.0 if scale.x < 0.0 else 1.0,
		-1.0 if scale.y < 0.0 else 1.0,
		-1.0 if scale.z < 0.0 else 1.0)
	var basis := Basis.from_scale(mirror) * local.basis
	# A reflected basis is not a rotation. These shapes are symmetric about their
	# own axes, so negating one column is the same shape in a right-handed frame.
	if mirror.x * mirror.y * mirror.z < 0.0:
		basis.x = -basis.x
	return Transform3D(basis, local.origin * scale)


static func scaled_axis_factors(local_basis: Basis, scale: Vector3) -> Vector3:
	"""How much each of a shape's OWN axes stretches under a body scale applied in
	the parent frame.

	Exact whenever the shape's rotation is axis-aligned with the scale, which is
	what Unreal's simple collision almost always is. When it is not, the true
	result is a shear that no primitive shape can represent (and that Jolt refuses
	outright) -- the stretch along each of the shape's own axes is the closest
	thing a primitive can carry. Convex hulls do not use this: they take the shear
	exactly, per vertex."""
	if scale == Vector3.ONE:
		return Vector3.ONE
	return Vector3(_axis_factor(local_basis.x, scale),
				   _axis_factor(local_basis.y, scale),
				   _axis_factor(local_basis.z, scale))


static func _axis_factor(axis: Vector3, scale: Vector3) -> float:
	var before := axis.length()
	if before <= 1e-9:
		return 1.0
	return (axis * scale).length() / before


static func uniform_factor(scale: Vector3) -> float:
	"""The single factor a shape that cannot be scaled non-uniformly (a sphere)
	has to settle for."""
	return (absf(scale.x) + absf(scale.y) + absf(scale.z)) / 3.0


static func vec3_from_array(arr, def := Vector3.ZERO) -> Vector3:
	if arr == null or not (arr is Array):
		return def
	var x: float = float(arr[0]) if (arr.size() > 0 and (arr[0] is float or arr[0] is int)) else def.x
	var y: float = float(arr[1]) if (arr.size() > 1 and (arr[1] is float or arr[1] is int)) else def.y
	var z: float = float(arr[2]) if (arr.size() > 2 and (arr[2] is float or arr[2] is int)) else def.z
	return Vector3(x, y, z)


static func color_from_array(arr, def := Color.WHITE) -> Color:
	"""Schema colors are linear; Godot's color properties are sRGB."""
	if arr == null or not (arr is Array):
		return def
	var r: float = float(arr[0]) if (arr.size() > 0 and (arr[0] is float or arr[0] is int)) else def.r
	var g: float = float(arr[1]) if (arr.size() > 1 and (arr[1] is float or arr[1] is int)) else def.g
	var b: float = float(arr[2]) if (arr.size() > 2 and (arr[2] is float or arr[2] is int)) else def.b
	var a: float = float(arr[3]) if (arr.size() > 3 and (arr[3] is float or arr[3] is int)) else def.a
	return Color(r, g, b, a).linear_to_srgb()


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


static func find_material_entry(mat_list: Array, mat_name: String, slot_idx: int, claimed: Dictionary) -> Dictionary:
	"""The exported material entry behind one glTF surface.

	Name beats slot, because the glTF drops unused slots and shifts indices. But
	several slots may share one material name, so a name match prefers the entry on
	the surface's own slot, then one no earlier surface of this mesh took. `claimed`
	remembers those; pass the same Dictionary for every surface of a mesh."""
	var best := -1
	var best_rank := 0
	for i in mat_list.size():
		var mat = mat_list[i]
		if not (mat is Dictionary):
			continue
		var entry_name := get_str(mat, "material_name", "")
		var name_matches := mat_name != "" and entry_name != "" and entry_name != "None" \
			and entry_name.to_lower() == mat_name.to_lower()
		var slot_matches := int(get_num(mat, "slot_index", -1.0)) == slot_idx
		var rank := 0
		if name_matches:
			rank = 2
			if not claimed.has(i):
				rank = 4 if slot_matches else 3
		elif slot_matches:
			rank = 1
		if rank > best_rank:
			best = i
			best_rank = rank
	if best < 0:
		return {}
	claimed[best] = true
	return mat_list[best]


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


static func save_generated_texture(image: Image, source_path: String, key: String) -> Texture2D:
	"""Image as a texture stored in its own file beside `source_path`, so scenes
	reference it instead of embedding the pixels in every material that uses it."""
	var tex := ImageTexture.create_from_image(image)
	if not source_path.begins_with("res://"):
		return tex
	var folder := source_path.get_base_dir().path_join("generated")
	DirAccess.make_dir_recursive_absolute(ProjectSettings.globalize_path(folder))
	var path := folder.path_join("%s_%s.res" % [
		source_path.get_file().get_basename(), key.md5_text().substr(0, 8)])
	var err := ResourceSaver.save(tex, path, ResourceSaver.FLAG_COMPRESS)
	if err == OK:
		tex.take_over_path(path)
	else:
		push_warning("Unreal Importer: could not save %s (%s)" % [path, error_string(err)])
	return tex


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

	The file's CONTENT picks the decoder, not its name. Unreal's
	ExportRenderTarget chooses the output format from the render-target format
	and keeps whatever filename it was handed, so a "*_height.exr" on disk is
	frequently PNG bytes; Godot picks its loader by extension and would simply
	fail on it. `notes` (optional Array) collects human-readable diagnostics
	about such mismatches and about the precision they cost."""
	if path == "":
		return null
	var raw_path := path
	if path.begins_with("res://"):
		raw_path = ProjectSettings.globalize_path(path)
	if raw_path != "" and FileAccess.file_exists(raw_path):
		var direct := _decompressed(_load_image_by_content(raw_path, path.get_extension().to_lower(), notes))
		if direct != null:
			return direct
	if path.begins_with("res://"):
		var tex := load(path)
		if tex is Texture2D:
			return _decompressed((tex as Texture2D).get_image())
	return null


static func _decompressed(img: Image) -> Image:
	"""A BC1/BC3/BC7 .dds loads still compressed, and resize()/get_pixel() reject
	that. Returns null when the format cannot be unpacked."""
	if img != null and img.is_compressed() and img.decompress() != OK:
		return null
	return img


static func _image_format_from_magic(header: PackedByteArray) -> String:
	"""Identifies an image file format from its leading bytes; "" when unknown.
	TGA has no magic number and stays extension-driven."""
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
	# Image.load_from_file() has no .dds loader; only the buffer decoder reads one.
	if actual == "" or (actual == claimed and actual != "dds"):
		return Image.load_from_file(raw_path)

	if notes is Array and actual != claimed:
		notes.append("'%s' is actually a %s file mislabeled as .%s (Unreal's ExportRenderTarget picks the format itself); loaded by content instead."
			% [raw_path.get_file(), actual.to_upper(), claimed_ext])
		# PNG IHDR bit depth lives at byte 24.
		if actual == "png" and header.size() >= 25 and header[24] == 16:
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
		var loaded := load(tex_path) as Texture2D
		if loaded != null:
			return loaded
	return texture_from_image(load_image_file(tex_path))


static func texture_from_image(img: Image) -> ImageTexture:
	"""An in-memory texture with mipmaps, which a res:// import would have had."""
	if img == null:
		return null
	if not img.has_mipmaps():
		img.generate_mipmaps()
	return ImageTexture.create_from_image(img)


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
	# Readable: a duplicate label becomes "Rock2", not "@Node3D@12".
	parent.add_child(child, true)
	child.owner = scene_owner
	set_owner_recursive(child, scene_owner)


static func kelvin_to_color(kelvin: float) -> Color:
	"""Approximate blackbody color temperature (Kelvin) to sRGB (Tanner Helland fit)."""
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
