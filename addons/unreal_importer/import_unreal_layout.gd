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

const Common = preload("res://addons/unreal_importer/import_common.gd")

# The layout schema this addon understands (docs/SCHEMA_V2.md). The exporter
# stamps a matching "format_version" into every layout it writes. The two halves
# of the toolchain are downloaded separately, so they drift apart on their own --
# checking here turns a mismatch into one sentence instead of a confusing failure
# somewhere deep in the actor loop.
const SUPPORTED_FORMAT_VERSION: int = 2

# Name of the Node3D every mesh actor is parented under. Kept in step with
# tscn_writer.py's STATIC_MESH_CONTAINER so a .tscn export and an addon import of
# the same layout produce the same tree.
const STATIC_MESH_CONTAINER: String = "UnrealStaticMeshes"

# Per-feature import toggles (see docs/SCHEMA_V2.md). The dock passes a matching
# dictionary; anything omitted falls back to these defaults.
const DEFAULT_IMPORT_OPTIONS := {
	"apply_lights": true,
	"apply_environment": true,
	"apply_decals": true,
	"build_terrain": true,
	"terrain_mode": "auto",   # auto | terrain3d | hterrain | mesh
	"apply_foliage": true,
	"apply_navigation": true,
	"navigation_bake": false,
	"apply_metadata": true,
	"apply_lods": true,
	"apply_skeletal_physics": true,
	"apply_reflection_probes": true,
	"apply_cameras": true,
	"apply_audio": true,
	"apply_spline_meshes": true,
	"preserve_folders": true,
	"flip_normal_green": true,
	"save_external_materials": false,
	"materials_folder": "res://materials/",
	"light_energy_scale": 1.0,
}

# Set this to true to use Godot-side transform conversion calculation (Option B).
# Set to false to use the pre-calculated "godot_transform" from the JSON (Option A - Recommended).
var USE_GDSCRIPT_TRANSFORM_CONVERSION: bool = false

const ImportReport = preload("res://addons/unreal_importer/import_report.gd")
const TextureLimit = preload("res://addons/unreal_importer/texture_import_limit.gd")
const SplineMeshHelper = preload("res://addons/unreal_importer/import_spline_mesh.gd")

# Material sharing cache
var material_cache: Dictionary = {}
var _normal_map_targets: Dictionary = {}
var _alpha_merged_cache: Dictionary = {}
var _external_normal_cache: Dictionary = {}
var _external_mat_paths: Dictionary = {}
var active_scene_root: Node
var active_textures_folder: String
var active_models_folder: String
var import_options: Dictionary = {}
# Collects a written record of the import. The console scrolls and truncates,
# so the report file is the thing that can actually be handed to someone else.
var report = null
var _missing_textures: Dictionary = {}
var _clamped_scalars: Dictionary = {}
# Meshes whose collision had a Jolt-unsafe scale baked into the shapes, and the
# subset where that bake could not be exact. See Common.physics_body_transform.
var _scale_baked: Dictionary = {}
var _scale_approximated: Dictionary = {}

func _run() -> void:
	# Standalone EditorScript execution entry point
	var scene_root: Node = get_scene()
	if not scene_root:
		printerr("Import Error: No active scene is open. Please open a 3D scene in the editor first.")
		return
	var _ok = await do_import(JSON_FILE_PATH, GLTF_MODELS_FOLDER, TEXTURES_FOLDER_PATH, scene_root)

