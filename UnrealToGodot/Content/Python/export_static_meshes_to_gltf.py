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
import json
import shutil
import unreal

import ue2g_common

def collect_textures_from_material(material, collected_textures):
    if not material:
        return
        
    visited = set()
    
    def _collect_recursive(mat):
        if not mat or mat in visited:
            return
        visited.add(mat)
        
        is_instance = isinstance(mat, unreal.MaterialInstance)
        if not is_instance and hasattr(unreal, "MaterialInstanceConstant"):
            is_instance = isinstance(mat, unreal.MaterialInstanceConstant)
            
        if is_instance:
            try:
                textures = mat.get_editor_property("texture_parameter_values")
                if textures:
                    for t in textures:
                        tex = t.parameter_value
                        if tex and isinstance(tex, unreal.Texture):
                            collected_textures.add(tex)
            except Exception:
                pass
                
            try:
                parent = mat.get_editor_property("parent")
                if parent:
                    _collect_recursive(parent)
            except Exception:
                pass
        else:
            try:
                expressions = mat.get_editor_property("expressions")
                if expressions:
                    for expr in expressions:
                        if expr and hasattr(unreal, "MaterialExpressionTextureSample") and isinstance(expr, unreal.MaterialExpressionTextureSample):
                            tex = expr.get_editor_property("texture")
                            if tex and isinstance(tex, unreal.Texture):
                                collected_textures.add(tex)
            except Exception as e:
                unreal.log_warning(f"Could not read expressions from base material {mat.get_name()}: {str(e)}")
                
    _collect_recursive(material)

# ---------------------------------------------------------------------------
# Coordinate conversion math lives in ue2g_common (single source of truth).
# ---------------------------------------------------------------------------
matrix_to_quat = ue2g_common.matrix_to_quat
unreal_to_godot_transform = ue2g_common.unreal_to_godot_transform
local_shape_to_godot_transform = ue2g_common.local_shape_to_godot_transform


def extract_skeletal_mesh_physics(skeletal_mesh):
    """
    Extracts collision shape data from a SkeletalMesh's associated UPhysicsAsset.
    """
    if not hasattr(unreal, "SkeletalMesh") or not isinstance(skeletal_mesh, unreal.SkeletalMesh):
        return None
        
    try:
        physics_asset = skeletal_mesh.get_editor_property("physics_asset")
        if not physics_asset:
            return None
            
        body_setups = physics_asset.get_editor_property("skeletal_body_setups")
        if not body_setups:
            return None
    except Exception as e:
        unreal.log_warning(f"Failed to read physics asset from skeletal mesh {skeletal_mesh.get_name()}: {str(e)}")
        return None
        
    physics_data = {
        "mesh_name": skeletal_mesh.get_name(),
        "physics_asset_name": physics_asset.get_name(),
        "bodies": []
    }
    
    for body_setup in body_setups:
        bone_name = body_setup.get_editor_property("bone_name")
        agg_geom = body_setup.get_editor_property("agg_geom")
        
        collision_data = {
            "boxes": [],
            "spheres": [],
            "capsules": [],
            "convex_hulls": []
        }
        
        # 1. Box Elements
        for box in agg_geom.get_editor_property("box_elems"):
            center = box.get_editor_property("center")
            rot = box.get_editor_property("rotation")
            u_quat = rot.quaternion()
            godot_local = local_shape_to_godot_transform(center, u_quat)
            
            collision_data["boxes"].append({
                "size": [
                    box.get_editor_property("x") * 2.0,
                    box.get_editor_property("y") * 2.0,
                    box.get_editor_property("z") * 2.0
                ],
                "godot_local_transform": godot_local
            })
            
        # 2. Sphere Elements
        for sphere in agg_geom.get_editor_property("sphere_elems"):
            center = sphere.get_editor_property("center")
            godot_local = local_shape_to_godot_transform(center, unreal.Quat(0.0, 0.0, 0.0, 1.0))
            
            collision_data["spheres"].append({
                "radius": sphere.get_editor_property("radius"),
                "godot_local_transform": godot_local
            })
            
        # 3. Capsule (Sphyl) Elements
        for capsule in agg_geom.get_editor_property("sphyl_elems"):
            center = capsule.get_editor_property("center")
            rot = capsule.get_editor_property("rotation")
            u_quat = rot.quaternion()
            godot_local = local_shape_to_godot_transform(center, u_quat)
            
            collision_data["capsules"].append({
                "radius": capsule.get_editor_property("radius"),
                "length": capsule.get_editor_property("length"),
                "godot_local_transform": godot_local
            })
            
        # 4. Convex Elements
        for convex in agg_geom.get_editor_property("convex_elems"):
            try:
                center = convex.get_editor_property("center")
                rot = convex.get_editor_property("rotation")
                u_quat = rot.quaternion()
                godot_local = local_shape_to_godot_transform(center, u_quat)
                
                vertices = []
                vertex_data = convex.get_editor_property("vertex_data")
                if vertex_data:
                    for v in vertex_data:
                        vertices.append([v.x, v.y, v.z])
                        
                collision_data["convex_hulls"].append({
                    "vertices": vertices,
                    "godot_local_transform": godot_local
                })
            except Exception as e:
                unreal.log_warning(f"Failed to read convex hull element for bone {bone_name}: {str(e)}")
                
        has_collision = (
            len(collision_data["boxes"]) > 0 or 
            len(collision_data["spheres"]) > 0 or 
            len(collision_data["capsules"]) > 0 or
            len(collision_data["convex_hulls"]) > 0
        )
        
        if has_collision:
            physics_data["bodies"].append({
                "bone_name": str(bone_name),
                "shapes": collision_data
            })
            
    return physics_data if physics_data["bodies"] else None

