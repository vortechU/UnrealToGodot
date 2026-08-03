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

    # The decal atlas crop lives in the expression graph, not in any parameter.
    # This is the ONLY route into it: Material.expressions has been protected
    # since UE 5.7, and MaterialEditorOnlyData exposes no expression list either
    # (both confirmed against UE 5.8 on 2026-08-03).
    @staticmethod
    def get_material_expressions(material):
        return list(getattr(material, "_expressions", []))


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

# The "_B" base-colour suffix (TreatmentStation's convention) must classify on a
# TEXTURE name but must not leak into parameter names, where "Tex B" is a generic
# slot letter -- the two candidates go through the same classifier.
check("'T_Cardboard_Box_1_B' is albedo as a texture name",
      EL.classify_texture_role("T_Cardboard_Box_1_B", is_texture_name=True) == "albedo_texture",
      EL.classify_texture_role("T_Cardboard_Box_1_B", is_texture_name=True))
check("bare 'Tex B' is still NOT albedo as a parameter name",
      EL.classify_texture_role("Tex B") is None)
check("no param name at all falls through to a '_B' texture name",
      EL.resolve_texture_role("", "T_Air_Compressor_1_B") == ("albedo_texture", None),
      EL.resolve_texture_role("", "T_Air_Compressor_1_B"))
check("'_RMO' packs as roughness/metallic/occlusion",
      EL.classify_packed_texture("T_Cardboard_Box_1_RMO") == {"roughness": 0, "metallic": 1, "ao": 2},
      EL.classify_packed_texture("T_Cardboard_Box_1_RMO"))
check("a '_RMO' set resolves with no param name",
      EL.resolve_texture_role("", "T_Cardboard_Box_1_RMO")[0] == "packed")
check("'_N' still normal with no param name",
      EL.resolve_texture_role("", "T_Cardboard_Box_1_N") == ("normal_texture", None))

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

# The light component hierarchy, mirroring UE 5.7's exactly: SpotLightComponent
# derives from PointLightComponent (so it must be classified first) while
# RectLightComponent does NOT, and SkyLightComponent is not a LightComponent at
# all -- which is what keeps a component scan from swallowing the sky light.
u.LightComponent = type("LightComponent", (), {})
u.LocalLightComponent = type("LocalLightComponent", (u.LightComponent,), {})
u.PointLightComponent = type("PointLightComponent", (u.LocalLightComponent,), {})
u.SpotLightComponent = type("SpotLightComponent", (u.PointLightComponent,), {})
u.RectLightComponent = type("RectLightComponent", (u.LocalLightComponent,), {})
u.DirectionalLightComponent = type("DirectionalLightComponent", (u.LightComponent,), {})
u.SkyLightComponent = type("SkyLightComponent", (), {})

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
    """A DecalComponent with the CDO defaults probed off UE 5.7."""

    def __init__(self, material, name="NewDecalComponent", **overrides):
        self.name = name
        self._props = {"decal_material": material, "sort_order": 0, "decal_size": None,
                       "decal_color": None, "fade_screen_size": 0.0,
                       "visible": True, "hidden_in_game": False}
        self._props.update(overrides)

    def get_editor_property(self, key):
        if key not in self._props:
            raise Exception("no property %r" % key)
        return self._props[key]

    def get_name(self):
        return self.name

    def get_world_transform(self):
        return _FakeTransform()


class _FakeDecalActor:
    def __init__(self, material, comps=None):
        self._comps = comps if comps is not None else [_FakeDecalComp(material)]

    def get_components_by_class(self, cls):
        return self._comps

    def get_actor_label(self):
        return "Decal_0"

    def get_actor_transform(self):
        return _FakeTransform()


def _decal_entry(material, **comp_kwargs):
    comp = _FakeDecalComp(material, **comp_kwargs)
    return EE._build_decal_entries(_FakeDecalActor(material, [comp]), set())[0]


def _decal_textures(material):
    return _decal_entry(material)["textures"]


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


print("\n=== 6b. Decal atlas cropping (uv_rect) ===")
# Values below are the REAL ones read out of UE 5.8 on 2026-08-03 for
# SpaceshipInterior's decal materials -- four TextureCoordinate nodes cutting
# four quadrants out of one 1024x1024 T_Scifi_Signs_Decal_BC sheet. Before this,
# every one of them bound the whole sheet and the "Exit" decal showed all four
# signs at once.


class _FakeTexCoord:
    """unreal.MaterialExpressionTextureCoordinate -- matched by CLASS NAME."""

    def __init__(self, u_tiling=1.0, v_tiling=1.0):
        self._p = {"u_tiling": u_tiling, "v_tiling": v_tiling, "coordinate_index": 0}

    def get_editor_property(self, key):
        if key not in self._p:
            raise Exception("no property %r" % key)
        return self._p[key]


# Named so type(ex).__name__ contains "TextureCoordinate", as the engine's does.
_FakeTexCoord.__name__ = "MaterialExpressionTextureCoordinate"


class _FakeOtherExpr:
    pass


_FakeOtherExpr.__name__ = "MaterialExpressionConstant3Vector"