func do_import(json_path: String, models_folder: String, textures_folder: String, scene_root: Node, options: Dictionary = {}) -> bool:
	active_scene_root = scene_root
	active_textures_folder = textures_folder
	active_models_folder = models_folder
	report = ImportReport.new()
	report.header(json_path, models_folder, textures_folder)
	_missing_textures.clear()
	_clamped_scalars.clear()
	_scale_baked.clear()
	_scale_approximated.clear()
	import_options = DEFAULT_IMPORT_OPTIONS.duplicate()
	for key in options:
		import_options[key] = options[key]
	import_options["models_folder"] = models_folder
	import_options["textures_folder"] = textures_folder
	import_options["json_dir"] = json_path.get_base_dir()
	import_options["material_helper"] = self

	# Open and read the JSON file
	if not FileAccess.file_exists(json_path):
		printerr("Import Error: JSON file not found at path: ", json_path)
		_fail_report("level_layout.json not found at " + json_path
			+ " -- check the path, and that the layout export (a separate "
			+ "action from the mesh export) actually ran.")
		return false

	var file := FileAccess.open(json_path, FileAccess.READ)
	if file == null:
		printerr("Import Error: Could not open JSON file (may be locked or inaccessible): ", json_path)
		_fail_report("level_layout.json exists but could not be opened (%s). It "
			% error_string(FileAccess.get_open_error())
			+ "may be locked by another program.")
		return false
	var json_string := file.get_as_text()
	file.close()

	# Parse JSON data
	var data = JSON.parse_string(json_string)
	if data == null:
		printerr("Import Error: Failed to parse JSON file.")
		_fail_report("level_layout.json is not valid JSON. The export may have "
			+ "been interrupted, leaving a truncated file.")
		return false

	if not _check_format_version(data):
		return false

	if not data.has("actors") or not (data["actors"] is Array):
		printerr("Import Error: Invalid JSON structure. Missing 'actors' array.")
		_fail_report("level_layout.json has no 'actors' array -- it is not a "
			+ "layout file, or was written by an incompatible exporter version.")
		return false

	var actors: Array = data["actors"]
	var meshes_lib: Dictionary = data.get("meshes", {}) if data.get("meshes") is Dictionary else {}
	print("Starting import of ", actors.size(), " actors from Unreal...")
	material_cache.clear()
	_normal_map_targets = _collect_normal_map_targets(data)
	_alpha_merged_cache.clear()
	_external_normal_cache.clear()
	_external_mat_paths.clear()

	# Configure DirectX -> OpenGL normal map Y-flip via Godot's non-destructive
	# process/normal_map_invert_y import parameter before materials load them.
	if not _normal_map_targets.is_empty():
		var flip_enabled := bool(import_options.get("flip_normal_green", true))
		var search_folders := [textures_folder, models_folder, json_path.get_base_dir().path_join("textures")]
		var flip_res: Dictionary = TextureLimit.apply_normal_map_flip(
			search_folders, _normal_map_targets, flip_enabled)
		if int(flip_res.get("reimported", 0)) > 0 and Engine.is_editor_hint():
			var fs := EditorInterface.get_resource_filesystem()
			while fs and fs.is_scanning():
				if scene_root and scene_root.get_tree():
					await scene_root.get_tree().process_frame
				else:
					break
		if int(flip_res.get("changed", 0)) > 0:
			print("Unreal Importer: updated normal map Y-flip on %d/%d normal map(s) (enabled=%s)"
				% [int(flip_res["changed"]), int(flip_res["total"]), str(flip_enabled)])
	
	var imported_count: int = 0
	var lod_chains_wired: int = 0
	var skel_physics_bones: int = 0
	var missing_meshes: Dictionary = {}
	var gameplay_helper = _load_feature("import_gameplay.gd")
	var skel_physics_helper = _load_feature("import_skeletal_physics.gd")

	# Every mesh actor goes under one container, the way lights (UnrealLights),
	# decals (UnrealDecals), foliage and navigation already do. A level is mostly
	# static meshes, so leaving them loose at the scene root buries the handful of
	# nodes a user actually goes looking for. It stays at identity, so nothing
	# below it moves. Every actor with components produces a node or a placeholder,
	# so this is created exactly when it would not be left empty.
	var mesh_root: Node = active_scene_root
	var folder_nodes: Dictionary = {}
	var preserve_folders := bool(import_options.get("preserve_folders", true))
	for actor_data in actors:
		if not (actor_data is Dictionary):
			continue
		var comps = actor_data.get("components", [])
		if comps is Array and (comps as Array).size() > 0:
			var container := Node3D.new()
			container.name = STATIC_MESH_CONTAINER
			Common.add_owned_child(active_scene_root, container, active_scene_root)
			mesh_root = container
			break

	# Iterate and instance actors
	for actor_data in actors:
		if not (actor_data is Dictionary):
			continue
		var actor_name: String = Common.get_str(actor_data, "name", "Actor")
		var actor_class: String = Common.get_str(actor_data, "class", "StaticMeshActor")
		var comps_raw = actor_data.get("components", [])
		var components: Array = comps_raw if comps_raw is Array else []
		
		if components.size() == 0:
			continue # No meshes to instance

		var actor_parent: Node = mesh_root
		if preserve_folders:
			var folder_path := Common.get_str(actor_data, "folder_path", "")
			if folder_path != "":
				actor_parent = _resolve_folder_parent(mesh_root, folder_path, folder_nodes)
			
		# Resolve the parent actor's transform
		var actor_transform: Transform3D = Transform3D.IDENTITY
		if USE_GDSCRIPT_TRANSFORM_CONVERSION:
			var u_trans = actor_data.get("unreal_transform")
			if u_trans is Dictionary:
				actor_transform = convert_unreal_to_godot_transform(
					u_trans.get("translation", [0.0, 0.0, 0.0]),
					u_trans.get("rotation_quat", [0.0, 0.0, 0.0, 1.0]),
					u_trans.get("scale", [1.0, 1.0, 1.0])
				)
		else:
			var g_trans = actor_data.get("godot_transform")
			if g_trans is Dictionary:
				actor_transform = get_transform_from_dict(g_trans)
			
		# Optimisation: If it is a simple single-mesh actor, instance it directly.
		# If it's a complex actor (multiple meshes / components), create a parent Node3D.
		if components.size() == 1:
			var comp_data = components[0]
			if not (comp_data is Dictionary):
				continue
			var mesh_name: String = Common.get_str(comp_data, "mesh_name", "")
			var mesh_key: String = Common.get_str(comp_data, "mesh_key", mesh_name)

			# The component's absolute placement, straight from the exporter.
			var comp_transform := component_world_transform(comp_data, actor_transform)
			# The glTF mesh is baked in a different axis order than the placement
			# convention; re-seat it (and the collision that rides with it) so it
			# lines up with its neighbours instead of yawing about its own pivot.
			var mesh_place := Common.gltf_mesh_placement(comp_transform)

			var gltf_path := find_model_for(models_folder, mesh_key, mesh_name)
			if gltf_path == "":
				missing_meshes[mesh_key] = true
				create_placeholder(actor_parent, actor_name, comp_transform, mesh_key)
				continue

			# Collision shapes are defined in mesh-local space, so the physics body
			# sits at the component's (axis-corrected) world transform and the mesh
			# sits under it carrying only whatever scale the body could not keep,
			# which is nothing at all for a uniformly scaled prop.
			var want_collision := bool(comp_data.get("collision_enabled", true))
			var spline_raw = comp_data.get("spline_mesh")
			var has_spline := bool(import_options.get("apply_spline_meshes", true)) and (spline_raw is Dictionary) and not (spline_raw as Dictionary).is_empty()
			var physics_body = setup_physics_body(actor_parent, actor_name, mesh_place, mesh_key, meshes_lib) if (want_collision and not has_spline) else null
			var instanced_mesh = instance_gltf(gltf_path)

			if instanced_mesh:
				# Apply materials to mesh instance
				var overrides_raw = comp_data.get("material_overrides", [])
				var overrides: Array = overrides_raw if overrides_raw is Array else []
				apply_materials_to_instance(instanced_mesh, mesh_key, overrides, meshes_lib)
				if has_spline:
					SplineMeshHelper.deform_instance(instanced_mesh, spline_raw as Dictionary)

				if physics_body:
					# Physics body already carries actor * component, minus any scale
					# Jolt could not have applied to the shapes -- which the mesh has
					# to take instead, or it would render unscaled.
					instanced_mesh.name = mesh_name if mesh_name != "" else (mesh_key if mesh_key != "" else "Mesh")
					physics_body.add_child(instanced_mesh)
					physics_body.owner = active_scene_root
					instanced_mesh.owner = active_scene_root
					_set_owner_recursive(instanced_mesh, active_scene_root)
					instanced_mesh.transform = Transform3D(
						Basis.from_scale(Common.physics_body_scale(mesh_place)), Vector3.ZERO)
				else:
					# Direct visual-only mesh instance at the component's world
					# transform (axis-corrected, same as the physics-body case).
					instanced_mesh.name = actor_name
					actor_parent.add_child(instanced_mesh)
					instanced_mesh.owner = active_scene_root
					_set_owner_recursive(instanced_mesh, active_scene_root)
					instanced_mesh.transform = mesh_place

				var target_node: Node = physics_body if physics_body else instanced_mesh
				if bool(import_options.get("apply_lods", true)) and not has_spline:
					var lod_parent: Node = physics_body if physics_body else instanced_mesh
					var lod_xform: Transform3D = instanced_mesh.transform if physics_body else Transform3D.IDENTITY
					if _apply_lod_chain(instanced_mesh, lod_parent, lod_xform, mesh_key, mesh_name, overrides, meshes_lib) > 0:
						lod_chains_wired += 1
				_apply_component_render_flags(target_node, instanced_mesh, comp_data)
				if bool(import_options.get("apply_skeletal_physics", true)) and skel_physics_helper:
					skel_physics_bones += int(skel_physics_helper.attach_to_instance(
						instanced_mesh, mesh_key, models_folder, active_scene_root, meshes_lib))

				imported_count += 1
				if gameplay_helper:
					gameplay_helper.apply_actor_metadata(target_node, actor_data, import_options)
					if gameplay_helper.has_method("apply_component_metadata"):
						gameplay_helper.apply_component_metadata(instanced_mesh if physics_body else target_node, comp_data, import_options)
			else:
				if physics_body:
					physics_body.queue_free()
		else:
			# Multi-mesh Blueprint actor
			var actor_node := Node3D.new()
			actor_node.name = actor_name
			actor_parent.add_child(actor_node)
			actor_node.owner = active_scene_root
			# A scale on the actor is INHERITED by the bodies beneath it, where Jolt
			# would hit it again -- stripping it at the body would achieve nothing.
			# Component placements are absolute, so the actor frame is free: drop the
			# unsafe scale here and it reappears in each component's local transform,
			# which is where setup_physics_body can bake it into the shapes.
			var actor_frame := Common.physics_body_transform(actor_transform)
			actor_node.transform = actor_frame

			# Components hang off actor_node, so their world placement has to be
			# expressed relative to the actor rather than to the world.
			var actor_inverse := Transform3D.IDENTITY
			if absf(actor_frame.basis.determinant()) > 1e-12:
				actor_inverse = actor_frame.affine_inverse()
			for comp_data in components:
				if not (comp_data is Dictionary):
					continue
				var comp_name: String = Common.get_str(comp_data, "name", "Component")
				var mesh_name: String = Common.get_str(comp_data, "mesh_name", "")
				var mesh_key: String = Common.get_str(comp_data, "mesh_key", mesh_name)

				var comp_transform := actor_inverse * component_world_transform(comp_data, actor_transform)
				# Re-seat the glTF mesh (and its collision) from the glTF axis order
				# into the placement convention -- see Common.gltf_mesh_placement.
				var mesh_place := Common.gltf_mesh_placement(comp_transform)

				var gltf_path := find_model_for(models_folder, mesh_key, mesh_name)
				if gltf_path == "":
					missing_meshes[mesh_key] = true
					create_placeholder(actor_node, comp_name, comp_transform, mesh_key)
					continue

				var want_collision := bool(comp_data.get("collision_enabled", true))
				var spline_raw = comp_data.get("spline_mesh")
				var has_spline := bool(import_options.get("apply_spline_meshes", true)) and (spline_raw is Dictionary) and not (spline_raw as Dictionary).is_empty()
				var physics_body = setup_physics_body(actor_node, comp_name, mesh_place, mesh_key, meshes_lib) if (want_collision and not has_spline) else null
				var instanced_mesh = instance_gltf(gltf_path)
				if instanced_mesh:
					# Apply materials to mesh instance
					var overrides_raw = comp_data.get("material_overrides", [])
					var overrides: Array = overrides_raw if overrides_raw is Array else []
					apply_materials_to_instance(instanced_mesh, mesh_key, overrides, meshes_lib)
					if has_spline:
						SplineMeshHelper.deform_instance(instanced_mesh, spline_raw as Dictionary)
					
					if physics_body:
						instanced_mesh.name = mesh_name if mesh_name != "" else (mesh_key if mesh_key != "" else "Mesh")
						physics_body.add_child(instanced_mesh)
						physics_body.owner = active_scene_root
						instanced_mesh.owner = active_scene_root
						_set_owner_recursive(instanced_mesh, active_scene_root)
						instanced_mesh.transform = Transform3D(
							Basis.from_scale(Common.physics_body_scale(mesh_place)), Vector3.ZERO)
					else:
						instanced_mesh.name = comp_name
						actor_node.add_child(instanced_mesh)
						instanced_mesh.owner = active_scene_root
						_set_owner_recursive(instanced_mesh, active_scene_root)
						instanced_mesh.transform = mesh_place

					var comp_target: Node = physics_body if physics_body else instanced_mesh
					if bool(import_options.get("apply_lods", true)) and not has_spline:
						var lod_parent: Node = physics_body if physics_body else instanced_mesh
						var lod_xform: Transform3D = instanced_mesh.transform if physics_body else Transform3D.IDENTITY
						if _apply_lod_chain(instanced_mesh, lod_parent, lod_xform, mesh_key, mesh_name, overrides, meshes_lib) > 0:
							lod_chains_wired += 1
					_apply_component_render_flags(comp_target, instanced_mesh, comp_data)
					if bool(import_options.get("apply_skeletal_physics", true)) and skel_physics_helper:
						skel_physics_bones += int(skel_physics_helper.attach_to_instance(
							instanced_mesh, mesh_key, models_folder, active_scene_root, meshes_lib))

					if gameplay_helper and gameplay_helper.has_method("apply_component_metadata"):
						gameplay_helper.apply_component_metadata(comp_target, comp_data, import_options)
				else:
					if physics_body:
						physics_body.queue_free()
					
			if actor_node.get_child_count() > 0:
				imported_count += 1
				if gameplay_helper:
					gameplay_helper.apply_actor_metadata(actor_node, actor_data, import_options)
			else:
				actor_node.queue_free()
			
	# Feature modules: lights/environment/decals, terrain, foliage, navigation.
	# Each is optional — a missing module file simply disables that feature.
	var feature_summary: Array[String] = []
	var feature_warnings := PackedStringArray()
	var feature_files := {
		"import_environment.gd": "Environment (lights/decals/post-fx)",
		"import_terrain.gd": "Terrain",
		"import_foliage.gd": "Foliage",
		"import_gameplay.gd": "Navigation",
	}
	for file_name in feature_files:
		var module = _load_feature(file_name)
		if module == null:
			continue
		# await, not a plain call: import_terrain has to yield a frame for
		# Terrain3D to build its data object (probed — the property does not
		# exist until one frame after the node enters the tree). Awaiting a
		# module that is NOT a coroutine returns its value unchanged, so this
		# is safe for every feature module.
		var res = await module.apply(data, active_scene_root, active_scene_root, import_options)
		if res is Dictionary:
			var created := int(res.get("created", 0))
			if created > 0:
				feature_summary.append("%s: %d node(s)" % [feature_files[file_name], created])
			for w in res.get("warnings", []):
				feature_warnings.append(str(w))

	# Summary reporting
	print("\n=================== IMPORT SUMMARY ===================")
	print("Successfully imported: ", imported_count, " actors.")
	for line in feature_summary:
		print(line)
	for w in feature_warnings:
		print("Warning: ", w)
	if missing_meshes.size() > 0:
		print("Warning: The following meshes were missing from the models directory:")
		for mesh in missing_meshes.keys():
			print(" - ", mesh, " (Looked in: ", models_folder, ")")
		print("Placeholders (Marker3Ds) were created at their respective transforms.")
	print("======================================================\n")

	if report:
		report.section("Layout")
		report.fact("actors in json", actors.size())
		report.fact("mesh entries in json", meshes_lib.size())
		report.fact("actors imported", imported_count)
		if lod_chains_wired > 0:
			report.fact("LOD chains wired", lod_chains_wired)
		if skel_physics_bones > 0:
			report.fact("skeletal physics bones built", skel_physics_bones)
		if not _scale_baked.is_empty():
			report.fact("meshes with baked collision scale", _scale_baked.size())
			report.line("  Their actors are scaled non-uniformly, which Jolt cannot apply to a")
			report.line("  collision shape: it would replace the scale with the average of the")
			report.line("  three axes and log an error per body on every load. The scale is baked")
			report.line("  into the shapes instead, so the bodies carry no scale at all.")
			for key in _scale_approximated:
				report.warn(("%s: this shape cannot carry a non-uniform scale exactly, "
					+ "so the collider is the closest primitive rather than an exact fit.") % key)
		for line in feature_summary:
			report.line("  " + line)
		for w in feature_warnings:
			report.warn(str(w))
		report.audit_scene(scene_root)
		report.note_missing_meshes(missing_meshes, models_folder)
		report.write()
	return true

