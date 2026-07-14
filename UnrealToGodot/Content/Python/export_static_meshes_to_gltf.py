"""
Unreal Engine Python Script
Exports all selected Static Meshes in the Content Browser to glTF format.

Requirements:
1. Enable 'Python Editor Script Plugin' (built-in plugin).
2. Enable 'glTF Exporter' (built-in plugin).

Usage:
1. Select one or more Static Meshes in your Content Browser.
2. Run this script in Unreal Engine (e.g. via the Python Console or via tools).
"""

import os
import unreal

def export_selected_static_meshes(export_dir=None, show_dialogs=True):
    # 1. Check requirements
    if not hasattr(unreal, "GLTFExporter"):
        if show_dialogs:
            unreal.EditorDialog.show_message(
                "Plugin Missing",
                "The 'glTF Exporter' plugin is not enabled.\n\n"
                "Please go to Edit > Plugins, enable 'glTF Exporter', and restart the editor.",
                unreal.AppMsgType.OK
            )
        else:
            unreal.log_error("GLTFExporter plugin is not enabled.")
        return False

    if not hasattr(unreal, "EditorUtilityLibrary"):
        if show_dialogs:
            unreal.EditorDialog.show_message(
                "Plugin Missing",
                "The 'Python Editor Script Plugin' or Editor Scripting Utilities are missing.\n\n"
                "Please go to Edit > Plugins, enable 'Python Editor Script Plugin', and restart the editor.",
                unreal.AppMsgType.OK
            )
        else:
            unreal.log_error("EditorUtilityLibrary is not enabled.")
        return False

    # 2. Get selected assets in Content Browser
    selected_assets = unreal.EditorUtilityLibrary.get_selected_assets()
    
    # Filter for Static or Skeletal Meshes
    meshes_to_export = []
    for asset in selected_assets:
        is_static = isinstance(asset, unreal.StaticMesh)
        is_skeletal = isinstance(asset, unreal.SkeletalMesh) if hasattr(unreal, "SkeletalMesh") else False
        if is_static or is_skeletal:
            meshes_to_export.append(asset)
    
    if not meshes_to_export:
        if show_dialogs:
            unreal.EditorDialog.show_message(
                "No Supported Meshes Selected",
                "Please select one or more Static Meshes or Skeletal Meshes in the Content Browser first.",
                unreal.AppMsgType.OK
            )
        else:
            unreal.log_warning("No supported meshes (Static/Skeletal) selected in Content Browser.")
        return False

    # 3. Determine the export directory
    project_dir = os.path.realpath(unreal.Paths.project_dir())
    default_export_dir = os.path.join(project_dir, "Saved", "Exports", "GLTF")
    
    if export_dir is None:
        if show_dialogs:
            # Ask the user if they want to choose a custom folder or use the default one
            dialog_msg = (
                f"Export {len(meshes_to_export)} Static Mesh(es) to glTF.\n\n"
                f"Would you like to select a custom export folder?\n"
                f"(Selecting 'No' will export to: {default_export_dir})"
            )
            user_choice = unreal.EditorDialog.show_message(
                "Export Folder Selection",
                dialog_msg,
                unreal.AppMsgType.YES_NO_CANCEL
            )
            
            if user_choice == unreal.AppReturnType.CANCEL:
                unreal.log("glTF Export cancelled by user.")
                return False
                
            export_dir = default_export_dir
            
            if user_choice == unreal.AppReturnType.YES:
                custom_dir = prompt_for_folder(default_export_dir)
                if custom_dir:
                    export_dir = custom_dir
                else:
                    fallback_choice = unreal.EditorDialog.show_message(
                        "No Folder Selected",
                        "No folder was selected. Would you like to use the default export folder?\n\n"
                        f"Path: {default_export_dir}",
                        unreal.AppMsgType.YES_NO
                    )
                    if fallback_choice != unreal.AppReturnType.YES:
                        unreal.log("glTF Export cancelled (no folder selected).")
                        return False
        else:
            export_dir = default_export_dir

    # Ensure export directory exists
    try:
        os.makedirs(export_dir, exist_ok=True)
    except Exception as e:
        if show_dialogs:
            unreal.EditorDialog.show_message(
                "Directory Creation Error",
                f"Failed to create directory:\n{export_dir}\n\nError: {str(e)}",
                unreal.AppMsgType.OK
            )
        else:
            unreal.log_error(f"Failed to create export directory {export_dir}: {str(e)}")
        return False

    # 4. Set up glTF Export Options
    export_options = unreal.GLTFExportOptions()
    
    # Configure export settings (adjust as needed)
    # We set editor properties to ensure they match common conventions
    try:
        export_options.set_editor_property("adjust_normalmaps", True) # Fix normal maps convention
        export_options.set_editor_property("export_vertex_colors", True)
    except Exception as e:
        unreal.log_warning(f"Could not configure some export options: {str(e)}")

    # 5. Export Meshes with a progress bar (ScopedSlowTask)
    exported_count = 0
    failed_exports = []
    
    selected_actors = set() # Empty set because we are exporting static mesh assets, not world actors
    
    with unreal.ScopedSlowTask(len(meshes_to_export), "Exporting Meshes to glTF...") as slow_task:
        slow_task.make_dialog(can_cancel=True)
        
        for mesh in meshes_to_export:
            if slow_task.should_cancel():
                unreal.log_warning("glTF Export task cancelled by user.")
                break
                
            mesh_name = mesh.get_name()
            slow_task.enter_progress_frame(1, f"Exporting: {mesh_name}")
            
            export_path = os.path.join(export_dir, f"{mesh_name}.gltf")
            
            try:
                # Perform the export
                success = unreal.GLTFExporter.export_to_gltf(
                    mesh,
                    export_path,
                    export_options,
                    selected_actors
                )
                if success:
                    exported_count += 1
                    unreal.log(f"Successfully exported: {mesh_name} -> {export_path}")
                else:
                    failed_exports.append(mesh_name)
                    unreal.log_error(f"Failed to export: {mesh_name}")
            except Exception as e:
                failed_exports.append(mesh_name)
                unreal.log_error(f"Error exporting {mesh_name}: {str(e)}")

    # 6. Show summary to user
    if show_dialogs:
        summary_message = f"Successfully exported {exported_count} of {len(meshes_to_export)} mesh(es) to:\n{export_dir}"
        if failed_exports:
            summary_message += f"\n\nFailed to export: {', '.join(failed_exports)}"
            summary_message += "\nCheck the Output Log for details."
            
        unreal.EditorDialog.show_message(
            "Export Completed",
            summary_message,
            unreal.AppMsgType.OK
        )
        
    return exported_count, len(failed_exports)