class _BaseMat:
    """A base unreal.Material carrying an expression graph."""

    def __init__(self, expressions, name="M_Scifi_Signs_Decal"):
        self._expressions = expressions
        self._name = name

    def get_editor_property(self, key):
        raise Exception("no property %r" % key)

    def get_name(self):
        return self._name


def _uv(u_tiling, v_tiling, extra=()):
    mat = _BaseMat([_FakeOtherExpr(), _FakeTexCoord(u_tiling, v_tiling)] + list(extra))
    return EE._decal_uv_rect(mat)


def _close(a, b, eps=1e-9):
    return a is not None and b is not None and all(abs(x - y) < eps for x, y in zip(a, b))


rect, fu, fv = _uv(0.5, 0.5)          # M_Scifi_Signs_Decal_01 -- the Exit sign
check("+0.5/+0.5 crops to the top-LEFT quadrant, upright",
      _close(rect, [0.0, 0.0, 0.5, 0.5]) and not fu and not fv, (rect, fu, fv))

rect, fu, fv = _uv(-0.5, 0.5)         # M_Scifi_Signs_Decal_02
check("-0.5/+0.5 crops to the top-RIGHT quadrant, mirrored in U",
      _close(rect, [0.5, 0.0, 0.5, 0.5]) and fu and not fv, (rect, fu, fv))

rect, fu, fv = _uv(0.5, -0.5)         # M_Scifi_Signs_Decal_03
check("+0.5/-0.5 crops to the bottom-LEFT quadrant, mirrored in V",
      _close(rect, [0.0, 0.5, 0.5, 0.5]) and not fu and fv, (rect, fu, fv))

rect, fu, fv = _uv(-0.5, -0.5)        # M_Scifi_Signs_Decal_04
check("-0.5/-0.5 crops to the bottom-RIGHT quadrant, mirrored in both",
      _close(rect, [0.5, 0.5, 0.5, 0.5]) and fu and fv, (rect, fu, fv))

rect, fu, fv = _uv(1.0, 0.5)          # M_Scifi_Stripe_Decal_01 -- the plain bar
check("1.0/+0.5 crops to the TOP HALF, full width",
      _close(rect, [0.0, 0.0, 1.0, 0.5]) and not fu and not fv, (rect, fu, fv))

rect, fu, fv = _uv(1.0, -0.5)         # M_Scifi_Stripe_Decal_02 -- the chevron
check("1.0/-0.5 crops to the BOTTOM HALF, mirrored in V",
      _close(rect, [0.0, 0.5, 1.0, 0.5]) and not fu and fv, (rect, fu, fv))

# The whole point of omitting the field: an ordinary decal's entry must not
# change at all just because this feature exists.
check("1.0/1.0 records NO crop (samples the whole sheet)",
      _uv(1.0, 1.0) == (None, False, False), _uv(1.0, 1.0))

# Refusals. A Godot Decal cannot repeat a texture inside its box, and guessing
# which of several TextureCoordinate nodes feeds albedo would bind a wrong cell.
check("tiling > 1 is refused, not approximated", _uv(2.0, 1.0)[0] is None, _uv(2.0, 1.0))
check("tiling < -1 is refused too", _uv(-4.0, 1.0)[0] is None, _uv(-4.0, 1.0))
check("a 0 tiling (degenerate) is refused", _uv(0.0, 1.0)[0] is None, _uv(0.0, 1.0))
check("two TextureCoordinate nodes -> refused rather than guessed",
      _uv(0.5, 0.5, extra=[_FakeTexCoord(0.25, 0.25)])[0] is None)
check("a graph with no TextureCoordinate at all records no crop",
      EE._decal_uv_rect(_BaseMat([_FakeOtherExpr()])) == (None, False, False))
check("a null material is handled", EE._decal_uv_rect(None) == (None, False, False))

# -1 mirrors the whole texture: a real crop even though the rect is full.
rect, fu, fv = _uv(-1.0, 1.0)
check("-1.0 is a full-width MIRROR, and is still recorded",
      _close(rect, [0.0, 0.0, 1.0, 1.0]) and fu and not fv, (rect, fu, fv))

# End to end: the rect has to reach the schema entry the importer reads.
_atlas_mi = _MI({"texture_parameter_values": [_TexParam("Diff", _Tex("T_Signs_BC"))],
                 "scalar_parameter_values": [], "vector_parameter_values": [],
                 "parent": _BaseMat([_FakeTexCoord(-0.5, 0.5)])})
_at = _decal_textures(_atlas_mi)
check("the crop reaches textures.uv_rect through a MaterialInstance's parent",
      _close(_at.get("uv_rect"), [0.5, 0.0, 0.5, 0.5]), _at.get("uv_rect"))
check("...along with its flips", _at.get("flip_u") is True and _at.get("flip_v") is False, _at)
check("an uncropped decal has no uv_rect key at all",
      "uv_rect" not in _decal_textures(_packed_mi("T_P_D", "ORM", "T_P_ORM")))


print("\n=== 6c. Decal atlas cropping via scalar parameters (M_decal shape) ===")
# ModularSciFiStation's M_decal crops with TextureCoordinate(1,1) -> Divide ->
# Add -> sampler, choosing the cell per INSTANCE with scalar parameters. Every
# expression INPUT is protected in Python (probed on UE 5.8: A/B on
# Divide/Add/AppendVector and Coordinates on the sampler all raise), so the
# formula was pinned down from the values instead -- UV Divide is 4.0 on all 16
# instances and U/V Tile only ever take {0, .25, .5, .75}, i.e. fractions, which
# fits uv/divide + (u,v). Confirmed cell by cell against T_numbers_01_basecolor:
# the 4x4 grid puts MI_numbers_01_red on "1" and MI_shape_u_white on the "U".


