# Unreal to Godot Toolchain Guidelines

## 1. Godot Editor UI Guidelines
*   **Filesystem Access**: Always use `FileDialog` instead of `EditorFileDialog` for asset or layout file pickers, and set `access = FileDialog.ACCESS_FILESYSTEM`. This allows the importer to read layouts exported to locations outside the Godot `res://` directory.
*   **Box Container Spacing**: Use `add_theme_constant_override("separation", size)` instead of `set_spacing` to adjust layout margins on VBoxContainer or HBoxContainer controls.

## 2. Unreal Engine Python Guidelines
*   **glTF Exporter Properties**: When calling `unreal.GLTFExporter.export_to_gltf()`, always configure `GLTFExportOptions` to disable material and animation sequence exporting:
    *   `export_options.set_editor_property("export_materials", False)`
    *   `export_options.set_editor_property("export_animation_sequences", False)`
    This prevents crashes and hangs on complex material bakes or heavy skeletal rigs.
*   **Texture Auto-Exporting**: Programmatically export referenced textures during mesh/layout scans using `unreal.AssetExportTask` and `unreal.TextureExporterPNG()`. Run these tasks via `unreal.Exporter.run_asset_export_tasks()`.
*   **Tkinter UI Focus**: Keep the Tkinter window floating topmost at all times (`self.root.attributes("-topmost", True)`) to prevent the window from falling behind when the user clicks inside the viewport.
