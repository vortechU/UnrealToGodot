# Layout JSON Schema v2 & Module Contracts

This document is the authoritative contract between the Unreal-side Python exporter
modules and the Godot-side GDScript importer modules. All feature modules MUST
read and write exactly these structures.

## General conventions

- All transforms in the JSON are stored as **already-converted Godot-space** dictionaries
  unless the key is prefixed with `unreal_`:

```json
{ "translation": [x, y, z], "rotation_quat": [x, y, z, w], "scale": [x, y, z] }
```

  Units: meters, right-handed, Y-up. Conversion from Unreal (left-handed, Z-up, cm):
  `godot = [uy*0.01, uz*0.01, -ux*0.01]`, rotation basis remapped via `C * R * C^T`
  (see TECHNICAL_BRIEF.md). Use `ue2g_common.py` (Python) / `import_common.gd` (GDScript);
  never re-implement the math.

- Note on directions: the basis conversion maps Unreal's forward **+X** onto Godot's
  **-Z**, which is exactly the forward axis of Godot lights, decals and cameras.
  Converted rotations can therefore be used **as-is** for lights/decals.

- Auxiliary files are written relative to the JSON file's directory:
  - `textures/` — exported PNG textures
  - `terrain/`  — heightmaps / weightmaps

- All colors are linear `[r, g, b]` or `[r, g, b, a]` floats 0..1.
- Fields that could not be read are `null` (Python `None`), never missing keys where the
  schema shows them. Consumers must still be defensive (`dict.get`).

## Top-level document

```json
{
    "format_version": 2,
    "level_name": "MyLevel",
    "unreal_project_dir": "C:/...",
    "total_actors": 0,
    "total_mesh_instances": 0,
    "meshes": { "<mesh_key>": { ... } },
    "actors": [ ... ],
    "lights": [ ... ],
    "post_process": [ ... ],
    "height_fog": { ... } | null,
    "sky_light": { ... } | null,
    "has_sky_atmosphere": false,
    "decals": [ ... ],
    "landscapes": [ ... ],
    "foliage": [ ... ],
    "navigation": { ... } | null
}
```

Feature arrays are `[]` / `null` when the feature was disabled or nothing was found.

## meshes (v2 change: keys & export_name)

`meshes` is keyed by a **mesh_key**: the mesh name, unless two different assets share a
name, in which case colliding entries (after the first) get `"<name>_<8-char-md5-of-path>"`.
Each entry:

```json
{
    "path": "/Game/Env/SM_Rock.SM_Rock",
    "export_name": "<mesh_key>",       // base filename of the exported glTF
    "collision": { "boxes": [...], "spheres": [...], "capsules": [...], "convex_hulls": [...] } | null,
    "materials": [ { "slot_index": 0, "slot_name": "...", "material_name": "...", "material_path": "...", "parameters": { ... } } ]
}
```

Components reference the library through `"mesh_key"` (falling back to `"mesh_name"`
for v1 files).

## actors (v2 additions per actor)

```json
{
    "name": "...", "class": "...",
    "unreal_transform": { ... }, "godot_transform": { ... },
    "tags": ["patrol_point"],                      // actor.tags, stringified
    "properties": { "health": 100.0, "is_interactable": true },  // best-effort BP vars
    "components": [ { "name": "...", "mesh_key": "...", "mesh_name": "...", ... , "tags": [...] } ]
}
```

Godot import: `node.set_meta("unreal_class", class)`, `set_meta("unreal_tags", tags)`,
and one `set_meta(k, v)` per entry in `properties`. Property values are restricted to
bool / int / float / string; anything else is stringified or dropped.

### Component placement: `godot_world_transform` is authoritative

**Importers MUST place a mesh component using its `godot_world_transform`, and MUST
NOT compute `actor.godot_transform * component.godot_relative_transform`.**

`*_relative_transform` comes from Unreal's `get_relative_transform()`, which is measured
against the component's **immediate parent component** — not against the actor:

- For an actor's **root** component (every plain `StaticMeshActor`) there is no parent
  component, so the relative transform *is already the world transform*. Composing it
  with the actor transform applies the placement **twice**: an actor at `(20, 3, -50)`
  lands at `(40, 6, -100)`.
- For a component nested two or more levels deep in a Blueprint, the relative transform
  is measured against an intermediate component, so composing with only the actor
  transform **silently drops** the intermediate offsets.

`*_relative_transform` is retained for debugging and for reconstructing hierarchy, but
it is not a placement input. Consumers that must parent under an actor node (multi-mesh
Blueprints) should reparent via `actor_transform.affine_inverse() * world`, never by
composing relatives.

Implementations: `component_world_transform()` in `import_unreal_layout.gd`, and
`_component_world_mat()` in `tscn_writer.py`.

## lights

