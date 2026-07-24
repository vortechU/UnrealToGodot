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
import struct
import unreal
import ue2g_common

MAX_RT_RESOLUTION = 4033   # matches the largest recommended UE landscape size
MIN_RT_RESOLUTION = 64
DEFAULT_RT_RESOLUTION = 1009
TRACE_GRID_MAX = 1009      # cap for the CPU collision-tracing fallback (~30s)


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

    # When a parent Landscape exists, its streaming proxies are exported through
    # the parent — drop them from the candidate list to avoid duplicate terrain
    # files, but KEEP them around: on proxy-based landscapes the parent actor
    # itself has no components, so its own bounds are empty (or wrong) and the
    # collision-tracing fallback needs the proxies' collision components.
    proxy_children = []
    if has_parent and streaming_class is not None:
        proxy_children = [a for a in candidates if isinstance(a, streaming_class)]
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
            related = _related_proxies(landscape, proxy_children)
            entry = _export_one_landscape(world, landscape, related, terrain_dir)
            if entry:
                results.append(entry)
        except Exception as e:
            try:
                name = landscape.get_actor_label()
            except Exception:
                name = "<unknown>"
            unreal.log_warning(f"export_landscape: failed to export landscape '{name}': {str(e)}")

    return results


def _related_proxies(parent, proxy_children):
    """Streaming proxies belonging to a parent landscape, matched by landscape
    GUID when the property is readable; otherwise all proxies are assumed to
    belong to the (typically only) parent."""
    if not proxy_children:
        return []
    try:
        parent_guid = parent.get_editor_property("landscape_guid")
        related = []
        for p in proxy_children:
            try:
                if p.get_editor_property("landscape_guid") == parent_guid:
                    related.append(p)
            except Exception:
                related.append(p)
        if related:
            return related
    except Exception:
        pass
    return list(proxy_children)


def _union_actor_bounds(actors):
    """Union of the actors' world bounds, skipping empty ones. Returns
    (center, extent) unreal.Vectors or (None, None).

    The parent of a streaming-proxy landscape owns no components, so its own
    get_actor_bounds is empty headless and unreliable in the GUI (observed
    returning DOUBLE the real extent, which shipped a 4 km terrain entry for a
    2 km landscape). The proxies' union is ground truth."""
    mn = [1e30, 1e30, 1e30]
    mx = [-1e30, -1e30, -1e30]
    for a in actors:
        try:
            o, e = a.get_actor_bounds(False)
        except Exception:
            continue
        if e.x <= 1.0 and e.y <= 1.0:
            continue
        for i, (c, ext) in enumerate(((o.x, e.x), (o.y, e.y), (o.z, e.z))):
            mn[i] = min(mn[i], c - ext)
            mx[i] = max(mx[i], c + ext)
    if mn[0] > mx[0]:
        return None, None
    center = unreal.Vector((mn[0] + mx[0]) * 0.5, (mn[1] + mx[1]) * 0.5, (mn[2] + mx[2]) * 0.5)
    extent = unreal.Vector((mx[0] - mn[0]) * 0.5, (mx[1] - mn[1]) * 0.5, (mx[2] - mn[2]) * 0.5)
    return center, extent


