"""Regression tests for the 15 second-pass fixes in the Unreal Python exporter,
material shader translator, landscape/environment/gameplay exporters, GUI, and
diagnostics (ue2g_diagnose.py).
"""
import json
import math
import os
import shutil
import sys
import tempfile
import types

TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.dirname(TESTS_DIR)
PY_DIR = os.path.join(REPO_ROOT, "UnrealToGodot", "Content", "Python")
sys.path.insert(0, PY_DIR)

# ---------------------------------------------------------------- unreal stub
u = types.ModuleType("unreal")


class _Vec:
    def __init__(self, x=0.0, y=0.0, z=0.0):
        self.x, self.y, self.z = float(x), float(y), float(z)


class _Quat:
    def __init__(self, x=0.0, y=0.0, z=0.0, w=1.0):
        self.x, self.y, self.z, self.w = float(x), float(y), float(z), float(w)

    def rotator(self):
        return types.SimpleNamespace(pitch=0.0, yaw=0.0, roll=0.0)


class _Transform:
    def __init__(self, t=None, q=None, s=None):
        self.translation = t or _Vec()
        self.rotation = q or _Quat()
        self.scale3d = s or _Vec(1.0, 1.0, 1.0)


class _Color:
    def __init__(self, r=1.0, g=1.0, b=1.0, a=1.0):
        self.r, self.g, self.b, self.a = float(r), float(g), float(b), float(a)


class _Asset:
    def __init__(self, name, path_name, **props):
        self._name = name
        self._path_name = path_name
        self._props = dict(props)

    def get_name(self):
        return self._name

    def get_path_name(self):
        return self._path_name

    def get_editor_property(self, name):
        if name not in self._props:
            raise AttributeError(name)
        return self._props[name]


class _Tex(_Asset):
    pass


class _SoundWave(_Asset):
    pass


class _Mat(_Asset):
    def __init__(self, name, path_name, expressions=None, texparams=None,
                 scalarparams=None, vectorparams=None, **props):
        super().__init__(name, path_name, **props)
        if expressions is not None:
            self._props["expressions"] = list(expressions)
        self._texparams = dict(texparams or {})
        self._scalarparams = dict(scalarparams or {})
        self._vectorparams = dict(vectorparams or {})


class _MatInst(_Mat):
    pass


class _MEL:
    @staticmethod
    def get_texture_parameter_names(mat):
        return list(getattr(mat, "_texparams", {}).keys())

    @staticmethod
    def get_material_default_texture_parameter_value(mat, name):
        return getattr(mat, "_texparams", {}).get(str(name))

    @staticmethod
    def get_scalar_parameter_names(mat):
        return list(getattr(mat, "_scalarparams", {}).keys())

    @staticmethod
    def get_material_default_scalar_parameter_value(mat, name):
        return getattr(mat, "_scalarparams", {}).get(str(name))

    @staticmethod
    def get_vector_parameter_names(mat):
        return list(getattr(mat, "_vectorparams", {}).keys())

    @staticmethod
    def get_material_default_vector_parameter_value(mat, name):
        return getattr(mat, "_vectorparams", {}).get(str(name))


class _PropObj:
    def __init__(self, **props):
        self._props = dict(props)

    def get_editor_property(self, name):
        if name not in self._props:
            raise AttributeError(name)
        return self._props[name]


class _Actor(_PropObj):
    def __init__(self, label, comps=None, hidden_ed=False, **props):
        super().__init__(**props)
        self._label = label
        self._comps = list(comps or [])
        self._hidden_ed = hidden_ed
        self._xform = _Transform()

    def get_actor_label(self):
        return self._label

    def get_name(self):
        return self._label

    def get_actor_transform(self):
        return self._xform

    def get_components_by_class(self, cls):
        return [c for c in self._comps if isinstance(c, cls)]

    def get_component_by_class(self, cls):
        for c in self._comps:
            if isinstance(c, cls):
                return c
        return None

    def is_hidden_ed(self):
        return self._hidden_ed


class _Comp(_PropObj):
    def __init__(self, name="Comp", xform=None, **props):
        super().__init__(**props)
        self._name = name
        self._xform = xform or _Transform()

    def get_name(self):
        return self._name

    def get_world_transform(self):
        return self._xform


class _Task:
    def __init__(self):
        self.object = None
        self.filename = ""
        self.automated = False
        self.prompt = False
        self.replace_identical = False


