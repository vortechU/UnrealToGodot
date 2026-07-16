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

import ue2g_common

# Per-feature export toggles (see docs/SCHEMA_V2.md). The GUI passes a matching
# dict; anything omitted falls back to these defaults.
DEFAULT_EXPORT_OPTIONS = {
    "lights": True,
    "decals": True,
    "landscape": True,
    "foliage": True,
    "navigation": True,
    "metadata": True,
    "write_tscn": False,
    "tscn_scene_name": "",
}


def _try_import(module_name):
    """Imports an optional feature module; a missing module just disables its feature."""
    try:
        return __import__(module_name)
    except Exception as e:
        unreal.log_warning(f"Unreal to Godot: optional module '{module_name}' unavailable: {str(e)}")
        return None

# ---------------------------------------------------------------------------
# Coordinate conversion math lives in ue2g_common (single source of truth).
# These module-level aliases preserve existing call sites and any external
# imports while guaranteeing exporter and importer never drift apart.
# ---------------------------------------------------------------------------
matrix_to_quat = ue2g_common.matrix_to_quat
unreal_transform_to_dict = ue2g_common.unreal_transform_to_dict
unreal_to_godot_transform = ue2g_common.unreal_to_godot_transform
local_shape_to_godot_transform = ue2g_common.local_shape_to_godot_transform


def extract_mesh_collision(static_mesh):
    """
    Reads the UBodySetup of a Static Mesh to extract all simple collision primitives
    (boxes, spheres, capsules, convex hulls) and converts their transforms.
    """
    try:
        body_setup = static_mesh.get_editor_property("body_setup")
        if not body_setup:
            return None
        agg_geom = body_setup.get_editor_property("agg_geom")
    except Exception:
        return None
        
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
                box.get_editor_property("x") * 2.0, # Store full width in cm
                box.get_editor_property("y") * 2.0, # Store full depth in cm
                box.get_editor_property("z") * 2.0  # Store full height in cm
            ],
            "godot_local_transform": godot_local
        })
        
    # 2. Sphere Elements
    for sphere in agg_geom.get_editor_property("sphere_elems"):
        center = sphere.get_editor_property("center")
        # Spheres don't have rotation
        godot_local = local_shape_to_godot_transform(center, unreal.Quat(0.0, 0.0, 0.0, 1.0))
        
        collision_data["spheres"].append({
            "radius": sphere.get_editor_property("radius"), # in cm
            "godot_local_transform": godot_local
        })
        
    # 3. Capsule (Sphyl) Elements
    for capsule in agg_geom.get_editor_property("sphyl_elems"):
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
                    vertices.append([v.x, v.y, v.z]) # in cm, relative to shape origin
                
            collision_data["convex_hulls"].append({
                "vertices": vertices,
                "godot_local_transform": godot_local
            })
        except Exception as e:
            unreal.log_warning(f"Failed to read convex hull element: {str(e)}")
        
    # Check if there is any valid collision shape
    has_collision = (
        len(collision_data["boxes"]) > 0 or 
        len(collision_data["spheres"]) > 0 or 
        len(collision_data["capsules"]) > 0 or
        len(collision_data["convex_hulls"]) > 0
    )
    return collision_data if has_collision else None

