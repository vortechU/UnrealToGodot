# Unreal Engine to Godot Asset & Level Exporter

A robust toolset for migrating level layouts, static/skeletal meshes, collision shapes, and PBR materials from **Unreal Engine** to **Godot Engine 4.x**. 

This repository contains two main components:
1. **Unreal to Godot Exporter (Unreal Plugin)**: A Python-based plugin that runs inside Unreal Engine, providing a thread-safe Tkinter GUI to batch export meshes to glTF and level layouts to JSON.
2. **Unreal Layout Importer (Godot Addon)**: A GDScript addon that parses the JSON layout and instances the exported glTF models at their correct remapped coordinate transforms, automatically generating physics collisions and remapping PBR materials.

---

## Key Features

- 🔄 **Coordinate & Scale Conversion**: Automatically transforms Unreal's left-handed Z-up (centimeter) coordinate system into Godot's right-handed Y-up (meter) system.
- 📦 **Static Mesh glTF Export**: Batch exports selected meshes (or all meshes used in the level) from Unreal directly into glTF format.
- 🧱 **Collision Shape Extraction**: Extracts Unreal collision primitives (Boxes, Spheres, Capsules, and Convex Hulls) and auto-generates corresponding Godot `CollisionShape3D` nodes attached to `StaticBody3D` nodes.
- 🎨 **PBR Material & Texture Binding**: Translates Unreal material parameters (albedo, roughness, metallic, tiling) and binds exported textures to Godot `StandardMaterial3D` resources.
- 🌿 **Blueprint/Complex Actor Support**: Supports multi-mesh/multi-component Blueprint actors by instancing them as a parent `Node3D` with relative component offsets.
- ⚡ **Thread-Safe Architecture**: Uses a queue-based asynchronous architecture for the Tkinter GUI to prevent Unreal Engine editor lock-ups.

---

## Project Structure

```
.
├── addons/
│   └── unreal_importer/                 # Godot Importer Addon
│       ├── import_unreal_layout.gd      # Core import execution logic
│       ├── importer_dock.gd             # Editor dock GUI implementation
│       ├── plugin.cfg                   # Addon configuration
│       └── unreal_importer.gd           # Addon activation script
├── UnrealToGodot/                       # Unreal Engine Plugin
│   ├── Content/
│   │   └── Python/
│   │       ├── export_level_to_json.py  # Level parser & JSON exporter
│   │       ├── export_static_meshes_to_gltf.py # glTF exporter script
│   │       ├── init_unreal.py           # Startup script (registers menu entry)
│   │       └── unreal_to_godot_gui.py   # Thread-safe Tkinter GUI
│   └── UnrealToGodot.uplugin            # Unreal Plugin configuration
└── import_unreal_layout.gd              # Root copy of Godot Importer Script (for standalone run)
```

---

## Installation & Setup

### 1. Unreal Engine Setup

1. Copy the `UnrealToGodot` folder into your Unreal Engine project's `Plugins` directory (create a `Plugins` folder in your project root if it doesn't exist).
2. Open your Unreal Project.
3. Go to **Edit > Plugins** and search for:
   - **Unreal to Godot Exporter** (Enable it)
   - **Python Editor Script Plugin** (Built-in, ensure it is enabled)
   - **Editor Scripting Utilities** (Built-in, ensure it is enabled)
   - **glTF Exporter** (Built-in, ensure it is enabled)
4. Restart the Unreal Editor.
5. You will see a new menu entry under **Window > Unreal to Godot Exporter**. Clicking this opens the Python GUI.

### 2. Godot Engine Setup

1. Copy the `addons/unreal_importer` folder into your Godot project's `addons` directory.
2. Open your Godot Project.
3. Go to **Project > Project Settings > Plugins** and enable **Unreal Engine Layout Importer**.
4. A new tab named **Unreal to Godot Importer** will appear in the bottom-right editor panel (under the Inspector tab).

---

## How to Use

### Step 1: Exporting from Unreal Engine

1. Open your level in Unreal Engine.
2. Open the **Unreal to Godot Exporter** window from the **Window** menu.
3. **Export Static Meshes**:
   - Choose a target folder to save the glTF models (e.g., `<GodotProject>/models/`).
   - Click **Batch Export All Level Meshes** (exports all unique meshes used in the active level) or select specific meshes in the Content Browser and click **Export Selected Meshes**.
4. **Export Level Layout**:
   - Select a target JSON file path (e.g., `<GodotProject>/level_layout.json`).
   - Click **Export Level Layout**.

### Step 2: Importing into Godot

1. Open or create a 3D scene in Godot to serve as the active root level.
2. In the **Unreal to Godot Importer** dock (bottom-right panel):
   - **Layout JSON File**: Point to the exported JSON file.
   - **glTF Models Folder**: Select the folder where you exported the glTF meshes.
   - **Textures Folder**: Select the folder containing exported textures (if any).
3. (Optional) Check **GDScript Coordinate Swap** if you want to recalculate the transform conversion entirely in Godot (defaults to using pre-calculated matrices from the JSON, which is recommended).
4. Click **Import Unreal Level Layout**.
5. The importer will populate the active scene with instanced models, collisions, and materials.

---

## Troubleshooting

- **GUI window doesn't appear or locks up**: Ensure Tkinter is installed in your python environment. On Windows, standard Unreal installations include Tkinter automatically.
- **Missing Meshes Warning**: If a mesh name doesn't match the glTF filename, the importer will place a `Marker3D` placeholder in the scene tree labeled `_MISSING_<MeshName>`. Verify your glTF export folder.
- **Textures aren't loading**: Make sure you have exported your textures from Unreal Engine using standard texture bulk exports and placed them inside your designated Godot textures folder. Supported extensions are `.png`, `.tga`, `.jpg`, `.jpeg`, and `.dds`.
