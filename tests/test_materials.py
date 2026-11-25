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

# The whole-layout walk has to reach mesh materials, component overrides,
# foliage overrides, decals, and landscape material textures.
layout = {
    "meshes": {"SM_Wall": {"materials": [
        {"parameters": EL.extract_material_parameters(_albedo_mi(tex_a))}]}},
    "actors": [{"components": [{"material_overrides": [
        {"parameters": EL.extract_material_parameters(_albedo_mi(tex_b))}]}]}],
    "foliage": [{"name": "Foliage_SM_Wall", "material_overrides": [
        {"parameters": EL.extract_material_parameters(_albedo_mi(tex_b))}]}],
    "decals": [{"textures": {"albedo": "T_Concrete_D", "normal": None, "orm": None,
                             "emission": None,
                             "texture_paths": {"albedo": tex_b.get_path_name()}}}],
    "landscapes": [{"material": {"textures": [
        {"parameter": "", "texture": "T_Concrete_D", "path": tex_a.get_path_name(), "role": "albedo"},
        {"parameter": "", "texture": "T_Concrete_D", "path": tex_b.get_path_name(), "role": "albedo"},
    ]}}],
}
EL.finalize_layout_texture_names(layout, by_path)
check("mesh material references are finalized",
      layout["meshes"]["SM_Wall"]["materials"][0]["parameters"]["albedo_texture"] == by_asset[tex_a],
      layout["meshes"]["SM_Wall"])
check("component material overrides are finalized",
      layout["actors"][0]["components"][0]["material_overrides"][0]["parameters"]["albedo_texture"]
      == by_asset[tex_b])
check("foliage material overrides are finalized",
      layout["foliage"][0]["material_overrides"][0]["parameters"]["albedo_texture"]
      == by_asset[tex_b], layout["foliage"][0])
check("decal textures are finalized",
      layout["decals"][0]["textures"]["albedo"] == by_asset[tex_b], layout["decals"][0])
check("landscape textures with colliding names disambiguate by path",
      layout["landscapes"][0]["material"]["textures"][0]["texture"] == by_asset[tex_a]
      and layout["landscapes"][0]["material"]["textures"][1]["texture"] == by_asset[tex_b],
      layout["landscapes"][0])

# Standalone roughness/metallic textures must promote unset scalars to 1.0 so
# Godot does not zero out metallic_texture (default 0.0) or halve roughness_texture (default 0.5).
sep_mi = _MI({
    "texture_parameter_values": [
        _TexParam("Roughness", _Tex("T_Steel_R")),
        _TexParam("Metallic", _Tex("T_Steel_M")),
    ],
    "scalar_parameter_values": [],
    "vector_parameter_values": [],
    "parent": None,
})
sep_params = EL.extract_material_parameters(sep_mi)
check("standalone roughness_texture promotes unset roughness scalar to 1.0",
      sep_params["roughness"] == 1.0, sep_params)
check("standalone metallic_texture promotes unset metallic scalar to 1.0",
      sep_params["metallic"] == 1.0, sep_params)

# Standalone maps are not injected into glTF, so preview factors must stay neutral.
sep_gltf = os.path.join(tmp, "sep.gltf")
with open(sep_gltf, "w", encoding="utf-8") as f:
    json.dump({"asset": {"version": "2.0"}, "materials": [{"name": "M_Sep"}]}, f)
EX.inject_texture_references(sep_gltf, {"M_Sep": sep_params}, "../textures")
with open(sep_gltf, encoding="utf-8") as f:
    spbr = json.load(f)["materials"][0]["pbrMetallicRoughness"]
check("standalone metallic_texture does not make glTF preview render chrome",
      spbr["metallicFactor"] == 0.0 and spbr["roughnessFactor"] == 1.0, spbr)

# LOD filename matching must require numeric LOD suffix, not arbitrary "_LOD..." mesh names.
lod_dir = tempfile.mkdtemp()
for fname in ("SM_Rock.gltf", "SM_Rock_LOD1.gltf", "SM_Rock_LOD_Helper.gltf"):
    with open(os.path.join(lod_dir, fname), "w", encoding="utf-8") as f:
        json.dump({"asset": {"version": "2.0"}, "materials": [{"name": "M_Rock"}]}, f)
_orig_mpfm = EX.material_params_for_mesh
EX.material_params_for_mesh = lambda _m: {"M_Rock": {"albedo_texture": "T_Rock_D"}}
try:
    EX.inject_textures_for_exported_mesh(object(), lod_dir, "SM_Rock", True)
finally:
    EX.material_params_for_mesh = _orig_mpfm
check("LOD injection touches SM_Rock and SM_Rock_LOD1",
      len(json.load(open(os.path.join(lod_dir, "SM_Rock.gltf"), encoding="utf-8")).get("images", [])) == 1
      and len(json.load(open(os.path.join(lod_dir, "SM_Rock_LOD1.gltf"), encoding="utf-8")).get("images", [])) == 1)
check("LOD injection skips unrelated SM_Rock_LOD_Helper.gltf",
      "images" not in json.load(open(os.path.join(lod_dir, "SM_Rock_LOD_Helper.gltf"), encoding="utf-8")))

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


check("'EmissiveColor' routes to emissive_color, NOT albedo tint",
      EL.classify_vector_parameter("EmissiveColor") == "emissive_color")
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


print("\n=== 6d. Material graph tracing (UE 5.8 graph API) ===")
# Shapes and values are Downtown_Alley's, dumped from UE 5.8: a "Surface" map no
# name can classify, plain texture samples on a brick master, a dirt colour lerped
# over the plaster, and decals cropped by a MaterialInstanceDynamic's scalars.


class _Node:
    _count = 0

    def __init__(self, kind, inputs=(), **props):
        # kind -> its own class, because the exporter reads the class name.
        self.__class__ = type("MaterialExpression" + kind, (_Node,), {})
        _Node._count += 1
        self._name = "%s_%d" % (kind, _Node._count)
        self._inputs = list(inputs)     # (input name, node, node's output name)
        self._props = props

    def get_name(self):
        return self._name

    def get_editor_property(self, key):
        if key not in self._props:
            raise Exception("no property %r" % key)
        return self._props[key]


class _GraphMat(_BaseMat):
    _count = 0

    def __init__(self, outputs, blend_mode="OPAQUE", scalars=(), vectors=(), textures=()):
        _BaseMat.__init__(self, [])
        _GraphMat._count += 1
        self._path = "/Game/M_Graph%d" % _GraphMat._count
        self._outputs = outputs
        self._blend = blend_mode
        self._scalarparams, self._vectorparams = dict(scalars), dict(vectors)
        self._texparams = dict(textures)

    def get_base_material(self):
        return self

    def get_path_name(self):
        return self._path

    def get_editor_property(self, key):
        if key == "blend_mode":
            return self._blend
        raise Exception("no property %r" % key)


class _GraphMI(_MI):
    def get_base_material(self):
        return self.get_editor_property("parent").get_base_material()


def _graph_mi(parent, **params):
    props = {"texture_parameter_values": [], "scalar_parameter_values": [],
             "vector_parameter_values": [], "parent": parent}
    props.update(params)
    return _GraphMI(props)


def _value_param(name, value):
    return types.SimpleNamespace(parameter_info=types.SimpleNamespace(name=name),
                                 parameter_value=value)


class _RGBA:
    def __init__(self, r, g, b, a):
        self.r, self.g, self.b, self.a = r, g, b, a


class _AnyProperty:
    def __getattr__(self, name):
        return name


