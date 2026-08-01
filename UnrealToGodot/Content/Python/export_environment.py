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
unreal.LightUnits {Unitless, Candelas, Lumens, EV, Nits} (ULocalLightComponent's
IntensityUnits). Godot's Light3D.light_energy is a small unitless multiplier
with no fixed physical scale -- its default of "1.0" is calibrated so Godot's
own default lights look reasonable.

The conversion runs in two steps.

**Step 1 -- normalise to candelas.** Every local-light unit is folded into
luminous intensity using Unreal's *own* factors, read out of
ULocalLightComponent::GetUnitsConversionFactor and the per-component
ComputeLightBrightness overrides (UE 5.7 source), not invented here:

    candelas  -> intensity
    unitless  -> intensity * 16 / (100*100)      # UE's "legacy scale of 16"
    lumens    -> intensity / (2*pi*(1-cos(half_cone)))   # point: cos=-1 -> /4pi
                 rect lights use /pi (cosine distribution over the panel)
    ev        -> 2**intensity                    # EV100ToLuminance, 1 m^2 implied
    nits      -> intensity * emissive_area_m2    # capsule area; rect: w*h

Doing this first is the whole point: before it, the same physical light
imported up to 625x brighter or 12x dimmer purely depending on which unit the
artist happened to author it in, because each unit had its own unrelated
divisor. They now agree with each other by construction.

**Step 2 -- anchor to Godot's defaults.** A single scale maps candelas to
light_energy, chosen so an untouched Unreal light lands on an untouched Godot
light (further tunable at import time via options.light_energy_scale):

    local (point/spot/rect) -> candelas / 8.0
    lux (directional)       -> intensity / 10.0

Both anchors are the engine defaults: UE 5.7 ships local lights at 5000
unitless, which is exactly 8 cd, and directional lights at 10 lux; Godot ships
every Light3D at light_energy 1.0. This is a calibration, not a photometric
identity -- two tonemapped renderers cannot be matched by a constant -- but it
makes the common case land right instead of 625x hot, which is what the old
"unitless -> intensity / 8.0" curve did to every default-authored UE light.

The raw `intensity` + `intensity_units` are always stored too, alongside the
normalised `intensity_candelas`, so the value can be re-derived or hand-tuned
on the Godot side.

--------------------------------------------------------------------------
Lights are collected per component
--------------------------------------------------------------------------
Like decals (below), lights are gathered by scanning every actor for
LightComponents rather than by matching actor classes. Keying off
unreal.PointLight and friends silently dropped every light living on a
Blueprint -- lamp props, ceiling fixtures, torch pickups -- which is where a
dressed level keeps most of them, and exported only the first light of any
actor carrying several. USkyLightComponent deliberately does not derive from
ULightComponent, so the sky light is not caught twice by this scan.

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

