# Unreal Engine to Godot Asset & Level Exporter

A robust toolset for migrating level layouts, static/skeletal meshes, collision shapes, and PBR materials from **Unreal Engine** to **Godot Engine 4.x**. 

This repository contains two main components:
1. **Unreal to Godot Exporter (Unreal Plugin)**: A Python-based plugin that runs inside Unreal Engine, providing a thread-safe Tkinter GUI to batch export meshes to glTF and level layouts to JSON.
2. **Unreal Layout Importer (Godot Addon)**: A GDScript addon that parses the JSON layout and instances the exported glTF models at their correct remapped coordinate transforms, automatically generating physics collisions and remapping PBR materials.

---

## Key Features

- 🔄 **Coordinate & Scale Conversion**: Automatically transforms Unreal's left-handed Z-up (centimeter) coordinate system into Godot's right-handed Y-up (meter) system.
- 📦 **Static Mesh glTF Export**: Batch exports selected meshes (or all meshes used in the level) from Unreal directly into glTF format, with collision-safe filenames when asset packs reuse mesh names.
- 🧱 **Collision Shape Extraction**: Extracts Unreal collision primitives (Boxes, Spheres, Capsules, and Convex Hulls) and auto-generates corresponding Godot `CollisionShape3D` nodes attached to `StaticBody3D` nodes.
- 🎨 **PBR Material & Texture Binding**: Translates Unreal material parameters (albedo, roughness, metallic, tiling) and binds exported textures to Godot `StandardMaterial3D` resources (case-insensitive file matching).
- 🌿 **Blueprint/Complex Actor Support**: Supports multi-mesh/multi-component Blueprint actors by instancing them as a parent `Node3D` with relative component offsets.
- 💡 **Lighting & Post-Processing**: Converts `DirectionalLight`/`PointLight`/`SpotLight`/`RectLight` into `DirectionalLight3D`/`OmniLight3D`/`SpotLight3D`, and `PostProcessVolume` + height fog + sky into a Godot `WorldEnvironment` (bloom, SSAO, exposure, fog, sky, color temperature).
- 🩹 **Decals**: Exports `DeferredDecal` actors as Godot `Decal` nodes with matching size, textures and sorting priority.
- ⛰️ **Landscape / Terrain Migration**: Exports Landscape heightmaps and paint-layer splatmaps as float EXR images; rebuilds them via the Terrain3D plugin when installed, or a plugin-free mesh terrain (with collision) otherwise.
- 🌱 **Foliage as MultiMesh**: Painted foliage, HISM and ISM instances are exported as packed transform arrays and rebuilt as `MultiMeshInstance3D` nodes — thousands of instances stay performant.
- 🗺️ **Navigation Volumes**: `NavMeshBoundsVolume` actors become `NavigationRegion3D` nodes with matching agent settings (optionally auto-baked on import).
- 🏷️ **Tags & Metadata**: Actor tags, component tags and (best-effort) Blueprint variables land on Godot nodes as metadata, readable via `get_meta()`.
- 🎬 **Direct `.tscn` Generation**: Optionally writes a ready-to-open Godot 4 scene file straight from Unreal — no importer dock run required.
- 🎛️ **Fully Customizable**: Every feature has its own toggle on both the Unreal export GUI and the Godot import dock, with persisted settings and tooltips.
- ⚡ **Thread-Safe Architecture**: Uses a queue-based asynchronous architecture for the Tkinter GUI to prevent Unreal Engine editor lock-ups.

---

## Project Structure