_GRAPH_API = {
    "get_material_property_input_node":
        lambda m, p: getattr(m, "_outputs", {}).get(p, (None, ""))[0],
    "get_material_property_input_node_output_name": lambda m, p: m._outputs[p][1],
    "get_inputs_for_material_expression": lambda m, n: [i[1] for i in n._inputs],
    "get_material_expression_input_names": lambda n: [i[0] for i in n._inputs],
    "get_input_node_output_name_for_material_expression":
        lambda n, child: [i[2] for i in n._inputs if i[1] is child][0],
}
for _name, _fn in _GRAPH_API.items():
    setattr(_FakeMEL, _name, staticmethod(_fn))
u.MaterialProperty = _AnyProperty()


def _sample(tex, param=None, uv=None):
    kind = "TextureSampleParameter2D" if param else "TextureSample"
    props = {"texture": tex}
    if param:
        props["parameter_name"] = param
    return _Node(kind, [("UVs", uv, "")] if uv else [("UVs", None, "")], **props)


def _bin(kind, a, b, **props):
    return _Node(kind, [("A",) + a, ("B",) + b], **props)


# --- packed "Surface" map, mask in its alpha ---
_surface = _sample(_Tex("T_Default_s"), "Surface")
_base_tex = _sample(_Tex("T_Default_d"), "Base Map")
_props_master = _GraphMat({
    "MP_BASE_COLOR": (_base_tex, "RGB"), "MP_ROUGHNESS": (_surface, "G"),
    "MP_METALLIC": (_surface, "B"), "MP_AMBIENT_OCCLUSION": (_surface, "R"),
    "MP_OPACITY_MASK": (_surface, "A")}, blend_mode="MASKED")
_fence = EL.extract_material_parameters(_graph_mi(_props_master, texture_parameter_values=[
    _TexParam("Surface", _Tex("T_Fence_s")), _TexParam("Base Map", _Tex("T_Fence_da"))]))
check("a 'Surface' map binds by what the graph reads from it",
      _fence["packed_texture"] == "T_Fence_s"
      and _fence["packed_channels"] == {"roughness": 1, "metallic": 2, "ao": 0}, _fence)
check("...and its scalars pass the map through",
      _fence["roughness"] == 1.0 and _fence["metallic"] == 1.0, _fence)
check("an opacity mask in the packed map's alpha is bound with its channel",
      (_fence["opacity_texture"], _fence["opacity_channel"]) == ("T_Fence_s", 3), _fence)
check("the traced albedo binds too", _fence["albedo_texture"] == "T_Fence_da", _fence)

# --- plain samples on a parent master, roughness scaled by a scalar ---
_brick_d, _brick_s = _sample(_Tex("T_Brick_d")), _sample(_Tex("T_Brick_s"))
_tint = _Node("VectorParameter", parameter_name="Brick Tint")
_dirt = _Node("VectorParameter", parameter_name="Dirt Color")
_mask = _sample(_Tex("T_Dirt_Mask"), "Dirt Mask")
_dirty = _Node("LinearInterpolate", [
    ("A", _bin("Multiply", (_brick_d, "RGB"), (_tint, "")), ""), ("B", _dirt, ""),
    ("Alpha", _mask, "R")])
_rough_scale = _Node("ScalarParameter", parameter_name="Gloss Amount", default_value=0.7)
_brick = _GraphMat({
    "MP_BASE_COLOR": (_dirty, ""),
    "MP_ROUGHNESS": (_bin("Multiply", (_brick_s, "G"), (_rough_scale, "")), ""),
    "MP_METALLIC": (_Node("Constant", r=0.25), "")},
    scalars={"Gloss Amount": 0.7, "Dirt Roughness Opacity": 0.1},
    vectors={"Dirt Color": _RGBA(0.04, 0.03, 0.02, 1.0),
             "Brick Tint Color": _RGBA(0.1, 0.1, 0.1, 1.0)},
    textures={"Dirt Mask": _Tex("T_Dirt_Mask")})
_p = EL.extract_material_parameters(_graph_mi(_brick))
check("plain TextureSample nodes on a parent master are bound",
      _p["albedo_texture"] == "T_Brick_d" and _p["packed_texture"] == "T_Brick_s"
      and _p["packed_channels"] == {"roughness": 1}, _p)
check("a colour blended over the texture is not its tint",
      _p["albedo_color"] == [1.0, 1.0, 1.0, 1.0], _p["albedo_color"])
check("the scalar that really scales roughness is used, not one named like it",
      _p["roughness"] == 0.7, _p["roughness"])
check("a constant wired into metallic is exported", _p["metallic"] == 0.25, _p["metallic"])

# --- two textures mixed into one output: nothing can be bound faithfully ---
_mixed = EL.extract_material_parameters(_GraphMat({
    "MP_ROUGHNESS": (_bin("Multiply", (_sample(_Tex("T_X")), "R"),
                          (_sample(_Tex("T_Y")), "G")), "")}))
_weighted = EL.extract_material_parameters(_GraphMat({
    "MP_ROUGHNESS": (_bin("Add", (_sample(_Tex("T_X")), "R"),
                          (_bin("Multiply", (_sample(_Tex("T_Y")), "G"), (_Node(
                              "ScalarParameter", parameter_name="Dirt Roughness"), "")), "")), "")},
    scalars={"Dirt Roughness": 0.0}))
check("a scalar weighting one of several textures is not the roughness",
      _weighted["roughness"] == 0.5, _weighted["roughness"])
check("a product of two textures binds neither",
      _mixed["packed_texture"] is None and _mixed["roughness_texture"] is None, _mixed)
_inverted = EL.extract_material_parameters(_GraphMat({
    "MP_ROUGHNESS": (_Node("OneMinus", [("Input", _sample(_Tex("T_Gloss")), "R")]), "")}))
check("an inverted (gloss) map is not bound as roughness",
      _inverted["packed_texture"] is None and _inverted["roughness_texture"] is None, _inverted)


# --- emission: only what the graph wires in counts ---
_glow = _Node("VectorParameter", parameter_name="Emissive Color")
_win_d, _win_e = _sample(_Tex("T_Window_d"), "Color Map"), _sample(_Tex("T_Window_e"))
_window = _GraphMat({
    "MP_BASE_COLOR": (_win_d, "RGB"),
    "MP_EMISSIVE_COLOR": (_bin("Multiply", (_bin("Multiply", (_win_d, "RGB"),
                                                 (_win_e, "RGB")), ""), (_glow, "")), "")},
    vectors={"Emissive Color": _RGBA(0.0, 0.0, 0.0, 1.0)},
    textures={"Color Map": _Tex("T_Window_d")})
_p = EL.extract_material_parameters(_graph_mi(_window, vector_parameter_values=[
    _value_param("Emissive Color", _RGBA(1.0, 0.9, 0.8, 1.0))]))
check("the glow mask, not the color map it is multiplied with, is the emissive texture",
      (_p["emissive_texture"], _p["albedo_texture"]) == ("T_Window_e", "T_Window_d"), _p)
check("a color that reaches emission is exported", _p["emissive_color"] == [1.0, 0.9, 0.8], _p)
_p = EL.extract_material_parameters(_graph_mi(_GraphMat({
    "MP_BASE_COLOR": (_sample(_Tex("T_Post_d"), "Base Map"), "RGB")}),
    vector_parameter_values=[_value_param("Emissive Color", _RGBA(1.0, 0.9, 0.4, 1.0))],
    scalar_parameter_values=[_value_param("Emissive Strength", 4.0)]))
check("a color a Blueprint sets on a material without emission does not make it glow",
      _p["emissive_color"] is None and _p["emissive_energy"] == 1.0, _p)

