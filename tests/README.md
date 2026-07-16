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

## What each test covers

| File | Covers |
| --- | --- |
| `test_math.py` | Coordinate conversion in `ue2g_common.py`: `C·R·Cᵀ`, the packed 12-float foliage basis, `matrix_to_quat` (all four branches), the decal fix-up quaternion, export-name collision hashing. Stubs the `unreal` module. |
| `test_layout.py` | Component placement in `tscn_writer.py`, including an explicit reproduction of the double-transform bug and `affine_inverse` round-trips. |
| `test_tscn_writer.py` | End-to-end `.tscn` generation: resource/node structure, `load_steps` accounting, dangling-reference checks, and node placement. |
| `godot_harness/` | Builds a throwaway Godot project (minimal glTF, fixture `level_layout.json`) and runs `import_unreal_layout.gd` for real, asserting where nodes land. |
| `probe_unreal_api.py` | Run *inside* Unreal to confirm `GLTFExportOptions` property names exist on your engine version. |

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
