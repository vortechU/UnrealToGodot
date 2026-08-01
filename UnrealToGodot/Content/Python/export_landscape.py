"""
Unreal Engine Python Script
Exports Unreal Landscape terrains: a heightmap and one weightmap per paint layer
are written to <json_dir>/terrain/. See docs/SCHEMA_V2.md ("landscapes" section).

WHY THIS IS NOT JUST "RENDER A HEIGHTMAP"
-----------------------------------------
Every GPU export route Unreal exposes to Python fails on a landscape that has no
edit layers -- which is what most marketplace/asset-pack levels ship. Measured on
UE 5.7.4 against ModularSciFiStation's landscape (1024 components, 4032 m square):

    landscape_export_heightmap_to_render_target  -> returns True, RT stays 0.0
        (all four render-target formats it documents, and both values of the
         export-height-into-RG flag)
    Landscape.render_heightmap                   -> returns False
    Landscape.render_weightmap                   -> returns False (True for
        RTF_RGBA8, but the RT still reads back constant 0.0)

So a GPU result is never trusted on its word: it is validated by reading pixels
back, and when it carries no variation the exporter falls back to CPU sampling of
the same data the game actually uses --

    heights: LandscapeHeightfieldCollisionComponent.line_trace_component
    weights: LandscapeComponent.editor_get_paint_layer_weight_by_name_at_location

Both are strictly component-local: a component only answers for points inside its
own footprint (probed -- sampling the whole landscape through one component gives
0 everywhere but that component). Landscape components sit on a uniform XY grid,
so _ComponentGrid indexes them in O(1) instead of scanning 1024 of them per texel.

Also version-dependent, all probed on 5.7.4 rather than recalled:
  * landscape_export_weightmap_to_render_target NO LONGER EXISTS (5.7). Calling it
    is what made every previous release export zero weightmaps.
  * render_heightmap / render_weightmap live on `Landscape`, not `LandscapeProxy`.
  * `landscape_guid`, `editor_layer_settings` and
    MaterialEditingLibrary.get_material_expression_landscape_layer_names are all
    unreadable/absent on 5.7; `get_target_layer_names()`, `target_layers` and
    `get_landscape_actor()` are the live equivalents.
  * RenderingLibrary.export_render_target picks the file format from the
    render-target format and ignores the requested extension (an RGBA32F RT
    written as "x.exr" is PNG bytes), so output is sniffed and renamed.
  * ReadRenderTargetRawPixel returns linear red (1,0,0,1) as its FAILURE
    sentinel -- constant red means "no data", not "red data".

Height encoding contract ("normalized"): pixel values are relative. The importer
normalizes the image by its own min/max and rescales into height_range_m. The GPU
paths pack height in engine-internal units that differ between versions, and the
CPU path writes absolute world centimetres; normalizing makes both correct as
long as height_range_m matches the image -- so the CPU path overrides
height_range_m with the heights it actually measured instead of the (padded)
actor bounds.

Image axis mapping (consumed by the Godot importer):
    image U (+X of the image) follows Unreal +X
    image V (+Y of the image) follows Unreal +Y
The importer reconstructs vertices in Unreal world space from ue_bounds and
converts each to Godot space, so orientation is correct by construction.
"""

import os
import struct
import unreal
import ue2g_common

MAX_RT_RESOLUTION = 4033       # matches the largest recommended UE landscape size
MIN_RT_RESOLUTION = 64
DEFAULT_RT_RESOLUTION = 1009
DEFAULT_TRACE_GRID_MAX = 513   # CPU height sampling cap (~1 trace/texel)
DEFAULT_WEIGHT_GRID_MAX = 129  # CPU weight sampling cap; ~815 samples/s per layer
EDGE_NUDGE_CM = 1.0            # pull boundary samples inside the last component