def prompt_for_folder(initial_dir):
    """
    Safely prompts the user to choose a folder using tkinter.
    Falls back gracefully if tkinter is unavailable or fails.
    """
    try:
        import tkinter as tk
        from tkinter import filedialog
        
        root = tk.Tk()
        root.withdraw() # Hide the main root window
        root.attributes('-topmost', True) # Bring the folder picker window to the front
        
        folder_path = filedialog.askdirectory(
            initialdir=initial_dir,
            title="Select Export Directory"
        )
        
        root.destroy()
        if folder_path:
            return os.path.normpath(folder_path)
    except Exception as e:
        unreal.log_warning(f"Could not open directory browser dialog via tkinter: {str(e)}")
    return None

def export_all_level_meshes(export_dir=None, show_dialogs=True):
    # 1. Check requirements
    if not hasattr(unreal, "GLTFExporter"):
        if show_dialogs:
            unreal.EditorDialog.show_message(
                "Plugin Missing",
                "The 'glTF Exporter' plugin is not enabled.\n\n"
                "Please go to Edit > Plugins, enable 'glTF Exporter', and restart the editor.",
                unreal.AppMsgType.OK
            )
        else:
            unreal.log_error("GLTFExporter plugin is not enabled.")
        return 0, 0

    # 2. Scan level for all unique meshes
    try:
        actor_subsystem = unreal.get_editor_subsystem(unreal.EditorActorSubsystem)
        all_actors = actor_subsystem.get_all_level_actors()
    except Exception as e:
        unreal.log_error(f"Failed to scan level actors: {str(e)}")
        return 0, 0
        
    unique_meshes = set()
    for actor in all_actors:
        # Static Meshes
        static_comps = actor.get_components_by_class(unreal.StaticMeshComponent)
        for comp in static_comps:
            mesh = comp.static_mesh if hasattr(comp, "static_mesh") else comp.get_editor_property("static_mesh")
            if mesh:
                unique_meshes.add(mesh)
                
        # Skeletal Meshes
        skeletal_comps = []
        if hasattr(unreal, "SkeletalMeshComponent"):
            skeletal_comps = actor.get_components_by_class(unreal.SkeletalMeshComponent)
        for comp in skeletal_comps:
            mesh = comp.skeletal_mesh if hasattr(comp, "skeletal_mesh") else comp.get_editor_property("skeletal_mesh")
            if mesh:
                unique_meshes.add(mesh)
                
    meshes_to_export = list(unique_meshes)
    if not meshes_to_export:
        if show_dialogs:
            unreal.EditorDialog.show_message(
                "No Meshes Found",
                "No static or skeletal meshes found in the active level.",
                unreal.AppMsgType.OK
            )
        else:
            unreal.log_warning("No static or skeletal meshes found in active level.")
        return 0, 0

    # 3. Determine export directory
    project_dir = os.path.realpath(unreal.Paths.project_dir())
    default_export_dir = os.path.join(project_dir, "Saved", "Exports", "GLTF")
    
    if export_dir is None:
        export_dir = default_export_dir

    try:
        os.makedirs(export_dir, exist_ok=True)
    except Exception as e:
        unreal.log_error(f"Failed to create export directory {export_dir}: {str(e)}")
        return 0, 0

    # 4. Set up glTF Export Options
    export_options = unreal.GLTFExportOptions()
    try:
        export_options.set_editor_property("adjust_normalmaps", True)
        export_options.set_editor_property("export_vertex_colors", True)
    except Exception:
        pass

    # 5. Export Meshes
    exported_count = 0
    failed_exports = []
    selected_actors = set()
    
    with unreal.ScopedSlowTask(len(meshes_to_export), "Batch Exporting Level Meshes to glTF...") as slow_task:
        slow_task.make_dialog(can_cancel=True)
        
        for mesh in meshes_to_export:
            if slow_task.should_cancel():
                break
                
            mesh_name = mesh.get_name()
            slow_task.enter_progress_frame(1, f"Exporting: {mesh_name}")
            
            export_path = os.path.join(export_dir, f"{mesh_name}.gltf")
            
            try:
                success = unreal.GLTFExporter.export_to_gltf(
                    mesh,
                    export_path,
                    export_options,
                    selected_actors
                )
                if success:
                    exported_count += 1
                else:
                    failed_exports.append(mesh_name)
            except Exception as e:
                failed_exports.append(mesh_name)
                unreal.log_error(f"Error exporting {mesh_name}: {str(e)}")

    if show_dialogs:
        summary_message = f"Successfully exported {exported_count} of {len(meshes_to_export)} level mesh(es) to:\n{export_dir}"
        if failed_exports:
            summary_message += f"\n\nFailed to export: {', '.join(failed_exports)}"
        unreal.EditorDialog.show_message(
            "Batch Export Completed",
            summary_message,
            unreal.AppMsgType.OK
        )
        
    return exported_count, len(failed_exports)

if __name__ == "__main__":
    export_selected_static_meshes()