u.Vector = _Vec
u.Quat = _Quat
u.Transform = _Transform
u.Rotator = type("Rotator", (), {})
u.LinearColor = _Color
u.Color = _Color
u.Texture = _Tex
u.Texture2D = _Tex
u.SoundWave = _SoundWave
u.MaterialInstance = _MatInst
u.MaterialInstanceConstant = _MatInst
u.MaterialEditingLibrary = _MEL
u.StaticMesh = type("StaticMesh", (_Asset,), {})
u.SkeletalMesh = type("SkeletalMesh", (_Asset,), {})
u.AudioComponent = type("AudioComponent", (_Comp,), {})
u.SkyAtmosphere = type("SkyAtmosphere", (_Actor,), {})
u.SkyAtmosphereComponent = type("SkyAtmosphereComponent", (_Comp,), {})
u.PostProcessVolume = type("PostProcessVolume", (_Actor,), {})
u.ExponentialHeightFog = type("ExponentialHeightFog", (_Actor,), {})
u.SkyLight = type("SkyLight", (_Actor,), {})
u.DecalComponent = type("DecalComponent", (_Comp,), {})
u.LightComponent = type("LightComponent", (_Comp,), {})
u.PointLightComponent = type("PointLightComponent", (u.LightComponent,), {})
u.SpotLightComponent = type("SpotLightComponent", (u.LightComponent,), {})
u.DirectionalLightComponent = type("DirectionalLightComponent", (u.LightComponent,), {})
u.BoxReflectionCaptureComponent = type("BoxReflectionCaptureComponent", (_Comp,), {})
u.SphereReflectionCaptureComponent = type("SphereReflectionCaptureComponent", (_Comp,), {})
u.CameraComponent = type("CameraComponent", (_Comp,), {})
u.AssetExportTask = _Task
u.MaterialExpression = type("MaterialExpression", (), {})
u.MaterialExpressionTextureSample = type("MaterialExpressionTextureSample", (u.MaterialExpression,), {})
u.MaterialExpressionTextureSampleParameter2D = type("MaterialExpressionTextureSampleParameter2D", (u.MaterialExpressionTextureSample,), {})
u.MaterialExpressionScalarParameter = type("MaterialExpressionScalarParameter", (u.MaterialExpression,), {})
u.MaterialExpressionVectorParameter = type("MaterialExpressionVectorParameter", (u.MaterialExpression,), {})
u.MaterialExpressionConstant = type("MaterialExpressionConstant", (u.MaterialExpression,), {})
u.MaterialExpressionConstant3Vector = type("MaterialExpressionConstant3Vector", (u.MaterialExpression,), {})
u.MaterialExpressionMultiply = type("MaterialExpressionMultiply", (u.MaterialExpression,), {})
u.MaterialExpressionPower = type("MaterialExpressionPower", (u.MaterialExpression,), {})
u.MaterialExpressionClamp = type("MaterialExpressionClamp", (u.MaterialExpression,), {})
u.MaterialExpressionStaticSwitch = type("MaterialExpressionStaticSwitch", (u.MaterialExpression,), {})
u.MaterialExpressionStaticBoolParameter = type("MaterialExpressionStaticBoolParameter", (u.MaterialExpression,), {})
u.Name = type("Name", (), {})
u.Text = type("Text", (), {})
u.log = u.log_warning = u.log_error = lambda *a: None
sys.modules["unreal"] = u

import ue2g_common as UC
import export_environment as EE
import export_gameplay as EG
import export_landscape as ELS
import export_level_to_json as EL
import export_static_meshes_to_gltf as EX
import material_shader as MS
import ue2g_diagnose as DIAG

FAIL = []


def check(name, cond, detail=""):
    print(("  PASS  " if cond else "  FAIL  ") + name + ("  " + str(detail) if not cond else ""))
    if not cond:
        FAIL.append(name)


def _make_node(cls_type, **props):
    inst = object.__new__(cls_type)
    inst._props = dict(props)
    inst._name = props.get("_name", cls_type.__name__)
    inst.get_editor_property = lambda name: inst._props[name] if name in inst._props else (_ for _ in ()).throw(AttributeError(name))
    inst.get_name = lambda: inst._name
    return inst


