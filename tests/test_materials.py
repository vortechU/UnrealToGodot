"""Tests packed-PBR-map classification and glTF texture-reference injection.

Stubs the `unreal` module so the real exporter code runs outside the engine.
"""
import json
import os
import sys
import tempfile
import types

# ---------------------------------------------------------------- unreal stub
u = types.ModuleType("unreal")


class _Vec:
    def __init__(self, x=0.0, y=0.0, z=0.0):
        self.x, self.y, self.z = x, y, z


class _Quat:
    def __init__(self, x=0.0, y=0.0, z=0.0, w=1.0):
        self.x, self.y, self.z, self.w = x, y, z, w

    def rotator(self):
        return None


u.Vector, u.Quat = _Vec, _Quat
u.Transform = type("Transform", (), {})
u.Rotator = type("Rotator", (), {})
u.LinearColor = type("LinearColor", (), {})
u.Color = type("Color", (), {})
u.Texture = type("Texture", (), {})
u.MaterialInstance = type("MaterialInstance", (), {})
u.StaticMesh = type("StaticMesh", (), {})
u.SkeletalMesh = type("SkeletalMesh", (), {})
u.log = lambda *a: None
u.log_warning = lambda *a: None
u.log_error = lambda *a: None
sys.modules["unreal"] = u

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                                "UnrealToGodot", "Content", "Python"))

import export_level_to_json as EL
import export_static_meshes_to_gltf as EX

FAIL = []


def check(name, cond, detail=""):
    print(("  PASS  " if cond else "  FAIL  ") + name + ("  " + str(detail) if not cond else ""))
    if not cond:
        FAIL.append(name)


print("\n=== 1. Packed map classification ===")
# Godot channel indices: 0=RED 1=GREEN 2=BLUE
check("'RMA' -> R=rough G=metal B=ao",
      EL.classify_packed_texture("RMA") == {"roughness": 0, "metallic": 1, "ao": 2},
      EL.classify_packed_texture("RMA"))
check("'ORM' -> R=ao G=rough B=metal (glTF order)",
      EL.classify_packed_texture("ORM") == {"ao": 0, "roughness": 1, "metallic": 2},
      EL.classify_packed_texture("ORM"))
check("'ARM' matches ORM layout",
      EL.classify_packed_texture("ARM") == {"ao": 0, "roughness": 1, "metallic": 2})
check("'MRA' -> R=metal G=rough B=ao",
      EL.classify_packed_texture("MRA") == {"metallic": 0, "roughness": 1, "ao": 2})
check("case insensitive ('rma')", EL.classify_packed_texture("rma") is not None)
check("separator tokens ('T_Rock_RMA_01')",
      EL.classify_packed_texture("T_Rock_RMA_01") is not None)
check("real-world param name with space ('RMA Map')",
      EL.classify_packed_texture("RMA Map") is not None)

print("\n--- must NOT false-positive ---")
# 'Normal' contains the substring 'orm'. A naive `"orm" in name` would classify
# the normal map as a packed ORM map and destroy it.
check("'Normal' is NOT a packed map", EL.classify_packed_texture("Normal") is None,
      EL.classify_packed_texture("Normal"))
check("'Albedo ' is NOT a packed map", EL.classify_packed_texture("Albedo ") is None)
check("'Format' is NOT a packed map", EL.classify_packed_texture("Format") is None)
check("'Deformation' is NOT a packed map", EL.classify_packed_texture("Deformation") is None)
check("empty name is safe", EL.classify_packed_texture("") is None)
check("None is safe", EL.classify_packed_texture(None) is None)

print("\n=== 2. glTF texture injection ===")
tmp = tempfile.mkdtemp()
gltf_path = os.path.join(tmp, "SM_Buoy.gltf")
base_doc = {
    "asset": {"version": "2.0"},
    "materials": [{"name": "MI_Old_Buoys_NN_02a"}, {"name": "MI_Unknown"}],
    "meshes": [], "nodes": [],
}
with open(gltf_path, "w", encoding="utf-8") as f:
    json.dump(base_doc, f)

params = {"MI_Old_Buoys_NN_02a": {
    "albedo_texture": "TX_Old_Bouys_NN_02a_ALB",
    "normal_texture": "TX_Old_Bouys_NN_02a_NRM",
    "packed_texture": "TX_Old_Bouys_NN_02a_RMA",
    "packed_channels": {"roughness": 0, "metallic": 1, "ao": 2},
}}

touched = EX.inject_texture_references(gltf_path, params, "../textures")
check("one material wired", touched == 1, touched)

with open(gltf_path, encoding="utf-8") as f:
    doc = json.load(f)

uris = [i["uri"] for i in doc.get("images", [])]
check("albedo uri spells the real relative path",
      "../textures/TX_Old_Bouys_NN_02a_ALB.png" in uris, uris)
check("normal uri present", "../textures/TX_Old_Bouys_NN_02a_NRM.png" in uris, uris)
check("packed RMA map NOT injected (glTF channel order differs)",
      not any("RMA" in u for u in uris), uris)

