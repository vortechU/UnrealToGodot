@tool
extends ScrollContainer

# Core UI Fields
var json_edit: LineEdit
var models_edit: LineEdit
var textures_edit: LineEdit
var convert_transforms_check: CheckBox
var import_btn: Button
var status_label: Label
var preview_label: Label
var editor_undo_redo: EditorUndoRedoManager = null

# Feature toggle fields (see docs/SCHEMA_V2.md import options)
var lights_check: CheckBox
var environment_check: CheckBox
var decals_check: CheckBox
var terrain_check: CheckBox
var terrain_mode_option: OptionButton
var foliage_check: CheckBox
var navigation_check: CheckBox
var navigation_bake_check: CheckBox
var metadata_check: CheckBox
var lods_check: CheckBox
var skel_physics_check: CheckBox
var probes_check: CheckBox
var cameras_check: CheckBox
var audio_check: CheckBox
var splines_check: CheckBox
var preserve_folders_check: CheckBox
var update_existing_check: CheckBox
var prune_removed_check: CheckBox
var extract_prefabs_check: CheckBox
var flip_normal_green_check: CheckBox
var save_external_mats_check: CheckBox
var energy_scale_spin: SpinBox
var decal_depth_spin: SpinBox
var texture_limit_option: OptionButton
var shrink_files_check: CheckBox

const DOCK_CONFIG_PATH := "user://unreal_importer_dock.cfg"

# Refactored importer script class reference
const ImporterClass = preload("res://addons/unreal_importer/import_unreal_layout.gd")
const Common = preload("res://addons/unreal_importer/import_common.gd")
const TextureLimit = preload("res://addons/unreal_importer/texture_import_limit.gd")
# For DEFAULT_DECAL_MIN_DEPTH_M, so the dock and the importer cannot disagree.
const EnvironmentClass = preload("res://addons/unreal_importer/import_environment.gd")

const TEXTURE_LIMIT_LABELS := ["No limit (as exported)", "512", "1024", "2048", "4096"]
const TEXTURE_LIMIT_VALUES := [0, 512, 1024, 2048, 4096]
# 1024 by default: a full-resolution import of a real level is slow, heavy, and
# can run Godot out of memory. Someone who wants the source art can say so.
const TEXTURE_LIMIT_DEFAULT_INDEX := 2