# =============================================================================
print("\n=== 1. ue2g_common: plain TextureSample on UE 5.0-5.6 & case-insensitive name map ===")
# #8: Plain MaterialExpressionTextureSample yields "" (not expr.get_name())
t_plain = _Tex("T_Wall_D", "/Game/T_Wall_D.T_Wall_D")
expr_plain = _make_node(u.MaterialExpressionTextureSample, _name="MaterialExpressionTextureSample_0", texture=t_plain)
mat_plain = _Mat("M_Wall", "/Game/M_Wall.M_Wall", expressions=[expr_plain])
pairs = list(UC.iter_base_material_textures(mat_plain, include_dependencies=False))
check("plain TextureSample yields empty param name '' so trace matches",
      pairs == [("", t_plain)], pairs)

# #9: Case-insensitive collision grouping & duplicate-ref deduplication
t_upper = _Tex("T_Concrete_D", "/Game/PackA/T_Concrete_D.T_Concrete_D")
t_lower = _Tex("T_concrete_d", "/Game/PackB/T_concrete_d.T_concrete_d")
t_unique = _Tex("T_Unique", "/Game/PackA/T_Unique.T_Unique")
nmap = UC.build_export_name_map([t_upper, t_lower, t_unique, t_unique], kind="texture")
check("case-only colliding assets both get hash suffixes",
      nmap[t_upper].startswith("T_Concrete_D_") and nmap[t_lower].startswith("T_concrete_d_"),
      (nmap.get(t_upper), nmap.get(t_lower)))
check("duplicate reference to same asset does NOT get spurious hash suffix",
      nmap[t_unique] == "T_Unique", nmap.get(t_unique))

# #11: export_textures_to_png selective skip_existing set
tmp_tex = tempfile.mkdtemp(prefix="ue2g_skiptex_")
try:
    t_mesh = _Tex("T_MeshOnly", "/Game/T_MeshOnly.T_MeshOnly")
    t_decal = _Tex("T_DecalOnly", "/Game/T_DecalOnly.T_DecalOnly")
    for fn in ("T_MeshOnly.png", "T_DecalOnly.png"):
        with open(os.path.join(tmp_tex, fn), "wb") as f:
            f.write(b"old")
    exported_tasks = []

    class _FakeExp:
        @staticmethod
        def run_asset_export_tasks(tasks):
            for t in tasks:
                exported_tasks.append(os.path.basename(t.filename))
                with open(t.filename, "wb") as f:
                    f.write(b"new")

    u.Exporter = _FakeExp
    res = UC.export_textures_to_png(
        [t_mesh, t_decal], tmp_tex, skip_existing={"T_MeshOnly"}
    )
    check("selective skip_existing set reuses mesh texture",
          res["reused"] == ["T_MeshOnly"], res)
    check("selective skip_existing set re-exports decal texture",
          res["exported"] == ["T_DecalOnly"] and exported_tasks == ["T_DecalOnly.png"],
          (res, exported_tasks))
finally:
    shutil.rmtree(tmp_tex, ignore_errors=True)


# =============================================================================
print("\n=== 2. export_level_to_json: emissive color role, graph tints, AO-only packed ===")
# #19: EmissiveColor classifies as emissive_texture, not albedo_texture
check("EmissiveColor classifies as emissive_texture",
      EL.classify_texture_role("EmissiveColor") == "emissive_texture",
      EL.classify_texture_role("EmissiveColor"))
check("T_Sign_EmissiveColorMap classifies as emissive_texture",
      EL.classify_texture_role("T_Sign_EmissiveColorMap") == "emissive_texture",
      EL.classify_texture_role("T_Sign_EmissiveColorMap"))

# #20 & #21: Graph-traced tint named 'Paint' + AO-only doesn't block RM packed map
t_ao = _Tex("T_Crate_AO", "/Game/T_Crate_AO.T_Crate_AO")
t_rm = _Tex("T_Crate_RM", "/Game/T_Crate_RM.T_Crate_RM")
mat_traced = _Mat(
    "M_Crate", "/Game/M_Crate.M_Crate",
    texparams={"AOMap": t_ao, "RMMap": t_rm},
    vectorparams={"Paint": _Color(0.2, 0.4, 0.8, 1.0)},
)
_orig_trace = UC.trace_material_graph
try:
    UC.trace_material_graph = lambda _m: {
        "samples": {
            "ao": {"param": "AOMap", "texture": t_ao, "output": "R"},
            "roughness": {"param": "RMMap", "texture": t_rm, "output": "R"},
            "metallic": {"param": "RMMap", "texture": t_rm, "output": "G"},
        },
        "scalars": {},
        "constants": {},
        "emissive": None,
        "tints": {"Paint"},
        "not_tints": set(),
        "hidden": False,
        "alpha": None,
    }
    p = EL.extract_material_parameters(mat_traced)
