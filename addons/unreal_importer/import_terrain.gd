@tool
extends RefCounted

# ==============================================================================
# Rebuilds Unreal Landscapes in Godot. Consumes the "landscapes" schema section
# (see docs/SCHEMA_V2.md).
#
# Height contract: the exported heightmap is RELATIVE — the image is normalized
# by its own min/max, then rescaled into height_range_m (world-space metres,
# which the exporter derives from the heights it actually measured). Placement
# uses ue_bounds, so vertices are reconstructed in Unreal world space and
# converted per-vertex to Godot space, making the axis mapping correct by
# construction:
#     image U (+X) follows Unreal +X  ->  Godot -Z
#     image V (+Y) follows Unreal +Y  ->  Godot +X
#
# Build modes:
#   "terrain3d" / "auto": import into the Terrain3D plugin when it is installed.
#       Both plugin generations are handled — 1.x (Terrain3D.data, validated
#       against 1.0.2) and 0.9.x (Terrain3DStorage) — falling back to "mesh" on
#       any failure.
#   "hterrain": not automated — falls back to "mesh" with a warning.
#   "mesh": plugin-free fallback — an ArrayMesh grid with trimesh collision and
#       a splat-blended material built from the exported paint-layer weightmaps.
# ==============================================================================

const Common = preload("res://addons/unreal_importer/import_common.gd")

const MAX_MESH_GRID := 257          # vertices per side for the mesh fallback
const MAX_TERRAIN3D_RES := 1025     # cap for Terrain3D image conversion
const MAX_SPLAT_LAYERS := 4         # RGBA channels of the packed splatmap
const SPLAT_RESOLUTION := 512       # packed splatmap size
const DEFAULT_LAYER_TILING := 64.0  # albedo repeats across the terrain


func apply(data: Dictionary, root: Node, scene_owner: Node, options: Dictionary) -> Dictionary:
	var created := 0
	var warnings := PackedStringArray()

	if not options.get("build_terrain", true):
		return {"created": 0, "warnings": warnings}
	var landscapes = data.get("landscapes", [])
	if not (landscapes is Array) or landscapes.is_empty():
		return {"created": 0, "warnings": warnings}

	var json_dir := str(options.get("json_dir", "res://"))
	var mode := str(options.get("terrain_mode", "auto"))

	for ls in landscapes:
		if not (ls is Dictionary):
			continue
		var ls_name := Common.get_str(ls, "name", "Landscape")
		var img := _load_heightmap(ls, json_dir, warnings)
		if img == null:
			warnings.append("Landscape '%s': heightmap could not be loaded (%s)." % [ls_name, Common.get_str(ls, "heightmap_file", "?")])
			continue

		# An all-constant heightmap is a broken export, not a real landscape:
		# every GPU route Unreal exposes can silently render nothing. Import it
		# anyway, but say so loudly — flat terrain with no warning reads as an
		# importer bug.
		if _is_constant_heightmap(img):
			warnings.append(("Landscape '%s': heightmap has NO height variation — the "
				+ "Unreal-side export wrote an empty render target. The terrain will "
				+ "import flat; re-export the level from Unreal.") % ls_name)

		var built := false
		if mode == "auto" or mode == "terrain3d":
			built = await _try_terrain3d(ls, img, root, scene_owner, warnings, options)
			if not built and mode == "terrain3d":
				warnings.append("Landscape '%s': Terrain3D unavailable or import failed — using mesh fallback." % ls_name)
		elif mode == "hterrain":
			warnings.append("Landscape '%s': automated HTerrain build is not supported — using mesh fallback." % ls_name)

		if not built:
			built = _build_mesh_terrain(ls, img, root, scene_owner, json_dir, warnings)
		if built:
			created += 1

	return {"created": created, "warnings": warnings}