def get_mesh_lod_count(mesh):
    """
    Returns the number of LOD levels for a StaticMesh or SkeletalMesh.
    """
    if isinstance(mesh, unreal.StaticMesh):
        if hasattr(unreal, "EditorStaticMeshLibrary"):
            return unreal.EditorStaticMeshLibrary.get_lod_count(mesh)
        try:
            return mesh.get_num_lods()
        except Exception:
            pass
    elif hasattr(unreal, "SkeletalMesh") and isinstance(mesh, unreal.SkeletalMesh):
        try:
            lod_info = mesh.get_editor_property("lod_info")
            if lod_info:
                return len(lod_info)
        except Exception:
            pass
    return 1

def export_textures_for_meshes(meshes, export_dir, separate_textures=True, max_res_limit=0):
    collected_textures = set()
    for mesh in meshes:
        if isinstance(mesh, unreal.StaticMesh):
            static_materials = mesh.get_editor_property("static_materials")
            for static_mat in static_materials:
                mat_interface = static_mat.material_interface
                collect_textures_from_material(mat_interface, collected_textures)
        elif hasattr(unreal, "SkeletalMesh") and isinstance(mesh, unreal.SkeletalMesh):
            skeletal_materials = mesh.get_editor_property("materials")
            for skel_mat in skeletal_materials:
                mat_interface = skel_mat.material_interface
                collect_textures_from_material(mat_interface, collected_textures)
                
    if not collected_textures:
        return
        
    export_dir = os.path.normpath(export_dir)
    if separate_textures:
        parent_dir = os.path.dirname(export_dir)
        textures_dir = os.path.join(parent_dir, "textures")
    else:
        textures_dir = export_dir
    os.makedirs(textures_dir, exist_ok=True)
    
    tasks = []
    original_sizes = {}  # Store original max sizes to restore them later
    
    try:
        for tex in collected_textures:
            tex_name = tex.get_name()
            filename = os.path.join(textures_dir, f"{tex_name}.png")
            
            # If a resolution limit is set (e.g., 1024 for 1K)
            if max_res_limit > 0:
                try:
                    # Store the original max texture size setting
                    original_sizes[tex] = tex.get_editor_property("max_texture_size")
                    # Apply the limit temporarily
                    tex.set_editor_property("max_texture_size", max_res_limit)
                except Exception as e:
                    unreal.log_warning(f"Could not limit resolution for {tex_name}: {str(e)}")
            
            task = unreal.AssetExportTask()
            task.object = tex
            task.filename = filename
            task.automated = True
            task.prompt = False
            task.replace_identical = True
            
            if hasattr(unreal, "TextureExporterPNG"):
                task.exporter = unreal.TextureExporterPNG()
                
            tasks.append(task)
            
        if tasks:
            unreal.log(f"Exporting {len(tasks)} referenced textures to: {textures_dir}")
            unreal.Exporter.run_asset_export_tasks(tasks)
    finally:
        # Restore the original texture sizes so we don't modify the user's source assets
        if max_res_limit > 0 and original_sizes:
            for tex, orig_size in original_sizes.items():
                try:
                    tex.set_editor_property("max_texture_size", orig_size)
                except Exception as e:
                    unreal.log_warning(f"Failed to restore texture size for {tex.get_name()}: {str(e)}")