func _fail_report(reason: String) -> void:
	"""An import that dies early is exactly the case where there is nothing to
	show but a red console line, so still leave a report behind."""
	if report == null:
		return
	report.section("Result")
	report.error(reason)
	report.write()


func _check_format_version(data: Dictionary) -> bool:
	"""Rejects a layout this addon cannot read, naming which half is out of date.

	The exporter (Unreal plugin) and this importer (Godot addon) are installed
	separately and updated separately, so a user running mismatched versions is
	the normal case, not the exotic one.
	"""
	if not data.has("format_version"):
		printerr("Import Error: layout has no 'format_version'.")
		_fail_report("level_layout.json carries no 'format_version', so it was "
			+ "written by an exporter older than the v2 schema (or is not a "
			+ "layout file at all). Update the Unreal plugin and export again.")
		return false

	var raw_ver = data["format_version"]
	if not (raw_ver is int or raw_ver is float):
		printerr("Import Error: layout has invalid 'format_version': ", raw_ver)
		_fail_report("level_layout.json has a non-numeric 'format_version' (%s)." % str(raw_ver))
		return false
	var found := int(raw_ver)
	if found == SUPPORTED_FORMAT_VERSION:
		return true

	var stale_half := "The Unreal plugin is newer than the addon -- update addons/unreal_importer to a matching release."
	if found < SUPPORTED_FORMAT_VERSION:
		stale_half = "The Unreal plugin is older than the addon -- update the plugin and export the level again."
	printerr("Import Error: layout format_version %d, but this addon supports %d."
		% [found, SUPPORTED_FORMAT_VERSION])
	_fail_report("level_layout.json is format_version %d, but this Godot addon understands version %d. %s"
		% [found, SUPPORTED_FORMAT_VERSION, stale_half])
	return false


func component_world_transform(comp_data: Dictionary, actor_transform: Transform3D) -> Transform3D:
	"""Resolves a mesh component's absolute placement in Godot space.

	Do NOT compose actor * relative here. Unreal reports get_relative_transform()
	against a component's IMMEDIATE parent component, and an actor's ROOT component
	has no parent component -- its relative transform is already the world transform.
	Composing therefore double-applies the placement for every plain StaticMeshActor,
	and for a component nested two levels deep inside a Blueprint it silently drops
	the intermediate transform. The exporter writes each component's absolute world
	transform, so use that and never accumulate.
	"""
	if USE_GDSCRIPT_TRANSFORM_CONVERSION:
		var u_trans = comp_data.get("unreal_world_transform")
		if u_trans is Dictionary:
			var u_loc = u_trans.get("translation")
			var u_rot = u_trans.get("rotation_quat")
			var u_scale = u_trans.get("scale")
			return convert_unreal_to_godot_transform(
				u_loc if u_loc is Array else [0.0, 0.0, 0.0],
				u_rot if u_rot is Array else [0.0, 0.0, 0.0, 1.0],
				u_scale if u_scale is Array else [1.0, 1.0, 1.0]
			)
	else:
		var g_trans = comp_data.get("godot_world_transform")
		if g_trans is Dictionary:
			return get_transform_from_dict(g_trans)
	# Layout predates the world transform: fall back to the actor's own placement,
	# which is correct for the single-root-component actors those layouts contain.
	return actor_transform


func find_gltf_path(folder: String, mesh_name: String) -> String:
	"""Searches for a .gltf/.glb file with matching mesh name (case-insensitive)."""
	return Common.find_model_path(folder, mesh_name)


