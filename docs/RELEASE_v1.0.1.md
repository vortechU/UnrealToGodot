# Unreal to Godot v1.0.1

Closer lighting and decals, and hanging cables.

## Includes
- **`UnrealToGodot/`** - Unreal Engine plugin (Python exporter with a GUI). Tested on Unreal 5.8.
- **`addons/unreal_importer/`** - Godot addon (layout importer dock). Tested on Godot 4.7.2.

## What's new since v1.0.0
- **Cables:** `CableComponent` wires are exported where they hang and rebuilt as meshes. They used to be missing.
- **Decals:** no longer faint when they sit off-centre on a wall, and their material's brightness and hue shift come across (Unreal 5.8).
- **Lumen levels:** bounced light in streets and alleys is closer to Unreal.

The direct `.tscn` export skips cables and hue shifts, and warns about both.

## Setup
- **Unreal**: Copy `UnrealToGodot/` into your project's `Plugins/` folder. Enable the plugin, Python Editor Script, Editor Scripting Utilities, and glTF Exporter.
- **Godot**: Copy `addons/unreal_importer/` into your project's `addons/` folder. Enable it in Project Settings > Plugins.

> **Upgrading from v1.0.0?** Replace both folders, then re-export the level layout and re-import. Meshes do not need re-exporting.
