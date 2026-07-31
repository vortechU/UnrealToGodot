"""
Unreal Engine Python Script
Exports Lights, Post-Process Volumes, Height Fog, Sky Light, Sky Atmosphere
presence, and Decal actors into the SCHEMA_V2.md "lights", "post_process",
"height_fog", "sky_light", "has_sky_atmosphere" and "decals" structures.

Entry point: collect_environment(all_actors, collected_textures) -> dict
See docs/SCHEMA_V2.md ("lights" / "post_process" / "height_fog" / "sky_light" /
"has_sky_atmosphere" / "decals" sections and the "Module contracts" table) for
the authoritative JSON layout this module must produce.

This module is exception-safe end-to-end: every actor is processed inside its
own try/except (logged via unreal.log_warning on failure) and the whole entry
point is wrapped again so a totally unexpected failure still returns the
(possibly partially filled) result dict rather than raising or bringing up an
editor dialog.

--------------------------------------------------------------------------
godot_energy heuristic (see docs/SCHEMA_V2.md "lights" section)
--------------------------------------------------------------------------
Unreal light intensities are expressed in very different units depending on
light type: directional lights are always authored in lux (illuminance),
while local lights (point/spot/rect) can be authored in any of
unreal.LightUnits {Unitless, Candelas, Lumens, EV} (ULocalLightComponent's
IntensityUnits). Godot's Light3D.light_energy is a small unitless multiplier
with no fixed physical scale -- its default of "1.0" is calibrated so Godot's
own default lights look reasonable.

To land Unreal-authored lights near their Godot-default-equivalent brightness
without per-light manual tuning, a fixed linear scale is applied per unit
type (further tunable at import time via options.light_energy_scale):

    lux (directional)   -> intensity / 10.0
    lumens (point/spot)  -> intensity / 1700.0 * 8.0
    candela               -> intensity / 100.0
    unitless               -> intensity / 8.0
    ev / unknown            -> treated as unitless (intensity / 8.0). EV is a
                               logarithmic photographic exposure value with no
                               documented canonical intensity range for scene
                               lights, and SCHEMA_V2.md does not specify a
                               dedicated EV formula, so falling back to the
                               unitless curve keeps godot_energy in a sane,
                               tunable range instead of leaving it undefined.

These constants match the ones documented in docs/SCHEMA_V2.md verbatim. The
raw `intensity` + `intensity_units` are always stored too, so the value can
be re-derived or hand-tuned on the Godot side.

--------------------------------------------------------------------------
Decal axis fix-up (see docs/SCHEMA_V2.md "decals" section)
--------------------------------------------------------------------------
UE decals project along their local **-X** axis. The shared coordinate
conversion (ue2g_common.unreal_to_godot_transform) maps UE local +X onto
Godot's local -Z (this is the fact that makes converted rotations usable
as-is for lights/cameras, whose forward axis is UE local +X / Godot local
-Z). By linearity, UE local -X therefore maps onto Godot local +Z under the
same conversion.

Godot's Decal node instead projects along its own local **-Y** axis. So the
plain converted rotation is not enough for decals: it points the "projection
direction" at the converted node's local +Z, not -Y. `_decal_transform` folds
in an extra fix-up quaternion (a -90 degree rotation about the local X axis)
so that after composition, the converted node's local -Y lines up with where
the decal's local +Z used to be. Because that fix-up re-labels the node's
local Y and Z axes, the converted **scale** has to be conjugated by it too --
its Y and Z components are swapped. See `_decal_transform`'s docstring for the
full worked derivation and a numeric sanity check.
"""

import unreal

import ue2g_common
from export_level_to_json import extract_material_parameters


# Optional/engine-version-dependent classes. Resolved once at import time
# (via getattr, which never raises) so that per-actor isinstance() checks
# never blow up on older Unreal versions that lack RectLight/SkyAtmosphere.
_RECT_LIGHT_CLASS = getattr(unreal, "RectLight", None)
_SKY_ATMOSPHERE_CLASS = getattr(unreal, "SkyAtmosphere", None)


# ---------------------------------------------------------------------------
# Small defensive helpers
# ---------------------------------------------------------------------------

def _safe_label(actor):
    """Best-effort human readable name for logging/naming; never raises."""
    try:
        return actor.get_actor_label()
    except Exception:
        try:
            return actor.get_name()
        except Exception:
            return "<unknown actor>"


