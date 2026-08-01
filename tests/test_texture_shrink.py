"""Drives texture_import_limit.shrink_source_files inside real headless Godot,
against real full-resolution Unreal exports, and checks the pixels afterwards.

The point is not just "the file got smaller". Unreal's own Max Texture
Resolution could not be made to work because every route to a resized texture
went through the cooked, block-compressed pixels -- which flatten a BC5 normal
map's blue channel to a constant 0. So the normal map is the load-bearing case
here: its blue channel must survive.

    python tests/test_texture_shrink.py --godot "V:/Apps/Godot_v4.7.1/Godot_v4.7.1-stable_win64.exe"

Needs a folder of real exports (default C:/scratch/texquality, written by
tests/probe_texture_export_routes.py). Skips cleanly if they are absent.
"""
import argparse
import os
import shutil
import subprocess
import sys
import tempfile

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LIMIT_GD = os.path.join(REPO, "addons", "unreal_importer", "texture_import_limit.gd")

PROJECT_GODOT = """\
config_version=5
[application]
config/name="shrink_test"
config/features=PackedStringArray("4.6")
"""

# Second leg: build the REAL dock inside a real editor. The pixel work is proven
# by the non-editor leg above; what this catches is the dock failing to build at
# all (its _enter_tree constructs every control, so one bad reference takes the
# whole addon down), plus the editor-only reimport branch that the non-editor
# run skips over.
PLUGIN_CFG = """\
[plugin]
name="shrink_dock_test"
description="drives the real importer dock headlessly"
author="tests"
version="1.0"
script="plugin.gd"
"""

PLUGIN_GD = """\
@tool
extends EditorPlugin

const DockClass = preload("res://addons/unreal_importer/importer_dock.gd")

func _enter_tree() -> void:
	call_deferred("_run")

func _run() -> void:
	var lines: PackedStringArray = []
	var dock = DockClass.new()
	# Adding it to the tree is what fires _enter_tree and builds the controls.
	EditorInterface.get_base_control().add_child(dock)
	await get_tree().process_frame

	lines.append("CHECK dock_built=%s" % (dock.import_btn != null))
	lines.append("CHECK shrink_checkbox_exists=%s" % (dock.shrink_files_check != null))
	if dock.shrink_files_check:
		lines.append("CHECK shrink_default_on=%s" % dock.shrink_files_check.button_pressed)

	# Drive the same call _on_import_pressed makes, through the dock's own
	# accessor, so a mismatch between the dropdown and the limit shows up here.
	dock.texture_limit_option.selected = 2  # "1024"
	var limit: int = dock._selected_texture_limit()
	lines.append("CHECK selected_limit=%d" % limit)

	# The cap has to cover every folder the material binder resolves from, not
	# just the Textures Folder -- 4K art left in models/ still blows the importer
	# up. res://models/ does not exist here, and the layout JSON sits at the
	# project root so its textures/ IS res://textures/: one entry, deduplicated
	# (simplify_path drops the trailing slash).
	lines.append("CHECK search_folders=%s" % ",".join(dock._texture_search_folders()))

	var TextureLimit = load("res://addons/unreal_importer/texture_import_limit.gd")

	# Assert the fixture really is oversized before shrinking it. Without this,
	# a harness that quietly shrank the files earlier reports shrunk=0 and looks
	# like a broken feature rather than a broken test.
	var found: PackedStringArray = TextureLimit.find_textures("res://textures/")
	var oversized := 0
	for p in found:
		var probe := Image.new()
		if probe.load(p) == OK and maxi(probe.get_width(), probe.get_height()) > limit:
			oversized += 1
	lines.append("CHECK fixture_oversized=%d of %d" % [oversized, found.size()])

	var r: Dictionary = TextureLimit.shrink_source_files("res://textures/", limit)
	lines.append("CHECK total=%d shrunk=%d failed=%d skipped=%d" % [
		r["total"], r["shrunk"], r["failed"], r["skipped"]])

	# And that they are actually capped on disk afterwards.
	var still_big := 0
	for p in TextureLimit.find_textures("res://textures/"):
		var probe2 := Image.new()
		if probe2.load(p) == OK and maxi(probe2.get_width(), probe2.get_height()) > limit:
			still_big += 1
	lines.append("CHECK still_oversized_after=%d" % still_big)

	# The editor-only branch: rewritten files must come back through a reimport.
	var fs := EditorInterface.get_resource_filesystem()
	while fs and fs.is_scanning():
		await get_tree().process_frame
	lines.append("CHECK reimport_survived=true")

	var f := FileAccess.open("res://dock_result.txt", FileAccess.WRITE)
	f.store_string("\\n".join(lines))
	f.close()
	dock.queue_free()
	get_tree().quit(0)
"""

