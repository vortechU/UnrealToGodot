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
import math
import re
import unreal

import material_shader
import ue2g_common

# Default vertical FOV (75 deg, Godot Camera3D default) used to convert Unreal's
# LOD screen-size fraction into a Godot visibility_range switch distance (m).
_LOD_FOV_HALF_TAN = math.tan(math.radians(75.0 * 0.5))
_DEFAULT_LOD_SCREEN_SIZES = (1.0, 0.5, 0.25, 0.125, 0.0625, 0.03125)

# Per-feature export toggles (see docs/SCHEMA_V2.md). The GUI passes a matching
# dict; anything omitted falls back to these defaults.
DEFAULT_EXPORT_OPTIONS = {
    "lights": True,
    "decals": True,
    "landscape": True,
    "foliage": True,
    "navigation": True,
    "metadata": True,
    "reflection_probes": True,
    "cameras": True,
    "audio": True,
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
    for box in (ue2g_common.safe_get_prop(agg_geom, "box_elems", []) or []):
        try:
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
        except Exception as e:
            unreal.log_warning(f"Failed to read box collision element: {str(e)}")

    # 2. Sphere Elements
    for sphere in (ue2g_common.safe_get_prop(agg_geom, "sphere_elems", []) or []):
        try:
            center = sphere.get_editor_property("center")
            # Spheres don't have rotation
            godot_local = gltf_local_shape_transform(center, unreal.Quat(0.0, 0.0, 0.0, 1.0))

            collision_data["spheres"].append({
                "radius": sphere.get_editor_property("radius"), # in cm
                "godot_local_transform": godot_local
            })
        except Exception as e:
            unreal.log_warning(f"Failed to read sphere collision element: {str(e)}")

    # 3. Capsule (Sphyl) Elements
    for capsule in (ue2g_common.safe_get_prop(agg_geom, "sphyl_elems", []) or []):
        try:
            center = capsule.get_editor_property("center")
            rot = capsule.get_editor_property("rotation")
            u_quat = rot.quaternion()
            godot_local = gltf_local_shape_transform(center, u_quat)

            collision_data["capsules"].append({
                "radius": capsule.get_editor_property("radius"), # in cm
                "length": capsule.get_editor_property("length"), # cylinder length in cm
                "godot_local_transform": godot_local
            })
        except Exception as e:
            unreal.log_warning(f"Failed to read capsule collision element: {str(e)}")

    # 4. Convex Elements
    # FKConvexElem stores a Transform (not center/rotation like boxes/capsules);
    # read transform first, fall back to center/rotation, and default to identity
    # so readable vertex_data is never lost when transform fields are unexposed.
    for convex in (ue2g_common.safe_get_prop(agg_geom, "convex_elems", []) or []):
        try:
            u_scale = None
            convex_transform = ue2g_common.safe_get_prop(convex, "transform", None)
            if convex_transform is not None:
                center = convex_transform.translation
                u_quat = convex_transform.rotation
                u_scale = getattr(convex_transform, "scale3d", None)
            else:
                center = ue2g_common.safe_get_prop(convex, "center", None)
                rot = ue2g_common.safe_get_prop(convex, "rotation", None)
                if center is None:
                    center = unreal.Vector(0.0, 0.0, 0.0)
                u_quat = rot.quaternion() if rot is not None else unreal.Quat(0.0, 0.0, 0.0, 1.0)
            godot_local = gltf_local_shape_transform(center, u_quat)

            sx = u_scale.x if u_scale is not None else 1.0
            sy = u_scale.y if u_scale is not None else 1.0
            sz = u_scale.z if u_scale is not None else 1.0

            vertices = []
            vertex_data = ue2g_common.safe_get_prop(convex, "vertex_data", None)
            if vertex_data:
                for v in vertex_data:
                    vertices.append([v.x * sx, v.y * sy, v.z * sz]) # in cm, relative to shape origin

            if vertices:
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


def screen_size_to_switch_distance_m(sphere_radius_m, screen_size):
    """Converts an Unreal LOD screen-size fraction (0..1) and mesh bounding
    sphere radius (m) into a Godot visibility_range distance (m) at 75 deg FOV.

    Mirrors Unreal's ComputeBoundsScreenRadiusSquared approximation:
    screen_size ~= sphere_radius / (distance * tan(fov / 2)).
    """
    try:
        r = max(0.01, float(sphere_radius_m))
        s = float(screen_size)
    except (TypeError, ValueError):
        return 0.0
    if s <= 0.0 or s >= 1.0:
        return 0.0
    return round(r / (s * _LOD_FOV_HALF_TAN), 3)


def extract_mesh_lods(mesh, export_name):
    """Reads per-LOD screen sizes and computes Godot switch distances for meshes
    with more than 1 LOD level. Returns [] for single-LOD meshes."""
    lod_count = 1
    screen_sizes = []
    is_static = isinstance(mesh, unreal.StaticMesh)
    is_skeletal = hasattr(unreal, "SkeletalMesh") and isinstance(mesh, unreal.SkeletalMesh)

    if is_static:
        subsys = None
        if hasattr(unreal, "get_editor_subsystem") and hasattr(unreal, "StaticMeshEditorSubsystem"):
            try:
                subsys = unreal.get_editor_subsystem(unreal.StaticMeshEditorSubsystem)
            except Exception:
                subsys = None
        if subsys is not None:
            try:
                lod_count = int(subsys.get_lod_count(mesh))
            except Exception:
                pass
            try:
                screen_sizes = [float(s) for s in (subsys.get_lod_screen_sizes(mesh) or [])]
            except Exception:
                pass
        if lod_count <= 1 and hasattr(unreal, "EditorStaticMeshLibrary"):
            try:
                lod_count = int(unreal.EditorStaticMeshLibrary.get_lod_count(mesh))
            except Exception:
                pass
            if not screen_sizes and hasattr(unreal.EditorStaticMeshLibrary, "get_lod_screen_sizes"):
                try:
                    screen_sizes = [float(s) for s in (unreal.EditorStaticMeshLibrary.get_lod_screen_sizes(mesh) or [])]
                except Exception:
                    pass
        if lod_count <= 1 and hasattr(mesh, "get_num_lods"):
            try:
                lod_count = int(mesh.get_num_lods())
            except Exception:
                pass
    elif is_skeletal:
        lod_info = ue2g_common.safe_get_prop(mesh, "lod_info", None)
        if lod_info:
            lod_count = len(lod_info)
            for info in lod_info:
                ss = ue2g_common.safe_get_prop(info, "screen_size", None)
                if ss is not None:
                    val = ue2g_common.safe_get_prop(ss, "default", None)
                    try:
                        screen_sizes.append(float(val if val is not None else ss))
                    except Exception:
                        pass

    if lod_count <= 1:
        return []

    sphere_radius_m = 1.0
    try:
        bounds = mesh.get_bounds()
        if bounds is not None:
            r_cm = getattr(bounds, "sphere_radius", None)
            if r_cm is not None and float(r_cm) > 0.0:
                sphere_radius_m = float(r_cm) * ue2g_common.CM_TO_M
    except Exception:
        pass

    lods = []
    prev_dist = 0.0
    for idx in range(lod_count):
        if idx < len(screen_sizes) and screen_sizes[idx] > 0.0:
            ss = float(screen_sizes[idx])
        elif idx < len(_DEFAULT_LOD_SCREEN_SIZES):
            ss = _DEFAULT_LOD_SCREEN_SIZES[idx]
        else:
            ss = _DEFAULT_LOD_SCREEN_SIZES[-1] / (2.0 ** (idx - len(_DEFAULT_LOD_SCREEN_SIZES) + 1))

        if idx == 0:
            dist = 0.0
        else:
            dist = screen_size_to_switch_distance_m(sphere_radius_m, ss)
            if dist <= prev_dist:
                dist = round(prev_dist + max(5.0, sphere_radius_m * 4.0), 3)
            prev_dist = dist

        lods.append({
            "lod_index": idx,
            "export_name": export_name if idx == 0 else "%s_LOD%d" % (export_name, idx),
            "screen_size": round(ss, 4),
            "switch_distance_m": dist,
        })
    return lods

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


# Texture sample output pin -> the same channel indices; "RGB" and "" read red.
_OUTPUT_CHANNELS = {"R": 0, "G": 1, "B": 2, "A": 3}


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
    # Check emissive before albedo so "EmissiveColor" / "EmissiveColorMap" does
    # not match "color" and get misclassified as albedo_texture.
    ("emissive_texture", ("emissive", "emission", "glow")),
    ("albedo_texture", ("albedo", "basecolor", "diff", "colour", "color", "maintex")),
    ("normal_texture", ("normal", "bump")),
    ("opacity_texture", ("opacity", "alpha", "translucen", "transparen")),
    ("roughness_texture", ("rough",)),
    ("metallic_texture", ("metal",)),
)