```json
{
    "name": "PointLight_2",
    "type": "directional" | "point" | "spot" | "rect",
    "godot_transform": { ... },
    "color": [r, g, b],
    "intensity": 5000.0,
    "intensity_units": "lux" | "lumens" | "candela" | "ev" | "unitless" | "unknown",
                                         // "lux" is always used for directional lights,
                                         // which have no IntensityUnits property in UE
    "godot_energy": 1.7,                 // pre-converted suggested Godot light_energy
    "temperature_kelvin": 6500.0 | null,
    "use_temperature": false,
    "cast_shadows": true,
    "attenuation_radius_m": 10.0,        // point/spot/rect; UE cm * 0.01
    "source_radius_m": 0.0,
    "inner_cone_angle_deg": 0.0,         // spot only (half-angle)
    "outer_cone_angle_deg": 44.0,        // spot only (half-angle)
    "indirect_intensity": 1.0,
    "visible": true
}
```

Godot mapping: directional→`DirectionalLight3D`; point→`OmniLight3D`
(`omni_range = attenuation_radius_m`); spot→`SpotLight3D` (`spot_range`,
`spot_angle = outer_cone_angle_deg`); rect→`OmniLight3D` approximation with
`set_meta("unreal_rect_light", true)`. `light_energy = godot_energy * options.light_energy_scale`.
If `use_temperature`, apply Kelvin→RGB approximation multiplied into color.

`godot_energy` conversion heuristic (documented in export_environment.py, tunable at
import time): directional lux → `intensity / 10.0`; lumens → `intensity / 1700.0 * 8.0`
adjusted so UE defaults land near Godot defaults; candela → `intensity / 100.0`;
unitless → `intensity / 8.0`. The exporter also always stores raw `intensity` + units so
the importer/user can re-derive.

## post_process

```json
{
    "name": "PostProcessVolume_1",
    "unbound": true,
    "priority": 0.0,
    "godot_transform": { ... },
    "extent_m": [x, y, z],
    "settings": {
        "bloom_intensity": 0.675 | null,
        "bloom_threshold": -1.0 | null,
        "ao_intensity": 0.5 | null,
        "ao_radius": 200.0 | null,          // UE cm
        "exposure_bias": 1.0 | null,
        "exposure_method": "auto" | "manual" | null,
        "white_temp": 6500.0 | null,
        "saturation": [r,g,b,a] | null,
        "contrast": [r,g,b,a] | null,
        "vignette_intensity": 0.4 | null
    }
}
```

Only settings whose `override_*` flag is set in Unreal are non-null.
Godot: one `WorldEnvironment` from the highest-priority unbound volume
(glow, ssao, tonemap exposure, adjustments). Bound volumes: skipped for
WorldEnvironment but still listed.

## height_fog / sky_light / has_sky_atmosphere

```json
"height_fog": {
    "fog_density": 0.02, "fog_height_falloff": 0.2,
    "color": [r, g, b], "start_distance_m": 0.0
},
"sky_light": { "intensity": 1.0, "color": [r, g, b] },
"has_sky_atmosphere": true
```