func _load_heightmap(ls: Dictionary, json_dir: String, warnings: PackedStringArray) -> Image:
	var path := Common.resolve_json_relative(json_dir, Common.get_str(ls, "heightmap_file", ""))
	if path == "":
		return null
	# The loader decodes by file CONTENT (Unreal writes PNG bytes under .exr
	# names); anything it noticed belongs in the import report.
	var notes := []
	var img := Common.load_image_file(path, notes)
	for n in notes:
		warnings.append("Landscape '%s': %s" % [Common.get_str(ls, "name", "Landscape"), str(n)])
	if img == null:
		return null
	if img.is_compressed():
		img.decompress()
	return img


func _is_constant_heightmap(img: Image) -> bool:
	"""True when every sampled pixel carries the same height — the signature of
	an Unreal export that rendered nothing. Sampled coarsely: per-pixel GDScript
	scans of a 4K image are far too slow for a load-time sanity check."""
	var probe := img
	if img.get_width() > 64 or img.get_height() > 64:
		probe = img.duplicate() as Image
		probe.resize(mini(img.get_width(), 64), mini(img.get_height(), 64), Image.INTERPOLATE_NEAREST)
	var mm := _scan_min_max(probe)
	return mm.y - mm.x <= 0.0


func _scan_min_max(img: Image) -> Vector2:
	var min_v := INF
	var max_v := -INF
	for py in img.get_height():
		for px in img.get_width():
			var v := img.get_pixel(px, py).r
			min_v = minf(min_v, v)
			max_v = maxf(max_v, v)
	if not is_finite(min_v) or not is_finite(max_v):
		return Vector2(0.0, 1.0)
	return Vector2(min_v, max_v)


func _ue_bounds(ls: Dictionary) -> Array:
	"""Returns [center: Vector3, extent: Vector3] in Unreal cm."""
	var b = ls.get("ue_bounds")
	if b is Dictionary:
		return [
			Common.vec3_from_array(b.get("center"), Vector3.ZERO),
			Common.vec3_from_array(b.get("extent"), Vector3(100.0, 100.0, 100.0)).abs(),
		]
	# Fallback: derive from world_size / height_range (Godot meters -> UE cm)
	var size = ls.get("world_size_m", [100.0, 100.0])
	var range_m = ls.get("height_range_m", [0.0, 10.0])
	var sx := float(size[0]) * 100.0 if (size is Array and size.size() > 0) else 10000.0
	var sz := float(size[1]) * 100.0 if (size is Array and size.size() > 1) else 10000.0
	var h0 := float(range_m[0]) * 100.0 if (range_m is Array and range_m.size() > 0) else 0.0
	var h1 := float(range_m[1]) * 100.0 if (range_m is Array and range_m.size() > 1) else 1000.0
	# world_size_m is [Godot X (UE Y), Godot Z (UE X)]
	return [
		Vector3(0.0, 0.0, (h0 + h1) * 0.5),
		Vector3(sz * 0.5, sx * 0.5, absf(h1 - h0) * 0.5),
	]


