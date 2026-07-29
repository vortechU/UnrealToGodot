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


def _set_export_option(export_options, name, value):
    """Sets one glTF export option in isolation.

    Each property gets its own try/except on purpose: property names differ
    between engine versions, and a single shared try block means the first
    unknown name silently skips every option after it.
    """
    try:
        export_options.set_editor_property(name, value)
        return True
    except Exception as e:
        unreal.log_warning(f"glTF export option '{name}' not applied: {str(e)}")
        return False


def _enum_value(enum_name, member, fallback):
    """Resolves unreal.<enum_name>.<member>, falling back to a raw int."""
    enum_type = getattr(unreal, enum_name, None)
    if enum_type is not None:
        value = getattr(enum_type, member, None)
        if value is not None:
            return value
    return fallback


def configure_gltf_export_options(export_options, export_animations):
    """Configures UGLTFExportOptions for a geometry-only export.

    Materials are deliberately NOT baked. UE's glTF exporter bakes each material
    into <material>_<mesh>_BaseColor.png (etc.) beside the .gltf and references
    them as relative URIs. Baking is slow, crashes on complex material graphs,
    and produces files this toolchain then relocates -- which would break those
    URIs. Instead the importer rebuilds materials from the layout JSON's material
    parameters plus the separately exported source textures.
    """
    _set_export_option(export_options, "adjust_normalmaps", True)
    _set_export_option(export_options, "export_vertex_colors", True)
    _set_export_option(export_options, "export_animation_sequences", export_animations)
    # Belt and braces: stop the bake itself, and stop it writing image files.
    _set_export_option(
        export_options,
        "bake_material_inputs",
        _enum_value("GLTFMaterialBakeMode", "DISABLED", 0),
    )
    _set_export_option(
        export_options,
        "texture_image_format",
        _enum_value("GLTFTextureImageFormat", "NONE", 0),
    )


def inject_texture_references(gltf_path, params_by_material, textures_rel="../textures"):
    """Points a .gltf's materials at the separately exported source textures.

    With baking disabled the exporter emits materials by name but no images, so a
    .gltf previewed on its own renders untextured. Rather than bake (which would
    duplicate every texture per material x mesh pair, lossily), we add image
    entries pointing at the shared textures/ folder.

    glTF resolves a relative image uri against the .gltf's OWN directory, so the
    uri must spell out the real relative path ("../textures/T_Foo.png"). A bare
    filename only ever resolves as a sibling -- which is exactly how the earlier
    baked-then-relocated PNGs ended up orphaned.

    Only base color and normal are wired. The packed roughness/metallic/AO map
    cannot go in as-is: glTF's metallicRoughness expects G=roughness/B=metallic,
    and Unreal's RMA is R=roughness/G=metallic, so a direct reference would render
    with the channels swapped. The layout importer handles that map properly via
    Godot's per-channel selectors; this is preview dressing only.

    Returns the number of materials it touched.
    """
    try:
        with open(gltf_path, "r", encoding="utf-8") as f:
            doc = json.load(f)
    except Exception as e:
        unreal.log_warning(f"Could not read {os.path.basename(gltf_path)} for texture injection: {str(e)}")
        return 0

    materials = doc.get("materials")
    if not materials:
        return 0

    images = doc.setdefault("images", [])
    textures = doc.setdefault("textures", [])

    # Seed from what is already in the document so re-running over an existing
    # .gltf reuses its entries instead of appending duplicates.
    uri_to_texture = {}
    for idx, tex in enumerate(textures):
        source = tex.get("source")
        if isinstance(source, int) and 0 <= source < len(images):
            existing_uri = images[source].get("uri")
            if existing_uri and existing_uri not in uri_to_texture:
                uri_to_texture[existing_uri] = idx

    def texture_index(tex_name):
        """Adds (or reuses) an image+texture pair for tex_name, returning its index."""
        uri = "%s/%s.png" % (textures_rel, tex_name)
        if uri in uri_to_texture:
            return uri_to_texture[uri]
        images.append({"uri": uri, "mimeType": "image/png", "name": tex_name})
        textures.append({"source": len(images) - 1})
        uri_to_texture[uri] = len(textures) - 1
        return len(textures) - 1

    touched = 0
    for mat in materials:
        params = params_by_material.get(mat.get("name"))
        if not params:
            continue
        changed = False

        albedo = params.get("albedo_texture")
        if albedo:
            pbr = mat.setdefault("pbrMetallicRoughness", {})
            pbr["baseColorTexture"] = {"index": texture_index(albedo)}
            changed = True

        normal = params.get("normal_texture")
        if normal:
            mat["normalTexture"] = {"index": texture_index(normal)}
            changed = True

        if changed:
            touched += 1

    if touched == 0:
        return 0

    try:
        with open(gltf_path, "w", encoding="utf-8") as f:
            json.dump(doc, f, indent=4)
    except Exception as e:
        unreal.log_warning(f"Could not write texture references into {os.path.basename(gltf_path)}: {str(e)}")
        return 0
    return touched


