@tool
extends EditorScript

# ==============================================================================
# Godot 4.x Editor Script: Import Unreal Engine Level Layout
# ==============================================================================
# Instructions:
# 1. Place this script in your Godot project.
# 2. Modify the paths below to point to your exported JSON file and glTF models folder.
# 3. Open the scene where you want to import the actors (e.g., an empty 3D scene).
# 4. In the Script Editor, choose "File -> Run" or press Ctrl+Shift+X (Cmd+Shift+X on macOS).
# ==============================================================================

# Path configuration
const JSON_FILE_PATH: String = "res://level_layout.json"
const GLTF_MODELS_FOLDER: String = "res://models/"
const TEXTURES_FOLDER_PATH: String = "res://textures/"

# Set this to true to use Godot-side transform conversion calculation (Option B).
# Set to false to use the pre-calculated "godot_transform" from the JSON (Option A - Recommended).
var USE_GDSCRIPT_TRANSFORM_CONVERSION: bool = false

# Material sharing cache
var material_cache: Dictionary = {}
var active_scene_root: Node
var active_textures_folder: String

func _run() -> void:
	# Standalone EditorScript execution entry point
	var scene_root: Node = get_scene()
	if not scene_root:
		printerr("Import Error: No active scene is open. Please open a 3D scene in the editor first.")
		return
	var _ok = do_import(JSON_FILE_PATH, GLTF_MODELS_FOLDER, TEXTURES_FOLDER_PATH, scene_root)