def _f(value, default=0.0):
    """Defensive float coercion; None/failed conversions fall back to default."""
    if value is None:
        return default
    try:
        return float(value)
    except Exception:
        return default


def _b(value, default=False):
    """Defensive bool coercion; None/failed conversions fall back to default."""
    if value is None:
        return default
    try:
        return bool(value)
    except Exception:
        return default


def _log_actor_warning(actor, context, err):
    unreal.log_warning(
        "export_environment: {} on '{}': {}".format(context, _safe_label(actor), err)
    )


# ---------------------------------------------------------------------------
# Lights
# ---------------------------------------------------------------------------

def _light_type_of(actor):
    """
    Classifies a level actor into one of the 4 supported light types, or None
    if it isn't a light actor. Order matters: ARectLight and ASpotLight both
    derive from APointLight in the Unreal class hierarchy, so the more
    specific subclasses must be checked before the generic PointLight check.
    """
    if _RECT_LIGHT_CLASS is not None and isinstance(actor, _RECT_LIGHT_CLASS):
        return "rect"
    if isinstance(actor, unreal.SpotLight):
        return "spot"
    if isinstance(actor, unreal.PointLight):
        return "point"
    if isinstance(actor, unreal.DirectionalLight):
        return "directional"
    return None


def _map_intensity_units(enum_val):
    """Maps an unreal.LightUnits enum value to the schema's unit string."""
    if enum_val is None:
        return "unknown"
    try:
        name = enum_val.name
    except Exception:
        name = str(enum_val)
    name = name.upper()
    if "CANDELA" in name:
        return "candela"
    if "LUMEN" in name:
        return "lumens"
    if "EV" in name:
        return "ev"
    if "UNITLESS" in name:
        return "unitless"
    return "unknown"


def _compute_godot_energy(intensity, units):
    """godot_energy heuristic; see the module docstring for the rationale."""
    intensity = _f(intensity, 0.0)
    if units == "lux":
        return intensity / 10.0
    if units == "lumens":
        return intensity / 1700.0 * 8.0
    if units == "candela":
        return intensity / 100.0
    # "unitless", "ev" and "unknown" all fall back to the same curve.
    return intensity / 8.0


def _build_light_entry(actor, light_type):
    try:
        light_comp = actor.get_component_by_class(unreal.LightComponent)
    except Exception as e:
        _log_actor_warning(actor, "failed to get LightComponent", e)
        light_comp = None

    intensity = _f(ue2g_common.safe_get_prop(light_comp, "intensity", None), 0.0)

    color_raw = ue2g_common.safe_get_prop(light_comp, "light_color", None)
    color = ue2g_common.linear_color_to_list(color_raw) if color_raw is not None else [1.0, 1.0, 1.0]

    attenuation_radius_m = None
    source_radius_m = None
    inner_cone_angle_deg = None
    outer_cone_angle_deg = None

    if light_type == "directional":
        # Directional lights have no IntensityUnits property; UE always
        # authors/interprets their intensity as lux (illuminance).
        intensity_units = "lux"
    else:
        radius = ue2g_common.safe_get_prop(light_comp, "attenuation_radius", None)
        if radius is not None:
            attenuation_radius_m = _f(radius) * ue2g_common.CM_TO_M

        src_radius = ue2g_common.safe_get_prop(light_comp, "source_radius", None)
        if src_radius is not None:
            source_radius_m = _f(src_radius) * ue2g_common.CM_TO_M

        intensity_units = _map_intensity_units(
            ue2g_common.safe_get_prop(light_comp, "intensity_units", None)
        )

        if light_type == "spot":
            inner = ue2g_common.safe_get_prop(light_comp, "inner_cone_angle", None)
            outer = ue2g_common.safe_get_prop(light_comp, "outer_cone_angle", None)
            if inner is not None:
                inner_cone_angle_deg = _f(inner)
            if outer is not None:
                outer_cone_angle_deg = _f(outer)

    temperature = ue2g_common.safe_get_prop(light_comp, "temperature", None)

    return {
        "name": _safe_label(actor),
        "type": light_type,
        "godot_transform": ue2g_common.unreal_to_godot_transform(actor.get_actor_transform()),
        "color": color,
        "intensity": intensity,
        "intensity_units": intensity_units,
        "godot_energy": _compute_godot_energy(intensity, intensity_units),
        "temperature_kelvin": _f(temperature) if temperature is not None else None,
        "use_temperature": _b(ue2g_common.safe_get_prop(light_comp, "use_temperature", False), False),
        "cast_shadows": _b(ue2g_common.safe_get_prop(light_comp, "cast_shadows", True), True),
        "attenuation_radius_m": attenuation_radius_m,
        "source_radius_m": source_radius_m,
        "inner_cone_angle_deg": inner_cone_angle_deg,
        "outer_cone_angle_deg": outer_cone_angle_deg,
        "indirect_intensity": _f(
            ue2g_common.safe_get_prop(light_comp, "indirect_lighting_intensity", None), 1.0
        ),
        "visible": _b(ue2g_common.safe_get_prop(light_comp, "visible", True), True),
    }