def material_params_for_mesh(mesh):
    """Maps material name -> extracted parameters for every slot on a mesh."""
    try:
        from export_level_to_json import extract_material_parameters
    except Exception:
        return {}
    out = {}
    slots = []
    try:
        if hasattr(unreal, "StaticMesh") and isinstance(mesh, unreal.StaticMesh):
            slots = mesh.get_editor_property("static_materials") or []
        elif hasattr(unreal, "SkeletalMesh") and isinstance(mesh, unreal.SkeletalMesh):
            slots = mesh.get_editor_property("materials") or []
    except Exception:
        return {}
    for slot in slots:
        try:
            mi = slot.material_interface
            if mi is None:
                continue
            params = extract_material_parameters(mi)
            if params:
                out[mi.get_name()] = params
        except Exception:
            continue
    return out


def inject_textures_for_exported_mesh(mesh, export_dir, mesh_name, separate_textures):
    """Injects source-texture references into every .gltf just exported for a mesh."""
    params_by_material = material_params_for_mesh(mesh)
    if not params_by_material:
        return
    # Where textures/ sits relative to the .gltf files themselves.
    textures_rel = ".." + "/textures" if separate_textures else "."
    for filename in os.listdir(export_dir):
        if not filename.lower().endswith(".gltf"):
            continue
        stem = filename[:-5]
        if stem != mesh_name and not stem.startswith(mesh_name + "_LOD"):
            continue
        try:
            n = inject_texture_references(os.path.join(export_dir, filename),
                                          params_by_material, textures_rel)
            if n:
                unreal.log(f"Wired source textures into {filename} ({n} material(s))")
        except Exception as e:
            unreal.log_warning(f"Texture injection failed for {filename}: {str(e)}")


def retarget_gltf_textures(gltf_path, textures_rel="../textures"):
    """Rewrites a .gltf's image uris to point at textures_rel/<filename>.

    A .gltf's uris describe the folder it was exported into, but the copy that
    matters is the one transferred into the Godot project -- and that always
    splits models/ and textures/, whatever layout the export folder used. With
    "separate textures" off, the exporter writes textures beside the models and
    injects "./TX_Foo.png"; the transfer then puts the .gltf in models/ and the
    texture in textures/, so Godot resolves "./TX_Foo.png" against res://models/
    and reports "Can't open file from path".

    Only the copy in the Godot project is retargeted, so the export folder keeps
    whatever layout it was asked for and still previews correctly.

    Returns the number of uris changed.
    """
    try:
        with open(gltf_path, "r", encoding="utf-8") as f:
            doc = json.load(f)
    except Exception as e:
        unreal.log_warning(f"Could not read {os.path.basename(gltf_path)} to retarget textures: {str(e)}")
        return 0

    images = doc.get("images")
    if not images:
        return 0

    prefix = textures_rel.rstrip("/")
    changed = 0
    for image in images:
        uri = image.get("uri")
        # Embedded (data:) images carry their own bytes and have no path to fix.
        if not uri or uri.startswith("data:"):
            continue
        base = os.path.basename(uri.replace("\\", "/"))
        new_uri = base if prefix in ("", ".") else "%s/%s" % (prefix, base)
        if new_uri != uri:
            image["uri"] = new_uri
            changed += 1

    if changed:
        try:
            with open(gltf_path, "w", encoding="utf-8") as f:
                json.dump(doc, f, indent=4)
        except Exception as e:
            unreal.log_warning(f"Could not rewrite {os.path.basename(gltf_path)}: {str(e)}")
            return 0
    return changed