func do_import(json_path: String, models_folder: String, textures_folder: String, scene_root: Node) -> bool:
	active_scene_root = scene_root
	active_textures_folder = textures_folder
	
	# Open and read the JSON file
	if not FileAccess.file_exists(json_path):
		printerr("Import Error: JSON file not found at path: ", json_path)
		return false
		
	var file := FileAccess.open(json_path, FileAccess.READ)
	var json_string := file.get_as_text()
	file.close()
	
	# Parse JSON data
	var data = JSON.parse_string(json_string)
	if data == null:
		printerr("Import Error: Failed to parse JSON file.")
		return false
		
	if not data.has("actors") or not (data["actors"] is Array):
		printerr("Import Error: Invalid JSON structure. Missing 'actors' array.")
		return false

	var actors: Array = data["actors"]
	var meshes_lib: Dictionary = data.get("meshes", {})
	print("Starting import of ", actors.size(), " actors from Unreal...")
	material_cache.clear()
	
	var imported_count: int = 0
	var missing_meshes: Dictionary = {}

	# Iterate and instance actors
	for actor_data in actors:
		var actor_name: String = actor_data.get("name", "Actor")
		var actor_class: String = actor_data.get("class", "StaticMeshActor")
		var components: Array = actor_data.get("components", [])
		
		if components.size() == 0:
			continue # No meshes to instance
			
		# Resolve the parent actor's transform
		var actor_transform: Transform3D
		if USE_GDSCRIPT_TRANSFORM_CONVERSION:
			var u_trans = actor_data["unreal_transform"]
			actor_transform = convert_unreal_to_godot_transform(
				u_trans["translation"], 
				u_trans["rotation_quat"], 
				u_trans["scale"]
			)
		else:
			var g_trans = actor_data["godot_transform"]
			actor_transform = get_transform_from_dict(g_trans)
			
		# Optimisation: If it is a simple single-mesh actor, instance it directly.
		# If it's a complex actor (multiple meshes / components), create a parent Node3D.
		if components.size() == 1:
			var comp_data = components[0]
			var mesh_name: String = comp_data.get("mesh_name", "")
			
			var gltf_path := find_gltf_path(models_folder, mesh_name)
			if gltf_path == "":
				missing_meshes[mesh_name] = true
				create_placeholder(active_scene_root, actor_name, actor_transform, mesh_name)
				continue
				
			var physics_body = setup_physics_body(active_scene_root, actor_name, actor_transform, mesh_name, meshes_lib)
			var instanced_mesh = instance_gltf(gltf_path)
			
			if instanced_mesh:
				# Apply materials to mesh instance
				var overrides = comp_data.get("material_overrides", [])
				apply_materials_to_instance(instanced_mesh, mesh_name, overrides, meshes_lib)
				
				if physics_body:
					# If there is collision, the physics body is the parent node,
					# and the mesh is a child of it at identity transform.
					instanced_mesh.name = mesh_name
					physics_body.add_child(instanced_mesh)
					physics_body.owner = active_scene_root
					instanced_mesh.owner = active_scene_root
					_set_owner_recursive(instanced_mesh, active_scene_root)
					instanced_mesh.transform = Transform3D.IDENTITY
				else:
					# Direct visual-only mesh instance
					instanced_mesh.name = actor_name
					active_scene_root.add_child(instanced_mesh)
					instanced_mesh.owner = active_scene_root
					_set_owner_recursive(instanced_mesh, active_scene_root)
					instanced_mesh.global_transform = actor_transform
				imported_count += 1
		else:
			# Multi-mesh Blueprint actor
			var actor_node := Node3D.new()
			actor_node.name = actor_name
			active_scene_root.add_child(actor_node)
			actor_node.owner = active_scene_root
			actor_node.global_transform = actor_transform
			
			for comp_data in components:
				var comp_name: String = comp_data.get("name", "Component")
				var mesh_name: String = comp_data.get("mesh_name", "")
				
				var comp_transform: Transform3D
				if USE_GDSCRIPT_TRANSFORM_CONVERSION:
					var u_trans = comp_data["unreal_relative_transform"]
					comp_transform = convert_unreal_to_godot_transform(
						u_trans["translation"], 
						u_trans["rotation_quat"], 
						u_trans["scale"]
					)
				else:
					var g_trans = comp_data["godot_relative_transform"]
					comp_transform = get_transform_from_dict(g_trans)
					
				var gltf_path := find_gltf_path(models_folder, mesh_name)
				if gltf_path == "":
					missing_meshes[mesh_name] = true
					create_placeholder(actor_node, comp_name, comp_transform, mesh_name)
					continue
					
				var physics_body = setup_physics_body(actor_node, comp_name, comp_transform, mesh_name, meshes_lib)
				var instanced_mesh = instance_gltf(gltf_path)
				if instanced_mesh:
					# Apply materials to mesh instance
					var overrides = comp_data.get("material_overrides", [])
					apply_materials_to_instance(instanced_mesh, mesh_name, overrides, meshes_lib)
					
					if physics_body:
						instanced_mesh.name = mesh_name
						physics_body.add_child(instanced_mesh)
						physics_body.owner = active_scene_root
						instanced_mesh.owner = active_scene_root
						_set_owner_recursive(instanced_mesh, active_scene_root)
						instanced_mesh.transform = Transform3D.IDENTITY
					else:
						instanced_mesh.name = comp_name
						actor_node.add_child(instanced_mesh)
						instanced_mesh.owner = active_scene_root
						_set_owner_recursive(instanced_mesh, active_scene_root)
						instanced_mesh.transform = comp_transform
					
			imported_count += 1
			
	# Summary reporting
	print("\n=================== IMPORT SUMMARY ===================")
	print("Successfully imported: ", imported_count, " actors.")
	if missing_meshes.size() > 0:
		print("Warning: The following meshes were missing from the models directory:")
		for mesh in missing_meshes.keys():
			print(" - ", mesh, " (Looked in: ", models_folder, ")")
		print("Placeholders (Marker3Ds) were created at their respective transforms.")
	print("======================================================\n")
	return true

func find_gltf_path(folder: String, mesh_name: String) -> String:
	"""Searches for .gltf or .glb file with matching mesh name."""
	var path_gltf = folder.path_join(mesh_name + ".gltf")
	var path_glb = folder.path_join(mesh_name + ".glb")
	
	if FileAccess.file_exists(path_gltf):
		return path_gltf
	elif FileAccess.file_exists(path_glb):
		return path_glb
		
	return ""

func instance_gltf(gltf_path: String) -> Node3D:
	"""Loads and instances a glTF scene."""
	var gltf_scene = load(gltf_path)
	if gltf_scene:
		return gltf_scene.instantiate() as Node3D
	return null

func create_placeholder(parent: Node, name_str: String, local_transform: Transform3D, mesh_name: String) -> void:
	"""Creates a Marker3D node in the editor to serve as a placement reminder."""
	var marker := Marker3D.new()
	marker.name = name_str + "_MISSING_" + mesh_name
	parent.add_child(marker)
	marker.owner = active_scene_root
	marker.transform = local_transform
	# Print to console
	print("Created placeholder for missing mesh: ", mesh_name, " at ", marker.global_position)