func _build_mesh_terrain(ls: Dictionary, img: Image, root: Node, scene_owner: Node, json_dir: String, warnings: PackedStringArray) -> bool:
	var ls_name := Common.get_str(ls, "name", "Landscape")
	var bounds := _ue_bounds(ls)
	var center: Vector3 = bounds[0]
	var extent: Vector3 = bounds[1]
	if extent.x < 1.0 or extent.y < 1.0:
		warnings.append("Landscape '%s': degenerate bounds; skipped." % ls_name)
		return false

	# Downsample to a manageable grid before per-pixel work
	var grid_w := mini(img.get_width(), MAX_MESH_GRID)
	var grid_h := mini(img.get_height(), MAX_MESH_GRID)
	var small := img.duplicate() as Image
	if small.get_width() != grid_w or small.get_height() != grid_h:
		small.resize(grid_w, grid_h, Image.INTERPOLATE_BILINEAR)

	var mm := _scan_min_max(small)
	var v_range := maxf(mm.y - mm.x, 0.000001)

	# Reconstruct vertices in UE world space, convert each to Godot space
	var verts := PackedVector3Array()
	var uvs := PackedVector2Array()
	verts.resize(grid_w * grid_h)
	uvs.resize(grid_w * grid_h)
	for py in grid_h:
		var v := float(py) / float(grid_h - 1)
		var ue_y := center.y - extent.y + v * 2.0 * extent.y
		for px in grid_w:
			var u := float(px) / float(grid_w - 1)
			var ue_x := center.x - extent.x + u * 2.0 * extent.x
			var hn := (small.get_pixel(px, py).r - mm.x) / v_range
			var ue_z := center.z - extent.z + hn * 2.0 * extent.z
			var i := py * grid_w + px
			verts[i] = Vector3(ue_y * 0.01, ue_z * 0.01, -ue_x * 0.01)
			uvs[i] = Vector2(u, v)

	# Normals via central differences (cross order chosen to point +Y upward)
	var normals := PackedVector3Array()
	normals.resize(grid_w * grid_h)
	for py in grid_h:
		for px in grid_w:
			var x0 := verts[py * grid_w + maxi(px - 1, 0)]
			var x1 := verts[py * grid_w + mini(px + 1, grid_w - 1)]
			var y0 := verts[maxi(py - 1, 0) * grid_w + px]
			var y1 := verts[mini(py + 1, grid_h - 1) * grid_w + px]
			normals[py * grid_w + px] = (y1 - y0).cross(x1 - x0).normalized()

	# Godot front faces wind clockwise; (v00, v10, v01) is clockwise seen from +Y
	var indices := PackedInt32Array()
	indices.resize((grid_w - 1) * (grid_h - 1) * 6)
	var k := 0
	for py in grid_h - 1:
		for px in grid_w - 1:
			var v00 := py * grid_w + px
			var v10 := py * grid_w + px + 1
			var v01 := (py + 1) * grid_w + px
			var v11 := (py + 1) * grid_w + px + 1
			indices[k] = v00; indices[k + 1] = v10; indices[k + 2] = v01
			indices[k + 3] = v10; indices[k + 4] = v11; indices[k + 5] = v01
			k += 6

	var arrays := []
	arrays.resize(Mesh.ARRAY_MAX)
	arrays[Mesh.ARRAY_VERTEX] = verts
	arrays[Mesh.ARRAY_NORMAL] = normals
	arrays[Mesh.ARRAY_TEX_UV] = uvs
	arrays[Mesh.ARRAY_INDEX] = indices
	var mesh := ArrayMesh.new()
	mesh.add_surface_from_arrays(Mesh.PRIMITIVE_TRIANGLES, arrays)
	mesh.surface_set_material(0, _build_terrain_material(ls, json_dir, warnings))

	var terrain_root := Node3D.new()
	terrain_root.name = ls_name
	_store_terrain_metadata(terrain_root, ls)
	Common.add_owned_child(root, terrain_root, scene_owner)

	var mesh_inst := MeshInstance3D.new()
	mesh_inst.name = "TerrainMesh"
	mesh_inst.mesh = mesh
	Common.add_owned_child(terrain_root, mesh_inst, scene_owner)

	var body := StaticBody3D.new()
	body.name = "TerrainCollision"
	Common.add_owned_child(terrain_root, body, scene_owner)
	var shape_node := CollisionShape3D.new()
	shape_node.shape = mesh.create_trimesh_shape()
	Common.add_owned_child(body, shape_node, scene_owner)

	if img.get_width() > grid_w or img.get_height() > grid_h:
		warnings.append("Landscape '%s': heightmap %dx%d downsampled to a %dx%d mesh grid (plugin-free fallback); install Terrain3D for full resolution."
			% [ls_name, img.get_width(), img.get_height(), grid_w, grid_h])
	return true


# --- material ---------------------------------------------------------------