finally:
    UC.trace_material_graph = _orig_trace
check("graph-traced vector param 'Paint' promotes to albedo_color",
      p["albedo_color"] == [0.2, 0.4, 0.8, 1.0], p["albedo_color"])
check("AO-only map does not lock out packed RM texture",
      p["packed_texture"] == "T_Crate_RM" and p["packed_channels"] == {"roughness": 0, "metallic": 1},
      (p["packed_texture"], p["packed_channels"]))


# =============================================================================
print("\n=== 3. export_static_meshes_to_gltf: shader textures, None body_setup, cache clear ===")
# #12: collect_textures_from_material harvests material_shader textures
t_blend = _Tex("T_Moss_Blend", "/Game/T_Moss_Blend.T_Moss_Blend")
mat_vp = _Mat("M_Blend", "/Game/M_Blend.M_Blend")
_orig_build = MS.build_shader
try:
    MS.build_shader = lambda *a, **kw: ({"code": "shader_type spatial;"}, {t_blend})
    col = set()
    EX.collect_textures_from_material(mat_vp, col)
finally:
    MS.build_shader = _orig_build
check("collect_textures_from_material harvests vertex-paint shader textures",
      t_blend in col, col)

# #25a: extract_skeletal_mesh_physics skips None/broken body_setup without aborting
valid_box = _PropObj(
    center=_Vec(0, 0, 0),
    rotation=types.SimpleNamespace(quaternion=lambda: _Quat()),
    x=10.0, y=10.0, z=10.0,
)
valid_body = _PropObj(
    bone_name="spine_01",
    agg_geom=_PropObj(box_elems=[valid_box], sphere_elems=[], sphyl_elems=[], convex_elems=[]),
)
phys_asset = _Asset("PA_Hero", "/Game/PA_Hero.PA_Hero", skeletal_body_setups=[None, valid_body])
skel_mesh = u.SkeletalMesh("SK_Hero", "/Game/SK_Hero.SK_Hero", physics_asset=phys_asset)
phys_out = EX.extract_skeletal_mesh_physics(skel_mesh)
check("extract_skeletal_mesh_physics survives None body_setup and keeps valid bone",
      phys_out is not None and len(phys_out["bodies"]) == 1 and phys_out["bodies"][0]["bone_name"] == "spine_01",
      phys_out)


# =============================================================================
print("\n=== 4. material_shader: Multiply(x,x), Power/Clamp widening, StaticSwitch Value ===")
def _make_tr(edges, mat=None):
    base = mat or _Mat("M_S", "/Game/M_S.M_S")
    tr = MS._Translator(base, base)
    tr.graph._inputs = {k: list(v) for k, v in edges.items()}
    return tr


# #22a: Multiply(x, x) with single-output ScalarParameter (out=None on both edges)
n_x = _make_node(u.MaterialExpressionScalarParameter, _name="MaskX", parameter_name="Mask", default_value=0.5)
n_mul = _make_node(u.MaterialExpressionMultiply, _name="MulXX")
tr_mul = _make_tr({"MulXX": [("a", n_x, None), ("b", n_x, None)]})
em, wm = tr_mul.value(n_mul, "")
check("Multiply(x, x) with single-output node and out=None succeeds",
      wm == 1 and any("*" in ln for ln in tr_mul.lines), (em, wm, tr_mul.lines))

# #22b: Power(float, vec3) widens base to vec3 instead of truncating exp
n_base = _make_node(u.MaterialExpressionConstant, _name="CBase", r=0.5)
n_exp = _make_node(u.MaterialExpressionConstant3Vector, _name="CExp", constant=_Color(1.0, 2.0, 3.0, 1.0))
n_pow = _make_node(u.MaterialExpressionPower, _name="PowSV")
tr_pow = _make_tr({"PowSV": [("base", n_base, ""), ("exp", n_exp, "")]})
ep, wp = tr_pow.value(n_pow, "")
check("Power(float, vec3) widens to width 3",
      wp == 3 and any("pow(max(vec3(" in ln for ln in tr_pow.lines), (ep, wp, tr_pow.lines))