func _set_owner_recursive(node: Node, owner: Node) -> void:
	"""Recursively sets the owner on all descendants so they persist in the saved scene."""
	for child in node.get_children():
		child.owner = owner
		_set_owner_recursive(child, owner)

func get_transform_from_dict(t_dict: Dictionary) -> Transform3D:
	"""Constructs a Transform3D from a JSON transform dictionary."""
	var trans_arr: Array = t_dict["translation"]
	var quat_arr: Array = t_dict["rotation_quat"]
	var scale_arr: Array = t_dict["scale"]
	
	var translation := Vector3(trans_arr[0], trans_arr[1], trans_arr[2])
	var quat := Quaternion(quat_arr[0], quat_arr[1], quat_arr[2], quat_arr[3])
	var scale := Vector3(scale_arr[0], scale_arr[1], scale_arr[2])
	
	var basis := Basis(quat).scaled(scale)
	return Transform3D(basis, translation)

func convert_unreal_to_godot_transform(u_loc: Array, u_rot_quat: Array, u_scale: Array) -> Transform3D:
	"""
	Option B: GDScript implementation of coordinate system translation:
	- Left-handed to Right-handed conversion.
	- Centimeter to Meter scale conversion (x 0.01).
	- Remaps rotation matrix columns to correct axes.
	"""
	# 1. Translation: cm -> meters and axes swap
	var translation = Vector3(u_loc[1] * 0.01, u_loc[2] * 0.01, -u_loc[0] * 0.01)
	
	# 2. Scale axes swap
	var scale = Vector3(u_scale[1], u_scale[2], u_scale[0])
	
	# 3. Rotation (Remap 3x3 Basis using C * R_unreal * C^T)
	var qx: float = u_rot_quat[0]
	var qy: float = u_rot_quat[1]
	var qz: float = u_rot_quat[2]
	var qw: float = u_rot_quat[3]
	
	# Unreal Quat -> 3x3 Matrix
	var r00 = 1.0 - 2.0 * (qy*qy + qz*qz)
	var r01 = 2.0 * (qx*qy - qw*qz)
	var r02 = 2.0 * (qx*qz + qw*qy)
	
	var r10 = 2.0 * (qx*qy + qw*qz)
	var r11 = 1.0 - 2.0 * (qx*qx + qz*qz)
	var r12 = 2.0 * (qy*qz - qw*qx)
	
	var r20 = 2.0 * (qx*qz - qw*qy)
	var r21 = 2.0 * (qy*qz + qw*qx)
	var r22 = 1.0 - 2.0 * (qx*qx + qy*qy)
	
	# Remap basis for right-handed Y-up Godot:
	var col0 = Vector3(r11, r21, -r01)
	var col1 = Vector3(r12, r22, -r02)
	var col2 = Vector3(-r10, -r20, r00)
	
	var basis := Basis(col0, col1, col2)
	basis = basis.scaled(scale)
	
	return Transform3D(basis, translation)

