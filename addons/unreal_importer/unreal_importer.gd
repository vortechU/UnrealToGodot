@tool
extends EditorPlugin

const ImporterDockClass = preload("res://addons/unreal_importer/importer_dock.gd")
var dock_instance: Control

func _enter_tree() -> void:
	# Instantiate our pure code-based UI dock
	dock_instance = ImporterDockClass.new()
	# add_control_to_dock() titles the tab with the control's node name. Without
	# this the dock inherits Godot's auto-generated name and the tab reads
	# "@ScrollContainer@64", which tells the user nothing about what it is.
	dock_instance.name = "Unreal Importer"
	# Register the dock in the lower-right quadrant (under the Inspector tab)
	add_control_to_dock(DOCK_SLOT_RIGHT_BL, dock_instance)

func _exit_tree() -> void:
	if dock_instance:
		remove_control_from_docks(dock_instance)
		dock_instance.queue_free()
		dock_instance = null