func _enter_tree() -> void:
	# Moving or floating the dock re-enters the tree with the UI already built.
	if get_child_count() > 0:
		return
	# Root Container
	var container := VBoxContainer.new()
	container.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	container.size_flags_vertical = Control.SIZE_EXPAND_FILL
	container.custom_minimum_size = Vector2(250, 0)
	add_child(container)
	
	# Margin setup
	var panel := PanelContainer.new()
	panel.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	panel.size_flags_vertical = Control.SIZE_EXPAND_FILL
	container.add_child(panel)
	
	var margin_container := MarginContainer.new()
	margin_container.add_theme_constant_override("margin_left", 12)
	margin_container.add_theme_constant_override("margin_right", 12)
	margin_container.add_theme_constant_override("margin_top", 12)
	margin_container.add_theme_constant_override("margin_bottom", 12)
	panel.add_child(margin_container)
	
	var vbox := VBoxContainer.new()
	vbox.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	vbox.add_theme_constant_override("separation", 10)
	margin_container.add_child(vbox)
	
	# Title
	var title_lbl := Label.new()
	title_lbl.text = "Unreal to Godot Importer"
	title_lbl.horizontal_alignment = HORIZONTAL_ALIGNMENT_CENTER
	title_lbl.add_theme_font_size_override("font_size", 15)
	vbox.add_child(title_lbl)
	
	var sep := HSeparator.new()
	vbox.add_child(sep)
	
	# --- Field 1: Layout JSON File ---
	var json_box := VBoxContainer.new()
	var json_lbl := Label.new()
	json_lbl.text = "Layout JSON File:"
	json_lbl.tooltip_text = "Select the exported level layout JSON file."
	json_box.add_child(json_lbl)
	
	var json_row := HBoxContainer.new()
	json_edit = LineEdit.new()
	json_edit.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	json_edit.text = "res://level_layout.json"
	json_row.add_child(json_edit)
	
	var json_btn := Button.new()
	json_btn.text = "Browse..."
	json_btn.pressed.connect(_on_browse_json)
	json_row.add_child(json_btn)
	json_box.add_child(json_row)
	vbox.add_child(json_box)
	
	# --- Field 2: glTF Models Folder ---
	var models_box := VBoxContainer.new()
	var models_lbl := Label.new()
	models_lbl.text = "glTF Models Folder:"
	models_lbl.tooltip_text = "Select the folder containing exported static mesh .gltf/.glb files."
	models_box.add_child(models_lbl)
	
	var models_row := HBoxContainer.new()
	models_edit = LineEdit.new()
	models_edit.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	models_edit.text = "res://models/"
	models_row.add_child(models_edit)
	
	var models_btn := Button.new()
	models_btn.text = "Browse..."
	models_btn.pressed.connect(_on_browse_models)
	models_row.add_child(models_btn)
	models_box.add_child(models_row)
	vbox.add_child(models_box)
	
	# --- Field 3: Textures Folder ---
	var textures_box := VBoxContainer.new()
	var textures_lbl := Label.new()
	textures_lbl.text = "Textures Folder:"
	textures_lbl.tooltip_text = "Select the folder containing exported texture files (png, tga, jpg, dds)."
	textures_box.add_child(textures_lbl)
	
	var textures_row := HBoxContainer.new()
	textures_edit = LineEdit.new()
	textures_edit.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	textures_edit.text = "res://textures/"
	textures_row.add_child(textures_edit)
	
	var textures_btn := Button.new()
	textures_btn.text = "Browse..."
	textures_btn.pressed.connect(_on_browse_textures)
	textures_row.add_child(textures_btn)
	textures_box.add_child(textures_row)
	vbox.add_child(textures_box)
	
	# --- Feature Toggles (everything is optional and user-controllable) ---
	# Folded away by default: these are ten-plus rows and, left open, they push
	# the Import button off the bottom of the dock. Everything a routine import
	# needs stays visible; the fine-grained toggles are one click under here.
	var features := _make_section(vbox, "Import Features", false)

	convert_transforms_check = CheckBox.new()
	convert_transforms_check.text = "GDScript Coordinate Swap"
	convert_transforms_check.tooltip_text = "Checked: Re-calculates swizzle (LH Z-up to RH Y-up) inside Godot.\nUnchecked: Uses pre-calculated values from JSON (Recommended)."
	convert_transforms_check.button_pressed = false
	features.add_child(convert_transforms_check)

	lights_check = _make_check(features,"Lights", "Create DirectionalLight3D / OmniLight3D / SpotLight3D nodes from every exported Unreal light component, with matching intensity, colour, cone, source size, shadows and distance fade.")
	environment_check = _make_check(features,"World Environment (post-fx, fog, sky)", "Create a WorldEnvironment from PostProcessVolume, height fog and sky data (bloom, SSAO, exposure, fog).")
	decals_check = _make_check(features,"Decals", "Create Decal nodes from Unreal decal components, binding exported textures, tint, visibility and distance fade.")
	terrain_check = _make_check(features,"Terrain (Landscapes)", "Rebuild Unreal Landscapes from the exported terrain/ heightmaps. Uses Terrain3D when installed (1.x and 0.9.x), otherwise a plugin-free mesh with collision. Either way the exported paint layers become a splat material tinted with each layer's Unreal debug colour, with a texture slot per layer for you to fill in.")

	var terrain_mode_row := HBoxContainer.new()
	var terrain_mode_lbl := Label.new()
	terrain_mode_lbl.text = "    Terrain mode:"
	terrain_mode_row.add_child(terrain_mode_lbl)
	terrain_mode_option = OptionButton.new()
	terrain_mode_option.add_item("Auto (Terrain3D if installed)")
	terrain_mode_option.add_item("Terrain3D")
	terrain_mode_option.add_item("HTerrain")
	terrain_mode_option.add_item("Mesh fallback")
	terrain_mode_option.selected = 0
	terrain_mode_option.tooltip_text = "Which terrain system to build with. Every mode falls back to the plugin-free mesh terrain if the plugin is unavailable. Terrain3D keeps the full exported heightmap resolution; the mesh fallback caps it at a 257x257 grid."
	terrain_mode_option.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	terrain_mode_row.add_child(terrain_mode_option)
	features.add_child(terrain_mode_row)

	foliage_check = _make_check(features,"Foliage (MultiMesh)", "Rebuild painted foliage and instanced meshes as MultiMeshInstance3D nodes (thousands of instances stay performant), with matching visibility, shadow casting, cull distances and material overrides.")
	navigation_check = _make_check(features,"Navigation Regions", "Create NavigationRegion3D nodes from Unreal NavMeshBoundsVolumes with matching agent settings.")
	navigation_bake_check = _make_check(features,"    Bake navigation on import", "Bake the navigation meshes immediately after import (synchronous; can take a moment on large levels).", false)
	metadata_check = _make_check(features,"Tags & Metadata", "Copy Unreal actor tags, classes and Blueprint variables onto nodes as metadata (get_meta()).")
	lods_check = _make_check(features,"LOD Chains (_LOD<n>.gltf)", "Instance exported _LOD1..N glTF meshes alongside LOD0 and wire Godot visibility_range switching from Unreal screen-size thresholds.", true)
	skel_physics_check = _make_check(features,"Skeletal Physics (_physics.json)", "Attach PhysicalBoneSimulator3D + PhysicalBone3D colliders onto imported Skeleton3D nodes from exported <mesh>_physics.json files.", true)
	probes_check = _make_check(features,"Reflection Probes", "Create ReflectionProbe nodes from exported BoxReflectionCapture and SphereReflectionCapture components.", true)
	cameras_check = _make_check(features,"Cameras", "Create Camera3D nodes from exported CameraActor / CineCameraActor components with matching horizontal FOV.", true)
	audio_check = _make_check(features,"Ambient Audio", "Create AudioStreamPlayer3D nodes from exported AmbientSound / AudioComponent actors and bind exported audio/*.wav streams.", true)
	splines_check = _make_check(features,"Spline Meshes & Cables", "Rebuild hanging cables, and deform SplineMeshComponent meshes along their exported Unreal cubic Hermite splines (roads, pipes, cables).", true)
	preserve_folders_check = _make_check(features,"Mirror Unreal Outliner Folders", "Recreate Unreal World Outliner folders as nested Node3D containers under UnrealStaticMeshes.", true)
	update_existing_check = _make_check(features,"Update existing scene (incremental)", "Match existing nodes by unreal_actor_id and update their transforms/metadata in place instead of duplicating actors, preserving scripts and custom child nodes.", false)
	prune_removed_check = _make_check(features,"    Remove deleted actors on update", "When incremental update is enabled, delete existing imported actors whose unreal_actor_id is no longer in the JSON.", false)
	extract_prefabs_check = _make_check(features,"Extract Blueprints as .tscn prefabs", "Saves multi-component Blueprint / PackedLevelActor templates once to res://prefabs/ and instances them so editing one prefab updates all copies.", false)
	flip_normal_green_check = _make_check(features,"Flip normal map green channel (DX -> GL)", "Converts Unreal DirectX (-Y) normal maps to Godot OpenGL (+Y) via Godot's non-destructive process/normal_map_invert_y import flag (skipping maps whose Unreal Flip Green Channel flag was already enabled).", true)
	save_external_mats_check = _make_check(features,"Save shared materials as .tres files", "Writes shared StandardMaterial3D resources to res://materials/ and reuses existing .tres files on re-import, keeping .tscn files smaller and allowing cross-scene material edits.", false)

	var energy_row := HBoxContainer.new()
	var energy_lbl := Label.new()
	energy_lbl.text = "Light energy scale:"
	energy_lbl.tooltip_text = "Global multiplier applied to converted light intensities. Raise or lower if the imported level looks too dark or too bright."
	energy_row.add_child(energy_lbl)
	energy_scale_spin = SpinBox.new()
	energy_scale_spin.min_value = 0.05
	energy_scale_spin.max_value = 10.0
	energy_scale_spin.step = 0.05
	energy_scale_spin.value = 1.0
	energy_scale_spin.tooltip_text = energy_lbl.tooltip_text
	energy_scale_spin.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	energy_row.add_child(energy_scale_spin)
	features.add_child(energy_row)

	var depth_row := HBoxContainer.new()
	var depth_lbl := Label.new()
	depth_lbl.text = "Min decal depth (m):"
	depth_lbl.tooltip_text = "Unreal packs often author decal boxes only a centimetre or two deep. Godot clips a decal to its box just as Unreal does, so on a wall with any surface relief such a box shreds the decal into slivers. This raises any decal thinner than the given projection depth. Set 0 to keep Unreal's depths exactly, at the cost of that shredding; raise it if decals still look patchy, lower it if a decal bleeds onto neighbouring geometry."
	depth_row.add_child(depth_lbl)
	decal_depth_spin = SpinBox.new()
	decal_depth_spin.min_value = 0.0
	decal_depth_spin.max_value = 1.0
	decal_depth_spin.step = 0.01
	decal_depth_spin.value = EnvironmentClass.DEFAULT_DECAL_MIN_DEPTH_M
	decal_depth_spin.tooltip_text = depth_lbl.tooltip_text
	decal_depth_spin.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	depth_row.add_child(decal_depth_spin)
	features.add_child(depth_row)

	var sep2 := HSeparator.new()
	vbox.add_child(sep2)
	
	# --- Action Button ---
	# Unreal exports its source art, so a real level arrives as gigabytes of 4K
	# PNGs. Unreal's own "Max Texture Resolution" cannot prevent that -- it
	# drives the cooked texture while the PNG exporter writes the source -- so
	# the cap has to be applied here.
	var tex_limit_row := HBoxContainer.new()
	var tex_limit_lbl := Label.new()
	tex_limit_lbl.text = "Texture size limit"
	tex_limit_lbl.tooltip_text = "Caps the resolution Godot imports textures at. Unreal exports full-resolution source art (often 4K), which is slow, heavy on VRAM, and large enough to crash Godot's importer. Applies to textures already imported as well as new ones."
	tex_limit_lbl.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	tex_limit_row.add_child(tex_limit_lbl)

	texture_limit_option = OptionButton.new()
	for item in TEXTURE_LIMIT_LABELS:
		texture_limit_option.add_item(item)
	texture_limit_option.selected = TEXTURE_LIMIT_DEFAULT_INDEX
	texture_limit_option.tooltip_text = tex_limit_lbl.tooltip_text
	texture_limit_option.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	texture_limit_option.item_selected.connect(_on_texture_limit_changed)
	tex_limit_row.add_child(texture_limit_option)
	vbox.add_child(tex_limit_row)

	# The limit above only changes what Godot loads; the exported PNGs stay 4K on
	# disk. This is what reclaims the space, and it is destructive, so it says so
	# plainly rather than quietly rewriting an export someone still needed.
	shrink_files_check = CheckBox.new()
	shrink_files_check.text = "Also shrink texture files on disk"
	shrink_files_check.button_pressed = true
	shrink_files_check.tooltip_text = "Rewrites exported PNGs larger than the limit at the capped size, which is usually the difference between a multi-gigabyte textures folder and a few hundred megabytes.\n\nDestructive: the full-resolution art only exists back in Unreal, so raising the limit later means exporting again. Only .png files named in the layout JSON are touched."
	vbox.add_child(shrink_files_check)

	import_btn = Button.new()
	import_btn.text = "Import Unreal Level Layout"
	import_btn.custom_minimum_size = Vector2(0, 36)
	import_btn.pressed.connect(_on_import_pressed)
	vbox.add_child(import_btn)

	# --- Pre-import Dry-Run Preview & Report Row ---
	var util_row := HBoxContainer.new()
	var preview_btn := Button.new()
	preview_btn.text = "Preview JSON"
	preview_btn.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	preview_btn.tooltip_text = "Inspect the selected layout JSON without modifying the scene: shows actor/mesh counts, missing models, and texture disk size."
	preview_btn.pressed.connect(_on_preview_pressed)
	util_row.add_child(preview_btn)

	var report_btn := Button.new()
	report_btn.text = "Open Import Report"
	report_btn.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	report_btn.tooltip_text = "Opens res://ue2g_import_report.txt from the last import run."
	report_btn.pressed.connect(_on_open_report_pressed)
	util_row.add_child(report_btn)
	vbox.add_child(util_row)

	preview_label = Label.new()
	preview_label.text = ""
	preview_label.visible = false
	preview_label.autowrap_mode = TextServer.AUTOWRAP_WORD_SMART
	preview_label.add_theme_color_override("font_color", Color(0.75, 0.85, 0.95))
	vbox.add_child(preview_label)

	# --- Utility: bind meshes into MultiMeshes of directly-generated .tscn scenes ---
	var bind_foliage_btn := Button.new()
	bind_foliage_btn.text = "Bind Foliage Meshes (.tscn scenes)"
	bind_foliage_btn.tooltip_text = "For scenes generated directly from Unreal (.tscn): loads each foliage MultiMesh's source glTF model and binds its mesh. Run once after opening the generated scene."
	bind_foliage_btn.pressed.connect(_on_bind_foliage_pressed)
	vbox.add_child(bind_foliage_btn)

	var build_skel_phys_btn := Button.new()
	build_skel_phys_btn.text = "Build Skeletal Physics (.tscn scenes)"
	build_skel_phys_btn.tooltip_text = "Attaches PhysicalBoneSimulator3D colliders from <mesh>_physics.json files to any Skeleton3D in the currently open scene."
	build_skel_phys_btn.pressed.connect(_on_build_skel_physics_pressed)
	vbox.add_child(build_skel_phys_btn)
	
	# --- Status Label ---
	status_label = Label.new()
	status_label.text = "Status: Ready"
	status_label.horizontal_alignment = HORIZONTAL_ALIGNMENT_CENTER
	status_label.autowrap_mode = TextServer.AUTOWRAP_WORD_SMART
	status_label.add_theme_color_override("font_color", Color(0.6, 0.6, 0.6))
	vbox.add_child(status_label)

	_load_dock_settings()