# Trailing-token conventions ("T_Crate_D", "T_Crate_N_01"). Only the LAST token
# may match these -- "d" or "n" anywhere else would match nearly every name.
_ROLE_LAST_TOKENS = (
    # No bare "c": packs name generic slots "Tex C", which is not a color map.
    ("albedo_texture", ("d", "alb", "bc", "col")),
    ("normal_texture", ("n", "nrm", "norm", "nor")),
    ("emissive_texture", ("em", "emi", "emis")),
    ("opacity_texture", ("op", "opa", "alp")),
    ("roughness_texture", ("r",)),
    ("metallic_texture", ("m",)),
)

# Trailing tokens that only mean something on a TEXTURE ASSET's name, never on a
# parameter name. "_B" for base colour is the dominant suffix in some packs
# (TreatmentStation ships 41 of them) and must classify, but a material PARAMETER
# called "Tex B" is a generic slot letter and means nothing -- the same trap the
# bare-"c" note above records. Same for "_E" (emissive) and "_O" (opacity).
# Splitting the two candidate kinds lets the texture name carry the convention
# without the parameter name inheriting the false positive. ("bump" is caught by
# the substring pass before any of this runs.)
_ROLE_LAST_TOKENS_TEXTURE_ONLY = (
    ("albedo_texture", ("b",)),
    ("emissive_texture", ("e",)),
    ("opacity_texture", ("a",)),
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
# tint. Emissive vectors are routed to "emissive_color" before this filter;
# remaining non-albedo vectors are dropped so they never stain albedo_color.
_EMISSIVE_TOKENS = ("emissive", "emission", "glow")
_NON_ALBEDO_VECTOR_TOKENS = ("specular", "subsurface",
                             "sss", "fresnel", "refraction", "rim")


def classify_scalar_parameter(name):
    """Maps a scalar parameter name to its parameters[] key, else None."""
    lowered = str(name or "").lower()
    for token in _EMISSIVE_TOKENS:
        if token in lowered:
            return "emissive_energy"
    if "roughness" in lowered or lowered == "rough":
        return "roughness"
    if "metallic" in lowered or lowered == "metal":
        return "metallic"
    for token in _TILING_TOKENS:
        if token in lowered:
            return "tiling"
    if ("clip" in lowered or "cutoff" in lowered or "scissor" in lowered) and (
            "opacity" in lowered or "alpha" in lowered or "mask" in lowered):
        return "opacity_mask_clip_value"
    if "opacity" in lowered or lowered == "alpha" or lowered.endswith("_alpha"):
        return "opacity"
    return None


def classify_vector_parameter(name):
    """Maps a vector parameter name to its parameters[] key, else None.

    Tiling is checked before colour: "UV Tiling Color" style names exist, and a
    tiling parameter read as a tint is the more damaging misread of the two.
    Emissive vectors are routed to "emissive_color" rather than "albedo_color".
    """
    lowered = str(name or "").lower()
    for token in _TILING_TOKENS:
        if token in lowered:
            return "tiling"
    for token in _EMISSIVE_TOKENS:
        if token in lowered:
            return "emissive_color"
    for token in _NON_ALBEDO_VECTOR_TOKENS:
        if token in lowered:
            return None
    if ("color" in lowered or "colour" in lowered
            or "albedo" in lowered or "diffuse" in lowered):
        return "albedo_color"
    return None


def _normalize_blend_mode(raw):
    """Maps an Unreal EBlendMode enum / int / string to a schema blend_mode string."""
    if raw is None:
        return None
    name = getattr(raw, "name", None)
    text = str(name if name is not None else raw).upper()
    if "MASK" in text or text == "1":
        return "masked"
    if "ADDITIVE" in text or text == "3":
        return "additive"
    if "TRANSLUCENT" in text or "ALPHA_COMPOSITE" in text or "MODULATE" in text or text in ("2", "4", "5"):
        return "translucent"
    if "OPAQUE" in text or text == "0":
        return "opaque"
    return None


def _is_normal_map_texture(tex):
    """Returns True when an Unreal Texture's compression/LOD settings mark it as a normal map."""
    for prop in ("compression_settings", "lod_group"):
        val = ue2g_common.safe_get_prop(tex, prop, None)
        if val is None:
            continue
        name = getattr(val, "name", None)
        text = str(name if name is not None else val).upper()
        if "NORMALMAP" in text or "NORMAL_MAP" in text:
            return True
    return False


def build_texture_metadata(collected_textures, texture_names, layout_data=None):
    """Builds schema `texture_metadata` keyed by exported filename stem.

    Records normal-map entries with Unreal's `flip_green_channel` flag:
    - flip_green_channel == False (UE default): source art is DirectX (-Y), so
      Godot should invert the green channel to match OpenGL (+Y).
    - flip_green_channel == True: source art was authored OpenGL (+Y) and UE
      flips it at cook time, so Godot must NOT invert it again.
    """
    meta = {}
    tex_by_export_name = {}
    for tex in (collected_textures or ()):
        if tex is None:
            continue
        export_name = (texture_names or {}).get(tex)
        if not export_name:
            export_name = ue2g_common.sanitize_name(ue2g_common.safe_get_name(tex))
        tex_by_export_name[export_name] = tex
        if _is_normal_map_texture(tex):
            flip_g = bool(ue2g_common.safe_get_prop(tex, "flip_green_channel", False))
            meta[export_name] = {
                "is_normal_map": True,
                "flip_green_channel": flip_g,
            }

    # Also include any texture bound into a normal slot in the layout JSON, in
    # case the texture asset left compression_settings at TC_Default.
    if isinstance(layout_data, dict):
        normal_names = set()
        for mesh_entry in (layout_data.get("meshes") or {}).values():
            for mat in (mesh_entry.get("materials") or []):
                nt = (mat.get("parameters") or {}).get("normal_texture")
                if nt:
                    normal_names.add(str(nt))
        for actor in (layout_data.get("actors") or []):
            for comp in (actor.get("components") or []):
                for ov in (comp.get("material_overrides") or []):
                    nt = (ov.get("parameters") or {}).get("normal_texture")
                    if nt:
                        normal_names.add(str(nt))
        for fol in (layout_data.get("foliage") or []):
            for ov in (fol.get("material_overrides") or []):
                nt = (ov.get("parameters") or {}).get("normal_texture")
                if nt:
                    normal_names.add(str(nt))
        for decal in (layout_data.get("decals") or []):
            nt = (decal.get("textures") or {}).get("normal")
            if nt:
                normal_names.add(str(nt))
        for ls in (layout_data.get("landscapes") or []):
            mat = ls.get("material")
            if isinstance(mat, dict):
                for t in (mat.get("textures") or []):
                    if isinstance(t, dict) and t.get("role") == "normal" and t.get("texture"):
                        normal_names.add(str(t["texture"]))

        for name in normal_names:
            if name not in meta:
                tex = tex_by_export_name.get(name)
                flip_g = bool(ue2g_common.safe_get_prop(tex, "flip_green_channel", False)) if tex else False
                meta[name] = {
                    "is_normal_map": True,
                    "flip_green_channel": flip_g,
                }
    return meta


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
    apply_texture_export_names(slots.get("shader"), name_by_path)
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
    for foliage in (layout_data.get("foliage") or []):
        for override in (foliage.get("material_overrides") or []):
            apply_texture_export_names(override.get("parameters"), name_by_path)
    for decal in (layout_data.get("decals") or []):
        apply_texture_export_names(decal.get("textures"), name_by_path)
    # Landscape material textures carry "path" when available (preferred so colliding
    # asset names disambiguate accurately) and fall back to matching by asset name.
    name_by_asset = {}
    for path, final in name_by_path.items():
        name_by_asset[str(path).split(".")[-1]] = final
    for landscape in (layout_data.get("landscapes") or []):
        material = landscape.get("material")
        if not isinstance(material, dict):
            continue
        for tex in (material.get("textures") or []):
            if not isinstance(tex, dict):
                continue
            final = name_by_path.get(tex.get("path")) if tex.get("path") else None
            if not final:
                final = name_by_asset.get(tex.get("texture"))
            if final:
                tex["texture"] = final


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
        "blend_mode": "opaque",
        "two_sided": False,
        "opacity": 1.0,
        "opacity_mask_clip_value": 0.3333,
        "opacity_texture": None,
        "opacity_channel": 0,
        "emissive_color": None,
        "emissive_texture": None,
        "emissive_energy": 1.0,
        "texture_paths": {}
    }

    visited = set()
    opacity_sources = {}
    # Tracks scalar/vector/surface params that have been explicitly set, so the
    # first (child-most) material that provides one wins — even if its value
    # happens to equal a default (e.g. roughness exactly 0.5). Texture params
    # already use a None sentinel below and need no tracking here.
    assigned = set()

    # What the graph really wires up beats what names suggest (None: cannot tell).
    trace = ue2g_common.trace_material_graph(material)
    traced = trace["samples"] if trace else {}

    def _traced_roles(param_name, tex):
        """{role: output pin} the graph gives this texture, e.g. {"roughness": "G"}."""
        roles = {}
        for role, sample in traced.items():
            same = (sample["param"] == param_name if param_name
                    else not sample["param"] and _path_of(sample["texture"]) == _path_of(tex))
            if same:
                roles[role] = sample["output"]
        return roles

    def _set_slot(slot, tex):
        parameters[slot] = tex.get_name()
        parameters["texture_paths"][slot] = _path_of(tex)

    def _assign_traced(tex, roles):
        for role in ("albedo", "normal", "emissive"):
            if role in roles and parameters[role + "_texture"] is None:
                _set_slot(role + "_texture", tex)
        channels = {r: _OUTPUT_CHANNELS.get(roles[r], 0)
                    for r in ("roughness", "metallic", "ao") if r in roles}
        if len(channels) == 1 and "ao" not in channels and 0 in channels.values():
            # One grayscale map, which is what the standalone slots hold.
            slot = next(iter(channels)) + "_texture"
            if parameters[slot] is None:
                _set_slot(slot, tex)
        elif channels:
            # Don't let an AO-only texture lock out a richer roughness/metallic
            # packed map that arrives later on the same material.
            existing_ch = parameters.get("packed_channels") or {}
            if parameters["packed_texture"] is None or (
                    set(existing_ch.keys()) == {"ao"} and set(channels.keys()) != {"ao"}):
                parameters["packed_channels"] = channels
                _set_slot("packed_texture", tex)

    def _assign_texture(param_name, tex):
        """Classifies one sampled texture and records its slot plus its asset path."""
        tex_name = tex.get_name()
        roles = _traced_roles(str(param_name or ""), tex)
        had_traced = bool(roles)
        for role in ("opacity", "opacity_mask"):
            output = roles.pop(role, None)
            if output is not None:
                opacity_sources.setdefault(role, (tex, output))
        if roles:
            _assign_traced(tex, roles)
            return
        if had_traced:
            return
        role, packed = resolve_texture_role(str(param_name).lower(), tex_name)
        # A name never overrules an output the graph already settled.
        if packed:
            packed = {k: v for k, v in packed.items() if k not in traced}
            if not packed:
                return
        elif role and role.replace("_texture", "") in ("albedo", "normal", "roughness",
                                                       "metallic", "emissive") \
                and role.replace("_texture", "") in traced:
            return
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

    def _reaches_emission(name):
        """False only when the graph says this parameter never reaches emission."""
        return (not trace or trace["emissive"] is None
                or str(name) in trace["emissive"])

    def _scalar_key(name):
        """classify_scalar_parameter, corrected by what the graph says."""
        key = classify_scalar_parameter(name)
        if not trace:
            return key
        if key == "emissive_energy" and not _reaches_emission(name):
            return None
        for role, names in trace["scalars"].items():
            if str(name) in names:
                return role
        # "Dirt Roughness Opacity" has the word but does not scale the roughness.
        if key in trace["scalars"] or key in trace["constants"]:
            return None
        return key

    def _assign_vector(name, value):
        key = classify_vector_parameter(name)
        if key is None and trace and trace["tints"] is not None and str(name) in trace["tints"]:
            key = "albedo_color"
        if key is None or key in assigned:
            return
        # A Blueprint often sets one on every slot, used by the material or not.
        if key == "emissive_color" and not _reaches_emission(name):
            return
        if key == "albedo_color" and trace and trace["tints"] is not None \
                and str(name) not in trace["tints"] \
                and (str(name) in trace["not_tints"] or not trace["hidden"]):
            return
        try:
            if key == "tiling":
                parameters["tiling"] = [float(value.r), float(value.g)]
            elif key == "emissive_color":
                parameters["emissive_color"] = [float(value.r), float(value.g), float(value.b)]
            else:
                parameters["albedo_color"] = [float(value.r), float(value.g),
                                              float(value.b), float(value.a)]
                # A tint's alpha means nothing unless the graph uses it.
                if trace and trace["alpha"] is not None and str(name) not in trace["alpha"]:
                    parameters["albedo_color"][3] = 1.0
            assigned.add(key)
        except Exception:
            pass

    def _read_surface_properties(mat, is_instance):
        """Reads blend_mode, two_sided, and opacity_mask_clip_value defensively."""
        if is_instance:
            bpo = ue2g_common.safe_get_prop(mat, "base_property_overrides", None)
            if bpo is not None:
                if "blend_mode" not in assigned and ue2g_common.safe_get_prop(bpo, "override_blend_mode", False):
                    bm = _normalize_blend_mode(ue2g_common.safe_get_prop(bpo, "blend_mode", None))
                    if bm is not None:
                        parameters["blend_mode"] = bm
                        assigned.add("blend_mode")
                if "two_sided" not in assigned and ue2g_common.safe_get_prop(bpo, "override_two_sided", False):
                    ts = ue2g_common.safe_get_prop(bpo, "two_sided", None)
                    if ts is not None:
                        parameters["two_sided"] = bool(ts)
                        assigned.add("two_sided")
                if "opacity_mask_clip_value" not in assigned and ue2g_common.safe_get_prop(bpo, "override_opacity_mask_clip_value", False):
                    cv = ue2g_common.safe_get_prop(bpo, "opacity_mask_clip_value", None)
                    if cv is not None:
                        try:
                            parameters["opacity_mask_clip_value"] = float(cv)
                            assigned.add("opacity_mask_clip_value")
                        except Exception:
                            pass
        else:
            if "blend_mode" not in assigned:
                bm = _normalize_blend_mode(ue2g_common.safe_get_prop(mat, "blend_mode", None))
                if bm is not None:
                    parameters["blend_mode"] = bm
                    assigned.add("blend_mode")
            if "two_sided" not in assigned:
                ts = ue2g_common.safe_get_prop(mat, "two_sided", None)
                if ts is not None:
                    parameters["two_sided"] = bool(ts)
                    assigned.add("two_sided")
            if "opacity_mask_clip_value" not in assigned:
                cv = ue2g_common.safe_get_prop(mat, "opacity_mask_clip_value", None)
                if cv is not None:
                    try:
                        parameters["opacity_mask_clip_value"] = float(cv)
                        assigned.add("opacity_mask_clip_value")
                    except Exception:
                        pass

    def _extract_recursive(mat, is_parent=False):
        if not mat or mat in visited:
            return
        visited.add(mat)

        is_instance = isinstance(mat, unreal.MaterialInstance)
        if not is_instance and hasattr(unreal, "MaterialInstanceConstant"):
            is_instance = isinstance(mat, unreal.MaterialInstanceConstant)

        _read_surface_properties(mat, is_instance)

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
                    _assign_scalar(_scalar_key(pname), float(value))
                except Exception:
                    continue
            for pname, value in ue2g_common.iter_base_material_vectors(mat):
                _assign_vector(pname, value)
            return

        # Material Instance: Parse overridden parameters
        # 1. Parse Scalar parameters
        try:
            scalars = mat.get_editor_property("scalar_parameter_values")
            if scalars:
                for s in scalars:
                    # Child instances are visited before parents; first explicit value wins.
                    _assign_scalar(_scalar_key(s.parameter_info.name), s.parameter_value)
        except Exception:
            pass

        # 2. Parse Vector parameters
        try:
            vectors = mat.get_editor_property("vector_parameter_values")
            if vectors:
                for v in vectors:
                    _assign_vector(v.parameter_info.name, v.parameter_value)
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

    if trace:
        for key, value in trace["constants"].items():
            _assign_scalar(key, value)

        # Masked and translucent read different outputs. Alpha already inside the
        # albedo texture needs no separate mask.
        source = opacity_sources.get(
            {"masked": "opacity_mask", "translucent": "opacity", "additive": "opacity"}
            .get(parameters["blend_mode"]))
        if source:
            tex, output = source
            if (output == "A"
                    and _path_of(tex) == parameters["texture_paths"].get("albedo_texture")):
                parameters["opacity_texture"] = None
                parameters["texture_paths"].pop("opacity_texture", None)
            else:
                _set_slot("opacity_texture", tex)
                parameters["opacity_channel"] = _OUTPUT_CHANNELS.get(output, 0)

    # Godot computes roughness/metallic as scalar * texture[channel]. Unreal drives
    # them straight from a packed or standalone map unless the material exposes an
    # explicit multiplier, so a scalar left at its default would zero or halve the
    # texture -- metallic defaults to 0.0, which cancels a metallic map entirely,
    # and roughness defaults to 0.5, which halves a standalone roughness map.
    # Promote unset scalars to 1.0 so the map passes through unchanged.
    if parameters["packed_texture"]:
        channels = parameters["packed_channels"] or {}
        if "roughness" in channels and "roughness" not in assigned:
            parameters["roughness"] = 1.0
        if "metallic" in channels and "metallic" not in assigned:
            parameters["metallic"] = 1.0
    if parameters["roughness_texture"] and "roughness" not in assigned:
        parameters["roughness"] = 1.0
    if parameters["metallic_texture"] and "metallic" not in assigned:
        parameters["metallic"] = 1.0

    # Unreal artists routinely author HDR emissive colors with components > 1.0
    # directly on Emissive Color (e.g. (3.5, 3.35, 2.8) with no separate scalar
    # intensity). Normalize into [0, 1] RGB and fold peak intensity into
    # emissive_energy so Godot's emission color picker and glTF emissiveFactor
    # preserve both hue and luminance instead of clipping to white.
    ec = parameters.get("emissive_color")
    if isinstance(ec, list) and len(ec) >= 3:
        peak = max(float(ec[0]), float(ec[1]), float(ec[2]))
        if peak > 1.0:
            parameters["emissive_color"] = [
                round(max(0.0, float(ec[0])) / peak, 4),
                round(max(0.0, float(ec[1])) / peak, 4),
                round(max(0.0, float(ec[2])) / peak, 4),
            ]
            parameters["emissive_energy"] = round(
                max(0.0, float(parameters.get("emissive_energy", 1.0))) * peak, 4
            )

    # Layers blended by vertex paint: the slots above hold the base layer only.
    shader, textures = material_shader.build_shader(
        material, parameters["blend_mode"], parameters["two_sided"],
        parameters["opacity_mask_clip_value"])
    if shader:
        parameters["shader"] = shader
        if collected_textures is not None:
            collected_textures.update(textures)

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
                                mesh_path, material_overrides, actor_label,
                                render_flags=None):
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

    if render_flags is None:
        render_flags = extract_component_render_flags(comp)

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
                "visible": render_flags["visible"],
                "cast_shadow": render_flags["cast_shadow"],
                "collision_enabled": render_flags["collision_enabled"],
                "max_draw_distance_m": render_flags["max_draw_distance_m"],
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