# --- decal: MaterialInstanceDynamic, tint alpha unused, cropped by scalars ---
def _decal_graph_mat():
    def scalar(name, mask):
        return (_Node("ComponentMask", [("None", _Node(
            "ScalarParameter", parameter_name=name, default_value=1.0), "")], **mask), "")
    xs, ys = {"r": True}, {"g": True}
    uv = _bin("Multiply",
              (_bin("Add", (_Node("TextureCoordinate", u_tiling=1.0, v_tiling=1.0), ""),
                    (_bin("AppendVector", scalar("Offset X", xs), scalar("Offset Y", ys)), "")),
               ""),
              (_bin("AppendVector", scalar("Scale X", xs), scalar("Scale Y", ys)), ""))
    tex = _sample(_Tex("T_Wall_Paint_da"), "Decal Texture", uv)
    tint = _Node("VectorParameter", parameter_name="Color Tint")
    return _GraphMat({"MP_BASE_COLOR": (_bin("Multiply", (tex, "RGB"), (tint, "")), ""),
                      "MP_OPACITY": (tex, "A")}, blend_mode="TRANSLUCENT",
                     scalars={"Scale X": 1.0, "Scale Y": 1.0, "Offset X": 0.0, "Offset Y": 0.0},
                     vectors={"Color Tint": _RGBA(0.5, 1.0, 1.0, 0.0)},
                     textures={"Decal Texture": _Tex("T_Wall_Paint_da")})


def _decal_mid(ox, oy, sx, sy):
    return _graph_mi(_decal_graph_mat(), scalar_parameter_values=[
        _value_param(n, v) for n, v in zip(("Offset X", "Offset Y", "Scale X", "Scale Y"),
                                           (ox, oy, sx, sy))])


# The library getter refuses a dynamic instance, as the engine's does.
def _mic_only(mi, name):
    raise TypeError("not a MaterialInstanceConstant")


_FakeMEL.get_material_instance_scalar_parameter_value = staticmethod(_mic_only)
_mid = _decal_mid(6.4, 7.25, 0.2, 0.12)
_p = EL.extract_material_parameters(_mid)
check("a 'Decal Texture' parameter is the decal's albedo",
      _p["albedo_texture"] == "T_Wall_Paint_da", _p)
check("a tint's unused alpha does not make the decal invisible",
      _p["albedo_color"] == [0.5, 1.0, 1.0, 1.0], _p["albedo_color"])
check("alpha already inside the albedo needs no opacity texture",
      _p["opacity_texture"] is None, _p)
rect, fu, fv = EE._decal_uv_rect(_mid)
check("a dynamic instance's offset/scale crop is read off the graph",
      _close(rect, [0.28, 0.87, 0.2, 0.12], 1e-6) and not fu and not fv, (rect, fu, fv))
rect, _, _ = EE._decal_uv_rect(_decal_mid(3.5, 0.0, 0.25, 0.5))
check("a crop running past the edge keeps its full size", _close(rect, [0.875, 0.0, 0.25, 0.5]), rect)
check("an uncropped decal records none",
      EE._decal_uv_rect(_decal_mid(0.0, 0.0, 1.0, 1.0)) == (None, False, False))
check("a tiled decal is refused", EE._decal_uv_rect(_decal_mid(0.0, 0.0, 3.0, 1.0))[0] is None)

# --- layers blended by vertex paint become a shader (Downtown_Alley's puddles) ---
import base64
import material_shader as MS

_vc = _Node("VertexColor")


def _by_paint(dry, wet):
    return _Node("LinearInterpolate", [("A",) + dry, ("B",) + wet, ("Alpha", _vc, "G")])


def _asphalt(**nodes):
    dry = _sample(_Tex("T_Asphalt_d"), "Asphalt")
    water = _Node("VectorParameter", parameter_name="Water Color",
                  default_value=_RGBA(0.1, 0.2, 0.3, 1.0))
    rough = _Node("ScalarParameter", parameter_name="Dry Roughness", default_value=0.5)
    outputs = {"MP_BASE_COLOR": (_by_paint((dry, "RGB"), (water, "RGB")), ""),
               "MP_ROUGHNESS": (_by_paint((rough, ""), (_Node("Constant", r=0.02), "")), "")}
    outputs.update(nodes)
    return _GraphMat(outputs)


_road = EL.extract_material_parameters(_asphalt())["shader"]
check("a vertex-painted material exports a shader, its texture named like any other slot",
      _road["tex_0"] == "T_Asphalt_d" and list(_road["texture_paths"]) == ["tex_0"], _road)
_lines = [line.strip() for line in _road["code"].splitlines()]
check("...that blends by the painted channel",
      sum(line.endswith(", COLOR.g);") or ".g" in line and "mix(" in line for line in _lines) == 2
      and "uniform float p_dry_roughness = 0.5;" in _lines
      and "uniform vec4 p_water_color = vec4(0.1, 0.2, 0.3, 1.0);" in _lines, _road["code"])
_collected = set()
EL.extract_material_parameters(_asphalt(), _collected)
check("...whose textures are exported", "T_Asphalt_d" in {t.get_name() for t in _collected})
_renamed = {"shader": dict(_road, texture_paths=dict(_road["texture_paths"]))}
EL.apply_texture_export_names(_renamed, {_road["texture_paths"]["tex_0"]: "T_Asphalt_d_1234"})
check("...and follow a renamed file", _renamed["shader"]["tex_0"] == "T_Asphalt_d_1234", _renamed)
check("a material that reads no vertex paint has no shader", "shader" not in _fence and "shader" not in _p)
_warnings, _prev_warn = [], u.log_warning
u.log_warning = _warnings.append
_custom = EL.extract_material_parameters(_asphalt(MP_METALLIC=(_Node("Custom"), "")))
u.log_warning = _prev_warn
check("a node with no translation keeps the single-layer material, and says so",
      "shader" not in _custom and any("Custom" in w for w in _warnings), _warnings)

# --- the paint: read per placement, positions as glTF has them ---
_unset = object()
_painted = types.SimpleNamespace(colors=[], points=[types.SimpleNamespace(x=100.0, y=200.0, z=300.0)])
_geo = {
    "GeometryScript_SceneUtils": types.SimpleNamespace(copy_mesh_from_component=lambda *a: None),
    "GeometryScriptCopyMeshFromComponentOptions":
        lambda: types.SimpleNamespace(set_editor_property=lambda k, v: None),
    "GeometryScriptMeshReadLOD": types.SimpleNamespace,
    "GeometryScriptLODType": types.SimpleNamespace(RENDER_DATA=0),
    "DynamicMesh": lambda: None,
    "GeometryScript_VertexColors": types.SimpleNamespace(
        get_mesh_per_vertex_colors=lambda m: (m, _painted.colors)),
    "GeometryScript_MeshQueries": types.SimpleNamespace(
        get_all_vertex_positions=lambda m, skip: (m, _painted.points)),
    "GeometryScript_List": types.SimpleNamespace(
        convert_color_list_to_array=lambda l: l, convert_vector_list_to_array=lambda l: l),
}
_prev = {k: getattr(u, k, _unset) for k in _geo}
for _k, _v in _geo.items():
    setattr(u, _k, _v)
_comp = types.SimpleNamespace(get_materials=lambda m=_asphalt(): [None, m])
_painted.colors = [_RGBA(0.0, 1.0, 0.5, 1.0)]
_result = MS.extract_vertex_paint(_comp)
check("paint is exported as bytes, at glTF's positions",
      _result and _result[0] == [1.0, 3.0, 2.0]
      and base64.b64decode(_result[1]) == bytes([0, 255, 128, 255]), _result)
_painted.colors = [_RGBA(1.0, 1.0, 1.0, 1.0)]
check("an unpainted placement exports none", MS.extract_vertex_paint(_comp) is None)
del u.GeometryScript_SceneUtils
_warnings = []
u.log_warning = _warnings.append
check("without the Geometry Script plugin: no paint, one warning",
      [MS.extract_vertex_paint(_comp), MS.extract_vertex_paint(_comp)] == [None, None]
      and len(_warnings) == 1, _warnings)
