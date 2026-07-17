"""Host-side (NOT Unreal). Compares RenderTarget-downscaled PNGs against a
PIL-downscaled reference built from the full-res baseline export.

Answers: did the RT capture real detail (or a blurry low mip)? is gamma right?
did the normal map's B channel survive BC5? did alpha survive?

    python tests/compare_texture_exports.py C:/scratch/texquality
"""
import os
import sys
from PIL import Image, ImageFilter, ImageChops

def stats(img):
    """Per-channel mean; plus a detail score (std of a high-pass)."""
    bands = img.split()
    means = [round(sum(i * c for i, c in enumerate(b.histogram())) / (b.size[0] * b.size[1]), 1)
             for b in bands]
    grey = img.convert("L")
    detail = ImageChops.difference(grey, grey.filter(ImageFilter.GaussianBlur(2)))
    hist = detail.histogram()
    n = sum(hist)
    mean = sum(i * c for i, c in enumerate(hist)) / n
    var = sum((i - mean) ** 2 * c for i, c in enumerate(hist)) / n
    return means, round(var ** 0.5, 2)

def mae(a, b):
    """Mean absolute error per channel between two same-size RGBA images."""
    out = []
    for ba, bb in zip(a.split(), b.split()):
        d = ImageChops.difference(ba, bb).histogram()
        n = sum(d)
        out.append(round(sum(i * c for i, c in enumerate(d)) / n, 2))
    return out

def main(d):
    names = sorted({f.split("__")[0] for f in os.listdir(d) if "__" in f})
    for name in names:
        base_p = os.path.join(d, "%s__baseline.png" % name)
        if not os.path.isfile(base_p):
            continue
        base = Image.open(base_p)
        print("\n=== %s ===" % name)
        print("  baseline      %-9s %s  mode=%s" % (
            "%dx%d" % base.size, "", base.mode))
        base_rgba = base.convert("RGBA")

        variants = sorted(f for f in os.listdir(d)
                          if f.startswith(name + "__rt_") and f.endswith(".png"))
        if not variants:
            continue
        cap = Image.open(os.path.join(d, variants[0])).size
        ref = base_rgba.resize(cap, Image.BOX)
        rm, rd = stats(ref)
        print("  reference(PIL BOX -> %dx%d)  mean=%-24s detail=%s" % (cap[0], cap[1], rm, rd))

        for v in variants:
            img = Image.open(os.path.join(d, v))
            rgba = img.convert("RGBA")
            m, det = stats(rgba)
            e = mae(rgba, ref)
            print("  %-22s mode=%-5s mean=%-24s detail=%-6s MAE_vs_ref=%s" % (
                v.replace(name + "__", ""), img.mode, m, det, e))

        # channel-constancy check (normal maps / packed masks)
        for v in variants:
            rgba = Image.open(os.path.join(d, v)).convert("RGBA")
            flat = [ch for ch, b in zip("RGBA", rgba.split())
                    if b.getextrema()[0] == b.getextrema()[1]]
            if flat:
                print("  %-22s CONSTANT channels: %s  (extrema=%s)" % (
                    v.replace(name + "__", ""), flat,
                    [b.getextrema() for b in rgba.split()]))
        b_ref = [b.getextrema() for b in base_rgba.split()]
        print("  baseline extrema RGBA: %s" % b_ref)

if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "C:/scratch/texquality")