# ---------------------------------------------------------------------------
# Post Process Volume
# ---------------------------------------------------------------------------

def _pp_field(settings, override_prop, value_prop, transform=None):
    """
    Reads a single PostProcessSettings field, but only if its matching
    override_* flag is True (per SCHEMA_V2.md: "Only settings whose
    override_* flag is set in Unreal are non-null."). Returns None otherwise.
    """
    if settings is None:
        return None
    override_flag = ue2g_common.safe_get_prop(settings, override_prop, False)
    if not override_flag:
        return None
    value = ue2g_common.safe_get_prop(settings, value_prop, None)
    if value is None:
        return None
    if transform is not None:
        try:
            return transform(value)
        except Exception:
            return None
    return _f(value, None)


def _vec4_to_list(v):
    """Converts an unreal.Vector4 (or similarly-shaped) value to [x,y,z,w]."""
    try:
        return [float(v.x), float(v.y), float(v.z), float(v.w)]
    except Exception:
        try:
            return [float(v.r), float(v.g), float(v.b), float(v.a)]
        except Exception:
            return None


def _map_exposure_method(value):
    """Maps unreal.AutoExposureMethod (Histogram/Basic/Manual) to schema strings."""
    try:
        name = value.name
    except Exception:
        name = str(value)
    name = name.upper()
    if "MANUAL" in name:
        return "manual"
    return "auto"


def _size_to_godot(v):
    """
    Converts an Unreal extent/size vector (cm, always non-negative
    half-extents) to Godot meters using the same axis reassignment as
    translations (Godot_x=UE_y, Godot_y=UE_z, Godot_z=UE_x) but WITHOUT the
    sign flip used for directional translations, since an extent has no
    direction to reverse.
    """
    return [
        _f(v.y) * ue2g_common.CM_TO_M,
        _f(v.z) * ue2g_common.CM_TO_M,
        _f(v.x) * ue2g_common.CM_TO_M,
    ]


def _build_post_process_entry(actor):
    settings = ue2g_common.safe_get_prop(actor, "settings", None)
    unbound = ue2g_common.safe_get_prop(actor, "unbound", True)
    priority = ue2g_common.safe_get_prop(actor, "priority", 0.0)

    extent_m = [0.0, 0.0, 0.0]
    try:
        bounds = actor.get_actor_bounds(False)
        box_extent = bounds[1]
        extent_m = _size_to_godot(box_extent)
    except Exception as e:
        _log_actor_warning(actor, "could not read actor bounds for PostProcessVolume", e)

    settings_out = {
        "bloom_intensity": _pp_field(settings, "override_bloom_intensity", "bloom_intensity"),
        "bloom_threshold": _pp_field(settings, "override_bloom_threshold", "bloom_threshold"),
        "ao_intensity": _pp_field(settings, "override_ambient_occlusion_intensity", "ambient_occlusion_intensity"),
        "ao_radius": _pp_field(settings, "override_ambient_occlusion_radius", "ambient_occlusion_radius"),
        "exposure_bias": _pp_field(settings, "override_auto_exposure_bias", "auto_exposure_bias"),
        "exposure_method": _pp_field(
            settings, "override_auto_exposure_method", "auto_exposure_method", _map_exposure_method
        ),
        # The auto-exposure adaptation range, in UE's cd/m^2. Shipping only the
        # BIAS was not enough: a volume that locks exposure by setting
        # min_brightness == max_brightness (the standard "manual exposure"
        # idiom, and the single most visually dominant setting a grade can
        # carry) overrides NEITHER the bias nor the method, so the whole grade
        # exported as null and Godot fell back to a linear tonemapper.
        "exposure_min_brightness": _pp_field(
            settings, "override_auto_exposure_min_brightness", "auto_exposure_min_brightness"),
        "exposure_max_brightness": _pp_field(
            settings, "override_auto_exposure_max_brightness", "auto_exposure_max_brightness"),
        "exposure_speed_up": _pp_field(
            settings, "override_auto_exposure_speed_up", "auto_exposure_speed_up"),
        "exposure_speed_down": _pp_field(
            settings, "override_auto_exposure_speed_down", "auto_exposure_speed_down"),
        "white_temp": _pp_field(settings, "override_white_temp", "white_temp"),
        "saturation": _pp_field(settings, "override_color_saturation", "color_saturation", _vec4_to_list),
        "contrast": _pp_field(settings, "override_color_contrast", "color_contrast", _vec4_to_list),
        "vignette_intensity": _pp_field(settings, "override_vignette_intensity", "vignette_intensity"),
    }

    return {
        "name": _safe_label(actor),
        "unbound": _b(unbound, True),
        "priority": _f(priority, 0.0),
        "godot_transform": ue2g_common.actor_godot_transform(actor),
        "extent_m": extent_m,
        "settings": settings_out,
    }