def _expr(class_name):
    cls = type("E", (), {})
    cls.__name__ = class_name
    return cls()


def _atlas_graph():
    """The M_decal node set: a (1,1) TexCoord plus the divide/offset trio."""
    return [_FakeTexCoord(1.0, 1.0),
            _expr("MaterialExpressionDivide"),
            _expr("MaterialExpressionAdd"),
            _expr("MaterialExpressionAppendVector")]


class _ParamBaseMat(_BaseMat):
    """A base Material exposing scalar parameters, as _FakeMEL reads them."""

    def __init__(self, expressions, scalars, name="M_decal"):
        _BaseMat.__init__(self, expressions, name)
        self._scalarparams = dict(scalars)


def _atlas_mat(u, v, divide, names=("U Tile", "V Tile", "UV Divide"),
               expressions=None):
    scalars = {names[0]: u, names[1]: v, names[2]: divide}
    return _ParamBaseMat(
        _atlas_graph() if expressions is None else expressions, scalars)


rect, fu, fv = EE._decal_uv_rect(_atlas_mat(0.0, 0.0, 4.0))     # MI_numbers_01_red
check("U/V Tile 0,0 over UV Divide 4 is the top-left cell of a 4x4 grid",
      _close(rect, [0.0, 0.0, 0.25, 0.25]) and not fu and not fv, (rect, fu, fv))

rect, _, _ = EE._decal_uv_rect(_atlas_mat(0.75, 0.5, 4.0))      # MI_shape_u_white
check("0.75,0.5 is the 'U' cell (row 3, column 4)",
      _close(rect, [0.75, 0.5, 0.25, 0.25]), rect)

rect, _, _ = EE._decal_uv_rect(_atlas_mat(0.0, 0.75, 4.0))      # MI_stripes_red
check("0,0.75 is the bottom-left cell", _close(rect, [0.0, 0.75, 0.25, 0.25]), rect)

check("a 1x1 grid is not a crop",
      EE._decal_uv_rect(_atlas_mat(0.0, 0.0, 1.0)) == (None, False, False))
check("a zero divide is refused rather than dividing by zero",
      EE._decal_uv_rect(_atlas_mat(0.0, 0.0, 0.0)) == (None, False, False))
check("a cell running off the edge is refused",
      EE._decal_uv_rect(_atlas_mat(0.9, 0.0, 4.0)) == (None, False, False))

# The two gates. Names alone must not be enough, and neither must shape alone.
check("the parameters WITHOUT the divide/offset graph shape are ignored",
      EE._decal_uv_rect(_atlas_mat(0.25, 0.25, 4.0,
                                   expressions=[_FakeTexCoord(1.0, 1.0)]))
      == (None, False, False))
check("the graph shape WITHOUT the parameters is ignored",
      EE._decal_uv_rect(_ParamBaseMat(_atlas_graph(), {"Roughness": 0.5}))
      == (None, False, False))
check("only two of the three parameters is not enough",
      EE._decal_uv_rect(_ParamBaseMat(_atlas_graph(),
                                      {"U Tile": 0.25, "UV Divide": 4.0}))
      == (None, False, False))
check("an unrelated 'Tile' scalar is not read as a cell index",
      EE._decal_uv_rect(_ParamBaseMat(_atlas_graph(),
                                      {"Tile Amount": 4.0, "UV Divide Strength": 2.0,
                                       "Detail Tiling": 3.0}))
      == (None, False, False))

# A TextureCoordinate crop is the more specific signal and must win.
both = _ParamBaseMat(
    [_FakeTexCoord(0.5, 0.5), _expr("MaterialExpressionDivide"),
     _expr("MaterialExpressionAdd"), _expr("MaterialExpressionAppendVector")],
    {"U Tile": 0.75, "V Tile": 0.75, "UV Divide": 4.0})
rect, _, _ = EE._decal_uv_rect(both)
check("a real TextureCoordinate crop wins over the parameter path",
      _close(rect, [0.0, 0.0, 0.5, 0.5]), rect)


class _AtlasMI(u.MaterialInstance):
    """A MaterialInstance overriding the cell its parent defaults to."""

    def __init__(self, parent, overrides):
        self._parent = parent
        self._overrides = overrides

    def get_editor_property(self, key):
        if key == "parent":
            return self._parent
        return None

    def get_name(self):
        return "MI_shape_u_white"


_prev_instance_getter = getattr(_FakeMEL, "get_material_instance_scalar_parameter_value", None)
_FakeMEL.get_material_instance_scalar_parameter_value = staticmethod(
    lambda mi, name: getattr(mi, "_overrides", {}).get(str(name)))

_parent = _atlas_mat(0.0, 0.0, 4.0)
rect, _, _ = EE._decal_uv_rect(
    _AtlasMI(_parent, {"U Tile": 0.75, "V Tile": 0.5, "UV Divide": 4.0}))