func _exit_tree() -> void:
	_save_dock_settings()


func _save_dock_settings(config_path: String = DOCK_CONFIG_PATH) -> void:
	if json_edit == null:
		return
	var cfg := ConfigFile.new()
	cfg.set_value("paths", "json", json_edit.text)
	cfg.set_value("paths", "models", models_edit.text)
	cfg.set_value("paths", "textures", textures_edit.text)
	cfg.set_value("options", "convert_transforms", convert_transforms_check.button_pressed)
	cfg.set_value("options", "lights", lights_check.button_pressed)
	cfg.set_value("options", "environment", environment_check.button_pressed)
	cfg.set_value("options", "decals", decals_check.button_pressed)
	cfg.set_value("options", "terrain", terrain_check.button_pressed)
	cfg.set_value("options", "terrain_mode", terrain_mode_option.selected)
	cfg.set_value("options", "foliage", foliage_check.button_pressed)
	cfg.set_value("options", "navigation", navigation_check.button_pressed)
	cfg.set_value("options", "navigation_bake", navigation_bake_check.button_pressed)
	cfg.set_value("options", "metadata", metadata_check.button_pressed)
	cfg.set_value("options", "lods", lods_check.button_pressed)
	cfg.set_value("options", "skel_physics", skel_physics_check.button_pressed)
	cfg.set_value("options", "probes", probes_check.button_pressed)
	cfg.set_value("options", "cameras", cameras_check.button_pressed)
	cfg.set_value("options", "audio", audio_check.button_pressed)
	cfg.set_value("options", "splines", splines_check.button_pressed)
	cfg.set_value("options", "preserve_folders", preserve_folders_check.button_pressed)
	cfg.set_value("options", "update_existing", update_existing_check.button_pressed)
	cfg.set_value("options", "prune_removed", prune_removed_check.button_pressed)
	cfg.set_value("options", "extract_prefabs", extract_prefabs_check.button_pressed)
	cfg.set_value("options", "flip_normal_green", flip_normal_green_check.button_pressed)
	cfg.set_value("options", "save_external_mats", save_external_mats_check.button_pressed)
	cfg.set_value("options", "energy_scale", energy_scale_spin.value)
	cfg.set_value("options", "decal_min_depth_m", decal_depth_spin.value)
	cfg.set_value("options", "texture_limit_index", texture_limit_option.selected)
	cfg.set_value("options", "shrink_files", shrink_files_check.button_pressed)
	cfg.save(config_path)


