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
import re
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
    # Landscape CPU-sampling grid caps. They only bite when Unreal's GPU
    # landscape export produces nothing (the usual case on asset-pack
    # landscapes -- see export_landscape's module docstring); both trade export
    # time for terrain detail. Weight sampling is ~40x slower per texel than
    # height tracing, hence the much smaller cap.
    "terrain_height_resolution": 513,
    "terrain_weight_resolution": 129,
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
# Collision shapes ride under the glTF mesh, so they use the glTF axis
# convention, NOT the level-layout one -- see gltf_local_shape_transform.
gltf_local_shape_transform = ue2g_common.gltf_local_shape_transform


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
        godot_local = gltf_local_shape_transform(center, u_quat)

        # FKBoxElem X/Y/Z are already the box's FULL side lengths in cm (Godot's
        # BoxShape3D.size is likewise full extents), so store them as-is.
        collision_data["boxes"].append({
            "size": [
                box.get_editor_property("x"), # Full width in cm  (Unreal X)
                box.get_editor_property("y"), # Full depth in cm  (Unreal Y)
                box.get_editor_property("z")  # Full height in cm (Unreal Z)
            ],
            "godot_local_transform": godot_local
        })

    # 2. Sphere Elements
    for sphere in agg_geom.get_editor_property("sphere_elems"):
        center = sphere.get_editor_property("center")
        # Spheres don't have rotation
        godot_local = gltf_local_shape_transform(center, unreal.Quat(0.0, 0.0, 0.0, 1.0))
        
        collision_data["spheres"].append({
            "radius": sphere.get_editor_property("radius"), # in cm
            "godot_local_transform": godot_local
        })
        
    # 3. Capsule (Sphyl) Elements
    for capsule in agg_geom.get_editor_property("sphyl_elems"):
        center = capsule.get_editor_property("center")
        rot = capsule.get_editor_property("rotation")
        u_quat = rot.quaternion()
        godot_local = gltf_local_shape_transform(center, u_quat)

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
            godot_local = gltf_local_shape_transform(center, u_quat)

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

# Packed PBR maps stuff roughness/metallic/AO into one texture's RGB channels.
# Which channel holds what is encoded in the parameter name by convention, so the
# name IS the layout -- guessing a single layout for all of them would silently
# swap metallic and AO. Channel indices match Godot's BaseMaterial3D.TextureChannel
# (0=RED, 1=GREEN, 2=BLUE, 3=ALPHA).
_PACKED_LAYOUTS = {
    "rma":  {"roughness": 0, "metallic": 1, "ao": 2},
    "rmao": {"roughness": 0, "metallic": 1, "ao": 2},
    "orm":  {"ao": 0, "roughness": 1, "metallic": 2},   # the glTF convention
    "arm":  {"ao": 0, "roughness": 1, "metallic": 2},
    "mra":  {"metallic": 0, "roughness": 1, "ao": 2},
    "mrao": {"metallic": 0, "roughness": 1, "ao": 2},
    # "O" for occlusion rather than "A" for ambient occlusion -- same packing as
    # rma/mra, different spelling. TreatmentStation ships its packed maps as _RMO.
    "rmo":  {"roughness": 0, "metallic": 1, "ao": 2},
    "mro":  {"metallic": 0, "roughness": 1, "ao": 2},
}


def classify_packed_texture(param_name):
    """Returns the channel layout for a packed PBR map parameter, else None.

    Matches whole tokens only: a material with a parameter literally named
    "Normal" must not be read as an ORM map because "orm" appears inside it.
    """
    if not param_name:
        return None
    tokens = re.split(r"[^a-z0-9]+", str(param_name).lower())
    for token in tokens:
        layout = _PACKED_LAYOUTS.get(token)
        if layout:
            return dict(layout)
    return None