# ---------------------------------------------------------------------------
# Height Fog / Sky Light
# ---------------------------------------------------------------------------

def _build_height_fog(actor):
    try:
        comp = actor.get_component_by_class(unreal.ExponentialHeightFogComponent)
    except Exception as e:
        _log_actor_warning(actor, "failed to get ExponentialHeightFogComponent", e)
        comp = None
    if comp is None:
        return None

    density = ue2g_common.safe_get_prop(comp, "fog_density", None)
    falloff = ue2g_common.safe_get_prop(comp, "fog_height_falloff", None)
    color_raw = ue2g_common.safe_get_prop(comp, "fog_inscattering_color", None)
    color = ue2g_common.linear_color_to_list(color_raw) if color_raw is not None else [1.0, 1.0, 1.0]
    start_distance = ue2g_common.safe_get_prop(comp, "start_distance", None)

    return {
        "fog_density": _f(density, 0.02),
        "fog_height_falloff": _f(falloff, 0.2),
        "color": color,
        "start_distance_m": _f(start_distance, 0.0) * ue2g_common.CM_TO_M,
    }


def _build_sky_light(actor):
    try:
        comp = actor.get_component_by_class(unreal.SkyLightComponent)
    except Exception as e:
        _log_actor_warning(actor, "failed to get SkyLightComponent", e)
        comp = None
    if comp is None:
        return None

    intensity = ue2g_common.safe_get_prop(comp, "intensity", None)
    color_raw = ue2g_common.safe_get_prop(comp, "light_color", None)
    color = ue2g_common.linear_color_to_list(color_raw) if color_raw is not None else [1.0, 1.0, 1.0]

    return {
        "intensity": _f(intensity, 1.0),
        "color": color,
    }


# ---------------------------------------------------------------------------
# Decals
# ---------------------------------------------------------------------------

# UE decals project along local -X; the shared conversion (ue2g_common.
# unreal_to_godot_transform) maps UE local +X onto Godot local -Z, so it maps
# UE local -X onto Godot local +Z. Godot's Decal instead projects along local
# -Y. _DECAL_FIXUP_QUAT is a -90 degree rotation about the local X axis; see
# _decal_transform() for the full derivation and a worked numeric example.
_DECAL_FIXUP_QUAT = (-0.7071067811865476, 0.0, 0.0, 0.7071067811865476)


def _quat_multiply(q1, q2):
    """
    Hamilton product q1 * q2 for (x, y, z, w) tuples: rotates by q2 first,
    then by q1. Matches the matrix composition convention R(q1 * q2) =
    R(q1) . R(q2) used throughout ue2g_common.py's matrix_to_quat().
    """
    x1, y1, z1, w1 = q1
    x2, y2, z2, w2 = q2
    return (
        w1 * x2 + x1 * w2 + y1 * z2 - z1 * y2,
        w1 * y2 - x1 * z2 + y1 * w2 + z1 * x2,
        w1 * z2 + x1 * y2 - y1 * x2 + z1 * w2,
        w1 * w2 - x1 * x2 - y1 * y2 - z1 * z2,
    )


