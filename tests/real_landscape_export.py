"""Drives the PRODUCTION landscape exporter against a real loaded level and
validates every file it writes. Run INSIDE Unreal, full editor boot with the map
as a FILE-PATH positional (the pythonscript commandlet cannot load a level):

    set UE2G_OUT_DIR=<dir>
    UnrealEditor-Cmd.exe <project>.uproject <map>.umap ^
      -ExecutePythonScript="tests/real_landscape_export.py" -unattended -nopause -nosplash

Findings are logged one line per field under `LogPython` in
<project>/Saved/Logs/<project>.log, prefixed REALLS| -- unreal.log only tags the
first line of a multi-line string.
"""
import json
import os
import struct
import time
import unreal

OUT_DIR = os.environ.get("UE2G_OUT_DIR")
if not OUT_DIR:
    unreal.log_error("UE2G_OUT_DIR env var required")
    raise SystemExit(2)
os.makedirs(OUT_DIR, exist_ok=True)


def line(msg):
    unreal.log("REALLS| " + str(msg))


def read_exr_r32(path):
    """Decodes the uncompressed single-channel float EXR the exporter writes.
    Returns (width, height, min, max, distinct_sample_count)."""
    with open(path, "rb") as f:
        data = f.read()
    if data[:4] != b"\x76\x2f\x31\x01":
        return None
    # dataWindow gives the size; find it in the header attribute list.
    idx = data.find(b"dataWindow\x00box2i\x00")
    if idx < 0:
        return None
    off = idx + len(b"dataWindow\x00box2i\x00") + 4
    x0, y0, x1, y1 = struct.unpack_from("<4i", data, off)
    w, h = x1 - x0 + 1, y1 - y0 + 1
    scanline = 8 + w * 4
    start = len(data) - h * scanline
    mn, mx = 1e30, -1e30
    seen = set()
    step = max(1, h // 64)
    for y in range(0, h, step):
        row = struct.unpack_from("<%df" % w, data, start + y * scanline + 8)
        for v in row[:: max(1, w // 64)]:
            mn = min(mn, v)
            mx = max(mx, v)
            seen.add(round(v, 3))
    return w, h, mn, mx, len(seen)


line("=== REAL LANDSCAPE EXPORT START ===")
import export_landscape

sub = unreal.get_editor_subsystem(unreal.EditorActorSubsystem)
actors = sub.get_all_level_actors()
line("level actors: %d" % len(actors))

opts = {
    "terrain_height_resolution": int(os.environ.get("UE2G_H_RES", "257")),
    "terrain_weight_resolution": int(os.environ.get("UE2G_W_RES", "65")),
}
line("options: %r" % opts)

t0 = time.time()
entries = export_landscape.collect_landscapes(actors, OUT_DIR, opts)
line("collect_landscapes: %d entries in %.1fs" % (len(entries), time.time() - t0))

failures = 0
for entry in entries:
    line("--- landscape %s" % entry.get("name"))
    for k in ("heightmap_resolution", "world_size_m", "world_center_m", "height_range_m",
              "height_encoding", "vertex_spacing_m"):
        line("    %s = %s" % (k, json.dumps(entry.get(k))))
    line("    ue_bounds = %s" % json.dumps(entry.get("ue_bounds")))

    files = [("height", entry.get("heightmap_file"))]
    for lay in entry.get("layers", []):
        files.append(("layer:%s%s" % (lay.get("name"), lay.get("debug_color") or ""),
                      lay.get("weightmap_file")))
    for tag, rel in files:
        if not rel:
            line("    %-24s (no file)" % tag)
            continue
        path = os.path.join(OUT_DIR, rel)
        if not os.path.exists(path):
            line("    %-24s MISSING %s" % (tag, path))
            failures += 1
            continue
        with open(path, "rb") as f:
            magic = f.read(4)
        info = read_exr_r32(path) if magic == b"\x76\x2f\x31\x01" else None
        if info is None:
            line("    %-24s %s  magic=%s size=%d  (not our EXR)"
                 % (tag, os.path.basename(rel), magic.hex(), os.path.getsize(path)))
            continue
        w, h, mn, mx, distinct = info
        verdict = "VARIES" if mx > mn else "CONSTANT -- BAD"
        if mx <= mn:
            failures += 1
        line("    %-24s %dx%d  range %.3f .. %.3f  distinct=%d  %s"
             % (tag, w, h, mn, mx, distinct, verdict))

with open(os.path.join(OUT_DIR, "entries.json"), "w", encoding="utf-8") as f:
    json.dump({"landscapes": entries}, f, indent=1)
line("wrote entries.json")
line("FAILURES: %d" % failures)
line("=== REAL LANDSCAPE EXPORT END ===")
