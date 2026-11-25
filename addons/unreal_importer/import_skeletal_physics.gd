@tool
extends RefCounted

# ==============================================================================
# Rebuilds Unreal UPhysicsAsset bone colliders exported to <mesh>_physics.json
# onto imported Godot Skeleton3D nodes using PhysicalBoneSimulator3D +
# PhysicalBone3D + CollisionShape3D.
# ==============================================================================

const Common = preload("res://addons/unreal_importer/import_common.gd")


func attach_to_instance(instanced_mesh: Node, mesh_key: String, models_folder: String,
		scene_owner: Node, meshes_lib: Dictionary = {}) -> int:
	"""Finds Skeleton3D inside instanced_mesh and builds PhysicalBoneSimulator3D
	from <mesh_key>_physics.json when present. Returns number of bones created."""
	if instanced_mesh == null or mesh_key == "":
		return 0
	var skeleton := _find_skeleton(instanced_mesh)
	if skeleton == null:
		return 0
	if skeleton.get_node_or_null("UnrealPhysicalBoneSimulator") != null:
		return 0

	var physics_path := _find_physics_json(models_folder, mesh_key, meshes_lib)
	if physics_path == "":
		return 0

	var data := _read_json(physics_path)
	if data.is_empty():
		return 0
	var bodies = data.get("bodies", [])
	if not (bodies is Array) or (bodies as Array).is_empty():
		return 0

	return _build_simulator(skeleton, bodies, scene_owner)


func attach_in_scene(root: Node, models_folder: String, scene_owner: Node) -> int:
	"""Walks an open scene (e.g. a directly-generated .tscn) and attaches
	PhysicalBoneSimulator3D to any Skeleton3D whose model has a _physics.json."""
	if root == null:
		return 0
	var total_bones := 0
	var skeletons: Array[Skeleton3D] = []
	_collect_skeletons(root, skeletons)
	for skel in skeletons:
		if skel.get_node_or_null("UnrealPhysicalBoneSimulator") != null:
			continue
		var mesh_key := _infer_mesh_key(skel)
		if mesh_key == "":
			continue
		var physics_path := _find_physics_json(models_folder, mesh_key, {})
		if physics_path == "":
			continue
		var data := _read_json(physics_path)
		var bodies = data.get("bodies", [])
		if bodies is Array and not (bodies as Array).is_empty():
			_make_instances_editable(skel, scene_owner)
			total_bones += _build_simulator(skel, bodies, scene_owner)
	return total_bones


func _make_instances_editable(skeleton: Node, scene_owner: Node) -> void:
	"""Godot drops nodes added inside an instanced scene on save unless every
	instance between them and the scene root is editable."""
	var node := skeleton
	while node != null and node != scene_owner:
		if node.scene_file_path != "" and not scene_owner.is_editable_instance(node):
			scene_owner.set_editable_instance(node, true)
			node.set_display_folded(true)
		node = node.get_parent()


func _build_simulator(skeleton: Skeleton3D, bodies: Array, scene_owner: Node) -> int:
	# Map lowercase bone names -> exact Skeleton3D bone name so minor casing
	# differences between Unreal PhysicsAsset and glTF skeleton still match.
	var bone_lookup := {}
	var bone_idx_lookup := {}
	for i in skeleton.get_bone_count():
		var bname := skeleton.get_bone_name(i)
		bone_lookup[bname.to_lower()] = bname
		bone_idx_lookup[bname.to_lower()] = i

	var simulator: Node3D
	if ClassDB.class_exists("PhysicalBoneSimulator3D"):
		simulator = ClassDB.instantiate("PhysicalBoneSimulator3D") as Node3D
	else:
		# Fallback container for older Godot builds without PhysicalBoneSimulator3D
		simulator = Node3D.new()
	simulator.name = "UnrealPhysicalBoneSimulator"
	simulator.set_meta("unreal_skeletal_physics", true)
	Common.add_owned_child(skeleton, simulator, scene_owner)

	var created_bones := 0
	for body_entry in bodies:
		if not (body_entry is Dictionary):
			continue
		var raw_bone := Common.get_str(body_entry, "bone_name", "")
		if raw_bone == "":
			continue
		var matched_bone := raw_bone
		if bone_lookup.has(raw_bone.to_lower()):
			matched_bone = str(bone_lookup[raw_bone.to_lower()])
		elif skeleton.get_bone_count() > 0:
			# Skip bones that do not exist on this skeleton to avoid invalid PhysicalBone3D refs.
			continue

		var shapes = body_entry.get("shapes")
		if not (shapes is Dictionary):
			continue

		var phys_bone := PhysicalBone3D.new()
		phys_bone.name = "PhysicalBone_" + matched_bone
		phys_bone.bone_name = matched_bone
		var bone_idx: int = int(bone_idx_lookup.get(matched_bone.to_lower(), -1))
		if bone_idx >= 0:
			phys_bone.transform = skeleton.get_bone_global_rest(bone_idx)
		Common.add_owned_child(simulator, phys_bone, scene_owner)

		var shape_count := _add_collision_shapes(phys_bone, shapes, scene_owner)
		if shape_count > 0:
			created_bones += 1
		else:
			# free(), not queue_free(): a Blueprint prefab is packed before the queue runs.
			phys_bone.free()

	if created_bones == 0:
		simulator.free()
	return created_bones