def collect_landscapes(all_actors, json_dir, options=None, collected_textures=None):
    """
    Returns a list of schema landscape entries; writes terrain image files under
    <json_dir>/terrain/. Never raises, never shows dialogs.

    collected_textures, when given, receives the landscape material's textures
    so the level exporter writes them out with everything else -- without it the
    ground textures never reach Godot and the terrain cannot be textured at all.

    options (all optional):
        terrain_height_resolution : int  CPU height-grid cap (default 513)
        terrain_weight_resolution : int  CPU weight-grid cap (default 129);
                                         0 disables the CPU weightmap fallback
    """
    opts = options or {}
    results = []
    proxy_class = getattr(unreal, "LandscapeProxy", None)
    if proxy_class is None:
        return results
    parent_class = getattr(unreal, "Landscape", None)
    streaming_class = getattr(unreal, "LandscapeStreamingProxy", None)

    candidates = []
    for actor in (all_actors or []):
        try:
            if actor is not None and isinstance(actor, proxy_class):
                candidates.append(actor)
        except Exception:
            continue
    if not candidates:
        return results

    # A streaming proxy is exported through its parent Landscape, but it is still
    # needed afterwards: on a proxy-based landscape the parent actor owns no
    # components, so its own bounds are empty (headless) or wrong (observed
    # returning DOUBLE the real extent in the GUI, which shipped a 4 km entry for
    # a 2 km terrain) and every CPU fallback needs the proxies' components.
    parents = []
    orphan_proxies = []
    children_of = {}
    for actor in candidates:
        is_streaming = streaming_class is not None and isinstance(actor, streaming_class)
        if not is_streaming:
            parents.append(actor)
            children_of.setdefault(_actor_key(actor), [])
    for actor in candidates:
        if streaming_class is None or not isinstance(actor, streaming_class):
            continue
        parent = _parent_landscape(actor, parent_class)
        key = _actor_key(parent) if parent is not None else None
        if key is not None and key in children_of:
            children_of[key].append(actor)
        elif parent is not None and parent not in parents:
            # Parent exists but was not in the actor list (unloaded cell).
            parents.append(parent)
            children_of.setdefault(_actor_key(parent), []).append(actor)
        else:
            orphan_proxies.append(actor)

    # Proxies whose parent could not be resolved still deserve an export.
    if orphan_proxies and not parents:
        parents = orphan_proxies
        for actor in orphan_proxies:
            children_of.setdefault(_actor_key(actor), [])
    elif orphan_proxies and parents:
        # Attribute them to the first parent rather than dropping their geometry.
        children_of[_actor_key(parents[0])].extend(orphan_proxies)

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

    for landscape in parents:
        try:
            related = children_of.get(_actor_key(landscape), [])
            entry = _export_one_landscape(world, landscape, related, terrain_dir, opts,
                                           collected_textures)
            if entry:
                results.append(entry)
        except Exception as e:
            try:
                name = landscape.get_actor_label()
            except Exception:
                name = "<unknown>"
            unreal.log_warning(f"export_landscape: failed to export landscape '{name}': {str(e)}")

    return results


def _actor_key(actor):
    try:
        return actor.get_path_name()
    except Exception:
        return id(actor)


def _parent_landscape(proxy, parent_class):
    """The Landscape actor a streaming proxy belongs to.

    get_landscape_actor() is the live API on 5.7 -- `landscape_guid` is not a
    readable property there, so GUID matching (what this used to do) silently
    lumped every proxy onto the first parent."""
    try:
        parent = proxy.get_landscape_actor()
    except Exception:
        return None
    if parent is None:
        return None
    if parent_class is not None:
        try:
            if not isinstance(parent, parent_class):
                return None
        except Exception:
            pass
    return parent


def _union_actor_bounds(actors):
    """Union of the actors' world bounds, skipping empty ones. Returns
    (center, extent) unreal.Vectors, or (None, None)."""
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