func _build_terrain_material(ls: Dictionary, json_dir: String, warnings: PackedStringArray) -> Material:
	"""Splat-blends the exported paint layers, or falls back to plain earth.

	Unreal's landscape look lives in a layer-blend material graph whose
	layer->texture mapping is not readable from Python (paint layers are often
	named "1", "2", "3"), so the exporter ships each layer's weightmap plus its
	`layer_usage_debug_color`. The shader blends those tints, giving the real
	paint layout immediately, and exposes a texture slot per layer so assigning
	the actual ground textures is a drag-and-drop away."""
	var ls_name := Common.get_str(ls, "name", "Landscape")
	var packed := _pack_splatmap(ls, json_dir)
	if packed.is_empty():
		var plain := StandardMaterial3D.new()
		plain.albedo_color = Color(0.36, 0.42, 0.32)
		plain.roughness = 1.0
		return plain

	var splat_tex: ImageTexture = packed["texture"]
	var tints: Array = packed["tints"]
	var used: int = packed["count"]

	var shader := Shader.new()
	shader.code = _SPLAT_SHADER
	var mat := ShaderMaterial.new()
	mat.shader = shader
	mat.set_shader_parameter("splatmap", splat_tex)
	mat.set_shader_parameter("layer_count", used)
	mat.set_shader_parameter("layer_tiling", DEFAULT_LAYER_TILING)
	for i in MAX_SPLAT_LAYERS:
		mat.set_shader_parameter("layer%d_tint" % i, tints[i] if i < tints.size() else Color.WHITE)

	var names: Array = packed["names"]
	warnings.append(("Landscape '%s': terrain material splat-blends %d paint layer(s) (%s) using each "
		+ "layer's Unreal debug colour. Assign layer0..%d_albedo on the TerrainMesh material to texture them.")
		% [ls_name, used, ", ".join(names), used - 1])
	return mat


func _pack_splatmap(ls: Dictionary, json_dir: String) -> Dictionary:
	"""Packs up to four layer weightmaps into the RGBA channels of one texture.

	Returns {} when no weightmap could be loaded. Weightmaps share the
	heightmap's UV mapping (image U/V follow Unreal X/Y over the same bounds),
	so the mesh's UVs address them directly, whatever their resolution."""
	var layers = ls.get("layers", [])
	if not (layers is Array) or layers.is_empty():
		return {}

	var images: Array[Image] = []
	var tints: Array = []
	var names: Array = []
	for layer in layers:
		if not (layer is Dictionary):
			continue
		var rel := Common.get_str(layer, "weightmap_file", "")
		if rel == "":
			continue
		var path := Common.resolve_json_relative(json_dir, rel)
		if path == "":
			continue
		var wimg := Common.load_image_file(path)
		if wimg == null:
			continue
		if wimg.is_compressed():
			wimg.decompress()
		if wimg.get_width() != SPLAT_RESOLUTION or wimg.get_height() != SPLAT_RESOLUTION:
			wimg.resize(SPLAT_RESOLUTION, SPLAT_RESOLUTION, Image.INTERPOLATE_BILINEAR)
		images.append(wimg)
		tints.append(Common.color_from_array(layer.get("debug_color"), Color(0.5, 0.5, 0.5)))
		names.append(Common.get_str(layer, "name", "layer%d" % images.size()))
		if images.size() >= MAX_SPLAT_LAYERS:
			break
	if images.is_empty():
		return {}

	var packed := Image.create(SPLAT_RESOLUTION, SPLAT_RESOLUTION, false, Image.FORMAT_RGBAF)
	for y in SPLAT_RESOLUTION:
		for x in SPLAT_RESOLUTION:
			var c := Color(0.0, 0.0, 0.0, 0.0)
			for i in images.size():
				c[i] = images[i].get_pixel(x, y).r
			packed.set_pixel(x, y, c)
	while tints.size() < MAX_SPLAT_LAYERS:
		tints.append(Color.WHITE)
	return {
		"texture": ImageTexture.create_from_image(packed),
		"tints": tints,
		"names": names,
		"count": images.size(),
	}