def extract_component_render_flags(comp, actor=None):
    """Reads per-component visibility, shadow casting, collision enabled, and
    max draw distance from a StaticMeshComponent / SkeletalMeshComponent."""
    comp_vis = bool(ue2g_common.safe_get_prop(comp, "visible", True))
    comp_hidden = bool(ue2g_common.safe_get_prop(comp, "hidden_in_game", False))
    actor_hidden = False
    if actor is not None:
        try:
            actor_hidden = bool(actor.is_hidden_ed()) if hasattr(actor, "is_hidden_ed") else False
        except Exception:
            actor_hidden = False
        if not actor_hidden:
            actor_hidden = bool(ue2g_common.safe_get_prop(actor, "hidden", False))
    visible = comp_vis and (not comp_hidden) and (not actor_hidden)

    cast_shadow = bool(ue2g_common.safe_get_prop(comp, "cast_shadow", True))

    collision_enabled = True
    try:
        if hasattr(comp, "get_collision_enabled"):
            ce = comp.get_collision_enabled()
            ce_name = str(getattr(ce, "name", ce)).upper().replace("_", "")
            if "NOCOLLISION" in ce_name or ce_name == "0":
                collision_enabled = False
        else:
            body_inst = ue2g_common.safe_get_prop(comp, "body_instance", None)
            if body_inst is not None:
                ce = ue2g_common.safe_get_prop(body_inst, "collision_enabled", None)
                if ce is not None:
                    ce_name = str(getattr(ce, "name", ce)).upper().replace("_", "")
                    if "NOCOLLISION" in ce_name or ce_name == "0":
                        collision_enabled = False
            if collision_enabled and hasattr(comp, "get_collision_profile_name"):
                prof = str(comp.get_collision_profile_name() or "").lower()
                if prof == "nocollision":
                    collision_enabled = False
    except Exception:
        pass

    max_draw_m = None
    for prop in ("ld_max_draw_distance", "cached_max_draw_distance"):
        val = ue2g_common.safe_get_prop(comp, prop, None)
        if val is not None:
            try:
                fval = float(val)
                if fval > 0.0:
                    max_draw_m = round(fval * ue2g_common.CM_TO_M, 3)
                    break
            except (TypeError, ValueError):
                pass

    return {
        "visible": visible,
        "cast_shadow": cast_shadow,
        "collision_enabled": collision_enabled,
        "max_draw_distance_m": max_draw_m,
    }