# #22b: Clamp(float, vec3, vec3) widens to width 3
n_lo = _make_node(u.MaterialExpressionConstant3Vector, _name="CLo", constant=_Color(0.0, 0.1, 0.2, 1.0))
n_hi = _make_node(u.MaterialExpressionConstant3Vector, _name="CHi", constant=_Color(1.0, 0.9, 0.8, 1.0))
n_cl = _make_node(u.MaterialExpressionClamp, _name="ClampSV")
tr_cl = _make_tr({"ClampSV": [("", n_base, ""), ("min", n_lo, ""), ("max", n_hi, "")]})
ec, wc = tr_cl.value(n_cl, "")
check("Clamp(float, vec3, vec3) widens to width 3",
      wc == 3 and any("clamp(vec3(" in ln for ln in tr_cl.lines), (ec, wc, tr_cl.lines))

# #22c: StaticSwitch reads wired StaticBoolParameter on 'Value' input
n_bool = _make_node(u.MaterialExpressionStaticBoolParameter, _name="UseAlt", parameter_name="UseAlt", default_value=False)
n_true = _make_node(u.MaterialExpressionConstant, _name="CTrue", r=1.0)
n_false = _make_node(u.MaterialExpressionConstant, _name="CFalse", r=0.25)
n_sw = _make_node(u.MaterialExpressionStaticSwitch, _name="Sw0", default_value=True)
tr_sw = _make_tr({"Sw0": [("value", n_bool, ""), ("True", n_true, ""), ("False", n_false, "")]})
es, ws = tr_sw.value(n_sw, "")
check("StaticSwitch follows wired StaticBoolParameter=False instead of default_value=True",
      ws == 1 and any("0.25" in ln for ln in tr_sw.lines) and not any("1.0" in ln for ln in tr_sw.lines),
      (es, ws, tr_sw.lines))


# =============================================================================
print("\n=== 5. export_environment: hidden SkyAtmosphere & colliding SoundWaves ===")
# #24a: Hidden SkyAtmosphere does NOT set has_sky_atmosphere=True
sky_comp_hidden = u.SkyAtmosphereComponent(visible=False)
sky_actor_vis_comp_off = u.SkyAtmosphere("SkyAtm1", comps=[sky_comp_hidden])
sky_actor_hidden = u.SkyAtmosphere("SkyAtm2", hidden_ed=True)
env_hidden = EE.collect_environment([sky_actor_vis_comp_off, sky_actor_hidden], set())
check("hidden/disabled SkyAtmosphere does NOT set has_sky_atmosphere",
      env_hidden["has_sky_atmosphere"] is False, env_hidden["has_sky_atmosphere"])

# #24b: Colliding SoundWave names disambiguated in collect_environment & export_sound_waves
sw1 = _SoundWave("S_Hum", "/Game/AreaA/S_Hum.S_Hum")
sw2 = _SoundWave("S_Hum", "/Game/AreaB/S_Hum.S_Hum")
a1 = _Actor("Amb1", comps=[u.AudioComponent(sound=sw1)])
a2 = _Actor("Amb2", comps=[u.AudioComponent(sound=sw2)])
col_sounds = set()
env_aud = EE.collect_environment([a1, a2], set(), include_lights=False, include_decals=False,
                                 include_reflection_probes=False, include_cameras=False,
                                 include_audio=True, collected_sounds=col_sounds)
sfiles = [e["sound_file"] for e in env_aud["audio"]]
check("colliding SoundWaves get distinct disambiguated .wav filenames",
      len(sfiles) == 2 and sfiles[0] != sfiles[1] and all(f.startswith("S_Hum_") for f in sfiles),
      sfiles)


# =============================================================================
print("\n=== 6. export_landscape: MI base-material dependency fallback ===")
# #23: MI -> BaseMaterial crawls base material dependencies, not MI dependencies
t_ground = _Tex("T_Ground_D", "/Game/T_Ground_D.T_Ground_D")
base_ls = _Mat("M_Land", "/Game/M_Land.M_Land")
mi_ls = _MatInst("MI_Land", "/Game/MI_Land.MI_Land", parent=base_ls)
_orig_dep = UC.iter_material_dependency_textures
try:
    UC.iter_material_dependency_textures = lambda m: [t_ground] if m is base_ls else []
    ls_actor = _Actor("Landscape_0", landscape_material=mi_ls)
    mat_info = ELS._collect_material_textures(ls_actor, set())
finally:
    UC.iter_material_dependency_textures = _orig_dep
check("landscape MaterialInstance falls back to base material's dependency textures",
      mat_info is not None and any(t.get("texture") == "T_Ground_D" for t in mat_info.get("textures", [])),
      mat_info)


# =============================================================================
print("\n=== 7. export_gameplay: inherited BP props, >64 dir() attrs, non-finite floats ===")
# #13: Multi-level BP inheritance + >64 methods before simple var + NaN/Inf rejection
class _NativeBase:
    pass