Godot: fog → `Environment.fog_enabled` (+density heuristic `fog_density * 0.5`, so UE's
default 0.02 lands on Godot's default 0.01);
sky_light/atmosphere → `Environment.background = Sky` with `ProceduralSkyMaterial` and
ambient light energy.

## decals

```json
{
    "name": "DecalActor_1",
    "godot_transform": { ... },
    "size_m": [x, y, z],           // Godot Decal.size: x=width, y=projection depth, z=height
    "sort_order": 0,
    "material_name": "...", "material_path": "...",
    "textures": { "albedo": "T_Blood" | null, "normal": null, "orm": null, "emission": null }
}
```

UE `decal_size` is half-size cm in (X=projection depth, Y=width, Z=height); UE decals
project along **-X** (local). The exporter must fold whatever axis fix-up is needed into
`godot_transform` so the Godot `Decal` (projects along **-Y**) matches, and set
`size_m = [Y*2, X*2, Z*2] * 0.01`. Texture names refer to files in `textures/`.

## landscapes

```json
{
    "name": "Landscape_0",
    "godot_transform": { ... },              // landscape actor transform
    "heightmap_file": "terrain/Landscape_0_height.exr",   // relative to JSON dir; float EXR (or 16-bit PNG)
    "heightmap_resolution": [513, 513],
    "world_size_m": [xz_size_x, xz_size_z],  // footprint in Godot meters
    "height_range_m": [min_m, max_m],        // world-space height range the heightmap spans
    "height_encoding": "float_absolute_m" | "normalized",  // how pixel values map to meters
    "layers": [
        { "name": "Grass", "weightmap_file": "terrain/Landscape_0_weight_Grass.exr" }
    ]
}
```

Godot: prefer `Terrain3D` if `ClassDB.class_exists("Terrain3D")`, else HTerrain if
present, else built-in fallback: `MeshInstance3D` with an ArrayMesh generated from the
heightmap + `StaticBody3D`/`HeightMapShape3D` collision. The fallback MUST exist so the
tool works with no third-party plugins.

## foliage

```json
{
    "name": "Foliage_SM_Grass_01",           // unique per (foliage actor, mesh)
    "mesh_key": "SM_Grass_01",               // key into meshes library / glTF filename base
    "mesh_name": "SM_Grass_01",
    "instance_count": 1234,
    "source": "foliage" | "ism" | "hism",    // component origin
    "godot_transforms": [ /* 12 floats per instance */ ]
}
```

`godot_transforms` layout, per instance, **world-space Godot**:
`[bx.x, bx.y, bx.z, by.x, by.y, by.z, bz.x, bz.y, bz.z, o.x, o.y, o.z]`
(basis column X, column Y, column Z, then origin).

Godot: one `MultiMeshInstance3D` per entry; the Mesh is taken from the first
`MeshInstance3D` found inside the instanced glTF for `mesh_key`.
Regular `InstancedStaticMeshComponent`/HISM components on ordinary actors are ALSO
routed here (source `"ism"`/`"hism"`), and are excluded from `actors[].components`.

## navigation

```json
{
    "bounds_volumes": [ { "name": "...", "godot_transform": { ... }, "extent_m": [x, y, z] } ],
    "agent_radius_m": 0.35, "agent_height_m": 1.92,
    "max_slope_deg": 44.0, "agent_max_step_height_m": 0.35,
    "cell_size_m": 0.19
}
```

Godot: one `NavigationRegion3D` per bounds volume with a configured (unbaked)
`NavigationMesh` using the agent parameters; optional auto-bake on import
(`options.navigation_bake`).

---

# Module contracts

## Unreal Python (UnrealToGodot/Content/Python/)

All modules import shared math from `ue2g_common`. All entry points MUST be
exception-safe: never raise, never show dialogs; log via `unreal.log_warning` and return
empty structures on failure.

| Module | Entry point | Returns |
|---|---|---|
| `export_environment.py` | `collect_environment(all_actors, collected_textures)` | `{"lights": [...], "post_process": [...], "height_fog": ... , "sky_light": ..., "has_sky_atmosphere": bool, "decals": [...]}` |
| `export_landscape.py` | `collect_landscapes(all_actors, json_dir)` | `[landscape, ...]` (writes files under `json_dir/terrain/`) |
| `export_foliage.py` | `collect_foliage(all_actors, register_mesh)` | `[foliage_entry, ...]`; `register_mesh(static_mesh) -> mesh_key` registers the mesh in the library and returns its key. Also exposes `get_instanced_component_classes()` so the orchestrator can exclude ISM components from regular actor export. |
| `export_gameplay.py` | `collect_navigation(all_actors)` / `extract_actor_metadata(actor)` / `extract_component_tags(component)` | navigation dict or `None` / `{"tags": [...], "properties": {...}}` / `[str]` |
| `tscn_writer.py` | `write_tscn(layout_data, output_path, res_paths, options)` | `True/False`; `res_paths = {"models": "res://models/", "textures": "res://textures/", "terrain": "res://terrain/"}` |

`collected_textures` is a `set` of `unreal.Texture` objects; anything added to it is
bulk-exported to `textures/` by the orchestrator.

## Godot GDScript (addons/unreal_importer/)

Shared helpers: `import_common.gd` (static funcs; preload it, do not duplicate helpers).
Each feature module extends `RefCounted`, is `@tool`, and exposes:

```gdscript
func apply(data: Dictionary, root: Node, scene_owner: Node, options: Dictionary) -> Dictionary
    # returns {"created": int, "warnings": PackedStringArray}
```

| Module | Consumes |
|---|---|
| `import_environment.gd` | `lights`, `post_process`, `height_fog`, `sky_light`, `has_sky_atmosphere`, `decals` |
| `import_terrain.gd` | `landscapes` |
| `import_foliage.gd` | `foliage` (+ `meshes` for material data) |
| `import_gameplay.gd` | `navigation` + per-actor metadata helper `apply_actor_metadata(node, actor_data, options)` |

`options` dictionary (import side):

```gdscript
{
    "apply_lights": true, "apply_environment": true, "apply_decals": true,
    "build_terrain": true, "terrain_mode": "auto",   # auto|terrain3d|hterrain|mesh
    "apply_foliage": true,
    "apply_navigation": true, "navigation_bake": false,
    "apply_metadata": true,
    "light_energy_scale": 1.0,
    "models_folder": "res://models/", "textures_folder": "res://textures/",
    "json_dir": "res://",           # directory containing the layout JSON (for terrain/ files)
}
```

Export-side options dictionary (orchestrator → GUI):

```python
{
    "lights": True, "decals": True, "landscape": True, "foliage": True,
    "navigation": True, "metadata": True,
    "write_tscn": False, "tscn_scene_name": "imported_level",
}
```