const _SPLAT_SHADER := """shader_type spatial;
// Terrain splat blend generated by the Unreal -> Godot importer.
// splatmap RGBA = the first four Unreal paint layers' weights, in export order.
// Each layer renders as tint * albedo; albedo defaults to white, so an
// unassigned layer shows its Unreal debug colour. Set the tint to white once
// you assign a real ground texture.
render_mode cull_back, diffuse_burley, specular_schlick_ggx;

uniform sampler2D splatmap : hint_default_black, filter_linear;
uniform int layer_count = 1;
uniform float layer_tiling = 64.0;

uniform vec4 layer0_tint : source_color = vec4(1.0);
uniform vec4 layer1_tint : source_color = vec4(1.0);
uniform vec4 layer2_tint : source_color = vec4(1.0);
uniform vec4 layer3_tint : source_color = vec4(1.0);

uniform sampler2D layer0_albedo : source_color, hint_default_white;
uniform sampler2D layer1_albedo : source_color, hint_default_white;
uniform sampler2D layer2_albedo : source_color, hint_default_white;
uniform sampler2D layer3_albedo : source_color, hint_default_white;

uniform float roughness_value : hint_range(0.0, 1.0) = 1.0;

void fragment() {
	vec4 w = texture(splatmap, UV);
	// Layers past layer_count carry no data; zero them so the blend stays honest.
	if (layer_count < 4) { w.a = 0.0; }
	if (layer_count < 3) { w.b = 0.0; }
	if (layer_count < 2) { w.g = 0.0; }
	float total = w.r + w.g + w.b + w.a;
	// Unpainted texels (total 0) fall back to layer 0 rather than going black.
	if (total <= 0.0001) { w = vec4(1.0, 0.0, 0.0, 0.0); total = 1.0; }
	w /= total;

	vec2 tuv = UV * layer_tiling;
	vec3 col = w.r * layer0_tint.rgb * texture(layer0_albedo, tuv).rgb;
	col += w.g * layer1_tint.rgb * texture(layer1_albedo, tuv).rgb;
	col += w.b * layer2_tint.rgb * texture(layer2_albedo, tuv).rgb;
	col += w.a * layer3_tint.rgb * texture(layer3_albedo, tuv).rgb;

	ALBEDO = col;
	ROUGHNESS = roughness_value;
}
"""


# --- Terrain3D --------------------------------------------------------------