class _ParentBP_C(_NativeBase):
    parent_hp = 100


class _ChildBP_C(_ParentBP_C):
    pass


# Add 70 dummy method names alphabetically before z_score to test slicing bug
for i in range(70):
    setattr(_ChildBP_C, f"a_method_{i:02d}", lambda self: None)
_ChildBP_C.z_score = 42
_ChildBP_C.z_bad_nan = float("nan")
_ChildBP_C.z_bad_inf = float("inf")


bp_inst = _ChildBP_C()
_bp_vals = {"parent_hp": 100, "z_score": 42, "z_bad_nan": float("nan"), "z_bad_inf": float("inf")}
bp_inst.get_editor_property = lambda name: _bp_vals[name] if name in _bp_vals else (_ for _ in ()).throw(AttributeError(name))
bp_props = EG._extract_bp_properties(bp_inst)
check("multi-level BP keeps inherited parent Blueprint variable",
      bp_props.get("parent_hp") == 100, bp_props)
check("BP with >64 methods still extracts variables past index 64",
      bp_props.get("z_score") == 42, bp_props)
check("non-finite NaN/Inf floats are rejected before JSON serialization",
      "z_bad_nan" not in bp_props and "z_bad_inf" not in bp_props, bp_props)


# =============================================================================
print("\n=== 8. ue2g_diagnose: .glb, case-insensitive, separate_textures=False, sidecars, null ===")
tmp_diag = tempfile.mkdtemp(prefix="ue2g_diag2_")
try:
    # 8a. Non-dict / null sections never raise
    bad_json = os.path.join(tmp_diag, "bad.json")
    with open(bad_json, "w", encoding="utf-8") as f:
        f.write("[1, 2, 3]")
    _txt, rep_bad = DIAG.diagnose(tmp_diag, layout_path=bad_json, strict=False)
    check("diagnose() handles non-dict JSON without raising",
          any("JSON object" in e for e in rep_bad.errors), rep_bad.errors)

    # 8b. Explicit layout_path + custom models_dir + .glb + case-insensitive + separate_textures=False
    custom_models = os.path.join(tmp_diag, "CustomModels")
    os.makedirs(custom_models, exist_ok=True)
    with open(os.path.join(custom_models, "sm_box.glb"), "wb") as f:
        f.write(b"glTF")
    with open(os.path.join(custom_models, "T_Albedo.png"), "wb") as f:
        f.write(b"png")
    with open(os.path.join(custom_models, "SM_Textured.gltf"), "w", encoding="utf-8") as f:
        json.dump({"asset": {"version": "2.0"}, "images": [{"uri": "T_Albedo.png"}]}, f)

    custom_layout = os.path.join(tmp_diag, "custom_scene.json")
    layout_obj = {
        "format_version": 2,
        "actors": [{
            "name": "BoxA",
            "components": [{
                "mesh_key": "SM_Box",
                "godot_world_transform": {"translation": [0, 0, 0]},
                "material_overrides": [{"parameters": {"roughness": 2.5, "albedo_texture": "T_Albedo"}}],
            }],
        }],
        "meshes": {"SM_Box": {"physics_file": "SM_Box_physics.json"}},
        "foliage": None,
        "audio": [{"sound_file": "S_Missing.wav"}],
    }
    with open(custom_layout, "w", encoding="utf-8") as f:
        json.dump(layout_obj, f)

    _txt2, rep2 = DIAG.diagnose(tmp_diag, strict=True, layout_path=custom_layout, models_dir=custom_models)
    check("diagnose() matches .glb case-insensitively with no missing-mesh error",
          not rep2.errors, rep2.errors)
    check("diagnose() flags out-of-range scalar on actor material_overrides",
          any("roughness" in w for w in rep2.warnings), rep2.warnings)
    check("diagnose() finds sibling textures when separate_textures=False (no false missing texture)",
          not any("T_Albedo" in w for w in rep2.warnings), rep2.warnings)
    check("diagnose() reports missing v2 sidecars (audio & physics)",
          any("sidecar" in w for w in rep2.warnings), rep2.warnings)
finally:
    shutil.rmtree(tmp_diag, ignore_errors=True)


print("\n" + "=" * 60)
if FAIL:
    print("FAILED (%d): %s" % (len(FAIL), ", ".join(FAIL)))
    sys.exit(1)
print("ALL SECOND-PASS EXPORTER REGRESSION CHECKS PASSED")