u.log_warning = _prev_warn
for _k, _v in _prev.items():
    if _v is not _unset:
        setattr(u, _k, _v)
    elif hasattr(u, _k):
        delattr(u, _k)

for _name in _GRAPH_API:
    delattr(_FakeMEL, _name)
del u.MaterialProperty

_tr = {"scale": [3.0, -5.0, 2.0]}
check("a decal's scale moves into its size, keeping only a mirror",
      EE._bake_decal_scale([1.0, 2.0, 0.5], _tr) == [3.0, 10.0, 1.0]
      and _tr["scale"] == [1.0, -1.0, 1.0], _tr)


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

# If DecalColor arrives as an sRGB FColor (0..255 bytes), it must be decoded to linear 0..1.
class _FColorStub(u.Color):
    def __init__(self, r, g, b, a=255):
        self.r, self.g, self.b, self.a = r, g, b, a

_prev_lc = u.LinearColor
u.LinearColor = _LinColor
try:
    srgb_decal = _decal_entry(_decal_mi(), decal_color=_FColorStub(255, 128, 0, 128))
finally:
    u.LinearColor = _prev_lc
check("sRGB FColor decal_color is decoded to linear 0..1 rather than 0..255",
      srgb_decal["modulate"][0] == 1.0 and 0.20 < srgb_decal["modulate"][1] < 0.23
      and srgb_decal["modulate"][2] == 0.0 and abs(srgb_decal["modulate"][3] - 128.0 / 255.0) < 1e-4,
      srgb_decal["modulate"])

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
check("UE's default local light (8 cd) reflects as much light in Godot: 8 / pi",
      _near(default_point["godot_energy"], 8.0 / math.pi, 1e-6), default_point["godot_energy"])
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
      all(_near(e, 8.0 / math.pi, 1e-6) for e in energies.values()), energies)

# A spot light's lumen->candela factor depends on its cone, per UE's
# SpotLightComponent::ComputeLightBrightness.
spot_lm = _light("spot", intensity=100.0, intensity_units="LUMENS", outer_cone_angle=44.0)
expected_cd = 100.0 / (2.0 * math.pi * (1.0 - math.cos(math.radians(44.0))))
check("spot lumens are divided by the cone's solid angle, not 4pi",
      _near(spot_lm["intensity_candelas"], expected_cd, 1e-6),
      (spot_lm["intensity_candelas"], expected_cd))

check("a spot's IES profile scales it by its peak, as UE does",
      _near(_light("spot", intensity=8.0, intensity_units="CANDELAS", ies_texture=types.SimpleNamespace(
          get_editor_property=lambda k: 4.0))["intensity_candelas"], 32.0, 1e-6))

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
      _near(no_isf["godot_energy"], 8.0 / math.pi, 1e-6) and no_isf["intensity_units"] == "candela",
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

# Edge cases from second sweep: 0-degree lumen spotlight, editor-only lights/decals,
# disabled PostProcessVolume, hidden fog/sky light, and FKConvexElem.transform.
zero_cone_spot = _light("spot", intensity=100.0, intensity_units="LUMENS", outer_cone_angle=0.0)
check("0-degree lumen spotlight does not divide by zero",
      zero_cone_spot["intensity_candelas"] > 0.0, zero_cone_spot)

ed_light = EE._build_light_entries(_FakeLightActor([_FakeLightComp("point", is_editor_only=True)]))
check("editor-only light component is skipped", ed_light == [], ed_light)
ed_decal = EE._build_decal_entries(_FakeDecalActor(None, [_FakeDecalComp(_decal_mi(), is_editor_only=True)]), set())
check("editor-only decal component is skipped", ed_decal == [], ed_decal)


class _DisabledPP:
    def __init__(self, enabled=True, blend_weight=1.0):
        self._p = {"enabled": enabled, "blend_weight": blend_weight, "settings": None, "unbound": True, "priority": 5.0}

    def get_editor_property(self, k):
        return self._p[k]

    def get_actor_label(self):
        return "PP_Disabled"

    def get_actor_transform(self):
        return _FakeTransform()

    def get_actor_bounds(self, _):
        return _Vec(), _Vec(100, 100, 100)


check("disabled PostProcessVolume is skipped",
      EE._build_post_process_entry(_DisabledPP(enabled=False)) is None)
check("zero blend_weight PostProcessVolume is skipped",
      EE._build_post_process_entry(_DisabledPP(blend_weight=0.0)) is None)


class _ConvexElem:
    """FKConvexElem: exposes transform + vertex_data, NOT center/rotation."""
    def __init__(self, verts, scale=(1.0, 1.0, 1.0)):
        self._props = {
            "transform": types.SimpleNamespace(
                translation=_Vec(10.0, 20.0, 30.0),
                rotation=_Quat(0.0, 0.0, 0.0, 1.0),
                scale3d=_Vec(*scale),
            ),
            "vertex_data": [_Vec(*v) for v in verts],
        }

    def get_editor_property(self, k):
        if k not in self._props:
            raise Exception("FKConvexElem has no property %r" % k)
        return self._props[k]


class _AggGeom:
    def __init__(self, convex_elems):
        self._p = {"box_elems": [], "sphere_elems": [], "sphyl_elems": [], "convex_elems": convex_elems}

    def get_editor_property(self, k):
        return self._p[k]


class _FakeSM:
    def __init__(self, convex_elems):
        self._bs = types.SimpleNamespace(get_editor_property=lambda k: _AggGeom(convex_elems))

    def get_editor_property(self, k):
        return self._bs


hull_col = EL.extract_mesh_collision(_FakeSM([
    _ConvexElem([(10, 20, 30), (0, 0, 0), (5, 5, 5)], scale=(2.0, 1.0, 0.5)),
    _ConvexElem([]),  # empty hull must not be emitted
]))
check("FKConvexElem reads transform + vertex_data without needing center/rotation",
      hull_col is not None and len(hull_col["convex_hulls"]) == 1, hull_col)
check("FKConvexElem transform translation and scale are preserved",
      hull_col["convex_hulls"][0]["godot_local_transform"]["translation"] == [0.1, 0.3, 0.2]
      and hull_col["convex_hulls"][0]["vertices"][0] == [20.0, 20.0, 15.0],
      hull_col["convex_hulls"][0])
check("mesh with only an empty convex hull returns None collision",
      EL.extract_mesh_collision(_FakeSM([_ConvexElem([])])) is None)

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

class _FogComp:
    visible = True
    hidden_in_game = False
    fog_density = 0.17
    fog_height_falloff = 2.0
    fog_inscattering_luminance = u.LinearColor(0.5, 0.75, 1.0)

    def get_editor_property(self, name):
        return getattr(self, name)


u.ExponentialHeightFogComponent = type("ExponentialHeightFogComponent", (), {})
_fog_actor = type("Fog", (), {"get_component_by_class": lambda self, cls: _FogComp()})()
check("fog color is read under its UE 5 name",
      EE._build_height_fog(_fog_actor)["color"][:3] == [0.5, 0.75, 1.0],
      EE._build_height_fog(_fog_actor))

# --- sky capture: the fit, and the render it is made from ---
def _gradient(sine):
    t = 1.0 - (1.0 - math.asin(sine) / (0.5 * math.pi)) ** 14.0
    return tuple(h + (z - h) * t for h, z in zip((0.6, 0.7, 0.8), (0.06, 0.08, 0.1)))


_sky = [(s / 100.0, 1.0, _gradient(s / 100.0)) for s in range(1, 100)]
_h, _z, _falloff = EE._fit_sky_gradient(_sky)
check("a two-color sky is recovered",
      _falloff == 14.0 and all(_near(a, b, 0.02) for a, b in zip(_h + _z, (0.6, 0.7, 0.8, 0.06, 0.08, 0.1))), (_h, _z, _falloff))
