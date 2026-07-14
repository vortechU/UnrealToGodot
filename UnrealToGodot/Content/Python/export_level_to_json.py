"""
Unreal Engine Python Script
Exports all level actors containing Static Meshes and their transforms
to a JSON file, including coordinate conversions for Godot Engine.

Requirements:
1. Enable 'Python Editor Script Plugin' (built-in plugin).

Usage:
1. Open the level you want to export.
2. Run this script in Unreal Engine.
"""

import os
import json
import unreal

def matrix_to_quat(R):
    """
    Converts a 3x3 rotation matrix to a normalized quaternion (qx, qy, qz, qw).
    Safely handles close-to-zero divisions.
    """
    tr = R[0][0] + R[1][1] + R[2][2]
    if tr > 0.0:
        s = max(0.0001, (tr + 1.0) ** 0.5 * 2.0)
        qw = 0.25 * s
        qx = (R[2][1] - R[1][2]) / s
        qy = (R[0][2] - R[2][0]) / s
        qz = (R[1][0] - R[0][1]) / s
    elif (R[0][0] > R[1][1]) and (R[0][0] > R[2][2]):
        s = max(0.0001, (1.0 + R[0][0] - R[1][1] - R[2][2]) ** 0.5 * 2.0)
        qw = (R[2][1] - R[1][2]) / s
        qx = 0.25 * s
        qy = (R[0][1] + R[1][0]) / s
        qz = (R[0][2] + R[2][0]) / s
    elif R[1][1] > R[2][2]:
        s = max(0.0001, (1.0 + R[1][1] - R[0][0] - R[2][2]) ** 0.5 * 2.0)
        qw = (R[0][2] - R[2][0]) / s
        qx = (R[0][1] + R[1][0]) / s
        qy = 0.25 * s
        qz = (R[1][2] + R[2][1]) / s
    else:
        s = max(0.0001, (1.0 + R[2][2] - R[0][0] - R[1][1]) ** 0.5 * 2.0)
        qw = (R[1][0] - R[0][1]) / s
        qx = (R[0][2] + R[2][0]) / s
        qy = (R[1][2] + R[2][1]) / s
        qz = 0.25 * s
    
    # Normalize to avoid numerical drift
    length = (qx**2 + qy**2 + qz**2 + qw**2) ** 0.5
    if length > 0.0:
        return (qx / length, qy / length, qz / length, qw / length)
    return (0.0, 0.0, 0.0, 1.0)

def unreal_transform_to_dict(transform):
    """Converts an unreal.Transform to a simple python dict (Left-handed, Z-up, cm)."""
    t = transform.translation
    r = transform.rotation
    s = transform.scale3d
    
    # Convert quaternion to euler degrees for helper reference
    rotator = transform.rotation.rotator()
    
    return {
        "translation": [t.x, t.y, t.z],
        "rotation_quat": [r.x, r.y, r.z, r.w],
        "rotation_euler": [rotator.roll, rotator.pitch, rotator.yaw], # Roll (X), Pitch (Y), Yaw (Z)
        "scale": [s.x, s.y, s.z]
    }

def unreal_to_godot_transform(u_transform):
    """
    Converts Unreal Transform to Godot Transform:
    1. Position: cm -> meters; Axis mapping: Godot_X = Unreal_Y, Godot_Y = Unreal_Z, Godot_Z = -Unreal_X
    2. Scale: Axis mapping: Godot_X = Unreal_Y, Godot_Y = Unreal_Z, Godot_Z = Unreal_X
    3. Rotation: Convert Unreal Quat -> 3x3 Matrix -> remap basis -> Matrix -> Godot Quat
    """
    # 1. Translation (cm to meters)
    ux, uy, uz = u_transform.translation.x, u_transform.translation.y, u_transform.translation.z
    godot_translation = [uy * 0.01, uz * 0.01, -ux * 0.01]
    
    # 2. Scale
    usx, usy, usz = u_transform.scale3d.x, u_transform.scale3d.y, u_transform.scale3d.z
    godot_scale = [usy, usz, usx]
    
    # 3. Rotation (Remap 3x3 Basis using C * R_unreal * C^T)
    u_quat = u_transform.rotation
    qx, qy, qz, qw = u_quat.x, u_quat.y, u_quat.z, u_quat.w
    
    # Unreal Quat -> 3x3 Matrix
    r00 = 1.0 - 2.0 * (qy**2 + qz**2)
    r01 = 2.0 * (qx*qy - qw*qz)
    r02 = 2.0 * (qx*qz + qw*qy)
    
    r10 = 2.0 * (qx*qy + qw*qz)
    r11 = 1.0 - 2.0 * (qx**2 + qz**2)
    r12 = 2.0 * (qy*qz - qw*qx)
    
    r20 = 2.0 * (qx*qz - qw*qy)
    r21 = 2.0 * (qy*qz + qw*qx)
    r22 = 1.0 - 2.0 * (qx**2 + qy**2)
    
    # Remap basis for right-handed Y-up Godot
    rg00 = r11
    rg01 = r12
    rg02 = -r10
    
    rg10 = r21
    rg11 = r22
    rg12 = -r20
    
    rg20 = -r01
    rg21 = -r02
    rg22 = r00
    
    R_godot = [
        [rg00, rg01, rg02],
        [rg10, rg11, rg12],
        [rg20, rg21, rg22]
    ]
    
    # Matrix -> Godot Quat
    g_quat = matrix_to_quat(R_godot)
    
    return {
        "translation": godot_translation,
        "rotation_quat": list(g_quat),
        "scale": godot_scale
    }