class _ComponentGrid:
    """O(1) world-XY -> landscape component lookup.

    Landscape components tile a uniform grid, so their world origins fall on a
    lattice; the pitch is the smallest positive gap between distinct origins.
    Necessary because both CPU sampling APIs are component-local and a landscape
    routinely has 1024 components -- scanning them per texel is not viable, and
    picking the *nearest origin* is wrong (an origin is the component's corner,
    not its centre: that mistake dropped the trace hit rate to 27%).
    """

    def __init__(self, entries):
        # entries: [(world_x, world_y, component), ...]
        self.entries = entries
        self.ok = False
        if len(entries) < 1:
            return
        xs = sorted(set(round(e[0], 1) for e in entries))
        ys = sorted(set(round(e[1], 1) for e in entries))
        self.min_x, self.min_y = xs[0], ys[0]
        self.pitch_x = min((b - a for a, b in zip(xs, xs[1:])), default=0.0)
        self.pitch_y = min((b - a for a, b in zip(ys, ys[1:])), default=0.0)
        if self.pitch_x <= 0.0:
            self.pitch_x = self.pitch_y
        if self.pitch_y <= 0.0:
            self.pitch_y = self.pitch_x
        self.single = None
        if self.pitch_x <= 0.0:
            # One component (or one row/column of them): it answers for the
            # whole landscape, so there is no lattice to index.
            self.single = entries[0][2]
            self.cells = {}
            self.max_ix = self.max_iy = 0
            self.ok = True
            return
        self.cells = {}
        self.max_ix = 0
        self.max_iy = 0
        for wx, wy, comp in entries:
            ix = int(round((wx - self.min_x) / self.pitch_x))
            iy = int(round((wy - self.min_y) / self.pitch_y))
            self.cells[(ix, iy)] = comp
            self.max_ix = max(self.max_ix, ix)
            self.max_iy = max(self.max_iy, iy)
        self.ok = True

    def at(self, wx, wy):
        if not self.ok:
            return None
        if self.single is not None:
            return self.single
        import math
        # Clamped, so the landscape's outer edge -- where the sample sits
        # exactly on the last component's boundary -- still resolves to that
        # component instead of falling off the lattice.
        ix = min(max(int(math.floor((wx - self.min_x) / self.pitch_x)), 0), self.max_ix)
        iy = min(max(int(math.floor((wy - self.min_y) / self.pitch_y)), 0), self.max_iy)
        return self.cells.get((ix, iy))


def _build_component_grid(actors, comp_class):
    entries = []
    for actor in actors:
        try:
            comps = list(actor.get_components_by_class(comp_class))
        except Exception:
            continue
        for c in comps:
            try:
                loc = c.get_world_location()
            except Exception:
                continue
            entries.append((loc.x, loc.y, c))
    return _ComponentGrid(entries)