# Single-role texture keywords, in priority order. Albedo before normal before
# metallic matters: "T_Walls_Metal_Diff" must classify as albedo, not metallic.
# "diff" rather than "diffuse" is deliberate -- marketplace packs name the
# parameter literally "Diff" (the ContainersHouse pack does), and "diffuse"
# contains "diff" anyway. "colour" covers UK-spelled packs that "color" misses.
_ROLE_SUBSTRINGS = (
    ("albedo_texture", ("albedo", "basecolor", "diff", "colour", "color", "maintex")),
    ("normal_texture", ("normal", "bump")),
    ("roughness_texture", ("rough",)),
    ("metallic_texture", ("metal",)),
)

# Trailing-token conventions ("T_Crate_D", "T_Crate_N_01"). Only the LAST token
# may match these -- "d" or "n" anywhere else would match nearly every name.
_ROLE_LAST_TOKENS = (
    # No bare "c": packs name generic slots "Tex C", which is not a color map.
    ("albedo_texture", ("d", "alb", "bc", "col")),
    ("normal_texture", ("n", "nrm", "norm", "nor")),
    ("roughness_texture", ("r",)),
    ("metallic_texture", ("m",)),
)

# Trailing tokens that only mean something on a TEXTURE ASSET's name, never on a
# parameter name. "_B" for base colour is the dominant suffix in some packs
# (TreatmentStation ships 41 of them) and must classify, but a material PARAMETER
# called "Tex B" is a generic slot letter and means nothing -- the same trap the
# bare-"c" note above records. Splitting the two candidate kinds lets the texture
# name carry the convention without the parameter name inheriting the false
# positive. ("bump" is caught by the substring pass before any of this runs.)
_ROLE_LAST_TOKENS_TEXTURE_ONLY = (
    ("albedo_texture", ("b",)),
)


def classify_texture_role(name, is_texture_name=False):
    """Maps a parameter or texture name to its parameters[] slot key, else None.

    is_texture_name says the string is a texture ASSET name rather than a material
    parameter name, which unlocks the suffix conventions that are only meaningful
    there (see _ROLE_LAST_TOKENS_TEXTURE_ONLY).
    """
    if not name:
        return None
    lowered = str(name).lower()
    for key, needles in _ROLE_SUBSTRINGS:
        for needle in needles:
            if needle in lowered:
                return key
    # Trailing-token pass; a numeric suffix ("T_Crate_D_01") is skipped first.
    tokens = [t for t in re.split(r"[^a-z0-9]+", lowered) if t]
    while tokens and tokens[-1].isdigit():
        tokens.pop()
    if tokens:
        tables = _ROLE_LAST_TOKENS
        if is_texture_name:
            tables = tables + _ROLE_LAST_TOKENS_TEXTURE_ONLY
        for key, suffixes in tables:
            if tokens[-1] in suffixes:
                return key
    return None


def resolve_texture_role(param_name, tex_name):
    """Classifies one sampled texture as a packed map or a single PBR role.

    The parameter name wins whenever it says anything at all; the texture
    asset's own name is the fallback -- a pack whose parameters are named
    "Tex A"/"Tex B" still ships textures named T_Foo_Diff/T_Foo_Normal/T_Foo_ORM,
    and dropping the whole set over an unhelpful parameter name renders every
    mesh white.

    Returns (slot_key or "packed" or None, packed_channels or None).
    """
    for candidate, is_texture_name in ((param_name, False), (tex_name, True)):
        if not candidate:
            continue
        packed = classify_packed_texture(candidate)
        if packed:
            return "packed", packed
        role = classify_texture_role(candidate, is_texture_name=is_texture_name)
        if role:
            return role, None
    return None, None


# UV tiling is spelled a dozen ways across packs, and arrives as either a scalar
# (uniform) or a Vector2/LinearColor (per-axis). Reading only the scalar form left
# every pack using the very common Vector2 "Tiling" parameter at [1, 1].
_TILING_TOKENS = ("tiling", "uvscale", "uv_scale", "uvtiling", "texturescale")