func setup_physics_body(parent: Node, node_name: String, transform: Transform3D, mesh_name: String, meshes_lib: Dictionary) -> StaticBody3D:
	"""
	Optionally generates a StaticBody3D node with collision shapes (Box, Sphere, Capsule, Convex)
	based on the Unreal Engine collision data for the mesh.
	"""
	var mesh_data = meshes_lib.get(mesh_name, {})
	var collision = mesh_data.get("collision", null)
	
	if collision == null:
		# No collision data defined for this mesh
		return null
		
	# Create the StaticBody3D node
	var body := StaticBody3D.new()
	body.name = node_name
	parent.add_child(body)
	body.owner = active_scene_root
	body.transform = transform
	
	# 1. Generate Box Colliders
	if collision.has("boxes"):
		for box_data in collision["boxes"]:
			var size_arr: Array = box_data["size"]
			var local_trans: Dictionary = box_data["godot_local_transform"]
			
			var shape_node := CollisionShape3D.new()
			shape_node.name = "BoxCollision"
			body.add_child(shape_node)
			shape_node.owner = active_scene_root
			
			var box_shape := BoxShape3D.new()
			# Convert cm size to meters
			box_shape.size = Vector3(size_arr[1], size_arr[2], size_arr[0]) * 0.01
			shape_node.shape = box_shape
			shape_node.transform = get_transform_from_dict(local_trans)
			
	# 2. Generate Sphere Colliders
	if collision.has("spheres"):
		for sphere_data in collision["spheres"]:
			var radius: float = sphere_data["radius"]
			var local_trans: Dictionary = sphere_data["godot_local_transform"]
			
			var shape_node := CollisionShape3D.new()
			shape_node.name = "SphereCollision"
			body.add_child(shape_node)
			shape_node.owner = active_scene_root
			
			var sphere_shape := SphereShape3D.new()
			sphere_shape.radius = radius * 0.01
			shape_node.shape = sphere_shape
			shape_node.transform = get_transform_from_dict(local_trans)
			
	# 3. Generate Capsule (Sphyl) Colliders
	if collision.has("capsules"):
		for cap_data in collision["capsules"]:
			var radius: float = cap_data["radius"]
			var length: float = cap_data["length"]
			var local_trans: Dictionary = cap_data["godot_local_transform"]
			
			var shape_node := CollisionShape3D.new()
			shape_node.name = "CapsuleCollision"
			body.add_child(shape_node)
			shape_node.owner = active_scene_root
			
			var capsule_shape := CapsuleShape3D.new()
			capsule_shape.radius = radius * 0.01
			# Godot capsule height is cylinder length + 2 * radius
			capsule_shape.height = (length + 2.0 * radius) * 0.01
			shape_node.shape = capsule_shape
			shape_node.transform = get_transform_from_dict(local_trans)
			
	# 4. Generate Convex Hulls
	if collision.has("convex_hulls"):
		for convex_data in collision["convex_hulls"]:
			var verts_arr: Array = convex_data.get("vertices", [])
			var local_trans: Dictionary = convex_data["godot_local_transform"]
			
			if verts_arr.size() == 0:
				continue
				
			var shape_node := CollisionShape3D.new()
			shape_node.name = "ConvexCollision"
			body.add_child(shape_node)
			shape_node.owner = active_scene_root
			
			var convex_shape := ConvexPolygonShape3D.new()
			var points = PackedVector3Array()
			for v in verts_arr:
				# Convert to Godot coordinate space: swap axes and scale to meters
				points.append(Vector3(v[1], v[2], -v[0]) * 0.01)
			convex_shape.points = points
			
			shape_node.shape = convex_shape
			shape_node.transform = get_transform_from_dict(local_trans)
			
	return body

func apply_materials_to_instance(instanced_mesh: Node3D, mesh_name: String, override_mats: Array, meshes_lib: Dictionary) -> void:
	"""
	Compiles and applies default and overridden materials to all child MeshInstance3D nodes
	found inside the instanced scene root.
	"""
	var mesh_data: Dictionary = meshes_lib.get(mesh_name, {})
	var default_mats: Array = mesh_data.get("materials", [])
	
	if default_mats.size() == 0 and override_mats.size() == 0:
		return # No material data to map
		
	# Compile active materials map (slot_index -> StandardMaterial3D)
	var active_materials: Dictionary = {}
	
	# 1. Map default materials
	for mat_data in default_mats:
		var slot_idx: int = int(mat_data["slot_index"])
		var mat_name: String = mat_data["material_name"]
		var mat_path: String = mat_data["material_path"]
		var params = mat_data.get("parameters", null)
		
		if params:
			var godot_mat = get_or_create_material(mat_name, mat_path, params)
			active_materials[slot_idx] = godot_mat
			
	# 2. Map overridden materials
	for mat_data in override_mats:
		var slot_idx: int = int(mat_data["slot_index"])
		var mat_name: String = mat_data["material_name"]
		var mat_path: String = mat_data["material_path"]
		var params = mat_data.get("parameters", null)
		
		if params:
			var godot_mat = get_or_create_material(mat_name, mat_path, params)
			active_materials[slot_idx] = godot_mat
			
	# 3. Find all MeshInstance3D nodes recursively inside the sub-tree
	var mesh_instances: Array[MeshInstance3D] = []
	_find_mesh_instances_recursive(instanced_mesh, mesh_instances)
	
	# 4. Apply surface overrides
	for mesh_inst in mesh_instances:
		if not mesh_inst.mesh:
			continue
		var surface_count: int = mesh_inst.mesh.get_surface_count()
		for slot_idx in active_materials.keys():
			if slot_idx < surface_count:
				mesh_inst.set_surface_override_material(slot_idx, active_materials[slot_idx])

