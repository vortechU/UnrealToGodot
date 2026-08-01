"""
Shared math and helper utilities for the Unreal to Godot exporter modules.

Coordinate conversion (see TECHNICAL_BRIEF.md and docs/SCHEMA_V2.md):
  Unreal: left-handed, Z-up, centimeters.  Godot: right-handed, Y-up, meters.

  TWO conventions live here, and mixing them up misplaces geometry:
  * Level layout (actor/component placement) -- unreal_to_godot_transform:
      Godot X = Unreal Y, Godot Y = Unreal Z, Godot Z = -Unreal X   (x 0.01)
      Rotation basis remapped via C * R_unreal * C^T.
  * Collision shapes -- gltf_local_shape_transform / gltf_collision_axis_swap:
      Godot X = Unreal X, Godot Y = Unreal Z, Godot Z = Unreal Y     (x 0.01)
    Collision rides UNDER the glTF mesh (StaticBody at the layout-converted
    world transform, mesh at identity beneath it), so shapes must use the axis
    convention Unreal's glTF exporter bakes into the mesh, NOT the layout one --
    otherwise they land rotated 90 deg about vertical and displaced.

All feature exporter modules must use these helpers instead of re-implementing
the conversion, so every transform in the layout JSON stays consistent.
"""

import hashlib
import os

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


def gltf_local_shape_transform(translation_vec, rotation_quat):
    """Converts a collision shape's mesh-local offset (unreal.Vector, cm) and
    rotation (unreal.Quat) into a Godot local transform dict, using the axis
    convention Unreal's glTF exporter bakes into the mesh geometry:

        Godot X = Unreal X,  Godot Y = Unreal Z,  Godot Z = Unreal Y   (x 0.01)

    This is DELIBERATELY different from unreal_to_godot_transform (the level-layout
    convention, Godot = (Y, Z, -X)). Collision shapes ride under a StaticBody that
    sits at the component's layout-converted world transform, with the glTF mesh
    parented at identity beneath it. The mesh vertices therefore live in
    glTF-local space, so a shape converted with the layout convention lands
    rotated 90 deg about the vertical axis and displaced relative to the mesh it
    is meant to hug (see gltf_collision_axis_swap for the geometry side). Using
    the glTF convention here keeps collision and mesh locked together.

    Rotation is remapped by conjugation with G (G = G^-1 = G^T, so Conj_G(R)
    swaps rows 1<->2 and columns 1<->2 of R).
    """
    tx = translation_vec.x
    ty = translation_vec.y
    tz = translation_vec.z
    godot_translation = [tx * CM_TO_M, tz * CM_TO_M, ty * CM_TO_M]

    qx, qy, qz, qw = rotation_quat.x, rotation_quat.y, rotation_quat.z, rotation_quat.w
    r00 = 1.0 - 2.0 * (qy**2 + qz**2)
    r01 = 2.0 * (qx*qy - qw*qz)
    r02 = 2.0 * (qx*qz + qw*qy)
    r10 = 2.0 * (qx*qy + qw*qz)
    r11 = 1.0 - 2.0 * (qx**2 + qz**2)
    r12 = 2.0 * (qy*qz - qw*qx)
    r20 = 2.0 * (qx*qz - qw*qy)
    r21 = 2.0 * (qy*qz + qw*qx)
    r22 = 1.0 - 2.0 * (qx**2 + qy**2)

    # Conj_G(R): swap rows 1<->2 and columns 1<->2.
    R_godot = [
        [r00, r02, r01],
        [r20, r22, r21],
        [r10, r12, r11],
    ]

    g_quat = matrix_to_quat(R_godot)

    return {
        "translation": godot_translation,
        "rotation_quat": list(g_quat),
        "scale": [1.0, 1.0, 1.0],
    }


