@tool
extends RefCounted

# ==============================================================================
# Rebuilds Unreal foliage / instanced static meshes as MultiMeshInstance3D
# nodes. Consumes the "foliage" schema section: 12 floats per instance in
# world-space Godot layout — basis column X, column Y, column Z, then origin.
# See docs/SCHEMA_V2.md.
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

		_apply_library_materials(mesh, mesh_key, meshes_lib, material_helper)

		var floats = entry.get("godot_transforms", [])
		if not (floats is Array):
			continue
		var count := int(floats.size() / 12)
		if count <= 0:
			continue

		var mm := MultiMesh.new()
		mm.transform_format = MultiMesh.TRANSFORM_3D
		mm.mesh = mesh
		mm.instance_count = count
		for i in count:
			var o := i * 12
			var basis := Basis(
				Vector3(floats[o + 0], floats[o + 1], floats[o + 2]),
				Vector3(floats[o + 3], floats[o + 4], floats[o + 5]),
				Vector3(floats[o + 6], floats[o + 7], floats[o + 8])
			)
			var origin := Vector3(floats[o + 9], floats[o + 10], floats[o + 11])
			mm.set_instance_transform(i, Transform3D(basis, origin))

		var mmi := MultiMeshInstance3D.new()
		mmi.name = entry_name
		mmi.multimesh = mm
		mmi.set_meta("unreal_foliage_source", Common.get_str(entry, "source", "foliage"))
		Common.add_owned_child(container, mmi, scene_owner)
		created += 1

	if created == 0:
		container.queue_free()
	return {"created": created, "warnings": warnings}


func _apply_library_materials(mesh: Mesh, mesh_key: String, meshes_lib: Dictionary, material_helper) -> void:
	"""Binds generated PBR materials onto the mesh surfaces using the shared
	material cache of the main importer (passed via options.material_helper)."""
	if material_helper == null or not (material_helper is Object):
		return
	if not material_helper.has_method("get_or_create_material"):
		return
	var mesh_data = meshes_lib.get(mesh_key)
	if not (mesh_data is Dictionary):
		return
	var materials = mesh_data.get("materials", [])
	if not (materials is Array):
		return
	for mat_entry in materials:
		if not (mat_entry is Dictionary):
			continue
		var slot := int(mat_entry.get("slot_index", -1))
		if slot < 0 or slot >= mesh.get_surface_count():
			continue
		var params = mat_entry.get("parameters")
		if not (params is Dictionary):
			continue
		var mat = material_helper.get_or_create_material(
			Common.get_str(mat_entry, "material_name", "None"),
			Common.get_str(mat_entry, "material_path", "None"),
			params,
			mesh.surface_get_material(slot)
		)
		if mat:
			mesh.surface_set_material(slot, mat)
