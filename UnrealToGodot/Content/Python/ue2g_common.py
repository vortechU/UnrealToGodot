"""
Shared math and helper utilities for the Unreal to Godot exporter modules.

Coordinate conversion (see TECHNICAL_BRIEF.md and docs/SCHEMA_V2.md):
  Unreal: left-handed, Z-up, centimeters.  Godot: right-handed, Y-up, meters.
  Godot X = Unreal Y * 0.01, Godot Y = Unreal Z * 0.01, Godot Z = -Unreal X * 0.01
  Rotation basis remapped via C * R_unreal * C^T.

All feature exporter modules must use these helpers instead of re-implementing
the conversion, so every transform in the layout JSON stays consistent.
"""

import hashlib
import unreal

CM_TO_M = 0.01


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

    length = (qx**2 + qy**2 + qz**2 + qw**2) ** 0.5
    if length > 0.0:
        return (qx / length, qy / length, qz / length, qw / length)
    return (0.0, 0.0, 0.0, 1.0)


class SimpleTransform:
    """Lightweight stand-in for unreal.Transform to feed into unreal_to_godot_transform."""
    def __init__(self, translation, rotation, scale3d=None):
        self.translation = translation
        self.rotation = rotation
        self.scale3d = scale3d if scale3d is not None else unreal.Vector(1.0, 1.0, 1.0)


def unreal_transform_to_dict(transform):
    """Converts an unreal.Transform to a simple python dict (Left-handed, Z-up, cm)."""
    t = transform.translation
    r = transform.rotation
    s = transform.scale3d

    rotator = transform.rotation.rotator()

    return {
        "translation": [t.x, t.y, t.z],
        "rotation_quat": [r.x, r.y, r.z, r.w],
        "rotation_euler": [rotator.roll, rotator.pitch, rotator.yaw],
        "scale": [s.x, s.y, s.z]
    }


def unreal_to_godot_transform(u_transform):
    """
    Converts an Unreal Transform (or SimpleTransform) to a Godot transform dict:
    { "translation": [x,y,z] (m), "rotation_quat": [x,y,z,w], "scale": [x,y,z] }
    """
    ux, uy, uz = u_transform.translation.x, u_transform.translation.y, u_transform.translation.z
    godot_translation = [uy * CM_TO_M, uz * CM_TO_M, -ux * CM_TO_M]

    usx, usy, usz = u_transform.scale3d.x, u_transform.scale3d.y, u_transform.scale3d.z
    godot_scale = [usy, usz, usx]

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

    # Remap basis for right-handed Y-up Godot (C * R * C^T)
    R_godot = [
        [r11, r12, -r10],
        [r21, r22, -r20],
        [-r01, -r02, r00]
    ]

    g_quat = matrix_to_quat(R_godot)

    return {
        "translation": godot_translation,
        "rotation_quat": list(g_quat),
        "scale": godot_scale
    }


def actor_godot_transform(actor):
    """Convenience: converts an actor's world transform to a Godot transform dict."""
    return unreal_to_godot_transform(actor.get_actor_transform())


def local_shape_to_godot_transform(translation_vec, rotation_quat):
    """
    Converts a local offset translation (unreal.Vector) and local rotation (unreal.Quat)
    into a Godot local transform dict.
    """
    mock = SimpleTransform(translation_vec, rotation_quat)
    return unreal_to_godot_transform(mock)


def godot_transform_basis(u_transform):
    """
    Returns the Godot-space transform of an Unreal transform as 12 floats:
    [bx.x, bx.y, bx.z, by.x, by.y, by.z, bz.x, bz.y, bz.z, o.x, o.y, o.z]
    (basis column X, column Y, column Z including scale, then origin in meters).
    Used for packed instance arrays (foliage / MultiMesh).
    """
    ux, uy, uz = u_transform.translation.x, u_transform.translation.y, u_transform.translation.z
    origin = [uy * CM_TO_M, uz * CM_TO_M, -ux * CM_TO_M]

    usx, usy, usz = u_transform.scale3d.x, u_transform.scale3d.y, u_transform.scale3d.z
    scale = [usy, usz, usx]

    u_quat = u_transform.rotation
    qx, qy, qz, qw = u_quat.x, u_quat.y, u_quat.z, u_quat.w

    r00 = 1.0 - 2.0 * (qy**2 + qz**2)
    r01 = 2.0 * (qx*qy - qw*qz)
    r02 = 2.0 * (qx*qz + qw*qy)
    r10 = 2.0 * (qx*qy + qw*qz)
    r11 = 1.0 - 2.0 * (qx**2 + qz**2)
    r12 = 2.0 * (qy*qz - qw*qx)
    r20 = 2.0 * (qx*qz - qw*qy)
    r21 = 2.0 * (qy*qz + qw*qx)
    r22 = 1.0 - 2.0 * (qx**2 + qy**2)

    # Remapped Godot rotation matrix rows
    g = [
        [r11, r12, -r10],
        [r21, r22, -r20],
        [-r01, -r02, r00]
    ]

    # Basis columns scaled by per-axis scale (column-major output)
    return [
        g[0][0] * scale[0], g[1][0] * scale[0], g[2][0] * scale[0],
        g[0][1] * scale[1], g[1][1] * scale[1], g[2][1] * scale[1],
        g[0][2] * scale[2], g[1][2] * scale[2], g[2][2] * scale[2],
        origin[0], origin[1], origin[2]
    ]