def copy_exports_to_godot(export_dir, meshes_exported, separate_textures, godot_project_dir, export_names=None):
    if not godot_project_dir or not os.path.isdir(godot_project_dir):
        return
    if export_names is None:
        export_names = {}
    
    unreal.log(f"Unreal to Godot: Automatically transferring exported files to Godot project: {godot_project_dir}")
    
    godot_models_dir = os.path.join(godot_project_dir, "models")
    godot_textures_dir = os.path.join(godot_project_dir, "textures")
    
    os.makedirs(godot_models_dir, exist_ok=True)
    os.makedirs(godot_textures_dir, exist_ok=True)
    
    # 1. Copy mesh assets (.gltf, .bin, _physics.json)
    for mesh in meshes_exported:
        mesh_name = export_names.get(mesh, mesh.get_name())
        if os.path.exists(export_dir):
            for filename in os.listdir(export_dir):
                base, ext = os.path.splitext(filename)
                
                # Check if it is the base mesh, physics json, or LOD mesh for this exact mesh_name
                is_match = False
                if base == mesh_name or base == f"{mesh_name}_physics":
                    is_match = True
                elif base.startswith(f"{mesh_name}_LOD"):
                    lod_part = base[len(mesh_name) + len("_LOD"):]
                    if lod_part.isdigit():
                        is_match = True
                        
                if is_match and (ext.lower() in [".gltf", ".bin", ".json"]):
                    src_path = os.path.join(export_dir, filename)
                    dest_path = os.path.join(godot_models_dir, filename)
                    try:
                        if os.path.abspath(src_path) != os.path.abspath(dest_path):
                            shutil.copy2(src_path, dest_path)
                            unreal.log(f"Transferred mesh file: {filename} -> {godot_models_dir}")
                    except Exception as copy_err:
                        unreal.log_warning(f"Failed to copy {filename} to Godot: {str(copy_err)}")
                        
    # 2. Copy textures
    export_dir = os.path.normpath(export_dir)
    if separate_textures:
        parent_dir = os.path.dirname(export_dir)
        textures_dir = os.path.join(parent_dir, "textures")
    else:
        textures_dir = export_dir
        
    if os.path.exists(textures_dir):
        collected_textures = set()
        for mesh in meshes_exported:
            if isinstance(mesh, unreal.StaticMesh):
                static_materials = mesh.get_editor_property("static_materials")
                for static_mat in static_materials:
                    collect_textures_from_material(static_mat.material_interface, collected_textures)
            elif hasattr(unreal, "SkeletalMesh") and isinstance(mesh, unreal.SkeletalMesh):
                skeletal_materials = mesh.get_editor_property("materials")
                for skel_mat in skeletal_materials:
                    collect_textures_from_material(skel_mat.material_interface, collected_textures)
                    
        tex_names = {t.get_name() for t in collected_textures if isinstance(t, unreal.Texture)}
        
        for filename in os.listdir(textures_dir):
            base_name, ext = os.path.splitext(filename)
            if ext.lower() in [".png", ".tga", ".jpg", ".jpeg", ".dds"]:
                is_match = base_name in tex_names
                if not is_match:
                    for mesh in meshes_exported:
                        m_name = export_names.get(mesh, mesh.get_name())
                        if base_name == m_name or base_name.startswith(f"{m_name}_") or base_name.startswith(f"{m_name} "):
                            is_match = True
                            break
                
                if is_match:
                    src_path = os.path.join(textures_dir, filename)
                    dest_path = os.path.join(godot_textures_dir, filename)
                    try:
                        if os.path.abspath(src_path) != os.path.abspath(dest_path):
                            shutil.copy2(src_path, dest_path)
                            unreal.log(f"Transferred texture file: {filename} -> {godot_textures_dir}")
                    except Exception as copy_err:
                        unreal.log_warning(f"Failed to copy texture {filename} to Godot: {str(copy_err)}")