def extract_spline_mesh_params(comp):
    """Extracts cubic Hermite spline deformation data from a SplineMeshComponent,
    converted into glTF mesh-local space ((ux, uz, uy) * 0.01). Returns None for
    ordinary non-spline components."""
    spline_cls = getattr(unreal, "SplineMeshComponent", None)
    if spline_cls is None or not isinstance(comp, spline_cls):
        return None
    params = ue2g_common.safe_get_prop(comp, "spline_params", None)
    if params is None:
        return None

    def _gltf_vec_m(v, default=(0.0, 0.0, 0.0)):
        if v is None:
            return list(default)
        try:
            return [float(v.x) * ue2g_common.CM_TO_M,
                    float(v.z) * ue2g_common.CM_TO_M,
                    float(v.y) * ue2g_common.CM_TO_M]
        except Exception:
            return list(default)

    def _vec2(v, default=(1.0, 1.0)):
        if v is None:
            return list(default)
        try:
            return [float(v.x), float(v.y)]
        except Exception:
            return list(default)

    def _num_p(obj, prop, default=0.0):
        val = ue2g_common.safe_get_prop(obj, prop, default)
        try:
            return float(val) if val is not None else default
        except (TypeError, ValueError):
            return default

    fwd_raw = ue2g_common.safe_get_prop(comp, "forward_axis", None)
    fwd_name = str(getattr(fwd_raw, "name", fwd_raw) or "X").upper()
    # Map Unreal local axis -> glTF mesh-local axis (X->X, Y->Z, Z->Y)
    if "Y" in fwd_name or fwd_name == "1":
        forward_axis = "z"
    elif "Z" in fwd_name or fwd_name == "2":
        forward_axis = "y"
    else:
        forward_axis = "x"

    up_raw = ue2g_common.safe_get_prop(comp, "spline_up_dir", None)
    if up_raw is not None:
        try:
            up_dir = [float(up_raw.x), float(up_raw.z), float(up_raw.y)]
        except Exception:
            up_dir = [0.0, 1.0, 0.0]
    else:
        up_dir = [0.0, 1.0, 0.0]

    def _vec2_m(v, default=(0.0, 0.0)):
        if v is None:
            return list(default)
        try:
            return [float(v.x) * ue2g_common.CM_TO_M, float(v.y) * ue2g_common.CM_TO_M]
        except Exception:
            return list(default)

    return {
        "start_pos": _gltf_vec_m(ue2g_common.safe_get_prop(params, "start_pos", None)),
        "start_tangent": _gltf_vec_m(ue2g_common.safe_get_prop(params, "start_tangent", None), (1.0, 0.0, 0.0)),
        "start_scale": _vec2(ue2g_common.safe_get_prop(params, "start_scale", None)),
        "start_offset": _vec2_m(ue2g_common.safe_get_prop(params, "start_offset", None)),
        "start_roll_rad": _num_p(params, "start_roll", 0.0),
        "end_pos": _gltf_vec_m(ue2g_common.safe_get_prop(params, "end_pos", None), (1.0, 0.0, 0.0)),
        "end_tangent": _gltf_vec_m(ue2g_common.safe_get_prop(params, "end_tangent", None), (1.0, 0.0, 0.0)),
        "end_scale": _vec2(ue2g_common.safe_get_prop(params, "end_scale", None)),
        "end_offset": _vec2_m(ue2g_common.safe_get_prop(params, "end_offset", None)),
        "end_roll_rad": _num_p(params, "end_roll", 0.0),
        "forward_axis": forward_axis,
        "up_dir": up_dir,
    }


