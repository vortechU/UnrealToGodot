@tool
extends RefCounted

# ==============================================================================
# Rebuilds Unreal foliage / instanced static meshes as MultiMeshInstance3D
# nodes. Consumes the "foliage" schema section: 12 floats per instance in
# world-space Godot layout — basis column X, column Y, column Z, then origin.
# See docs/SCHEMA_V2.md.
#
# Beyond the transforms, each entry carries the component's rendering state:
# visibility, shadow casting and UE's per-instance cull distances (which become
# a Godot visibility range). These are duplicated in tscn_writer.py
# (_build_foliage), which builds the same MultiMeshInstance3D for the .tscn
# export path — any change here must be mirrored there, and
# tests/test_tscn_writer.py pins the two against each other.
# ==============================================================================

const Common = preload("res://addons/unreal_importer/import_common.gd")


func apply(data: Dictionary, root: Node, scene_owner: Node, options: Dictionary) -> Dictionary:
	var created := 0
	var warnings := PackedStringArray()

	if not options.get("apply_foliage", true):
		return {"created": 0, "warnings": warnings}
	var entries = data.get("foliage", [])
	if not (entries is Array) or entries.is_empty():
		return {"created": 0, "warnings": warnings}

	var models_folder := str(options.get("models_folder", "res://models/"))
	var meshes_lib: Dictionary = data.get("meshes", {}) if data.get("meshes") is Dictionary else {}
	# Optional: the main importer passes itself so foliage meshes get the same
	# generated PBR materials as regular instances (glTF is exported unlit).
	var material_helper = options.get("material_helper")

	var container := Node3D.new()
	container.name = "UnrealFoliage"
	container.add_to_group(Common.NAV_SOURCE_GROUP, true)
	Common.add_owned_child(root, container, scene_owner)

	for entry in entries:
		if not (entry is Dictionary):
			continue
		var entry_name := Common.get_str(entry, "name", "Foliage")
		var mesh_key := Common.get_str(entry, "mesh_key", "")
		var mesh_name := Common.get_str(entry, "mesh_name", mesh_key)

		var model_path := Common.find_model_path(models_folder, mesh_key)
		if model_path == "" and mesh_name != mesh_key:
			model_path = Common.find_model_path(models_folder, mesh_name)
		if model_path == "":
			warnings.append("Foliage '%s': model '%s' not found in %s" % [entry_name, mesh_key, models_folder])
			var marker := Marker3D.new()
			marker.name = "MISSING_FOLIAGE_" + mesh_key
			Common.add_owned_child(container, marker, scene_owner)
			continue

		var scene_inst := Common.instantiate_model(model_path)
		if scene_inst == null:
			warnings.append("Foliage '%s': failed to instance model %s" % [entry_name, model_path])
			continue
		var mesh_inst := Common.find_first_mesh(scene_inst)
		if mesh_inst == null or mesh_inst.mesh == null:
			warnings.append("Foliage '%s': no MeshInstance3D found inside %s" % [entry_name, model_path])
			scene_inst.free()
			continue
		var mesh: Mesh = mesh_inst.mesh
		scene_inst.free()

		var overrides = entry.get("material_overrides")
		var has_overrides: bool = overrides is Array and not (overrides as Array).is_empty()
		var mesh_lib_entry = meshes_lib.get(mesh_key)
		var has_lib_mats: bool = (mesh_lib_entry is Dictionary
			and mesh_lib_entry.get("materials") is Array
			and not (mesh_lib_entry.get("materials") as Array).is_empty())
		if material_helper != null and (has_overrides or has_lib_mats):
			# Duplicate the external glTF Mesh resource before setting surface
			# materials: mutating the imported Mesh in place pollutes Godot's
			# resource cache and is discarded when the scene is saved to .tscn.
			mesh = mesh.duplicate()

		var surface_slots := _apply_library_materials(mesh, mesh_key, meshes_lib, material_helper)
		# A foliage type pointing at an overridden material (the usual way a pack
		# ships a winter/dead variant of one mesh) used to render with the base
		# mesh's material instead.
		_apply_material_overrides(mesh, overrides, material_helper, surface_slots)

		var floats = entry.get("godot_transforms", [])
		if not (floats is Array):
			continue
		var count := int(floats.size() / 12)
		if count <= 0:
			continue

		var chunks := _cull_chunks(floats, count, Common.get_num(entry, "cull_end_m", 0.0))
		for chunk_idx in chunks.size():
			var members: Array = chunks[chunk_idx]
			var mm := MultiMesh.new()
			mm.transform_format = MultiMesh.TRANSFORM_3D
			mm.mesh = mesh
			mm.instance_count = members.size()
			var bounds := AABB(_origin_at(floats, members[0]), Vector3.ZERO)
			for i in members.size():
				var o: int = members[i] * 12
				var basis := Basis(
					Vector3(floats[o + 0], floats[o + 1], floats[o + 2]),
					Vector3(floats[o + 3], floats[o + 4], floats[o + 5]),
					Vector3(floats[o + 6], floats[o + 7], floats[o + 8])
				)
				var origin := _origin_at(floats, members[i])
				bounds = bounds.expand(origin)
				# Same glTF-vs-layout axis correction as the layout importer: the packed
				# basis is in the placement convention, the mesh is in the glTF one.
				mm.set_instance_transform(i, Common.gltf_mesh_placement(Transform3D(basis, origin)))

			var mmi := MultiMeshInstance3D.new()
			mmi.name = entry_name if chunk_idx == 0 else "%s_%d" % [entry_name, chunk_idx + 1]
			mmi.multimesh = mm
			mmi.visible = bool(entry.get("visible", true))
			# Grass is routinely authored shadowless in Unreal; importing it ON is a
			# visual AND a performance regression.
			mmi.cast_shadow = (GeometryInstance3D.SHADOW_CASTING_SETTING_ON
				if bool(entry.get("cast_shadow", true))
				else GeometryInstance3D.SHADOW_CASTING_SETTING_OFF)
			_apply_cull_range(mmi, entry, bounds.size.length() * 0.5)
			mmi.set_meta("unreal_foliage", true)
			mmi.set_meta("unreal_mesh_key", mesh_key)
			mmi.set_meta("unreal_foliage_source", Common.get_str(entry, "source", "foliage"))
			Common.add_owned_child(container, mmi, scene_owner)
			created += 1

	if container.get_child_count() == 0:
		container.queue_free()
	return {"created": created, "warnings": warnings}