check("an instance's OWN cell wins over the parent's default",
      _close(rect, [0.75, 0.5, 0.25, 0.25]), rect)


print("\n=== 7. Decal modulate, visibility and distance fade ===")


class _ScalarParam:
    def __init__(self, name, value):
        self.parameter_info = types.SimpleNamespace(name=name)
        self.parameter_value = value


class _LinColor:
    def __init__(self, r=1.0, g=1.0, b=1.0, a=1.0):
        self.r, self.g, self.b, self.a = r, g, b, a


def _decal_mi(scalars=(), vectors=(), textures=()):
    return _MI({"texture_parameter_values": list(textures),
                "scalar_parameter_values": list(scalars),
                "vector_parameter_values": list(vectors),
                "parent": None})


def _near(a, b, eps=1e-4):
    return abs(a - b) < eps


plain = _decal_entry(_decal_mi())
check("a decal with no tint and no DecalColor modulates white",
      plain["modulate"] == [1.0, 1.0, 1.0, 1.0], plain["modulate"])
check("a decal defaults to visible", plain["visible"] is True)
check("FadeScreenSize 0 means no distance fade",
      plain["distance_fade_begin_m"] is None and plain["distance_fade_length_m"] is None,
      plain)

tinted = _decal_entry(_decal_mi(vectors=[_ScalarParam("BaseColor", _LinColor(0.5, 0.25, 0.0, 1.0))]),
                      decal_color=_LinColor(1.0, 1.0, 1.0, 0.5))
check("the material tint reaches Decal.modulate (it used to be dropped entirely)",
      [round(c, 4) for c in tinted["modulate"]] == [0.5, 0.25, 0.0, 0.5], tinted["modulate"])

faded = _decal_entry(_decal_mi(scalars=[_ScalarParam("Opacity", 0.25)]))
check("an Opacity parameter folds into modulate's alpha",
      _near(faded["modulate"][3], 0.25), faded["modulate"])

not_opacity = _decal_entry(_decal_mi(scalars=[_ScalarParam("Opacity Mask Contrast", 0.25)]))
check("a contrast control named like opacity does NOT make the decal transparent",
      _near(not_opacity["modulate"][3], 1.0), not_opacity["modulate"])

hidden = _decal_entry(_decal_mi(), hidden_in_game=True)
check("a decal hidden in game exports as invisible", hidden["visible"] is False)

# size_m defaults to [1, 1, 1] m with no decal_size, so the lateral radius is
# 0.5 m: cull distance = 0.5 / (0.1 * tan(37.5 deg)) = 6.516 m, fading in over
# the last quarter of the way there.
dfade = _decal_entry(_decal_mi(), fade_screen_size=0.1)
check("FadeScreenSize converts to a Godot distance fade",
      _near(dfade["distance_fade_length_m"], 1.62903, 1e-3)
      and _near(dfade["distance_fade_begin_m"], 4.88709, 1e-3), dfade)

tiny = _decal_entry(_decal_mi(), fade_screen_size=1e-9)
check("an absurd fade distance is dropped rather than written into the scene",
      tiny["distance_fade_begin_m"] is None, tiny)

# Two decal components on one actor: both must survive, with unique names.
multi_actor = _FakeDecalActor(None, [_FakeDecalComp(_decal_mi(), name="DecalA"),
                                     _FakeDecalComp(_decal_mi(), name="DecalB")])
multi = EE._build_decal_entries(multi_actor, set())
check("every DecalComponent on an actor is exported, not just the first",
      len(multi) == 2, multi)
check("multiple decals on one actor get unique names",
      [e["name"] for e in multi] == ["Decal_0_DecalA", "Decal_0_DecalB"],
      [e["name"] for e in multi])

print("\n=== 8. Light units, components and properties ===")
import math

# CDO defaults probed off UE 5.7 (see scratchpad probe_light_api.py): a local
# light ships at 5000 UNITLESS, a directional at 10 lux.
_LIGHT_DEFAULTS = {
    "intensity": 5000.0, "intensity_units": "UNITLESS", "light_color": None,
    "temperature": 6500.0, "use_temperature": False, "cast_shadows": True,
    "affects_world": True, "visible": True, "hidden_in_game": False,
    "indirect_lighting_intensity": 1.0, "volumetric_scattering_intensity": 1.0,
    "specular_scale": 1.0, "max_draw_distance": 0.0, "max_distance_fade_range": 0.0,
    "attenuation_radius": 1000.0, "source_radius": 0.0, "source_length": 0.0,
    "mobility": "MOVABLE", "use_inverse_squared_falloff": True,
}
_SPOT_DEFAULTS = {"inner_cone_angle": 0.0, "outer_cone_angle": 44.0}
_RECT_DEFAULTS = {"source_width": 64.0, "source_height": 64.0}
_DIR_DEFAULTS = {
    "intensity": 10.0, "light_source_angle": 0.5357,
    "dynamic_shadow_distance_movable_light": 40000.0,
    "dynamic_shadow_distance_stationary_light": 0.0,
}


class _FakeEnum:
    def __init__(self, name):
        self.name = name