_h, _z, _ = EE._fit_sky_gradient(_sky + [(0.9, 1.0, (9000.0, 9000.0, 9000.0))])
check("a sun disc does not brighten the sky", _z[1] < 0.12, _z)


class _SkyWorld:
    """Fog below the horizon, 0.2 above it, as one capture after another would show."""
    def __init__(self, value=0.2):
        self.value, self.destroyed, self.selected, self.props, self.pitch = value, 0, None, {}, None
        self.capture_component2d = self

    get_world = get_world_location = lambda self: None
    get_selected_level_actors = lambda self: ["keep me selected"]
    set_editor_property = lambda self, k, v: self.props.__setitem__(k, v)
    capture_scene = lambda self: None

    def get_editor_property(self, name):
        return {"sky_distance_threshold": 1234.0, "lower_hemisphere_is_black": False}[name]

    def set_selected_level_actors(self, actors):
        self.selected = actors

    def spawn_actor_from_class(self, cls, loc, rot, transient=False):
        assert transient, "a saved capture actor would end up in the user's level"
        return self

    def destroy_actor(self, actor):
        self.destroyed += 1

    def set_actor_rotation(self, rot, teleport):
        self.pitch = rot.pitch

    def read(self, world, target, normalize):
        assert normalize is False, "normalizing clamps a bright sky to 1"
        return [types.SimpleNamespace(r=v, g=v, b=v) for row in range(32) for col in range(32)
                for v in [self.value if self.pitch or row < 16 else self.value * 4]]


_unset = object()
_fake = {"EditorActorSubsystem": None, "SceneCapture2D": None,
         "TextureRenderTargetFormat": types.SimpleNamespace(RTF_RGBA32F=0),
         "SceneCaptureSource": types.SimpleNamespace(SCS_SCENE_COLOR_HDR=0),
         "Rotator": lambda roll=0, pitch=0, yaw=0: types.SimpleNamespace(pitch=pitch)}
_fake_keys = list(_fake) + ["get_editor_subsystem", "RenderingLibrary"]
_prev = {k: getattr(u, k, _unset) for k in _fake_keys}
for _value in (0.2, 0.0):
    _w = _SkyWorld(_value)
    _fake.update(get_editor_subsystem=lambda cls, w=_w: w, RenderingLibrary=types.SimpleNamespace(
        create_render_target2d=lambda *a: None, read_render_target_raw=_w.read))
    for _k, _v in _fake.items():
        setattr(u, _k, _v)
    _sky_result = EE._capture_sky(_w, _w)
    if _value:
        check("a uniform sky is captured as one color, its lower half apart",
              _sky_result and all(_near(v, 0.2, 1e-6) for k in ("top", "horizon") for v in _sky_result[k])
              and all(_near(v, 0.8, 1e-6) for v in _sky_result["ground"]), _sky_result)
        check("only what is past the sky distance is captured",
              _w.props["custom_near_clipping_plane"] == 1234.0, _w.props)
    else:
        check("a render that comes back black is no sky at all", _sky_result is None, _sky_result)
    check("the capture actor is removed and the selection restored",
          _w.destroyed == 1 and _w.selected == ["keep me selected"])
for _k, _v in _prev.items():
    if _v is _unset:
        delattr(u, _k)
    else:
        setattr(u, _k, _v)

check("Lumen is recognised from the enum and the console variable",
      [EE._map_gi_method(v) for v in ("DynamicGlobalIlluminationMethod.LUMEN", 1, 0, "SCREEN_SPACE")]
      == ["lumen", "lumen", "other", "other"])

_prev_sys = getattr(u, "SystemLibrary", None)
for _ext, _want in ((0, 1.0), (1, 0.18 * 1.2 * 2.0)):
    u.SystemLibrary = type("SystemLibrary", (), {
        "get_console_variable_int_value": staticmethod(lambda name, v=_ext: v)})
    check("exposure brightness %s is exported in cd/m^2" % ("EV100" if _ext else "luminance"),
          _near(EE._exposure_luminance(1.0), _want, 1e-9), EE._exposure_luminance(1.0))
u.SystemLibrary = _prev_sys

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

print("\n=== 11. collect_environment respects include_decals / include_lights flags ===")
u.PostProcessVolume = type("PostProcessVolume", (), {})
u.ExponentialHeightFog = type("ExponentialHeightFog", (), {})
u.SkyLight = type("SkyLight", (), {})
decal_tex = _Tex("T_DecalOnly_D", "/Game/Decals/T_DecalOnly_D.T_DecalOnly_D")
decal_mi = _MI({
    "texture_parameter_values": [_TexParam("Diff", decal_tex)],
    "scalar_parameter_values": [], "vector_parameter_values": [], "parent": None,
})


class _DecalOnlyActor(_FakeDecalActor):
    def get_components_by_class(self, cls):
        return self._comps if cls is u.DecalComponent else []


decal_actor = _DecalOnlyActor(decal_mi)
tex_when_off = set()
env_no_decals = EE.collect_environment([decal_actor], tex_when_off,
                                       include_lights=True, include_decals=False)
check("disabling decals skips decal entries AND does not leak decal textures",
      env_no_decals["decals"] == [] and len(tex_when_off) == 0,
      (env_no_decals["decals"], tex_when_off))

tex_when_on = set()
env_with_decals = EE.collect_environment([decal_actor], tex_when_on,
                                         include_lights=False, include_decals=True)
check("enabling decals with lights off collects decals and their textures",
      len(env_with_decals["decals"]) == 1 and decal_tex in tex_when_on,
      (len(env_with_decals["decals"]), tex_when_on))

print("\n=== 12. Phase 1: Blend modes, two-sided, emissive, opacity & normal-map metadata ===")
check("emissive texture classified by parameter name",
      EL.classify_texture_role("EmissiveMap") == "emissive_texture")
check("emissive texture classified by trailing '_E' on texture asset only",
      EL.classify_texture_role("T_Panel_E", is_texture_name=True) == "emissive_texture"
      and EL.classify_texture_role("Tex E", is_texture_name=False) is None)
check("opacity texture classified by parameter name",
      EL.classify_texture_role("OpacityMask") == "opacity_texture")
check("emissive scalar maps to emissive_energy",
      EL.classify_scalar_parameter("EmissiveIntensity") == "emissive_energy")
check("opacity scalar maps to opacity",
      EL.classify_scalar_parameter("Opacity") == "opacity")
check("clip scalar maps to opacity_mask_clip_value",
      EL.classify_scalar_parameter("OpacityMaskClipValue") == "opacity_mask_clip_value")


class _EnumVal:
    def __init__(self, name):
        self.name = name


class _BasePropOverrides:
    def __init__(self, **props):
        self._p = props

    def get_editor_property(self, key):
        return self._p.get(key)


class _SurfaceBaseMat:
    """Standalone base-Material stub (avoids the shadowed _BaseMat at line 805)."""
    def __init__(self, texparams, scalars=None, vectors=None, surface=None):
        self._texparams = texparams or {}
        self._scalarparams = scalars or {}
        self._vectorparams = vectors or {}
        self._surface = surface or {}

    def get_editor_property(self, key):
        if key in self._surface:
            return self._surface[key]
        if key == "expressions":
            raise Exception("expressions is protected")
        return None

    def get_name(self):
        return "M_Foliage_Master"


