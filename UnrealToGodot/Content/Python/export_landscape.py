"""
Unreal Engine Python Script
Exports Unreal Landscape terrains: heightmaps and paint-layer weightmaps are
rendered into float render targets and written to <json_dir>/terrain/ as EXR
files. See docs/SCHEMA_V2.md ("landscapes" section).

Height encoding contract ("normalized"): the exact values that
landscape_export_heightmap_to_render_target writes vary across engine versions
and internal packing, so the Godot importer treats the heightmap as RELATIVE —
it normalizes the loaded image by its own min/max and rescales the result into
height_range_m, which this exporter derives from the landscape actor's world
bounds. That makes the pipeline robust to encoding differences.

Image axis mapping (consumed by the Godot importer):
    image U (+X of the render target) follows Unreal +X
    image V (+Y of the render target) follows Unreal +Y
The importer reconstructs vertices in Unreal world space from ue_bounds and
converts each to Godot space, so orientation is correct by construction.
"""

import os
import unreal
import ue2g_common

MAX_RT_RESOLUTION = 4033   # matches the largest recommended UE landscape size
MIN_RT_RESOLUTION = 64
DEFAULT_RT_RESOLUTION = 1009


def collect_landscapes(all_actors, json_dir, options=None):
    """
    Returns a list of schema landscape entries; writes terrain image files under
    <json_dir>/terrain/. Never raises, never shows dialogs.
    """
    results = []
    proxy_class = getattr(unreal, "LandscapeProxy", None)
    if proxy_class is None:
        return results
    parent_class = getattr(unreal, "Landscape", None)
    streaming_class = getattr(unreal, "LandscapeStreamingProxy", None)

    candidates = []
    has_parent = False
    for actor in (all_actors or []):
        try:
            if actor is not None and isinstance(actor, proxy_class):
                candidates.append(actor)
                if parent_class is not None and isinstance(actor, parent_class):
                    has_parent = True
        except Exception:
            continue
    if not candidates:
        return results

    # When a parent Landscape exists, streaming proxies are covered by exporting
    # the parent with in_export_landscape_proxies=True — drop them to avoid
    # duplicate terrain files.
    if has_parent and streaming_class is not None:
        candidates = [a for a in candidates if not isinstance(a, streaming_class)]

    world = _get_editor_world()
    if world is None:
        unreal.log_warning("export_landscape: could not resolve the editor world; skipping terrain export.")
        return results

    terrain_dir = os.path.join(json_dir, "terrain")
    try:
        os.makedirs(terrain_dir, exist_ok=True)
    except Exception as e:
        unreal.log_warning(f"export_landscape: cannot create terrain directory: {str(e)}")
        return results

    for landscape in candidates:
        try:
            entry = _export_one_landscape(world, landscape, terrain_dir)
            if entry:
                results.append(entry)
        except Exception as e:
            try:
                name = landscape.get_actor_label()
            except Exception:
                name = "<unknown>"
            unreal.log_warning(f"export_landscape: failed to export landscape '{name}': {str(e)}")

    return results


def _export_one_landscape(world, landscape, terrain_dir):
    label = ue2g_common.sanitize_name(landscape.get_actor_label())
    origin, extent = landscape.get_actor_bounds(False)
    width, height = _estimate_resolution(landscape, extent)

    # 1. Heightmap
    height_file = f"{label}_height.exr"
    rt = _create_render_target(world, width, height)
    if rt is None:
        return None
    exported = False
    try:
        landscape.landscape_export_heightmap_to_render_target(rt, False, True)
        exported = _export_render_target(world, rt, terrain_dir, height_file)
    except TypeError:
        # Older signature without the proxies flag
        try:
            landscape.landscape_export_heightmap_to_render_target(rt, False)
            exported = _export_render_target(world, rt, terrain_dir, height_file)
        except Exception as e:
            unreal.log_warning(f"export_landscape: heightmap export failed for '{label}': {str(e)}")
    except Exception as e:
        unreal.log_warning(f"export_landscape: heightmap export failed for '{label}': {str(e)}")
    if not exported:
        return None
    unreal.log(f"export_landscape: exported heightmap {width}x{height} for '{label}'")

    # 2. Weightmaps per paint layer
    layers = []
    for layer_name in _discover_layer_names(landscape):
        try:
            wrt = _create_render_target(world, width, height)
            if wrt is None:
                continue
            landscape.landscape_export_weightmap_to_render_target(wrt, layer_name)
            weight_file = f"{label}_weight_{ue2g_common.sanitize_name(layer_name)}.exr"
            if _export_render_target(world, wrt, terrain_dir, weight_file):
                layers.append({
                    "name": str(layer_name),
                    "weightmap_file": f"terrain/{weight_file}",
                })
        except Exception as e:
            unreal.log_warning(f"export_landscape: weightmap '{layer_name}' failed for '{label}': {str(e)}")

    # 3. Bounds-derived placement data (see module docstring for the contract)
    transform = ue2g_common.unreal_to_godot_transform(landscape.get_actor_transform())
    return {
        "name": label,
        "godot_transform": transform,
        "heightmap_file": f"terrain/{height_file}",
        "heightmap_resolution": [width, height],
        # Godot footprint: X follows UE Y, Z follows UE X
        "world_size_m": [extent.y * 2.0 * 0.01, extent.x * 2.0 * 0.01],
        "world_center_m": ue2g_common.vector_to_godot(origin),
        "height_range_m": [(origin.z - extent.z) * 0.01, (origin.z + extent.z) * 0.01],
        "height_encoding": "normalized",
        "ue_bounds": {
            "center": [origin.x, origin.y, origin.z],
            "extent": [extent.x, extent.y, extent.z],
        },
        "layers": layers,
    }


