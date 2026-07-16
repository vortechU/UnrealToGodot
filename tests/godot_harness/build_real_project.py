"""Builds a disposable Godot project around a REAL Unreal export (not synthetic
fixtures), so bugs that only surface against real assets/materials are caught.

Usage: python build_real_project.py <export_out_dir>
  <export_out_dir> must contain models/, level_layout.json, and (usually) textures/
  -- exactly what real_export.py (run inside Unreal) produces.
"""
import os
import shutil
import sys

HARNESS = os.path.join(os.path.dirname(os.path.abspath(__file__)), "_project_real")
REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def build(export_out_dir):
    if os.path.exists(HARNESS):
        shutil.rmtree(HARNESS)
    os.makedirs(HARNESS)

    shutil.copytree(os.path.join(export_out_dir, "models"), os.path.join(HARNESS, "models"))
    tex_src = os.path.join(export_out_dir, "textures")
    if os.path.exists(tex_src):
        shutil.copytree(tex_src, os.path.join(HARNESS, "textures"))
    else:
        os.makedirs(os.path.join(HARNESS, "textures"))

    shutil.copy(os.path.join(export_out_dir, "level_layout.json"),
                os.path.join(HARNESS, "level_layout.json"))

    with open(os.path.join(HARNESS, "project.godot"), "w", encoding="utf-8") as f:
        f.write('config_version=5\n\n[application]\n\nconfig/name="RealImportHarness"\n'
                'config/features=PackedStringArray("4.6")\n\n'
                '[editor_plugins]\n\nenabled=PackedStringArray("res://addons/test_runner/plugin.cfg")\n')

    shutil.copytree(os.path.join(REPO, "addons"), os.path.join(HARNESS, "addons"))

    n_models = len([f for f in os.listdir(os.path.join(HARNESS, "models")) if f.endswith(".gltf")])
    n_tex = len(os.listdir(os.path.join(HARNESS, "textures")))
    print("real harness built at:", HARNESS)
    print("  %d .gltf models, %d texture files" % (n_models, n_tex))


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("usage: build_real_project.py <export_out_dir>")
        sys.exit(2)
    build(sys.argv[1])