```
.
├── addons/
│   └── unreal_importer/                 # Godot Importer Addon
│       ├── import_unreal_layout.gd      # Core import orchestrator
│       ├── import_common.gd             # Shared helpers (transforms, file lookup)
│       ├── import_environment.gd        # Lights, WorldEnvironment, decals
│       ├── import_terrain.gd            # Landscape rebuild (Terrain3D / mesh fallback)
│       ├── import_foliage.gd            # MultiMesh foliage rebuild
│       ├── import_gameplay.gd           # Navigation regions + node metadata
│       ├── importer_dock.gd             # Editor dock GUI (feature toggles)
│       ├── plugin.cfg                   # Addon configuration
│       └── unreal_importer.gd           # Addon activation script
├── UnrealToGodot/                       # Unreal Engine Plugin
│   ├── Content/
│   │   └── Python/
│   │       ├── export_level_to_json.py  # Level parser & JSON exporter (orchestrator)
│   │       ├── export_static_meshes_to_gltf.py # glTF exporter script
│   │       ├── export_environment.py    # Lights, post-process, fog, sky, decals
│   │       ├── export_landscape.py      # Landscape heightmaps & splatmaps
│   │       ├── export_foliage.py        # Foliage/ISM/HISM packed instances
│   │       ├── export_gameplay.py       # Nav volumes, tags, Blueprint variables
│   │       ├── tscn_writer.py           # Direct Godot .tscn scene generation
│   │       ├── ue2g_common.py           # Shared conversion math
│   │       ├── init_unreal.py           # Startup script (registers menu entry)
│   │       └── unreal_to_godot_gui.py   # Thread-safe Tkinter GUI
│   └── UnrealToGodot.uplugin            # Unreal Plugin configuration
└── docs/
    └── SCHEMA_V2.md                     # Layout JSON schema & module contracts
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
3. Set both paths: the glTF target folder (defaults to `Saved/Exports/GLTF`) and the layout JSON path.
4. **Choose Level Export Features** (all optional, all persisted between sessions):
   - **Lights & Post-Process**, **Decals**, **Landscape / Terrain**, **Foliage & Instances**, **Navigation Volumes**, **Tags & Metadata** — untick anything you don't want in the export.
   - **Generate Godot .tscn scene directly**: writes a ready-to-open scene file into your Godot project (requires the Godot Project Path in the *Godot Engine Integration* section).
5. Click **Export Everything (Meshes + Layout)**.

That last button is the one to use. The layout names meshes the mesh export has
to have written, so exporting the two halves separately is how they drift out of
sync — and a mismatch shows up much later in Godot as a missing texture or a
`MISSING_` placeholder, far from the click that caused it. Exporting together
also encodes each texture once instead of twice, which on a real level is the
difference between three minutes and a fraction of a second.

The individual actions are still there when you want them — **Batch Export All
Level Meshes**, **Export Selected Meshes** (for a Content Browser selection),
and **Export Level Layout only**. If you use them, run both halves before
importing.

Heightmaps/splatmaps go to a sibling `terrain/` folder, textures to `textures/`.

> **Keeping textures light**: Unreal exports source art, so a real level arrives
> as several GB of 4K PNGs — enough to make Godot's importer run out of memory
> and crash partway through. Texture sizing lives in the Godot importer dock,
> because Unreal's Python API has no way to resize source art
> ([why](docs/texture-sizing.md)):
>
> - **Texture size limit** (e.g. `1024`) caps what Godot imports, for textures
>   already in the project as well as new ones.
> - **Also shrink texture files on disk** rewrites the oversized PNGs at that
>   size, which is what reclaims the space — 3.1 GB of 4K exports drops to a few
>   hundred MB. It is destructive: the full-resolution art only exists back in
>   Unreal, so raising the limit later means exporting again.

### Step 2: Importing into Godot

1. Open or create a 3D scene in Godot to serve as the active root level.
2. In the **Unreal to Godot Importer** dock (bottom-right panel):
   - **Layout JSON File**: Point to the exported JSON file.
   - **glTF Models Folder**: Select the folder where you exported the glTF meshes.
   - **Textures Folder**: Select the folder containing exported textures (if any).
3. (Optional) Check **GDScript Coordinate Swap** if you want to recalculate the transform conversion entirely in Godot (defaults to using pre-calculated matrices from the JSON, which is recommended).
4. **Choose Import Features**: toggle Lights, World Environment, Decals, Terrain (with a mode selector: Auto / Terrain3D / HTerrain / Mesh fallback), Foliage, Navigation Regions (with optional immediate baking), and Tags & Metadata. Adjust **Light energy scale** if the imported level looks too dark or bright.
5. Click **Import Unreal Level Layout**.
6. The importer will populate the active scene with instanced models, collisions, materials, lights, environment, decals, terrain, foliage and navigation regions.

### Direct .tscn workflow (no importer run needed)

If you enabled **Generate Godot .tscn scene directly** on the Unreal side, just open the generated `.tscn` in Godot. Two follow-ups:
- Click **Bind Foliage Meshes (.tscn scenes)** in the importer dock once — `.tscn` files cannot reference meshes inside glTF scenes, so foliage MultiMeshes are bound in-place from their `source_model` metadata.
- Landscapes appear as placeholder nodes carrying heightmap metadata; run the dock import with only **Terrain** enabled to build them, or use your terrain plugin's import tools with the exported `terrain/` files.

---

## Troubleshooting

### Start here: read the reports

Both halves of the toolchain describe themselves in writing, automatically.
There is nothing to run and nothing to remember.

**After an export**, the exporter audits its own output and writes
`ue2g_report.txt` next to it. Any problem is also surfaced in the GUI status
bar and the Unreal output log, prefixed `EXPORT CHECK:`. It catches the things
that actually go wrong: stale glTFs left from an older export, texture URIs
pointing at files that don't exist, meshes the layout references but that were
never exported, material scalars outside Godot's valid range, and texture sets
big enough to crash Godot's importer.

**After an import**, the importer writes `res://ue2g_import_report.txt`,
listing how many material slots got each map bound, which textures were
missing, and what failed — including when the import dies early.

