@tool
extends RefCounted

# ==============================================================================
# Rebuilds Unreal Landscapes in Godot. Consumes the "landscapes" schema section
# (see docs/SCHEMA_V2.md).
#
# Height contract: the exported heightmap is treated as RELATIVE — the image is
# normalized by its own min/max, then rescaled into height_range_m (world-space
# meters, derived from the landscape's Unreal bounds). Placement uses ue_bounds
# so vertices are reconstructed in Unreal world space and converted per-vertex
# to Godot space, making the axis mapping correct by construction:
#     image U (+X) follows Unreal +X  ->  Godot -Z
#     image V (+Y) follows Unreal +Y  ->  Godot +X
#
# Build modes:
#   "terrain3d" / "auto": best-effort import into the Terrain3D plugin when the
#       plugin is present (validated against Terrain3D 0.9.x API); falls back
#       to "mesh" on any failure.
#   "hterrain": not automated yet — falls back to "mesh" with a warning.
#   "mesh": plugin-free fallback — an ArrayMesh grid (max 256x256 quads) with
#       trimesh collision. Weightmap/layer file paths are stored as metadata so
#       the terrain can be rebuilt with a real terrain plugin later.
# ==============================================================================

const Common = preload("res://addons/unreal_importer/import_common.gd")

const MAX_MESH_GRID := 257          # vertices per side for the mesh fallback
const MAX_TERRAIN3D_RES := 1025     # cap for Terrain3D image conversion


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
		# Unreal's render-target export can silently produce an empty image
		# (e.g. no GPU in a commandlet). Import it anyway, but say so loudly —
		# a flat terrain with no warning looks like an importer bug.
		if _is_constant_heightmap(img):
			warnings.append(("Landscape '%s': heightmap has NO height variation — the "
				+ "Unreal-side export likely wrote an empty render target. The terrain "
				+ "will import flat; re-export the level from Unreal.") % ls_name)

		var built := false
		if mode == "auto" or mode == "terrain3d":
			built = _try_terrain3d(ls, img, root, scene_owner, warnings)
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
	# The loader sniffs file content (Unreal writes PNG bytes under .exr names);
	# anything it noticed about the file belongs in the import report.
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
	an Unreal export that rendered nothing. Sampled coarsely: 4K x 4K per-pixel
	GDScript scans are far too slow for a load-time sanity check."""
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

	var material := StandardMaterial3D.new()
	material.albedo_color = Color(0.36, 0.42, 0.32)
	material.roughness = 1.0
	mesh.surface_set_material(0, material)

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

	var layers = ls.get("layers", [])
	if layers is Array and not layers.is_empty():
		warnings.append("Landscape '%s': %d splatmap layer(s) exported to terrain/ — a terrain plugin or custom shader is needed for full layer blending (paths stored in node metadata)." % [ls_name, layers.size()])
	return true


func _try_terrain3d(ls: Dictionary, img: Image, root: Node, scene_owner: Node, warnings: PackedStringArray) -> bool:
	if not ClassDB.class_exists("Terrain3D"):
		return false
	var terrain = ClassDB.instantiate("Terrain3D")
	if terrain == null or not (terrain is Node3D):
		return false

	var storage = null
	if ClassDB.class_exists("Terrain3DStorage"):
		storage = ClassDB.instantiate("Terrain3DStorage")
	if storage == null or not terrain.has_method("set_storage") or not storage.has_method("import_images"):
		if storage:
			storage = null
		terrain.free()
		return false
	terrain.set_storage(storage)

	var bounds := _ue_bounds(ls)
	var center: Vector3 = bounds[0]
	var extent: Vector3 = bounds[1]

	# Convert to an absolute-height FORMAT_RF image, rotated into Godot's frame:
	# Terrain3D maps image +X -> world +X and +Y -> world +Z, while our source
	# maps +X -> Godot -Z and +Y -> Godot +X. new(ix, iz) = src(w-1-iz, ix).
	var src := img.duplicate() as Image
	var cap_w := mini(src.get_width(), MAX_TERRAIN3D_RES)
	var cap_h := mini(src.get_height(), MAX_TERRAIN3D_RES)
	if src.get_width() != cap_w or src.get_height() != cap_h:
		src.resize(cap_w, cap_h, Image.INTERPOLATE_BILINEAR)
	var mm := _scan_min_max(src)
	var v_range := maxf(mm.y - mm.x, 0.000001)
	var h_min := (center.z - extent.z) * 0.01
	var h_span := extent.z * 2.0 * 0.01

	var height_img := Image.create(src.get_height(), src.get_width(), false, Image.FORMAT_RF)
	for iz in height_img.get_height():
		for ix in height_img.get_width():
			var hn := (src.get_pixel(src.get_width() - 1 - iz, ix).r - mm.x) / v_range
			height_img.set_pixel(ix, iz, Color(h_min + hn * h_span, 0.0, 0.0))

	var world_center := Vector3(center.y * 0.01, 0.0, -center.x * 0.01)
	storage.call("import_images", [height_img, null, null], world_center, 0.0, 1.0)

	# Verify something was actually imported when the API allows it
	if storage.has_method("get_region_count") and int(storage.call("get_region_count")) <= 0:
		terrain.free()
		return false

	terrain.name = Common.get_str(ls, "name", "Landscape") + "_Terrain3D"
	_store_terrain_metadata(terrain, ls)
	Common.add_owned_child(root, terrain, scene_owner)
	warnings.append("Landscape '%s' imported via Terrain3D (assign a Terrain3D material/assets to texture it)." % Common.get_str(ls, "name", "Landscape"))
	return true


func _store_terrain_metadata(node: Node, ls: Dictionary) -> void:
	"""Keeps source file references on the node so the terrain can be rebuilt
	with a dedicated terrain plugin later."""
	node.set_meta("unreal_landscape", true)
	node.set_meta("heightmap_file", Common.get_str(ls, "heightmap_file", ""))
	var res = ls.get("heightmap_resolution", [])
	if res is Array and res.size() >= 2:
		node.set_meta("heightmap_resolution", Vector2i(int(res[0]), int(res[1])))
	node.set_meta("height_range_m", ls.get("height_range_m", []))
	var layer_files := PackedStringArray()
	var layer_names := PackedStringArray()
	var layers = ls.get("layers", [])
	if layers is Array:
		for layer in layers:
			if layer is Dictionary:
				layer_names.append(Common.get_str(layer, "name", ""))
				layer_files.append(Common.get_str(layer, "weightmap_file", ""))
	if not layer_files.is_empty():
		node.set_meta("weightmap_files", layer_files)
		node.set_meta("weightmap_layers", layer_names)
