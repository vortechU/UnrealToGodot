@tool
extends ScrollContainer

# Core UI Fields
var json_edit: LineEdit
var models_edit: LineEdit
var textures_edit: LineEdit
var convert_transforms_check: CheckBox
var import_btn: Button
var status_label: Label

# Refactored importer script class reference
const ImporterClass = preload("res://addons/unreal_importer/import_unreal_layout.gd")

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
	
	# --- Option Checkbox ---
	convert_transforms_check = CheckBox.new()
	convert_transforms_check.text = "GDScript Coordinate Swap"
	convert_transforms_check.tooltip_text = "Checked: Re-calculates swizzle (LH Z-up to RH Y-up) inside Godot.\nUnchecked: Uses pre-calculated values from JSON (Recommended)."
	convert_transforms_check.button_pressed = false
	vbox.add_child(convert_transforms_check)
	
	var sep2 := HSeparator.new()
	vbox.add_child(sep2)
	
	# --- Action Button ---
	import_btn = Button.new()
	import_btn.text = "Import Unreal Level Layout"
	import_btn.custom_minimum_size = Vector2(0, 36)
	import_btn.pressed.connect(_on_import_pressed)
	vbox.add_child(import_btn)
	
	# --- Status Label ---
	status_label = Label.new()
	status_label.text = "Status: Ready"
	status_label.horizontal_alignment = HORIZONTAL_ALIGNMENT_CENTER
	status_label.autowrap_mode = TextServer.AUTOWRAP_WORD_SMART
	status_label.add_theme_color_override("font_color", Color(0.6, 0.6, 0.6))
	vbox.add_child(status_label)

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
	
	# Instantiate our importer class. Note that since we invoke do_import() directly 
	# and pass the scene root, we bypass the need for EditorScript's get_scene() context.
	var importer = ImporterClass.new()
	importer.USE_GDSCRIPT_TRANSFORM_CONVERSION = convert_transforms_check.button_pressed
	
	# Execute
	var success: bool = importer.do_import(json_edit.text, models_edit.text, textures_edit.text, scene_root)
	
	import_btn.disabled = false
	if success:
		status_label.text = "Status: Import Completed Successfully!"
		status_label.add_theme_color_override("font_color", Color(0.2, 0.8, 0.2))
		EditorInterface.mark_scene_as_unsaved()
	else:
		status_label.text = "Status: Import Failed! Check console log for details."
		status_label.add_theme_color_override("font_color", Color(0.9, 0.3, 0.3))