class _FakeLightComp:
    """A light component carrying UE 5.7's real CDO defaults."""

    def __init__(self, kind="point", name="LightComponent0", **overrides):
        base = dict(_LIGHT_DEFAULTS)
        if kind == "spot":
            base.update(_SPOT_DEFAULTS)
        elif kind == "rect":
            base.update(_RECT_DEFAULTS)
        elif kind == "directional":
            base = dict(base)
            base.update(_DIR_DEFAULTS)
            for gone in ("intensity_units", "attenuation_radius", "source_radius",
                         "source_length", "use_inverse_squared_falloff"):
                base.pop(gone, None)
        base.update(overrides)
        self._props = base
        self.name = name
        self.world = _FakeTransform()
        self.__class__ = _LIGHT_CLASSES[kind]

    def get_editor_property(self, key):
        if key not in self._props:
            raise Exception("no property %r" % key)
        value = self._props[key]
        if key in ("intensity_units", "mobility") and isinstance(value, str):
            return _FakeEnum(value)
        return value

    def get_name(self):
        return self.name

    def get_world_transform(self):
        return self.world


# _FakeLightComp rebinds __class__ so isinstance() sees the right UE class,
# which requires the stand-ins to share its layout.
_LIGHT_CLASSES = {
    "point": type("_FakePoint", (_FakeLightComp, u.PointLightComponent), {}),
    "spot": type("_FakeSpot", (_FakeLightComp, u.SpotLightComponent), {}),
    "rect": type("_FakeRect", (_FakeLightComp, u.RectLightComponent), {}),
    "directional": type("_FakeDir", (_FakeLightComp, u.DirectionalLightComponent), {}),
}


class _FakeLightActor:
    def __init__(self, comps, label="PointLight_0"):
        self._comps = comps
        self.label = label

    def get_components_by_class(self, cls):
        return [c for c in self._comps if isinstance(c, cls)]

    def get_actor_label(self):
        return self.label

    def get_actor_transform(self):
        return _FakeTransform()


def _light(kind="point", **overrides):
    comp = _FakeLightComp(kind, **overrides)
    return EE._build_light_entries(_FakeLightActor([comp]))[0]


def _near(a, b, tol=1e-6):
    return a is not None and abs(a - b) <= tol


# --- the headline bug: default lights were 625x too bright ------------------
default_point = _light("point")
check("UE's default local light (5000 unitless) lands on Godot's default energy",
      _near(default_point["godot_energy"], 1.0, 1e-6), default_point["godot_energy"])
check("UE's default directional light (10 lux) lands on Godot's default energy",
      _near(_light("directional")["godot_energy"], 1.0, 1e-6),
      _light("directional")["godot_energy"])
check("5000 unitless normalises to UE's own 8 candelas",
      _near(default_point["intensity_candelas"], 8.0, 1e-9),
      default_point["intensity_candelas"])

# The same physical light authored four ways must import at the same energy.
# 8 cd == 5000 unitless == 8*4pi lm (isotropic point) == 2^3 EV.
same = {
    "candela": _light("point", intensity=8.0, intensity_units="CANDELAS"),
    "unitless": default_point,
    "lumens": _light("point", intensity=8.0 * 4.0 * math.pi, intensity_units="LUMENS"),
    "ev": _light("point", intensity=3.0, intensity_units="EV"),
}
energies = {k: v["godot_energy"] for k, v in same.items()}
check("the same light authored in any unit imports at the same energy",
      all(_near(e, 1.0, 1e-6) for e in energies.values()), energies)

# A spot light's lumen->candela factor depends on its cone, per UE's
# SpotLightComponent::ComputeLightBrightness.
spot_lm = _light("spot", intensity=100.0, intensity_units="LUMENS", outer_cone_angle=44.0)
expected_cd = 100.0 / (2.0 * math.pi * (1.0 - math.cos(math.radians(44.0))))
check("spot lumens are divided by the cone's solid angle, not 4pi",
      _near(spot_lm["intensity_candelas"], expected_cd, 1e-6),
      (spot_lm["intensity_candelas"], expected_cd))

rect_lm = _light("rect", intensity=100.0, intensity_units="LUMENS")
check("rect lumens use the panel's cosine distribution (/pi)",
      _near(rect_lm["intensity_candelas"], 100.0 / math.pi, 1e-6),
      rect_lm["intensity_candelas"])

nits = _light("rect", intensity=100.0, intensity_units="NITS")
check("nits are scaled by the emissive area instead of falling back to unitless",
      _near(nits["intensity_candelas"], 100.0 * 0.64 * 0.64, 1e-9),
      nits["intensity_candelas"])

no_isf = _light("point", intensity=5000.0, intensity_units="CANDELAS",
                use_inverse_squared_falloff=False)
check("units are ignored when inverse-square falloff is off, as UE ignores them",
      _near(no_isf["godot_energy"], 1.0, 1e-6) and no_isf["intensity_units"] == "candela",
      no_isf["godot_energy"])

# --- component-level collection ---------------------------------------------
blueprint = _FakeLightActor(
    [_FakeLightComp("point", name="LampA"), _FakeLightComp("spot", name="LampB")],
    label="BP_Lamp")
entries = EE._build_light_entries(blueprint)
check("every LightComponent on an actor is exported, not just the first",
      len(entries) == 2, entries)
check("multiple lights on one actor get unique names",
      [e["name"] for e in entries] == ["BP_Lamp_LampA", "BP_Lamp_LampB"],
      [e["name"] for e in entries])