def gltf_collision_axis_swap(xyz):
    """Reorders an Unreal-axis (x, y, z) triple into the glTF/Godot axis order
    used by exported mesh geometry: (x, z, y). Callers apply their own cm->m
    scale. Used for box extents and convex hull vertices so the collision
    geometry matches gltf_local_shape_transform's frame."""
    x = xyz[0] if len(xyz) > 0 else 0.0
    y = xyz[1] if len(xyz) > 1 else 0.0
    z = xyz[2] if len(xyz) > 2 else 0.0
    return [x, z, y]


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


def srgb_to_linear(channel):
    """
    sRGB EOTF, matching FLinearColor::sRGBToLinearTable entry for entry.

    UE stores some colours -- notably every light's LightColor -- as an FColor,
    which is sRGB-encoded bytes. Every time the engine renders one it goes
    through FLinearColor(FColor), i.e. this curve. Dividing by 255 and calling
    it linear (what this module used to do) leaves a tinted light far too pale:
    an orange FColor(255, 128, 50) renders as 1.0/0.216/0.030 in Unreal but
    arrived in Godot as 1.0/0.502/0.196.
    """
    c = max(0.0, min(1.0, channel))
    if c <= 0.04045:
        return c / 12.92
    return ((c + 0.055) / 1.055) ** 2.4


def linear_color_to_list(color, include_alpha=False):
    """
    Converts an unreal.LinearColor (or unreal.Color) to a [r, g, b(, a)] float list.

    SCHEMA_V2.md guarantees linear colours, so an sRGB-encoded FColor is
    decoded on the way through. Alpha is a plain 0..255 ratio in both engines
    and is never gamma-encoded.
    """
    try:
        if isinstance(color, unreal.Color):
            color = unreal.LinearColor(srgb_to_linear(color.r / 255.0),
                                       srgb_to_linear(color.g / 255.0),
                                       srgb_to_linear(color.b / 255.0),
                                       color.a / 255.0)
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


