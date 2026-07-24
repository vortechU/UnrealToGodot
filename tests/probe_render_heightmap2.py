"""Run INSIDE Unreal (full editor boot with the map positional). Validates
render_heightmap through the FILE pipeline: read_render_target_raw_pixel
returns FLinearColor::Red on failure, which earlier probes mistook for pixel
data. Here each render target is exported to disk and the PNG16 bytes are
decoded in pure Python for truthful stats. Needs UE2G_OUT_DIR env var.
"""
import os
import struct
import zlib
import unreal

OUT_DIR = os.environ.get("UE2G_OUT_DIR", os.path.join(os.path.dirname(os.path.abspath(__file__)), "_probe_out"))
os.makedirs(OUT_DIR, exist_ok=True)

unreal.log("=== RENDER_HEIGHTMAP FILE PROBE START ===")

world = unreal.get_editor_subsystem(unreal.UnrealEditorSubsystem).get_editor_world()
sub = unreal.get_editor_subsystem(unreal.EditorActorSubsystem)
parent = None
for a in sub.get_all_level_actors():
    if a is not None and isinstance(a, unreal.Landscape):
        parent = a
        break
if parent is None:
    unreal.log_error("no parent Landscape")
    raise SystemExit(1)

unreal.log("RenderingLibrary read api: %s" % [m for m in dir(unreal.RenderingLibrary) if "read" in m.lower()])


def decode_png_stats(path):
    """Pure-python PNG decode -> per-channel (min, max) over all pixels."""
    with open(path, "rb") as f:
        d = f.read()
    if d[:4] != b"\x89PNG":
        return "not png: %s" % d[:4]
    i = 8
    idat = b""
    w = h = bd = ct = None
    while i < len(d):
        ln = struct.unpack(">I", d[i:i + 4])[0]
        tag = d[i + 4:i + 8]
        if tag == b"IHDR":
            w, h, bd, ct = struct.unpack(">IIBB", d[i + 8:i + 18])
        elif tag == b"IDAT":
            idat += d[i + 8:i + 8 + ln]
        elif tag == b"IEND":
            break
        i += 12 + ln
    raw = zlib.decompress(idat)
    nch = {0: 1, 2: 3, 4: 2, 6: 4}[ct]
    bpp = nch * (bd // 8)
    stride = w * bpp
    out = bytearray(w * h * bpp)
    prev = bytearray(stride)
    pos = 0
    for row in range(h):
        ft = raw[pos]
        pos += 1
        cur = bytearray(raw[pos:pos + stride])
        pos += stride
        if ft == 1:
            for j in range(bpp, stride):
                cur[j] = (cur[j] + cur[j - bpp]) & 0xFF
        elif ft == 2:
            for j in range(stride):
                cur[j] = (cur[j] + prev[j]) & 0xFF
        elif ft == 3:
            for j in range(stride):
                a = cur[j - bpp] if j >= bpp else 0
                cur[j] = (cur[j] + ((a + prev[j]) >> 1)) & 0xFF
        elif ft == 4:
            for j in range(stride):
                a = cur[j - bpp] if j >= bpp else 0
                b = prev[j]
                c = prev[j - bpp] if j >= bpp else 0
                pa, pb, pc = abs(b - c), abs(a - c), abs(a + b - 2 * c)
                pr = a if (pa <= pb and pa <= pc) else (b if pb <= pc else c)
                cur[j] = (cur[j] + pr) & 0xFF
        out[row * stride:(row + 1) * stride] = cur
        prev = cur
    step = bd // 8
    mins = [1 << 16] * nch
    maxs = [-1] * nch
    for row in range(0, h, 7):
        base = row * stride
        for col in range(0, w, 7):
            o = base + col * bpp
            for ch in range(nch):
                if bd == 16:
                    v = (out[o + ch * 2] << 8) | out[o + ch * 2 + 1]
                else:
                    v = out[o + ch]
                if v < mins[ch]:
                    mins[ch] = v
                if v > maxs[ch]:
                    maxs[ch] = v
    return "%dx%d bd=%d ct=%d stats=%s" % (w, h, bd, ct, list(zip(mins, maxs)))


SIZE = 505
combos = [
    ("actor_xform_empty_box", parent.get_actor_transform(), unreal.Box2D()),
    ("identity_empty_box", unreal.Transform(), unreal.Box2D()),
]

for label, xform, box in combos:
    try:
        rt = unreal.RenderingLibrary.create_render_target2d(world, SIZE, SIZE, unreal.TextureRenderTargetFormat.RTF_R32F)
        ok = parent.render_heightmap(xform, box, rt)
        unreal.log("render_heightmap(%s) -> %s" % (label, ok))
        fname = "probe_%s.exr" % label
        unreal.RenderingLibrary.export_render_target(world, rt, OUT_DIR, fname)
        path = os.path.join(OUT_DIR, fname)
        if os.path.exists(path):
            unreal.log("FILE %s: %s" % (label, decode_png_stats(path)))
        else:
            unreal.log("FILE %s: not written" % label)
    except Exception as e:
        unreal.log("COMBO %s raised: %r" % (label, e))

# Control: an undrawn RT through the same writer, to tell "empty RT" apart
# from "file writer broken in this context".
try:
    rt = unreal.RenderingLibrary.create_render_target2d(world, SIZE, SIZE, unreal.TextureRenderTargetFormat.RTF_R32F)
    unreal.RenderingLibrary.export_render_target(world, rt, OUT_DIR, "probe_control_undrawn.exr")
    path = os.path.join(OUT_DIR, "probe_control_undrawn.exr")
    unreal.log("FILE control_undrawn: %s" % (decode_png_stats(path) if os.path.exists(path) else "not written"))
except Exception as e:
    unreal.log("control raised: %r" % e)

unreal.log("=== RENDER_HEIGHTMAP FILE PROBE END ===")