foliage_base = _SurfaceBaseMat(
    {"Diff": _Tex("T_Leaf_D"), "OpacityMask": _Tex("T_Leaf_A"), "Emissive": _Tex("T_Leaf_E")},
    scalars={"EmissivePower": 2.5},
    vectors={"EmissiveColor": _LC(0.2, 1.0, 0.4, 1.0)},
    surface={"blend_mode": _EnumVal("BLEND_MASKED"), "two_sided": True, "opacity_mask_clip_value": 0.45},
)
fp = EL.extract_material_parameters(foliage_base)
check("base material masked blend_mode extracted", fp["blend_mode"] == "masked", fp["blend_mode"])
check("base material two_sided extracted", fp["two_sided"] is True, fp["two_sided"])
check("base material opacity_mask_clip_value extracted",
      abs(fp["opacity_mask_clip_value"] - 0.45) < 1e-6, fp["opacity_mask_clip_value"])
check("base material opacity_texture extracted", fp["opacity_texture"] == "T_Leaf_A", fp["opacity_texture"])
check("base material emissive_texture extracted", fp["emissive_texture"] == "T_Leaf_E", fp["emissive_texture"])
check("base material emissive_color extracted without staining albedo",
      fp["emissive_color"] == [0.2, 1.0, 0.4] and fp["albedo_color"] == [1.0, 1.0, 1.0, 1.0],
      (fp["emissive_color"], fp["albedo_color"]))
check("base material emissive_energy extracted", abs(fp["emissive_energy"] - 2.5) < 1e-6, fp["emissive_energy"])

# MaterialInstance base_property_overrides must override parent base material.
glass_mi = _MI({
    "texture_parameter_values": [],
    "scalar_parameter_values": [_ValueParam("Opacity", 0.35)],
    "vector_parameter_values": [],
    "base_property_overrides": _BasePropOverrides(
        override_blend_mode=True, blend_mode=_EnumVal("BLEND_TRANSLUCENT"),
        override_two_sided=True, two_sided=False,
    ),
    "parent": foliage_base,
})
gp = EL.extract_material_parameters(glass_mi)
check("MI base_property_overrides blend_mode overrides parent",
      gp["blend_mode"] == "translucent", gp["blend_mode"])
check("MI base_property_overrides two_sided=False overrides parent True",
      gp["two_sided"] is False, gp["two_sided"])
check("MI opacity scalar extracted", abs(gp["opacity"] - 0.35) < 1e-6, gp["opacity"])

# glTF preview injection writes alphaMode, alphaCutoff, doubleSided, and emissive.
p1_gltf = os.path.join(tmp, "phase1_mat.gltf")
with open(p1_gltf, "w", encoding="utf-8") as f:
    json.dump({"asset": {"version": "2.0"},
               "materials": [{"name": "M_Leaf"}, {"name": "M_Glass"}]}, f)
EX.inject_texture_references(p1_gltf, {"M_Leaf": fp, "M_Glass": gp}, "../textures")
with open(p1_gltf, encoding="utf-8") as f:
    p1_doc = json.load(f)
m_leaf, m_glass = p1_doc["materials"][0], p1_doc["materials"][1]
check("glTF masked material gets alphaMode=MASK and alphaCutoff",
      m_leaf.get("alphaMode") == "MASK" and abs(m_leaf.get("alphaCutoff", 0) - 0.45) < 1e-6, m_leaf)
check("glTF two-sided material gets doubleSided=True", m_leaf.get("doubleSided") is True, m_leaf)
check("glTF emissive material wires emissiveTexture + emissiveFactor",
      "emissiveTexture" in m_leaf and m_leaf.get("emissiveFactor") == [0.5, 1.0, 1.0], m_leaf)
check("glTF translucent material gets alphaMode=BLEND and opacity in baseColorFactor.a",
      m_glass.get("alphaMode") == "BLEND"
      and abs(m_glass["pbrMetallicRoughness"]["baseColorFactor"][3] - 0.35) < 1e-6, m_glass)


class _NormalTex(_Tex):
    def __init__(self, name, compression="TC_NORMALMAP", flip_green=False):
        _Tex.__init__(self, name)
        self._props = {
            "compression_settings": _EnumVal(compression),
            "flip_green_channel": flip_green,
        }

    def get_editor_property(self, key):
        return self._props.get(key)


n_dx = _NormalTex("T_Wall_N", compression="TC_NORMALMAP", flip_green=False)
n_gl = _NormalTex("T_Trim_N", compression="TC_NORMALMAP", flip_green=True)
n_slot_only = _NormalTex("T_Loose_N", compression="TC_DEFAULT", flip_green=False)
tex_meta = EL.build_texture_metadata(
    [n_dx, n_gl, n_slot_only],
    {n_dx: "T_Wall_N", n_gl: "T_Trim_N", n_slot_only: "T_Loose_N"},
    {"meshes": {"SM_A": {"materials": [{"parameters": {"normal_texture": "T_Loose_N"}}]}}},
)
check("DirectX normal map recorded with flip_green_channel=False",
      tex_meta.get("T_Wall_N") == {"is_normal_map": True, "flip_green_channel": False}, tex_meta)
check("OpenGL-authored normal map preserves flip_green_channel=True",
      tex_meta.get("T_Trim_N") == {"is_normal_map": True, "flip_green_channel": True}, tex_meta)
check("normal slot fallback catches TC_DEFAULT normal maps too",
      tex_meta.get("T_Loose_N") == {"is_normal_map": True, "flip_green_channel": False}, tex_meta)

print("\n=== 13. Phase 2: LOD screen-size -> switch distance extraction ===")


class _FakeStaticMeshForLOD(u.StaticMesh):
    def __init__(self, lod_count, radius_cm=200.0):
        self._lods = lod_count
        self._r = radius_cm

    def get_num_lods(self):
        return self._lods

    def get_bounds(self):
        return types.SimpleNamespace(sphere_radius=self._r)


single_lod_mesh = _FakeStaticMeshForLOD(1)
check("single-LOD mesh returns empty lods list",
      EL.extract_mesh_lods(single_lod_mesh, "SM_Single") == [])

multi_lod_mesh = _FakeStaticMeshForLOD(3, radius_cm=200.0)
u.StaticMeshEditorSubsystem = type("StaticMeshEditorSubsystem", (), {})
_fake_sm_subsys = types.SimpleNamespace(
    get_lod_count=lambda m: m._lods,
    get_lod_screen_sizes=lambda m: [1.0, 0.5, 0.2],
)
_prev_get_sub = getattr(u, "get_editor_subsystem", None)
u.get_editor_subsystem = lambda cls: _fake_sm_subsys if cls is u.StaticMeshEditorSubsystem else None
try:
    lods = EL.extract_mesh_lods(multi_lod_mesh, "SM_Cliff")
finally:
    if _prev_get_sub is None:
        delattr(u, "get_editor_subsystem")
    else:
        u.get_editor_subsystem = _prev_get_sub

check("3-LOD mesh produces 3 entries with _LOD1/_LOD2 filenames",
      len(lods) == 3 and [l["export_name"] for l in lods] == ["SM_Cliff", "SM_Cliff_LOD1", "SM_Cliff_LOD2"],
      lods)
check("LOD0 switch_distance_m is 0.0 and LOD distances strictly increase",
      lods[0]["switch_distance_m"] == 0.0
      and 0.0 < lods[1]["switch_distance_m"] < lods[2]["switch_distance_m"],
      lods)
# At 75 deg FOV, R=2.0m, screen_size=0.5 => d = 2.0 / (0.5 * tan(37.5 deg)) ~= 5.213m
check("screen_size_to_switch_distance_m matches 75-deg FOV formula",
      abs(EL.screen_size_to_switch_distance_m(2.0, 0.5) - 5.213) < 0.01,
      EL.screen_size_to_switch_distance_m(2.0, 0.5))

print("\n=== 14. Phase 3: render flags, Outliner folders, splines, probes, cameras, audio ===")