def iter_base_material_textures(material, include_dependencies=True):
    """Yields (parameter_name, texture) for every texture a *base* Material references.

    include_dependencies enables the AssetRegistry dependency crawl described at the
    bottom of this function. Callers pass False when the material was reached as the
    PARENT of a MaterialInstance rather than being assigned to the mesh directly: a
    master material's dependencies include the textures of every instance-facing
    default, and the instance has usually overridden them, so crawling there exports
    PNGs nothing samples. A directly assigned base material has no such indirection.

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

    # Last resort: the material's package dependencies from the AssetRegistry.
    #
    # Both passes above find NOTHING for a base material that wires its maps
    # through plain TextureSample nodes instead of texture PARAMETERS -- it has no
    # parameters to enumerate, and on 5.7 the expression graph cannot be read. That
    # is not a rare shape: hand-authored per-prop materials in marketplace packs
    # look exactly like this (TreatmentStation's M_Cardboard_2, M_Air_Compressor_1,
    # ...), and every mesh using one arrived in Godot untextured white while the
    # MI_-based meshes beside it came through fine.
    #
    # A material's package dependencies include every texture its graph samples, no
    # matter how it is wired, and the AssetRegistry answers without compiling the
    # material (MaterialEditingLibrary.get_used_textures does not -- it returns an
    # empty array in the pythonscript commandlet). The cost is that dependencies are
    # not roles: there is no parameter name to classify by, so "" is yielded and the
    # caller falls back to classifying by the TEXTURE's own name (T_Foo_B / _N /
    # _ORM), which is exactly what resolve_texture_role's second candidate is for.
    #
    # Only runs when the material yielded nothing at all, so it can never displace a
    # properly named parameter -- it fills a slot that was otherwise empty.
    if seen or not include_dependencies:
        return
    for tex in iter_material_dependency_textures(material):
        if tex not in seen:
            seen.add(tex)
            yield "", tex


def iter_material_dependency_textures(material):
    """Yields every Texture the AssetRegistry lists as a dependency of `material`.

    Package-level, so it sees textures wired through non-parameter nodes that the
    Python material API cannot reach. Non-recursive by design: one hop finds the
    material's own samplers without dragging in a whole MaterialFunction library.
    """
    try:
        registry = unreal.AssetRegistryHelpers.get_asset_registry()
    except Exception:
        return
    # get_dependencies takes a PACKAGE name -- "/Game/Foo/M_Bar", not the object
    # path "/Game/Foo/M_Bar.M_Bar" that get_path_name() hands back.
    try:
        package = material.get_path_name().split(".")[0]
    except Exception:
        return
    try:
        options = unreal.AssetRegistryDependencyOptions()
    except Exception:
        return
    try:
        dependencies = registry.get_dependencies(unreal.Name(package), options) or []
    except Exception as e:
        unreal.log_warning("Could not read dependencies of material %s: %s"
                           % (safe_get_name(material), str(e)))
        return
    for dep in dependencies:
        dep = str(dep)
        # Skip engine content and anything obviously not a texture package before
        # paying to load it.
        if not dep.startswith("/Game/"):
            continue
        try:
            asset = unreal.EditorAssetLibrary.load_asset(dep)
        except Exception:
            continue
        if asset and isinstance(asset, unreal.Texture):
            yield asset


def _iter_base_material_parameters(material, names_getter, value_getter):
    """Yields (parameter_name, default_value) for one base-material parameter kind.

    Both getters are MaterialEditingLibrary method names. Everything is guarded:
    an engine that does not expose them simply yields nothing, exactly as the
    texture walk above degrades.
    """
    if not material:
        return
    mel = getattr(unreal, "MaterialEditingLibrary", None)
    if mel is None or not hasattr(mel, names_getter) or not hasattr(mel, value_getter):
        return
    try:
        names = getattr(mel, names_getter)(material) or []
    except Exception as e:
        unreal.log_warning(
            "Could not read %s from base material %s: %s"
            % (names_getter, safe_get_name(material), str(e))
        )
        return
    for pname in names:
        try:
            value = getattr(mel, value_getter)(material, pname)
        except Exception:
            continue
        if value is not None:
            yield str(pname), value


def iter_base_material_scalars(material):
    """Yields (parameter_name, float) for a *base* Material's scalar parameter defaults.

    A base material has no scalar_parameter_values array -- that only exists on a
    MaterialInstance, and holds its OVERRIDES. Without this, a mesh whose slot
    points straight at a base material exported with the schema defaults
    (roughness 0.5 / metallic 0.0) no matter what the material actually says.
    """
    return _iter_base_material_parameters(
        material, "get_scalar_parameter_names", "get_material_default_scalar_parameter_value")


def iter_base_material_vectors(material):
    """Yields (parameter_name, LinearColor) for a *base* Material's vector defaults."""
    return _iter_base_material_parameters(
        material, "get_vector_parameter_names", "get_material_default_vector_parameter_value")


