"""
Unreal Engine Startup Script
Runs automatically when the Unreal to Godot Exporter plugin is loaded by the engine.
"""

import unreal

unreal.log("Initializing Unreal to Godot Exporter plugin...")

try:
    # Importing the GUI automatically runs register_menu_entry() defined at module scope
    import unreal_to_godot_gui
    unreal.log("Unreal to Godot Exporter plugin initialized successfully.")
except Exception as e:
    unreal.log_error(f"Failed to initialize Unreal to Godot Exporter: {str(e)}")