def _decal_transform(actor):
    """
    Returns the Godot transform dict for a decal actor, with the UE -> Godot
    projection-axis fix-up folded into the rotation.

    Worked derivation:
      Let A = the actor's world rotation matrix (Unreal space) and
      R_std = C . A . C^T the standard converted rotation returned by
      ue2g_common.unreal_to_godot_transform (C is the fixed UE-world ->
      Godot-world axis change with C . (ux,uy,uz) = (uy,uz,-ux)).

      SCHEMA_V2.md notes that C maps UE local +X onto Godot's local -Z
      (i.e. C . (1,0,0) = (0,0,-1)), which is exactly why the converted
      rotation can be used as-is for lights/cameras (UE forward = local
      +X, Godot forward = local -Z).

      UE decals instead project along local **-X**. Since C . (1,0,0) =
      (0,0,-1), linearity gives C . (-1,0,0) = (0,0,1). Working through
      R_std . v0 = C . (A . (-e_x)) the same way the light/camera case is
      derived shows the decal's real projection direction lands on
      R_std's own local **+Z** axis, not -Z.

      Godot's Decal projects along its local **-Y** axis. We want an
      extra local rotation R_fix, applied before R_std so that
      R_final = R_std . R_fix satisfies:
          R_final . (0,-1,0) = R_std . (0,0,1)
      which reduces to:
          R_fix . (0,-1,0) = (0,0,1)   i.e.   R_fix . (0,1,0) = (0,0,-1)
      A -90 degree rotation about the local X axis satisfies exactly
      this (Rx(-90) maps +Y -> -Z), which is _DECAL_FIXUP_QUAT below.

      Numeric sanity check (A = Identity, so R_std = Identity too):
      an unrotated UE decal projects along local -X = world -X. Its
      Godot-space direction is C . (-1,0,0) = (0,0,1), i.e. Godot world
      +Z. With R_final = R_fix (since R_std = I here), the Decal node's
      local -Y axis maps to R_fix . (0,-1,0) = (0,0,1) by construction --
      also Godot world +Z. The two agree, confirming the fix-up.

      The scale must travel with the rotation. unreal_to_godot_transform
      returns the scale expressed along R_std's axes, as [usy, usz, usx].
      The correct world basis is R_std . S_std . R_fix, and consumers build
      it as R_final . S = (R_std . R_fix) . S, so:
          S = R_fix^T . S_std . R_fix
      Conjugating a diagonal by Rx(-90) just swaps its Y and Z entries, so
      the decal's scale is [usy, usx, usz]. Leaving it as [usy, usz, usx]
      swaps the decal's projection depth with its height -- a graffiti decal
      1 m tall with a 1.9 m projection depth renders 3.7 m tall and 0.5 m
      deep instead (measured on L_Overview's MI_Graffiti_01).
    """
    std = ue2g_common.unreal_to_godot_transform(actor.get_actor_transform())
    q_std = tuple(std.get("rotation_quat", [0.0, 0.0, 0.0, 1.0]))
    q_final = _quat_multiply(q_std, _DECAL_FIXUP_QUAT)

    length = sum(c * c for c in q_final) ** 0.5
    if length > 0.0:
        q_final = tuple(c / length for c in q_final)
    else:
        q_final = (0.0, 0.0, 0.0, 1.0)

    scale = list(std.get("scale", [1.0, 1.0, 1.0]))
    if len(scale) >= 3:
        # R_fix^T . diag(sx, sy, sz) . R_fix  ==  diag(sx, sz, sy)
        scale[1], scale[2] = scale[2], scale[1]
        std["scale"] = scale

    std["rotation_quat"] = list(q_final)
    return std


# Godot's Decal.TEXTURE_ORM channel order. Matches _PACKED_LAYOUTS["orm"]/["arm"]
# in export_level_to_json; any other packing (RMA, MRA) cannot be bound to a Decal.
_GODOT_DECAL_ORM_CHANNELS = {"ao": 0, "roughness": 1, "metallic": 2}


