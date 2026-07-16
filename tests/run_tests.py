"""Runs the whole test suite.

    python tests/run_tests.py                 # pure-Python tests only
    python tests/run_tests.py --godot <exe>   # also drives the real Godot importer

The --godot leg builds a throwaway project under tests/godot_harness/_project,
runs import_unreal_layout.gd headlessly against a fixture layout, and asserts
where the nodes actually land. See tests/README.md.
"""
import argparse
import os
import shutil
import subprocess
import sys

TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
HARNESS_DIR = os.path.join(TESTS_DIR, "godot_harness")
PROJECT_DIR = os.path.join(HARNESS_DIR, "_project")

PY_TESTS = ["test_math.py", "test_layout.py", "test_tscn_writer.py"]


def run(cmd, **kw):
    print("\n$ " + " ".join(str(c) for c in cmd))
    return subprocess.run(cmd, **kw).returncode


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--godot", help="path to the Godot editor executable")
    args = ap.parse_args()

    failed = []

    for t in PY_TESTS:
        if run([sys.executable, os.path.join(TESTS_DIR, t)]) != 0:
            failed.append(t)

    if args.godot:
        if not os.path.exists(args.godot):
            print("ERROR: Godot executable not found: %s" % args.godot)
            return 2
        if os.path.exists(PROJECT_DIR):
            shutil.rmtree(PROJECT_DIR)
        for step in ["build_harness.py", "make_test_plugin.py"]:
            if run([sys.executable, os.path.join(HARNESS_DIR, step)]) != 0:
                print("ERROR: harness build step failed: %s" % step)
                return 2
        # Import assets first so load() can resolve the glTF scenes.
        run([args.godot, "--headless", "--path", PROJECT_DIR, "--import"],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        # -e (editor mode) is required: import_unreal_layout.gd extends
        # EditorScript, which Godot refuses to instantiate outside the editor.
        if run([args.godot, "--headless", "-e", "--path", PROJECT_DIR]) != 0:
            failed.append("godot_headless_import")
    else:
        print("\n(skipping Godot leg; pass --godot <path-to-godot.exe> to enable)")

    print("\n" + "=" * 60)
    if failed:
        print("FAILED: %s" % ", ".join(failed))
        return 1
    print("ALL TESTS PASSED")
    return 0


if __name__ == "__main__":
    sys.exit(main())
