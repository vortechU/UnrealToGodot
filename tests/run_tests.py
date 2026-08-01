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

PY_TESTS = ["test_math.py", "test_layout.py", "test_tscn_writer.py", "test_materials.py",
            "test_landscape.py",
            "test_texture_export.py",
            # Opens a real Tk window; skips itself when there is no display.
            "test_exporter_gui.py"]


def run(cmd, **kw):
    print("\n$ " + " ".join(str(c) for c in cmd))
    return subprocess.run(cmd, **kw).returncode


def report_godot_results(exit_code):
    """Reads the harness's result file and prints it. Returns True only on a pass.

    Godot's GUI-subsystem exe on Windows drops stdout when the parent redirects it
    to a file, so its console output cannot be trusted as the pass signal -- and a
    plugin that never ran also exits 0. The result file is the only evidence that
    the assertions actually executed, so a missing file is a failure.
    """
    result_path = os.path.join(PROJECT_DIR, "test_result.txt")
    if not os.path.exists(result_path):
        print("ERROR: godot harness produced no test_result.txt "
              "(exit=%s) -- the test plugin did not run." % exit_code)
        return False
    with open(result_path, encoding="utf-8") as f:
        lines = [ln.rstrip("\n") for ln in f if ln.strip()]
    verdict = ""
    total = 0
    for ln in lines:
        if ln.startswith("VERDICT "):
            verdict = ln.split(" ", 1)[1].strip()
        elif ln.startswith("TOTAL "):
            total = int(ln.split(" ", 1)[1])
        else:
            print("  " + ln)
    if total == 0:
        print("ERROR: godot harness ran no checks at all.")
        return False
    print("  -> %d checks, verdict=%s (exit=%s)" % (total, verdict, exit_code))
    return verdict == "PASS" and exit_code == 0


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
        code = run([args.godot, "--headless", "-e", "--path", PROJECT_DIR])
        if not report_godot_results(code):
            failed.append("godot_headless_import")

        # Environment/exposure mapping. A plain SceneTree script, so unlike the
        # layout harness it does not need -e; it exercises import_environment.gd
        # against real Godot Environment/CameraAttributes objects, which the
        # Python-side tests cannot reach.
        if run([args.godot, "--headless", "--path", PROJECT_DIR,
                "--script", "res://env_test.gd"]) != 0:
            failed.append("godot_environment_mapping")

        # Skips itself when there are no real exports to work on, so it costs
        # nothing on a machine that has never run the Unreal probe.
        if run([sys.executable, os.path.join(TESTS_DIR, "test_texture_shrink.py"),
                "--godot", args.godot]) != 0:
            failed.append("test_texture_shrink.py")
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
