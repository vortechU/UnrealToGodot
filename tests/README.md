# Tests

Both engines can be driven headlessly, so most of this toolchain is testable
without opening an editor.

## Run

```bash
# Pure-Python: conversion math, layout composition, .tscn writer
python tests/run_tests.py

# Also drive the real GDScript importer inside headless Godot
python tests/run_tests.py --godot "V:/Apps/Godot_v4.6.3/Godot_v4.6.3.exe"
```

## The real-data loop

The tests above use synthetic fixtures, so they pass on data that never
exercises a real material graph. To check the toolchain against an actual map,
export once from Unreal and then re-run the Godot half as often as you like:

```bash
# SLOW (minutes) -- run once per Unreal-side change
UnrealEditor-Cmd.exe <project>.uproject -run=pythonscript \
  -script="tests/real_export.py" -unattended -nopause -nosplash \
  -MapPath="/Game/.../Maps/Demonstration" -OutDir="C:/scratch/real_export_out"

# FAST (~1s import) -- run after every Godot-side change
python tests/run_real_check.py C:/scratch/real_export_out \
  --godot "V:/Apps/Godot_v4.6.3/Godot_v4.6.3.exe"
```

`run_real_check.py` builds a throwaway project from the export, imports it, and
prints how many material slots got each map bound, how many packed maps resolved
to the right channels, and how many meshes went missing.

Two traps this harness had to work around, both of which silently report
"everything is fine" when hit:

* **The inspector must wait for the editor's asset scan.** A plugin's
  `_enter_tree()` runs while imports are still queued on a background thread, so
  `load()` on a not-yet-imported `.gltf` returns `null` and every mesh vanishes.
  The report then blames the importer for a race. `_wait_for_import()` polls
  `EditorInterface.get_resource_filesystem().is_scanning()` first.
* **A real texture set will crash `--import`.** A full map exports gigabytes of
  4K RGBA PNGs; Godot's WebP packer exhausts memory and segfaults
  (`webp_common.cpp:110`, then `alloc_static: "mem" is null`), leaving a
  half-populated `.godot` cache that the next run happily trusts. The harness
  sets `importer_defaults/texture` with `process/size_limit` — it is testing
  that textures are *wired up*, not how they look.
* **`--import` runs your editor plugins too.** It is not a quiet asset pass: it
  boots the editor and enables `[editor_plugins]`, so a test plugin's
  `_enter_tree()` fires there *and* in the following `-e` run. `test_texture_shrink.py`
  hit this — the `--import` pass did the work and quit, then `-e` ran the same
  plugin against already-processed files, found nothing to do, and overwrote the
  result with a zero. A working feature looked broken. Either skip the separate
  `--import` (the `-e` pass imports anyway) or make the plugin idempotent, and
  assert the fixture is in the state you think it is before acting on it.

## Landscape export needs a GPU

`export_level_to_json` with landscape enabled calls
`LandscapeExportHeightmapToRenderTarget`, which asserts on a null RenderTarget
(`Canvas.cpp:880`) under the `pythonscript` commandlet. Pass
`options={"landscape": False}` when exporting headlessly. This is a limit of the
headless test method, not of the exporter.

The cause is `FApp::CanEverRender()` being false in a commandlet, and
`-AllowCommandletRendering` flips it — that is what lets
`probe_texture_export_routes.py` drive real render targets headlessly. It is
plausible that the same flag lifts this landscape limitation, but that has not
been tried; the flag is only known to work for the texture probe.

## What each test covers

| File | Covers |
| --- | --- |
| `test_math.py` | Coordinate conversion in `ue2g_common.py`: `C·R·Cᵀ`, the packed 12-float foliage basis, `matrix_to_quat` (all four branches), the decal fix-up quaternion, export-name collision hashing. Stubs the `unreal` module. |
| `test_layout.py` | Component placement in `tscn_writer.py`, including an explicit reproduction of the double-transform bug and `affine_inverse` round-trips. |
| `test_tscn_writer.py` | End-to-end `.tscn` generation: resource/node structure, `load_steps` accounting, dangling-reference checks, and node placement. |
| `godot_harness/` | Builds a throwaway Godot project (minimal glTF, fixture `level_layout.json`) and runs `import_unreal_layout.gd` for real, asserting where nodes land. |
| `test_texture_shrink.py` | Drives `texture_import_limit.shrink_source_files` in headless Godot against real 4K exports, then checks the pixels with PIL: capped dimensions, and no channel collapsed to a constant. The normal map's blue channel is the case that matters — see `docs/texture-sizing.md`. |
| `probe_unreal_api.py` | Run *inside* Unreal to confirm `GLTFExportOptions` property names exist on your engine version. |
| `probe_texture_export_routes.py` | Run *inside* Unreal (needs `-AllowCommandletRendering`) to re-check why no Unreal route resizes textures. Pair with `compare_texture_exports.py`, which does the pixel verdict on the host. |

## Driving the engines headlessly

**Godot** — note the `-e`. `import_unreal_layout.gd` extends `EditorScript`, which
Godot refuses to instantiate outside the editor, so `--script` (a non-editor main
loop) cannot drive it. The harness works around this with a temporary
`EditorPlugin` whose `_enter_tree()` runs the import and then quits:

```bash
godot --headless --path <project> --import     # import assets first
godot --headless -e --path <project>           # editor mode; test plugin runs
```

**Unreal** — use the `pythonscript` commandlet, not `-ExecutePythonScript`. The
latter boots the full editor and crashes in Slate under `-NullRHI`:

```bash
UnrealEditor-Cmd.exe <project>.uproject -run=pythonscript \
  -script="tests/probe_unreal_api.py" -unattended -nopause -nosplash
```

Python output lands in `<project>/Saved/Logs/<project>.log` under `LogPython`,
not on stdout.

## Why `probe_unreal_api.py` exists

`GLTFExportOptions` property names are engine-version specific and
`set_editor_property` raises on an unknown name. The exporter once set
`export_materials`, which has never existed; the exception skipped every option
after it in the same `try` block, leaving material baking on and writing
`<material>_<mesh>_BaseColor.png` files next to each `.gltf`. Probe before
trusting a property name.
