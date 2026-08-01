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

- **`scale` is a LOCAL scale** — it names the node's own axes, so consumers must rebuild
  the basis as `R * diag(scale)`, i.e. scale the basis **columns**. In GDScript that is
  `Basis(quat).scaled_local(scale)`; `Basis.scaled()` scales *rows* and applies the scale
  in the **parent** frame, which shears any rotated actor with a non-uniform scale.
  Only column scaling reproduces `C * (R_unreal * S_unreal) * C^T`.

- Note on directions: the basis conversion maps Unreal's forward **+X** onto Godot's
  **-Z**, which is exactly the forward axis of Godot lights and cameras. Converted
  rotations can therefore be used **as-is** for lights. Decals need an extra fix-up
  (see the `decals` section).

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

### material `parameters`

```json
{
    "albedo_color": [r, g, b, a],      // a tint MULTIPLIER, and legitimately >1:
                                       // packs often author dark albedo and scale it up
    "roughness": 1.0,                  // multiplier against the roughness channel
    "metallic": 1.0,                   // multiplier against the metallic channel
    "albedo_texture": "TX_Foo_ALB" | null,
    "normal_texture": "TX_Foo_NRM" | null,
    "roughness_texture": null,         // only when the material uses SEPARATE maps
    "metallic_texture": null,
    "packed_texture": "TX_Foo_RMA" | null,   // one texture holding 3 channels
    "packed_channels": { "roughness": 0, "metallic": 1, "ao": 2 } | null,
    "tiling": [u, v],
    "texture_paths": { "albedo_texture": "/Game/Pack/T_Foo.T_Foo", ... }
}
```

**Texture names are exported FILENAMES, not asset names.** Two different texture
assets can share a name (two packs both shipping a `T_Concrete_D`); such names are
exported as `<name>_<8-char-path-hash>.png`, exactly like colliding mesh names, so
each material still binds its own art. Importers must treat the string as a
filename stem and nothing more. `texture_paths` records the Unreal asset path each
slot came from — the exporter needs it because the final filename is only known
once the whole level has been scanned, and it is left in the JSON for diagnostics.
Importers should ignore it.

**Packed PBR maps.** Unreal packs roughness/metallic/AO into one texture's RGB
channels, and the channel order is a naming convention, not a standard:

| Parameter name | R | G | B |
| --- | --- | --- | --- |
| `RMA`, `RMAO` | roughness | metallic | ao |
| `ORM`, `ARM` | ao | roughness | metallic |
| `MRA`, `MRAO` | metallic | roughness | ao |

The exporter resolves the layout from the parameter name and emits explicit
channel indices in `packed_channels`, matching Godot's
`BaseMaterial3D.TextureChannel` (`0=RED, 1=GREEN, 2=BLUE, 3=ALPHA`). Importers
assign the same texture to `roughness_texture`/`metallic_texture`/`ao_texture` and
set each `*_texture_channel` from those indices — **never assume one layout**, or
metallic and AO silently swap.

Matching is on whole tokens, so a parameter named `Normal` is not read as an `ORM`
map. When a packed map supplies a channel and the material exposes no explicit
multiplier, the exporter promotes that scalar to `1.0`: Godot computes
`roughness = scalar * texture[channel]`, so the `metallic` default of `0.0` would
otherwise cancel the map out entirely.

**glTF image URIs** are resolved relative to the `.gltf` file's own directory, so
they must spell out the real path (`"../textures/TX_Foo.png"`). A bare filename
only ever resolves as a sibling of the `.gltf`. Verified: Godot loads a
`../textures/` URI out of a sibling folder correctly.

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

One entry per **LightComponent**, not per light actor: a Blueprint prop with a
light component (lamps, ceiling fixtures, torches) exports too, and an actor
carrying several lights gets one entry each, named `"<actor>_<component>"`.
`godot_transform` is the **component's** world transform, so a light offset
inside a Blueprint lands where it belongs instead of on the actor origin.
`USkyLightComponent` does not derive from `ULightComponent`, so the sky light is
not caught by this scan — it stays in `sky_light`.

