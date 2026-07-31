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


# Stubs UE 5.7's supported base-material texture API. The engine made
# Material.expressions unreadable from Python, so iter_base_material_textures
# now enumerates texture parameters through MaterialEditingLibrary instead.
class _FakeMEL:
    @staticmethod
    def get_texture_parameter_names(material):
        return list(getattr(material, "_texparams", {}).keys())

    @staticmethod
    def get_material_default_texture_parameter_value(material, name):
        return getattr(material, "_texparams", {}).get(str(name))

    # Scalar/vector defaults come from the same library. A base Material has no
    # *_parameter_values arrays (those hold a MaterialInstance's overrides), so
    # without these its roughness/metallic/tint never reach the layout JSON.
    @staticmethod
    def get_scalar_parameter_names(material):
        return list(getattr(material, "_scalarparams", {}).keys())

    @staticmethod
    def get_material_default_scalar_parameter_value(material, name):
        return getattr(material, "_scalarparams", {}).get(str(name))

    @staticmethod
    def get_vector_parameter_names(material):
        return list(getattr(material, "_vectorparams", {}).keys())

    @staticmethod
    def get_material_default_vector_parameter_value(material, name):
        return getattr(material, "_vectorparams", {}).get(str(name))


u.MaterialEditingLibrary = _FakeMEL
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

print("\n=== 1b. Single-role classification and texture-name fallback ===")
check("param 'Diff' is albedo (ContainersHouse pack names it exactly this)",
      EL.classify_texture_role("Diff") == "albedo_texture",
      EL.classify_texture_role("Diff"))
check("param 'Base Colour' (UK spelling) is albedo",
      EL.classify_texture_role("Base Colour") == "albedo_texture")
check("'T_CH_Walls_Metal_Diff' is albedo, not metallic",
      EL.classify_texture_role("T_CH_Walls_Metal_Diff") == "albedo_texture")
check("'T_CH_Walls_Metal_Normal' is normal, not metallic",
      EL.classify_texture_role("T_CH_Walls_Metal_Normal") == "normal_texture")
check("trailing token 'T_Crate_D' is albedo",
      EL.classify_texture_role("T_Crate_D") == "albedo_texture")
check("trailing token survives numeric suffix ('T_Crate_N_01')",
      EL.classify_texture_role("T_Crate_N_01") == "normal_texture")
check("'Specular' is unclassified", EL.classify_texture_role("Specular") is None)
check("'Darkness' is unclassified", EL.classify_texture_role("Darkness") is None)
check("empty/None are safe",
      EL.classify_texture_role("") is None and EL.classify_texture_role(None) is None)

check("unhelpful param name falls back to the texture's own name",
      EL.resolve_texture_role("Tex A", "T_CH_Bases_Diff") == ("albedo_texture", None),
      EL.resolve_texture_role("Tex A", "T_CH_Bases_Diff"))
check("a param name that says something wins over the texture name",
      EL.resolve_texture_role("Normal", "T_Weird_ORM") == ("normal_texture", None))
check("packed map found via texture-name fallback",
      EL.resolve_texture_role("Tex B", "T_Rock_ORM")[0] == "packed")
check("nothing classifiable returns (None, None)",
      EL.resolve_texture_role("Tex C", "T_Mystery") == (None, None))
check("generic 'Tex C' alone is NOT albedo (no bare-letter C rule)",
      EL.classify_texture_role("Tex C") is None)

print("\n=== 1c. extract_material_parameters on a CH-style instance ===")
# Reproduces the real MI_CH_* materials: parameters named Diff/Normal/ORM.
# "Diff" matched no albedo needle before this test's fix, so every mesh in the
# ContainersHouse map imported white (0/126 albedo in the import report).


class _TexParam:
    def __init__(self, name, tex):
        self.parameter_info = types.SimpleNamespace(name=name)
        self.parameter_value = tex


class _Tex(u.Texture):
    def __init__(self, name, path=None):
        self._n = name
        self._p = path or ("/Game/Textures/%s.%s" % (name, name))

    def get_name(self):
        return self._n

    def get_path_name(self):
        return self._p


class _MI(u.MaterialInstance):
    def __init__(self, props):
        self._p = props

    def get_editor_property(self, key):
        return self._p.get(key)

    def get_name(self):
        return "MI_CH_Bases"