def _build_decal_entry(actor, collected_textures):
    try:
        comp = actor.get_component_by_class(unreal.DecalComponent)
    except Exception as e:
        _log_actor_warning(actor, "failed to get DecalComponent", e)
        comp = None
    if comp is None:
        return None

    decal_size = ue2g_common.safe_get_prop(comp, "decal_size", None)
    if decal_size is not None:
        # UE decal_size is HALF-size cm as (X=projection depth, Y=width, Z=height).
        # Godot Decal.size is FULL-size meters as (x=width, y=projection depth, z=height).
        size_m = [
            _f(decal_size.y) * 2.0 * ue2g_common.CM_TO_M,
            _f(decal_size.x) * 2.0 * ue2g_common.CM_TO_M,
            _f(decal_size.z) * 2.0 * ue2g_common.CM_TO_M,
        ]
    else:
        size_m = [1.0, 1.0, 1.0]

    sort_order = int(_f(ue2g_common.safe_get_prop(comp, "sort_order", 0), 0))

    material = ue2g_common.safe_get_prop(comp, "decal_material", None)
    material_name = "None"
    material_path = "None"
    textures = {"albedo": None, "normal": None, "orm": None, "emission": None,
                "texture_paths": {}}

    if material is not None:
        try:
            material_name = material.get_name()
            material_path = material.get_path_name()
        except Exception as e:
            _log_actor_warning(actor, "could not read decal material identity", e)

        try:
            params = extract_material_parameters(material, collected_textures)
            if params:
                paths = params.get("texture_paths") or {}
                textures["albedo"] = params.get("albedo_texture")
                textures["normal"] = params.get("normal_texture")
                for key, slot in (("albedo", "albedo_texture"), ("normal", "normal_texture")):
                    if paths.get(slot):
                        textures["texture_paths"][key] = paths[slot]

                # Godot's Decal.TEXTURE_ORM slot is hard-wired to R=AO,
                # G=roughness, B=metallic and exposes no per-channel selectors
                # (unlike BaseMaterial3D), so ONLY a map already in that order
                # can be bound. A standalone greyscale roughness map has the
                # roughness value in all three channels, which would read back
                # as AO=roughness (backwards -- rougher gets less occlusion) and
                # metallic=roughness (a 0.9-rough concrete decal renders as
                # chrome). Binding nothing is strictly better than that.
                # extract_material_parameters has no emissive extraction path at
                # all, so "emission" is deliberately left null.
                packed = params.get("packed_texture")
                if packed and params.get("packed_channels") == _GODOT_DECAL_ORM_CHANNELS:
                    textures["orm"] = packed
                    if paths.get("packed_texture"):
                        textures["texture_paths"]["orm"] = paths["packed_texture"]
                elif packed:
                    _log_actor_warning(
                        actor, "decal ORM map skipped",
                        "'{}' is packed as {} but Godot decals require ORM order "
                        "(R=AO, G=roughness, B=metallic)".format(
                            packed, params.get("packed_channels")))
        except Exception as e:
            _log_actor_warning(actor, "failed to extract decal material parameters", e)

    return {
        "name": _safe_label(actor),
        "godot_transform": _decal_transform(actor),
        "size_m": size_m,
        "sort_order": sort_order,
        "material_name": material_name,
        "material_path": material_path,
        "textures": textures,
    }


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def collect_environment(all_actors, collected_textures):
    """
    Scans all_actors and returns:
        {"lights": [...], "post_process": [...], "height_fog": {...} | None,
         "sky_light": {...} | None, "has_sky_atmosphere": bool, "decals": [...]}

    Never raises and never shows dialogs: failures are logged via
    unreal.log_warning and the affected actor/feature is simply skipped.
    """
    result = {
        "lights": [],
        "post_process": [],
        "height_fog": None,
        "sky_light": None,
        "has_sky_atmosphere": False,
        "decals": [],
    }

    try:
        if collected_textures is None:
            collected_textures = set()

        for actor in (all_actors or []):
            try:
                if actor is None:
                    continue

                light_type = _light_type_of(actor)
                if light_type is not None:
                    entry = _build_light_entry(actor, light_type)
                    if entry is not None:
                        result["lights"].append(entry)
                    continue

                if isinstance(actor, unreal.PostProcessVolume):
                    entry = _build_post_process_entry(actor)
                    if entry is not None:
                        result["post_process"].append(entry)
                    continue

                if isinstance(actor, unreal.ExponentialHeightFog):
                    fog = _build_height_fog(actor)
                    if fog is not None:
                        result["height_fog"] = fog
                    continue

                if isinstance(actor, unreal.SkyLight):
                    sky = _build_sky_light(actor)
                    if sky is not None:
                        result["sky_light"] = sky
                    continue

                if _SKY_ATMOSPHERE_CLASS is not None and isinstance(actor, _SKY_ATMOSPHERE_CLASS):
                    result["has_sky_atmosphere"] = True
                    continue

                if isinstance(actor, unreal.DecalActor):
                    entry = _build_decal_entry(actor, collected_textures)
                    if entry is not None:
                        result["decals"].append(entry)
                    continue

            except Exception as actor_err:
                _log_actor_warning(actor, "failed to process actor", actor_err)

    except Exception as outer_err:
        unreal.log_warning(
            "export_environment: collect_environment failed entirely: {}".format(outer_err)
        )

    return result
