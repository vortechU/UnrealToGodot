"""Runs INSIDE Unreal (headless) to verify the MaterialEditingLibrary entry points
the base-material parameter reader uses actually exist on this engine version.

A base Material has no scalar_parameter_values/vector_parameter_values array --
those hold a MaterialInstance's OVERRIDES -- so a mesh slot pointing straight at
a base material exported with the schema defaults (roughness 0.5 / metallic 0.0 /
white) whatever the material said. ue2g_common.iter_base_material_scalars reads
the parameter DEFAULTS through MaterialEditingLibrary instead; these names are
guessed by symmetry with the texture path, so prove them.

Run:
    UnrealEditor-Cmd.exe <project>.uproject -run=pythonscript
        -script="<repo>/tests/probe_material_params.py"
Output lands in <project>/Saved/Logs/<project>.log under LogPython.
"""
import unreal

unreal.log("=== MATERIAL PARAM PROBE START ===")
unreal.log("Engine version: %s" % unreal.SystemLibrary.get_engine_version())

mel = getattr(unreal, "MaterialEditingLibrary", None)
unreal.log("MaterialEditingLibrary present: %s" % (mel is not None))

for name in [
    # The pair already in use and verified, as a control.
    "get_texture_parameter_names",
    "get_material_default_texture_parameter_value",
    # The pair the base-material scalar/vector reader depends on.
    "get_scalar_parameter_names",
    "get_material_default_scalar_parameter_value",
    "get_vector_parameter_names",
    "get_material_default_vector_parameter_value",
]:
    unreal.log("METHOD %-46s %s" % (name, "OK" if hasattr(mel, name) else "MISSING"))

# Read a real master material end to end, the way the exporter does.
MATERIAL_PATHS = [
    "/Game/ContainersHouseCH/Materials/M_Containers_Master",
]
for path in MATERIAL_PATHS:
    mat = unreal.load_asset(path)
    if mat is None:
        unreal.log("SKIP  %s (not in this project)" % path)
        continue
    unreal.log("MATERIAL %s (%s)" % (path, type(mat).__name__))
    try:
        import sys
        sys.path.insert(0, unreal.Paths.project_plugins_dir())
        import ue2g_common
        for pname, value in ue2g_common.iter_base_material_scalars(mat):
            unreal.log("  SCALAR %-28s = %r" % (pname, value))
        for pname, value in ue2g_common.iter_base_material_vectors(mat):
            unreal.log("  VECTOR %-28s = (%.3f, %.3f, %.3f, %.3f)"
                       % (pname, value.r, value.g, value.b, value.a))
    except Exception as e:
        unreal.log("  HELPER FAILED: %s" % e)
        # Fall back to calling the library directly so the probe still reports
        # which names work even when the helper import path is wrong.
        for names_getter, value_getter in [
                ("get_scalar_parameter_names", "get_material_default_scalar_parameter_value"),
                ("get_vector_parameter_names", "get_material_default_vector_parameter_value")]:
            try:
                for pname in getattr(mel, names_getter)(mat) or []:
                    unreal.log("  %s %-24s = %r"
                               % (names_getter, pname, getattr(mel, value_getter)(mat, pname)))
            except Exception as inner:
                unreal.log("  %s FAILED: %s" % (names_getter, inner))

unreal.log("=== MATERIAL PARAM PROBE END ===")