# Runs inside Godot. Non-editor headless: Image.load reads the file straight off
# disk, so none of this needs the editor's import pass (which is what runs the
# WebP packer out of memory on a real 4K texture set).
TEST_GD = """\
extends SceneTree

const TextureLimit = preload("res://addons/unreal_importer/texture_import_limit.gd")

func _initialize() -> void:
	var folder := "res://textures/"
	for p in TextureLimit.find_textures(folder):
		var img := Image.new()
		if img.load(p) == OK:
			print("BEFORE %s %dx%d" % [p.get_file(), img.get_width(), img.get_height()])

	var r: Dictionary = TextureLimit.shrink_source_files(folder, __LIMIT__)
	print("RESULT total=%d shrunk=%d skipped=%d failed=%d before=%d after=%d" % [
		r["total"], r["shrunk"], r["skipped"], r["failed"],
		r["bytes_before"], r["bytes_after"]])

	for p in TextureLimit.find_textures(folder):
		var img := Image.new()
		if img.load(p) == OK:
			print("AFTER %s %dx%d" % [p.get_file(), img.get_width(), img.get_height()])
	quit(0)
"""


def build_project(root, textures, limit, whole_addon=False):
    addon = os.path.join(root, "addons", "unreal_importer")
    os.makedirs(addon, exist_ok=True)
    os.makedirs(os.path.join(root, "textures"), exist_ok=True)
    with open(os.path.join(root, "project.godot"), "w") as f:
        f.write(PROJECT_GODOT)
    if whole_addon:
        # The dock preloads its siblings, so the addon has to arrive intact.
        for f in os.listdir(os.path.dirname(LIMIT_GD)):
            if f.endswith((".gd", ".cfg")):
                shutil.copy(os.path.join(os.path.dirname(LIMIT_GD), f), os.path.join(addon, f))
    else:
        # texture_import_limit preloads import_common for the shared image
        # extension list, and a failed preload takes the whole script with it.
        for name in ("texture_import_limit.gd", "import_common.gd"):
            shutil.copy(os.path.join(os.path.dirname(LIMIT_GD), name),
                        os.path.join(addon, name))
    with open(os.path.join(root, "test_shrink.gd"), "w") as f:
        f.write(TEST_GD.replace("__LIMIT__", str(limit)))
    for src in textures:
        shutil.copy(src, os.path.join(root, "textures", os.path.basename(src)))