def extract_material_parameters(material, collected_textures=None):
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
    
    visited = set()
    # Tracks scalar/vector params that have been explicitly set, so the first
    # (child-most) material that provides one wins — even if its value happens
    # to equal a default (e.g. roughness exactly 0.5). Texture params already
    # use a None sentinel below and need no tracking here.
    assigned = set()

    def _extract_recursive(mat):
        if not mat or mat in visited:
            return
        visited.add(mat)
        
        is_instance = isinstance(mat, unreal.MaterialInstance)
        if not is_instance and hasattr(unreal, "MaterialInstanceConstant"):
            is_instance = isinstance(mat, unreal.MaterialInstanceConstant)
            
        if not is_instance:
            # Base material: extract textures from its expressions graph
            try:
                expressions = mat.get_editor_property("expressions")
                if expressions:
                    for expr in expressions:
                        if expr and hasattr(unreal, "MaterialExpressionTextureSample") and isinstance(expr, unreal.MaterialExpressionTextureSample):
                            tex = expr.get_editor_property("texture")
                            if tex and isinstance(tex, unreal.Texture):
                                tex_name = tex.get_name()
                                if collected_textures is not None:
                                    collected_textures.add(tex)
                                
                                # Determine parameter/expression name
                                name = ""
                                if hasattr(unreal, "MaterialExpressionTextureSampleParameter2D") and isinstance(expr, unreal.MaterialExpressionTextureSampleParameter2D):
                                    name = str(expr.get_editor_property("parameter_name")).lower()
                                else:
                                    name = str(expr.get_name()).lower()
                                    
                                if "albedo" in name or "basecolor" in name or "diffuse" in name or "color" in name or "maintex" in name:
                                    if parameters["albedo_texture"] is None:
                                        parameters["albedo_texture"] = tex_name
                                elif "normal" in name or "bump" in name:
                                    if parameters["normal_texture"] is None:
                                        parameters["normal_texture"] = tex_name
                                elif "roughness" in name or "rough" in name:
                                    if parameters["roughness_texture"] is None:
                                        parameters["roughness_texture"] = tex_name
                                elif "metallic" in name or "metal" in name:
                                    if parameters["metallic_texture"] is None:
                                        parameters["metallic_texture"] = tex_name
            except Exception as e:
                unreal.log_warning(f"Could not read expressions from base material {mat.get_name()}: {str(e)}")
            return

        # Material Instance: Parse overridden parameters
        # 1. Parse Scalar parameters
        try:
            scalars = mat.get_editor_property("scalar_parameter_values")
            if scalars:
                for s in scalars:
                    name = str(s.parameter_info.name).lower()
                    val = s.parameter_value
                    
                    if "roughness" in name or name == "rough":
                        # Child instances are visited before parents; first explicit value wins.
                        if "roughness" not in assigned:
                            parameters["roughness"] = val
                            assigned.add("roughness")
                    elif "metallic" in name or name == "metal":
                        if "metallic" not in assigned:
                            parameters["metallic"] = val
                            assigned.add("metallic")
                    elif "tiling" in name or "uvscale" in name or "uv_scale" in name:
                        if "tiling" not in assigned:
                            parameters["tiling"] = [val, val]
                            assigned.add("tiling")
        except Exception:
            pass

        # 2. Parse Vector parameters
        try:
            vectors = mat.get_editor_property("vector_parameter_values")
            if vectors:
                for v in vectors:
                    name = str(v.parameter_info.name).lower()
                    val = v.parameter_value
                    
                    if "color" in name or "albedo" in name or "diffuse" in name:
                        if "albedo_color" not in assigned:
                            parameters["albedo_color"] = [val.r, val.g, val.b, val.a]
                            assigned.add("albedo_color")
        except Exception:
            pass

        # 3. Parse Texture parameters
        try:
            textures = mat.get_editor_property("texture_parameter_values")
            if textures:
                for t in textures:
                    name = str(t.parameter_info.name).lower()
                    tex = t.parameter_value
                    
                    if not tex:
                        continue
                        
                    tex_name = tex.get_name()
                    
                    if collected_textures is not None and isinstance(tex, unreal.Texture):
                        collected_textures.add(tex)
                    
                    if "albedo" in name or "basecolor" in name or "diffuse" in name or "color" in name or "maintex" in name:
                        if parameters["albedo_texture"] is None:
                            parameters["albedo_texture"] = tex_name
                    elif "normal" in name or "bump" in name:
                        if parameters["normal_texture"] is None:
                            parameters["normal_texture"] = tex_name
                    elif "roughness" in name or "rough" in name:
                        if parameters["roughness_texture"] is None:
                            parameters["roughness_texture"] = tex_name
                    elif "metallic" in name or "metal" in name:
                        if parameters["metallic_texture"] is None:
                            parameters["metallic_texture"] = tex_name
        except Exception:
            pass
            
        # Walk up to parent
        try:
            parent = mat.get_editor_property("parent")
            if parent:
                _extract_recursive(parent)
        except Exception:
            pass

    _extract_recursive(material)
    return parameters

def extract_mesh_materials(mesh, collected_textures=None):
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
                params = extract_material_parameters(mat_interface, collected_textures)
                
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
                params = extract_material_parameters(mat_interface, collected_textures)
                
            materials_data.append({
                "slot_index": i,
                "slot_name": slot_name,
                "material_name": mat_name,
                "material_path": mat_path,
                "parameters": params
            })
        
    return materials_data

def extract_component_material_overrides(comp, collected_textures=None):
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
                    "parameters": extract_material_parameters(mat, collected_textures)
                })
            except Exception as e:
                unreal.log_warning(f"Could not read material override at slot {i}: {str(e)}")
            
    return overrides_data

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