class _FakeRenderComp:
    def __init__(self, visible=True, hidden_in_game=False, cast_shadow=True,
                 collision="QUERY_AND_PHYSICS", max_draw=0.0):
        self._p = {
            "visible": visible, "hidden_in_game": hidden_in_game,
            "cast_shadow": cast_shadow, "ld_max_draw_distance": max_draw,
        }
        self._col = _EnumVal(collision)

    def get_editor_property(self, key):
        return self._p.get(key)

    def get_collision_enabled(self):
        return self._col


rf_off = EL.extract_component_render_flags(
    _FakeRenderComp(visible=True, hidden_in_game=True, cast_shadow=False,
                    collision="NO_COLLISION", max_draw=4500.0))
check("hidden_in_game + NoCollision + shadowless + max_draw_distance extracted",
      rf_off["visible"] is False and rf_off["cast_shadow"] is False
      and rf_off["collision_enabled"] is False
      and abs(rf_off["max_draw_distance_m"] - 45.0) < 1e-6, rf_off)

folder_actor = types.SimpleNamespace(get_folder_path=lambda: "\\Architecture\\Interior/")
check("Outliner folder path normalized",
      EL.extract_actor_folder_path(folder_actor) == "Architecture/Interior")

u.SplineMeshComponent = type("SplineMeshComponent", (), {})


class _FakeSplineComp(u.SplineMeshComponent):
    def __init__(self):
        self._params = types.SimpleNamespace(
            start_pos=_Vec(0.0, 0.0, 0.0), start_tangent=_Vec(400.0, 0.0, 0.0),
            start_scale=types.SimpleNamespace(x=1.0, y=1.0), start_roll=0.0,
            end_pos=_Vec(400.0, 0.0, 200.0), end_tangent=_Vec(400.0, 0.0, 0.0),
            end_scale=types.SimpleNamespace(x=1.0, y=1.0), end_roll=0.0,
        )
        self._params.get_editor_property = lambda k: getattr(self._params, k)
        self._p = {
            "spline_params": self._params,
            "forward_axis": _EnumVal("X"),
            "spline_up_dir": _Vec(0.0, 0.0, 1.0),
        }

    def get_editor_property(self, key):
        return self._p.get(key)


sp = EL.extract_spline_mesh_params(_FakeSplineComp())
check("SplineMeshComponent converts endpoints to glTF (x, z, y) meters",
      sp is not None and sp["end_pos"] == [4.0, 2.0, 0.0]
      and sp["forward_axis"] == "x" and sp["up_dir"] == [0.0, 1.0, 0.0], sp)

# Reflection probes, cameras, and audio components
u.BoxReflectionCaptureComponent = type("BoxReflectionCaptureComponent", (), {})
u.SphereReflectionCaptureComponent = type("SphereReflectionCaptureComponent", (), {})
u.CameraComponent = type("CameraComponent", (), {})
u.AudioComponent = type("AudioComponent", (), {})
u.SoundWave = type("SoundWave", (), {})


class _FakeBoxProbeComp(u.BoxReflectionCaptureComponent):
    def get_editor_property(self, k):
        return {"box_extent": _Vec(400.0, 600.0, 300.0), "box_transition_distance": 100.0,
                "brightness": 1.5, "visible": True, "hidden_in_game": False,
                "is_editor_only": False}.get(k)

    def get_world_transform(self):
        return _FakeTransform()


class _FakeCamComp(u.CameraComponent):
    def get_editor_property(self, k):
        return {"projection_mode": _EnumVal("PERSPECTIVE"), "field_of_view": 85.0,
                "ortho_width": 512.0, "ortho_near_clip_plane": 10.0,
                "ortho_far_clip_plane": 200000.0, "aspect_ratio": 1.7777,
                "is_editor_only": False}.get(k)

    def get_world_transform(self):
        return _FakeTransform()


class _FakeSoundWave(u.SoundWave):
    def get_name(self):
        return "S_Wind"

    def get_editor_property(self, k):
        return {"looping": True}.get(k)


_fake_sw = _FakeSoundWave()


class _FakeAudioComp(u.AudioComponent):
    def get_editor_property(self, k):
        att = types.SimpleNamespace(falloff_distance=3100.0, attenuation_shape_extents=_Vec(400.0, 0.0, 0.0))
        att.get_editor_property = lambda key: getattr(att, key)
        return {"sound": _fake_sw, "volume_multiplier": 0.5, "pitch_multiplier": 1.1,
                "auto_activate": True, "allow_spatialization": True,
                "attenuation_overrides": att, "is_editor_only": False}.get(k)

    def get_world_transform(self):
        return _FakeTransform()


class _FakeP3Actor:
    def __init__(self, label, comp_map):
        self._label = label
        self._map = comp_map

    def get_actor_label(self):
        return self._label

    def get_actor_transform(self):
        return _FakeTransform()

    def get_components_by_class(self, cls):
        return self._map.get(cls, [])


p3_sounds = set()
p3_env = EE.collect_environment([
    _FakeP3Actor("BoxProbe_1", {u.BoxReflectionCaptureComponent: [_FakeBoxProbeComp()]}),
    _FakeP3Actor("ShowcaseCam", {u.CameraComponent: [_FakeCamComp()]}),
    _FakeP3Actor("AmbientWind", {u.AudioComponent: [_FakeAudioComp()]}),
], set(), collected_sounds=p3_sounds)
check("reflection probe collected with Godot-swizzled full size",
      len(p3_env["reflection_probes"]) == 1
      and p3_env["reflection_probes"][0]["size_m"] == [12.0, 6.0, 8.0]
      and p3_env["reflection_probes"][0]["box_projection"] is True, p3_env["reflection_probes"])
check("camera collected with horizontal FOV and clip planes",
      len(p3_env["cameras"]) == 1 and abs(p3_env["cameras"][0]["fov_deg"] - 85.0) < 1e-6,
      p3_env["cameras"])
check("audio collected with dB conversion, attenuation radius, and SoundWave queued",
      len(p3_env["audio"]) == 1 and abs(p3_env["audio"][0]["volume_db"] - (-6.021)) < 0.05
      and abs(p3_env["audio"][0]["attenuation_radius_m"] - 35.0) < 0.01
      and _fake_sw in p3_sounds, p3_env["audio"])

print("\n=== 15. Phase 4: actor_id, blueprint_asset & world_streaming ===")


class _FakeGuid:
    """unreal.Guid as probed on UE 5.8: only to_string() yields the value."""

    def to_string(self):
        return "1564D3824FD7C91333AC658C0594F567"

    def __str__(self):
        return "<Struct 'Guid' (0x%016X) {}>" % id(self)


guid_actor = types.SimpleNamespace(
    get_editor_property=lambda k: _FakeGuid() if k == "actor_guid" else None,
    get_path_name=lambda: "/Game/Maps/Demo.Demo:PersistentLevel.Crate_1",
    get_class=lambda: types.SimpleNamespace(get_name=lambda: "BP_Lamp_C", get_path_name=lambda: "/Game/BP/BP_Lamp.BP_Lamp_C"),
)
check("extract_actor_id prefers non-zero actor_guid",
      EL.extract_actor_id(guid_actor) == "1564D3824FD7C91333AC658C0594F567")
path_only_actor = types.SimpleNamespace(
    get_editor_property=lambda k: None,
    get_path_name=lambda: "/Game/Maps/Demo.Demo:PersistentLevel.Crate_2",
)
check("extract_actor_id falls back to persistent path",
      EL.extract_actor_id(path_only_actor) == "/Game/Maps/Demo.Demo:PersistentLevel.Crate_2")
check("extract_blueprint_asset reads _C Blueprint class path",
      EL.extract_blueprint_asset(guid_actor) == "/Game/BP/BP_Lamp.BP_Lamp_C")