func _add_collision_shapes(parent: Node, shapes: Dictionary, scene_owner: Node) -> int:
	var count := 0

	var boxes = shapes.get("boxes")
	if boxes is Array:
		for box_data in boxes:
			if not (box_data is Dictionary):
				continue
			var size_arr = box_data.get("size", [0.0, 0.0, 0.0])
			if not (size_arr is Array) or size_arr.size() < 3:
				continue
			var box_shape := BoxShape3D.new()
			box_shape.size = Vector3(
				absf(float(size_arr[0])),
				absf(float(size_arr[2])),
				absf(float(size_arr[1]))
			) * 0.01
			var shape_node := CollisionShape3D.new()
			shape_node.name = "BoxCollision"
			shape_node.shape = box_shape
			shape_node.transform = Common.get_transform_from_dict(box_data.get("godot_local_transform", {}))
			Common.add_owned_child(parent, shape_node, scene_owner)
			count += 1

	var spheres = shapes.get("spheres")
	if spheres is Array:
		for sph_data in spheres:
			if not (sph_data is Dictionary):
				continue
			var radius := absf(Common.get_num(sph_data, "radius", 0.0))
			if radius <= 0.0:
				continue
			var sph_shape := SphereShape3D.new()
			sph_shape.radius = radius * 0.01
			var shape_node := CollisionShape3D.new()
			shape_node.name = "SphereCollision"
			shape_node.shape = sph_shape
			shape_node.transform = Common.get_transform_from_dict(sph_data.get("godot_local_transform", {}))
			Common.add_owned_child(parent, shape_node, scene_owner)
			count += 1

	var capsules = shapes.get("capsules")
	if capsules is Array:
		for cap_data in capsules:
			if not (cap_data is Dictionary):
				continue
			var radius := absf(Common.get_num(cap_data, "radius", 0.0))
			var length := absf(Common.get_num(cap_data, "length", 0.0))
			if radius <= 0.0:
				continue
			var cap_shape := CapsuleShape3D.new()
			cap_shape.radius = radius * 0.01
			cap_shape.height = (length + 2.0 * radius) * 0.01
			var shape_node := CollisionShape3D.new()
			shape_node.name = "CapsuleCollision"
			shape_node.shape = cap_shape
			shape_node.transform = Common.get_transform_from_dict(cap_data.get("godot_local_transform", {}))
			Common.add_owned_child(parent, shape_node, scene_owner)
			count += 1

	var hulls = shapes.get("convex_hulls")
	if hulls is Array:
		for hull_data in hulls:
			if not (hull_data is Dictionary):
				continue
			var verts = hull_data.get("vertices", [])
			if not (verts is Array) or (verts as Array).is_empty():
				continue
			var pts := PackedVector3Array()
			for v in verts:
				if v is Array and v.size() >= 3:
					pts.append(Vector3(float(v[0]), float(v[2]), float(v[1])) * 0.01)
			if pts.is_empty():
				continue
			var hull_shape := ConvexPolygonShape3D.new()
			hull_shape.points = pts
			var shape_node := CollisionShape3D.new()
			shape_node.name = "ConvexCollision"
			shape_node.shape = hull_shape
			shape_node.transform = Common.get_transform_from_dict(hull_data.get("godot_local_transform", {}))
			Common.add_owned_child(parent, shape_node, scene_owner)
			count += 1

	return count


func _find_physics_json(models_folder: String, mesh_key: String, meshes_lib: Dictionary) -> String:
	var mesh_entry = meshes_lib.get(mesh_key)
	if mesh_entry is Dictionary:
		var explicit := Common.get_str(mesh_entry, "physics_file", "")
		if explicit != "":
			var p := Common.find_file_case_insensitive(models_folder, explicit.get_basename(), [".json"])
			if p != "":
				return p
	return Common.find_file_case_insensitive(models_folder, mesh_key + "_physics", [".json"])


func _read_json(path: String) -> Dictionary:
	if not FileAccess.file_exists(path):
		return {}
	var text := FileAccess.get_file_as_string(path)
	if text == "":
		return {}
	var parsed = JSON.parse_string(text)
	return parsed if parsed is Dictionary else {}


func _find_skeleton(node: Node) -> Skeleton3D:
	if node is Skeleton3D:
		return node as Skeleton3D
	for child in node.get_children():
		var found := _find_skeleton(child)
		if found:
			return found
	return null


func _collect_skeletons(node: Node, out: Array[Skeleton3D]) -> void:
	if node is Skeleton3D:
		out.append(node as Skeleton3D)
	for child in node.get_children():
		_collect_skeletons(child, out)


func _infer_mesh_key(skel: Skeleton3D) -> String:
	var cur: Node = skel
	while cur != null:
		if cur.has_meta("unreal_mesh_key"):
			return str(cur.get_meta("unreal_mesh_key"))
		if cur.scene_file_path != "":
			return cur.scene_file_path.get_file().get_basename()
		cur = cur.get_parent()
	return ""