def vector_to_godot(v):
    """Converts an unreal.Vector position (cm) to a Godot [x, y, z] list in meters."""
    return [v.y * CM_TO_M, v.z * CM_TO_M, -v.x * CM_TO_M]


def linear_color_to_list(color, include_alpha=False):
    """Converts an unreal.LinearColor (or unreal.Color) to a [r, g, b(, a)] float list."""
    try:
        if isinstance(color, unreal.Color):
            color = unreal.LinearColor(color.r / 255.0, color.g / 255.0, color.b / 255.0, color.a / 255.0)
        if include_alpha:
            return [color.r, color.g, color.b, color.a]
        return [color.r, color.g, color.b]
    except Exception:
        return [1.0, 1.0, 1.0, 1.0] if include_alpha else [1.0, 1.0, 1.0]


def safe_get_prop(obj, prop_name, default=None):
    """Reads an editor property defensively; returns default when unavailable."""
    if obj is None:
        return default
    try:
        return obj.get_editor_property(prop_name)
    except Exception:
        return default


def iter_base_material_textures(material):
    """Yields (parameter_name, texture) for every texture a *base* Material references.

    UE 5.7 made unreal.Material's 'expressions' property protected: reading it from
    Python now raises ("Expressions ... is protected and cannot be read"), so the
    old expression-graph walk silently harvested nothing from base materials -- any
    texture referenced only by a base material (never overridden on a
    MaterialInstance) dropped out of both the texture export and the layout JSON.

    The supported replacement enumerates the material's texture *parameters* via
    MaterialEditingLibrary (get_texture_parameter_names +
    get_material_default_texture_parameter_value), which works on 5.7 and earlier
    and hands back clean parameter names to classify by. Verified on UE 5.7.4
    against /Game/ContainersHouseCH/Materials/M_Containers_Master.

    Textures wired through non-parameter TextureSample nodes are still picked up by
    the legacy expression walk on engines that allow it (pre-5.7); on 5.7 that read
    raises and is skipped. Master materials in asset packs expose their maps as
    parameters, which is the case that matters here.
    """
    if not material:
        return
    seen = set()

    # Primary path: texture parameters via MaterialEditingLibrary (UE 5.7+).
    mel = getattr(unreal, "MaterialEditingLibrary", None)
    if mel is not None and hasattr(mel, "get_texture_parameter_names"):
        try:
            names = mel.get_texture_parameter_names(material) or []
        except Exception as e:
            unreal.log_warning(
                "Could not read texture parameter names from base material %s: %s"
                % (safe_get_name(material), str(e))
            )
            names = []
        for pname in names:
            try:
                tex = mel.get_material_default_texture_parameter_value(material, pname)
            except Exception:
                tex = None
            if tex and isinstance(tex, unreal.Texture) and tex not in seen:
                seen.add(tex)
                yield str(pname), tex

    # Fallback: walk the expression graph directly. Raises on UE 5.7 (property is
    # protected) -- caught and skipped, since the MEL pass above already covered
    # the parameter textures on that version. On older engines this also catches
    # textures wired through plain (non-parameter) TextureSample nodes.
    try:
        expressions = material.get_editor_property("expressions")
    except Exception:
        expressions = None
    if expressions and hasattr(unreal, "MaterialExpressionTextureSample"):
        for expr in expressions:
            if not expr or not isinstance(expr, unreal.MaterialExpressionTextureSample):
                continue
            try:
                tex = expr.get_editor_property("texture")
            except Exception:
                tex = None
            if not tex or not isinstance(tex, unreal.Texture) or tex in seen:
                continue
            seen.add(tex)
            name = ""
            if (hasattr(unreal, "MaterialExpressionTextureSampleParameter2D")
                    and isinstance(expr, unreal.MaterialExpressionTextureSampleParameter2D)):
                try:
                    name = str(expr.get_editor_property("parameter_name"))
                except Exception:
                    name = ""
            if not name:
                try:
                    name = str(expr.get_name())
                except Exception:
                    name = ""
            yield name, tex


def safe_get_name(obj):
    """Reads an object's asset name defensively; never raises."""
    try:
        return obj.get_name()
    except Exception:
        return "<unknown>"


def short_path_hash(asset_path):
    """8-char stable hash of an asset path; used to disambiguate colliding mesh names."""
    return hashlib.md5(str(asset_path).encode("utf-8")).hexdigest()[:8]


def sanitize_name(name):
    """Makes an asset/actor name safe for use as a filename and Godot node name."""
    out = []
    for ch in str(name):
        out.append(ch if (ch.isalnum() or ch in ("_", "-", ".")) else "_")
    result = "".join(out).strip("._")
    return result if result else "Unnamed"


def build_export_name_map(meshes):
    """
    Returns {mesh_asset: export_name} for a batch of mesh assets.

    Unique names are kept as-is; when several different assets share a name
    (common in large asset packs), EVERY colliding asset gets a deterministic
    "<name>_<8-char-path-hash>" suffix so exported files never overwrite each
    other, regardless of iteration order.
    """
    groups = {}
    for mesh in meshes:
        if not mesh:
            continue
        try:
            name = sanitize_name(mesh.get_name())
        except Exception:
            continue
        groups.setdefault(name, []).append(mesh)

    result = {}
    for name, group in groups.items():
        if len(group) == 1:
            result[group[0]] = name
        else:
            for mesh in group:
                try:
                    result[mesh] = "%s_%s" % (name, short_path_hash(mesh.get_path_name()))
                except Exception:
                    result[mesh] = name
    return result