class _SimpleTransform:
    """Lightweight stand-in for unreal.Transform to feed into unreal_to_godot_transform."""
    def __init__(self, translation, rotation, scale3d):
        self.translation = translation
        self.rotation = rotation
        self.scale3d = scale3d

def local_shape_to_godot_transform(translation_vec, rotation_quat):
    """
    Converts a local collision shape offset translation (unreal.Vector)
    and local rotation (unreal.Quat) into a Godot local transform dict.
    Uses a lightweight wrapper to avoid fragile unreal.Transform constructor calls.
    """
    mock = _SimpleTransform(translation_vec, rotation_quat, unreal.Vector(1.0, 1.0, 1.0))
    return unreal_to_godot_transform(mock)

def extract_mesh_collision(static_mesh):
    """
    Reads the UBodySetup of a Static Mesh to extract all simple collision primitives
    (boxes, spheres, capsules, convex hulls) and converts their transforms.
    """
    try:
        body_setup = static_mesh.get_editor_property("body_setup")
        if not body_setup:
            return None
        agg_geom = body_setup.agg_geom
    except Exception:
        return None
        
    collision_data = {
        "boxes": [],
        "spheres": [],
        "capsules": [],
        "convex_hulls": []
    }
    
    # 1. Box Elements
    for box in agg_geom.box_elems:
        center = box.get_editor_property("center")
        rot = box.get_editor_property("rotation")
        u_quat = rot.quaternion()
        godot_local = local_shape_to_godot_transform(center, u_quat)
        
        collision_data["boxes"].append({
            "size": [
                box.get_editor_property("x") * 2.0, # Store full width in cm
                box.get_editor_property("y") * 2.0, # Store full depth in cm
                box.get_editor_property("z") * 2.0  # Store full height in cm
            ],
            "godot_local_transform": godot_local
        })
        
    # 2. Sphere Elements
    for sphere in agg_geom.sphere_elems:
        center = sphere.get_editor_property("center")
        # Spheres don't have rotation
        godot_local = local_shape_to_godot_transform(center, unreal.Quat(0.0, 0.0, 0.0, 1.0))
        
        collision_data["spheres"].append({
            "radius": sphere.get_editor_property("radius"), # in cm
            "godot_local_transform": godot_local
        })
        
    # 3. Capsule (Sphyl) Elements
    for capsule in agg_geom.sphyl_elems:
        center = capsule.get_editor_property("center")
        rot = capsule.get_editor_property("rotation")
        u_quat = rot.quaternion()
        godot_local = local_shape_to_godot_transform(center, u_quat)
        
        collision_data["capsules"].append({
            "radius": capsule.get_editor_property("radius"), # in cm
            "length": capsule.get_editor_property("length"), # cylinder length in cm
            "godot_local_transform": godot_local
        })
        
    # 4. Convex Elements
    for convex in agg_geom.convex_elems:
        center = convex.get_editor_property("center")
        rot = convex.get_editor_property("rotation")
        u_quat = rot.quaternion()
        godot_local = local_shape_to_godot_transform(center, u_quat)
        
        vertices = []
        try:
            vertex_data = convex.get_editor_property("vertex_data")
            for v in vertex_data:
                vertices.append([v.x, v.y, v.z]) # in cm, relative to shape origin
        except Exception as e:
            unreal.log_warning(f"Failed to read convex hull vertices: {str(e)}")
            
        collision_data["convex_hulls"].append({
            "vertices": vertices,
            "godot_local_transform": godot_local
        })
        
    # Check if there is any valid collision shape
    has_collision = (
        len(collision_data["boxes"]) > 0 or 
        len(collision_data["spheres"]) > 0 or 
        len(collision_data["capsules"]) > 0 or
        len(collision_data["convex_hulls"]) > 0
    )
    return collision_data if has_collision else None