func _load_dock_settings(config_path: String = DOCK_CONFIG_PATH) -> void:
	var cfg := ConfigFile.new()
	if cfg.load(config_path) != OK:
		return
	json_edit.text = str(cfg.get_value("paths", "json", json_edit.text))
	models_edit.text = str(cfg.get_value("paths", "models", models_edit.text))
	textures_edit.text = str(cfg.get_value("paths", "textures", textures_edit.text))
	convert_transforms_check.button_pressed = bool(cfg.get_value("options", "convert_transforms", false))
	lights_check.button_pressed = bool(cfg.get_value("options", "lights", true))
	environment_check.button_pressed = bool(cfg.get_value("options", "environment", true))
	decals_check.button_pressed = bool(cfg.get_value("options", "decals", true))
	terrain_check.button_pressed = bool(cfg.get_value("options", "terrain", true))
	terrain_mode_option.selected = clampi(int(cfg.get_value("options", "terrain_mode", 0)), 0, terrain_mode_option.item_count - 1)
	foliage_check.button_pressed = bool(cfg.get_value("options", "foliage", true))
	navigation_check.button_pressed = bool(cfg.get_value("options", "navigation", true))
	navigation_bake_check.button_pressed = bool(cfg.get_value("options", "navigation_bake", false))
	metadata_check.button_pressed = bool(cfg.get_value("options", "metadata", true))
	lods_check.button_pressed = bool(cfg.get_value("options", "lods", true))
	skel_physics_check.button_pressed = bool(cfg.get_value("options", "skel_physics", true))
	probes_check.button_pressed = bool(cfg.get_value("options", "probes", true))
	cameras_check.button_pressed = bool(cfg.get_value("options", "cameras", true))
	audio_check.button_pressed = bool(cfg.get_value("options", "audio", true))
	splines_check.button_pressed = bool(cfg.get_value("options", "splines", true))
	preserve_folders_check.button_pressed = bool(cfg.get_value("options", "preserve_folders", true))
	update_existing_check.button_pressed = bool(cfg.get_value("options", "update_existing", false))
	prune_removed_check.button_pressed = bool(cfg.get_value("options", "prune_removed", false))
	extract_prefabs_check.button_pressed = bool(cfg.get_value("options", "extract_prefabs", false))
	flip_normal_green_check.button_pressed = bool(cfg.get_value("options", "flip_normal_green", true))
	save_external_mats_check.button_pressed = bool(cfg.get_value("options", "save_external_mats", false))
	energy_scale_spin.value = float(cfg.get_value("options", "energy_scale", 1.0))
	decal_depth_spin.value = float(cfg.get_value("options", "decal_min_depth_m", EnvironmentClass.DEFAULT_DECAL_MIN_DEPTH_M))
	texture_limit_option.selected = clampi(int(cfg.get_value("options", "texture_limit_index", TEXTURE_LIMIT_DEFAULT_INDEX)), 0, texture_limit_option.item_count - 1)
	shrink_files_check.button_pressed = bool(cfg.get_value("options", "shrink_files", true))