def _export_one_landscape(world, landscape, related_proxies, terrain_dir, opts,
                          collected_textures=None):
    label = ue2g_common.sanitize_name(landscape.get_actor_label())
    group = [landscape] + list(related_proxies)
    origin, extent = _union_actor_bounds(group)
    if origin is None:
        origin, extent = landscape.get_actor_bounds(False)
    width, height = _estimate_resolution(landscape, extent)

    height_range_cm = None

    # --- 1. Heightmap: GPU first, validated; CPU collision tracing otherwise ---
    height_file = f"{label}_height.exr"
    exported = _gpu_heightmap_to_file(world, landscape, label, terrain_dir, height_file, width, height)
    if exported is None:
        traced = _trace_heightmap_cpu(group, origin, extent, terrain_dir, height_file,
                                      width, height, opts)
        if traced is not None:
            exported, width, height, height_range_cm = traced
    if not exported:
        unreal.log_warning(f"export_landscape: every heightmap source failed for '{label}'; landscape skipped.")
        return None
    height_file = exported
    unreal.log(f"export_landscape: heightmap {width}x{height} for '{label}' -> {height_file}")

    # --- 2. Weightmaps, one per paint layer ------------------------------------
    layers = _export_layers(world, landscape, group, origin, extent, terrain_dir,
                            label, width, height, opts)

    # --- 3. Placement data (see module docstring for the contract) -------------
    if height_range_cm is None:
        height_range_cm = (origin.z - extent.z, origin.z + extent.z)
    z_center = (height_range_cm[0] + height_range_cm[1]) * 0.5
    z_extent = max((height_range_cm[1] - height_range_cm[0]) * 0.5, 0.5)

    transform = ue2g_common.unreal_to_godot_transform(landscape.get_actor_transform())
    return {
        "name": label,
        "godot_transform": transform,
        "heightmap_file": f"terrain/{height_file}",
        "heightmap_resolution": [width, height],
        # Godot footprint: X follows UE Y, Z follows UE X
        "world_size_m": [extent.y * 2.0 * 0.01, extent.x * 2.0 * 0.01],
        # Same box as ue_bounds, in Godot metres -- including the corrected Z
        # centre, so the two never disagree.
        "world_center_m": ue2g_common.vector_to_godot(
            unreal.Vector(origin.x, origin.y, z_center)),
        "height_range_m": [height_range_cm[0] * 0.01, height_range_cm[1] * 0.01],
        "height_encoding": "normalized",
        # Per-quad size in metres, i.e. Terrain3D's vertex_spacing.
        "vertex_spacing_m": _vertex_spacing_m(landscape),
        "ue_bounds": {
            "center": [origin.x, origin.y, z_center],
            "extent": [extent.x, extent.y, z_extent],
        },
        "layers": layers,
        "material": _collect_material_textures(landscape, collected_textures),
    }


def _vertex_spacing_m(landscape):
    """Metres between landscape vertices (UE's per-quad scale). Terrain3D calls
    this vertex_spacing and defaults it to 1.0."""
    try:
        s = landscape.get_actor_scale3d()
        v = abs(s.x) * 0.01
        return v if v > 0.0001 else 1.0
    except Exception:
        return 1.0


# =============================================================================
# Heightmap
# =============================================================================

def _rt_readback_varies(world, rt, width, height):
    """True when a coarse pixel readback shows variation. A constant readback
    after a landscape draw means the draw never happened -- the legacy export
    call returns True regardless. The readback API returns linear red (1,0,0,1)
    as its own failure sentinel, so constant red also (correctly) counts as
    'no data'. If reading raises, the draw gets the benefit of the doubt."""
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
    """GPU heightmap render -> file. Returns the filename actually written, or
    None when the GPU path verifiably produced nothing."""
    drew = False
    rt = None
    # render_heightmap (Landscape only) reports failure honestly, so it is tried
    # first and its False is believed without a readback.
    if hasattr(landscape, "render_heightmap"):
        rt = _create_render_target(world, width, height)
        if rt is None:
            return None
        try:
            drew = bool(landscape.render_heightmap(landscape.get_actor_transform(), unreal.Box2D(), rt))
        except Exception as e:
            unreal.log_warning(f"export_landscape: render_heightmap raised for '{label}': {str(e)}")
        if drew and not _rt_readback_varies(world, rt, width, height):
            drew = False
        if not drew:
            unreal.log_warning(f"export_landscape: render_heightmap produced no data for '{label}' "
                               f"(landscape without edit layers?).")

    if not drew and hasattr(landscape, "landscape_export_heightmap_to_render_target"):
        rt = _create_render_target(world, width, height)
        if rt is None:
            return None
        try:
            # export_height_into_rg_channel=True: UE stores landscape height as
            # two 8-bit halves in R and G. With the flag set (and an RGBA16f /
            # RGBA32f target, which _create_render_target now prefers) the
            # engine recombines them into a single R channel. Passing False --
            # what this used to do -- leaves the importer reading only the high
            # half out of R, i.e. a 256-step staircase instead of a heightfield.
            try:
                landscape.landscape_export_heightmap_to_render_target(rt, True, True)
            except TypeError:
                landscape.landscape_export_heightmap_to_render_target(rt, True)
            drew = True
        except Exception as e:
            unreal.log_warning(f"export_landscape: legacy heightmap export failed for '{label}': {str(e)}")
        if drew and not _rt_readback_varies(world, rt, width, height):
            unreal.log_warning(f"export_landscape: legacy heightmap export left a constant render target "
                               f"for '{label}' (it returns success regardless).")
            drew = False

    if not drew or rt is None:
        unreal.log(f"export_landscape: no GPU heightmap for '{label}' -- falling back to collision tracing.")
        return None
    return _export_render_target(world, rt, terrain_dir, height_file)