def export_selected_static_meshes(export_dir=None, export_animations=False, export_lods=False, separate_textures=True, show_dialogs=True, godot_project_dir=None, max_texture_resolution=0):
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
        return 0, 0

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
        return 0, 0

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
                return 0, 0
                
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
                        return 0, 0
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
        return 0, 0

    # 4. Set up glTF Export Options
    export_options = unreal.GLTFExportOptions()
    
    # Configure export settings (adjust as needed)
    # We set editor properties to ensure they match common conventions
    try:
        export_options.set_editor_property("adjust_normalmaps", True) # Fix normal maps convention
        export_options.set_editor_property("export_vertex_colors", True)
        export_options.set_editor_property("export_materials", False) # Prevent crash on complex materials baking
        export_options.set_editor_property("export_animation_sequences", export_animations) # Avoid heavy/crashing anim exports unless requested
    except Exception as e:
        unreal.log_warning(f"Could not configure some export options: {str(e)}")

    # 5. Export Meshes with a progress bar (ScopedSlowTask)
    exported_count = 0
    failed_exports = []
    exported_meshes = []
    # Deterministic collision-safe export filenames (asset packs often reuse mesh names)
    export_names = ue2g_common.build_export_name_map(meshes_to_export)
    
    selected_actors = set() # Empty set because we are exporting static mesh assets, not world actors
    
    with unreal.ScopedSlowTask(len(meshes_to_export), "Exporting Meshes to glTF...") as slow_task:
        slow_task.make_dialog(can_cancel=True)
        
        for mesh in meshes_to_export:
            if slow_task.should_cancel():
                unreal.log_warning("glTF Export task cancelled by user.")
                break
                
            mesh_name = export_names.get(mesh, mesh.get_name())
            slow_task.enter_progress_frame(1, f"Exporting: {mesh_name}")
            
            # Determine how many LODs to export
            lod_count = 1
            if export_lods:
                lod_count = get_mesh_lod_count(mesh)
                
            mesh_has_exported = False
            for lod_index in range(lod_count):
                if lod_index == 0:
                    export_path = os.path.join(export_dir, f"{mesh_name}.gltf")
                else:
                    export_path = os.path.join(export_dir, f"{mesh_name}_LOD{lod_index}.gltf")
                    
                # Configure the export options for the current LOD level
                try:
                    export_options.set_editor_property("default_level_of_detail", lod_index)
                except Exception as e:
                    unreal.log_warning(f"Could not set default_level_of_detail to {lod_index}: {str(e)}")
                    
                try:
                    # Perform the export with instance fallback safety
                    success = False
                    try:
                        success = unreal.GLTFExporter.export_to_gltf(
                            mesh,
                            export_path,
                            export_options,
                            selected_actors
                        )
                    except (TypeError, AttributeError):
                        exporter = unreal.GLTFExporter()
                        success = exporter.export_to_gltf(
                            mesh,
                            export_path,
                            export_options,
                            selected_actors
                        )
                    if success:
                        exported_count += 1
                        mesh_has_exported = True
                        unreal.log(f"Successfully exported LOD {lod_index}: {mesh_name} -> {export_path}")
                        
                        # Only export Physics Asset for LOD 0
                        if lod_index == 0:
                            # Try exporting Physics Asset companion if it is a skeletal mesh
                            if hasattr(unreal, "SkeletalMesh") and isinstance(mesh, unreal.SkeletalMesh):
                                try:
                                    physics_data = extract_skeletal_mesh_physics(mesh)
                                    if physics_data:
                                        physics_path = os.path.join(export_dir, f"{mesh_name}_physics.json")
                                        with open(physics_path, "w", encoding="utf-8") as pf:
                                            json.dump(physics_data, pf, indent=4)
                                        unreal.log(f"Exported Skeletal Mesh Physics Asset to: {physics_path}")
                                except Exception as pe:
                                    unreal.log_warning(f"Failed to export physics asset for {mesh_name}: {str(pe)}")
                    else:
                        failed_exports.append(f"{mesh_name}_LOD{lod_index}" if lod_index > 0 else mesh_name)
                        unreal.log_error(f"Failed to export: {mesh_name} LOD {lod_index}")
                except Exception as e:
                    failed_exports.append(f"{mesh_name}_LOD{lod_index}" if lod_index > 0 else mesh_name)
                    unreal.log_error(f"Error exporting {mesh_name} LOD {lod_index}: {str(e)}")
            
            if mesh_has_exported:
                exported_meshes.append(mesh)

    # Automatically export referenced textures
    if exported_count > 0:
        try:
            export_textures_for_meshes(meshes_to_export, export_dir, separate_textures, max_texture_resolution)
        except Exception as tex_err:
            unreal.log_warning(f"Failed to export textures for meshes: {str(tex_err)}")

        if separate_textures:
            # Post-export safety relocator: move any PNG textures generated by the glTF exporter
            # inside the GLTF/ folder to the sibling textures/ folder.
            try:
                clean_export_dir = os.path.normpath(export_dir)
                parent_dir = os.path.dirname(clean_export_dir)
                textures_dir = os.path.join(parent_dir, "textures")
                os.makedirs(textures_dir, exist_ok=True)
                
                if os.path.exists(clean_export_dir):
                    for filename in os.listdir(clean_export_dir):
                        if filename.lower().endswith(".png"):
                            src_path = os.path.join(clean_export_dir, filename)
                            dest_path = os.path.join(textures_dir, filename)
                            try:
                                shutil.move(src_path, dest_path)
                                unreal.log(f"Relocated baked texture: {filename} -> {textures_dir}")
                            except Exception as move_err:
                                unreal.log_warning(f"Failed to relocate baked texture {filename}: {str(move_err)}")
            except Exception as scan_err:
                unreal.log_warning(f"Failed to scan and relocate baked textures: {str(scan_err)}")

        if godot_project_dir:
            try:
                copy_exports_to_godot(export_dir, exported_meshes, separate_textures, godot_project_dir, export_names)
            except Exception as copy_err:
                unreal.log_warning(f"Failed to auto-transfer exports to Godot: {str(copy_err)}")

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