def export_textures_to_png(textures, textures_dir, skip_existing=False, name_map=None):
    """Writes each texture's SOURCE art to <textures_dir>/<export name>.png.

    name_map is the {texture: export_name} dict from build_export_name_map. Pass
    it whenever the filenames end up referenced from somewhere else (a layout
    JSON, a .gltf uri): two different textures sharing an asset name would
    otherwise write to the same file, and every material naming that texture
    would bind whichever art was written last. Without a map the bare asset name
    is used, which is only safe for a throwaway one-off export.

    Returns {"exported": [names], "reused": [names], "unsupported": [names]}.
    Callers should report "unsupported" -- those materials arrive in Godot
    without that map, and ue2g_diagnose flags the dangling .gltf image URIs.

    NEVER assign AssetExportTask.exporter here. Forcing
    unreal.TextureExporterPNG() skips the engine's own compatibility check:
    RunAssetExportTask only verifies the exporter's SupportedClass, so a
    Texture2D whose SOURCE art is float (imported from .exr/.hdr) passes the
    class check and reaches UTextureExporterGeneric::ExportBinary, which does
    check(SupportsTexture(Texture)) -- an assert that takes the whole editor
    down with no catchable exception. Three .exr-sourced normal maps in the
    TreatmentStation pack (T_Concrete_2_N, T_Concrete_3_N, T_Damage_Concrete_N)
    killed every "Export Everything" run this way.

    Leaving the exporter unset makes the engine pick one via SupportsObject().
    For a supported texture that is TextureExporterPNG and the bytes are
    identical (verified byte-for-byte on UE 5.7.4); for an unsupported one it
    logs a warning and moves on to the next task instead of asserting.
    """
    result = {"exported": [], "reused": [], "unsupported": []}
    tasks = []

    for tex in textures:
        if tex is None:
            continue
        # Only 2D textures have PNG-writable source art. Cubemaps, render
        # targets, arrays and volume textures have no single-image form, so
        # drop them here rather than let the engine warn once per task.
        if not isinstance(tex, unreal.Texture2D):
            result["unsupported"].append(safe_get_name(tex))
            continue

        name = None
        if name_map:
            name = name_map.get(tex)
        if not name:
            name = sanitize_name(safe_get_name(tex))
        filename = os.path.join(textures_dir, "%s.png" % name)

        if skip_existing and os.path.exists(filename):
            result["reused"].append(name)
            continue

        task = unreal.AssetExportTask()
        task.object = tex
        task.filename = filename
        task.automated = True
        task.prompt = False
        task.replace_identical = True
        # task.exporter is deliberately left unset -- see the docstring.
        tasks.append((name, filename, task))

    if tasks:
        unreal.Exporter.run_asset_export_tasks([t[2] for t in tasks])
        # The batch keeps going past a failure, so the file on disk is the only
        # reliable per-texture verdict.
        for name, filename, _task in tasks:
            if os.path.isfile(filename):
                result["exported"].append(name)
            else:
                result["unsupported"].append(name)

    return result


def log_texture_export_result(result, textures_dir):
    """Logs the counts from export_textures_to_png, naming anything skipped."""
    if result["reused"]:
        unreal.log("Reusing %d texture(s) already exported to: %s"
                   % (len(result["reused"]), textures_dir))
    if result["exported"]:
        unreal.log("Exported %d texture(s) to: %s" % (len(result["exported"]), textures_dir))
    if result["unsupported"]:
        unreal.log_warning(
            "%d texture(s) could not be written as PNG and were skipped: %s. "
            "Unreal's PNG exporter only handles 8/16-bit source art, so textures "
            "imported from .exr/.hdr (and cubemaps) have no PNG form. Materials "
            "using them import into Godot without that map -- re-import the "
            "texture in Unreal from an 8/16-bit source to include it."
            % (len(result["unsupported"]), ", ".join(sorted(result["unsupported"])))
        )


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


def build_export_name_map(assets, kind="asset"):
    """
    Returns {asset: export_name} for a batch of assets (meshes or textures).

    Unique names are kept as-is; when several different assets share a name
    (common in large asset packs), EVERY colliding asset gets a deterministic
    "<name>_<8-char-path-hash>" suffix so exported files never overwrite each
    other, regardless of iteration order.

    kind only names the assets in the collision warning. That warning matters:
    a silent collision is invisible downstream -- every reference still resolves,
    just to the wrong art -- so the log is the only place it ever surfaces.
    """
    groups = {}
    for asset in assets:
        if not asset:
            continue
        try:
            name = sanitize_name(asset.get_name())
        except Exception:
            continue
        groups.setdefault(name, []).append(asset)

    result = {}
    collisions = []
    for name, group in groups.items():
        if len(group) == 1:
            result[group[0]] = name
        else:
            collisions.append(name)
            for asset in group:
                try:
                    result[asset] = "%s_%s" % (name, short_path_hash(asset.get_path_name()))
                except Exception:
                    result[asset] = name

    if collisions:
        unreal.log_warning(
            "%d %s name(s) are used by more than one asset and were exported with a "
            "path-hash suffix so they do not overwrite each other: %s"
            % (len(collisions), kind, ", ".join(sorted(collisions)))
        )
    return result