def extract_actor_folder_path(actor):
    """Returns normalized World Outliner folder path (e.g. 'Architecture/Walls') or ''."""
    if actor is None or not hasattr(actor, "get_folder_path"):
        return ""
    try:
        raw = str(actor.get_folder_path() or "").strip()
    except Exception:
        return ""
    if not raw or raw.lower() == "none":
        return ""
    parts = [p.strip() for p in raw.replace("\\", "/").split("/") if p.strip()]
    return "/".join(parts)


def extract_actor_id(actor):
    """Returns a stable identifier for incremental re-import (actor GUID or persistent path)."""
    if actor is None:
        return ""
    guid = ue2g_common.safe_get_prop(actor, "actor_guid", None)
    try:
        # str(unreal.Guid) is the wrapper's memory address, different on every export.
        gstr = str(guid.to_string()).strip()
        if gstr and not all(c in "0-" for c in gstr):
            return gstr
    except Exception:
        pass
    if hasattr(actor, "get_path_name"):
        try:
            p = str(actor.get_path_name() or "").strip()
            if p:
                return p
        except Exception:
            pass
    return ue2g_common.safe_get_name(actor)


def extract_blueprint_asset(actor):
    """Returns the Blueprint / PackedLevelActor class path for prefab extraction, or ''."""
    if actor is None:
        return ""
    try:
        cls = actor.get_class()
        cls_name = str(cls.get_name() or "")
        if cls_name.endswith("_C") or "PackedLevelActor" in cls_name:
            if hasattr(cls, "get_path_name"):
                return str(cls.get_path_name() or cls_name)
            return cls_name
    except Exception:
        pass
    return ""