func _find_mesh_instances_recursive(node: Node, list: Array[MeshInstance3D]) -> void:
	"""Recursively finds all MeshInstance3D nodes in a node tree."""
	if node is MeshInstance3D:
		list.append(node)
	for child in node.get_children():
		_find_mesh_instances_recursive(child, list)

func get_or_create_material(mat_name: String, mat_path: String, params: Dictionary) -> StandardMaterial3D:
	"""
	Material instantiation cache manager. Reuses existing StandardMaterial3D instances
	by path or name to preserve shared materials across mesh instances.
	"""
	if mat_path != "None" and material_cache.has(mat_path):
		return material_cache[mat_path]
	if mat_name != "None" and material_cache.has(mat_name):
		return material_cache[mat_name]
		
	var mat = create_godot_material(params)
	mat.resource_name = mat_name
	
	if mat_path != "None":
		material_cache[mat_path] = mat
	elif mat_name != "None":
		material_cache[mat_name] = mat
		
	return mat

func create_godot_material(params: Dictionary) -> StandardMaterial3D:
	"""
	Creates a new StandardMaterial3D, maps Unreal scalar/vector settings to Godot,
	and attempts to find and bind bulk-exported texture files in the textures directory.
	"""
	var mat := StandardMaterial3D.new()
	
	# 1. Map Albedo color
	var color_arr: Array = params.get("albedo_color", [1.0, 1.0, 1.0, 1.0])
	mat.albedo_color = Color(color_arr[0], color_arr[1], color_arr[2], color_arr[3])
	
	# 2. Map PBR multipliers
	mat.roughness = params.get("roughness", 0.5)
	mat.metallic = params.get("metallic", 0.0)
	
	# 3. Map Tiling scale
	var tiling_arr: Array = params.get("tiling", [1.0, 1.0])
	mat.uv1_scale = Vector3(tiling_arr[0], tiling_arr[1], 1.0)
	
	# 4. Find and load textures
	# Python None serializes as JSON null, which Godot reads as Variant null, not ""
	var albedo_tex: String = _safe_str(params.get("albedo_texture"))
	var normal_tex: String = _safe_str(params.get("normal_texture"))
	var roughness_tex: String = _safe_str(params.get("roughness_texture"))
	var metallic_tex: String = _safe_str(params.get("metallic_texture"))
	
	if albedo_tex != "":
		var tex_path = find_texture_path(active_textures_folder, albedo_tex)
		if tex_path != "":
			var tex = load(tex_path)
			if tex:
				mat.albedo_texture = tex
				
	if normal_tex != "":
		var tex_path = find_texture_path(active_textures_folder, normal_tex)
		if tex_path != "":
			var tex = load(tex_path)
			if tex:
				mat.normal_enabled = true
				mat.normal_texture = tex
				
	if roughness_tex != "":
		var tex_path = find_texture_path(active_textures_folder, roughness_tex)
		if tex_path != "":
			var tex = load(tex_path)
			if tex:
				mat.roughness_texture = tex
				
	if metallic_tex != "":
		var tex_path = find_texture_path(active_textures_folder, metallic_tex)
		if tex_path != "":
			var tex = load(tex_path)
			if tex:
				mat.metallic_texture = tex
				
	return mat

func _safe_str(value) -> String:
	"""Converts a Variant (possibly null from JSON) to a String, returning '' for null."""
	if value == null:
		return ""
	return str(value)

func find_texture_path(folder: String, tex_name: String) -> String:
	"""
	Searches for matching texture file with various image extensions
	to support standard bulk exports (PNG, TGA, JPG, DDS).
	"""
	if tex_name == "":
		return ""
		
	var extensions = [".png", ".tga", ".jpg", ".jpeg", ".dds"]
	for ext in extensions:
		var path = folder.path_join(tex_name + ext)
		if FileAccess.file_exists(path):
			return path
			
	return ""