def _trace_at(grid, Vector, wx, wy, top, bottom):
    """One downward trace against whichever component owns (wx, wy). None on miss."""
    comp = grid.at(wx, wy)
    if comp is None:
        return None
    try:
        res = comp.line_trace_component(Vector(wx, wy, top), Vector(wx, wy, bottom), True, False)
    except Exception:
        return None
    if res is None:
        return None
    loc = res[0] if isinstance(res, tuple) else getattr(res, "location", None)
    return None if loc is None else loc.z


def _trace_heightmap_cpu(group, center, extent, terrain_dir, height_file, width, height, opts):
    """Samples height by line-tracing the landscape's collision heightfield, one
    trace per texel, and writes a float32 EXR of absolute world centimetres.

    Slower than a GPU render but immune to every edit-layer/RHI failure mode:
    this is the same surface the player walks on.

    Returns (filename, width, height, (min_cm, max_cm)) or None."""
    comp_class = getattr(unreal, "LandscapeHeightfieldCollisionComponent", None)
    if comp_class is None or center is None:
        return None
    grid = _build_component_grid(group, comp_class)
    if not grid.ok:
        unreal.log_warning("export_landscape: no landscape collision components to trace against.")
        return None

    cap = _int_opt(opts, "terrain_height_resolution", DEFAULT_TRACE_GRID_MAX)
    w = max(2, min(width, cap))
    h = max(2, min(height, cap))
    top = center.z + extent.z + 10000.0
    bottom = center.z - extent.z - 10000.0
    Vector = unreal.Vector
    heights = [None] * (w * h)
    hits = 0
    misses = 0
    min_h = 1e30
    max_h = -1e30
    unreal.log(f"export_landscape: tracing a {w}x{h} height grid against "
               f"{len(grid.entries)} collision components (CPU fallback)...")
    for iy in range(h):
        wy = center.y - extent.y + (2.0 * extent.y) * iy / (h - 1)
        row = iy * w
        last = None
        for ix in range(w):
            wx = center.x - extent.x + (2.0 * extent.x) * ix / (w - 1)
            z = _trace_at(grid, Vector, wx, wy, top, bottom)
            if z is None and (ix == 0 or iy == 0 or ix == w - 1 or iy == h - 1):
                # The far row/column sits exactly on the last component's
                # boundary plane, where the ray slides past the triangles
                # instead of hitting them (measured: exactly h misses, all in
                # one edge row). Nudge a hair inside and retry.
                z = _trace_at(grid, Vector,
                              wx + (EDGE_NUDGE_CM if ix == 0 else (-EDGE_NUDGE_CM if ix == w - 1 else 0.0)),
                              wy + (EDGE_NUDGE_CM if iy == 0 else (-EDGE_NUDGE_CM if iy == h - 1 else 0.0)),
                              top, bottom)
            if z is None:
                misses += 1
                z = last          # holes/edges: carry the previous height
            else:
                hits += 1
                min_h = min(min_h, z)
                max_h = max(max_h, z)
                last = z
            heights[row + ix] = z
    if hits == 0:
        unreal.log_warning("export_landscape: collision tracing hit nothing; landscape has no usable collision.")
        return None
    for i, v in enumerate(heights):
        if v is None:
            heights[i] = min_h
    _write_exr_r32(os.path.join(terrain_dir, height_file), w, h, heights)
    unreal.log(f"export_landscape: traced heightmap {w}x{h} ({hits} hits, {misses} filled), "
               f"z = {min_h:.1f} .. {max_h:.1f} cm")
    if max_h <= min_h:
        max_h = min_h + 1.0
    return height_file, w, h, (min_h, max_h)