def _export_one_landscape(world, landscape, related_proxies, terrain_dir):
    label = ue2g_common.sanitize_name(landscape.get_actor_label())
    origin, extent = _union_actor_bounds([landscape] + list(related_proxies))
    if origin is None:
        origin, extent = landscape.get_actor_bounds(False)
    width, height = _estimate_resolution(landscape, extent)

    # 1. Heightmap. Three sources, most capable first:
    #    a) render_heightmap (UE 5.3+) — fast, full-res, and returns an HONEST
    #       bool. It fails (False) on landscapes without edit layers.
    #    b) the legacy landscape_export_* call — returns nothing and, on
    #       no-edit-layers landscapes, silently leaves the render target
    #       cleared, so its result is readback-validated.
    #    c) collision tracing — slow CPU sampling of the landscape's collision
    #       heightfield. Works on every landscape, GPU or not.
    height_file = f"{label}_height.exr"
    exported = _gpu_heightmap_to_file(world, landscape, label, terrain_dir, height_file, width, height)
    if exported is None:
        traced = _trace_heightmap_cpu(landscape, related_proxies, origin, extent,
                                      terrain_dir, height_file, width, height)
        if traced is not None:
            exported, width, height = traced
    if not exported:
        unreal.log_warning(f"export_landscape: every heightmap source failed for '{label}'; landscape skipped.")
        return None
    height_file = exported
    unreal.log(f"export_landscape: exported heightmap {width}x{height} for '{label}' -> {height_file}")

    # 2. Weightmaps per paint layer
    layers = []
    for layer_name in _discover_layer_names(landscape):
        try:
            weight_file = f"{label}_weight_{ue2g_common.sanitize_name(layer_name)}.exr"
            actual_weight_file = _gpu_weightmap_to_file(world, landscape, layer_name,
                                                       terrain_dir, weight_file, width, height)
            if actual_weight_file:
                layers.append({
                    "name": str(layer_name),
                    "weightmap_file": f"terrain/{actual_weight_file}",
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


def _rt_readback_varies(world, rt, width, height):
    """True when a coarse pixel readback shows variation. A constant readback
    after a landscape draw means the draw never happened. Note the readback API
    returns LinearColor red (1,0,0,1) as its own failure sentinel — constant
    red therefore also (correctly) counts as 'no data'. If reading raises, the
    draw is given the benefit of the doubt."""
    first = None
    try:
        for iy in range(5):
            for ix in range(5):
                x = int((width - 1) * ix / 4)
                y = int((height - 1) * iy / 4)
                c = unreal.RenderingLibrary.read_render_target_raw_pixel(world, rt, x, y)
                v = (round(c.r, 5), round(c.g, 5), round(c.b, 5))
                if first is None:
                    first = v
                elif v != first:
                    return True
    except Exception:
        return True
    return False


def _gpu_heightmap_to_file(world, landscape, label, terrain_dir, height_file, width, height):
    """GPU heightmap render -> file. Returns the actual filename written, or
    None when the GPU path verifiably produced nothing."""
    rt = _create_render_target(world, width, height)
    if rt is None:
        return None
    drew = False
    if hasattr(landscape, "render_heightmap"):
        try:
            drew = bool(landscape.render_heightmap(landscape.get_actor_transform(), unreal.Box2D(), rt))
        except Exception as e:
            unreal.log_warning(f"export_landscape: render_heightmap raised for '{label}': {str(e)}")
        if not drew:
            unreal.log_warning(f"export_landscape: render_heightmap reported failure for '{label}' "
                               f"(landscape without edit layers?) -- falling back to collision tracing.")
    else:
        try:
            try:
                landscape.landscape_export_heightmap_to_render_target(rt, False, True)
            except TypeError:
                # Older signature without the proxies flag
                landscape.landscape_export_heightmap_to_render_target(rt, False)
            drew = True
        except Exception as e:
            unreal.log_warning(f"export_landscape: heightmap export failed for '{label}': {str(e)}")
        if drew and not _rt_readback_varies(world, rt, width, height):
            unreal.log_warning(f"export_landscape: legacy heightmap export left a constant render target "
                               f"for '{label}' -- falling back to collision tracing.")
            drew = False
    if not drew:
        return None
    return _export_render_target(world, rt, terrain_dir, height_file)


def _gpu_weightmap_to_file(world, landscape, layer_name, terrain_dir, weight_file, width, height):
    """Weightmap render -> file; returns the actual filename or None. There is
    no CPU fallback for weightmaps -- a constant-zero result is treated as an
    unpainted layer and skipped."""
    rt = _create_render_target(world, width, height)
    if rt is None:
        return None
    drew = False
    if hasattr(landscape, "render_weightmap"):
        try:
            drew = bool(landscape.render_weightmap(landscape.get_actor_transform(), unreal.Box2D(),
                                                   str(layer_name), rt))
        except Exception as e:
            unreal.log_warning(f"export_landscape: render_weightmap('{layer_name}') raised: {str(e)}")
    if not drew:
        try:
            landscape.landscape_export_weightmap_to_render_target(rt, layer_name)
            drew = _rt_readback_varies(world, rt, width, height)
        except Exception as e:
            unreal.log_warning(f"export_landscape: weightmap export failed for '{layer_name}': {str(e)}")
            return None
    if not drew:
        unreal.log(f"export_landscape: weightmap '{layer_name}' rendered no data (unpainted layer?); skipped.")
        return None
    return _export_render_target(world, rt, terrain_dir, weight_file)


def _trace_heightmap_cpu(landscape, related_proxies, center, extent, terrain_dir, height_file, width, height):
    """Samples the landscape height by line-tracing its collision heightfield,
    one trace per texel, and writes a float32 EXR. Slow (~30 s for a 1009 grid
    at ~30k traces/s) but immune to every GPU/edit-layer failure mode: the
    collision data is the same surface the player walks on.

    Returns (filename, width, height) or None."""
    comp_class = getattr(unreal, "LandscapeHeightfieldCollisionComponent", None)
    if comp_class is None or center is None:
        return None
    bins = []
    for actor in [landscape] + list(related_proxies):
        try:
            o, e = actor.get_actor_bounds(False)
            comps = list(actor.get_components_by_class(comp_class))
        except Exception:
            continue
        if not comps or (e.x <= 1.0 and e.y <= 1.0):
            continue
        for c in comps:
            bins.append((o.x - e.x, o.x + e.x, o.y - e.y, o.y + e.y, c))
    if not bins:
        unreal.log_warning("export_landscape: no landscape collision components to trace against.")
        return None

    w = min(width, TRACE_GRID_MAX)
    h = min(height, TRACE_GRID_MAX)
    top = center.z + extent.z + 10000.0
    bottom = center.z - extent.z - 10000.0
    Vector = unreal.Vector
    heights = [None] * (w * h)
    hits = 0
    misses = 0
    min_h = 1e30
    unreal.log(f"export_landscape: tracing a {w}x{h} height grid against {len(bins)} collision components "
               f"(CPU fallback; takes tens of seconds on large landscapes)...")
    for iy in range(h):
        wy = center.y - extent.y + (2.0 * extent.y) * iy / (h - 1)
        # Candidate components for this row (y-filter once per row, not per texel)
        row_bins = [(x0, x1, c) for (x0, x1, y0, y1, c) in bins if y0 <= wy <= y1]
        row = iy * w
        last = None
        for ix in range(w):
            wx = center.x - extent.x + (2.0 * extent.x) * ix / (w - 1)
            z = None
            start = Vector(wx, wy, top)
            end = Vector(wx, wy, bottom)
            for x0, x1, c in row_bins:
                if wx < x0 or wx > x1:
                    continue
                try:
                    res = c.line_trace_component(start, end, True, False)
                except Exception:
                    res = None
                if res is not None:
                    loc = res[0] if isinstance(res, tuple) else getattr(res, "location", None)
                    if loc is not None:
                        z = loc.z
                        break
            if z is None:
                misses += 1
                z = last  # holes/edges: carry the last height; leading gaps filled below
            else:
                hits += 1
                min_h = min(min_h, z)
                last = z
            heights[row + ix] = z
    if hits == 0:
        unreal.log_warning("export_landscape: collision tracing hit nothing; landscape has no usable collision.")
        return None
    for i, v in enumerate(heights):
        if v is None:
            heights[i] = min_h
    _write_exr_r32(os.path.join(terrain_dir, height_file), w, h, heights)
    unreal.log(f"export_landscape: traced heightmap {w}x{h} ({hits} hits, {misses} filled)")
    return height_file, w, h


def _write_exr_r32(path, width, height, values):
    """Writes a minimal single-channel float32 scanline EXR, uncompressed.

    Pure Python on purpose: the engine's own writers pick their format by
    render-target type (see _export_render_target), while this output is
    deterministic and NO_COMPRESSION is readable by every EXR consumer,
    including Godot's tinyexr."""
    def attr(name, type_name, data):
        return (name.encode("ascii") + b"\x00" + type_name.encode("ascii") + b"\x00"
                + struct.pack("<i", len(data)) + data)

    # chlist: channel "R", pixel type FLOAT(2), pLinear+reserved, x/y sampling
    chlist = b"R\x00" + struct.pack("<i", 2) + b"\x00\x00\x00\x00" + struct.pack("<ii", 1, 1) + b"\x00"
    box = struct.pack("<4i", 0, 0, width - 1, height - 1)
    header = (attr("channels", "chlist", chlist)
              + attr("compression", "compression", b"\x00")          # NO_COMPRESSION
              + attr("dataWindow", "box2i", box)
              + attr("displayWindow", "box2i", box)
              + attr("lineOrder", "lineOrder", b"\x00")              # INCREASING_Y
              + attr("pixelAspectRatio", "float", struct.pack("<f", 1.0))
              + attr("screenWindowCenter", "v2f", struct.pack("<2f", 0.0, 0.0))
              + attr("screenWindowWidth", "float", struct.pack("<f", 1.0))
              + b"\x00")
    magic = struct.pack("<I", 20000630) + struct.pack("<I", 2)
    data_start = len(magic) + len(header) + 8 * height
    scanline_size = 8 + width * 4
    with open(path, "wb") as f:
        f.write(magic)
        f.write(header)
        for y in range(height):
            f.write(struct.pack("<Q", data_start + y * scanline_size))
        for y in range(height):
            f.write(struct.pack("<ii", y, width * 4))
            f.write(struct.pack("<%df" % width, *values[y * width:(y + 1) * width]))


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


# File-format magic numbers: what the engine ACTUALLY wrote, regardless of the
# filename it was asked to write.
_MAGIC_FORMATS = (
    (b"\x89PNG", "png"),
    (b"\x76\x2f\x31\x01", "exr"),
    (b"\xff\xd8\xff", "jpg"),
    (b"DDS ", "dds"),
    (b"BM", "bmp"),
)


def _sniff_format(path):
    try:
        with open(path, "rb") as f:
            head = f.read(8)
    except Exception:
        return None
    for magic, name in _MAGIC_FORMATS:
        if head.startswith(magic):
            return name
    return None


def _export_exr_via_image_write(rt, full_path):
    """Attempts a synchronous float-EXR write via ImageWriteBlueprintLibrary.

    ExportRenderTarget cannot store every float render-target format as EXR and
    silently writes PNG bytes instead; ImageWriteBlueprintLibrary can, but lives
    in an optional plugin. Property names are probed one at a time (they shift
    across engine versions). Returns True only when the file on disk verifiably
    contains EXR data.
    """
    lib = getattr(unreal, "ImageWriteBlueprintLibrary", None)
    options_cls = getattr(unreal, "ImageWriteOptions", None)
    format_cls = getattr(unreal, "DesiredImageFormat", None)
    exr_format = getattr(format_cls, "EXR", None) if format_cls else None
    if lib is None or options_cls is None or exr_format is None:
        return False
    try:
        options = options_cls()
    except Exception:
        return False
    try:
        options.set_editor_property("format", exr_format)
    except Exception:
        return False
    # bAsync=False makes the write block until the file is on disk.
    for prop, value in (("async_", False), ("async", False), ("overwrite_file", True)):
        try:
            options.set_editor_property(prop, value)
        except Exception:
            pass
    try:
        if os.path.exists(full_path):
            os.remove(full_path)
        lib.export_to_disk(rt, full_path, options)
    except Exception as e:
        unreal.log_warning(f"export_landscape: export_to_disk EXR failed for {os.path.basename(full_path)}: {str(e)}")
        return False
    return os.path.exists(full_path) and _sniff_format(full_path) == "exr"


def _rename_to_actual_format(directory, filename):
    """Renames a written file so its extension matches its actual content.

    Godot (like most tools) picks image decoders by extension, so a PNG named
    .exr fails to load there. The Godot importer sniffs content too, but a
    truthful name also keeps Godot's own texture import from erroring."""
    full_path = os.path.join(directory, filename)
    actual = _sniff_format(full_path)
    base, ext = os.path.splitext(filename)
    if actual is None or ext.lower().lstrip(".") == actual:
        return filename
    corrected = base + "." + actual
    try:
        corrected_path = os.path.join(directory, corrected)
        if os.path.exists(corrected_path):
            os.remove(corrected_path)
        os.rename(full_path, corrected_path)
    except Exception:
        return filename  # mislabeled, but the Godot importer sniffs content
    unreal.log_warning(
        f"export_landscape: the engine wrote {actual.upper()} data into '{filename}'; renamed to "
        f"'{corrected}'. PNG heightmaps are decoded at 8 bits/channel by Godot -- enable the "
        f"ImageWriteQueue plugin (default-on) so the exporter can write a float EXR instead.")
    return corrected


def _export_render_target(world, rt, directory, filename):
    """Writes the render target to disk and returns the filename actually
    written -- which may differ from the requested one -- or None on failure.

    Preferred path is a real float EXR via ImageWriteBlueprintLibrary. The
    fallback, ExportRenderTarget, picks the file format from the render-target
    format and IGNORES the requested extension (float formats it cannot store
    as EXR come out as PNG bytes under the .exr name), so its output is sniffed
    and renamed to whatever was actually written."""
    if _export_exr_via_image_write(rt, os.path.join(directory, filename)):
        return filename
    try:
        unreal.RenderingLibrary.export_render_target(world, rt, directory, filename)
    except Exception as e:
        unreal.log_warning(f"export_landscape: export_render_target failed for {filename}: {str(e)}")
        return None
    if not os.path.exists(os.path.join(directory, filename)):
        return None
    return _rename_to_actual_format(directory, filename)


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