def run_dock_leg(godot, textures, limit):
    """Builds the real dock in a real editor and reports its CHECK lines."""
    root = tempfile.mkdtemp(prefix="shrink_dock_")
    try:
        build_project(root, textures, limit, whole_addon=True)
        plug = os.path.join(root, "addons", "shrink_dock_test")
        os.makedirs(plug, exist_ok=True)
        with open(os.path.join(plug, "plugin.cfg"), "w") as f:
            f.write(PLUGIN_CFG)
        with open(os.path.join(plug, "plugin.gd"), "w") as f:
            f.write(PLUGIN_GD)
        with open(os.path.join(root, "project.godot"), "a") as f:
            f.write('\n[editor_plugins]\nenabled=PackedStringArray('
                    '"res://addons/shrink_dock_test/plugin.cfg")\n')
            # Editor mode actually imports these, and a 4K set is exactly what
            # runs the WebP packer out of memory (tests/README.md). Capping the
            # import does not weaken the test: shrink_source_files reads the
            # files off disk with Image.load, not through the import pipeline.
            f.write('\n[importer_defaults]\n'
                    'texture={"process/size_limit": 512}\n')

        # Deliberately NO separate --import pass. --import boots the editor and
        # enables editor plugins, so this plugin's _enter_tree fires there too:
        # it shrinks the textures, writes its result, and quits. The -e pass then
        # runs a second time against already-capped files, finds nothing to do,
        # and overwrites the result with shrunk=0 -- a real success reported as a
        # failure. -e imports on its own, and Image.load reads raw files anyway.
        proc = subprocess.run([godot, "--headless", "-e", "--path", root],
                              capture_output=True, text=True, timeout=900)
        res = os.path.join(root, "dock_result.txt")
        if not os.path.isfile(res):
            out = (proc.stdout + proc.stderr)
            errs = [l for l in out.splitlines()
                    if "SCRIPT ERROR" in l or "Parse Error" in l or "Cannot" in l]
            return ["dock leg produced no result file"] + errs[:6]
        with open(res) as f:
            text = f.read()
        for line in text.splitlines():
            print("  " + line)

        n = len(textures)
        expected = [
            "dock_built=true",
            "shrink_checkbox_exists=true",
            "shrink_default_on=true",
            "selected_limit=%d" % limit,
            # Existing folders only, and no folder listed twice.
            "search_folders=res://textures",
            # Guards against a false pass: if something already shrank the
            # fixture, shrunk=0 below would otherwise look like a broken feature.
            "fixture_oversized=%d of %d" % (n, n),
            "total=%d shrunk=%d failed=0 skipped=0" % (n, n),
            "still_oversized_after=0",
            "reimport_survived=true",
        ]
        return ["dock leg: expected %r" % e for e in expected if e not in text]
    finally:
        shutil.rmtree(root, ignore_errors=True)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--godot", default="V:/Apps/Godot_v4.7.1/Godot_v4.7.1-stable_win64.exe")
    ap.add_argument("--exports", default="C:/scratch/texquality")
    ap.add_argument("--limit", type=int, default=1024)
    args = ap.parse_args()

    if not os.path.isdir(args.exports):
        print("SKIP: no exports at %s" % args.exports)
        return 0
    textures = sorted(os.path.join(args.exports, f) for f in os.listdir(args.exports)
                      if f.endswith("__baseline.png"))
    if not textures:
        print("SKIP: no *__baseline.png in %s" % args.exports)
        return 0

    root = tempfile.mkdtemp(prefix="shrink_test_")
    try:
        build_project(root, textures, args.limit)
        before = {os.path.basename(p): os.path.getsize(p) for p in textures}
        proc = subprocess.run(
            [args.godot, "--headless", "--path", root, "--script", "test_shrink.gd"],
            capture_output=True, text=True, timeout=900)
        out = proc.stdout + proc.stderr
        for line in out.splitlines():
            if line.startswith(("BEFORE", "AFTER", "RESULT")) or "SCRIPT ERROR" in line:
                print("  " + line)

        failures = []
        if "SCRIPT ERROR" in out:
            failures.append("GDScript error in headless run")

        try:
            from PIL import Image
        except ImportError:
            print("\n(PIL unavailable on host; skipping pixel verification)")
            return 1 if failures else 0

        print("\n--- pixel verification (host PIL) ---")
        total_before = total_after = 0
        for src in textures:
            name = os.path.basename(src)
            out_p = os.path.join(root, "textures", name)
            ref = Image.open(src).convert("RGBA")
            got = Image.open(out_p).convert("RGBA")
            total_before += before[name]
            total_after += os.path.getsize(out_p)

            longest = max(ref.size)
            expect = (max(1, round(ref.size[0] * args.limit / longest)),
                      max(1, round(ref.size[1] * args.limit / longest))) \
                if longest > args.limit else ref.size
            ok_dims = got.size == expect
            if not ok_dims:
                failures.append("%s: expected %s got %s" % (name, expect, got.size))

            # Per-channel range: the failure the GPU route had was a whole
            # channel collapsing to a constant.
            ex = [b.getextrema() for b in got.split()]
            ref_ex = [b.getextrema() for b in ref.split()]
            flat = [c for c, e, r in zip("RGBA", ex, ref_ex)
                    if e[0] == e[1] and r[0] != r[1]]
            if flat:
                failures.append("%s: channel(s) %s collapsed to a constant" % (name, flat))

            print("  %-34s %s -> %s  %.1fMB -> %.2fMB  extrema=%s%s" % (
                name, ref.size, got.size, before[name] / 1048576.0,
                os.path.getsize(out_p) / 1048576.0, ex,
                "  FLATTENED:%s" % flat if flat else ""))

        print("\n  total on disk: %.1f MB -> %.1f MB (%.1f%% smaller)" % (
            total_before / 1048576.0, total_after / 1048576.0,
            100.0 * (1 - total_after / total_before) if total_before else 0))

        print("\n--- dock leg (real dock, real editor) ---")
        failures += run_dock_leg(args.godot, textures, args.limit)

        if failures:
            print("\nFAIL:")
            for f in failures:
                print("  - " + f)
            return 1
        print("\nPASS: every texture capped to %dpx with all channels intact,"
              " and the dock drives it" % args.limit)
        return 0
    finally:
        shutil.rmtree(root, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