ch_mi = _MI({
    "texture_parameter_values": [
        _TexParam("Diff", _Tex("T_CH_Bases_Diff")),
        _TexParam("Normal", _Tex("T_CH_Bases_Normal")),
        _TexParam("ORM", _Tex("T_CH_Bases_ORM")),
    ],
    "scalar_parameter_values": [],
    "vector_parameter_values": [],
    "parent": None,
})
ch_params = EL.extract_material_parameters(ch_mi)
check("CH albedo resolved", ch_params["albedo_texture"] == "T_CH_Bases_Diff", ch_params)
check("CH normal resolved", ch_params["normal_texture"] == "T_CH_Bases_Normal")
check("CH packed ORM resolved", ch_params["packed_texture"] == "T_CH_Bases_ORM")
check("CH ORM channels are glTF order",
      ch_params["packed_channels"] == {"ao": 0, "roughness": 1, "metallic": 2})

print("\n=== 1d. Base material via MaterialEditingLibrary (UE 5.7 path) ===")
# UE 5.7 made Material.expressions protected, so reading it raises and the old
# base-material branch harvested nothing -- textures referenced only by a base
# material silently vanished from both the export and the layout JSON. The fix
# enumerates texture parameters through MaterialEditingLibrary instead.


class _BaseMat:  # a plain Material: deliberately NOT a MaterialInstance
    def __init__(self, texparams):
        self._texparams = texparams

    def get_editor_property(self, key):
        if key == "expressions":
            # Reproduce the 5.7 failure exactly.
            raise Exception("Material: Property 'Expressions' for attribute "
                            "'expressions' on 'Material' is protected and cannot be read")
        return None

    def get_name(self):
        return "M_Containers_Master"


base_mat = _BaseMat({
    "Diff": _Tex("T_CH_Walls_Metal_Diff"),
    "Normal": _Tex("T_CH_Walls_Metal_Normal"),
    "ORM": _Tex("T_CH_Walls_Metal_ORM"),
})

# The raw helper both call sites share.
harvested = list(EX.ue2g_common.iter_base_material_textures(base_mat))
check("helper harvests all 3 base-material textures despite the 5.7 block",
      len(harvested) == 3, harvested)
check("helper preserves parameter names",
      sorted(n for n, _ in harvested) == ["Diff", "Normal", "ORM"], harvested)

base_params = EL.extract_material_parameters(base_mat)
check("base-material albedo resolved (was lost on 5.7)",
      base_params["albedo_texture"] == "T_CH_Walls_Metal_Diff", base_params)
check("base-material normal resolved",
      base_params["normal_texture"] == "T_CH_Walls_Metal_Normal")
check("base-material packed ORM resolved",
      base_params["packed_texture"] == "T_CH_Walls_Metal_ORM")
check("base-material ORM channels are glTF order",
      base_params["packed_channels"] == {"ao": 0, "roughness": 1, "metallic": 2})

# The gltf exporter's collector must gather the same textures for export.
collected = set()
EX.collect_textures_from_material(base_mat, collected)
check("collect_textures_from_material gathers all 3 base-material textures",
      {t.get_name() for t in collected} ==
      {"T_CH_Walls_Metal_Diff", "T_CH_Walls_Metal_Normal", "T_CH_Walls_Metal_ORM"},
      {t.get_name() for t in collected})

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

print("\n=== 3. Texture name collisions must not bind the wrong art ===")
# Two asset packs shipping a texture called T_Concrete_D used to write the same
# T_Concrete_D.png -- last write won, and BOTH packs' materials then bound
# whichever art landed there. Nothing downstream noticed: the uri resolved fine.
tex_a = _Tex("T_Concrete_D", "/Game/PackA/T_Concrete_D.T_Concrete_D")
tex_b = _Tex("T_Concrete_D", "/Game/PackB/T_Concrete_D.T_Concrete_D")
tex_solo = _Tex("T_Unique_D", "/Game/PackA/T_Unique_D.T_Unique_D")

by_asset, by_path = EL.build_texture_export_names([tex_a, tex_b, tex_solo])
check("colliding textures get different filenames",
      by_asset[tex_a] != by_asset[tex_b], by_asset)
check("both colliding names keep the original as a prefix",
      by_asset[tex_a].startswith("T_Concrete_D_") and by_asset[tex_b].startswith("T_Concrete_D_"),
      by_asset)