check("a lone light keeps the plain actor label",
      default_point["name"] == "PointLight_0", default_point["name"])
check("a spot component is classified as a spot, not the point light it derives from",
      [e["type"] for e in entries] == ["point", "spot"], [e["type"] for e in entries])
check("a rect component is classified as rect",
      _light("rect")["type"] == "rect")
check("an actor with no light components yields nothing",
      EE._build_light_entries(_FakeLightActor([])) == [])

# The transform must come from the COMPONENT, or a light offset inside a
# Blueprint lands on the actor's origin.
offset = _FakeLightComp("point")
offset.world.translation = _Vec(100.0, 200.0, 300.0)
placed = EE._build_light_entries(_FakeLightActor([offset]))[0]
check("a light's transform comes from the component, not the actor",
      placed["godot_transform"]["translation"] == [2.0, 3.0, -1.0],
      placed["godot_transform"]["translation"])

# --- colour, visibility and the newly mapped properties ---------------------
class _FakeColor:
    def __init__(self, r, g, b, a=255):
        self.r, self.g, self.b, self.a = r, g, b, a


u.Color = _FakeColor
u.LinearColor = lambda r, g, b, a=1.0: type("LC", (), {"r": r, "g": g, "b": b, "a": a})()
tinted = _light("point", light_color=_FakeColor(255, 128, 50))
# Expected values are FLinearColor::sRGBToLinearTable[128] and [50] verbatim,
# read out of UE 5.7's Core/Private/Math/Color.cpp.
check("an sRGB FColor light tint is decoded to linear, not just divided by 255",
      _near(tinted["color"][1], 0.2158605, 1e-6)
      and _near(tinted["color"][2], 0.0318960326156814, 1e-9),
      tinted["color"])

check("a light hidden in game exports invisible",
      _light("point", hidden_in_game=True)["visible"] is False)
check("a light excluded from the world exports invisible",
      _light("point", affects_world=False)["visible"] is False)

fade = _light("point", max_draw_distance=5000.0, max_distance_fade_range=1000.0)
check("MaxDrawDistance becomes a Godot distance fade",
      _near(fade["distance_fade_begin_m"], 40.0) and _near(fade["distance_fade_length_m"], 10.0),
      (fade["distance_fade_begin_m"], fade["distance_fade_length_m"]))
check("a light UE never culls gets no distance fade",
      default_point["distance_fade_begin_m"] is None)

sun = _light("directional")
check("the sun's angular diameter is carried across for soft shadows",
      _near(sun["source_angle_deg"], 0.5357, 1e-4), sun["source_angle_deg"])
check("UE's dynamic shadow distance is converted to metres",
      _near(sun["shadow_distance_m"], 400.0), sun["shadow_distance_m"])
check("a directional light reports lux and no candela figure",
      sun["intensity_units"] == "lux" and sun["intensity_candelas"] is None, sun)

check("source radius is exported in metres for Godot's light_size",
      _near(_light("point", source_radius=25.0)["source_radius_m"], 0.25),
      _light("point", source_radius=25.0)["source_radius_m"])
check("a rect light's panel size is carried as metres",
      _light("rect")["rect_size_m"] == [0.64, 0.64], _light("rect")["rect_size_m"])
check("specular and volumetric scattering multipliers are exported",
      _light("point", specular_scale=0.25, volumetric_scattering_intensity=2.0)["specular_scale"] == 0.25
      and _light("point", volumetric_scattering_intensity=2.0)["volumetric_scattering"] == 2.0)
check("mobility is exported for diagnostics",
      _light("point", mobility="STATIC")["mobility"] == "static")

# Defensiveness: a component that fails every property read must not take the
# level's export down with it.
class _HostileComp(u.PointLightComponent):
    def get_editor_property(self, key):
        raise Exception("boom")

    def get_name(self):
        return "Hostile"

    def get_world_transform(self):
        raise Exception("boom")


hostile = EE._build_light_entries(_FakeLightActor([_HostileComp()]))
check("an unreadable light component still produces an entry rather than raising",
      len(hostile) == 1 and hostile[0]["type"] == "point", hostile)

print("\n=== 9. Foliage / instanced meshes ===")

# UE 5.7 hierarchy, probed: FISM derives from HISM derives from ISM.
u.InstancedStaticMeshComponent = type("InstancedStaticMeshComponent", (), {})
u.HierarchicalInstancedStaticMeshComponent = type(
    "HierarchicalInstancedStaticMeshComponent", (u.InstancedStaticMeshComponent,), {})
u.FoliageInstancedStaticMeshComponent = type(
    "FoliageInstancedStaticMeshComponent", (u.HierarchicalInstancedStaticMeshComponent,), {})
u.InstancedFoliageActor = type("InstancedFoliageActor", (), {})

import export_foliage as EF

# CDO defaults probed off UE 5.7 (scratchpad probe_foliage_api.py).
_ISM_DEFAULTS = {
    "static_mesh": None, "cast_shadow": True, "visible": True, "hidden_in_game": False,
    "instance_start_cull_distance": 0, "instance_end_cull_distance": 0,
    "is_editor_only": False, "override_materials": [],
}


