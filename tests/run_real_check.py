"""Fast-iteration tool: re-imports a REAL Unreal export into headless Godot and
prints a material/texture diagnostic. This is the loop for material/texture
fixes -- it does NOT re-run Unreal (that's the slow step), only the Godot half,
so a code change here is a ~10-20s turnaround instead of a multi-minute re-export.

    python tests/run_real_check.py <export_out_dir> --godot <path-to-godot.exe>

<export_out_dir> must contain models/, level_layout.json, and (usually)
textures/ -- produced by running an Unreal-side export script through
export_static_meshes_to_gltf.export_all_level_meshes() and
export_level_to_json.export_level_to_json(), the same functions the GUI calls.
"""
import argparse
import os
import subprocess
import sys

TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
HARNESS_DIR = os.path.join(TESTS_DIR, "godot_harness")
PROJECT_DIR = os.path.join(HARNESS_DIR, "_project_real")


def run(cmd, **kw):
    print("\n$ " + " ".join(str(c) for c in cmd))
    return subprocess.run(cmd, **kw).returncode


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("export_out_dir")
    ap.add_argument("--godot", required=True)
    args = ap.parse_args()

    if run([sys.executable, os.path.join(HARNESS_DIR, "build_real_project.py"),
            args.export_out_dir]) != 0:
        return 2
    if run([sys.executable, os.path.join(HARNESS_DIR, "make_real_inspector.py")]) != 0:
        return 2

    # --import can die partway through a multi-gigabyte texture set (Godot's WebP
    # packer OOMs and segfaults). That used to leave a half-populated .godot
    # cache, and the next run trusted it. Treat this pass as best-effort: the
    # inspector waits for the editor's own scan before it inspects anything.
    import_code = run([args.godot, "--headless", "--path", PROJECT_DIR, "--import"],
                      stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    if import_code != 0:
        print("  (--import exited %s; editor pass will finish the scan)" % import_code)
    code = run([args.godot, "--headless", "-e", "--path", PROJECT_DIR])

    report_path = os.path.join(PROJECT_DIR, "real_report.txt")
    if not os.path.exists(report_path):
        print("\nERROR: no real_report.txt written (exit=%s) -- inspector did not run." % code)
        return 1
    print("\n" + "=" * 60)
    with open(report_path, encoding="utf-8") as f:
        print(f.read())
    return 0


if __name__ == "__main__":
    sys.exit(main())