func find_model_for(folder: String, mesh_key: String, mesh_name: String) -> String:
	"""Resolves a model file by its collision-safe export key, falling back to
	the raw mesh name for v1 layouts or partial exports."""
	var path := Common.find_model_path(folder, mesh_key)
	if path == "" and mesh_name != "" and mesh_name != mesh_key:
		path = Common.find_model_path(folder, mesh_name)
	return path


func _load_feature(file_name: String):
	"""Instantiates an optional feature module from the addon folder."""
	var path := "res://addons/unreal_importer/" + file_name
	if not ResourceLoader.exists(path):
		return null
	var script = load(path)
	if script == null:
		return null
	return script.new()

func instance_gltf(gltf_path: String) -> Node3D:
	"""Loads and instances a glTF scene, supporting both resource load and external files."""
	if gltf_path.begins_with("res://"):
		var gltf_scene = load(gltf_path)
		if gltf_scene:
			return gltf_scene.instantiate() as Node3D
	
	# Fallback for external paths or if load fails
	print("Warning: Loading external glTF model: ", gltf_path, ". It is highly recommended to place glTF assets within the 'res://' directory to ensure correct scene serialization, skinning, and to prevent editor lag/crashes.")
	var doc := GLTFDocument.new()
	var state := GLTFState.new()
	var err = doc.append_from_file(gltf_path, state)
	if err == OK:
		return doc.generate_scene(state) as Node3D
	else:
		printerr("Failed to load external glTF model: ", gltf_path, " Error code: ", err)
	
	return null

func create_placeholder(parent: Node, name_str: String, local_transform: Transform3D, mesh_name: String) -> void:
	"""Creates a Marker3D node in the editor to serve as a placement reminder."""
	var marker := Marker3D.new()
	marker.name = name_str + "_MISSING_" + mesh_name
	parent.add_child(marker)
	marker.owner = active_scene_root
	marker.transform = local_transform
	# Print to console
	print("Created placeholder for missing mesh: ", mesh_name, " at local position: ", local_transform.origin)

func _set_owner_recursive(node: Node, owner: Node) -> void:
	"""Recursively sets the owner on all descendants so they persist in the saved scene."""
	# If this node is a scene instance, we do not traverse into its children!
	# Godot will serialize it as an instance of the sub-scene.
	if node != owner and node.scene_file_path != "":
		return
		
	for child in node.get_children():
		# Skip nodes that are already correctly owned by the target scene root
		if child.owner == owner:
			continue
		child.owner = owner
		_set_owner_recursive(child, owner)

func get_transform_from_dict(t_dict: Dictionary) -> Transform3D:
	"""Constructs a Transform3D from a JSON transform dictionary."""
	return Common.get_transform_from_dict(t_dict)

func convert_unreal_to_godot_transform(u_loc: Array, u_rot_quat: Array, u_scale: Array) -> Transform3D:
	"""
	Option B: GDScript implementation of coordinate system translation:
	- Left-handed to Right-handed conversion.
	- Centimeter to Meter scale conversion (x 0.01).
	- Remaps rotation matrix columns to correct axes.
	"""
	# 1. Translation: cm -> meters and axes swap with safety bounds checks
	var ux = u_loc[0] if u_loc.size() > 0 else 0.0
	var uy = u_loc[1] if u_loc.size() > 1 else 0.0
	var uz = u_loc[2] if u_loc.size() > 2 else 0.0
	var translation := Vector3(uy * 0.01, uz * 0.01, -ux * 0.01)
	
	# 2. Scale axes swap with safety bounds checks
	var sx = u_scale[0] if u_scale.size() > 0 else 1.0
	var sy = u_scale[1] if u_scale.size() > 1 else 1.0
	var sz = u_scale[2] if u_scale.size() > 2 else 1.0
	var scale := Vector3(sy, sz, sx)
	
	# 3. Rotation (Remap 3x3 Basis using C * R_unreal * C^T) with safety bounds checks
	var qx: float = u_rot_quat[0] if u_rot_quat.size() > 0 else 0.0
	var qy: float = u_rot_quat[1] if u_rot_quat.size() > 1 else 0.0
	var qz: float = u_rot_quat[2] if u_rot_quat.size() > 2 else 0.0
	var qw: float = u_rot_quat[3] if u_rot_quat.size() > 3 else 1.0
	var qlen: float = sqrt(qx * qx + qy * qy + qz * qz + qw * qw)
	if qlen > 1e-12:
		qx /= qlen
		qy /= qlen
		qz /= qlen
		qw /= qlen
	else:
		qx = 0.0
		qy = 0.0
		qz = 0.0
		qw = 1.0
	
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
	# Scale each basis COLUMN by the matching axis (scaled_local), so the result
	# equals C * (R_unreal * S_unreal) * C^T. Basis.scaled() scales rows instead,
	# which applies the scale in the parent frame and shears rotated actors.
	basis = basis.scaled_local(scale)

	return Transform3D(basis, translation)

func setup_physics_body(parent: Node, node_name: String, transform: Transform3D, mesh_name: String, meshes_lib: Dictionary) -> StaticBody3D:
	"""
	Optionally generates a StaticBody3D node with collision shapes (Box, Sphere, Capsule, Convex)
	based on the Unreal Engine collision data for the mesh.

	`transform` is the component's full placement. Any scale Jolt cannot apply to a
	shape is stripped off the body here and baked into the shapes instead (see
	Common.physics_body_transform); the caller must put the same scale on the mesh
	instance it parents under the returned body, via Common.physics_body_scale.
	"""
	var mesh_data = meshes_lib.get(mesh_name, {})
	if not (mesh_data is Dictionary):
		return null
	var collision = mesh_data.get("collision", null)

	if collision == null or not (collision is Dictionary):
		# No collision data defined for this mesh
		return null

	# Create the StaticBody3D node
	var body := StaticBody3D.new()
	body.name = node_name
	parent.add_child(body)
	body.owner = active_scene_root
	body.transform = Common.physics_body_transform(transform)

	# Vector3.ONE for a uniformly scaled prop, in which case every shape below is
	# built exactly as it was before and the body keeps its scale.
	var scale := Common.physics_body_scale(transform)
	if scale != Vector3.ONE:
		_scale_baked[mesh_name] = true

	# 1. Generate Box Colliders
	var boxes = collision.get("boxes")
	if boxes is Array:
		for box_data in boxes:
			if not (box_data is Dictionary):
				continue
			var size_arr = box_data.get("size", [0.0, 0.0, 0.0])
			if not (size_arr is Array) or size_arr.size() < 3:
				continue
			var local_trans = box_data.get("godot_local_transform", {})

			var shape_node := CollisionShape3D.new()
			shape_node.name = "BoxCollision"
			body.add_child(shape_node)
			shape_node.owner = active_scene_root

			var local := get_transform_from_dict(local_trans)
			var box_shape := BoxShape3D.new()
			# Full extents cm -> m, remapped into the glTF axis order (X, Z, Y) so
			# the box lines up with the mesh it hugs (matches gltf_local_shape_transform).
			box_shape.size = (Vector3(absf(float(size_arr[0])), absf(float(size_arr[2])), absf(float(size_arr[1]))) * 0.01) \
				* Common.scaled_axis_factors(local.basis, scale)
			shape_node.shape = box_shape
			shape_node.transform = Common.scaled_shape_transform(local, scale)

	# 2. Generate Sphere Colliders
	var spheres = collision.get("spheres")
	if spheres is Array:
		for sphere_data in spheres:
			if not (sphere_data is Dictionary):
				continue
			var radius: float = absf(Common.get_num(sphere_data, "radius", 0.0))
			if radius <= 0.0:
				continue
			var local_trans = sphere_data.get("godot_local_transform", {})

			var shape_node := CollisionShape3D.new()
			shape_node.name = "SphereCollision"
			body.add_child(shape_node)
			shape_node.owner = active_scene_root

			var sphere_shape := SphereShape3D.new()
			# A sphere has no way to express a non-uniform scale, so it settles for
			# the mean -- the one shape where the baking is still an approximation.
			sphere_shape.radius = radius * 0.01 * Common.uniform_factor(scale)
			if scale != Vector3.ONE:
				_note_approximate_collision(mesh_name, "sphere")
			shape_node.shape = sphere_shape
			shape_node.transform = Common.scaled_shape_transform(
				get_transform_from_dict(local_trans), scale)

	# 3. Generate Capsule (Sphyl) Colliders
	var capsules = collision.get("capsules")
	if capsules is Array:
		for cap_data in capsules:
			if not (cap_data is Dictionary):
				continue
			var radius: float = absf(Common.get_num(cap_data, "radius", 0.0))
			var length: float = absf(Common.get_num(cap_data, "length", 0.0))
			if radius <= 0.0:
				continue
			var local_trans = cap_data.get("godot_local_transform", {})

			var shape_node := CollisionShape3D.new()
			shape_node.name = "CapsuleCollision"
			body.add_child(shape_node)
			shape_node.owner = active_scene_root

			var local := get_transform_from_dict(local_trans)
			# A Godot capsule runs along its local Y, so Y stretches the cylinder
			# and X/Z stretch the radius -- and the radius can only take one of
			# them, so unequal X/Z is the second approximate case.
			var factors := Common.scaled_axis_factors(local.basis, scale)
			var radial := 0.5 * (factors.x + factors.z)
			if absf(factors.x - factors.z) > 0.001:
				_note_approximate_collision(mesh_name, "capsule")

			var capsule_shape := CapsuleShape3D.new()
			capsule_shape.radius = radius * 0.01 * radial
			# Godot capsule height is cylinder length + 2 * radius
			capsule_shape.height = (length * factors.y + 2.0 * radius * radial) * 0.01
			shape_node.shape = capsule_shape
			shape_node.transform = Common.scaled_shape_transform(local, scale)

	# 4. Generate Convex Hulls
	var convex_hulls = collision.get("convex_hulls")
	if convex_hulls is Array:
		for convex_data in convex_hulls:
			if not (convex_data is Dictionary):
				continue
			var verts_raw = convex_data.get("vertices", [])
			if not (verts_raw is Array) or (verts_raw as Array).is_empty():
				continue
			var verts_arr: Array = verts_raw
			var local_trans = convex_data.get("godot_local_transform", {})

			var shape_node := CollisionShape3D.new()
			shape_node.name = "ConvexCollision"
			body.add_child(shape_node)
			shape_node.owner = active_scene_root

			var local := get_transform_from_dict(local_trans)
			# A hull is a raw point cloud, so it takes the body scale EXACTLY --
			# shear, mirror and all -- expressed in the shape's own frame:
			# B^-1 * S * B, which is identity whenever there is nothing to bake.
			var point_map := Basis.from_scale(scale)
			if absf(local.basis.determinant()) > 1e-12:
				point_map = local.basis.inverse() * Basis.from_scale(scale) * local.basis

			var convex_shape := ConvexPolygonShape3D.new()
			var points = PackedVector3Array()
			for v in verts_arr:
				if not (v is Array) or v.size() < 3:
					continue
				# glTF axis order (X, Z, Y), cm -> m, matching the mesh geometry.
				points.append(point_map * (Vector3(float(v[0]), float(v[2]), float(v[1])) * 0.01))
			if points.is_empty():
				shape_node.queue_free()
				continue
			convex_shape.points = points

			shape_node.shape = convex_shape
			shape_node.transform = Common.scaled_shape_transform(local, scale)

	return body