mat0 = doc["materials"][0]
bct = mat0.get("pbrMetallicRoughness", {}).get("baseColorTexture")
check("baseColorTexture wired on the known material", bct is not None, mat0)
check("normalTexture wired", mat0.get("normalTexture") is not None)
check("unknown material left untouched",
      "pbrMetallicRoughness" not in doc["materials"][1], doc["materials"][1])

# every texture index must resolve to a real image
for t in doc.get("textures", []):
    check("texture.source %d resolves to an image" % t["source"],
          0 <= t["source"] < len(doc["images"]))
for m in doc["materials"]:
    for ref in [m.get("pbrMetallicRoughness", {}).get("baseColorTexture"), m.get("normalTexture")]:
        if ref:
            check("material texture index %d resolves" % ref["index"],
                  0 <= ref["index"] < len(doc["textures"]))

print("\n--- idempotence: re-running must not duplicate images ---")
EX.inject_texture_references(gltf_path, params, "../textures")
with open(gltf_path, encoding="utf-8") as f:
    doc2 = json.load(f)
check("re-injection does not grow the image list",
      len(doc2["images"]) == len(doc["images"]),
      "%d -> %d" % (len(doc["images"]), len(doc2["images"])))

print("\n--- shared texture is only added once ---")
p2 = os.path.join(tmp, "b.gltf")
with open(p2, "w", encoding="utf-8") as f:
    json.dump({"asset": {"version": "2.0"},
               "materials": [{"name": "A"}, {"name": "B"}]}, f)
shared = {"A": {"albedo_texture": "T_Shared"}, "B": {"albedo_texture": "T_Shared"}}
EX.inject_texture_references(p2, shared, "../textures")
with open(p2, encoding="utf-8") as f:
    d3 = json.load(f)
check("two materials sharing a texture produce one image", len(d3["images"]) == 1, d3["images"])

print("\n--- no materials / missing file are handled ---")
p3 = os.path.join(tmp, "c.gltf")
with open(p3, "w", encoding="utf-8") as f:
    json.dump({"asset": {"version": "2.0"}}, f)
check("gltf with no materials returns 0", EX.inject_texture_references(p3, params) == 0)
check("missing file returns 0 rather than raising",
      EX.inject_texture_references(os.path.join(tmp, "nope.gltf"), params) == 0)

# ------------------------------------------------- retargeting on transfer
# A .gltf exported with textures beside it says "./TX_Foo.png". The transfer
# into a Godot project splits models/ from textures/, so that uri then
# resolves against res://models/ and Godot reports "Can't open file from
# path" -- the exact failure this reproduces.
def _uris(path):
    with open(path, encoding="utf-8") as f:
        return [i.get("uri") for i in json.load(f).get("images", [])]


def _write_gltf(path, uris):
    with open(path, "w", encoding="utf-8") as f:
        json.dump({
            "asset": {"version": "2.0"},
            "materials": [{"name": "M"}],
            "images": [{"uri": u} for u in uris],
            "textures": [{"source": i} for i in range(len(uris))],
        }, f)


side_by_side = os.path.join(tmp, "sbs.gltf")
_write_gltf(side_by_side, ["./TX_Foo_ALB.png", "./TX_Foo_NRM.png"])
n = EX.retarget_gltf_textures(side_by_side, "../textures")
check("retarget rewrites every sibling uri", n == 2)
check("retargeted uris point at ../textures/",
      _uris(side_by_side) == ["../textures/TX_Foo_ALB.png", "../textures/TX_Foo_NRM.png"])

bare = os.path.join(tmp, "bare.gltf")
_write_gltf(bare, ["TX_Bare.png"])
EX.retarget_gltf_textures(bare, "../textures")
check("bare filename uri is retargeted too", _uris(bare) == ["../textures/TX_Bare.png"])

already = os.path.join(tmp, "already.gltf")
_write_gltf(already, ["../textures/TX_Foo_ALB.png"])
check("already-correct uri is left alone (0 changes)",
      EX.retarget_gltf_textures(already, "../textures") == 0)
check("already-correct uri unchanged on disk",
      _uris(already) == ["../textures/TX_Foo_ALB.png"])

embedded = os.path.join(tmp, "embedded.gltf")
_write_gltf(embedded, ["data:image/png;base64,iVBORw0KGgo="])
check("embedded data: uri is not touched",
      EX.retarget_gltf_textures(embedded, "../textures") == 0)

deep = os.path.join(tmp, "deep.gltf")
_write_gltf(deep, ["../some/other/place/TX_Foo_ALB.png"])
EX.retarget_gltf_textures(deep, "../textures")
check("a wrong nested path is rewritten to the basename",
      _uris(deep) == ["../textures/TX_Foo_ALB.png"])

check("retarget on a missing file returns 0 rather than raising",
      EX.retarget_gltf_textures(os.path.join(tmp, "nope.gltf")) == 0)

no_images = os.path.join(tmp, "noimg.gltf")
with open(no_images, "w", encoding="utf-8") as f:
    json.dump({"asset": {"version": "2.0"}}, f)
check("gltf with no images returns 0", EX.retarget_gltf_textures(no_images) == 0)

print("\n" + "=" * 60)
if FAIL:
    print("FAILURES (%d): %s" % (len(FAIL), FAIL))
    sys.exit(1)
print("ALL MATERIAL CHECKS PASSED")