func _on_preview_pressed() -> void:
	var summary: Dictionary = ImporterClass.summarize_layout_json(json_edit.text, models_edit.text, textures_edit.text)
	preview_label.visible = true
	if not bool(summary.get("ok", false)):
		preview_label.text = "Preview error: " + str(summary.get("error", "Unknown error"))
		preview_label.add_theme_color_override("font_color", Color(0.95, 0.4, 0.4))
		return
	var tex_mb := float(summary.get("texture_bytes", 0)) / 1048576.0
	var lines := PackedStringArray([
		"Level: %s (schema v%d)" % [summary["level_name"], int(summary["format_version"])],
		"Actors: %d | Unique meshes: %d (missing: %d)" % [int(summary["actors"]), int(summary["unique_meshes"]), int(summary["missing_meshes"])],
		"Lights: %d | Decals: %d | Probes: %d | Cameras: %d | Audio: %d" % [
			int(summary["lights"]), int(summary["decals"]), int(summary["reflection_probes"]),
			int(summary["cameras"]), int(summary["audio"])],
		"Foliage groups: %d | Landscapes: %d | Textures: %.1f MB" % [
			int(summary["foliage_groups"]), int(summary["landscapes"]), tex_mb],
	])
	if int(summary.get("unloaded_sublevels", 0)) > 0:
		lines.append("Warning: %d streaming sublevel(s) were unloaded during export." % int(summary["unloaded_sublevels"]))
	if bool(summary.get("is_world_partition", false)):
		lines.append("Note: World Partition level (only loaded cells exported).")
	preview_label.text = "\n".join(lines)
	preview_label.add_theme_color_override("font_color",
		Color(0.95, 0.75, 0.3) if int(summary["missing_meshes"]) > 0 else Color(0.75, 0.9, 0.8))


func _on_open_report_pressed() -> void:
	var report_path := ProjectSettings.globalize_path("res://ue2g_import_report.txt")
	if not FileAccess.file_exists(report_path):
		status_label.text = "Status: No import report found yet (run an import first)."
		status_label.add_theme_color_override("font_color", Color(0.9, 0.7, 0.2))
		return
	OS.shell_open(report_path)

