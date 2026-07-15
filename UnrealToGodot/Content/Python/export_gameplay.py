"""
Unreal Engine Python Script
Exports navigation bounds volumes and gameplay metadata (actor tags, component
tags, and best-effort Blueprint variables) for the Godot importer.
See docs/SCHEMA_V2.md ("navigation" and actor tags/properties sections).

Blueprint variable extraction is best-effort: the UE Python wrapper reflects
Blueprint-added variables onto generated wrapper classes for Blueprint classes
(Python type names ending in "_C"). We enumerate candidate attribute names by
diffing dir() of the wrapper type against its closest base wrapper, then read
each candidate through get_editor_property, keeping only simple value types
(bool/int/float/str/Name/Text). On engine versions where Blueprint variables
are not reflected into the Python wrapper type, only tags are exported — this
limitation is inherent to the stock UE Python API.
"""

import unreal
import ue2g_common

# Schema defaults, used when no RecastNavMesh actor is found in the level
_DEFAULT_NAV_PARAMS = {
    "agent_radius_m": 0.35,
    "agent_height_m": 1.92,
    "max_slope_deg": 44.0,
    "agent_max_step_height_m": 0.35,
    "cell_size_m": 0.19,
}

_MAX_BP_PROPERTIES = 64


def collect_navigation(all_actors):
    """
    Returns the schema "navigation" dict, or None when the level contains no
    NavMeshBoundsVolume actors. Never raises, never shows dialogs.
    """
    bounds = []
    params = dict(_DEFAULT_NAV_PARAMS)

    try:
        vol_class = getattr(unreal, "NavMeshBoundsVolume", None)
        recast_class = getattr(unreal, "RecastNavMesh", None)
        recast_actor = None

        for actor in (all_actors or []):
            try:
                if actor is None:
                    continue
                if vol_class is not None and isinstance(actor, vol_class):
                    origin, extent = actor.get_actor_bounds(False)
                    # Brush scale is already folded into the world bounds, so the
                    # region transform must not re-apply it.
                    transform = ue2g_common.unreal_to_godot_transform(actor.get_actor_transform())
                    transform["scale"] = [1.0, 1.0, 1.0]
                    bounds.append({
                        "name": actor.get_actor_label(),
                        "godot_transform": transform,
                        "extent_m": [extent.y * 0.01, extent.z * 0.01, extent.x * 0.01],
                    })
                elif recast_actor is None and recast_class is not None and isinstance(actor, recast_class):
                    recast_actor = actor
            except Exception as e:
                unreal.log_warning(f"export_gameplay: failed to read nav actor: {str(e)}")

        if not bounds:
            return None

        if recast_actor is not None:
            radius = ue2g_common.safe_get_prop(recast_actor, "agent_radius")
            height = ue2g_common.safe_get_prop(recast_actor, "agent_height")
            slope = ue2g_common.safe_get_prop(recast_actor, "agent_max_slope")
            step = ue2g_common.safe_get_prop(recast_actor, "agent_max_step_height")
            cell = ue2g_common.safe_get_prop(recast_actor, "cell_size")
            if radius:
                params["agent_radius_m"] = float(radius) * 0.01
            if height:
                params["agent_height_m"] = float(height) * 0.01
            if slope:
                params["max_slope_deg"] = float(slope)
            if step:
                params["agent_max_step_height_m"] = float(step) * 0.01
            if cell:
                params["cell_size_m"] = float(cell) * 0.01

        result = {"bounds_volumes": bounds}
        result.update(params)
        return result
    except Exception as e:
        unreal.log_warning(f"export_gameplay: collect_navigation failed: {str(e)}")
        return None


def extract_actor_metadata(actor):
    """
    Returns {"tags": [str], "properties": {name: bool|int|float|str}} for an actor.
    Never raises.
    """
    result = {"tags": [], "properties": {}}
    if actor is None:
        return result

    try:
        tags = actor.tags
        if tags:
            result["tags"] = [str(t) for t in tags if str(t)]
    except Exception:
        pass

    try:
        result["properties"] = _extract_bp_properties(actor)
    except Exception:
        pass

    return result


def extract_component_tags(component):
    """Returns the component's ComponentTags as a list of strings. Never raises."""
    try:
        tags = ue2g_common.safe_get_prop(component, "component_tags")
        if tags:
            return [str(t) for t in tags if str(t)]
    except Exception:
        pass
    return []


def _extract_bp_properties(actor):
    """Best-effort Blueprint variable reflection (see module docstring)."""
    props = {}
    py_type = type(actor)
    if not py_type.__name__.endswith("_C"):
        return props

    base = py_type.__mro__[1] if len(py_type.__mro__) > 1 else None
    base_attrs = set(dir(base)) if base is not None else set()
    candidates = [a for a in dir(py_type) if a not in base_attrs and not a.startswith("_")]

    for name in candidates[:_MAX_BP_PROPERTIES]:
        try:
            value = actor.get_editor_property(name)
        except Exception:
            continue
        coerced = _coerce_simple_value(value)
        if coerced is not None:
            props[name] = coerced
    return props


def _coerce_simple_value(value):
    """Keeps only JSON-friendly simple values; returns None for everything else."""
    if isinstance(value, bool):
        return value
    if isinstance(value, int):
        return int(value)
    if isinstance(value, float):
        return float(value)
    if isinstance(value, str):
        return value
    try:
        if isinstance(value, (unreal.Name, unreal.Text)):
            text = str(value)
            return text if text and text != "None" else None
    except Exception:
        pass
    return None