def relocate_baked_textures(export_dir, textures_dir):
    """Moves stray PNGs emitted beside the .gltf files into textures_dir.

    Skips any PNG a .gltf actually references: those are resolved as URIs
    relative to the .gltf, so moving them would leave the model pointing at a
    file that is no longer there. This should be a no-op now that baking is
    disabled, but older engine versions may ignore the bake settings.
    """
    if not os.path.exists(export_dir):
        return
    referenced = set()
    for filename in os.listdir(export_dir):
        if not filename.lower().endswith(".gltf"):
            continue
        try:
            with open(os.path.join(export_dir, filename), "r", encoding="utf-8") as gf:
                gltf_doc = json.load(gf)
            for image in gltf_doc.get("images", []) or []:
                uri = image.get("uri")
                if uri:
                    referenced.add(os.path.basename(uri).lower())
        except Exception as read_err:
            # Unreadable glTF: assume every sibling PNG is referenced rather than
            # risk moving one out from under it.
            unreal.log_warning(
                f"Could not scan {filename} for texture references, "
                f"leaving sibling PNGs in place: {str(read_err)}"
            )
            return

    os.makedirs(textures_dir, exist_ok=True)
    for filename in os.listdir(export_dir):
        if not filename.lower().endswith(".png"):
            continue
        if filename.lower() in referenced:
            unreal.log(f"Keeping glTF-referenced texture beside model: {filename}")
            continue
        try:
            shutil.move(os.path.join(export_dir, filename), os.path.join(textures_dir, filename))
            unreal.log(f"Relocated baked texture: {filename} -> {textures_dir}")
        except Exception as move_err:
            unreal.log_warning(f"Failed to relocate baked texture {filename}: {str(move_err)}")

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
            # Base material: harvest its texture parameters. UE 5.7 made
            # Material.expressions unreadable from Python, so reading it directly
            # here raised and silently collected nothing; the shared helper uses
            # MaterialEditingLibrary instead (falling back to the expression walk
            # on older engines). See ue2g_common.iter_base_material_textures.
            for _name, tex in ue2g_common.iter_base_material_textures(mat):
                collected_textures.add(tex)
                
    _collect_recursive(material)

# ---------------------------------------------------------------------------
# Coordinate conversion math lives in ue2g_common (single source of truth).
# ---------------------------------------------------------------------------
matrix_to_quat = ue2g_common.matrix_to_quat
unreal_to_godot_transform = ue2g_common.unreal_to_godot_transform
local_shape_to_godot_transform = ue2g_common.local_shape_to_godot_transform
# Collision shapes use the glTF axis convention (they hug the glTF mesh), not
# the level-layout one -- see ue2g_common.gltf_local_shape_transform.
gltf_local_shape_transform = ue2g_common.gltf_local_shape_transform


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
            godot_local = gltf_local_shape_transform(center, u_quat)

            # FKBoxElem X/Y/Z are the box's FULL side lengths in cm already.
            collision_data["boxes"].append({
                "size": [
                    box.get_editor_property("x"),
                    box.get_editor_property("y"),
                    box.get_editor_property("z")
                ],
                "godot_local_transform": godot_local
            })

        # 2. Sphere Elements
        for sphere in agg_geom.get_editor_property("sphere_elems"):
            center = sphere.get_editor_property("center")
            godot_local = gltf_local_shape_transform(center, unreal.Quat(0.0, 0.0, 0.0, 1.0))
            
            collision_data["spheres"].append({
                "radius": sphere.get_editor_property("radius"),
                "godot_local_transform": godot_local
            })
            
        # 3. Capsule (Sphyl) Elements
        for capsule in agg_geom.get_editor_property("sphyl_elems"):
            center = capsule.get_editor_property("center")
            rot = capsule.get_editor_property("rotation")
            u_quat = rot.quaternion()
            godot_local = gltf_local_shape_transform(center, u_quat)

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
                godot_local = gltf_local_shape_transform(center, u_quat)

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