def inspect_world_streaming(world):
    """Detects World Partition and unloaded streaming sublevels on `world`.

    Returns None for simple single-level worlds, or:
        {"is_world_partition": bool, "loaded_sublevels": [...], "unloaded_sublevels": [...]}
    """
    if world is None:
        return None
    is_wp = False
    try:
        if hasattr(world, "is_partitioned_world"):
            is_wp = bool(world.is_partitioned_world())
    except Exception:
        pass
    if not is_wp:
        # In UE 5.x, world_partition is a property on AWorldSettings (not UWorld).
        try:
            ws = world.get_world_settings() if hasattr(world, "get_world_settings") else None
            if ws is not None and ue2g_common.safe_get_prop(ws, "world_partition", None) is not None:
                is_wp = True
        except Exception:
            pass
    if not is_wp:
        try:
            if ue2g_common.safe_get_prop(world, "world_partition", None) is not None:
                is_wp = True
        except Exception:
            pass

    loaded = []
    unloaded = []
    try:
        levels = None
        if hasattr(world, "get_streaming_levels"):
            levels = world.get_streaming_levels()
        if levels is None:
            levels = ue2g_common.safe_get_prop(world, "streaming_levels", None)
        for lvl in (levels or []):
            if lvl is None:
                continue
            pkg = ue2g_common.safe_get_prop(lvl, "world_asset_package_name", None)
            if not pkg and hasattr(lvl, "get_world_asset_package_f_name"):
                try:
                    pkg = lvl.get_world_asset_package_f_name()
                except Exception:
                    pkg = None
            if not pkg:
                wa = ue2g_common.safe_get_prop(lvl, "world_asset", None)
                if wa is not None:
                    pkg = ue2g_common.safe_get_name(wa) if hasattr(wa, "get_name") else str(wa)
            name = str(pkg or ue2g_common.safe_get_name(lvl))
            is_loaded = False
            if hasattr(lvl, "is_level_loaded"):
                try:
                    is_loaded = bool(lvl.is_level_loaded())
                except Exception:
                    is_loaded = False
            elif ue2g_common.safe_get_prop(lvl, "loaded_level", None) is not None:
                is_loaded = True
            if is_loaded:
                loaded.append(name)
            else:
                unloaded.append(name)
    except Exception:
        pass

    if not is_wp and not loaded and not unloaded:
        return None
    return {
        "is_world_partition": is_wp,
        "loaded_sublevels": sorted(set(loaded)),
        "unloaded_sublevels": sorted(set(unloaded)),
    }