Those two files are self-contained: they describe a run without anyone having
to remember what they saw.

To re-check an export folder later, run the same audit from a **terminal**
(PowerShell, cmd, bash — *not* Unreal's Python console, which would treat the
command as a script filename):

```bash
python tools/ue2g_diagnose.py <export_folder>
# optionally cross-check the Godot side too:
python tools/ue2g_diagnose.py <export_folder> --godot-project <godot_project>
```

It needs only Python — no Unreal, no Godot — so it also runs on a machine that
has nothing but the exported files.

### Common problems

- **Errors like `Can't open file from path 'res://models/<Material>_<Mesh>_BaseColor.png'`**: those filenames are produced only by material *baking*, which the exporter disables. Their presence means the `.gltf` files are left over from an older export. Clear the `models/` folder, re-run the mesh export (a **separate action** from the layout export — running one does not re-run the other), and delete the Godot project's `.godot/` cache, which otherwise keeps serving the old import.
- **Godot crashes, hangs, or half-imports a large level**: a full map can export several GB of 4K textures, and Godot's WebP packer runs out of memory and segfaults partway through, leaving a half-populated `.godot` cache that looks like a toolchain bug. Set **Max Texture Resolution** (e.g. 2048) in the exporter. `tools/ue2g_diagnose.py` warns before you hit this.
- **GUI window doesn't appear or locks up**: Ensure Tkinter is installed in your python environment. On Windows, standard Unreal installations include Tkinter automatically.
- **Missing Meshes Warning**: If a mesh name doesn't match the glTF filename, the importer will place a `Marker3D` placeholder in the scene tree labeled `_MISSING_<MeshName>`. Verify your glTF export folder. Note: when an asset pack contains two different meshes with the same name, exports get a deterministic `_<hash>` suffix — re-export both meshes and layout together so the names line up.
- **Textures aren't loading**: Textures referenced by materials and decals are auto-exported to a `textures/` folder next to your export. File matching is case-insensitive; supported extensions are `.png`, `.tga`, `.jpg`, `.jpeg`, `.dds`, `.exr` and `.webp`.
- **Imported level too dark/bright**: Unreal and Godot use different light intensity models (lumens/candela/lux vs. energy). Adjust **Light energy scale** in the importer dock, or tweak individual lights — the raw Unreal intensity and units are preserved in the JSON.
- **Terrain looks flat or missing**: Terrain rebuild needs the exported `terrain/*.exr` files next to the layout JSON (or copied into your Godot project by auto-transfer). Without a terrain plugin, the mesh fallback caps detail at a 256×256 grid; install Terrain3D for full resolution.
- **Foliage invisible after opening a generated .tscn**: run **Bind Foliage Meshes** from the importer dock (see the direct .tscn workflow above).
- **Blueprint variables missing from metadata**: the stock Unreal Python API only exposes Blueprint variables on some engine versions; actor/component tags always export.

## Schema & Extending

The layout JSON format and per-module contracts are documented in [docs/SCHEMA_V2.md](docs/SCHEMA_V2.md). Each feature is an isolated module pair (Python exporter + GDScript importer) — deleting a module file simply disables that feature, and new features can follow the same contract.