fake_sub_loaded = types.SimpleNamespace(
    get_editor_property=lambda k: "/Game/Maps/Sub_Loaded" if k == "world_asset_package_name" else None,
    is_level_loaded=lambda: True,
)
fake_sub_unloaded = types.SimpleNamespace(
    get_editor_property=lambda k: "/Game/Maps/Sub_Unloaded" if k == "world_asset_package_name" else None,
    is_level_loaded=lambda: False,
)
fake_wp_world = types.SimpleNamespace(
    get_editor_property=lambda k: object() if k == "world_partition" else ([fake_sub_loaded, fake_sub_unloaded] if k == "streaming_levels" else None)
)
ws = EL.inspect_world_streaming(fake_wp_world)
check("inspect_world_streaming detects World Partition and separates loaded/unloaded sublevels",
      ws is not None and ws["is_world_partition"] is True
      and ws["loaded_sublevels"] == ["/Game/Maps/Sub_Loaded"]
      and ws["unloaded_sublevels"] == ["/Game/Maps/Sub_Unloaded"], ws)

print("\n=== 16. UE 5.8 live-probe regression checks ===")
# 1) HDR Emissive Color > 1.0 (e.g. Downtown_Alley MI_Windows_Emissive (3.5, 3.354, 2.8))
_hdr_vec_param = types.SimpleNamespace(
    parameter_info=types.SimpleNamespace(name="Emissive Color"),
    parameter_value=types.SimpleNamespace(r=3.5, g=3.354, b=2.8, a=1.0),
)
hdr_mi = _MI({
    "vector_parameter_values": [_hdr_vec_param],
    "scalar_parameter_values": [],
    "texture_parameter_values": [],
    "parent": None,
})
hdr_params = EL.extract_material_parameters(hdr_mi, set())
check("HDR emissive_color > 1.0 is normalized into [0,1] RGB and peak folded into emissive_energy",
      abs(hdr_params["emissive_energy"] - 3.5) < 1e-3
      and abs(hdr_params["emissive_color"][0] - 1.0) < 1e-3
      and abs(hdr_params["emissive_color"][2] - 0.8) < 1e-3, hdr_params)

# 2) BodyInstance.collisionEnabled == "NoCollision" (without underscore, as in UE 5.8 CDO)
nocol_body = types.SimpleNamespace(get_editor_property=lambda k: "NoCollision" if k == "collision_enabled" else None)
nocol_comp = types.SimpleNamespace(get_editor_property=lambda k: nocol_body if k == "body_instance" else None)
check("BodyInstance collision_enabled 'NoCollision' (no underscore) maps to collision_enabled=False",
      EL.extract_component_render_flags(nocol_comp)["collision_enabled"] is False)

# 3) UE 5.8 World Partition lives on AWorldSettings.world_partition (and world.is_partitioned_world())
fake_ws_actor = types.SimpleNamespace(get_editor_property=lambda k: object() if k == "world_partition" else None)
fake_sub_wa = types.SimpleNamespace(
    get_editor_property=lambda k: types.SimpleNamespace(get_name=lambda: "Sub_ViaWorldAsset") if k == "world_asset" else None,
    is_level_loaded=lambda: False,
)
fake_ue58_world = types.SimpleNamespace(
    get_editor_property=lambda k: None,
    get_world_settings=lambda: fake_ws_actor,
    get_streaming_levels=lambda: [fake_sub_wa],
)
ws58 = EL.inspect_world_streaming(fake_ue58_world)
check("inspect_world_streaming reads world_partition from AWorldSettings and world_asset from LevelStreaming",
      ws58 is not None and ws58["is_world_partition"] is True
      and ws58["unloaded_sublevels"] == ["Sub_ViaWorldAsset"], ws58)

# 4) UE 5.8 AudioComponent.attenuation_overrides is a non-null struct; override_attenuation=False must use attenuation_settings
default_override_struct = types.SimpleNamespace(
    get_editor_property=lambda k: {"falloff_distance": 3600.0, "attenuation_shape_extents": _Vec(400.0, 0.0, 0.0), "spatialize": True}.get(k)
)
custom_att_struct = types.SimpleNamespace(
    get_editor_property=lambda k: {"falloff_distance": 1100.0, "attenuation_shape_extents": _Vec(200.0, 0.0, 0.0), "spatialize": True}.get(k)
)
custom_att_asset = types.SimpleNamespace(get_editor_property=lambda k: custom_att_struct if k == "attenuation" else None)


class _FakeUE58AudioComp(u.AudioComponent):
    def get_editor_property(self, k):
        return {
            "sound": _fake_sw,
            "volume_multiplier": 1.0,
            "pitch_multiplier": 1.0,
            "auto_activate": True,
            "allow_spatialization": True,
            "override_attenuation": False,
            "attenuation_overrides": default_override_struct,
            "attenuation_settings": custom_att_asset,
            "is_editor_only": False,
        }.get(k)

    def get_world_transform(self):
        return _FakeTransform()


ue58_aud = EE.collect_environment(
    [_FakeP3Actor("AssetAttAudio", {u.AudioComponent: [_FakeUE58AudioComp()]})],
    set(),
)["audio"]
check("AudioComponent with override_attenuation=False uses attenuation_settings (13m) over default struct (40m)",
      len(ue58_aud) == 1 and abs(ue58_aud[0]["attenuation_radius_m"] - 13.0) < 0.01, ue58_aud)


# 5) CineCameraComponent prefers current_horizontal_fov and perspective camera uses 4000m far default
class _FakeCineCamComp(u.CameraComponent):
    def get_editor_property(self, k):
        return {
            "projection_mode": _EnumVal("Perspective"),
            "field_of_view": 90.0,
            "current_horizontal_fov": 52.6708,
            "ortho_near_clip_plane": -768.0,
            "ortho_far_clip_plane": 2097152.0,
            "aspect_ratio": 1.777778,
            "is_editor_only": False,
        }.get(k)

    def get_world_transform(self):
        return _FakeTransform()


ue58_cams = EE.collect_environment(
    [_FakeP3Actor("CineCam", {u.CameraComponent: [_FakeCineCamComp()]})],
    set(),
)["cameras"]
check("CineCameraComponent reads current_horizontal_fov and uses clean 0.05..4000m perspective clip planes",
      len(ue58_cams) == 1 and abs(ue58_cams[0]["fov_deg"] - 52.6708) < 1e-3
      and abs(ue58_cams[0]["near_clip_m"] - 0.05) < 1e-4
      and abs(ue58_cams[0]["far_clip_m"] - 4000.0) < 1e-3, ue58_cams)

# Camera proxy meshes (SM_CineCam, MatineeCam_SM) are editor-only; a level holding
# nothing else has no meshes to export, so this returns before touching the exporter.
_eo_comp = types.SimpleNamespace(
    get_editor_property=lambda k: {"is_editor_only": True, "static_mesh": object()}.get(k))
_eo_actor = types.SimpleNamespace(get_components_by_class=lambda cls: [_eo_comp])
_eo_prev = dict(vars(u))
u.GLTFExporter = u.EditorActorSubsystem = u.StaticMeshComponent = object
u.get_editor_subsystem = lambda cls: types.SimpleNamespace(get_all_level_actors=lambda: [_eo_actor])
_eo_warned = []
u.log_warning = _eo_warned.append
try:
    _eo_result = EX.export_all_level_meshes(export_dir=tempfile.mkdtemp(), show_dialogs=False)
except Exception as e:
    _eo_result = repr(e)
finally:
    vars(u).clear()
    vars(u).update(_eo_prev)
check("export_all_level_meshes skips editor-only components",
      _eo_result == (0, 0) and "No static or skeletal" in "".join(_eo_warned), _eo_result)

print("\n" + "=" * 60)
if FAIL:
    print("FAILURES (%d): %s" % (len(FAIL), FAIL))
    sys.exit(1)
print("ALL MATERIAL CHECKS PASSED")