func _note_approximate_collision(mesh_name: String, shape_kind: String) -> void:
	"""Records the two cases where baking a non-uniform scale into a shape cannot
	be exact, so the report can name them instead of leaving the user to notice."""
	_scale_approximated["%s (%s)" % [mesh_name, shape_kind]] = true

func apply_materials_to_instance(instanced_mesh: Node3D, mesh_name: String, override_mats: Array, meshes_lib: Dictionary) -> void:
	"""
	Compiles and applies default and overridden materials to all child MeshInstance3D nodes
	found inside the instanced scene root, matching by material name and slot.
	Override materials (per-instance) always create fresh materials to support unique colors.
	Default materials (shared base) use the material cache for efficiency.
	"""
	var mesh_data_raw = meshes_lib.get(mesh_name)
	var mesh_data: Dictionary = mesh_data_raw if mesh_data_raw is Dictionary else {}
	var default_mats_raw = mesh_data.get("materials")
	var default_mats: Array = default_mats_raw if default_mats_raw is Array else []
	
	# 1. Find all MeshInstance3D nodes recursively inside the sub-tree
	var mesh_instances: Array[MeshInstance3D] = []
	_find_mesh_instances_recursive(instanced_mesh, mesh_instances)
	
	# Helper to find a material entry in a list by name or slot index
	var find_in_list = func(mat_list: Array, mat_name: String, slot_idx: int) -> Dictionary:
		# By name first
		for mat in mat_list:
			if not (mat is Dictionary):
				continue
			var o_name: String = Common.get_str(mat, "material_name", "")
			if o_name != "" and o_name != "None" and mat_name != "" and o_name.to_lower() == mat_name.to_lower():
				return mat
		# By slot index
		for mat in mat_list:
			if not (mat is Dictionary):
				continue
			if int(mat.get("slot_index", -1)) == slot_idx:
				return mat
		return {}

	# Apply overrides on a per-MeshInstance3D basis
	for mesh_inst in mesh_instances:
		if not mesh_inst.mesh:
			continue
		var surface_count: int = mesh_inst.mesh.get_surface_count()
		for slot_idx in range(surface_count):
			var existing_mat = mesh_inst.get_active_material(slot_idx)
			if not existing_mat:
				existing_mat = mesh_inst.mesh.surface_get_material(slot_idx)
				
			var mat_name = ""
			if existing_mat:
				mat_name = existing_mat.resource_name
			
			# Resolve the default material first so we know the true Unreal slot_index
			# even when the glTF dropped unused material slots and shifted indices.
			var default_data = find_in_list.call(default_mats, mat_name, slot_idx)
			var unreal_slot_idx: int = int(default_data.get("slot_index", slot_idx)) if default_data.size() > 0 else slot_idx

			# Check override_mats by slot index (not by the default material's name,
			# which fails when slots are swapped or overrides carry a different name).
			var override_data: Dictionary = {}
			for mat_entry in override_mats:
				if mat_entry is Dictionary and int(mat_entry.get("slot_index", -1)) == unreal_slot_idx:
					override_data = mat_entry
					break
			if override_data.size() > 0:
				var params = override_data.get("parameters", {})
				if params is Dictionary and not params.is_empty():
					var override_name: String = Common.get_str(override_data, "material_name", mat_name)
					# Only duplicate existing_mat if the override is the same material;
					# otherwise duplicating leaks the default slot's textures onto the override.
					var base_mat: Material = existing_mat if (override_name != "" and override_name.to_lower() == mat_name.to_lower()) else null
					var godot_mat = create_godot_material(params, base_mat)
					godot_mat.resource_name = override_name if override_name != "" else mat_name
					mesh_inst.set_surface_override_material(slot_idx, godot_mat)
				continue
			
			# Check default_mats (shared base, USE cache)
			if default_data.size() > 0:
				var mat_path: String = Common.get_str(default_data, "material_path", "")
				var resolved_name: String = mat_name if mat_name != "" else Common.get_str(default_data, "material_name", "")
				var params = default_data.get("parameters", {})
				if params is Dictionary and not params.is_empty():
					var godot_mat = get_or_create_material(resolved_name, mat_path, params, existing_mat)
					mesh_inst.set_surface_override_material(slot_idx, godot_mat)

func _find_mesh_instances_recursive(node: Node, list: Array[MeshInstance3D]) -> void:
	"""Recursively finds all MeshInstance3D nodes in a node tree."""
	if node is MeshInstance3D:
		list.append(node)
	for child in node.get_children():
		_find_mesh_instances_recursive(child, list)


func _resolve_folder_parent(root_container: Node, folder_path: String, cache: Dictionary) -> Node:
	"""Creates or reuses nested identity Node3D folders mirroring Unreal's World Outliner."""
	var clean := folder_path.replace("\\", "/").strip_edges()
	var parts := clean.split("/", false)
	var parent: Node = root_container
	var key := ""
	for seg in parts:
		var seg_clean := str(seg).strip_edges()
		if seg_clean == "":
			continue
		key = seg_clean if key == "" else (key + "/" + seg_clean)
		if cache.has(key):
			parent = cache[key]
		else:
			var folder_node := Node3D.new()
			folder_node.name = seg_clean
			Common.add_owned_child(parent, folder_node, active_scene_root)
			cache[key] = folder_node
			parent = folder_node
	return parent