check("a unique texture name is left alone", by_asset[tex_solo] == "T_Unique_D")
check("the path map agrees with the asset map",
      by_path["/Game/PackA/T_Concrete_D.T_Concrete_D"] == by_asset[tex_a], by_path)


def _albedo_mi(tex):
    return _MI({"texture_parameter_values": [_TexParam("Diff", tex)],
                "scalar_parameter_values": [], "vector_parameter_values": [], "parent": None})


p_a = EL.extract_material_parameters(_albedo_mi(tex_a))
p_b = EL.extract_material_parameters(_albedo_mi(tex_b))
check("extraction records which asset each slot came from",
      p_a["texture_paths"]["albedo_texture"] == "/Game/PackA/T_Concrete_D.T_Concrete_D",
      p_a["texture_paths"])

EL.apply_texture_export_names(p_a, by_path)
EL.apply_texture_export_names(p_b, by_path)
check("each material now names its OWN file",
      p_a["albedo_texture"] != p_b["albedo_texture"],
      (p_a["albedo_texture"], p_b["albedo_texture"]))
check("pack A binds pack A's art", p_a["albedo_texture"] == by_asset[tex_a])
check("pack B binds pack B's art", p_b["albedo_texture"] == by_asset[tex_b])

# The whole-layout walk has to reach mesh materials, component overrides and decals.
layout = {
    "meshes": {"SM_Wall": {"materials": [
        {"parameters": EL.extract_material_parameters(_albedo_mi(tex_a))}]}},
    "actors": [{"components": [{"material_overrides": [
        {"parameters": EL.extract_material_parameters(_albedo_mi(tex_b))}]}]}],
    "decals": [{"textures": {"albedo": "T_Concrete_D", "normal": None, "orm": None,
                             "emission": None,
                             "texture_paths": {"albedo": tex_b.get_path_name()}}}],
}
EL.finalize_layout_texture_names(layout, by_path)
check("mesh material references are finalized",
      layout["meshes"]["SM_Wall"]["materials"][0]["parameters"]["albedo_texture"] == by_asset[tex_a],
      layout["meshes"]["SM_Wall"])
check("component material overrides are finalized",
      layout["actors"][0]["components"][0]["material_overrides"][0]["parameters"]["albedo_texture"]
      == by_asset[tex_b])
check("decal textures are finalized",
      layout["decals"][0]["textures"]["albedo"] == by_asset[tex_b], layout["decals"][0])

untouched = EL.extract_material_parameters(_albedo_mi(tex_a))
EL.apply_texture_export_names(untouched, {})
check("an unknown path leaves the asset name in place (no data loss)",
      untouched["albedo_texture"] == "T_Concrete_D")
check("empty/None input does not raise",
      EL.apply_texture_export_names(None, by_path) is None
      and EL.apply_texture_export_names({}, by_path) is None)


print("\n=== 4. Scalar/vector parameter classification ===")


class _LC:
    def __init__(self, r, g, b=0.0, a=1.0):
        self.r, self.g, self.b, self.a = r, g, b, a


class _ValueParam:
    def __init__(self, name, value):
        self.parameter_info = types.SimpleNamespace(name=name)
        self.parameter_value = value


check("'EmissiveColor' is NOT the albedo tint",
      EL.classify_vector_parameter("EmissiveColor") is None)
check("'SpecularColor' is NOT the albedo tint",
      EL.classify_vector_parameter("SpecularColor") is None)
check("'SSS Color' is NOT the albedo tint",
      EL.classify_vector_parameter("SSS Color") is None)
check("'BaseColor' still is the albedo tint",
      EL.classify_vector_parameter("BaseColor") == "albedo_color")
check("'Tint Colour' still is the albedo tint",
      EL.classify_vector_parameter("Tint Colour") == "albedo_color")
check("vector 'Tiling' is tiling", EL.classify_vector_parameter("Tiling") == "tiling")
check("scalar 'UVScale' is tiling", EL.classify_scalar_parameter("UVScale") == "tiling")
check("scalar 'Roughness' is roughness",
      EL.classify_scalar_parameter("Roughness") == "roughness")

