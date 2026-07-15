@tool
extends RefCounted

# ==============================================================================
# Applies exported Unreal navigation volumes (NavigationRegion3D) and gameplay
# metadata (actor tags / Blueprint variables -> node metadata).
# See docs/SCHEMA_V2.md ("navigation" and actor tags/properties sections).
# ==============================================================================

const Common = preload("res://addons/unreal_importer/import_common.gd")


func apply(data: Dictionary, root: Node, scene_owner: Node, options: Dictionary) -> Dictionary:
	var created := 0
	var warnings := PackedStringArray()

	if not options.get("apply_navigation", true):
		return {"created": 0, "warnings": warnings}

	var nav = data.get("navigation")
	if nav == null or not (nav is Dictionary):
		return {"created": 0, "warnings": warnings}
	var volumes = nav.get("bounds_volumes", [])
	if not (volumes is Array) or volumes.is_empty():
		return {"created": 0, "warnings": warnings}

	var container := Node3D.new()
	container.name = "UnrealNavigation"
	Common.add_owned_child(root, container, scene_owner)

	var do_bake := bool(options.get("navigation_bake", false))

	for vol in volumes:
		if not (vol is Dictionary):
			continue
		var region := NavigationRegion3D.new()
		region.name = Common.get_str(vol, "name", "NavRegion")
		region.transform = Common.get_transform_from_dict(vol.get("godot_transform", {}))

		var navmesh := NavigationMesh.new()
		navmesh.agent_radius = maxf(0.05, Common.get_num(nav, "agent_radius_m", 0.35))
		navmesh.agent_height = maxf(0.1, Common.get_num(nav, "agent_height_m", 1.92))
		navmesh.agent_max_climb = maxf(0.0, Common.get_num(nav, "agent_max_step_height_m", 0.35))
		navmesh.agent_max_slope = clampf(Common.get_num(nav, "max_slope_deg", 44.0), 0.1, 89.9)
		navmesh.cell_size = clampf(Common.get_num(nav, "cell_size_m", 0.25), 0.01, 1.0)
		navmesh.geometry_parsed_geometry_type = NavigationMesh.PARSED_GEOMETRY_BOTH

		# Confine baking to the exported bounds volume (AABB is centered on the
		# region node, which sits at the volume's transform).
		var extent := Common.vec3_from_array(vol.get("extent_m"), Vector3(5.0, 5.0, 5.0)).abs()
		if extent.length() > 0.01:
			navmesh.filter_baking_aabb = AABB(-extent, extent * 2.0)

		region.navigation_mesh = navmesh
		Common.add_owned_child(container, region, scene_owner)
		created += 1

		if do_bake:
			# Synchronous in-editor bake; requires the imported geometry to
			# already be inside the tree (the dock imports geometry first).
			region.bake_navigation_mesh(false)

	if created == 0:
		container.queue_free()
	elif not do_bake:
		warnings.append("Navigation regions created unbaked — enable 'Bake navigation on import' or bake manually.")

	return {"created": created, "warnings": warnings}


func apply_actor_metadata(node: Node, actor_data: Dictionary, options: Dictionary) -> void:
	"""Writes Unreal tags/class/Blueprint variables onto the node as metadata."""
	if node == null or not bool(options.get("apply_metadata", true)):
		return

	var actor_class := Common.get_str(actor_data, "class", "")
	if actor_class != "":
		node.set_meta("unreal_class", actor_class)

	var tags = actor_data.get("tags")
	if tags is Array and not tags.is_empty():
		var tag_list := PackedStringArray()
		for t in tags:
			tag_list.append(str(t))
		node.set_meta("unreal_tags", tag_list)

	var properties = actor_data.get("properties")
	if properties is Dictionary:
		for key in properties:
			var value = properties[key]
			if value == null:
				continue
			node.set_meta(_sanitize_meta_key(str(key)), value)


func _sanitize_meta_key(key: String) -> String:
	"""Godot metadata names must be valid identifiers."""
	var out := ""
	for i in key.length():
		var c := key.unicode_at(i)
		var ch := key[i]
		var ok := (c >= 97 and c <= 122) or (c >= 65 and c <= 90) or (c >= 48 and c <= 57) or ch == "_"
		out += ch if ok else "_"
	if out == "":
		out = "_"
	elif out.unicode_at(0) >= 48 and out.unicode_at(0) <= 57:
		out = "_" + out
	return out