func _apply_component_render_flags(target_node: Node, instanced_mesh: Node, comp_data: Dictionary) -> void:
	"""Applies per-component visibility, shadow casting, and max draw distance."""
	if comp_data.has("visible") and not bool(comp_data.get("visible", true)):
		if target_node is Node3D:
			(target_node as Node3D).visible = false
	var cast_shadow := bool(comp_data.get("cast_shadow", true))
	var max_draw = comp_data.get("max_draw_distance_m")
	var has_max_draw: bool = (max_draw is float or max_draw is int) and float(max_draw) > 0.0
	if not cast_shadow or has_max_draw:
		var instances: Array[MeshInstance3D] = []
		_find_mesh_instances_recursive(target_node if target_node != null else instanced_mesh, instances)
		for mi in instances:
			if not cast_shadow:
				mi.cast_shadow = GeometryInstance3D.SHADOW_CASTING_SETTING_OFF
			# Only set visibility_range_end when no LOD band already capped it.
			if has_max_draw and absf(mi.visibility_range_end) < 1e-4:
				mi.visibility_range_end = float(max_draw)


func _set_visibility_range_recursive(node: Node, range_begin: float, range_end: float) -> void:
	"""Applies Godot 4 visibility_range_begin / visibility_range_end to every
	MeshInstance3D in a glTF subtree for LOD distance switching."""
	var instances: Array[MeshInstance3D] = []
	_find_mesh_instances_recursive(node, instances)
	for mi in instances:
		mi.visibility_range_begin = range_begin
		mi.visibility_range_end = range_end


func _estimate_mesh_radius(node: Node) -> float:
	"""Estimates the bounding-sphere radius (m) of a MeshInstance3D subtree for
	auto-discovered LOD chains that lack explicit switch_distance_m values."""
	var instances: Array[MeshInstance3D] = []
	_find_mesh_instances_recursive(node, instances)
	var max_r := 0.0
	for mi in instances:
		if mi.mesh:
			var aabb := mi.mesh.get_aabb()
			max_r = maxf(max_r, aabb.size.length() * 0.5)
	return max_r if max_r > 0.01 else 1.0


func _apply_lod_chain(lod0_node: Node3D, lod_parent: Node, lod_transform: Transform3D,
		mesh_key: String, mesh_name: String, overrides: Array, meshes_lib: Dictionary) -> int:
	"""Instances exported <mesh_key>_LOD1.gltf .. _LODn.gltf files and configures
	non-overlapping visibility_range bands on LOD0 and every LOD sibling.
	Returns the number of additional LOD levels wired (0 when no LOD1+ exists)."""
	var mesh_entry = meshes_lib.get(mesh_key, {})
	var raw_lods = mesh_entry.get("lods") if mesh_entry is Dictionary else null
	var candidates: Array = []

	if raw_lods is Array and (raw_lods as Array).size() > 1:
		for item in raw_lods:
			if not (item is Dictionary):
				continue
			var idx := int(item.get("lod_index", 0))
			if idx <= 0:
				continue
			var exp_name := Common.get_str(item, "export_name", "%s_LOD%d" % [mesh_key, idx])
			var path := find_model_for(active_models_folder, exp_name, "%s_LOD%d" % [mesh_name, idx])
			if path == "":
				continue
			candidates.append({
				"lod_index": idx,
				"path": path,
				"switch_distance_m": Common.get_num(item, "switch_distance_m", 0.0),
			})
	else:
		# Auto-discover _LOD1.._LOD7 on disk even when the layout JSON predates "lods".
		var radius_m := _estimate_mesh_radius(lod0_node)
		var tan_half_fov := tan(deg_to_rad(37.5))
		var default_ss := [0.5, 0.25, 0.125, 0.0625, 0.03125, 0.015625, 0.0078125]
		for idx in range(1, 8):
			var exp_name := "%s_LOD%d" % [mesh_key, idx]
			var path := find_model_for(active_models_folder, exp_name, "%s_LOD%d" % [mesh_name, idx])
			if path == "":
				break
			var ss: float = default_ss[idx - 1]
			candidates.append({
				"lod_index": idx,
				"path": path,
				"switch_distance_m": radius_m / (ss * tan_half_fov),
			})

	if candidates.is_empty():
		return 0

	candidates.sort_custom(func(a, b): return int(a["lod_index"]) < int(b["lod_index"]))

	# Ensure strictly increasing switch distances.
	var prev_dist := 0.0
	for c in candidates:
		var d := float(c["switch_distance_m"])
		if d <= prev_dist:
			d = prev_dist + 10.0
		c["switch_distance_m"] = d
		prev_dist = d

	# Set LOD0 visibility end at LOD1's switch distance BEFORE adding LOD1..N as children
	# of lod0_node (otherwise _set_visibility_range_recursive would overwrite them).
	_set_visibility_range_recursive(lod0_node, 0.0, float(candidates[0]["switch_distance_m"]))

	var wired := 0
	for i in range(candidates.size()):
		var entry: Dictionary = candidates[i]
		var lod_inst := instance_gltf(str(entry["path"]))
		if lod_inst == null:
			continue
		apply_materials_to_instance(lod_inst, mesh_key, overrides, meshes_lib)
		var r_begin := float(entry["switch_distance_m"])
		var r_end := float(candidates[i + 1]["switch_distance_m"]) if (i + 1 < candidates.size()) else 0.0
		_set_visibility_range_recursive(lod_inst, r_begin, r_end)
		lod_inst.name = "%s_LOD%d" % [mesh_name if mesh_name != "" else mesh_key, int(entry["lod_index"])]
		lod_parent.add_child(lod_inst)
		lod_inst.owner = active_scene_root
		_set_owner_recursive(lod_inst, active_scene_root)
		lod_inst.transform = lod_transform
		lod_inst.set_meta("unreal_lod_index", int(entry["lod_index"]))
		wired += 1

	return wired

func get_or_create_material(mat_name: String, mat_path: String, params: Dictionary, existing_mat: Material = null) -> BaseMaterial3D:
	"""
	Material instantiation cache manager. Reuses existing StandardMaterial3D/ORMMaterial3D instances
	by path or name to preserve shared materials across mesh instances.
	When options.save_external_materials is enabled, saves shared materials as reusable
	res://materials/<name>.tres files and reuses already-saved .tres files on disk.
	"""
	var has_path := mat_path != "" and mat_path != "None"
	var has_name := mat_name != "" and mat_name != "None"
	if has_path and material_cache.has(mat_path):
		return material_cache[mat_path]
	if has_name and material_cache.has(mat_name):
		return material_cache[mat_name]

	var tres_path := ""
	if bool(import_options.get("save_external_materials", false)) and (has_name or has_path):
		tres_path = _external_material_res_path(mat_name, mat_path)
		if tres_path != "" and ResourceLoader.exists(tres_path):
			var existing_res = ResourceLoader.load(tres_path)
			if existing_res is BaseMaterial3D:
				if has_path:
					material_cache[mat_path] = existing_res
				elif has_name:
					material_cache[mat_name] = existing_res
				return existing_res as BaseMaterial3D

	var mat = create_godot_material(params, existing_mat)
	mat.resource_name = mat_name

	if tres_path != "":
		var dir_global := ProjectSettings.globalize_path(tres_path.get_base_dir())
		if dir_global != "":
			DirAccess.make_dir_recursive_absolute(dir_global)
		var save_err := ResourceSaver.save(mat, tres_path)
		if save_err == OK:
			var reloaded = ResourceLoader.load(tres_path)
			if reloaded is BaseMaterial3D:
				mat = reloaded as BaseMaterial3D
		else:
			push_warning("Unreal Importer: could not save external material %s (%s)"
				% [tres_path, error_string(save_err)])

	if has_path:
		material_cache[mat_path] = mat
	elif has_name:
		material_cache[mat_name] = mat

	return mat