emissive_mi = _MI({
    "texture_parameter_values": [],
    "scalar_parameter_values": [],
    "vector_parameter_values": [_ValueParam("EmissiveColor", _LC(3.0, 0.1, 0.1, 1.0))],
    "parent": None,
})
ep = EL.extract_material_parameters(emissive_mi)
check("an emissive-only override leaves albedo white",
      ep["albedo_color"] == [1.0, 1.0, 1.0, 1.0], ep["albedo_color"])

tiling_mi = _MI({
    "texture_parameter_values": [],
    "scalar_parameter_values": [],
    "vector_parameter_values": [_ValueParam("Tiling", _LC(4.0, 2.0))],
    "parent": None,
})
tp = EL.extract_material_parameters(tiling_mi)
check("a Vector2 'Tiling' parameter is read per-axis",
      tp["tiling"] == [4.0, 2.0], tp["tiling"])


print("\n=== 4b. Base materials keep their own scalar/vector defaults ===")


class _BaseMat2(_BaseMat):
    def __init__(self, texparams, scalars=None, vectors=None):
        _BaseMat.__init__(self, texparams)
        self._scalarparams = scalars or {}
        self._vectorparams = vectors or {}


bm = _BaseMat2(
    {"Diff": _Tex("T_Base_D")},
    scalars={"Roughness": 0.2, "Metallic": 0.9, "Tiling": 3.0},
    vectors={"BaseColor": _LC(0.5, 0.25, 0.1, 1.0), "EmissiveColor": _LC(9.0, 0.0, 0.0, 1.0)},
)
bp = EL.extract_material_parameters(bm)
check("base-material roughness default is read (was always 0.5)",
      abs(bp["roughness"] - 0.2) < 1e-9, bp["roughness"])
check("base-material metallic default is read (was always 0.0)",
      abs(bp["metallic"] - 0.9) < 1e-9, bp["metallic"])
check("base-material tiling default is read", bp["tiling"] == [3.0, 3.0], bp["tiling"])
check("base-material tint is read",
      [round(c, 4) for c in bp["albedo_color"]] == [0.5, 0.25, 0.1, 1.0], bp["albedo_color"])
check("base-material emissive is still not mistaken for the tint",
      bp["albedo_color"][0] < 1.001, bp["albedo_color"])
check("base-material textures still resolve", bp["albedo_texture"] == "T_Base_D")


print("\n=== 5. glTF PBR factors ===")
# UE's exporter may emit a material with no pbrMetallicRoughness block at all.
# glTF's spec default is metallicFactor 1.0 -- i.e. every mesh renders chrome,
# in a standalone preview and in the direct .tscn path, which does no material
# rebuild of its own.
fac_path = os.path.join(tmp, "factors.gltf")
with open(fac_path, "w", encoding="utf-8") as f:
    json.dump({"asset": {"version": "2.0"},
               "materials": [{"name": "M_Plain"}, {"name": "M_Packed"}]}, f)

fac_params = {
    "M_Plain": {"albedo_color": [0.5, 0.25, 0.125, 1.0], "roughness": 0.3, "metallic": 0.0},
    "M_Packed": {"albedo_color": [1.0, 1.0, 1.0, 1.0], "roughness": 1.0, "metallic": 1.0,
                 "packed_texture": "T_Foo_ORM",
                 "packed_channels": {"ao": 0, "roughness": 1, "metallic": 2}},
}
EX.inject_texture_references(fac_path, fac_params, "../textures")
with open(fac_path, encoding="utf-8") as f:
    fdoc = json.load(f)
plain = fdoc["materials"][0]["pbrMetallicRoughness"]
packed = fdoc["materials"][1]["pbrMetallicRoughness"]
check("baseColorFactor written from albedo_color",
      plain["baseColorFactor"] == [0.5, 0.25, 0.125, 1.0], plain)
check("roughnessFactor written", abs(plain["roughnessFactor"] - 0.3) < 1e-9, plain)
check("metallicFactor written as 0, not the glTF default 1 (the chrome bug)",
      plain["metallicFactor"] == 0.0, plain)
check("a packed-map material does not preview as chrome",
      packed["metallicFactor"] == 0.0, packed)
check("a packed-map material previews fully rough rather than mirror",
      packed["roughnessFactor"] == 1.0, packed)