# Vector parameters whose name contains "color" but which are NOT the base colour
# tint. Without this, a material instance that only overrides "EmissiveColor"
# (or SpecularColor, or "SSS Color") has that colour applied as the albedo tint of
# every mesh using it -- the mesh comes into Godot stained with its glow colour.
_NON_ALBEDO_VECTOR_TOKENS = ("emissive", "emission", "specular", "subsurface",
                             "sss", "fresnel", "refraction", "rim")


def classify_scalar_parameter(name):
    """Maps a scalar parameter name to its parameters[] key, else None."""
    lowered = str(name or "").lower()
    if "roughness" in lowered or lowered == "rough":
        return "roughness"
    if "metallic" in lowered or lowered == "metal":
        return "metallic"
    for token in _TILING_TOKENS:
        if token in lowered:
            return "tiling"
    return None


def classify_vector_parameter(name):
    """Maps a vector parameter name to its parameters[] key, else None.

    Tiling is checked before colour: "UV Tiling Color" style names exist, and a
    tiling parameter read as a tint is the more damaging misread of the two.
    """
    lowered = str(name or "").lower()
    for token in _TILING_TOKENS:
        if token in lowered:
            return "tiling"
    for token in _NON_ALBEDO_VECTOR_TOKENS:
        if token in lowered:
            return None
    if ("color" in lowered or "colour" in lowered
            or "albedo" in lowered or "diffuse" in lowered):
        return "albedo_color"
    return None


def _path_of(asset):
    """Asset path name, or "" when unreadable. Used as the texture identity key."""
    try:
        return asset.get_path_name()
    except Exception:
        return ""


def build_texture_export_names(textures):
    """Returns ({texture: export_name}, {asset_path: export_name}) for a texture batch."""
    by_asset = ue2g_common.build_export_name_map(textures, kind="texture")
    by_path = {}
    for tex, name in by_asset.items():
        path = _path_of(tex)
        if path:
            by_path[path] = name
    return by_asset, by_path


def apply_texture_export_names(slots, name_by_path):
    """Rewrites a dict's texture-name values to the filenames actually exported.

    `slots` is either a parameters dict from extract_material_parameters or a
    decal "textures" dict; both carry a "texture_paths" sub-dict keyed by the same
    slot names. The recorded asset path is the only thing that survives a name
    collision, so this is what keeps two same-named textures bound to their own art.
    """
    if not isinstance(slots, dict):
        return
    paths = slots.get("texture_paths")
    if not isinstance(paths, dict):
        return
    for slot, path in paths.items():
        final = name_by_path.get(path)
        if final and slots.get(slot):
            slots[slot] = final


def finalize_layout_texture_names(layout_data, name_by_path):
    """Applies apply_texture_export_names to every texture reference in the layout."""
    for mesh_entry in (layout_data.get("meshes") or {}).values():
        for mat in (mesh_entry.get("materials") or []):
            apply_texture_export_names(mat.get("parameters"), name_by_path)
    for actor in (layout_data.get("actors") or []):
        for comp in (actor.get("components") or []):
            for override in (comp.get("material_overrides") or []):
                apply_texture_export_names(override.get("parameters"), name_by_path)
    for decal in (layout_data.get("decals") or []):
        apply_texture_export_names(decal.get("textures"), name_by_path)