```json
{
    "name": "PointLight_2",
    "type": "directional" | "point" | "spot" | "rect",
    "godot_transform": { ... },
    "color": [r, g, b],                  // linear; UE's FColor is sRGB and is decoded
    "intensity": 5000.0,                 // as authored, in intensity_units
    "intensity_units": "lux" | "lumens" | "candela" | "ev" | "nits" | "unitless" | "unknown",
                                         // "lux" is always used for directional lights,
                                         // which have no IntensityUnits property in UE
    "intensity_candelas": 8.0 | null,    // local lights only; null for directional (lux)
    "inverse_squared_falloff": true,     // when false UE ignores intensity_units
    "godot_energy": 1.0,                 // pre-converted suggested Godot light_energy
    "temperature_kelvin": 6500.0 | null,
    "use_temperature": false,
    "cast_shadows": true,
    "attenuation_radius_m": 10.0,        // point/spot/rect; UE cm * 0.01
    "source_radius_m": 0.0,              // -> Godot light_size (soft shadows)
    "source_angle_deg": 0.5357 | null,   // directional only; -> light_angular_distance
    "shadow_distance_m": 400.0 | null,   // directional only; UE dynamic shadow distance
    "rect_size_m": [w, h] | null,        // rect only; the emissive panel
    "inner_cone_angle_deg": 0.0,         // spot only (half-angle)
    "outer_cone_angle_deg": 44.0,        // spot only (half-angle)
    "indirect_intensity": 1.0,           // -> light_indirect_energy
    "specular_scale": 1.0,               // multiplier against Godot's light_specular default
    "volumetric_scattering": 1.0,        // -> light_volumetric_fog_energy
    "distance_fade_begin_m": null,       // from UE MaxDrawDistance/MaxDistanceFadeRange
    "distance_fade_length_m": null,      // both null when UE never culls the light
    "mobility": "static" | "stationary" | "movable" | null,
    "visible": true                      // Visible AND NOT HiddenInGame AND AffectsWorld
}
```

Godot mapping: directional→`DirectionalLight3D`; point→`OmniLight3D`
(`omni_range = attenuation_radius_m`); spot→`SpotLight3D` (`spot_range`,
`spot_angle = clamp(outer_cone_angle_deg, 0.5, 89.9)`); rect→`OmniLight3D`
approximation with `set_meta("unreal_rect_light", true)` and
`set_meta("unreal_rect_size_m", Vector2)`. `light_energy = godot_energy *
options.light_energy_scale`. If `use_temperature`, apply Kelvin→RGB approximation
multiplied into color.

`specular_scale` is applied **relative** to Godot's own per-class default
(`light_specular` ships at 1.0 on directional lights and 0.5 on omni/spot), so a
UE light left at the default 1.0 keeps Godot's stock look. `mobility` is exported
for diagnostics and carried as metadata but deliberately **not** mapped to
`light_bake_mode`: Godot's `BAKE_STATIC` removes the light from real-time
rendering, so a project that never bakes a lightmap would simply lose it.

### `godot_energy` conversion

Two steps, both documented at length in export_environment.py.

**1. Normalise to candelas** using Unreal's own factors, taken from
`ULocalLightComponent::GetUnitsConversionFactor` and the per-component
`ComputeLightBrightness` overrides:

| units | → candelas |
|---|---|
| `candela` | `intensity` |
| `unitless` | `intensity * 16 / (100*100)` (UE's "legacy scale of 16") |
| `lumens` | `intensity / (2π(1-cos(outer_cone)))`; point: `/4π`; rect: `/π` |
| `ev` | `2**intensity` (EV100→luminance over an implied 1 m²) |
| `nits` | `intensity * emissive_area_m2` (capsule area; rect: `w*h`) |

When `inverse_squared_falloff` is false, Unreal ignores `intensity_units`
entirely and the unitless curve is used regardless of what the field says.

**2. Anchor to Godot's defaults**: local lights `candelas / 8.0`, directional
`lux / 10.0`. Both are the engines' shipped defaults — UE places local lights at
5000 unitless (exactly 8 cd) and directional lights at 10 lux, Godot places every
`Light3D` at `light_energy` 1.0 — so an untouched Unreal light imports as an
untouched Godot light. It is a calibration, not a photometric identity: two
tonemapped renderers cannot be matched by a constant, which is why
`options.light_energy_scale` exists. Raw `intensity` + `intensity_units` +
`intensity_candelas` are all stored so any of it can be re-derived.

Before this normalisation each unit had its own unrelated divisor, so the *same*
physical light imported up to 625× brighter or 12× dimmer depending only on which
unit the artist happened to author it in — and since UE's default unit is
`unitless`, the common case was the 625× one.

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
        "exposure_min_brightness": 3.0 | null,   // UE cd/m^2 adaptation range
        "exposure_max_brightness": 3.0 | null,   // min == max means LOCKED exposure
        "exposure_speed_up": 3.0 | null,
        "exposure_speed_down": 3.0 | null,
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

`tonemap_mode` is **always** set to `TONE_MAPPER_ACES`, graded volume or not:
Unreal never renders with a linear tonemapper, so Godot's linear default is
wrong for every import, not just graded ones. ACES over AGX because UE5's
default film curve is ACES-derived.

Exposure composes from three things, and they map to different Godot objects:

| UE | Godot |
| --- | --- |
| `exposure_bias` only | `tonemap_exposure = 2^bias` |
| `min_brightness == max_brightness` (locked/manual) | `tonemap_exposure = 0.18 / L * 2^bias`, no auto exposure |
| `min_brightness < max_brightness` (range) | `CameraAttributesPractical` with `auto_exposure_enabled`, `tonemap_exposure = 2^bias` |

0.18 is scene-referred middle grey, the average luminance UE's auto exposure
drives towards. In the range case sensitivity is **inverse** to target luminance,
so UE's *min* brightness becomes Godot's *max* sensitivity (`100.0 / L`), and
`speed_up` is scaled by `0.5/3.0` to line the two engines' defaults up.

Both consumers -- `addons/unreal_importer/import_environment.gd` and
`tscn_writer.py::_build_environment` -- must produce the same environment from the
same layout. They are separate implementations, so the constants are pinned
against each other by `tests/test_tscn_writer.py`.

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
    "sort_order": 0,               // -> Decal.sorting_offset (both engines: higher = on top)
    "visible": true,               // component visible AND not hidden in game
    "modulate": [r, g, b, a],      // -> Decal.modulate; DecalColor * material tint, opacity in alpha
    "fade_screen_size": 0.01,      // raw UE FadeScreenSize, for reference
    "distance_fade_begin_m": 143.0 | null,   // -> Decal.distance_fade_*; null = no fade
    "distance_fade_length_m": 47.6 | null,
    "material_name": "...", "material_path": "...",
    "textures": { "albedo": "T_Blood" | null, "normal": null, "orm": null, "emission": null,
                  "texture_paths": { "albedo": "/Game/Pack/T_Blood.T_Blood", ... } }
}
```

One entry per **DecalComponent**, not per `DecalActor`: decal components ride on
Blueprint props too. Actors carrying more than one get the component name appended to
`name` so Godot node names stay unique. `godot_transform` is built from the
**component's world transform**, which is what makes a component-relative offset survive.

`modulate` is where a decal's colour lives when it is not in the albedo texture — the
usual setup for tinted blood/rust/paint instances. It is the component's `DecalColor`
times the material's albedo tint, with any `Opacity`-style scalar parameter folded into
alpha. Channels may legitimately exceed 1.0 (packs author dark albedo and scale it up),
so only the negative side is clamped. Caveat: UE's `DecalColor` only reaches the shader
when the material samples the Decal Color node, but it defaults to white, so it only ever
moves the result when someone set it deliberately.

`distance_fade_*` converts UE's screen-size fade, which Godot has no equivalent for: an
object of world radius `r` covers a screen fraction `r / (d * tan(fov/2))`, inverted at
Godot's default 75° camera FOV to the distance UE would have dropped the decal. `r` comes
from the **lateral** extent (`size_m[0]`/`size_m[2]` times their scale) — projection depth
does not affect on-screen size. Both fields are `null` when `fade_screen_size <= 0` or the
result exceeds 100 km (indistinguishable from "never fades").

Deliberately **not** mapped: Godot's `upper_fade`/`lower_fade` are left at their engine
defaults. UE clips hard at the decal box, so a faithful import would zero them, but the
soft falloff is what keeps projections onto curved geometry from showing a hard seam.

**`orm` is only ever set when the source map is genuinely ORM-ordered**
(R=AO, G=roughness, B=metallic — the `ORM`/`ARM` row of the packed-map table
above). Godot's `Decal.TEXTURE_ORM` has no per-channel selectors, unlike
`BaseMaterial3D`, so an `RMA`-ordered map or a standalone greyscale roughness map
cannot be bound at all: the latter would read back as AO=roughness (backwards) and
metallic=roughness, rendering a rough concrete decal as chrome. Anything else is
left `null` and the exporter logs which decal it skipped.

UE `decal_size` is half-size cm in (X=projection depth, Y=width, Z=height); UE decals
project along **-X** (local). The exporter must fold whatever axis fix-up is needed into
`godot_transform` so the Godot `Decal` (projects along **-Y**) matches, and set
`size_m = [Y*2, X*2, Z*2] * 0.01`. Texture names refer to files in `textures/`.

Because that fix-up re-labels the node's local Y and Z axes, `godot_transform.scale` for a
decal is **conjugated by it too**: its Y and Z components are swapped relative to the
standard `[usy, usz, usx]`, giving `[usy, usx, usz]`. The rotation and the scale must stay
in the same frame — Godot renders the half-extent along local axis `j` as
`0.5 * size_m[j] * basis_column[j]`, so a scale left in the un-fixed frame trades the
decal's projection depth for its height. Guarded by `test_math.py` section 11 and the
`Decal_Skewed` fixture in the Godot harness.

## landscapes

```json
{
    "name": "Landscape_0",
    "godot_transform": { ... },              // landscape actor transform
    "heightmap_file": "terrain/Landscape_0_height.exr",   // relative to JSON dir; float EXR (or PNG, see below)
    "heightmap_resolution": [513, 513],      // the size of the file that was ACTUALLY written
    "world_size_m": [xz_size_x, xz_size_z],  // footprint in Godot meters
    "world_center_m": [x, y, z],             // same box as ue_bounds, in Godot meters
    "height_range_m": [min_m, max_m],        // world-space height range the heightmap spans
    "height_encoding": "normalized",         // how pixel values map to meters
    "vertex_spacing_m": 1.0,                 // Unreal's per-quad scale, in meters
    "ue_bounds": {                           // the landscape's world bounds, Unreal cm
        "center": [x, y, z],
        "extent": [x, y, z]
    },
    "layers": [
        {
            "name": "Grass",
            "weightmap_file": "terrain/Landscape_0_weight_Grass.exr",  // omitted when unpainted
            "debug_color": [r, g, b]         // LandscapeLayerInfoObject.layer_usage_debug_color, linear
        }
    ],
    "material": {                            // the landscape material itself
        "name": "MI_landscape",
        "path": "/Game/.../MI_landscape.MI_landscape",
        "textures": [                        // exported to textures/ like any other
            { "parameter": "", "texture": "T_ash_01_basecolor", "role": "albedo" }
        ]
    }
}
```

`material.textures` exists because the level exporter only walks MESH materials
and decal materials -- a landscape's material hangs off the actor and is
referenced by nothing else, so before this the ground textures never left Unreal
and there was nothing in the Godot project to texture the terrain with. `role` is
`albedo` / `normal` / `roughness` / `metallic` / `ao` / `emission` / `packed` /
`unknown`, classified from the parameter name first and the texture ASSET name
second (`resolve_texture_role`), with the `_texture` slot suffix stripped. There
is deliberately NO layer -> texture mapping (see below).

### Height encoding

`height_encoding` is always `"normalized"`: **pixel values are relative**. The consumer
normalizes the image by its own min/max and rescales the result into `height_range_m`.
The reason is that the three sources the exporter can draw a heightmap from all encode
differently (an engine-internal packing that varies by UE version, versus absolute world
centimetres from the CPU fallback), and normalizing makes all of them correct. The
exporter's contract is therefore that `height_range_m` describes the *image it wrote* —
on the CPU path it is set from the heights actually measured, not from the actor bounds,
which are padded.

`ue_bounds` is the placement contract, and it is what both consumers use: vertices are
reconstructed in Unreal world space from it and converted per-vertex, so the axis mapping
is right by construction rather than by an extra transform:

```
image U (+X) follows Unreal +X  ->  Godot -Z
image V (+Y) follows Unreal +Y  ->  Godot +X
```

`godot_transform` is the landscape *actor's* transform. It is informational: `ue_bounds`
is already in world space, so the rebuilt terrain must NOT be placed at it as well.

### Why the heightmap may not be an EXR

`RenderingLibrary.export_render_target` picks the file format from the render-target
format and ignores the requested extension — an RGBA32F target asked for `.exr` comes out
as PNG bytes (measured, UE 5.7.4). The exporter prefers a real float EXR through
`ImageWriteBlueprintLibrary` and, when it has to fall back, sniffs the magic number and
renames the file to match its true content. `heightmap_file` always names the file that
is actually on disk. Consumers should still decode by content, since older exports exist.

### Deliberately not mapped

* **The layer -> texture mapping.** Unreal's terrain look lives in a layer-blend material
  graph, and which texture belongs to which paint layer is not readable from Python
  (layers are frequently named `1`, `2`, `3` while the textures are named after the
  material). The exporter ships each layer's weightmap plus its `debug_color`, AND every
  texture the landscape material references (`material.textures`, with the base-colour
  ones flagged `role: "albedo"` as the candidates). The Godot addon blends the tints in a
  splat shader with one texture slot per layer and reports the candidate names, so the
  final pairing is a drag-and-drop rather than a rebuild -- but it is the user's call,
  not a guess the exporter makes.
* **Landscape holes / the visibility layer**, **landscape splines**, **grass types**, and
  **per-layer physical materials.** None are exported.
* **More than four paint layers.** The Godot splat material packs weights into one RGBA
  texture; layers beyond the fourth are listed in the schema and written to `terrain/`
  but not blended.

### Godot side

Prefer `Terrain3D` when `ClassDB.class_exists("Terrain3D")` (Terrain3D 1.x's
`Terrain3D.data` and 0.9.x's `Terrain3DStorage` are both handled), otherwise the
plugin-free fallback: a `MeshInstance3D` with an ArrayMesh generated from the heightmap,
a splat `ShaderMaterial`, and `StaticBody3D` + `ConcavePolygonShape3D` collision. The
fallback MUST exist so the tool works with no third-party plugins.

Note for Terrain3D: `vertex_spacing` has to be derived from the loaded image
(`world_size_m / (heightmap_resolution - 1)`), NOT from `vertex_spacing_m`. The exported
heightmap is routinely coarser than the landscape, and using the raw Unreal quad size
shrinks a 4032 m landscape to 257 m.

The `.tscn` writer emits a placeholder `Node3D` per landscape carrying all of the above as
`metadata/*`, and builds no geometry — decoding a float EXR and generating a mesh is not
something a text scene can express. Run the addon's dock import with **Terrain** enabled
to build the real terrain.

## foliage

```json
{
    "name": "Foliage_SM_Grass_01",           // unique per (foliage actor, mesh)
    "mesh_key": "SM_Grass_01",               // key into meshes library / glTF filename base
    "mesh_name": "SM_Grass_01",
    "instance_count": 1234,
    "source": "foliage" | "ism" | "hism",    // component origin
    "visible": true,                         // Visible AND NOT HiddenInGame
    "cast_shadow": true,
    "cull_begin_m": 30.0 | null,             // UE InstanceStartCullDistance (cm * 0.01)
    "cull_end_m": 50.0 | null,               // UE InstanceEndCullDistance; both null = never culled
    "material_overrides": [ { "slot_index": 0, "material_name": "...",
                              "material_path": "...", "parameters": { ... } } ],
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
Components flagged `bIsEditorOnly` are skipped entirely — they never ship with the
game and must not ship with the export.

**When foliage export is switched off**, that exclusion is lifted and each
instanced component expands into `actors[].components` as **one placement per
instance**, named `"<component>_Inst<n>"`, each carrying its own instance world
transform. Exporting one placement per *component* instead would collapse an
entire painted field onto a single mesh at the component origin. The expansion is
exact but unbatched, so a component past ~1000 instances logs a warning pointing
back at the foliage option, which rebuilds it as a single MultiMesh.

### Cull distances

UE's per-instance cull pair maps onto `GeometryInstance3D`'s visibility range:
`visibility_range_end = cull_end_m`, and when a start distance is set,
`visibility_range_end_margin = cull_end_m - cull_begin_m` with
`visibility_range_fade_mode = VISIBILITY_RANGE_FADE_SELF`. UE treats
`InstanceEndCullDistance == 0` as "never cull" regardless of the start distance,
so both fields are then `null` and no visibility range is written. With an end but
no usable start distance UE pops the instances out, so the fade mode is left
DISABLED and the pop is reproduced rather than smoothed over.

`cast_shadow` matters more than it looks: grass and undergrowth are routinely
authored shadowless in Unreal because per-blade shadows are ruinous, so importing
them shadow-casting is both a visual and a performance regression.

Deliberately **not** mapped: `bounds_scale`, `min_lod`,
`world_position_offset_disable_distance` and `receives_decals` have no faithful
Godot counterpart. Per-instance custom data is **unreachable**, not merely
skipped — UE 5.7 exposes `num_custom_data_floats` but no getter for the values
(verified by probing the component API), so Godot's `MultiMesh.use_custom_data`
channel cannot be filled from an export.

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