# =============================================================================
# Weightmaps / paint layers
# =============================================================================

def _export_layers(world, landscape, group, center, extent, terrain_dir, label, width, height, opts):
    layers = []
    for layer_name, layer_info in _discover_layers(landscape):
        try:
            safe = ue2g_common.sanitize_name(layer_name)
            weight_file = f"{label}_weight_{safe}.exr"
            written = _gpu_weightmap_to_file(world, landscape, layer_name, terrain_dir,
                                             weight_file, width, height)
            if written is None:
                written = _sample_weightmap_cpu(group, center, extent, terrain_dir,
                                                weight_file, layer_name, width, height, opts)
            entry = {"name": str(layer_name)}
            if written:
                entry["weightmap_file"] = f"terrain/{written}"
            colour = _layer_debug_color(layer_info)
            if colour:
                entry["debug_color"] = colour
            layers.append(entry)
        except Exception as e:
            unreal.log_warning(f"export_landscape: layer '{layer_name}' failed for '{label}': {str(e)}")
    return layers


def _gpu_weightmap_to_file(world, landscape, layer_name, terrain_dir, weight_file, width, height):
    """Weightmap render -> file; returns the filename actually written or None.

    landscape_export_weightmap_to_render_target was REMOVED in UE 5.7 -- calling
    it unconditionally (what this used to do) is why no release exported a single
    weightmap on 5.7. Both spellings are now hasattr-guarded."""
    for attempt in ("render_weightmap", "landscape_export_weightmap_to_render_target"):
        if not hasattr(landscape, attempt):
            continue
        rt = _create_render_target(world, width, height)
        if rt is None:
            return None
        try:
            if attempt == "render_weightmap":
                ok = bool(landscape.render_weightmap(landscape.get_actor_transform(), unreal.Box2D(),
                                                     unreal.Name(str(layer_name)), rt))
            else:
                landscape.landscape_export_weightmap_to_render_target(rt, layer_name)
                ok = True
        except Exception as e:
            unreal.log_warning(f"export_landscape: {attempt}('{layer_name}') failed: {str(e)}")
            continue
        if ok and _rt_readback_varies(world, rt, width, height):
            return _export_render_target(world, rt, terrain_dir, weight_file)
    return None


def _sample_weightmap_cpu(group, center, extent, terrain_dir, weight_file, layer_name,
                          width, height, opts):
    """CPU paint-layer sampling via editor_get_paint_layer_weight_by_name_at_location.

    Component-local like the trace path, and considerably slower (~800 samples/s
    measured on 5.7.4), so the grid is capped well below the heightmap's. A layer
    that samples to all-zero is unpainted and produces no file."""
    cap = _int_opt(opts, "terrain_weight_resolution", DEFAULT_WEIGHT_GRID_MAX)
    if cap <= 0:
        return None
    comp_class = getattr(unreal, "LandscapeComponent", None)
    if comp_class is None or center is None:
        return None
    grid = _build_component_grid(group, comp_class)
    if not grid.ok:
        return None
    sampler = "editor_get_paint_layer_weight_by_name_at_location"
    probe = grid.entries[0][2]
    if not hasattr(probe, sampler):
        return None

    w = max(2, min(width, cap))
    h = max(2, min(height, cap))
    Vector = unreal.Vector
    name = unreal.Name(str(layer_name))
    values = [0.0] * (w * h)
    total = 0.0
    for iy in range(h):
        wy = center.y - extent.y + (2.0 * extent.y) * iy / (h - 1)
        row = iy * w
        for ix in range(w):
            wx = center.x - extent.x + (2.0 * extent.x) * ix / (w - 1)
            comp = grid.at(wx, wy)
            if comp is None:
                continue
            try:
                v = float(comp.editor_get_paint_layer_weight_by_name_at_location(
                    Vector(wx, wy, center.z), name))
            except Exception:
                v = 0.0
            values[row + ix] = v
            total += v
    if total <= 0.0:
        unreal.log(f"export_landscape: paint layer '{layer_name}' samples to zero everywhere "
                   f"(unpainted); no weightmap written.")
        return None
    _write_exr_r32(os.path.join(terrain_dir, weight_file), w, h, values)
    unreal.log(f"export_landscape: sampled weightmap {w}x{h} for layer '{layer_name}' (CPU fallback)")
    return weight_file