def extract_material_parameters(material, collected_textures=None):
    """
    Safely queries a Material Interface for PBR parameter values (scalars, vectors, textures).
    Works on MaterialInstance assets by parsing overridden parameters, and on base
    Materials by reading their parameter defaults through MaterialEditingLibrary.

    Texture slots hold the texture's ASSET name at this point. The final export
    filename is only known once the whole level has been scanned (colliding names
    get a path-hash suffix), so "texture_paths" records which asset each slot came
    from and apply_texture_export_names() rewrites the slots afterwards.
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
        "packed_texture": None,
        "packed_channels": None,
        "tiling": [1.0, 1.0],
        "texture_paths": {}
    }

    visited = set()
    # Tracks scalar/vector params that have been explicitly set, so the first
    # (child-most) material that provides one wins — even if its value happens
    # to equal a default (e.g. roughness exactly 0.5). Texture params already
    # use a None sentinel below and need no tracking here.
    assigned = set()

    def _assign_texture(param_name, tex):
        """Classifies one sampled texture and records its slot plus its asset path."""
        tex_name = tex.get_name()
        role, packed = resolve_texture_role(str(param_name).lower(), tex_name)
        if role == "packed":
            if parameters["packed_texture"] is None:
                parameters["packed_texture"] = tex_name
                parameters["packed_channels"] = packed
                parameters["texture_paths"]["packed_texture"] = _path_of(tex)
        elif role is not None and parameters[role] is None:
            parameters[role] = tex_name
            parameters["texture_paths"][role] = _path_of(tex)

    def _assign_scalar(key, value):
        if key is None or key in assigned:
            return
        if key == "tiling":
            parameters["tiling"] = [value, value]
        else:
            parameters[key] = value
        assigned.add(key)

    def _extract_recursive(mat, is_parent=False):
        if not mat or mat in visited:
            return
        visited.add(mat)

        is_instance = isinstance(mat, unreal.MaterialInstance)
        if not is_instance and hasattr(unreal, "MaterialInstanceConstant"):
            is_instance = isinstance(mat, unreal.MaterialInstanceConstant)

        if not is_instance:
            # Base material: extract textures from its texture parameters. UE 5.7
            # made Material.expressions unreadable from Python, so the old
            # expression-graph read raised and silently harvested nothing here;
            # the shared helper uses MaterialEditingLibrary instead (falling back
            # to the expression walk on older engines). Parameter names classify
            # by role exactly as the MaterialInstance branch below does.
            for pname, tex in ue2g_common.iter_base_material_textures(
                    mat, include_dependencies=not is_parent):
                if collected_textures is not None:
                    collected_textures.add(tex)
                _assign_texture(pname, tex)

            # A base material's scalars/vectors live in its parameter DEFAULTS,
            # not in the *_parameter_values arrays a MaterialInstance carries.
            # Reading only those arrays meant a mesh slot pointing straight at a
            # base material imported at roughness 0.5 / metallic 0 / white,
            # whatever the material said.
            for pname, value in ue2g_common.iter_base_material_scalars(mat):
                try:
                    _assign_scalar(classify_scalar_parameter(pname), float(value))
                except Exception:
                    continue
            for pname, value in ue2g_common.iter_base_material_vectors(mat):
                key = classify_vector_parameter(pname)
                if key is None or key in assigned:
                    continue
                try:
                    if key == "tiling":
                        parameters["tiling"] = [float(value.r), float(value.g)]
                    else:
                        parameters["albedo_color"] = [float(value.r), float(value.g),
                                                      float(value.b), float(value.a)]
                except Exception:
                    continue
                assigned.add(key)
            return

        # Material Instance: Parse overridden parameters
        # 1. Parse Scalar parameters
        try:
            scalars = mat.get_editor_property("scalar_parameter_values")
            if scalars:
                for s in scalars:
                    # Child instances are visited before parents; first explicit value wins.
                    _assign_scalar(classify_scalar_parameter(s.parameter_info.name),
                                   s.parameter_value)
        except Exception:
            pass

        # 2. Parse Vector parameters
        try:
            vectors = mat.get_editor_property("vector_parameter_values")
            if vectors:
                for v in vectors:
                    key = classify_vector_parameter(v.parameter_info.name)
                    if key is None or key in assigned:
                        continue
                    val = v.parameter_value
                    # A Vector2 material parameter arrives as a LinearColor with
                    # the pair in .r/.g.
                    if key == "tiling":
                        parameters["tiling"] = [val.r, val.g]
                    else:
                        parameters["albedo_color"] = [val.r, val.g, val.b, val.a]
                    assigned.add(key)
        except Exception:
            pass

        # 3. Parse Texture parameters
        try:
            textures = mat.get_editor_property("texture_parameter_values")
            if textures:
                for t in textures:
                    tex = t.parameter_value
                    if not tex:
                        continue

                    if collected_textures is not None and isinstance(tex, unreal.Texture):
                        collected_textures.add(tex)

                    # Packed maps take precedence inside the resolver: a
                    # parameter named "RMA" matches no role substring, so
                    # without that the roughness/metallic/AO set is dropped.
                    _assign_texture(t.parameter_info.name, tex)
        except Exception:
            pass

        # Walk up to parent
        try:
            parent = mat.get_editor_property("parent")
            if parent:
                _extract_recursive(parent, is_parent=True)
        except Exception:
            pass

    _extract_recursive(material)

    # Godot computes roughness/metallic as scalar * texture[channel]. Unreal drives
    # them straight from the packed map unless the material exposes an explicit
    # multiplier, so a scalar left at its default would zero the texture out --
    # metallic defaults to 0.0, which would cancel the map entirely. Promote the
    # unset scalars to 1.0 so the packed map passes through unchanged.
    if parameters["packed_texture"]:
        channels = parameters["packed_channels"] or {}
        if "roughness" in channels and "roughness" not in assigned:
            parameters["roughness"] = 1.0
        if "metallic" in channels and "metallic" not in assigned:
            parameters["metallic"] = 1.0

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

# Expanding an instanced component into individual placements is exact but
# unbatched, so a dense painted field turns into tens of thousands of separate
# mesh placements. Past this many instances on ONE component, say so plainly --
# re-enabling foliage export rebuilds it as a single MultiMesh instead.
_INSTANCE_EXPANSION_WARN_THRESHOLD = 1000


def _expand_instanced_component(foliage_mod, comp, comp_name, mesh_key, mesh_name,
                                mesh_path, material_overrides, actor_label):
    """
    One schema component entry per instance of an instanced (ISM/HISM/foliage)
    component, for the path where foliage export is switched off.

    Both importers place a component from its WORLD transform, so that is the
    one that has to be right. `godot_relative_transform` carries the instance's
    component-local transform, which is the actor-relative transform whenever
    the instanced component is the actor root -- the overwhelmingly common case,
    and only ever used for diagnostics.
    """
    if foliage_mod is None or not hasattr(foliage_mod, "iter_instance_transforms"):
        return []

    placements = []
    try:
        for index, world_transform, local_transform in foliage_mod.iter_instance_transforms(comp):
            if local_transform is None:
                local_transform = world_transform
            placements.append({
                "name": f"{comp_name}_Inst{index}",
                "mesh_key": mesh_key,
                "mesh_name": mesh_name,
                "mesh_path": mesh_path,
                "unreal_relative_transform": unreal_transform_to_dict(local_transform),
                "godot_relative_transform": unreal_to_godot_transform(local_transform),
                "unreal_world_transform": unreal_transform_to_dict(world_transform),
                "godot_world_transform": unreal_to_godot_transform(world_transform),
                "material_overrides": material_overrides,
            })
    except Exception as e:
        unreal.log_warning(
            f"Could not expand instanced component '{comp_name}' on '{actor_label}': {str(e)}")

    if len(placements) >= _INSTANCE_EXPANSION_WARN_THRESHOLD:
        unreal.log_warning(
            f"'{actor_label}.{comp_name}' expanded to {len(placements)} individual mesh "
            f"placements because Foliage & Instances export is off. Enable it to export "
            f"this as one batched MultiMesh instead.")
    return placements


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

def export_level_to_json(save_path=None, show_dialogs=True, godot_project_dir=None,
                         options=None, skip_existing_textures=False):
    """Exports the level layout, and the textures its materials reference.

    skip_existing_textures leaves any texture already written to the textures
    folder alone. Set it when the mesh export has just run and written the same
    4K PNGs: re-encoding gigabytes of identical images is the slowest thing this
    toolchain does. Textures the mesh export cannot see -- decals, landscape
    layers -- are still exported, because only the ones actually present are
    skipped, not all of them.
    """
    opts = dict(DEFAULT_EXPORT_OPTIONS)
    if options:
        opts.update(options)

    # Optional feature modules (each missing module simply disables its feature).
    # export_foliage is loaded even when foliage export is OFF: it owns both the
    # list of instanced component classes and the per-instance transform reader,
    # and the fallback path below needs the second even when the first is unused.
    foliage_mod = _try_import("export_foliage")
    environment_mod = _try_import("export_environment") if (opts.get("lights") or opts.get("decals")) else None
    landscape_mod = _try_import("export_landscape") if opts.get("landscape") else None
    gameplay_mod = _try_import("export_gameplay") if (opts.get("navigation") or opts.get("metadata")) else None

    # Instanced-mesh components (foliage/ISM/HISM) are normally exported as packed
    # instance arrays by the foliage exporter, so they are excluded from the
    # per-component actor export to avoid emitting them twice.
    #
    # That exclusion is conditional on the foliage exporter actually running. It
    # used to be unconditional, which meant unticking "Foliage & Instances"
    # deleted the content outright: nothing collected those components and
    # nothing placed them either. With foliage off they now fall through to the
    # per-component path, where each INSTANCE becomes its own placement -- one
    # placement per component would collapse a whole painted field onto a single
    # mesh at the component origin, which is worse than useless.
    instanced_classes = ()
    if foliage_mod:
        try:
            instanced_classes = foliage_mod.get_instanced_component_classes()
        except Exception as e:
            unreal.log_warning(f"Could not query instanced component classes: {str(e)}")
    expand_instanced = bool(instanced_classes) and not opts.get("foliage")

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
    export_names = ue2g_common.build_export_name_map(unique_meshes, kind="mesh")

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
                if instanced_classes and isinstance(comp, instanced_classes) \
                        and not expand_instanced:
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

                # Foliage export is off: emit one placement per instance rather
                # than one per component, which would put a whole painted field
                # on a single mesh at the component origin.
                if expand_instanced and isinstance(comp, instanced_classes):
                    placements = _expand_instanced_component(
                        foliage_mod, comp, comp_name, mesh_key, mesh_name, mesh_path,
                        extract_component_material_overrides(comp, collected_textures),
                        actor_label)
                    actor_data["components"].extend(placements)
                    total_components_count += len(placements)
                    continue

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
            landscapes_data = landscape_mod.collect_landscapes(
                all_actors, os.path.dirname(save_path), opts) or []
        except Exception as e:
            unreal.log_warning(f"Landscape export failed: {str(e)}")

    foliage_data = []
    if foliage_mod and opts.get("foliage"):
        try:
            foliage_data = foliage_mod.collect_foliage(
                all_actors, register_mesh, collected_textures) or []
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

    # Every texture reference above still holds a bare ASSET name. Now that the
    # whole level has been scanned, the collision-safe export filenames are known,
    # so rewrite the references to match the files that are about to be written.
    texture_names, texture_names_by_path = build_texture_export_names(collected_textures)
    finalize_layout_texture_names(layout_data, texture_names_by_path)

    # Write to JSON file
    try:
        with open(save_path, "w", encoding="utf-8") as f:
            json.dump(layout_data, f, indent=4)
            
        unreal.log(f"Level layout exported: {save_path}")
        
        # Export all collected textures automatically
        exported_textures_count = 0
        if collected_textures:
            try:
                parent_dir = os.path.dirname(save_path)
                textures_dir = os.path.join(parent_dir, "textures")
                os.makedirs(textures_dir, exist_ok=True)
                
                # skip_existing_textures reuses PNGs the mesh export wrote moments
                # ago -- re-encoding a 4K PNG to produce identical bytes is the
                # single most expensive thing here.
                result = ue2g_common.export_textures_to_png(
                    collected_textures, textures_dir,
                    skip_existing=skip_existing_textures, name_map=texture_names
                )
                ue2g_common.log_texture_export_result(result, textures_dir)
                exported_textures_count = len(result["exported"]) + len(result["reused"])
            except Exception as tex_err:
                unreal.log_warning(f"Failed to export level textures: {str(tex_err)}")

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
                            tex_name = texture_names.get(tex) or ue2g_common.sanitize_name(tex.get_name())
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