func _on_build_skel_physics_pressed() -> void:
	var scene_root: Node = EditorInterface.get_edited_scene_root()
	if not scene_root:
		status_label.text = "Error: Open a 3D scene first."
		status_label.add_theme_color_override("font_color", Color(0.9, 0.3, 0.3))
		return
	var skel_script = load("res://addons/unreal_importer/import_skeletal_physics.gd")
	if skel_script == null:
		status_label.text = "Error: import_skeletal_physics.gd missing."
		status_label.add_theme_color_override("font_color", Color(0.9, 0.3, 0.3))
		return
	var helper = skel_script.new()
	var bones := int(helper.attach_in_scene(scene_root, models_edit.text, scene_root))
	if bones > 0:
		EditorInterface.mark_scene_as_unsaved()
	status_label.text = "Status: Built %d skeletal physics bone(s)." % bones
	status_label.add_theme_color_override("font_color", Color(0.2, 0.8, 0.2) if bones > 0 else Color(0.6, 0.6, 0.6))


func _on_bind_foliage_pressed() -> void:
	var scene_root: Node = EditorInterface.get_edited_scene_root()
	if not scene_root:
		status_label.text = "Error: Open the generated scene first."
		status_label.add_theme_color_override("font_color", Color(0.9, 0.3, 0.3))
		return
	var bound := _bind_foliage_recursive(scene_root)
	if bound > 0:
		EditorInterface.mark_scene_as_unsaved()
	status_label.text = "Status: Bound %d foliage mesh(es)." % bound
	status_label.add_theme_color_override("font_color", Color(0.2, 0.8, 0.2) if bound > 0 else Color(0.6, 0.6, 0.6))


func _bind_foliage_recursive(node: Node) -> int:
	var count := 0
	if node is MultiMeshInstance3D and node.has_meta("source_model"):
		var mmi := node as MultiMeshInstance3D
		if mmi.multimesh and mmi.multimesh.mesh == null:
			var model_path := str(mmi.get_meta("source_model"))
			var scene_inst := Common.instantiate_model(model_path)
			if scene_inst:
				var mesh_inst := Common.find_first_mesh(scene_inst)
				if mesh_inst and mesh_inst.mesh:
					mmi.multimesh.mesh = mesh_inst.mesh
					count += 1
				scene_inst.free()
	for child in node.get_children():
		count += _bind_foliage_recursive(child)
	return count


func _make_section(parent: Control, title: String, start_expanded: bool = false) -> VBoxContainer:
	# A fold-out: a clickable header plus an indented content box it shows or
	# hides. Lets optional controls collapse so the dock does not overflow.
	var header := Button.new()
	header.toggle_mode = true
	header.button_pressed = start_expanded
	header.flat = true
	header.focus_mode = Control.FOCUS_NONE
	header.alignment = HORIZONTAL_ALIGNMENT_LEFT
	parent.add_child(header)

	var indent := MarginContainer.new()
	indent.add_theme_constant_override("margin_left", 8)
	parent.add_child(indent)

	var content := VBoxContainer.new()
	content.add_theme_constant_override("separation", 6)
	indent.add_child(content)

	var refresh := func(expanded: bool) -> void:
		header.text = ("  ▼  " if expanded else "  ▶  ") + title
		indent.visible = expanded
	header.toggled.connect(refresh)
	refresh.call(start_expanded)
	return content


func _make_check(parent: Control, text: String, tooltip: String, default_on: bool = true) -> CheckBox:
	var check := CheckBox.new()
	check.text = text
	check.tooltip_text = tooltip
	check.button_pressed = default_on
	parent.add_child(check)
	return check


func _dialog_initial_dir(path_str: String, is_file: bool) -> String:
	var dir_path := path_str.get_base_dir() if is_file else path_str
	if not (dir_path.begins_with("res://") or dir_path.contains("/") or dir_path.contains("\\")):
		dir_path = "res://"
	if dir_path.begins_with("res://"):
		dir_path = ProjectSettings.globalize_path(dir_path)
	return dir_path


func _on_browse_json() -> void:
	var dialog := FileDialog.new()
	dialog.title = "Select Layout JSON File"
	dialog.file_mode = FileDialog.FILE_MODE_OPEN_FILE
	dialog.access = FileDialog.ACCESS_FILESYSTEM
	dialog.add_filter("*.json", "JSON Files")
	dialog.current_dir = _dialog_initial_dir(json_edit.text, true)
	EditorInterface.get_base_control().add_child(dialog)
	
	dialog.file_selected.connect(func(path):
		json_edit.text = ProjectSettings.localize_path(path)
		_save_dock_settings()
		_on_preview_pressed()
	)
	dialog.visibility_changed.connect(func():
		if not dialog.visible:
			dialog.queue_free()
	)
	dialog.popup_centered_ratio(0.6)

func _on_browse_models() -> void:
	var dialog := FileDialog.new()
	dialog.title = "Select glTF Models Folder"
	dialog.file_mode = FileDialog.FILE_MODE_OPEN_DIR
	dialog.access = FileDialog.ACCESS_FILESYSTEM
	dialog.current_dir = _dialog_initial_dir(models_edit.text, false)
	EditorInterface.get_base_control().add_child(dialog)
	
	dialog.dir_selected.connect(func(path):
		var localized = ProjectSettings.localize_path(path)
		if not localized.ends_with("/"):
			localized += "/"
		models_edit.text = localized
	)
	dialog.visibility_changed.connect(func():
		if not dialog.visible:
			dialog.queue_free()
	)
	dialog.popup_centered_ratio(0.6)