def _layer_debug_color(layer_info):
    """LandscapeLayerInfoObject.layer_usage_debug_color, as linear RGB.

    Unreal shows it in the landscape layer list, and it is the only per-layer
    colour the API exposes -- the actual look lives in the landscape material's
    layer-blend graph, which carries no reliable layer->texture mapping (layers
    here are literally named "1", "2", "3"). Godot uses it to tint each splat
    layer so the paint layout is visible before textures are assigned."""
    if layer_info is None:
        return None
    c = ue2g_common.safe_get_prop(layer_info, "layer_usage_debug_color")
    if c is None:
        return None
    try:
        return [float(c.r), float(c.g), float(c.b)]
    except Exception:
        return None


def _collect_material_textures(landscape, collected_textures):
    """Harvests the landscape material's textures and describes them in the schema.

    Without this the ground textures never leave Unreal: the level exporter only
    walks MESH materials and decal materials, and a landscape's material hangs
    off the actor, referenced by nothing else. Every previous release therefore
    shipped weightmaps with no textures to blend -- there was physically nothing
    in the Godot project to assign.

    The layer -> texture mapping is still not derivable (paint layers are
    routinely named "1", "2", "3" while the textures are named after the
    material they came from), so the roles here are classified from the texture
    ASSET names and the user does the final pairing. Returns {} when there is no
    readable material."""
    material = ue2g_common.safe_get_prop(landscape, "landscape_material")
    if material is None:
        return {}

    role_of = None
    try:
        # Deferred: export_level_to_json imports THIS module, so a top-level
        # import would be circular. By the time a landscape is exported the
        # parent module is already in sys.modules.
        import export_level_to_json
        role_of = export_level_to_json.resolve_texture_role
    except Exception:
        pass

    textures = []
    seen = set()
    try:
        for param_name, tex in ue2g_common.iter_base_material_textures(material):
            if tex is None:
                continue
            try:
                tex_name = tex.get_name()
            except Exception:
                continue
            if tex_name in seen:
                continue
            seen.add(tex_name)
            if collected_textures is not None:
                collected_textures.add(tex)
            role = None
            if role_of is not None:
                try:
                    role, _packed = role_of(str(param_name or "").lower(), tex_name.lower())
                except Exception:
                    role = None
            # resolve_texture_role returns material SLOT keys ("albedo_texture",
            # "normal_texture"). This field names a role, not a slot, so the
            # suffix comes off -- both consumers match on "albedo".
            if role and role.endswith("_texture"):
                role = role[: -len("_texture")]
            textures.append({
                "parameter": str(param_name or ""),
                "texture": tex_name,
                "role": role or "unknown",
            })
    except Exception as e:
        unreal.log_warning(f"export_landscape: could not read the landscape material's textures: {str(e)}")

    entry = {"textures": textures}
    try:
        entry["name"] = material.get_name()
        entry["path"] = material.get_path_name()
    except Exception:
        pass
    if textures:
        unreal.log(f"export_landscape: harvested {len(textures)} texture(s) from the landscape material.")
    return entry


