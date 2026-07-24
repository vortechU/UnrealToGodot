"""Run INSIDE Unreal (full editor boot with the map positional). Drives the
PRODUCTION landscape exporter (export_landscape.collect_landscapes) against the
loaded map and validates the files it writes. Needs UE2G_OUT_DIR env var.

    UnrealEditor-Cmd.exe <project>.uproject <map> \
      -ExecutePythonScript="tests/real_landscape_export.py" -unattended -nopause -nosplash
"""
import json
import os
import struct
import unreal

OUT_DIR = os.environ.get("UE2G_OUT_DIR")
if not OUT_DIR:
    unreal.log_error("UE2G_OUT_DIR env var required")
    raise SystemExit(2)
os.makedirs(OUT_DIR, exist_ok=True)

unreal.log("=== REAL LANDSCAPE EXPORT START ===")
import export_landscape

sub = unreal.get_editor_subsystem(unreal.EditorActorSubsystem)
actors = sub.get_all_level_actors()
entries = export_landscape.collect_landscapes(actors, OUT_DIR)
unreal.log("entries: %s" % json.dumps(entries, indent=1))

for entry in entries:
    path = os.path.join(OUT_DIR, entry["heightmap_file"])
    if not os.path.exists(path):
        unreal.log_error("MISSING FILE: %s" % path)
        continue
    with open(path, "rb") as f:
        head = f.read(8)
    unreal.log("file %s: %d bytes, magic %s" % (entry["heightmap_file"], os.path.getsize(path), head[:4]))
    if head[:4] == b"\x76\x2f\x31\x01":
        # our uncompressed float EXR: decode min/max directly
        with open(path, "rb") as f:
            d = f.read()
        w, h = entry["heightmap_resolution"]
        scanline_size = 8 + w * 4
        data_start = len(d) - h * scanline_size
        mn, mx = 1e30, -1e30
        for y in range(0, h, 7):
            off = data_start + y * scanline_size + 8
            row = struct.unpack_from("<%df" % w, d, off)
            for v in row[::7]:
                mn = min(mn, v)
                mx = max(mx, v)
        unreal.log("EXR heights: min=%.1f max=%.1f (cm) -- %s" % (mn, mx, "VARIES OK" if mx > mn else "CONSTANT (BAD)"))

unreal.log("=== REAL LANDSCAPE EXPORT END ===")