func _on_browse_textures() -> void:
	var dialog := FileDialog.new()
	dialog.title = "Select Textures Folder"
	dialog.file_mode = FileDialog.FILE_MODE_OPEN_DIR
	dialog.access = FileDialog.ACCESS_FILESYSTEM
	dialog.current_dir = _dialog_initial_dir(textures_edit.text, false)
	EditorInterface.get_base_control().add_child(dialog)
	
	dialog.dir_selected.connect(func(path):
		var localized = ProjectSettings.localize_path(path)
		if not localized.ends_with("/"):
			localized += "/"
		textures_edit.text = localized
	)
	dialog.visibility_changed.connect(func():
		if not dialog.visible:
			dialog.queue_free()
	)
	dialog.popup_centered_ratio(0.6)

func _selected_texture_limit() -> int:
	if texture_limit_option == null:
		return 0
	return TEXTURE_LIMIT_VALUES[clampi(texture_limit_option.selected, 0, TEXTURE_LIMIT_VALUES.size() - 1)]


func _on_texture_limit_changed(_index: int) -> void:
	## Set the project default as soon as it is chosen, not at import time.
	## Textures are imported by the editor the moment they appear in the
	## project -- typically when the exporter's auto-transfer drops them in,
	## long before anyone presses Import. Waiting until then would be too late
	## to prevent the expensive full-resolution import we are trying to avoid.
	var limit := _selected_texture_limit()
	TextureLimit.set_default_limit(limit)
	if limit > 0:
		status_label.text = "Status: New textures will import at max %dpx." % limit
	else:
		status_label.text = "Status: New textures will import at full resolution."
	status_label.add_theme_color_override("font_color", Color(0.6, 0.6, 0.6))


func _texture_search_folders() -> PackedStringArray:
	## Every folder the material binder resolves textures from, deduplicated.
	##
	## Capping only the Textures Folder left 4K art in the models folder and in the
	## textures/ folder beside the layout JSON -- both of which the binder happily
	## loads, so the import-time out-of-memory this guards against still happened.
	var folders := PackedStringArray()
	var seen := {}
	var candidates := [textures_edit.text, models_edit.text,
		json_edit.text.get_base_dir().path_join("textures")]
	for folder in candidates:
		var clean := str(folder).strip_edges().simplify_path()
		if clean == "" or not DirAccess.dir_exists_absolute(clean):
			continue
		# "res://textures" and "res://textures/" are the same folder; shrinking it
		# twice would resample already-resampled art.
		var key := clean.trim_suffix("/") if clean != "res://" else clean
		if seen.has(key):
			continue
		seen[key] = true
		folders.append(clean)
	return folders