# Out-of-range values from a material must not produce an invalid glTF.
wild_path = os.path.join(tmp, "wild.gltf")
with open(wild_path, "w", encoding="utf-8") as f:
    json.dump({"asset": {"version": "2.0"}, "materials": [{"name": "M"}]}, f)
EX.inject_texture_references(wild_path, {"M": {"albedo_color": [4.0, -1.0, 0.5, 1.0],
                                               "roughness": 7.0, "metallic": -3.0}})
with open(wild_path, encoding="utf-8") as f:
    wpbr = json.load(f)["materials"][0]["pbrMetallicRoughness"]
check("HDR/negative factors are clamped into glTF's valid range",
      wpbr["baseColorFactor"] == [1.0, 0.0, 0.5, 1.0]
      and wpbr["roughnessFactor"] == 1.0 and wpbr["metallicFactor"] == 0.0, wpbr)

# The injected uri must name the file that was actually written.
coll_path = os.path.join(tmp, "collide.gltf")
with open(coll_path, "w", encoding="utf-8") as f:
    json.dump({"asset": {"version": "2.0"}, "materials": [{"name": "M_A"}]}, f)
coll_params = {"M_A": {"albedo_texture": "T_Concrete_D",
                       "texture_paths": {"albedo_texture": tex_b.get_path_name()}}}
EX.inject_texture_references(coll_path, coll_params, "../textures", by_path)
with open(coll_path, encoding="utf-8") as f:
    curis = [i["uri"] for i in json.load(f)["images"]]
check("a colliding texture's uri points at its own exported file",
      curis == ["../textures/%s.png" % by_asset[tex_b]], curis)


print("\n=== 6. Decal ORM binding ===")
u.DecalComponent = type("DecalComponent", (), {})
import export_environment as EE

check("Godot's decal ORM order matches the ORM/ARM layout",
      EE._GODOT_DECAL_ORM_CHANNELS == EL._PACKED_LAYOUTS["orm"],
      EE._GODOT_DECAL_ORM_CHANNELS)


class _FakeTransform:
    def __init__(self):
        self.translation = _Vec(0.0, 0.0, 0.0)
        self.rotation = _Quat(0.0, 0.0, 0.0, 1.0)
        self.scale3d = _Vec(1.0, 1.0, 1.0)


class _FakeDecalComp:
    def __init__(self, material):
        self._props = {"decal_material": material, "sort_order": 0, "decal_size": None}

    def get_editor_property(self, key):
        return self._props.get(key)


class _FakeDecalActor:
    def __init__(self, material):
        self._c = _FakeDecalComp(material)

    def get_component_by_class(self, cls):
        return self._c

    def get_actor_label(self):
        return "Decal_0"

    def get_actor_transform(self):
        return _FakeTransform()


def _decal_textures(material):
    return EE._build_decal_entry(_FakeDecalActor(material), set())["textures"]


def _packed_mi(albedo_name, packed_param, packed_name):
    return _MI({"texture_parameter_values": [
        _TexParam("Diff", _Tex(albedo_name)),
        _TexParam(packed_param, _Tex(packed_name)),
    ], "scalar_parameter_values": [], "vector_parameter_values": [], "parent": None})


dt = _decal_textures(_packed_mi("T_Splat_D", "ORM", "T_Splat_ORM"))
check("a real ORM-ordered packed map IS bound (it never was before)",
      dt["orm"] == "T_Splat_ORM", dt)
check("the decal records the ORM texture's asset path for renaming",
      dt["texture_paths"].get("orm") is not None, dt["texture_paths"])
check("decal albedo still resolves", dt["albedo"] == "T_Splat_D")

rma = _decal_textures(_packed_mi("T_Splat2_D", "RMA", "T_Splat2_RMA"))
check("an RMA-ordered map is NOT bound to a decal (channels would be swapped)",
      rma["orm"] is None, rma)

rough = _decal_textures(_packed_mi("T_Splat3_D", "Roughness", "T_Splat3_R"))
check("a greyscale roughness map is NOT bound as ORM (would read 90% metallic)",
      rough["orm"] is None, rough)
check("but its albedo still binds", rough["albedo"] == "T_Splat3_D")

print("\n" + "=" * 60)
if FAIL:
    print("FAILURES (%d): %s" % (len(FAIL), FAIL))
    sys.exit(1)
print("ALL MATERIAL CHECKS PASSED")
