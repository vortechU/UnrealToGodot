"""Run INSIDE Unreal (-run=pythonscript) to export a real map through the exact
production functions the GUI calls, for use as fast-iteration test data.

    UnrealEditor-Cmd.exe <project>.uproject -run=pythonscript \
      -script="tests/real_export.py" -unattended -nopause -nosplash \
      -MapPath="/Game/Docks/VOL2_Powell/Maps/Demonstration" \
      -OutDir="C:/path/to/scratch/real_export_out"

Produces <OutDir>/models/, <OutDir>/textures/, <OutDir>/level_layout.json --
exactly the shape tests/run_real_check.py expects. This is the slow step (a
full map's Nanite build can take minutes); run it once per Unreal-side change
and reuse the output for many Godot-side iterations via run_real_check.py.
"""
import os
import sys
import unreal

MAP_PATH = None
OUT_DIR = None
for arg in sys.argv:
    if arg.startswith("-MapPath="):
        MAP_PATH = arg.split("=", 1)[1].strip('"')
    elif arg.startswith("-OutDir="):
        OUT_DIR = arg.split("=", 1)[1].strip('"')

if not MAP_PATH or not OUT_DIR:
    unreal.log_error("real_export.py requires -MapPath=... and -OutDir=...")
    sys.exit(2)

os.makedirs(OUT_DIR, exist_ok=True)
unreal.log("=== REAL EXPORT START (%s) ===" % MAP_PATH)

les = unreal.get_editor_subsystem(unreal.LevelEditorSubsystem)
loaded = les.load_level(MAP_PATH)
unreal.log("load_level(%s) -> %s" % (MAP_PATH, loaded))

sub = unreal.get_editor_subsystem(unreal.EditorActorSubsystem)
actors = sub.get_all_level_actors()
mesh_actors = [a for a in actors if a.get_components_by_class(unreal.StaticMeshComponent)]
unreal.log("actors: %d total, %d with static meshes" % (len(actors), len(mesh_actors)))

import export_static_meshes_to_gltf as EX
import export_level_to_json as EL

unreal.log("--- mesh export (export_all_level_meshes) ---")
try:
    exported, failed = EX.export_all_level_meshes(
        export_dir=os.path.join(OUT_DIR, "models"),
        export_animations=False, export_lods=False, separate_textures=True,
        show_dialogs=False, godot_project_dir=None,
    )
    unreal.log("mesh export: exported=%s failed=%s" % (exported, failed))
except Exception as e:
    unreal.log_error("mesh export raised: %s" % e)

unreal.log("--- layout export (export_level_to_json) ---")
try:
    ok = EL.export_level_to_json(
        save_path=os.path.join(OUT_DIR, "level_layout.json"),
        show_dialogs=False, godot_project_dir=None, options=None,
    )
    unreal.log("layout export: %s" % ok)
except Exception as e:
    unreal.log_error("layout export raised: %s" % e)

unreal.log("=== REAL EXPORT END ===")