def _discover_layers(landscape):
    """Yields (layer_name, layer_info_object_or_None) for every paint layer.

    Probed on 5.7.4: get_target_layer_names() is the documented live API,
    target_layers is the backing map, and editor_layer_settings /
    MaterialEditingLibrary.get_material_expression_landscape_layer_names -- the
    only two routes the previous implementation had -- no longer exist there."""
    ordered = []
    seen = set()

    def _add(name, info):
        text = str(name)
        if not text or text.lower() == "none" or text in seen:
            return
        seen.add(text)
        ordered.append((text, info))

    target_layers = ue2g_common.safe_get_prop(landscape, "target_layers")

    def _info_for(name):
        if not target_layers:
            return None
        try:
            settings = target_layers[unreal.Name(str(name))]
        except Exception:
            return None
        return ue2g_common.safe_get_prop(settings, "layer_info_obj")

    # 1. UE 5.x: the documented accessor.
    if hasattr(landscape, "get_target_layer_names"):
        try:
            for n in (landscape.get_target_layer_names() or []):
                _add(n, _info_for(n))
        except Exception:
            pass

    # 2. The backing map, keyed by layer name.
    if not ordered and target_layers:
        try:
            for key in target_layers:
                _add(key, ue2g_common.safe_get_prop(target_layers[key], "layer_info_obj"))
        except Exception:
            pass

    # 3. UE4 / early UE5: editor_layer_settings -> layer_info_obj.
    if not ordered:
        settings = ue2g_common.safe_get_prop(landscape, "editor_layer_settings")
        for setting in (settings or []):
            info = ue2g_common.safe_get_prop(setting, "layer_info_obj")
            if info is None:
                continue
            name = ue2g_common.safe_get_prop(info, "layer_name")
            if name is None:
                try:
                    name = info.get_name()
                except Exception:
                    continue
            _add(name, info)

    # 4. Last resort: layer names referenced by the landscape material.
    if not ordered:
        material = ue2g_common.safe_get_prop(landscape, "landscape_material")
        getter = getattr(getattr(unreal, "MaterialEditingLibrary", None),
                         "get_material_expression_landscape_layer_names", None)
        if material is not None and getter is not None:
            try:
                for layer in (getter(material) or []):
                    _add(layer, None)
            except Exception:
                pass

    return ordered


# =============================================================================
# Files
# =============================================================================

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
    across engine versions -- 5.7 has `async_`, not `async`). Returns True only
    when the file on disk verifiably contains EXR data.
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

    Godot picks image decoders by extension, so a PNG named .exr fails to load
    there. The Godot importer sniffs content too, but a truthful name also keeps
    Godot's own texture import from erroring on it."""
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
    """Writes a render target to disk and returns the filename actually written
    -- which may differ from the requested one -- or None on failure.

    Preferred path is a real float EXR via ImageWriteBlueprintLibrary. The
    fallback, ExportRenderTarget, picks the file format from the render-target
    format and IGNORES the requested extension (measured: an RGBA32F RT asked
    for "x.exr" comes out as PNG bytes), so its output is sniffed and renamed."""
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


# =============================================================================
# Misc
# =============================================================================

def _int_opt(opts, key, default):
    try:
        v = int(opts.get(key, default))
    except Exception:
        return default
    return v if v >= 0 else default


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
    """Creates a float render target.

    RTF_RGBA32F first: it is the only format that both
    landscape_export_heightmap_to_render_target documents as valid AND
    read_render_target_raw_pixel can read back. RTF_R32F is deliberately last --
    the readback API returns its red failure sentinel for it, which makes an
    empty export indistinguishable from real data."""
    formats = []
    for fmt_name in ("RTF_RGBA32F", "RTF_RGBA16F", "RTF_R32F"):
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