func _try_terrain3d(ls: Dictionary, img: Image, root: Node, scene_owner: Node,
		warnings: PackedStringArray, options: Dictionary) -> bool:
	"""Imports into Terrain3D. Validated against Terrain3D 1.0.2 / Godot 4.7.1.

	Three plugin behaviours drive the shape of this function, all measured
	rather than assumed:
	  * `Terrain3D.data` does not exist until one frame AFTER the node enters
	    the tree — no property order avoids it — hence the await.
	  * The plugin refuses to initialise unless `data_directory` names a folder
	    that already exists.
	  * `import_images` snaps the requested position DOWN to the region grid,
	    so a landscape whose corner is not region-aligned would land up to one
	    region (256 m by default) away. The image is pre-padded to the grid
	    instead, which puts the terrain exactly where Unreal had it."""
	if not ClassDB.class_exists("Terrain3D"):
		return false
	var terrain = ClassDB.instantiate("Terrain3D")
	if terrain == null or not (terrain is Node3D):
		return false

	var ls_name := Common.get_str(ls, "name", "Landscape")
	var bounds := _ue_bounds(ls)
	var center: Vector3 = bounds[0]
	var extent: Vector3 = bounds[1]

	# Terrain3D spaces its vertices `vertex_spacing` metres apart, so the spacing
	# must describe the HEIGHTMAP's texels, not Unreal's landscape quads: the
	# exported heightmap is routinely coarser than the landscape (the CPU
	# fallback caps its grid), and using the raw Unreal quad size shrank a
	# 4032 m landscape to a 257 m one.
	var height_img := _to_terrain3d_image(img, center, extent)
	# Image +X spans the UE-Y extent, image +Z spans the UE-X extent.
	var spacing_x := (extent.y * 2.0 * 0.01) / maxf(float(height_img.get_width() - 1), 1.0)
	var spacing_z := (extent.x * 2.0 * 0.01) / maxf(float(height_img.get_height() - 1), 1.0)
	var spacing := maxf((spacing_x + spacing_z) * 0.5, 0.001)
	if absf(spacing_x - spacing_z) > 0.01 * spacing:
		warnings.append(("Landscape '%s': non-square heightmap texels (%.3f x %.3f m); Terrain3D has a "
			+ "single vertex spacing, so %.3f m is used and the terrain is slightly stretched.")
			% [ls_name, spacing_x, spacing_z, spacing])
	if terrain.has_method("set_vertex_spacing"):
		terrain.set_vertex_spacing(spacing)

	var data_dir := str(options.get("terrain3d_data_dir", "res://terrain_data")).path_join(ls_name)
	if DirAccess.make_dir_recursive_absolute(data_dir) != OK and not DirAccess.dir_exists_absolute(data_dir):
		terrain.free()
		warnings.append("Landscape '%s': could not create the Terrain3D data directory '%s'." % [ls_name, data_dir])
		return false
	terrain.set_data_directory(data_dir)

	terrain.name = ls_name + "_Terrain3D"
	Common.add_owned_child(root, terrain, scene_owner)
	var tree: SceneTree = terrain.get_tree()
	if tree == null:
		tree = Engine.get_main_loop() as SceneTree
	if tree == null:
		_discard(terrain, root)
		warnings.append("Landscape '%s': Terrain3D needs the target scene to be inside the tree." % ls_name)
		return false
	await tree.process_frame

	var data = terrain.get_data() if terrain.has_method("get_data") else null
	var storage = null
	if data == null and ClassDB.class_exists("Terrain3DStorage") and terrain.has_method("set_storage"):
		# Terrain3D 0.9.x kept the regions in a separate resource.
		storage = ClassDB.instantiate("Terrain3DStorage")
		if storage != null:
			terrain.set_storage(storage)
			data = storage
	if data == null or not data.has_method("import_images"):
		_discard(terrain, root)
		return false

	# Godot-space (min X, min Z) corner of the landscape footprint — where the
	# image's first texel belongs.
	var corner := Vector3((center.y - extent.y) * 0.01, 0.0, -(center.x + extent.x) * 0.01)
	var region_size := 256
	if terrain.has_method("get_region_size"):
		region_size = maxi(int(terrain.get_region_size()), 1)
	var aligned := _align_to_region_grid(height_img, corner, region_size * spacing, spacing)

	data.call("import_images", [aligned["image"], null, null], aligned["origin"], 0.0, 1.0)
	if data.has_method("get_region_count") and int(data.call("get_region_count")) <= 0:
		_discard(terrain, root)
		return false

	# import_images only populates memory. Terrain3D's own editor plugin writes
	# the regions out when the user saves the scene, but nothing guarantees that
	# — and without it the data directory stays EMPTY, so reopening the scene
	# gives a Terrain3D node with zero regions and a flat, checkered surface.
	# Persist them here so the import is complete on its own.
	if data.has_method("save_directory"):
		data.call("save_directory", data_dir)

	_store_terrain_metadata(terrain, ls)
	warnings.append(("Landscape '%s' imported via Terrain3D at %.2f m vertex spacing, %d region(s) "
		+ "saved to '%s'. It renders as a flat checkerboard until you assign a Terrain3DAssets "
		+ "resource — that is Terrain3D's untextured default, not a failed import; the exported "
		+ "weightmaps in terrain/ carry the Unreal paint layers.")
		% [ls_name, spacing, int(data.call("get_region_count")) if data.has_method("get_region_count") else 0,
			data_dir])
	if int(aligned.get("pad_x", 0)) > 0 or int(aligned.get("pad_z", 0)) > 0:
		warnings.append(("Landscape '%s': Terrain3D snaps imports to its region grid, so %.0f x %.0f m "
			+ "of flat filler at the landscape's lowest height was added on the -X/-Z sides to keep "
			+ "the real terrain at its Unreal world position. The Unreal footprint itself starts at "
			+ "(%.0f, %.0f).") % [ls_name, int(aligned.get("pad_x", 0)) * spacing,
			int(aligned.get("pad_z", 0)) * spacing, corner.x, corner.z])
	return true


func _discard(node: Node, root: Node) -> void:
	if node.get_parent() == root:
		root.remove_child(node)
	node.free()