def export_all_level_meshes(export_dir=None, export_animations=False, export_lods=False, separate_textures=True, show_dialogs=True, godot_project_dir=None, max_texture_resolution=0):
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
        export_options.set_editor_property("export_materials", False)
        export_options.set_editor_property("export_animation_sequences", export_animations)
    except Exception:
        pass

    # 5. Export Meshes
    exported_count = 0
    failed_exports = []
    exported_meshes = []
    # Deterministic collision-safe export filenames (asset packs often reuse mesh names)
    export_names = ue2g_common.build_export_name_map(meshes_to_export)
    selected_actors = set()
    
    with unreal.ScopedSlowTask(len(meshes_to_export), "Batch Exporting Level Meshes to glTF...") as slow_task:
        slow_task.make_dialog(can_cancel=True)
        
        for mesh in meshes_to_export:
            if slow_task.should_cancel():
                break
                
            mesh_name = export_names.get(mesh, mesh.get_name())
            slow_task.enter_progress_frame(1, f"Exporting: {mesh_name}")
            
            # Determine how many LODs to export
            lod_count = 1
            if export_lods:
                lod_count = get_mesh_lod_count(mesh)
                
            mesh_has_exported = False
            for lod_index in range(lod_count):
                if lod_index == 0:
                    export_path = os.path.join(export_dir, f"{mesh_name}.gltf")
                else:
                    export_path = os.path.join(export_dir, f"{mesh_name}_LOD{lod_index}.gltf")
                    
                # Configure the export options for the current LOD level
                try:
                    export_options.set_editor_property("default_level_of_detail", lod_index)
                except Exception as e:
                    unreal.log_warning(f"Could not set default_level_of_detail to {lod_index}: {str(e)}")
                    
                try:
                    success = False
                    try:
                        success = unreal.GLTFExporter.export_to_gltf(
                            mesh,
                            export_path,
                            export_options,
                            selected_actors
                        )
                    except (TypeError, AttributeError):
                        exporter = unreal.GLTFExporter()
                        success = exporter.export_to_gltf(
                            mesh,
                            export_path,
                            export_options,
                            selected_actors
                        )
                    if success:
                        exported_count += 1
                        mesh_has_exported = True
                        # Only export Physics Asset for LOD 0
                        if lod_index == 0:
                            # Try exporting Physics Asset companion if it is a skeletal mesh
                            if hasattr(unreal, "SkeletalMesh") and isinstance(mesh, unreal.SkeletalMesh):
                                try:
                                    physics_data = extract_skeletal_mesh_physics(mesh)
                                    if physics_data:
                                        physics_path = os.path.join(export_dir, f"{mesh_name}_physics.json")
                                        with open(physics_path, "w", encoding="utf-8") as pf:
                                            json.dump(physics_data, pf, indent=4)
                                        unreal.log(f"Exported Skeletal Mesh Physics Asset to: {physics_path}")
                                except Exception as pe:
                                    unreal.log_warning(f"Failed to export physics asset for {mesh_name}: {str(pe)}")
                    else:
                        failed_exports.append(f"{mesh_name}_LOD{lod_index}" if lod_index > 0 else mesh_name)
                except Exception as e:
                    failed_exports.append(f"{mesh_name}_LOD{lod_index}" if lod_index > 0 else mesh_name)
                    unreal.log_error(f"Error exporting {mesh_name} LOD {lod_index}: {str(e)}")
            
            if mesh_has_exported:
                exported_meshes.append(mesh)

    # Automatically export referenced textures
    if exported_count > 0:
        try:
            export_textures_for_meshes(meshes_to_export, export_dir, separate_textures, max_texture_resolution)
        except Exception as tex_err:
            unreal.log_warning(f"Failed to export textures for meshes: {str(tex_err)}")

        if separate_textures:
            # Post-export safety relocator: move any PNG textures generated by the glTF exporter
            # inside the GLTF/ folder to the sibling textures/ folder.
            try:
                clean_export_dir = os.path.normpath(export_dir)
                parent_dir = os.path.dirname(clean_export_dir)
                textures_dir = os.path.join(parent_dir, "textures")
                os.makedirs(textures_dir, exist_ok=True)
                
                if os.path.exists(clean_export_dir):
                    for filename in os.listdir(clean_export_dir):
                        if filename.lower().endswith(".png"):
                            src_path = os.path.join(clean_export_dir, filename)
                            dest_path = os.path.join(textures_dir, filename)
                            try:
                                shutil.move(src_path, dest_path)
                                unreal.log(f"Relocated baked texture: {filename} -> {textures_dir}")
                            except Exception as move_err:
                                unreal.log_warning(f"Failed to relocate baked texture {filename}: {str(move_err)}")
            except Exception as scan_err:
                unreal.log_warning(f"Failed to scan and relocate baked textures: {str(scan_err)}")

        if godot_project_dir:
            try:
                copy_exports_to_godot(export_dir, exported_meshes, separate_textures, godot_project_dir, export_names)
            except Exception as copy_err:
                unreal.log_warning(f"Failed to auto-transfer exports to Godot: {str(copy_err)}")

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