def _get_editor_world():
    try:
        subsystem = unreal.get_editor_subsystem(unreal.UnrealEditorSubsystem)
        if subsystem:
            return subsystem.get_editor_world()
    except Exception:
        pass
    try:
        return unreal.EditorLevelLibrary.get_editor_world()
    except Exception:
        return None


def _estimate_resolution(landscape, extent):
    """One texel per landscape quad: quads = world size / per-quad scale."""
    try:
        scale = landscape.get_actor_scale3d()
        sx = abs(scale.x) if abs(scale.x) > 0.001 else 100.0
        sy = abs(scale.y) if abs(scale.y) > 0.001 else 100.0
        w = int(round((extent.x * 2.0) / sx)) + 1
        h = int(round((extent.y * 2.0) / sy)) + 1
        w = max(MIN_RT_RESOLUTION, min(MAX_RT_RESOLUTION, w))
        h = max(MIN_RT_RESOLUTION, min(MAX_RT_RESOLUTION, h))
        return w, h
    except Exception:
        return DEFAULT_RT_RESOLUTION, DEFAULT_RT_RESOLUTION


def _create_render_target(world, width, height):
    """Creates a float render target (float formats export as EXR)."""
    formats = []
    for fmt_name in ("RTF_R32F", "RTF_RGBA32F", "RTF_RGBA16F"):
        fmt = getattr(unreal.TextureRenderTargetFormat, fmt_name, None)
        if fmt is not None:
            formats.append(fmt)
    for fmt in formats:
        try:
            rt = unreal.RenderingLibrary.create_render_target2d(world, width, height, fmt)
            if rt:
                return rt
        except Exception:
            continue
    unreal.log_warning("export_landscape: could not create a float render target.")
    return None


def _export_render_target(world, rt, directory, filename):
    """Writes the render target to disk (EXR for float formats); verifies the file."""
    try:
        unreal.RenderingLibrary.export_render_target(world, rt, directory, filename)
    except Exception as e:
        unreal.log_warning(f"export_landscape: export_render_target failed for {filename}: {str(e)}")
        return False
    return os.path.exists(os.path.join(directory, filename))


def _discover_layer_names(landscape):
    """
    Discovers paint layer names across UE version differences: tries the
    editor layer settings first, then the landscape material's layer names.
    """
    names = []

    def _add(value):
        text = str(value)
        if text and text.lower() != "none" and text not in names:
            names.append(text)

    # UE4/UE5: editor_layer_settings -> LandscapeEditorLayerSettings.layer_info_obj
    try:
        settings = landscape.get_editor_property("editor_layer_settings")
        if settings:
            for setting in settings:
                info = None
                try:
                    info = setting.get_editor_property("layer_info_obj")
                except Exception:
                    pass
                if info:
                    try:
                        _add(info.get_editor_property("layer_name"))
                        continue
                    except Exception:
                        pass
                    try:
                        _add(info.get_name())
                    except Exception:
                        pass
    except Exception:
        pass

    # UE5.3+: target_layers map keyed by layer name
    if not names:
        try:
            target_layers = landscape.get_editor_property("target_layers")
            if target_layers:
                for key in target_layers:
                    _add(key)
        except Exception:
            pass

    # Fallback: layer names referenced by the landscape material
    if not names:
        try:
            material = landscape.get_editor_property("landscape_material")
            if material and hasattr(unreal, "MaterialEditingLibrary"):
                # Editor-only utility; wrapped because it may not exist at runtime
                layer_names = unreal.MaterialEditingLibrary.get_material_expression_landscape_layer_names(material)
                for layer in (layer_names or []):
                    _add(layer)
        except Exception:
            pass

    return names