func _align_to_region_grid(img: Image, corner: Vector3, region_extent_m: float, spacing: float) -> Dictionary:
	"""Pads the heightmap so its data lands exactly at `corner` despite
	import_images snapping the import origin down to the region grid.

	Returns {"image": Image, "origin": Vector3} — import at `origin` and the
	original pixels come to rest on `corner`."""
	if region_extent_m <= 0.0:
		return {"image": img, "origin": corner}
	var snapped := Vector3(floorf(corner.x / region_extent_m) * region_extent_m, 0.0,
		floorf(corner.z / region_extent_m) * region_extent_m)
	var pad_x := int(round((corner.x - snapped.x) / spacing))
	var pad_z := int(round((corner.z - snapped.z) / spacing))
	if pad_x <= 0 and pad_z <= 0:
		return {"image": img, "origin": corner, "pad_x": 0, "pad_z": 0}
	pad_x = maxi(pad_x, 0)
	pad_z = maxi(pad_z, 0)
	# Padded texels sit outside the Unreal footprint; hold them at the lowest
	# exported height so they read as ground rather than a wall.
	var lowest := _scan_min_max(img).x
	var padded := Image.create(img.get_width() + pad_x, img.get_height() + pad_z, false, img.get_format())
	padded.fill(Color(lowest, 0.0, 0.0))
	padded.blit_rect(img, Rect2i(Vector2i.ZERO, img.get_size()), Vector2i(pad_x, pad_z))
	return {"image": padded, "origin": snapped, "pad_x": pad_x, "pad_z": pad_z}


func _to_terrain3d_image(img: Image, center: Vector3, extent: Vector3) -> Image:
	"""Converts the exported heightmap into an absolute-height FORMAT_RF image
	rotated into Terrain3D's frame.

	Terrain3D maps image +X -> world +X and image +Y -> world +Z, while the
	source maps +U -> Godot -Z and +V -> Godot +X. So new(ix, iz) = src(W-1-iz, ix),
	and the result is the transpose-and-flip of the source."""
	var src := img.duplicate() as Image
	var cap_w := mini(src.get_width(), MAX_TERRAIN3D_RES)
	var cap_h := mini(src.get_height(), MAX_TERRAIN3D_RES)
	if src.get_width() != cap_w or src.get_height() != cap_h:
		src.resize(cap_w, cap_h, Image.INTERPOLATE_BILINEAR)
	var mm := _scan_min_max(src)
	var v_range := maxf(mm.y - mm.x, 0.000001)
	var h_min := (center.z - extent.z) * 0.01
	var h_span := extent.z * 2.0 * 0.01

	var out := Image.create(src.get_height(), src.get_width(), false, Image.FORMAT_RF)
	for iz in out.get_height():
		for ix in out.get_width():
			var hn := (src.get_pixel(src.get_width() - 1 - iz, ix).r - mm.x) / v_range
			out.set_pixel(ix, iz, Color(h_min + hn * h_span, 0.0, 0.0))
	return out


func _store_terrain_metadata(node: Node, ls: Dictionary) -> void:
	"""Keeps source file references on the node so the terrain can be rebuilt
	with a dedicated terrain plugin later."""
	node.set_meta("unreal_landscape", true)
	node.set_meta("heightmap_file", Common.get_str(ls, "heightmap_file", ""))
	var res = ls.get("heightmap_resolution", [])
	if res is Array and res.size() >= 2:
		node.set_meta("heightmap_resolution", Vector2i(int(res[0]), int(res[1])))
	node.set_meta("height_range_m", ls.get("height_range_m", []))
	node.set_meta("vertex_spacing_m", float(ls.get("vertex_spacing_m", 1.0)))
	var layer_files := PackedStringArray()
	var layer_names := PackedStringArray()
	var layers = ls.get("layers", [])
	if layers is Array:
		for layer in layers:
			if layer is Dictionary:
				layer_names.append(Common.get_str(layer, "name", ""))
				layer_files.append(Common.get_str(layer, "weightmap_file", ""))
	if not layer_names.is_empty():
		node.set_meta("weightmap_files", layer_files)
		node.set_meta("weightmap_layers", layer_names)
