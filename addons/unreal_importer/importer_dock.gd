@tool
extends ScrollContainer

# Core UI Fields
var json_edit: LineEdit
var models_edit: LineEdit
var textures_edit: LineEdit
var convert_transforms_check: CheckBox
var import_btn: Button
var status_label: Label

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
var energy_scale_spin: SpinBox
var texture_limit_option: OptionButton
var shrink_files_check: CheckBox

# Refactored importer script class reference
const ImporterClass = preload("res://addons/unreal_importer/import_unreal_layout.gd")
const Common = preload("res://addons/unreal_importer/import_common.gd")
const TextureLimit = preload("res://addons/unreal_importer/texture_import_limit.gd")

const TEXTURE_LIMIT_LABELS := ["No limit (as exported)", "512", "1024", "2048", "4096"]
const TEXTURE_LIMIT_VALUES := [0, 512, 1024, 2048, 4096]
# 1024 by default: a full-resolution import of a real level is slow, heavy, and
# can run Godot out of memory. Someone who wants the source art can say so.
const TEXTURE_LIMIT_DEFAULT_INDEX := 2

func _enter_tree() -> void:
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
	terrain_check = _make_check(features,"Terrain (Landscapes)", "Rebuild Unreal Landscapes from exported heightmaps. Uses Terrain3D when installed, otherwise a plugin-free mesh fallback with collision.")

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
	terrain_mode_option.tooltip_text = "Which terrain system to build with. Every mode falls back to the plugin-free mesh terrain if the plugin is unavailable."
	terrain_mode_option.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	terrain_mode_row.add_child(terrain_mode_option)
	features.add_child(terrain_mode_row)

	foliage_check = _make_check(features,"Foliage (MultiMesh)", "Rebuild painted foliage and instanced meshes as MultiMeshInstance3D nodes (thousands of instances stay performant), with matching visibility, shadow casting, cull distances and material overrides.")
	navigation_check = _make_check(features,"Navigation Regions", "Create NavigationRegion3D nodes from Unreal NavMeshBoundsVolumes with matching agent settings.")
	navigation_bake_check = _make_check(features,"    Bake navigation on import", "Bake the navigation meshes immediately after import (synchronous; can take a moment on large levels).", false)
	metadata_check = _make_check(features,"Tags & Metadata", "Copy Unreal actor tags, classes and Blueprint variables onto nodes as metadata (get_meta()).")

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
	shrink_files_check.tooltip_text = "Rewrites exported PNGs larger than the limit at the capped size, which is usually the difference between a multi-gigabyte textures folder and a few hundred megabytes.\n\nDestructive: the full-resolution art only exists back in Unreal, so raising the limit later means exporting again. Only .png files the exporter writes are touched."
	vbox.add_child(shrink_files_check)

	import_btn = Button.new()
	import_btn.text = "Import Unreal Level Layout"
	import_btn.custom_minimum_size = Vector2(0, 36)
	import_btn.pressed.connect(_on_import_pressed)
	vbox.add_child(import_btn)

	# --- Utility: bind meshes into MultiMeshes of directly-generated .tscn scenes ---
	var bind_foliage_btn := Button.new()
	bind_foliage_btn.text = "Bind Foliage Meshes (.tscn scenes)"
	bind_foliage_btn.tooltip_text = "For scenes generated directly from Unreal (.tscn): loads each foliage MultiMesh's source glTF model and binds its mesh. Run once after opening the generated scene."
	bind_foliage_btn.pressed.connect(_on_bind_foliage_pressed)
	vbox.add_child(bind_foliage_btn)
	
	# --- Status Label ---
	status_label = Label.new()
	status_label.text = "Status: Ready"
	status_label.horizontal_alignment = HORIZONTAL_ALIGNMENT_CENTER
	status_label.autowrap_mode = TextServer.AUTOWRAP_WORD_SMART
	status_label.add_theme_color_override("font_color", Color(0.6, 0.6, 0.6))
	vbox.add_child(status_label)

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


func _on_browse_json() -> void:
	var dialog := FileDialog.new()
	dialog.title = "Select Layout JSON File"
	dialog.file_mode = FileDialog.FILE_MODE_OPEN_FILE
	dialog.access = FileDialog.ACCESS_FILESYSTEM
	dialog.add_filter("*.json", "JSON Files")
	var current_path = json_edit.text
	dialog.current_dir = current_path.get_base_dir() if (current_path.begins_with("res://") or current_path.contains("/") or current_path.contains("\\")) else "res://"
	EditorInterface.get_base_control().add_child(dialog)
	
	dialog.file_selected.connect(func(path):
		json_edit.text = ProjectSettings.localize_path(path)
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
	var current_path = models_edit.text
	dialog.current_dir = current_path if (current_path.begins_with("res://") or current_path.contains("/") or current_path.contains("\\")) else "res://"
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
	var current_path = textures_edit.text
	dialog.current_dir = current_path if (current_path.begins_with("res://") or current_path.contains("/") or current_path.contains("\\")) else "res://"
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
			var shrunk: Dictionary = TextureLimit.shrink_source_files(folder, tex_limit)
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

	# Cap textures that are already in the project. Changing the importer
	# default only affects future imports, so anything transferred in before
	# the limit was chosen is still sitting there at full 4K.
	if tex_limit > 0:
		var total_capped := 0
		var total_textures := 0
		for folder in tex_folders:
			var capped: Dictionary = TextureLimit.apply_to_folder(folder, tex_limit)
			total_capped += int(capped.get("changed", 0))
			total_textures += int(capped.get("total", 0))
		if total_capped > 0:
			status_label.text = "Status: capping %d texture(s) to %dpx..." % [total_capped, tex_limit]
			await get_tree().process_frame
			# Let the queued reimports finish before materials load the textures.
			var fs := EditorInterface.get_resource_filesystem()
			while fs and fs.is_scanning():
				await get_tree().process_frame
			print("Unreal Importer: capped %d/%d texture(s) to %dpx"
				% [total_capped, total_textures, tex_limit])

	# Instantiate our importer class. Note that since we invoke do_import() directly
	# and pass the scene root, we bypass the need for EditorScript's get_scene() context.
	var importer = ImporterClass.new()
	importer.USE_GDSCRIPT_TRANSFORM_CONVERSION = convert_transforms_check.button_pressed

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
		"light_energy_scale": energy_scale_spin.value,
	}

	# Execute
	var success: bool = importer.do_import(json_edit.text, models_edit.text, textures_edit.text, scene_root, options)
	
	import_btn.disabled = false
	if success:
		status_label.text = "Status: Import Completed Successfully!"
		status_label.add_theme_color_override("font_color", Color(0.2, 0.8, 0.2))
		EditorInterface.mark_scene_as_unsaved()
	else:
		status_label.text = "Status: Import Failed! Check console log for details."
		status_label.add_theme_color_override("font_color", Color(0.9, 0.3, 0.3))