static func _origin_at(floats: Array, instance: int) -> Vector3:
	var o := instance * 12
	return Vector3(floats[o + 9], floats[o + 10], floats[o + 11])


static func _cull_chunks(floats: Array, count: int, cull_end: float) -> Array:
	"""Splits a distance-culled group into grid cells of instance indices.

	UE culls each instance; Godot culls a whole MultiMesh by the distance to its
	centre, so one MultiMesh across a level would vanish under the camera's feet.
	An unculled group stays whole. Mirrors tscn_writer.py's _cull_chunks."""
	if cull_end <= 0.0:
		return [range(count)]
	var bounds := AABB(_origin_at(floats, 0), Vector3.ZERO)
	for i in count:
		bounds = bounds.expand(_origin_at(floats, i))
	# Cells well inside the cull distance, but never more than ~32 per side.
	var cell := maxf(cull_end * 0.5, maxf(bounds.size.x, bounds.size.z) / 32.0)
	var cells := {}
	for i in count:
		var local := _origin_at(floats, i) - bounds.position
		var key := Vector2i(int(local.x / cell), int(local.z / cell))
		if not cells.has(key):
			cells[key] = []
		cells[key].append(i)
	return cells.values()


func _apply_cull_range(mmi: MultiMeshInstance3D, entry: Dictionary, chunk_radius: float = 0.0) -> void:
	"""Maps UE's per-instance cull distances onto Godot's visibility range.

	UE hides instances past instance_end_cull_distance and fades them from
	instance_start_cull_distance, which is visibility_range_end with a matching
	end margin. Godot measures to the MultiMesh's centre, so the range grows by the
	chunk's radius: an instance nearer than UE's distance is never hidden. With no
	start distance UE pops rather than fades, so the fade mode stays DISABLED and
	the pop is reproduced rather than smoothed."""
	var cull_end = entry.get("cull_end_m")
	if not (cull_end is float or cull_end is int) or float(cull_end) <= 0.0:
		return
	mmi.visibility_range_end = float(cull_end) + chunk_radius

	var cull_begin = entry.get("cull_begin_m")
	if (cull_begin is float or cull_begin is int) and float(cull_begin) > 0.0 \
			and float(cull_begin) < float(cull_end):
		mmi.visibility_range_end_margin = float(cull_end) - float(cull_begin)
		mmi.visibility_range_fade_mode = GeometryInstance3D.VISIBILITY_RANGE_FADE_SELF