def export_level_to_json(save_path=None, show_dialogs=True, godot_project_dir=None, max_texture_resolution=0, options=None):
    opts = dict(DEFAULT_EXPORT_OPTIONS)
    if options:
        opts.update(options)

    # Optional feature modules (each missing module simply disables its feature).
    # export_foliage is loaded even when foliage export is OFF: it owns the list of
    # instanced component classes that must be EXCLUDED from per-component actor
    # export. Without it, every ISM/HISM/painted-foliage component would be emitted
    # as a single mesh placement at its component origin instead of being omitted.
    foliage_mod = _try_import("export_foliage")
    environment_mod = _try_import("export_environment") if (opts.get("lights") or opts.get("decals")) else None
    landscape_mod = _try_import("export_landscape") if opts.get("landscape") else None
    gameplay_mod = _try_import("export_gameplay") if (opts.get("navigation") or opts.get("metadata")) else None

    # Instanced-mesh components (foliage/ISM/HISM) are exported as packed instance
    # arrays, never as single per-component placements.
    instanced_classes = ()
    if foliage_mod:
        try:
            instanced_classes = foliage_mod.get_instanced_component_classes()
        except Exception as e:
            unreal.log_warning(f"Could not query instanced component classes: {str(e)}")

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
    
    # 3. Determine save path and ensure parent directory exists
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
        dir_name = os.path.dirname(save_path)
        if dir_name:
            os.makedirs(dir_name, exist_ok=True)
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
    collected_textures = set()
    
    # Quick pre-scan: gather every referenced mesh (including instanced/foliage meshes)
    # so filenames can be disambiguated consistently with the glTF mesh exporter.
    unique_meshes = set()
    for actor in all_actors:
        try:
            for comp in actor.get_components_by_class(unreal.StaticMeshComponent):
                m = ue2g_common.safe_get_prop(comp, "static_mesh")
                if m:
                    unique_meshes.add(m)
            if hasattr(unreal, "SkeletalMeshComponent"):
                for comp in actor.get_components_by_class(unreal.SkeletalMeshComponent):
                    m = ue2g_common.safe_get_prop(comp, "skeletal_mesh")
                    if m:
                        unique_meshes.add(m)
        except Exception:
            continue
    export_names = ue2g_common.build_export_name_map(unique_meshes)

    def register_mesh(mesh):
        """Registers a mesh in the library under its collision-safe key; returns the key."""
        mesh_key = export_names.get(mesh)
        if mesh_key is None:
            base = ue2g_common.sanitize_name(mesh.get_name())
            mesh_key = base
            if mesh_key in mesh_library and mesh_library[mesh_key].get("path") != mesh.get_path_name():
                mesh_key = "%s_%s" % (base, ue2g_common.short_path_hash(mesh.get_path_name()))
            export_names[mesh] = mesh_key
        if mesh_key not in mesh_library:
            mesh_library[mesh_key] = {
                "path": mesh.get_path_name(),
                "export_name": mesh_key,
                "collision": extract_mesh_collision(mesh),
                "materials": extract_mesh_materials(mesh, collected_textures)
            }
        return mesh_key

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
                if instanced_classes and isinstance(comp, instanced_classes):
                    continue  # exported as packed instance arrays by the foliage exporter
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

            if gameplay_mod and opts.get("metadata"):
                try:
                    meta = gameplay_mod.extract_actor_metadata(actor)
                    actor_data["tags"] = meta.get("tags", [])
                    actor_data["properties"] = meta.get("properties", {})
                except Exception as e:
                    unreal.log_warning(f"Metadata extraction failed for {actor_label}: {str(e)}")
            
            for comp, mesh in valid_components:
                comp_name = comp.get_name()
                mesh_name = mesh.get_name()
                mesh_path = mesh.get_path_name()
                mesh_key = register_mesh(mesh)
                
                # Fetch local relative transform and absolute world transform of the component
                comp_relative_transform = comp.get_relative_transform()
                comp_world_transform = comp.get_world_transform()
                
                comp_data = {
                    "name": comp_name,
                    "mesh_key": mesh_key,
                    "mesh_name": mesh_name,
                    "mesh_path": mesh_path,
                    "unreal_relative_transform": unreal_transform_to_dict(comp_relative_transform),
                    "godot_relative_transform": unreal_to_godot_transform(comp_relative_transform),
                    "unreal_world_transform": unreal_transform_to_dict(comp_world_transform),
                    "godot_world_transform": unreal_to_godot_transform(comp_world_transform),
                    "material_overrides": extract_component_material_overrides(comp, collected_textures)
                }

                if gameplay_mod and opts.get("metadata"):
                    try:
                        comp_tags = gameplay_mod.extract_component_tags(comp)
                        if comp_tags:
                            comp_data["tags"] = comp_tags
                    except Exception:
                        pass

                actor_data["components"].append(comp_data)
                total_components_count += 1
                
            exported_actors.append(actor_data)
 
    # 5. Collect feature data (lights, post-process, decals, terrain, foliage, navigation)
    environment_data = {}
    if environment_mod:
        try:
            environment_data = environment_mod.collect_environment(all_actors, collected_textures) or {}
        except Exception as e:
            unreal.log_warning(f"Environment export failed: {str(e)}")
    if not opts.get("lights"):
        environment_data["lights"] = []
        environment_data["post_process"] = []
        environment_data["height_fog"] = None
        environment_data["sky_light"] = None
        environment_data["has_sky_atmosphere"] = False
    if not opts.get("decals"):
        environment_data["decals"] = []

    landscapes_data = []
    if landscape_mod:
        try:
            landscapes_data = landscape_mod.collect_landscapes(all_actors, os.path.dirname(save_path)) or []
        except Exception as e:
            unreal.log_warning(f"Landscape export failed: {str(e)}")

    foliage_data = []
    if foliage_mod and opts.get("foliage"):
        try:
            foliage_data = foliage_mod.collect_foliage(all_actors, register_mesh) or []
        except Exception as e:
            unreal.log_warning(f"Foliage export failed: {str(e)}")

    navigation_data = None
    if gameplay_mod and opts.get("navigation"):
        try:
            navigation_data = gameplay_mod.collect_navigation(all_actors)
        except Exception as e:
            unreal.log_warning(f"Navigation export failed: {str(e)}")

    # 6. Build final layout JSON (schema v2 — see docs/SCHEMA_V2.md)
    layout_data = {
        "format_version": 2,
        "level_name": world_name,
        "unreal_project_dir": project_dir,
        "total_actors": len(exported_actors),
        "total_mesh_instances": total_components_count,
        "meshes": mesh_library,
        "actors": exported_actors,
        "lights": environment_data.get("lights", []),
        "post_process": environment_data.get("post_process", []),
        "height_fog": environment_data.get("height_fog"),
        "sky_light": environment_data.get("sky_light"),
        "has_sky_atmosphere": environment_data.get("has_sky_atmosphere", False),
        "decals": environment_data.get("decals", []),
        "landscapes": landscapes_data,
        "foliage": foliage_data,
        "navigation": navigation_data
    }
 
    # Write to JSON file
    try:
        with open(save_path, "w", encoding="utf-8") as f:
            json.dump(layout_data, f, indent=4)
            
        unreal.log(f"Level layout exported: {save_path}")
        
        # Export all collected textures automatically
        exported_textures_count = 0
        if collected_textures:
            original_sizes = {}
            try:
                parent_dir = os.path.dirname(save_path)
                textures_dir = os.path.join(parent_dir, "textures")
                os.makedirs(textures_dir, exist_ok=True)
                
                tasks = []
                for tex in collected_textures:
                    if not tex or not isinstance(tex, unreal.Texture):
                        continue
                    tex_name = tex.get_name()
                    filename = os.path.join(textures_dir, f"{tex_name}.png")
                    
                    if max_texture_resolution > 0:
                        try:
                            original_sizes[tex] = tex.get_editor_property("max_texture_size")
                            tex.set_editor_property("max_texture_size", max_texture_resolution)
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
                    unreal.log(f"Exporting {len(tasks)} level textures to: {textures_dir}")
                    unreal.Exporter.run_asset_export_tasks(tasks)
                    # Verify on disk
                    for task in tasks:
                        if os.path.exists(task.filename):
                            exported_textures_count += 1
            except Exception as tex_err:
                unreal.log_warning(f"Failed to export level textures: {str(tex_err)}")
            finally:
                if max_texture_resolution > 0 and original_sizes:
                    for tex, orig_size in original_sizes.items():
                        try:
                            tex.set_editor_property("max_texture_size", orig_size)
                        except Exception as e:
                            unreal.log_warning(f"Failed to restore texture size for {tex.get_name()}: {str(e)}")

        if godot_project_dir and os.path.isdir(godot_project_dir):
            try:
                import shutil
                unreal.log(f"Unreal to Godot: Automatically transferring layout and textures to Godot project: {godot_project_dir}")
                
                # Copy JSON file
                dest_json = os.path.join(godot_project_dir, os.path.basename(save_path))
                if os.path.abspath(save_path) != os.path.abspath(dest_json):
                    shutil.copy2(save_path, dest_json)
                    unreal.log(f"Transferred layout JSON: {os.path.basename(save_path)} -> {godot_project_dir}")
                
                # Copy textures
                if collected_textures:
                    godot_textures_dir = os.path.join(godot_project_dir, "textures")
                    os.makedirs(godot_textures_dir, exist_ok=True)
                    
                    local_textures_dir = os.path.join(os.path.dirname(save_path), "textures")
                    if os.path.exists(local_textures_dir):
                        for tex in collected_textures:
                            if not tex or not isinstance(tex, unreal.Texture):
                                continue
                            tex_name = tex.get_name()
                            src_tex = os.path.join(local_textures_dir, f"{tex_name}.png")
                            if os.path.exists(src_tex):
                                dest_tex = os.path.join(godot_textures_dir, f"{tex_name}.png")
                                if os.path.abspath(src_tex) != os.path.abspath(dest_tex):
                                    shutil.copy2(src_tex, dest_tex)
                                    unreal.log(f"Transferred texture: {tex_name}.png -> {godot_textures_dir}")

                # Copy terrain data (heightmaps / weightmaps written by the landscape exporter)
                local_terrain_dir = os.path.join(os.path.dirname(save_path), "terrain")
                if os.path.isdir(local_terrain_dir):
                    godot_terrain_dir = os.path.join(godot_project_dir, "terrain")
                    os.makedirs(godot_terrain_dir, exist_ok=True)
                    for terrain_file in os.listdir(local_terrain_dir):
                        src_t = os.path.join(local_terrain_dir, terrain_file)
                        dest_t = os.path.join(godot_terrain_dir, terrain_file)
                        if os.path.isfile(src_t) and os.path.abspath(src_t) != os.path.abspath(dest_t):
                            shutil.copy2(src_t, dest_t)
                    unreal.log(f"Transferred terrain data -> {godot_terrain_dir}")
            except Exception as copy_err:
                unreal.log_warning(f"Failed to auto-transfer level layout to Godot: {str(copy_err)}")
        
        # Optionally generate a Godot .tscn scene directly inside the Godot project
        tscn_path = None
        if opts.get("write_tscn") and godot_project_dir and os.path.isdir(godot_project_dir):
            tscn_mod = _try_import("tscn_writer")
            if tscn_mod:
                try:
                    scene_name = ue2g_common.sanitize_name(opts.get("tscn_scene_name") or f"{world_name}_imported")
                    tscn_path = os.path.join(godot_project_dir, f"{scene_name}.tscn")
                    res_paths = {"models": "res://models/", "textures": "res://textures/", "terrain": "res://terrain/"}
                    tscn_options = {
                        "scene_name": scene_name,
                        "godot_project_dir": godot_project_dir,
                        "light_energy_scale": 1.0,
                        "lights": bool(opts.get("lights")),
                        "decals": bool(opts.get("decals")),
                        "foliage": bool(opts.get("foliage")),
                        "navigation": bool(opts.get("navigation")),
                        "metadata": bool(opts.get("metadata")),
                        "landscape": bool(opts.get("landscape")),
                    }
                    if tscn_mod.write_tscn(layout_data, tscn_path, res_paths, tscn_options):
                        unreal.log(f"Generated Godot scene: {tscn_path}")
                    else:
                        tscn_path = None
                        unreal.log_warning("Godot .tscn generation failed; see log for details.")
                except Exception as e:
                    tscn_path = None
                    unreal.log_warning(f"Failed to generate .tscn scene: {str(e)}")
        elif opts.get("write_tscn"):
            unreal.log_warning("Direct .tscn generation requires a valid Godot project path (enable auto-transfer).")

        if show_dialogs:
            feature_lines = []
            if layout_data.get("lights"):
                feature_lines.append(f"Lights: {len(layout_data['lights'])}")
            if layout_data.get("decals"):
                feature_lines.append(f"Decals: {len(layout_data['decals'])}")
            if layout_data.get("landscapes"):
                feature_lines.append(f"Landscapes: {len(layout_data['landscapes'])}")
            if layout_data.get("foliage"):
                total_foliage = sum(int(f.get("instance_count", 0)) for f in layout_data["foliage"])
                feature_lines.append(f"Foliage Instances: {total_foliage}")
            if layout_data.get("navigation"):
                feature_lines.append(f"Nav Volumes: {len(layout_data['navigation'].get('bounds_volumes', []))}")
            if tscn_path:
                feature_lines.append(f"Godot Scene: {tscn_path}")
            extra = ("\n" + "\n".join(feature_lines)) if feature_lines else ""
            summary_msg = (
                f"Successfully exported layout for '{world_name}'!\n\n"
                f"Actors Exported: {len(exported_actors)}\n"
                f"Mesh Instances: {total_components_count}\n"
                f"Textures Exported: {exported_textures_count}"
                f"{extra}\n"
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

if __name__ == "__main__":
    export_level_to_json()