class _Rotator:
    def __init__(self, roll=0.0, pitch=0.0, yaw=0.0):
        self.roll, self.pitch, self.yaw = roll, pitch, yaw


class _FoliageQuat(_Quat):
    """The shared _Quat stub returns None from rotator(); unreal_transform_to_dict
    reads rotator().roll/pitch/yaw, so the fallback path needs a real one."""

    def rotator(self):
        return _Rotator()


class _FoliageTransform(u.Transform):
    """Must actually BE an unreal.Transform: _instance_world_transform
    isinstance-checks the value it gets back before packing it."""

    def __init__(self, translation, scale=(1.0, 1.0, 1.0)):
        self.translation = _Vec(*translation)
        self.rotation = _FoliageQuat(0.0, 0.0, 0.0, 1.0)
        self.scale3d = _Vec(*scale)


class _FakeClass:
    def __init__(self, name):
        self._name = name

    def get_name(self):
        return self._name


class _FakeISM:
    def __init__(self, mesh, class_name="InstancedStaticMeshComponent",
                 instances=None, **overrides):
        self._props = dict(_ISM_DEFAULTS)
        self._props["static_mesh"] = mesh
        self._props.update(overrides)
        self._class_name = class_name
        self._instances = instances if instances is not None else [
            _FoliageTransform((100.0, 200.0, 300.0), (1.0, 2.0, 3.0))]

    def get_editor_property(self, key):
        if key not in self._props:
            raise Exception("no property %r" % key)
        return self._props[key]

    def get_instance_count(self):
        return len(self._instances)

    def get_instance_transform(self, index, world_space):
        return self._instances[index]

    def get_class(self):
        return _FakeClass(self._class_name)


class _FakeFoliageActor:
    def __init__(self, comps, label="Foliage_0", is_ifa=False):
        self._comps = comps
        self.label = label
        if is_ifa:
            self.__class__ = _IFA_ACTOR

    def get_components_by_class(self, cls):
        return self._comps

    def get_actor_label(self):
        return self.label


_IFA_ACTOR = type("_FakeIFA", (_FakeFoliageActor, u.InstancedFoliageActor), {})


class _FakeMesh:
    def __init__(self, name="SM_Grass"):
        self._name = name

    def get_name(self):
        return self._name

    def get_path_name(self):
        return "/Game/%s.%s" % (self._name, self._name)


def _foliage(comp_kwargs=None, class_name="InstancedStaticMeshComponent",
             is_ifa=False, instances=None):
    comp = _FakeISM(_FakeMesh(), class_name=class_name, instances=instances,
                    **(comp_kwargs or {}))
    actor = _FakeFoliageActor([comp], is_ifa=is_ifa)
    entries = EF.collect_foliage([actor], lambda m: m.get_name(), set())
    return entries[0] if entries else None


default_foliage = _foliage()
check("a default instanced component exports visible and shadow-casting",
      default_foliage["visible"] is True and default_foliage["cast_shadow"] is True,
      default_foliage)
check("a default instanced component has no cull range",
      default_foliage["cull_begin_m"] is None and default_foliage["cull_end_m"] is None,
      (default_foliage["cull_begin_m"], default_foliage["cull_end_m"]))

# Packing: UE (100,200,300) cm, identity rotation, scale (1,2,3) becomes
# origin (uy, uz, -ux) m and basis columns scaled by (usy, usz, usx).
check("instance transforms pack as basis columns then origin, in metres",
      default_foliage["godot_transforms"] == [2.0, 0.0, 0.0,
                                              0.0, 3.0, 0.0,
                                              0.0, 0.0, 1.0,
                                              2.0, 3.0, -1.0],
      default_foliage["godot_transforms"])
check("instance_count matches the packed transforms",
      default_foliage["instance_count"] == 1, default_foliage["instance_count"])

check("a shadowless foliage component exports shadowless",
      _foliage({"cast_shadow": False})["cast_shadow"] is False)
check("a foliage component hidden in game exports invisible",
      _foliage({"hidden_in_game": True})["visible"] is False)
check("a foliage component switched off exports invisible",
      _foliage({"visible": False})["visible"] is False)

culled = _foliage({"instance_start_cull_distance": 3000,
                   "instance_end_cull_distance": 5000})
check("UE cull distances convert to metres",
      culled["cull_begin_m"] == 30.0 and culled["cull_end_m"] == 50.0,
      (culled["cull_begin_m"], culled["cull_end_m"]))

no_end = _foliage({"instance_start_cull_distance": 3000,
                   "instance_end_cull_distance": 0})
check("a start distance alone is not a cull range (UE treats end 0 as never)",
      no_end["cull_begin_m"] is None and no_end["cull_end_m"] is None,
      (no_end["cull_begin_m"], no_end["cull_end_m"]))

hard_pop = _foliage({"instance_start_cull_distance": 9000,
                     "instance_end_cull_distance": 5000})
check("a start beyond the end culls hard rather than fading backwards",
      hard_pop["cull_begin_m"] is None and hard_pop["cull_end_m"] == 50.0,
      (hard_pop["cull_begin_m"], hard_pop["cull_end_m"]))

check("an editor-only instanced component is not exported at all",
      _foliage({"is_editor_only": True}) is None)

# Source classification, which drives the node naming.
check("a component on an InstancedFoliageActor is painted foliage",
      _foliage(is_ifa=True)["source"] == "foliage")