func _find_material_entry(mat_list: Array, mat_name: String, slot_idx: int, claimed: Dictionary) -> Dictionary:
	return Common.find_material_entry(mat_list, mat_name, slot_idx, claimed)


func _apply_material_overrides(mesh: Mesh, overrides, material_helper, surface_slots: Array[int] = []) -> void:
	"""Applies the component's override_materials onto the shared mesh surfaces,
	using the same generated-PBR cache the rest of the importer uses."""
	if material_helper == null or not (material_helper is Object):
		return
	if not material_helper.has_method("get_or_create_material"):
		return
	if not (overrides is Array) or (overrides as Array).is_empty():
		return
	for surf_idx in range(mesh.get_surface_count()):
		var unreal_slot: int = surface_slots[surf_idx] if surf_idx < surface_slots.size() else surf_idx
		var entry: Dictionary = {}
		for ov in overrides:
			if ov is Dictionary and int(ov.get("slot_index", -1)) == unreal_slot:
				entry = ov
				break
		if entry.is_empty():
			continue
		var params = entry.get("parameters")
		if not (params is Dictionary):
			continue
		var override_name := Common.get_str(entry, "material_name", "None")
		var existing_mat := mesh.surface_get_material(surf_idx)
		var base_mat: Material = existing_mat if (existing_mat and override_name != "" and existing_mat.resource_name.to_lower() == override_name.to_lower()) else null
		var mat = material_helper.get_or_create_material(
			override_name,
			Common.get_str(entry, "material_path", "None"),
			params,
			base_mat
		)
		if mat:
			mesh.surface_set_material(surf_idx, mat)


func _apply_library_materials(mesh: Mesh, mesh_key: String, meshes_lib: Dictionary, material_helper) -> Array[int]:
	"""Binds generated PBR materials onto the mesh surfaces using the shared
	material cache of the main importer (passed via options.material_helper).
	Returns the resolved Unreal slot index for each glTF surface index."""
	var surface_slots: Array[int] = []
	var surface_count := mesh.get_surface_count()
	for s in surface_count:
		surface_slots.append(s)
	if material_helper == null or not (material_helper is Object):
		return surface_slots
	if not material_helper.has_method("get_or_create_material"):
		return surface_slots
	var mesh_data = meshes_lib.get(mesh_key)
	if not (mesh_data is Dictionary):
		return surface_slots
	var materials = mesh_data.get("materials", [])
	if not (materials is Array):
		return surface_slots
	var claimed := {}
	for surf_idx in range(surface_count):
		var existing_mat := mesh.surface_get_material(surf_idx)
		var mat_name := existing_mat.resource_name if existing_mat else ""
		var mat_entry := _find_material_entry(materials, mat_name, surf_idx, claimed)
		if mat_entry.is_empty():
			continue
		surface_slots[surf_idx] = int(mat_entry.get("slot_index", surf_idx))
		var params = mat_entry.get("parameters")
		if not (params is Dictionary):
			continue
		var mat = material_helper.get_or_create_material(
			Common.get_str(mat_entry, "material_name", "None"),
			Common.get_str(mat_entry, "material_path", "None"),
			params,
			existing_mat
		)
		if mat:
			mesh.surface_set_material(surf_idx, mat)
	return surface_slots