Beyond the transform, a decal carries its colour (`modulate` = DecalColor *
material tint * opacity), its visibility, and a distance fade converted from
UE's screen-size fade. Decals are collected per DecalComponent rather than per
DecalActor, so Blueprint props with decal components export too.
"""

import math

import unreal

import ue2g_common
from export_level_to_json import extract_material_parameters


# Optional/engine-version-dependent classes. Resolved once at import time
# (via getattr, which never raises) so that per-actor isinstance() checks
# never blow up on older Unreal versions that lack RectLight/SkyAtmosphere.
_RECT_LIGHT_COMPONENT = getattr(unreal, "RectLightComponent", None)
_SKY_ATMOSPHERE_CLASS = getattr(unreal, "SkyAtmosphere", None)

# Unreal's "legacy scale of 16" over its cm^2 -> m^2 factor: the exact figure
# ULocalLightComponent::GetUnitsConversionFactor uses for Unitless -> Candelas.
_UNITLESS_TO_CANDELA = 16.0 / (100.0 * 100.0)

# Anchors mapping Unreal's physical units onto Godot's unitless light_energy.
# Both are the engines' own defaults: UE ships local lights at 5000 unitless
# (= 8 cd) and directional lights at 10 lux, Godot ships Light3D at energy 1.0.
_CANDELA_PER_GODOT_ENERGY = 8.0
_LUX_PER_GODOT_ENERGY = 10.0


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

def _light_type_of(comp):
    """
    Classifies a LightComponent into one of the 4 supported light types, or
    None if it is not one of them. Order matters: USpotLightComponent derives
    from UPointLightComponent, so the more specific subclass must be checked
    first. (URectLightComponent does *not* derive from UPointLightComponent --
    verified against UE 5.7 -- but is checked first anyway so the ordering
    stays correct if that ever changes.)
    """
    if _RECT_LIGHT_COMPONENT is not None and isinstance(comp, _RECT_LIGHT_COMPONENT):
        return "rect"
    if isinstance(comp, unreal.SpotLightComponent):
        return "spot"
    if isinstance(comp, unreal.PointLightComponent):
        return "point"
    if isinstance(comp, unreal.DirectionalLightComponent):
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
    # UNITLESS is tested first on purpose: it contains "NIT", so a substring
    # test for nits would claim every default-authored light as a luminance.
    if "UNITLESS" in name:
        return "unitless"
    if "CANDELA" in name:
        return "candela"
    if "LUMEN" in name:
        return "lumens"
    if "NIT" in name:
        return "nits"
    if "EV" in name:
        return "ev"
    return "unknown"


def _emissive_area_m2(comp, light_type):
    """
    The emitting surface area UE uses to turn nits (luminance) into candelas.

    Mirrors the ComputeLightBrightness overrides: a rect light emits from its
    source panel, a point/spot light from a capsule of SourceRadius and
    SourceLength. Both are authored in cm, so the cm^2 area is scaled to m^2.
    """
    if light_type == "rect":
        width = _f(ue2g_common.safe_get_prop(comp, "source_width", 0.0), 0.0)
        height = _f(ue2g_common.safe_get_prop(comp, "source_height", 0.0), 0.0)
        area_cm2 = width * height
    else:
        radius = _f(ue2g_common.safe_get_prop(comp, "source_radius", 0.0), 0.0)
        length = _f(ue2g_common.safe_get_prop(comp, "source_length", 0.0), 0.0)
        area_cm2 = 4.0 * math.pi * radius * (radius + 0.5 * length)
    return area_cm2 * ue2g_common.CM_TO_M * ue2g_common.CM_TO_M


def _intensity_in_candelas(comp, light_type, intensity, units, outer_cone_angle_deg):
    """
    Normalises a local light's authored intensity to candelas using Unreal's
    own unit factors. See the module docstring for the table and its source.
    """
    intensity = _f(intensity, 0.0)

    if units == "candela":
        return intensity
    if units == "ev":
        # EV is logarithmic, and legitimately negative for dim lights.
        try:
            return 2.0 ** intensity
        except Exception:
            return 0.0
    if units == "lumens":
        if light_type == "rect":
            return intensity / math.pi
        cos_half_cone = -1.0
        if light_type == "spot" and outer_cone_angle_deg is not None:
            # USpotLightComponent::GetCosHalfConeAngle clamps to 0..89.9 deg.
            cos_half_cone = math.cos(math.radians(
                min(max(_f(outer_cone_angle_deg, 44.0), 0.0), 89.9)))
        return intensity / (2.0 * math.pi * (1.0 - cos_half_cone))
    if units == "nits":
        return intensity * _emissive_area_m2(comp, light_type)

    # "unitless", "unknown", and every light with inverse-square falloff
    # switched off (where UE ignores IntensityUnits entirely) share UE's
    # legacy scale of 16.
    return intensity * _UNITLESS_TO_CANDELA


def _compute_godot_energy(intensity_candelas, lux=None):
    """godot_energy heuristic; see the module docstring for the rationale."""
    if lux is not None:
        return _f(lux, 0.0) / _LUX_PER_GODOT_ENERGY
    return _f(intensity_candelas, 0.0) / _CANDELA_PER_GODOT_ENERGY


def _light_distance_fade(comp):
    """
    Converts UE's MaxDrawDistance/MaxDistanceFadeRange (cm) into Godot
    (distance_fade_begin, distance_fade_length) metres, or (None, None) when
    the light is never culled. UE fades out over the last MaxDistanceFadeRange
    before MaxDrawDistance, which is exactly Godot's begin/length pair.
    """
    max_draw_cm = _f(ue2g_common.safe_get_prop(comp, "max_draw_distance", 0.0), 0.0)
    if max_draw_cm <= 0.0:
        return None, None
    fade_range_cm = _f(ue2g_common.safe_get_prop(comp, "max_distance_fade_range", 0.0), 0.0)

    end_m = max_draw_cm * ue2g_common.CM_TO_M
    length_m = max(0.01, fade_range_cm * ue2g_common.CM_TO_M)
    return max(0.0, end_m - length_m), length_m


def _light_mobility(comp):
    """'static' | 'stationary' | 'movable' | None -- diagnostic only."""
    mobility = ue2g_common.safe_get_prop(comp, "mobility", None)
    if mobility is None:
        return None
    try:
        name = mobility.name
    except Exception:
        name = str(mobility)
    name = name.upper()
    for candidate in ("STATIONARY", "STATIC", "MOVABLE"):
        if candidate in name:
            return candidate.lower()
    return None


def _light_components(actor):
    """Every LightComponent on an actor, in declaration order; never raises."""
    try:
        comps = actor.get_components_by_class(unreal.LightComponent)
    except Exception as e:
        _log_actor_warning(actor, "failed to list LightComponents", e)
        return []
    return [c for c in (comps or []) if c is not None]


def _build_light_entries(actor):
    """
    One schema entry per supported LightComponent on `actor`.

    Actors carrying more than one light get the component name appended so the
    Godot node names stay unique and traceable -- the same rule decals use.
    """
    comps = [c for c in _light_components(actor) if _light_type_of(c) is not None]
    label = _safe_label(actor)
    entries = []
    for comp in comps:
        name = label
        if len(comps) > 1:
            name = "{}_{}".format(label, ue2g_common.safe_get_name(comp))
        try:
            entry = _build_light_entry(actor, comp, name)
        except Exception as e:
            _log_actor_warning(actor, "failed to build light entry", e)
            continue
        if entry is not None:
            entries.append(entry)
    return entries


def _build_light_entry(actor, light_comp, name):
    light_type = _light_type_of(light_comp)
    if light_type is None:
        return None

    intensity = _f(ue2g_common.safe_get_prop(light_comp, "intensity", None), 0.0)

    color_raw = ue2g_common.safe_get_prop(light_comp, "light_color", None)
    color = ue2g_common.linear_color_to_list(color_raw) if color_raw is not None else [1.0, 1.0, 1.0]

    attenuation_radius_m = None
    source_radius_m = None
    inner_cone_angle_deg = None
    outer_cone_angle_deg = None
    source_angle_deg = None
    shadow_distance_m = None
    rect_size_m = None
    intensity_candelas = None
    inverse_squared = True

    if light_type == "directional":
        # Directional lights have no IntensityUnits property; UE always
        # authors/interprets their intensity as lux (illuminance).
        intensity_units = "lux"
        godot_energy = _compute_godot_energy(None, lux=intensity)

        # The angular diameter of the sun disc: UE calls it LightSourceAngle,
        # Godot light_angular_distance, and both are degrees.
        angle = ue2g_common.safe_get_prop(light_comp, "light_source_angle", None)
        if angle is not None:
            source_angle_deg = _f(angle)

        shadow_cm = _f(ue2g_common.safe_get_prop(
            light_comp, "dynamic_shadow_distance_movable_light", 0.0), 0.0)
        if shadow_cm <= 0.0:
            shadow_cm = _f(ue2g_common.safe_get_prop(
                light_comp, "dynamic_shadow_distance_stationary_light", 0.0), 0.0)
        if shadow_cm > 0.0:
            shadow_distance_m = shadow_cm * ue2g_common.CM_TO_M
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

        if light_type == "rect":
            rect_size_m = [
                _f(ue2g_common.safe_get_prop(light_comp, "source_width", 0.0), 0.0)
                * ue2g_common.CM_TO_M,
                _f(ue2g_common.safe_get_prop(light_comp, "source_height", 0.0), 0.0)
                * ue2g_common.CM_TO_M,
            ]

        # With inverse-square falloff off, UE ignores IntensityUnits and reads
        # Intensity on its legacy scale, so the unit conversion must too.
        inverse_squared = _b(ue2g_common.safe_get_prop(
            light_comp, "use_inverse_squared_falloff", True), True)
        units_for_conversion = intensity_units if inverse_squared else "unitless"

        intensity_candelas = _intensity_in_candelas(
            light_comp, light_type, intensity, units_for_conversion, outer_cone_angle_deg)
        godot_energy = _compute_godot_energy(intensity_candelas)

        if intensity > 0.0 and intensity_candelas <= 0.0 and units_for_conversion == "nits":
            _log_actor_warning(
                actor, "light exports black",
                "'{}' is authored in nits but has no emissive area (source "
                "radius/width are 0), which Unreal also renders as no light"
                .format(name))

    temperature = ue2g_common.safe_get_prop(light_comp, "temperature", None)
    fade_begin, fade_length = _light_distance_fade(light_comp)

    # A light switched off in game, or excluded from the world entirely, is
    # content the level deliberately disabled; a Godot node that ignores that
    # arrives lit.
    visible = (_b(ue2g_common.safe_get_prop(light_comp, "visible", True), True)
               and not _b(ue2g_common.safe_get_prop(light_comp, "hidden_in_game", False), False)
               and _b(ue2g_common.safe_get_prop(light_comp, "affects_world", True), True))

    return {
        "name": name,
        "type": light_type,
        "godot_transform": ue2g_common.unreal_to_godot_transform(
            _component_world_transform(actor, light_comp)),
        "color": color,
        "intensity": intensity,
        "intensity_units": intensity_units,
        "intensity_candelas": intensity_candelas,
        "inverse_squared_falloff": inverse_squared,
        "godot_energy": godot_energy,
        "temperature_kelvin": _f(temperature) if temperature is not None else None,
        "use_temperature": _b(ue2g_common.safe_get_prop(light_comp, "use_temperature", False), False),
        "cast_shadows": _b(ue2g_common.safe_get_prop(light_comp, "cast_shadows", True), True),
        "attenuation_radius_m": attenuation_radius_m,
        "source_radius_m": source_radius_m,
        "source_angle_deg": source_angle_deg,
        "shadow_distance_m": shadow_distance_m,
        "rect_size_m": rect_size_m,
        "inner_cone_angle_deg": inner_cone_angle_deg,
        "outer_cone_angle_deg": outer_cone_angle_deg,
        "indirect_intensity": _f(
            ue2g_common.safe_get_prop(light_comp, "indirect_lighting_intensity", None), 1.0
        ),
        "specular_scale": _f(
            ue2g_common.safe_get_prop(light_comp, "specular_scale", None), 1.0
        ),
        "volumetric_scattering": _f(
            ue2g_common.safe_get_prop(light_comp, "volumetric_scattering_intensity", None), 1.0
        ),
        "distance_fade_begin_m": fade_begin,
        "distance_fade_length_m": fade_length,
        "mobility": _light_mobility(light_comp),
        "visible": visible,
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


def _decal_transform(u_transform):
    """
    Returns the Godot transform dict for a decal's WORLD transform, with the
    UE -> Godot projection-axis fix-up folded into the rotation.

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

    Takes the DecalComponent's WORLD transform, not the actor's: on a stock
    ADecalActor the component *is* the root and the two agree, but a Blueprint
    decal actor (or a decal component parented under a prop) carries a relative
    offset the actor transform knows nothing about.
    """
    std = ue2g_common.unreal_to_godot_transform(u_transform)
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

# UE fades a decal out once its projected size drops below FadeScreenSize (a
# fraction of the view's half-height; default 0.01). Godot has no screen-size
# fade, only a distance one, so the exporter converts: an object of world radius
# r covers a screen fraction of r / (d * tan(fov/2)), which inverts to the
# distance at which UE would have dropped the decal. The FOV has to be assumed --
# Godot's Camera3D default (75 degrees vertical) is the only sane reference
# point, and the result is a soft fade rather than a hard cull, so being a few
# degrees off just moves the fade slightly.
_GODOT_DEFAULT_FOV_DEG = 75.0
_TAN_HALF_FOV = math.tan(math.radians(_GODOT_DEFAULT_FOV_DEG * 0.5))

# A tiny FadeScreenSize inverts to an absurd distance ("never fades"). Past this
# the fade is emitted as null rather than writing 10^6 into every scene.
_MAX_FADE_DISTANCE_M = 100000.0

# Scalar parameter names taken to mean "the decal's overall opacity", folded into
# Godot's Decal.modulate alpha. Deliberately an EXACT-match whitelist rather than
# a substring test like classify_scalar_parameter uses: a false positive here
# makes a decal transparent, and names such as "Opacity Mask Contrast" or
# "Alpha Threshold" are contrast controls, not opacity.
_DECAL_OPACITY_PARAM_NAMES = frozenset((
    "opacity", "decalopacity", "globalopacity", "overallopacity",
    "opacityscale", "opacitymultiplier", "opacitystrength", "opacityamount",
    "alpha", "decalalpha",
))


def _is_opacity_param(name):
    lowered = str(name or "").lower().replace(" ", "").replace("_", "")
    return lowered in _DECAL_OPACITY_PARAM_NAMES


def _decal_opacity(material):
    """
    Returns an explicit overall-opacity scalar from a decal material, or None.

    Godot's Decal has no opacity property of its own, but modulate's alpha
    multiplies the whole projection, so an `Opacity` parameter -- the standard
    way decal material instances are dialled back in UE -- can be carried
    across. Walks the instance chain child-first, matching
    extract_material_parameters' "first explicit value wins" rule.
    """
    visited = set()

    def _scan(mat):
        if mat is None or mat in visited:
            return None
        visited.add(mat)

        is_instance = isinstance(mat, unreal.MaterialInstance)
        if not is_instance and hasattr(unreal, "MaterialInstanceConstant"):
            is_instance = isinstance(mat, unreal.MaterialInstanceConstant)

        if is_instance:
            try:
                for s in (mat.get_editor_property("scalar_parameter_values") or []):
                    if _is_opacity_param(s.parameter_info.name):
                        return float(s.parameter_value)
            except Exception:
                pass
            return _scan(ue2g_common.safe_get_prop(mat, "parent", None))

        for pname, value in ue2g_common.iter_base_material_scalars(mat):
            if _is_opacity_param(pname):
                try:
                    return float(value)
                except Exception:
                    return None
        return None

    try:
        return _scan(material)
    except Exception:
        return None


def _decal_modulate(comp, params, material):
    """
    Godot Decal.modulate [r, g, b, a]: the component's DecalColor times the
    material's albedo tint, with any opacity parameter folded into alpha.

    Without this a decal whose colour lives in a parameter rather than in its
    albedo texture -- the usual setup for tinted blood/rust/paint instances --
    imported at full white. Two caveats worth knowing:
      * UE's DecalColor only reaches the shader when the material samples the
        Decal Color node. It defaults to white, so it only ever moves the
        result when someone deliberately set it, and honouring an explicitly
        authored colour beats dropping it.
      * The tint can legitimately exceed 1.0 (packs author dark albedo and
        scale it up), so only the negative side is clamped.
    """
    modulate = [1.0, 1.0, 1.0, 1.0]

    decal_color = ue2g_common.safe_get_prop(comp, "decal_color", None)
    if decal_color is not None:
        for i, channel in enumerate(("r", "g", "b", "a")):
            modulate[i] = _f(getattr(decal_color, channel, 1.0), 1.0)

    tint = (params or {}).get("albedo_color")
    if isinstance(tint, (list, tuple)):
        for i in range(min(4, len(tint))):
            modulate[i] *= _f(tint[i], 1.0)

    opacity = _decal_opacity(material)
    if opacity is not None:
        modulate[3] *= min(max(opacity, 0.0), 1.0)

    return [max(0.0, c) for c in modulate]


def _decal_distance_fade(comp, size_m, scale):
    """
    Converts UE's FadeScreenSize into Godot (distance_fade_begin,
    distance_fade_length) in metres, or (None, None) when UE asked for no fade.

    `size_m` is the decal's local box and `scale` its converted local scale, so
    the lateral (width/height) world extent is size_m[0]*scale[0] and
    size_m[2]*scale[2] -- the projection depth (index 1) is not what determines
    on-screen size. The decal is fully gone at the distance UE would have
    dropped it, fading in over the last quarter of the way there.
    """
    fade_screen_size = _f(ue2g_common.safe_get_prop(comp, "fade_screen_size", 0.0), 0.0)
    if fade_screen_size <= 0.0:
        return None, None

    def _axis_scale(index):
        try:
            return abs(_f(scale[index], 1.0)) or 1.0
        except Exception:
            return 1.0

    radius = 0.5 * max(abs(_f(size_m[0], 1.0)) * _axis_scale(0),
                       abs(_f(size_m[2], 1.0)) * _axis_scale(2))
    if radius <= 0.0:
        return None, None

    cull_m = radius / (fade_screen_size * _TAN_HALF_FOV)
    if cull_m > _MAX_FADE_DISTANCE_M:
        return None, None

    length = max(1.0, cull_m * 0.25)
    return max(0.0, cull_m - length), length


def _decal_components(actor):
    """Every DecalComponent on an actor, in declaration order; never raises."""
    try:
        comps = actor.get_components_by_class(unreal.DecalComponent)
    except Exception as e:
        _log_actor_warning(actor, "failed to list DecalComponents", e)
        return []
    return [c for c in (comps or []) if c is not None]


def _build_decal_entries(actor, collected_textures):
    """
    One schema entry per DecalComponent on `actor`.

    Scanning components rather than keying off unreal.DecalActor is what lets a
    Blueprint prop with a decal component -- a scorched wall panel, a signed
    crate -- export at all; those used to vanish silently. Actors carrying more
    than one decal get the component name appended so the Godot node names stay
    unique and traceable.
    """
    comps = _decal_components(actor)
    label = _safe_label(actor)
    entries = []
    for comp in comps:
        name = label
        if len(comps) > 1:
            name = "{}_{}".format(label, ue2g_common.safe_get_name(comp))
        # Guarded per component: this runs before the light/volume dispatch in
        # collect_environment, so an unreadable decal must not cost the actor
        # its light export too.
        try:
            entry = _build_decal_entry(actor, comp, name, collected_textures)
        except Exception as e:
            _log_actor_warning(actor, "failed to build decal entry", e)
            continue
        if entry is not None:
            entries.append(entry)
    return entries


def _build_decal_entry(actor, comp, name, collected_textures):
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
    params = None

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

    godot_transform = _decal_transform(_component_world_transform(actor, comp))
    fade_begin, fade_length = _decal_distance_fade(
        comp, size_m, godot_transform.get("scale") or [1.0, 1.0, 1.0])

    # A decal component hidden in game is content the level explicitly turned
    # off; a Godot node that ignores that arrives visible.
    visible = (_b(ue2g_common.safe_get_prop(comp, "visible", True), True)
               and not _b(ue2g_common.safe_get_prop(comp, "hidden_in_game", False), False))

    return {
        "name": name,
        "godot_transform": godot_transform,
        "size_m": size_m,
        "sort_order": sort_order,
        "visible": visible,
        "modulate": _decal_modulate(comp, params, material),
        "fade_screen_size": _f(ue2g_common.safe_get_prop(comp, "fade_screen_size", 0.0), 0.0),
        "distance_fade_begin_m": fade_begin,
        "distance_fade_length_m": fade_length,
        "material_name": material_name,
        "material_path": material_path,
        "textures": textures,
    }


def _component_world_transform(actor, comp):
    """
    A scene component's world transform, falling back to the actor's.

    The actor transform is only the same thing when the component *is* the
    root -- true for a plain DecalActor or PointLight, false for every decal
    or light hanging off a Blueprint, which is exactly the content that
    component-level collection exists to pick up.
    """
    try:
        transform = comp.get_world_transform()
        if transform is not None:
            return transform
    except Exception as e:
        _log_actor_warning(actor, "could not read component world transform", e)
    return actor.get_actor_transform()


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

                # Decals and lights first and unconditionally: both are
                # components, not actor classes, so keying off
                # unreal.DecalActor / unreal.PointLight dropped every one of
                # them riding on a Blueprint prop -- and every branch below
                # continues, which would have kept skipping them.
                result["decals"].extend(_build_decal_entries(actor, collected_textures))
                result["lights"].extend(_build_light_entries(actor))

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

            except Exception as actor_err:
                _log_actor_warning(actor, "failed to process actor", actor_err)

    except Exception as outer_err:
        unreal.log_warning(
            "export_environment: collect_environment failed entirely: {}".format(outer_err)
        )

    return result