def extract_material_parameters(material):
    """
    Safely queries a Material Interface for PBR parameter values (scalars, vectors, textures).
    Works on MaterialInstance assets by parsing overridden parameters.
    """
    if not material:
        return None
        
    parameters = {
        "albedo_color": [1.0, 1.0, 1.0, 1.0],
        "roughness": 0.5,
        "metallic": 0.0,
        "albedo_texture": None,
        "normal_texture": None,
        "roughness_texture": None,
        "metallic_texture": None,
        "tiling": [1.0, 1.0]
    }
    
    is_instance = isinstance(material, unreal.MaterialInstance)
    if not is_instance and hasattr(unreal, "MaterialInstanceConstant"):
        is_instance = isinstance(material, unreal.MaterialInstanceConstant)
    if not is_instance:
        # Base material: can't easily dynamically extract parameter defaults
        return parameters

    # 1. Parse Scalar parameters
    scalars = material.get_editor_property("scalar_parameter_values")
    for s in scalars:
        name = str(s.parameter_info.name).lower()
        val = s.parameter_value
        
        if "roughness" in name or name == "rough":
            parameters["roughness"] = val
        elif "metallic" in name or name == "metal":
            parameters["metallic"] = val
        elif "tiling" in name or "uvscale" in name or "uv_scale" in name:
            parameters["tiling"] = [val, val]

    # 2. Parse Vector parameters
    vectors = material.get_editor_property("vector_parameter_values")
    for v in vectors:
        name = str(v.parameter_info.name).lower()
        val = v.parameter_value
        
        if "color" in name or "albedo" in name or "diffuse" in name:
            parameters["albedo_color"] = [val.r, val.g, val.b, val.a]

    # 3. Parse Texture parameters
    textures = material.get_editor_property("texture_parameter_values")
    for t in textures:
        name = str(t.parameter_info.name).lower()
        tex = t.parameter_value
        
        if not tex:
            continue
            
        tex_name = tex.get_name()
        
        if "albedo" in name or "basecolor" in name or "diffuse" in name or "color" in name or "maintex" in name:
            parameters["albedo_texture"] = tex_name
        elif "normal" in name or "bump" in name:
            parameters["normal_texture"] = tex_name
        elif "roughness" in name or "rough" in name:
            parameters["roughness_texture"] = tex_name
        elif "metallic" in name or "metal" in name:
            parameters["metallic_texture"] = tex_name
            
    return parameters

def extract_mesh_materials(mesh):
    """
    Extracts all material slot descriptions and parameter details from a UStaticMesh or USkeletalMesh.
    """
    materials_data = []
    
    # Check type
    is_static = isinstance(mesh, unreal.StaticMesh)
    is_skeletal = isinstance(mesh, unreal.SkeletalMesh) if hasattr(unreal, "SkeletalMesh") else False
    
    if is_static:
        static_materials = mesh.get_editor_property("static_materials")
        for i, static_mat in enumerate(static_materials):
            slot_name = str(static_mat.material_slot_name)
            mat_interface = static_mat.material_interface
            
            mat_name = "None"
            mat_path = "None"
            params = None
            
            if mat_interface:
                mat_name = mat_interface.get_name()
                mat_path = mat_interface.get_path_name()
                params = extract_material_parameters(mat_interface)
                
            materials_data.append({
                "slot_index": i,
                "slot_name": slot_name,
                "material_name": mat_name,
                "material_path": mat_path,
                "parameters": params
            })
    elif is_skeletal:
        skeletal_materials = mesh.get_editor_property("materials")
        for i, skel_mat in enumerate(skeletal_materials):
            slot_name = str(skel_mat.material_slot_name)
            mat_interface = skel_mat.material_interface
            
            mat_name = "None"
            mat_path = "None"
            params = None
            
            if mat_interface:
                mat_name = mat_interface.get_name()
                mat_path = mat_interface.get_path_name()
                params = extract_material_parameters(mat_interface)
                
            materials_data.append({
                "slot_index": i,
                "slot_name": slot_name,
                "material_name": mat_name,
                "material_path": mat_path,
                "parameters": params
            })
        
    return materials_data