check("a HISM component is classified hism",
      _foliage(class_name="HierarchicalInstancedStaticMeshComponent")["source"] == "hism")
check("a plain ISM component is classified ism",
      _foliage()["source"] == "ism")
check("painted foliage and plain instances get different node names",
      _foliage(is_ifa=True)["name"] == "Foliage_SM_Grass"
      and _foliage()["name"].startswith("Instances_"),
      (_foliage(is_ifa=True)["name"], _foliage()["name"]))

# Material overrides: reached through the real extract_component_material_overrides,
# which needs an asset path as well as a name.
class _OverrideMI(_MI):
    def get_name(self):
        return "MI_Grass_Winter"

    def get_path_name(self):
        return "/Game/MI_Grass_Winter.MI_Grass_Winter"


override_entry = _foliage({"override_materials": [_OverrideMI({
    "texture_parameter_values": [_TexParam("Diff", _Tex("TX_Winter_ALB"))],
    "scalar_parameter_values": [], "vector_parameter_values": [], "parent": None})]})
check("a foliage material override is exported with its parameters",
      len(override_entry["material_overrides"]) == 1
      and override_entry["material_overrides"][0]["slot_index"] == 0
      and override_entry["material_overrides"][0]["parameters"]["albedo_texture"] == "TX_Winter_ALB",
      override_entry["material_overrides"])

# Two components on one actor must both survive with unique names.
multi_actor = _FakeFoliageActor([_FakeISM(_FakeMesh("SM_Grass")),
                                 _FakeISM(_FakeMesh("SM_Grass"))], label="BP_Field")
multi = EF.collect_foliage([multi_actor], lambda m: m.get_name(), set())
check("every instanced component on an actor is exported",
      len(multi) == 2, multi)
check("colliding foliage node names are made unique",
      multi[0]["name"] != multi[1]["name"], [e["name"] for e in multi])

# Defensiveness: an unreadable component must not take the level down with it.
class _HostileISM(_FakeISM):
    def get_instance_count(self):
        raise Exception("boom")


survivors = EF.collect_foliage(
    [_FakeFoliageActor([_HostileISM(_FakeMesh()), _FakeISM(_FakeMesh("SM_Fern"))])],
    lambda m: m.get_name(), set())
check("an unreadable instanced component is skipped, not fatal",
      len(survivors) == 1 and survivors[0]["mesh_name"] == "SM_Fern", survivors)

print("\n=== 10. Instanced-component fallback when foliage export is off ===")

# Unticking "Foliage & Instances" used to DELETE instanced meshes: the exclusion
# from per-component export was unconditional, but nothing collected them as
# foliage either. They now fall through and expand to one placement per instance
# -- one placement per COMPONENT would collapse a painted field onto a single
# mesh at the component origin.
fallback_comp = _FakeISM(_FakeMesh("SM_Fence"), instances=[
    _FoliageTransform((100.0, 200.0, 300.0)),
    _FoliageTransform((0.0, 0.0, 0.0), (2.0, 2.0, 2.0)),
])
placements = EL._expand_instanced_component(
    EF, fallback_comp, "FenceISM", "SM_Fence", "SM_Fence", "/Game/SM_Fence.SM_Fence",
    [], "BP_Fence")
check("every instance becomes its own component placement",
      len(placements) == 2, len(placements))
check("expanded placements get unique names",
      [p["name"] for p in placements] == ["FenceISM_Inst0", "FenceISM_Inst1"],
      [p["name"] for p in placements])
check("an expanded placement carries the instance's own world transform",
      placements[0]["godot_world_transform"]["translation"] == [2.0, 3.0, -1.0],
      placements[0]["godot_world_transform"]["translation"])
check("an expanded placement keeps the instance's scale",
      placements[1]["godot_world_transform"]["scale"] == [2.0, 2.0, 2.0],
      placements[1]["godot_world_transform"]["scale"])
check("expanded placements point at the same mesh the foliage path would use",
      all(p["mesh_key"] == "SM_Fence" for p in placements))
check("expanded placements emit the world transform both importers actually read",
      "unreal_world_transform" in placements[0] and "godot_world_transform" in placements[0])

# A component whose instances cannot be read must not take the actor down.
class _UnreadableISM(_FakeISM):
    def get_instance_count(self):
        raise Exception("boom")


check("an unreadable instanced component expands to nothing rather than raising",
      EL._expand_instanced_component(EF, _UnreadableISM(_FakeMesh()), "X", "k", "n", "p",
                                     [], "Actor") == [])
check("expansion is a no-op when the foliage module is unavailable",
      EL._expand_instanced_component(None, fallback_comp, "X", "k", "n", "p",
                                     [], "Actor") == [])

# The generator the fallback rides on must report both spaces.
rows = list(EF.iter_instance_transforms(fallback_comp))
check("iter_instance_transforms yields (index, world, local) per instance",
      len(rows) == 2 and rows[0][0] == 0 and rows[0][1] is not None and rows[0][2] is not None,
      rows)

print("\n" + "=" * 60)
if FAIL:
    print("FAILURES (%d): %s" % (len(FAIL), FAIL))
    sys.exit(1)
print("ALL MATERIAL CHECKS PASSED")