def export_textures_for_meshes(meshes, export_dir, separate_textures=True):
    """Writes every texture the given meshes reference, at its source size.

    There is no resolution cap here on purpose. This used to set each texture's
    max_texture_size, which was a no-op: that drives the cooked texture, while
    TextureExporterPNG writes the source art. Unreal's Python API has no way to
    resize source art, and the cooked pixels are unusable for this -- BC5 normal
    maps read back with a constant 0 blue channel. The Godot importer dock caps
    textures instead, and can shrink these files on disk. See
    docs/texture-sizing.md.

    Returns the ue2g_common.export_textures_to_png result dict.
    """
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
        return {"exported": [], "reused": [], "unsupported": []}

    export_dir = os.path.normpath(export_dir)
    if separate_textures:
        parent_dir = os.path.dirname(export_dir)
        textures_dir = os.path.join(parent_dir, "textures")
    else:
        textures_dir = export_dir
    os.makedirs(textures_dir, exist_ok=True)
    
    unreal.log(f"Exporting {len(collected_textures)} referenced textures to: {textures_dir}")
    result = ue2g_common.export_textures_to_png(collected_textures, textures_dir)
    ue2g_common.log_texture_export_result(result, textures_dir)
    return result

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
                            # Textures always land in <project>/textures, so the
                            # copy has to reference them there regardless of how
                            # the export folder itself was arranged.
                            if ext.lower() == ".gltf":
                                retarget_gltf_textures(dest_path, "../textures")
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

def export_selected_static_meshes(export_dir=None, export_animations=False, export_lods=False, separate_textures=True, show_dialogs=True, godot_project_dir=None):
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
    
    configure_gltf_export_options(export_options, export_animations)

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
                inject_textures_for_exported_mesh(mesh, export_dir, mesh_name, separate_textures)

    # Automatically export referenced textures
    if exported_count > 0:
        try:
            export_textures_for_meshes(meshes_to_export, export_dir, separate_textures)
        except Exception as tex_err:
            unreal.log_warning(f"Failed to export textures for meshes: {str(tex_err)}")

        if separate_textures:
            # Post-export safety net: with baking disabled the glTF folder should
            # contain no PNGs at all, but relocate any that appear anyway.
            try:
                clean_export_dir = os.path.normpath(export_dir)
                relocate_baked_textures(
                    clean_export_dir,
                    os.path.join(os.path.dirname(clean_export_dir), "textures"),
                )
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

def export_all_level_meshes(export_dir=None, export_animations=False, export_lods=False, separate_textures=True, show_dialogs=True, godot_project_dir=None):
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
    configure_gltf_export_options(export_options, export_animations)

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
                inject_textures_for_exported_mesh(mesh, export_dir, mesh_name, separate_textures)

    # Automatically export referenced textures
    if exported_count > 0:
        try:
            export_textures_for_meshes(meshes_to_export, export_dir, separate_textures)
        except Exception as tex_err:
            unreal.log_warning(f"Failed to export textures for meshes: {str(tex_err)}")

        if separate_textures:
            # Post-export safety net: with baking disabled the glTF folder should
            # contain no PNGs at all, but relocate any that appear anyway.
            try:
                clean_export_dir = os.path.normpath(export_dir)
                relocate_baked_textures(
                    clean_export_dir,
                    os.path.join(os.path.dirname(clean_export_dir), "textures"),
                )
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