def extract_component_material_overrides(comp):
    """
    Extracts material overrides from a component.
    """
    overrides_data = []
    try:
        override_materials = comp.get_editor_property("override_materials")
        if not override_materials:
            return overrides_data
    except Exception:
        return overrides_data
    
    for i, mat in enumerate(override_materials):
        if mat:
            try:
                overrides_data.append({
                    "slot_index": i,
                    "material_name": mat.get_name(),
                    "material_path": mat.get_path_name(),
                    "parameters": extract_material_parameters(mat)
                })
            except Exception as e:
                unreal.log_warning(f"Could not read material override at slot {i}: {str(e)}")
            
    return overrides_data

def export_level_to_json(save_path=None, show_dialogs=True):
    # 1. Check requirements
    if not hasattr(unreal, "get_editor_subsystem"):
        if show_dialogs:
            unreal.EditorDialog.show_message(
                "Plugin Missing",
                "The Editor Scripting subsystem is not available.\n\n"
                "Please go to Edit > Plugins, enable 'Python Editor Script Plugin', and restart the editor.",
                unreal.AppMsgType.OK
            )
        else:
            unreal.log_error("Editor Scripting subsystem is not available.")
        return False

    # 2. Setup subsystems and world
    actor_subsystem = unreal.get_editor_subsystem(unreal.EditorActorSubsystem)
    
    try:
        world = unreal.EditorLevelLibrary.get_editor_world()
        world_name = world.get_name()
    except Exception:
        world_name = "UntitledLevel"
    
    # Determine default save path
    project_dir = os.path.realpath(unreal.Paths.project_dir())
    default_save_path = os.path.join(project_dir, "Saved", "Exports", f"{world_name}_layout.json")

    if save_path is None:
        if show_dialogs:
            # Prompt the user if they want to choose a custom save path
            dialog_msg = (
                f"Export level layout data for '{world_name}'.\n\n"
                f"Would you like to select a custom file path to save the JSON?\n"
                f"(Selecting 'No' will export to: {default_save_path})"
            )
            user_choice = unreal.EditorDialog.show_message(
                "JSON Save Path Selection",
                dialog_msg,
                unreal.AppMsgType.YES_NO_CANCEL
            )
            
            if user_choice == unreal.AppReturnType.CANCEL:
                unreal.log("Export cancelled by user.")
                return False
                
            save_path = default_save_path
            
            if user_choice == unreal.AppReturnType.YES:
                custom_path = prompt_for_save_file(default_save_path)
                if custom_path:
                    save_path = custom_path
                else:
                    fallback_choice = unreal.EditorDialog.show_message(
                        "No File Path Chosen",
                        "No path was selected. Would you like to use the default path?\n\n"
                        f"Path: {default_save_path}",
                        unreal.AppMsgType.YES_NO
                    )
                    if fallback_choice != unreal.AppReturnType.YES:
                        unreal.log("Export cancelled (no file path selected).")
                        return False
        else:
            save_path = default_save_path

    # Ensure parent directory exists
    try:
        os.makedirs(os.path.dirname(save_path), exist_ok=True)
    except Exception as e:
        if show_dialogs:
            unreal.EditorDialog.show_message(
                "Directory Creation Error",
                f"Failed to create directory for file:\n{save_path}\n\nError: {str(e)}",
                unreal.AppMsgType.OK
            )
        else:
            unreal.log_error(f"Failed to create directory for file {save_path}: {str(e)}")
        return False

    # 4. Extract transforms and static mesh details
    all_actors = actor_subsystem.get_all_level_actors()
    exported_actors = []
    total_components_count = 0
    mesh_library = {}
    
    with unreal.ScopedSlowTask(len(all_actors), "Scanning level actors for Static Meshes...") as slow_task:
        slow_task.make_dialog(can_cancel=True)
        
        for actor in all_actors:
            if slow_task.should_cancel():
                unreal.log_warning("Export cancelled by user.")
                break
                
            actor_label = actor.get_actor_label()
            slow_task.enter_progress_frame(1, f"Scanning: {actor_label}")
            
            # Find all StaticMeshComponents inside this actor
            static_comps = actor.get_components_by_class(unreal.StaticMeshComponent)
            
            # Find all SkeletalMeshComponents inside this actor
            skeletal_comps = []
            if hasattr(unreal, "SkeletalMeshComponent"):
                skeletal_comps = actor.get_components_by_class(unreal.SkeletalMeshComponent)
                
            valid_components = []
            
            for comp in static_comps:
                mesh = comp.static_mesh if hasattr(comp, "static_mesh") else comp.get_editor_property("static_mesh")
                if mesh:
                    valid_components.append((comp, mesh))
                    
            for comp in skeletal_comps:
                mesh = comp.skeletal_mesh if hasattr(comp, "skeletal_mesh") else comp.get_editor_property("skeletal_mesh")
                if mesh:
                    valid_components.append((comp, mesh))
                    
            if not valid_components:
                continue # Skip actors with no meshes (e.g. lights, cameras, logic)
                
            # Collect actor-level properties
            actor_class = actor.get_class().get_name()
            actor_transform = actor.get_actor_transform()
            
            actor_data = {
                "name": actor_label,
                "class": actor_class,
                "unreal_transform": unreal_transform_to_dict(actor_transform),
                "godot_transform": unreal_to_godot_transform(actor_transform),
                "components": []
            }
            
            for comp, mesh in valid_components:
                comp_name = comp.get_name()
                mesh_name = mesh.get_name()
                mesh_path = mesh.get_path_name()
                
                # Extract and store collision & material data if not already in the library
                if mesh_name not in mesh_library:
                    collision_data = extract_mesh_collision(mesh)
                    materials_data = extract_mesh_materials(mesh)
                    mesh_library[mesh_name] = {
                        "path": mesh_path,
                        "collision": collision_data,
                        "materials": materials_data
                    }
                
                # Fetch local relative transform and absolute world transform of the component
                comp_relative_transform = comp.get_relative_transform()
                comp_world_transform = comp.get_world_transform()
                
                comp_data = {
                    "name": comp_name,
                    "mesh_name": mesh_name,
                    "mesh_path": mesh_path,
                    "unreal_relative_transform": unreal_transform_to_dict(comp_relative_transform),
                    "godot_relative_transform": unreal_to_godot_transform(comp_relative_transform),
                    "unreal_world_transform": unreal_transform_to_dict(comp_world_transform),
                    "godot_world_transform": unreal_to_godot_transform(comp_world_transform),
                    "material_overrides": extract_component_material_overrides(comp)
                }
                
                actor_data["components"].append(comp_data)
                total_components_count += 1
                
            exported_actors.append(actor_data)

    # 5. Build final layout JSON
    layout_data = {
        "level_name": world_name,
        "unreal_project_dir": project_dir,
        "total_actors": len(exported_actors),
        "total_mesh_instances": total_components_count,
        "meshes": mesh_library,
        "actors": exported_actors
    }

    # Write to JSON file
    try:
        with open(save_path, "w", encoding="utf-8") as f:
            json.dump(layout_data, f, indent=4)
            
        unreal.log(f"Level layout exported: {save_path}")
        
        if show_dialogs:
            summary_msg = (
                f"Successfully exported layout for '{world_name}'!\n\n"
                f"Actors Exported: {len(exported_actors)}\n"
                f"Mesh Instances: {total_components_count}\n"
                f"Saved to: {save_path}"
            )
            unreal.EditorDialog.show_message(
                "Export Completed",
                summary_msg,
                unreal.AppMsgType.OK
            )
        return True
    except Exception as e:
        unreal.log_error(f"Failed to write JSON layout file: {str(e)}")
        if show_dialogs:
            unreal.EditorDialog.show_message(
                "File Write Error",
                f"Failed to save JSON file:\n{save_path}\n\nError: {str(e)}",
                unreal.AppMsgType.OK
            )
        return False

def prompt_for_save_file(default_path):
    """
    Safely prompts the user to select a location to save the JSON file using tkinter.
    """
    try:
        import tkinter as tk
        from tkinter import filedialog
        
        root = tk.Tk()
        root.withdraw()
        root.attributes('-topmost', True)
        
        file_path = filedialog.asksaveasfilename(
            initialdir=os.path.dirname(default_path),
            initialfile=os.path.basename(default_path),
            defaultextension=".json",
            filetypes=[("JSON Files", "*.json"), ("All Files", "*.*")],
            title="Save Layout JSON File"
        )
        
        root.destroy()
        if file_path:
            return os.path.normpath(file_path)
    except Exception as e:
        unreal.log_warning(f"Could not open file save dialog via tkinter: {str(e)}")
    return None

if __name__ == "__main__":
    export_level_to_json()
