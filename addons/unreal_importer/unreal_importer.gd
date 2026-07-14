@tool
extends EditorPlugin

const ImporterDockClass = preload("res://addons/unreal_importer/importer_dock.gd")
var dock_instance: Control

func _enter_tree() -> void:
	# Instantiate our pure code-based UI dock
	dock_instance = ImporterDockClass.new()
	# Register the dock in the lower-right quadrant (under the Inspector tab)
	add_control_to_dock(DOCK_SLOT_RIGHT_BL, dock_instance)

func _exit_tree() -> void:
	if dock_instance:
		remove_control_from_docks(dock_instance)
		dock_instance.queue_free()
		dock_instance = null