func _on_import_pressed() -> void:
	# 1. Get the current active scene root
	var scene_root: Node = EditorInterface.get_edited_scene_root()
	if not scene_root:
		status_label.text = "Error: Open a 3D scene in the editor first."
		status_label.add_theme_color_override("font_color", Color(0.9, 0.3, 0.3))
		return
		
	status_label.text = "Status: Importing..."
	status_label.add_theme_color_override("font_color", Color(0.9, 0.7, 0.2))
	import_btn.disabled = true
	
	# Deferred frame execution to let status label redraw
	await get_tree().process_frame

	var tex_limit := _selected_texture_limit()
	var tex_folders := _texture_search_folders()
	# These folders also hold the project's own art, which is none of our business.
	var tex_names: Dictionary = TextureLimit.layout_texture_names(json_edit.text)

	# Shrink the exported PNGs first, so the reimport below reads the small ones
	# rather than importing 4K art and then throwing most of it away.
	if tex_limit > 0 and shrink_files_check and shrink_files_check.button_pressed:
		status_label.text = "Status: shrinking texture files to %dpx..." % tex_limit
		await get_tree().process_frame
		var total_shrunk := 0
		var total_seen := 0
		var total_failed := 0
		var saved_bytes := 0
		for folder in tex_folders:
			var shrunk: Dictionary = TextureLimit.shrink_source_files(folder, tex_limit, tex_names)
			total_shrunk += int(shrunk.get("shrunk", 0))
			total_seen += int(shrunk.get("total", 0))
			total_failed += int(shrunk.get("failed", 0))
			saved_bytes += int(shrunk.get("bytes_before", 0)) - int(shrunk.get("bytes_after", 0))
		if total_shrunk > 0:
			print("Unreal Importer: shrank %d/%d texture file(s) to %dpx, freeing %.1f MB on disk"
				% [total_shrunk, total_seen, tex_limit, float(saved_bytes) / 1048576.0])
		if total_failed > 0:
			push_warning("Unreal Importer: %d texture file(s) could not be shrunk; see warnings above"
				% total_failed)
		var shrink_fs := EditorInterface.get_resource_filesystem()
		while shrink_fs and shrink_fs.is_scanning():
			await get_tree().process_frame

	# Sync process/size_limit on textures already in the project (including 0 when
	# switching back to "No limit" -- _rewrite_import_file is a no-op when already equal).
	var total_capped := 0
	var total_textures := 0
	for folder in tex_folders:
		var capped: Dictionary = TextureLimit.apply_to_folder(folder, tex_limit, tex_names)
		total_capped += int(capped.get("changed", 0))
		total_textures += int(capped.get("total", 0))
	if total_capped > 0:
		if tex_limit > 0:
			status_label.text = "Status: capping %d texture(s) to %dpx..." % [total_capped, tex_limit]
		else:
			status_label.text = "Status: uncapping %d texture(s)..." % total_capped
		await get_tree().process_frame
		# Let the queued reimports finish before materials load the textures.
		var fs := EditorInterface.get_resource_filesystem()
		while fs and fs.is_scanning():
			await get_tree().process_frame
		if tex_limit > 0:
			print("Unreal Importer: capped %d/%d texture(s) to %dpx"
				% [total_capped, total_textures, tex_limit])
		else:
			print("Unreal Importer: removed size cap on %d/%d texture(s)"
				% [total_capped, total_textures])

	# Instantiate our importer class. Note that since we invoke do_import() directly
	# and pass the scene root, we bypass the need for EditorScript's get_scene() context.
	var importer = ImporterClass.new()
	importer.USE_GDSCRIPT_TRANSFORM_CONVERSION = convert_transforms_check.button_pressed

	_save_dock_settings()
	var pre_children: Array[Node] = []
	for c in scene_root.get_children():
		pre_children.append(c)

	var terrain_modes := ["auto", "terrain3d", "hterrain", "mesh"]
	var options := {
		"apply_lights": lights_check.button_pressed,
		"apply_environment": environment_check.button_pressed,
		"apply_decals": decals_check.button_pressed,
		"build_terrain": terrain_check.button_pressed,
		"terrain_mode": terrain_modes[clampi(terrain_mode_option.selected, 0, terrain_modes.size() - 1)],
		"apply_foliage": foliage_check.button_pressed,
		"apply_navigation": navigation_check.button_pressed,
		"navigation_bake": navigation_bake_check.button_pressed,
		"apply_metadata": metadata_check.button_pressed,
		"apply_lods": lods_check.button_pressed,
		"apply_skeletal_physics": skel_physics_check.button_pressed,
		"apply_reflection_probes": probes_check.button_pressed,
		"apply_cameras": cameras_check.button_pressed,
		"apply_audio": audio_check.button_pressed,
		"apply_spline_meshes": splines_check.button_pressed,
		"preserve_folders": preserve_folders_check.button_pressed,
		"update_existing": update_existing_check.button_pressed,
		"prune_removed": prune_removed_check.button_pressed,
		"extract_prefabs": extract_prefabs_check.button_pressed,
		"flip_normal_green": flip_normal_green_check.button_pressed,
		"save_external_materials": save_external_mats_check.button_pressed,
		"light_energy_scale": energy_scale_spin.value,
		"decal_min_depth_m": decal_depth_spin.value,
	}

	# Execute
	# awaited: do_import yields a frame when it hands a landscape to Terrain3D
	# (the plugin builds its data object one frame after the node enters the tree).
	var success: bool = await importer.do_import(json_edit.text, models_edit.text, textures_edit.text, scene_root, options)

	if success and editor_undo_redo != null:
		var added_nodes: Array[Node] = []
		for c in scene_root.get_children():
			if not pre_children.has(c):
				added_nodes.append(c)
		if not added_nodes.is_empty():
			editor_undo_redo.create_action("Import Unreal Level Layout")
			for n in added_nodes:
				editor_undo_redo.add_do_method(scene_root, "add_child", n)
				editor_undo_redo.add_do_method(self, "_reown_subtree", n, scene_root)
				editor_undo_redo.add_do_reference(n)
				editor_undo_redo.add_undo_method(scene_root, "remove_child", n)
			editor_undo_redo.commit_action(false)
	
	import_btn.disabled = false
	if success:
		status_label.text = "Status: Import Completed Successfully!"
		status_label.add_theme_color_override("font_color", Color(0.2, 0.8, 0.2))
		EditorInterface.mark_scene_as_unsaved()
	else:
		status_label.text = "Status: Import Failed! Check console log for details."
		status_label.add_theme_color_override("font_color", Color(0.9, 0.3, 0.3))


func _reown_subtree(node: Node, scene_root: Node) -> void:
	"""Restores recursive scene ownership (and editable-instance flags) on Redo,
	since Node.remove_child() clears owner on all descendants during Undo."""
	if node == null or scene_root == null:
		return
	node.owner = scene_root
	# Terrain3D rebuilds its internal children itself; import_terrain.gd disowns them
	# on purpose, because saved copies clash with the rebuilt ones.
	if node.is_class("Terrain3D"):
		return
	if node != scene_root and node.scene_file_path != "":
		scene_root.set_editable_instance(node, true)
		node.set_display_folded(true)
		_reown_instance_additions(node, scene_root)
		return
	for child in node.get_children():
		_reown_subtree(child, scene_root)


func _reown_instance_additions(instance: Node, scene_root: Node) -> void:
	"""Restores ownership of what the importer added inside an instanced model (LODs,
	skeletal physics). The model's own nodes still belong to it and are left alone."""
	for child in instance.get_children():
		if child.owner == null and (child.has_meta("unreal_lod_index")
				or child.has_meta("unreal_skeletal_physics")
				or child.name == &"UnrealPhysicalBoneSimulator"):
			_reown_subtree(child, scene_root)
		else:
			_reown_instance_additions(child, scene_root)