func _external_material_res_path(mat_name: String, mat_path: String) -> String:
	"""Returns a collision-safe res://materials/<name>.tres path for a material."""
	var folder := str(import_options.get("materials_folder", "res://materials/"))
	if not folder.begins_with("res://"):
		return ""
	var raw_stem := mat_name if (mat_name != "" and mat_name != "None") else mat_path.get_file().get_basename()
	var safe := ""
	for i in raw_stem.length():
		var c := raw_stem.unicode_at(i)
		var ch := raw_stem[i]
		var ok := (c >= 97 and c <= 122) or (c >= 65 and c <= 90) or (c >= 48 and c <= 57) or ch == "_" or ch == "-"
		safe += ch if ok else "_"
	safe = safe.strip_edges()
	if safe == "":
		safe = "Material"
	var key := safe.to_lower()
	var identity := mat_path if (mat_path != "" and mat_path != "None") else mat_name
	if _external_mat_paths.has(key) and _external_mat_paths[key] != identity:
		safe = "%s_%s" % [safe, identity.md5_text().substr(0, 8)]
	else:
		_external_mat_paths[key] = identity
	return folder.path_join(safe + ".tres")


func _collect_normal_map_targets(data: Dictionary) -> Dictionary:
	"""Collects lowercase normal-map filename stems -> bool (should_invert_y).

	Reads explicit `texture_metadata` first (carrying Unreal's own
	`flip_green_channel` flag), and falls back to scanning normal_texture /
	decal normal references so older v2 layouts without texture_metadata still
	get DirectX -> OpenGL Y-flipping.
	"""
	var targets := {}
	var tex_meta = data.get("texture_metadata")
	if tex_meta is Dictionary:
		for raw_name in tex_meta:
			var entry = tex_meta[raw_name]
			if entry is Dictionary and bool(entry.get("is_normal_map", false)):
				var ue_flipped := bool(entry.get("flip_green_channel", false))
				targets[str(raw_name).to_lower()] = not ue_flipped

	# Fallback scan of material/decal normal slots (for layouts predating texture_metadata).
	var record_normal := func(raw_name) -> void:
		var s := _safe_str(raw_name)
		if s == "":
			return
		var key := s.to_lower()
		if not targets.has(key):
			targets[key] = true

	var meshes = data.get("meshes")
	if meshes is Dictionary:
		for mesh_entry in meshes.values():
			if not (mesh_entry is Dictionary):
				continue
			for mat in (mesh_entry.get("materials") if mesh_entry.get("materials") is Array else []):
				if mat is Dictionary and mat.get("parameters") is Dictionary:
					record_normal.call(mat["parameters"].get("normal_texture"))
	for actor in (data.get("actors") if data.get("actors") is Array else []):
		if not (actor is Dictionary):
			continue
		for comp in (actor.get("components") if actor.get("components") is Array else []):
			if not (comp is Dictionary):
				continue
			for ov in (comp.get("material_overrides") if comp.get("material_overrides") is Array else []):
				if ov is Dictionary and ov.get("parameters") is Dictionary:
					record_normal.call(ov["parameters"].get("normal_texture"))
	for fol in (data.get("foliage") if data.get("foliage") is Array else []):
		if not (fol is Dictionary):
			continue
		for ov in (fol.get("material_overrides") if fol.get("material_overrides") is Array else []):
			if ov is Dictionary and ov.get("parameters") is Dictionary:
				record_normal.call(ov["parameters"].get("normal_texture"))
	for decal in (data.get("decals") if data.get("decals") is Array else []):
		if decal is Dictionary and decal.get("textures") is Dictionary:
			record_normal.call(decal["textures"].get("normal"))
	return targets


func _load_normal_texture(tex_path: String, tex_stem: String) -> Texture2D:
	"""Loads a normal map, inverting green in memory for external (non-res://)
	files that have no Godot .import file."""
	var want_flip: bool = bool(import_options.get("flip_normal_green", true)) \
		and bool(_normal_map_targets.get(tex_stem.to_lower(), true))
	if not tex_path.begins_with("res://") and want_flip:
		if _external_normal_cache.has(tex_path):
			return _external_normal_cache[tex_path]
		var img := Image.load_from_file(tex_path)
		if img != null:
			var flipped := TextureLimit.invert_green_channel(img)
			var tex := ImageTexture.create_from_image(flipped)
			_external_normal_cache[tex_path] = tex
			return tex
	return load_texture(tex_path)


func _combine_albedo_and_opacity(albedo_path: String, opacity_path: String) -> Texture2D:
	"""Packs a standalone grayscale Unreal opacity texture into the alpha channel
	of albedo_texture (or white RGB if no albedo texture is set), since Godot's
	StandardMaterial3D reads alpha exclusively from albedo_texture.a."""
	var cache_key := "%s|%s" % [albedo_path, opacity_path]
	if _alpha_merged_cache.has(cache_key):
		return _alpha_merged_cache[cache_key]
	var op_img := Common.load_image_file(opacity_path)
	if op_img == null:
		return null
	var alb_img: Image = Common.load_image_file(albedo_path) if albedo_path != "" else null
	var w := alb_img.get_width() if alb_img != null else op_img.get_width()
	var h := alb_img.get_height() if alb_img != null else op_img.get_height()
	if w <= 0 or h <= 0:
		return null
	if op_img.get_width() != w or op_img.get_height() != h:
		op_img = op_img.duplicate()
		op_img.resize(w, h, Image.INTERPOLATE_BILINEAR)
	var out := Image.create(w, h, false, Image.FORMAT_RGBA8)
	for y in h:
		for x in w:
			var rgb := alb_img.get_pixel(x, y) if alb_img != null else Color.WHITE
			var a := op_img.get_pixel(x, y).r
			out.set_pixel(x, y, Color(rgb.r, rgb.g, rgb.b, a))
	var merged := ImageTexture.create_from_image(out)
	_alpha_merged_cache[cache_key] = merged
	return merged


