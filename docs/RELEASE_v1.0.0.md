# Unreal to Godot v1.0.0

The first stable release.

## Includes
- **`UnrealToGodot/`** - Unreal Engine plugin (Python exporter with a GUI). Tested on Unreal 5.7 and 5.8.
- **`addons/unreal_importer/`** - Godot addon (layout importer dock). Tested on Godot 4.7.1 and 4.7.2.

## What's new since v0.4

**Materials**
- On Unreal 5.8 the exporter reads the material graph to see which texture channel drives what, instead of guessing from names.
- Blend modes, alpha cutout, two-sided and emissive settings come across. DirectX normal maps are flipped for Godot.
- Vertex-painted layers (puddles, moss) become a Godot shader with the paint intact (Unreal 5.8 with Geometry Script enabled).
- Float textures export as `.exr`. Materials can be saved as `.tres` files, and material overrides survive saving the scene.

**Level**
- New: spline meshes, reflection probes, cameras, ambient sounds, LOD chains, skeletal physics as ragdolls, and Outliner folders.
- Static mesh visibility, shadow and draw-distance settings are kept.
- Foliage with a cull distance is split into chunks, so it no longer vanishes up close.

**Lighting**
- The sky is captured on export. Lumen levels import with SDFGI and screen-space reflections, and fog matches Unreal's density and start distance.

**Decals**
- Fixed orientation, opacity, missing textures and atlas crops. Very thin decals no longer shred on uneven surfaces.

**Workflow**
- Re-import into an existing scene and keep your own nodes, scripts, groups and signal connections.
- Repeated Blueprints can become shared prefab scenes.
- Both sides write a report. The exporter warns about unloaded sublevels and World Partition cells.
- Many fixes across the exporter, the addon and the direct `.tscn` export, with regression tests.

## Setup
- **Unreal**: Copy `UnrealToGodot/` into your project's `Plugins/` folder. Enable the plugin, Python Editor Script, Editor Scripting Utilities, and glTF Exporter.
- **Godot**: Copy `addons/unreal_importer/` into your project's `addons/` folder. Enable it in Project Settings > Plugins.

> **Upgrading from v0.4?** Replace both folders, then re-export and re-import into a fresh scene. Scenes imported with v0.4 have no actor IDs, so they cannot be updated in place.