def _get_skeletal_mesh(comp):
    """Reads a SkeletalMeshComponent's SkeletalMesh across UE 4.x and UE 5.1+ APIs."""
    skel_cls = getattr(unreal, "SkeletalMesh", None)
    if hasattr(comp, "get_skeletal_mesh_asset"):
        try:
            m = comp.get_skeletal_mesh_asset()
            if m is not None:
                return m
        except Exception:
            pass
    for prop in ("skeletal_mesh_asset", "skinned_asset", "skeletal_mesh"):
        m = ue2g_common.safe_get_prop(comp, prop, None)
        if m is not None and (skel_cls is None or isinstance(m, skel_cls)):
            return m
    if hasattr(comp, "skeletal_mesh"):
        try:
            return comp.skeletal_mesh
        except Exception:
            pass
    return None


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
    need_env = (opts.get("lights") or opts.get("decals")
                or opts.get("reflection_probes") or opts.get("cameras") or opts.get("audio"))
    environment_mod = _try_import("export_environment") if need_env else None
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
    
    world = None
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
    ue2g_common.clear_material_trace_cache()
    material_shader.clear_cache()
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
                if ue2g_common.safe_get_prop(comp, "is_editor_only", False):
                    continue
                m = ue2g_common.safe_get_prop(comp, "static_mesh")
                if m:
                    unique_meshes.add(m)
            if hasattr(unreal, "SkeletalMeshComponent"):
                for comp in actor.get_components_by_class(unreal.SkeletalMeshComponent):
                    if ue2g_common.safe_get_prop(comp, "is_editor_only", False):
                        continue
                    m = _get_skeletal_mesh(comp)
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
            entry = {
                "path": mesh.get_path_name(),
                "export_name": mesh_key,
                "collision": extract_mesh_collision(mesh),
                "materials": extract_mesh_materials(mesh, collected_textures)
            }
            lods = extract_mesh_lods(mesh, mesh_key)
            if lods:
                entry["lods"] = lods
            if hasattr(unreal, "SkeletalMesh") and isinstance(mesh, unreal.SkeletalMesh):
                if ue2g_common.safe_get_prop(mesh, "physics_asset", None) is not None:
                    entry["physics_file"] = "%s_physics.json" % mesh_key
            mesh_library[mesh_key] = entry
        return mesh_key

    cancelled = False
    with unreal.ScopedSlowTask(len(all_actors), "Scanning level actors for Static Meshes...") as slow_task:
        slow_task.make_dialog(can_cancel=True)
        
        for actor in all_actors:
            if slow_task.should_cancel():
                unreal.log_warning("Export cancelled by user.")
                cancelled = True
                break
            if actor is None:
                continue

            try:
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
                    if ue2g_common.safe_get_prop(comp, "is_editor_only", False):
                        continue
                    if instanced_classes and isinstance(comp, instanced_classes) \
                            and not expand_instanced:
                        continue  # exported as packed instance arrays by the foliage exporter
                    mesh = comp.static_mesh if hasattr(comp, "static_mesh") else comp.get_editor_property("static_mesh")
                    if mesh:
                        valid_components.append((comp, mesh))
                        
                for comp in skeletal_comps:
                    if ue2g_common.safe_get_prop(comp, "is_editor_only", False):
                        continue
                    mesh = _get_skeletal_mesh(comp)
                    if mesh:
                        valid_components.append((comp, mesh))
                        
                if not valid_components:
                    continue # Skip actors with no meshes (e.g. lights, cameras, logic)
                    
                # Collect actor-level properties
                actor_class = actor.get_class().get_name()
                actor_transform = actor.get_actor_transform()
                
                actor_data = {
                    "name": actor_label,
                    "actor_id": extract_actor_id(actor),
                    "class": actor_class,
                    "unreal_transform": unreal_transform_to_dict(actor_transform),
                    "godot_transform": unreal_to_godot_transform(actor_transform),
                    "components": []
                }
                folder_path = extract_actor_folder_path(actor)
                if folder_path:
                    actor_data["folder_path"] = folder_path
                bp_asset = extract_blueprint_asset(actor)
                if bp_asset:
                    actor_data["blueprint_asset"] = bp_asset

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

                    comp_tags = []
                    if gameplay_mod and opts.get("metadata"):
                        try:
                            comp_tags = gameplay_mod.extract_component_tags(comp) or []
                        except Exception:
                            comp_tags = []

                    # Foliage export is off: emit one placement per instance rather
                    # than one per component, which would put a whole painted field
                    # on a single mesh at the component origin.
                    if expand_instanced and isinstance(comp, instanced_classes):
                        render_flags = extract_component_render_flags(comp, actor)
                        placements = _expand_instanced_component(
                            foliage_mod, comp, comp_name, mesh_key, mesh_name, mesh_path,
                            extract_component_material_overrides(comp, collected_textures),
                            actor_label, render_flags=render_flags)
                        if comp_tags:
                            for p in placements:
                                p["tags"] = list(comp_tags)
                        actor_data["components"].extend(placements)
                        total_components_count += len(placements)
                        continue

                    # Fetch local relative transform and absolute world transform of the component
                    comp_relative_transform = comp.get_relative_transform()
                    comp_world_transform = comp.get_world_transform()

                    render_flags = extract_component_render_flags(comp, actor)
                    comp_data = {
                        "name": comp_name,
                        "mesh_key": mesh_key,
                        "mesh_name": mesh_name,
                        "mesh_path": mesh_path,
                        "unreal_relative_transform": unreal_transform_to_dict(comp_relative_transform),
                        "godot_relative_transform": unreal_to_godot_transform(comp_relative_transform),
                        "unreal_world_transform": unreal_transform_to_dict(comp_world_transform),
                        "godot_world_transform": unreal_to_godot_transform(comp_world_transform),
                        "visible": render_flags["visible"],
                        "cast_shadow": render_flags["cast_shadow"],
                        "collision_enabled": render_flags["collision_enabled"],
                        "max_draw_distance_m": render_flags["max_draw_distance_m"],
                        "material_overrides": extract_component_material_overrides(comp, collected_textures)
                    }
                    spline_data = extract_spline_mesh_params(comp)
                    if spline_data is not None:
                        comp_data["spline_mesh"] = spline_data
                    paint = None
                    try:
                        paint = (material_shader.extract_vertex_paint(comp)
                                 if isinstance(comp, unreal.StaticMeshComponent) else None)
                    except Exception as e:
                        unreal.log_warning(f"Could not read the vertex paint of {actor_label}: {str(e)}")
                    if paint:
                        # One placement after another paints the same vertices.
                        mesh_library[mesh_key].setdefault("paint_points", paint[0])
                        comp_data["vertex_paint"] = paint[1]

                    if comp_tags:
                        comp_data["tags"] = comp_tags

                    actor_data["components"].append(comp_data)
                    total_components_count += 1
                    
                if actor_data["components"]:
                    exported_actors.append(actor_data)
            except Exception as actor_err:
                unreal.log_warning(f"Failed to export actor '{ue2g_common.safe_get_name(actor)}': {str(actor_err)}")

    if cancelled:
        unreal.log_warning("Level layout export aborted before writing file.")
        return False

    # 5. Collect feature data (lights, post-process, decals, probes, cameras, audio, terrain, foliage, navigation)
    environment_data = {}
    collected_sounds = set()
    if environment_mod:
        try:
            want_lights = bool(opts.get("lights"))
            want_decals = bool(opts.get("decals"))
            want_probes = bool(opts.get("reflection_probes", True))
            want_cameras = bool(opts.get("cameras", True))
            want_audio = bool(opts.get("audio", True))
            try:
                environment_data = environment_mod.collect_environment(
                    all_actors, collected_textures,
                    include_lights=want_lights, include_decals=want_decals,
                    include_reflection_probes=want_probes,
                    include_cameras=want_cameras,
                    include_audio=want_audio,
                    collected_sounds=collected_sounds) or {}
            except TypeError:
                # Backward compatibility with custom/legacy collect_environment signatures.
                tex_sink = collected_textures if want_decals else set()
                environment_data = environment_mod.collect_environment(all_actors, tex_sink) or {}
        except Exception as e:
            unreal.log_warning(f"Environment export failed: {str(e)}")
    if not opts.get("lights"):
        environment_data["lights"] = []
        environment_data["post_process"] = []
        environment_data["height_fog"] = None
        environment_data["sky_light"] = None
        environment_data["has_sky_atmosphere"] = False
        environment_data["gi_method"] = None
    if not opts.get("decals"):
        environment_data["decals"] = []
    if not opts.get("reflection_probes", True):
        environment_data["reflection_probes"] = []
    if not opts.get("cameras", True):
        environment_data["cameras"] = []
    if not opts.get("audio", True):
        environment_data["audio"] = []

    landscapes_data = []
    if landscape_mod:
        try:
            landscapes_data = landscape_mod.collect_landscapes(
                all_actors, os.path.dirname(save_path), opts, collected_textures) or []
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
    world_streaming = inspect_world_streaming(world)
    if isinstance(world_streaming, dict):
        unloaded_lvls = world_streaming.get("unloaded_sublevels") or []
        if unloaded_lvls:
            unreal.log_warning(
                "Unreal to Godot: %d streaming sublevel(s) are currently unloaded and "
                "were omitted from this export: %s"
                % (len(unloaded_lvls), ", ".join(unloaded_lvls[:5])))
        if world_streaming.get("is_world_partition"):
            unreal.log_warning(
                "Unreal to Godot: '%s' uses World Partition; only currently loaded cells "
                "were exported." % world_name)

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
        "gi_method": environment_data.get("gi_method"),
        "decals": environment_data.get("decals", []),
        "reflection_probes": environment_data.get("reflection_probes", []),
        "cameras": environment_data.get("cameras", []),
        "audio": environment_data.get("audio", []),
        "landscapes": landscapes_data,
        "foliage": foliage_data,
        "navigation": navigation_data
    }
    if world_streaming is not None:
        layout_data["world_streaming"] = world_streaming

    # Every texture reference above still holds a bare ASSET name. Now that the
    # whole level has been scanned, the collision-safe export filenames are known,
    # so rewrite the references to match the files that are about to be written.
    texture_names, texture_names_by_path = build_texture_export_names(collected_textures)
    finalize_layout_texture_names(layout_data, texture_names_by_path)
    layout_data["texture_metadata"] = build_texture_metadata(
        collected_textures, texture_names, layout_data)

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

        # Export collected SoundWave audio assets to sibling audio/ folder
        if collected_sounds and environment_mod and hasattr(environment_mod, "export_sound_waves"):
            try:
                audio_dir = os.path.join(os.path.dirname(save_path), "audio")
                environment_mod.export_sound_waves(collected_sounds, audio_dir)
            except Exception as aud_err:
                unreal.log_warning(f"Failed to export level audio: {str(aud_err)}")

        if godot_project_dir and os.path.isdir(godot_project_dir):
            import shutil
            unreal.log(f"Unreal to Godot: Automatically transferring layout and textures to Godot project: {godot_project_dir}")

            def _same_path(a, b):
                return os.path.normcase(os.path.abspath(a)) == os.path.normcase(os.path.abspath(b))

            # Copy JSON file
            try:
                dest_json = os.path.join(godot_project_dir, os.path.basename(save_path))
                if not _same_path(save_path, dest_json):
                    shutil.copy2(save_path, dest_json)
                    unreal.log(f"Transferred layout JSON: {os.path.basename(save_path)} -> {godot_project_dir}")
            except Exception as copy_err:
                unreal.log_warning(f"Failed to auto-transfer layout JSON to Godot: {str(copy_err)}")

            # Copy textures
            if collected_textures:
                try:
                    godot_textures_dir = os.path.join(godot_project_dir, "textures")
                    os.makedirs(godot_textures_dir, exist_ok=True)
                    local_textures_dir = os.path.join(os.path.dirname(save_path), "textures")
                    if os.path.exists(local_textures_dir):
                        for tex in collected_textures:
                            if not tex or not isinstance(tex, unreal.Texture):
                                continue
                            tex_name = texture_names.get(tex) or ue2g_common.sanitize_name(tex.get_name())
                            for ext in ue2g_common.TEXTURE_EXPORT_EXTS:
                                src_tex = os.path.join(local_textures_dir, tex_name + ext)
                                if os.path.exists(src_tex):
                                    dest_tex = os.path.join(godot_textures_dir, tex_name + ext)
                                    if not _same_path(src_tex, dest_tex):
                                        shutil.copy2(src_tex, dest_tex)
                                        unreal.log(f"Transferred texture: {tex_name}{ext} -> {godot_textures_dir}")
                except Exception as copy_err:
                    unreal.log_warning(f"Failed to auto-transfer level textures to Godot: {str(copy_err)}")

            # Copy terrain data (heightmaps / weightmaps written by the landscape exporter)
            try:
                local_terrain_dir = os.path.join(os.path.dirname(save_path), "terrain")
                if os.path.isdir(local_terrain_dir):
                    godot_terrain_dir = os.path.join(godot_project_dir, "terrain")
                    os.makedirs(godot_terrain_dir, exist_ok=True)
                    for terrain_file in os.listdir(local_terrain_dir):
                        src_t = os.path.join(local_terrain_dir, terrain_file)
                        dest_t = os.path.join(godot_terrain_dir, terrain_file)
                        if os.path.isfile(src_t) and not _same_path(src_t, dest_t):
                            shutil.copy2(src_t, dest_t)
                    unreal.log(f"Transferred terrain data -> {godot_terrain_dir}")
            except Exception as copy_err:
                unreal.log_warning(f"Failed to auto-transfer terrain data to Godot: {str(copy_err)}")

            # Copy exported audio (.wav) files
            try:
                local_audio_dir = os.path.join(os.path.dirname(save_path), "audio")
                if os.path.isdir(local_audio_dir):
                    godot_audio_dir = os.path.join(godot_project_dir, "audio")
                    os.makedirs(godot_audio_dir, exist_ok=True)
                    for audio_file in os.listdir(local_audio_dir):
                        src_a = os.path.join(local_audio_dir, audio_file)
                        dest_a = os.path.join(godot_audio_dir, audio_file)
                        if os.path.isfile(src_a) and not _same_path(src_a, dest_a):
                            shutil.copy2(src_a, dest_a)
                    unreal.log(f"Transferred audio data -> {godot_audio_dir}")
            except Exception as copy_err:
                unreal.log_warning(f"Failed to auto-transfer audio data to Godot: {str(copy_err)}")
        
        # Optionally generate a Godot .tscn scene directly inside the Godot project
        tscn_path = None
        tscn_project_dir = godot_project_dir or opts.get("tscn_godot_project_dir")
        if opts.get("write_tscn") and tscn_project_dir and os.path.isdir(tscn_project_dir):
            tscn_mod = _try_import("tscn_writer")
            if tscn_mod:
                try:
                    scene_name = ue2g_common.sanitize_name(opts.get("tscn_scene_name") or f"{world_name}_imported")
                    tscn_path = os.path.join(tscn_project_dir, f"{scene_name}.tscn")
                    res_paths = {
                        "models": "res://models/",
                        "textures": "res://textures/",
                        "terrain": "res://terrain/",
                        "audio": "res://audio/",
                    }
                    tscn_options = {
                        "scene_name": scene_name,
                        "godot_project_dir": tscn_project_dir,
                        "light_energy_scale": 1.0,
                        "lights": bool(opts.get("lights")),
                        "decals": bool(opts.get("decals")),
                        "reflection_probes": bool(opts.get("reflection_probes", True)),
                        "cameras": bool(opts.get("cameras", True)),
                        "audio": bool(opts.get("audio", True)),
                        "preserve_folders": True,
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