func create_godot_material(params: Dictionary, existing_mat: Material = null) -> BaseMaterial3D:
	"""
	Creates a new StandardMaterial3D (or duplicates existing_mat if it is a BaseMaterial3D),
	maps Unreal scalar/vector/surface settings to Godot, and binds exported textures.
	"""
	var mat: BaseMaterial3D
	if existing_mat and existing_mat is BaseMaterial3D:
		mat = existing_mat.duplicate()
	else:
		mat = StandardMaterial3D.new()
	
	# 1. Map Albedo color
	mat.albedo_color = Common.color_from_array(params.get("albedo_color"), Color.WHITE)
	
	# 2. Map PBR multipliers
	# Unreal's scalar parameters are inputs to a shader graph and are not bound
	# to 0..1 -- real materials ship values like 4.57 and -0.27. Godot's
	# roughness/metallic are multiplied straight into the corresponding texture
	# channel, so an out-of-range value does not "look a bit off": 4.57 drives
	# every texel above 0.22 to fully rough, flattening the surface completely.
	# Clamping to the range Godot actually defines lets the packed map show
	# through, which is what the map is there for.
	mat.roughness = _clamp_unit(params.get("roughness", 0.5), 0.5, "roughness")
	mat.metallic = _clamp_unit(params.get("metallic", 0.0), 0.0, "metallic")
	
	# 3. Map Tiling scale
	var tiling_raw = params.get("tiling")
	var tile_u: float = 1.0
	var tile_v: float = 1.0
	if tiling_raw is Array:
		if tiling_raw.size() > 0 and (tiling_raw[0] is float or tiling_raw[0] is int):
			tile_u = float(tiling_raw[0])
		if tiling_raw.size() > 1 and (tiling_raw[1] is float or tiling_raw[1] is int):
			tile_v = float(tiling_raw[1])
	mat.uv1_scale = Vector3(tile_u, tile_v, 1.0)

	# 4. Blend mode, alpha cutout, opacity & two-sided culling
	var blend_mode := _safe_str(params.get("blend_mode")).to_lower()
	match blend_mode:
		"masked":
			mat.transparency = BaseMaterial3D.TRANSPARENCY_ALPHA_SCISSOR
			mat.alpha_scissor_threshold = clampf(
				Common.get_num(params, "opacity_mask_clip_value", 0.3333), 0.0, 1.0)
		"translucent":
			mat.transparency = BaseMaterial3D.TRANSPARENCY_ALPHA
			var op := clampf(Common.get_num(params, "opacity", 1.0), 0.0, 1.0)
			if op < 1.0:
				mat.albedo_color.a *= op
		"additive":
			mat.transparency = BaseMaterial3D.TRANSPARENCY_ALPHA
			mat.blend_mode = BaseMaterial3D.BLEND_MODE_ADD
			var op := clampf(Common.get_num(params, "opacity", 1.0), 0.0, 1.0)
			if op < 1.0:
				mat.albedo_color.a *= op
	if bool(params.get("two_sided", false)):
		mat.cull_mode = BaseMaterial3D.CULL_DISABLED

	# 5. Find and load textures (Only override if a valid file path is resolved on disk)
	var albedo_tex: String = _safe_str(params.get("albedo_texture"))
	var normal_tex: String = _safe_str(params.get("normal_texture"))
	var roughness_tex: String = _safe_str(params.get("roughness_texture"))
	var metallic_tex: String = _safe_str(params.get("metallic_texture"))
	var opacity_tex: String = _safe_str(params.get("opacity_texture"))
	var emissive_tex: String = _safe_str(params.get("emissive_texture"))
	
	# A texture the exporter named but that is not on disk used to fail in
	# silence, leaving a flat material and no clue why. Every miss is recorded.
	var albedo_path := ""
	if albedo_tex != "":
		albedo_path = find_texture_path(active_textures_folder, albedo_tex)
		if albedo_path != "":
			var tex = load_texture(albedo_path)
			if tex:
				mat.albedo_texture = tex
		else:
			_note_missing_texture(albedo_tex)

	# Separate opacity mask: Godot reads alpha from albedo_texture.a, so pack
	# the grayscale opacity texture into albedo's alpha channel when needed.
	if opacity_tex != "" and (blend_mode == "masked" or blend_mode == "translucent" or blend_mode == "additive"):
		var opacity_path := find_texture_path(active_textures_folder, opacity_tex)
		if opacity_path != "":
			var merged := _combine_albedo_and_opacity(albedo_path, opacity_path)
			if merged != null:
				mat.albedo_texture = merged
		else:
			_note_missing_texture(opacity_tex)

	if normal_tex != "":
		var tex_path = find_texture_path(active_textures_folder, normal_tex)
		if tex_path != "":
			var tex = _load_normal_texture(tex_path, normal_tex)
			if tex:
				mat.normal_enabled = true
				mat.normal_texture = tex
		else:
			_note_missing_texture(normal_tex)

	if roughness_tex != "":
		var tex_path = find_texture_path(active_textures_folder, roughness_tex)
		if tex_path != "":
			var tex = load_texture(tex_path)
			if tex:
				mat.roughness_texture = tex
		else:
			_note_missing_texture(roughness_tex)

	if metallic_tex != "":
		var tex_path = find_texture_path(active_textures_folder, metallic_tex)
		if tex_path != "":
			var tex = load_texture(tex_path)
			if tex:
				mat.metallic_texture = tex
		else:
			_note_missing_texture(metallic_tex)

	# 6. Emissive map, colour & energy
	var emissive_raw = params.get("emissive_color")
	var has_emissive_color := false
	var emissive_col := Color.BLACK
	if emissive_raw is Array and emissive_raw.size() >= 3:
		emissive_col = Common.color_from_array(emissive_raw, Color.BLACK)
		has_emissive_color = maxf(emissive_col.r, maxf(emissive_col.g, emissive_col.b)) > 0.0
	var emissive_energy := maxf(0.0, Common.get_num(params, "emissive_energy", 1.0))

	if emissive_tex != "":
		var tex_path := find_texture_path(active_textures_folder, emissive_tex)
		if tex_path != "":
			var tex := load_texture(tex_path)
			if tex:
				mat.emission_enabled = true
				mat.emission_texture = tex
				# Godot defaults to EMISSION_OP_ADD, which adds `emission` onto
				# dark pixels of emission_texture. Multiply matches Unreal's
				# EmissiveTexture * EmissiveColor shader idiom.
				mat.emission_operator = BaseMaterial3D.EMISSION_OP_MULTIPLY
				mat.emission = emissive_col if has_emissive_color else Color.WHITE
				mat.emission_energy_multiplier = emissive_energy
		else:
			_note_missing_texture(emissive_tex)
	elif has_emissive_color and emissive_energy > 0.0:
		mat.emission_enabled = true
		mat.emission = emissive_col
		mat.emission_energy_multiplier = emissive_energy

	_apply_packed_texture(mat, params)
	return mat


func _clamp_unit(value, fallback: float, label: String) -> float:
	"""Brings an Unreal scalar into the 0..1 range Godot defines for it.

	Reported once per distinct out-of-range value rather than per material: one
	bad parameter is shared by dozens of slots, and repeating it drowns the
	report it appears in.
	"""
	if not (value is float or value is int):
		return fallback
	var v := float(value)
	if v >= 0.0 and v <= 1.0:
		return v
	var clamped: float = clampf(v, 0.0, 1.0)
	var key := "%s=%.3f" % [label, v]
	if not _clamped_scalars.has(key):
		_clamped_scalars[key] = true
		push_warning("Unreal Importer: %s %.3f is outside Godot's 0..1 range; clamped to %.2f. %s"
			% [label, v, clamped,
			   "Unreal treats this as a shader-graph input; Godot multiplies it into the texture."])
		if report:
			report.warn("%s of %.3f clamped to %.2f (Unreal scalar outside Godot's 0..1 range)"
				% [label, v, clamped])
	return clamped


func _note_missing_texture(texture_name: String) -> void:
	"""Records a texture the layout referenced but that is not in the textures
	folder. Deduplicated: one missing 4K albedo is typically referenced by
	dozens of slots, and 40 identical lines bury everything else."""
	if _missing_textures.has(texture_name):
		return
	_missing_textures[texture_name] = true
	printerr("Unreal Importer: texture not found on disk: ", texture_name,
		" (looked in: ", active_textures_folder, ")")
	if report:
		report.note_missing_texture(texture_name)


func _apply_packed_texture(mat: BaseMaterial3D, params: Dictionary) -> void:
	"""Wires a packed PBR map (RMA/ORM/ARM/...) into roughness, metallic and AO.

	Unreal packs these three into one texture's RGB channels. Godot reads each
	from a channel selector, so no image processing is needed -- the same texture
	is assigned three times with a different channel each. The layout varies by
	convention (RMA is R=rough/G=metal/B=ao, ORM is R=ao/G=rough/B=metal), so the
	exporter sends explicit channel indices rather than us assuming one."""
	var packed_tex := _safe_str(params.get("packed_texture"))
	if packed_tex == "":
		return
	var tex_path := find_texture_path(active_textures_folder, packed_tex)
	if tex_path == "":
		_note_missing_texture(packed_tex)
		return
	var tex := load_texture(tex_path)
	if tex == null:
		return
	var channels = params.get("packed_channels", {})
	if not (channels is Dictionary):
		return

	if channels.has("roughness"):
		mat.roughness_texture = tex
		mat.roughness_texture_channel = int(channels["roughness"])
	if channels.has("metallic"):
		mat.metallic_texture = tex
		mat.metallic_texture_channel = int(channels["metallic"])
	if channels.has("ao"):
		mat.ao_enabled = true
		mat.ao_texture = tex
		mat.ao_texture_channel = int(channels["ao"])

func load_texture(tex_path: String) -> Texture2D:
	"""Loads a texture from res:// or from the external filesystem."""
	return Common.load_texture_file(tex_path)

func _safe_str(value) -> String:
	"""Converts a Variant (possibly null from JSON) to a String, returning '' for null."""
	if value == null:
		return ""
	return str(value)

func find_texture_path(folder: String, tex_name: String) -> String:
	"""
	Searches for matching texture file with various image extensions
	to support standard bulk exports (PNG, TGA, JPG, DDS).
	Falls back to active_models_folder if not found in the specified textures folder.
	"""
	# Ordered, case-insensitive search: textures folder, models folder, then the
	# textures/ directory next to the layout JSON (asset packs mix casings freely).
	var json_textures := ""
	var json_dir := str(import_options.get("json_dir", ""))
	if json_dir != "":
		json_textures = json_dir.path_join("textures")
	return Common.find_texture_path([folder, active_models_folder, json_textures], tex_name)
