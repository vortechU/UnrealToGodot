"""Regression tests covering the 20 code-sweep bug fixes across the Unreal
Python exporter, tscn_writer, ue2g_diagnose, and Godot GDScript importer.
"""
import json
import os
import shutil
import sys
import tempfile
import types

TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.dirname(TESTS_DIR)
PY_DIR = os.path.join(REPO_ROOT, "UnrealToGodot", "Content", "Python")
ADDON_DIR = os.path.join(REPO_ROOT, "addons", "unreal_importer")
sys.path.insert(0, PY_DIR)

# ---------------------------------------------------------------- unreal stub
u = types.ModuleType("unreal")


class _Vec:
    def __init__(self, x=0.0, y=0.0, z=0.0):
        self.x, self.y, self.z = float(x), float(y), float(z)


class _Vec2:
    def __init__(self, x=0.0, y=0.0):
        self.x, self.y = float(x), float(y)


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


class _FakeAsset:
    def __init__(self, name, path_name):
        self._name = name
        self._path_name = path_name

    def get_name(self):
        return self._name

    def get_path_name(self):
        return self._path_name


class _FakeTex(_FakeAsset):
    pass


class _FakeMat(_FakeAsset):
    def __init__(self, name, path_name, expressions=None, texparams=None,
                 scalarparams=None, vectorparams=None):
        super().__init__(name, path_name)
        self._expressions = list(expressions or [])
        self._texparams = dict(texparams or {})
        self._scalarparams = dict(scalarparams or {})
        self._vectorparams = dict(vectorparams or {})


class _FakeMEL:
    @staticmethod
    def get_material_expressions(mat):
        return list(getattr(mat, "_expressions", []))

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


class _FakeActor(_PropObj):
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

    def get_path_name(self):
        return "/Game/Maps/Test." + self._label

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


class _FakeComp(_PropObj):
    def __init__(self, name="Comp", xform=None, **props):
        super().__init__(**props)
        self._name = name
        self._xform = xform or _Transform()

    def get_name(self):
        return self._name

    def get_component_transform(self):
        return self._xform

    def get_world_transform(self):
        return self._xform


class _DecalComp(_FakeComp):
    def get_decal_material(self):
        return self._props.get("decal_material")


class _BoxReflComp(_FakeComp):
    pass


class _SphereReflComp(_FakeComp):
    pass


class _PointLightComp(_FakeComp):
    pass


class _HeightFogComp(_FakeComp):
    pass


class _SkyLightComp(_FakeComp):
    pass


class _InstancedComp(_FakeComp):
    def __init__(self, name="ISM", instances=None, **props):
        super().__init__(name=name, **props)
        self._instances = list(instances or [])

    def get_instance_count(self):
        return len(self._instances)

    def get_instance_transform(self, idx, world_space=True):
        return self._instances[idx]


class _SplineMeshComp(_FakeComp):
    pass


class _FoliageInstComp(_InstancedComp):
    pass


class _FakeStaticMesh(_FakeAsset):
    def __init__(self, name, path_name, num_lods=1, mats=None):
        super().__init__(name, path_name)
        self._num_lods = num_lods
        self._mats = list(mats or [])

    def get_num_lods(self):
        return self._num_lods

    def get_editor_property(self, name):
        if name == "static_materials":
            return [types.SimpleNamespace(material_interface=m) for m in self._mats]
        raise AttributeError(name)


u.Vector = _Vec
u.Vector2D = _Vec2
u.Quat = _Quat
u.Transform = _Transform
u.Rotator = type("Rotator", (), {})
u.LinearColor = _Color
u.Color = _Color
u.Texture = _FakeTex
u.MaterialInstance = type("MaterialInstance", (), {})
u.MaterialEditingLibrary = _FakeMEL
u.StaticMesh = _FakeStaticMesh
u.SkeletalMesh = type("SkeletalMesh", (_FakeAsset,), {})
u.DecalComponent = _DecalComp
u.BoxReflectionCaptureComponent = _BoxReflComp
u.SphereReflectionCaptureComponent = _SphereReflComp
u.PointLightComponent = _PointLightComp
u.SpotLightComponent = type("SpotLightComponent", (_FakeComp,), {})
u.DirectionalLightComponent = type("DirectionalLightComponent", (_FakeComp,), {})
u.RectLightComponent = type("RectLightComponent", (_FakeComp,), {})
u.ExponentialHeightFogComponent = _HeightFogComp
u.SkyLightComponent = _SkyLightComp
u.PostProcessComponent = type("PostProcessComponent", (_FakeComp,), {})
u.SkyAtmosphereComponent = type("SkyAtmosphereComponent", (_FakeComp,), {})
u.CameraComponent = type("CameraComponent", (_FakeComp,), {})
u.AudioComponent = type("AudioComponent", (_FakeComp,), {})
u.InstancedStaticMeshComponent = _InstancedComp
u.HierarchicalInstancedStaticMeshComponent = type("HISM", (_InstancedComp,), {})
u.FoliageInstancedStaticMeshComponent = _FoliageInstComp
u.StaticMeshComponent = type("StaticMeshComponent", (_FakeComp,), {})
u.SkeletalMeshComponent = type("SkeletalMeshComponent", (_FakeComp,), {})
u.SplineMeshComponent = _SplineMeshComp
u.InstancedFoliageActor = type("InstancedFoliageActor", (_FakeActor,), {})
u.Landscape = type("Landscape", (_FakeActor,), {})
u.MaterialExpression = type("MaterialExpression", (), {})
u.MaterialExpressionTextureSample = type("MaterialExpressionTextureSample", (u.MaterialExpression,), {})
u.MaterialExpressionTextureSampleParameter2D = type("MaterialExpressionTextureSampleParameter2D", (u.MaterialExpression,), {})
u.MaterialExpressionLinearInterpolate = type("MaterialExpressionLinearInterpolate", (u.MaterialExpression,), {})
u.MaterialExpressionDesaturation = type("MaterialExpressionDesaturation", (u.MaterialExpression,), {})
u.MaterialExpressionScalarParameter = type("MaterialExpressionScalarParameter", (u.MaterialExpression,), {})
u.MaterialExpressionVectorParameter = type("MaterialExpressionVectorParameter", (u.MaterialExpression,), {})
u.MaterialExpressionVertexColor = type("MaterialExpressionVertexColor", (u.MaterialExpression,), {})
u.MaterialExpressionConstant = type("MaterialExpressionConstant", (u.MaterialExpression,), {})
u.MaterialExpressionConstant3Vector = type("MaterialExpressionConstant3Vector", (u.MaterialExpression,), {})
u.MaterialExpressionConstant4Vector = type("MaterialExpressionConstant4Vector", (u.MaterialExpression,), {})
u.MaterialExpressionTextureCoordinate = type("MaterialExpressionTextureCoordinate", (u.MaterialExpression,), {})
u.MaterialExpressionMultiply = type("MaterialExpressionMultiply", (u.MaterialExpression,), {})
u.MaterialExpressionAdd = type("MaterialExpressionAdd", (u.MaterialExpression,), {})
u.MaterialExpressionSubtract = type("MaterialExpressionSubtract", (u.MaterialExpression,), {})
u.MaterialExpressionDivide = type("MaterialExpressionDivide", (u.MaterialExpression,), {})
u.MaterialExpressionOneMinus = type("MaterialExpressionOneMinus", (u.MaterialExpression,), {})
u.MaterialExpressionClamp = type("MaterialExpressionClamp", (u.MaterialExpression,), {})
u.MaterialExpressionSaturate = type("MaterialExpressionSaturate", (u.MaterialExpression,), {})
u.MaterialExpressionPower = type("MaterialExpressionPower", (u.MaterialExpression,), {})
u.MaterialExpressionDotProduct = type("MaterialExpressionDotProduct", (u.MaterialExpression,), {})
u.MaterialExpressionAppendVector = type("MaterialExpressionAppendVector", (u.MaterialExpression,), {})
u.MaterialExpressionComponentMask = type("MaterialExpressionComponentMask", (u.MaterialExpression,), {})
u.MaterialExpressionStaticSwitchParameter = type("MaterialExpressionStaticSwitchParameter", (u.MaterialExpression,), {})
u.MaterialExpressionStaticBoolParameter = type("MaterialExpressionStaticBoolParameter", (u.MaterialExpression,), {})
u.MaterialExpressionMaterialFunctionCall = type("MaterialExpressionMaterialFunctionCall", (u.MaterialExpression,), {})
u.MaterialExpressionReroute = type("MaterialExpressionReroute", (u.MaterialExpression,), {})
u.MaterialExpressionNamedRerouteUsage = type("MaterialExpressionNamedRerouteUsage", (u.MaterialExpression,), {})
u.MaterialExpressionNamedRerouteDeclaration = type("MaterialExpressionNamedRerouteDeclaration", (u.MaterialExpression,), {})
u.MaterialExpressionFeatureLevelSwitch = type("MaterialExpressionFeatureLevelSwitch", (u.MaterialExpression,), {})
u.MaterialExpressionQualitySwitch = type("MaterialExpressionQualitySwitch", (u.MaterialExpression,), {})
u.log = u.log_warning = u.log_error = lambda *a: None
sys.modules["unreal"] = u

import export_environment as EE
import export_foliage as EF
import export_level_to_json as EL
import export_static_meshes_to_gltf as EX
import material_shader as MS
import tscn_writer as TW
import ue2g_diagnose as DIAG

FAIL = []


def check(name, cond, detail=""):
    print(("  PASS  " if cond else "  FAIL  ") + name + ("  " + str(detail) if not cond else ""))
    if not cond:
        FAIL.append(name)


# =============================================================================
print("\n=== 1. export_environment: decal emission, actor hidden, probe scale, sky fit ===")
# 1a. Decal emission export (#6a)
t_alb = _FakeTex("T_Sign_D", "/Game/Decals/T_Sign_D.T_Sign_D")
t_em = _FakeTex("T_Sign_E", "/Game/Decals/T_Sign_E.T_Sign_E")
decal_mat = _FakeMat("M_Sign", "/Game/Decals/M_Sign.M_Sign",
                     texparams={"BaseColor": t_alb, "Emissive": t_em})
decal_comp = _DecalComp(decal_material=decal_mat, decal_size=_Vec(50.0, 100.0, 100.0), visible=True)
decal_actor = _FakeActor("Decal_Sign", comps=[decal_comp], hidden_ed=True)
collected_decal_tex = set()
decal_entry = EE._build_decal_entry(decal_actor, decal_comp, "Decal_Sign", collected_decal_tex)
check("decal exports emission texture name",
      decal_entry["textures"]["emission"] == "T_Sign_E", decal_entry["textures"])
check("decal exports emission texture_paths entry",
      decal_entry["textures"]["texture_paths"]["emission"] == "/Game/Decals/T_Sign_E.T_Sign_E",
      decal_entry["textures"]["texture_paths"])
check("decal collects emission texture for export",
      t_em in collected_decal_tex, collected_decal_tex)
check("decal respects actor is_hidden_ed()",
      decal_entry["visible"] is False, decal_entry["visible"])

# 1b. Actor hidden on lights, fog, sky light, reflection probes (#10)
pt_comp = _PointLightComp(visible=True, intensity=100.0, attenuation_radius=500.0)
pt_actor = _FakeActor("HiddenLight", comps=[pt_comp], hidden=True)
pt_entry = EE._build_light_entry(pt_actor, pt_comp, "HiddenLight")
check("light respects actor hidden property", pt_entry["visible"] is False, pt_entry["visible"])

fog_comp = _HeightFogComp(visible=True, fog_density=0.02)
fog_actor = _FakeActor("HiddenFog", comps=[fog_comp], hidden_ed=True)
fog_entry = EE._build_height_fog(fog_actor)
check("height fog hidden via actor is_hidden_ed() returns None", fog_entry is None, fog_entry)

sky_comp = _SkyLightComp(visible=True, intensity=1.0)
sky_actor = _FakeActor("HiddenSky", comps=[sky_comp], hidden=True)
sky_entry = EE._build_sky_light(sky_actor)
check("sky light hidden via actor hidden property returns None", sky_entry is None, sky_entry)

# 1c. Reflection probe scale folding into size_m (#8)
# UE scale (2, 3, 4) -> Godot scale (3, 4, 2); UE box_extent (100, 200, 300) cm -> base size_m [4.0, 6.0, 2.0]
box_comp = _BoxReflComp(
    xform=_Transform(s=_Vec(2.0, 3.0, 4.0)),
    box_extent=_Vec(100.0, 200.0, 300.0),
    box_transition_distance=150.0,
    visible=True,
)
box_actor = _FakeActor("BoxProbe", comps=[box_comp], hidden_ed=True)
box_entries = EE._build_reflection_probe_entries(box_actor)
check("box probe folds scale into size_m",
      box_entries[0]["size_m"] == [12.0, 24.0, 4.0], box_entries[0]["size_m"])
check("box probe resets godot_transform scale to [1, 1, 1]",
      box_entries[0]["godot_transform"]["scale"] == [1.0, 1.0, 1.0],
      box_entries[0]["godot_transform"]["scale"])
check("box probe respects actor is_hidden_ed()",
      box_entries[0]["visible"] is False, box_entries[0]["visible"])

sph_comp = _SphereReflComp(
    xform=_Transform(s=_Vec(2.0, 2.0, 2.0)),
    influence_radius=500.0,
    visible=True,
)
sph_actor = _FakeActor("SphereProbe", comps=[sph_comp])
sph_entries = EE._build_reflection_probe_entries(sph_actor)
check("sphere probe folds scale into size_m",
      sph_entries[0]["size_m"] == [20.0, 20.0, 20.0], sph_entries[0]["size_m"])
check("sphere probe resets godot_transform scale to [1, 1, 1]",
      sph_entries[0]["godot_transform"]["scale"] == [1.0, 1.0, 1.0],
      sph_entries[0]["godot_transform"]["scale"])

# 1d. _fit_sky_gradient degenerate inputs (#19)
# Each sample is (sine_of_elevation, weight, (r, g, b))
try:
    hor, top, exp = EE._fit_sky_gradient([(0.5, 1.0, (0.2, 0.4, 0.8))])
    check("_fit_sky_gradient handles single identical elevation without ZeroDivisionError",
          len(top) == 3 and len(hor) == 3 and exp > 0.0, (hor, top, exp))
    hor2, top2, exp2 = EE._fit_sky_gradient([(0.5, 0.0, (0.2, 0.4, 0.8))])
    check("_fit_sky_gradient handles zero weights without ZeroDivisionError",
          len(top2) == 3 and len(hor2) == 3, (hor2, top2, exp2))
except ZeroDivisionError as exc:
    check("_fit_sky_gradient degenerate inputs", False, repr(exc))


# =============================================================================
print("\n=== 2. material_shader: Lerp width, Desaturation input, uniform collisions ===")
def _make_node(cls_type, **props):
    inst = object.__new__(cls_type)
    inst._props = dict(props)
    inst._name = props.get("_name", cls_type.__name__)
    inst.get_editor_property = lambda name: inst._props[name] if name in inst._props else (_ for _ in ()).throw(AttributeError(name))
    inst.get_name = lambda: inst._name
    return inst


def _make_translator(edges):
    """Creates a _Translator with an injected MaterialGraph edge map: {node_name: [(input_name_lower, src_node, out_pin)]}."""
    base_mat = _FakeMat("M_Base", "/Game/M_Base.M_Base")
    tr = MS._Translator(base_mat, base_mat)
    # MaterialGraph.inputs(node) caches under _inputs[safe_get_name(node)] -> [(name, child, out)]
    tr.graph._inputs = {k: list(v) for k, v in edges.items()}
    return tr


# 2a. LinearInterpolate with vec3 A/B and vec4 Alpha (#7a)
n_a = _make_node(u.MaterialExpressionConstant3Vector, _name="ConstA", constant=_Color(1.0, 0.0, 0.0, 1.0))
n_b = _make_node(u.MaterialExpressionConstant3Vector, _name="ConstB", constant=_Color(0.0, 1.0, 0.0, 1.0))
n_t = _make_node(u.MaterialExpressionConstant4Vector, _name="ConstT", constant=_Color(0.5, 0.5, 0.5, 0.5))
n_lerp = _make_node(u.MaterialExpressionLinearInterpolate, _name="Lerp0", const_alpha=0.5)
tr = _make_translator({
    "Lerp0": [("a", n_a, ""), ("b", n_b, ""), ("alpha", n_t, "")],
})
expr_lerp, w_lerp = tr.value(n_lerp, "")
check("LinearInterpolate(vec3, vec3, vec4) emits width 3 without failing",
      w_lerp == 3 and any("mix(" in ln and (".rgb" in ln or ".xyz" in ln) for ln in tr.lines),
      (expr_lerp, w_lerp, tr.lines))

# 2b. Desaturation with custom color input key + vec4 fraction (#7b)
n_col = _make_node(u.MaterialExpressionConstant3Vector, _name="Col0", constant=_Color(0.8, 0.2, 0.1, 1.0))
n_frac = _make_node(u.MaterialExpressionConstant4Vector, _name="Frac0", constant=_Color(0.5, 0.0, 0.0, 0.0))
n_desat = _make_node(u.MaterialExpressionDesaturation, _name="Desat0", luminance_factors=_Color(0.3, 0.59, 0.11, 0.0))
tr2 = _make_translator({
    "Desat0": [("", n_col, ""), ("fraction", n_frac, "")],
})
expr_desat, w_desat = tr2.value(n_desat, "")
check("Desaturation reads non-fraction color input and broadcasts vec4 fraction to scalar",
      w_desat == 3 and any("mix(" in ln and ".x" in ln for ln in tr2.lines), (expr_desat, w_desat, tr2.lines))

# 2c. Colliding scalar & vector uniform names (#7c)
tr3 = _make_translator({})
p_scalar = _make_node(u.MaterialExpressionScalarParameter, _name="S0", parameter_name="Tint")
p_vec3 = _make_node(u.MaterialExpressionVectorParameter, _name="V0", parameter_name="Tint")
u_scalar = tr3._uniform(p_scalar, "float", "1.0")
u_vec3 = tr3._uniform(p_vec3, "vec3", "vec3(1.0)")
check("colliding scalar/vector uniform names are disambiguated",
      u_scalar != u_vec3 and u_scalar == "p_tint" and u_vec3 == "p_tint_vec3",
      (u_scalar, u_vec3))
check("same uniform name with same type reuses declaration",
      tr3._uniform(p_scalar, "float", "1.0") == "p_tint")


# =============================================================================
print("\n=== 3. export_level_to_json: opacity, ISM flags, spline offset, skel mesh, cancel ===")
# 3a. Opacity-only traced texture does not fall through to name classifier (#16)
import ue2g_common as UC
t_op = _FakeTex("T_Glass_BaseColor_Opacity", "/Game/T_Glass_BaseColor_Opacity.T_Glass_BaseColor_Opacity")
op_mat = _FakeMat("M_Glass", "/Game/M_Glass.M_Glass", texparams={"BaseColorOpacity": t_op})
op_mat.get_editor_property = lambda name: "BLEND_Translucent" if name == "blend_mode" else (_ for _ in ()).throw(AttributeError(name))
_orig_trace = UC.trace_material_graph
try:
    UC.trace_material_graph = lambda _m: {
        "samples": {"opacity": {"param": "BaseColorOpacity", "texture": t_op, "output": "R"}},
        "scalars": {},
        "constants": {},
        "emissive": None,
        "tints": None,
        "not_tints": set(),
        "hidden": False,
        "alpha": None,
    }
    params = EL.extract_material_parameters(op_mat)
finally:
    UC.trace_material_graph = _orig_trace
check("opacity-only traced texture sets opacity_texture",
      params.get("opacity_texture") == "T_Glass_BaseColor_Opacity", params)
check("opacity-only traced texture does NOT fall through into albedo_texture",
      params.get("albedo_texture") is None, params)

# 3b. _expand_instanced_component carries render flags (#9)
ism_mesh = _FakeStaticMesh("SM_Pillar", "/Game/SM_Pillar.SM_Pillar")
ism_comp = _InstancedComp(
    name="ISM_Pillars",
    instances=[_Transform(t=_Vec(100.0, 0.0, 0.0)), _Transform(t=_Vec(200.0, 0.0, 0.0))],
    visible=False,
    cast_shadow=False,
    cached_max_draw_distance=2500.0,
)
ism_actor = _FakeActor("PillarActor", comps=[ism_comp])
flags = EL.extract_component_render_flags(ism_comp, ism_actor)
expanded = EL._expand_instanced_component(
    EF, ism_comp, "ISM_Pillars", "SM_Pillar", "SM_Pillar", "/Game/SM_Pillar.SM_Pillar",
    [], "PillarActor", render_flags=flags)
check("ISM expanded placements carry visible=False",
      len(expanded) == 2 and all(e.get("visible") is False for e in expanded), expanded)
check("ISM expanded placements carry cast_shadow=False",
      all(e.get("cast_shadow") is False for e in expanded), expanded)
check("ISM expanded placements carry max_draw_distance_m=25.0",
      all(abs(e.get("max_draw_distance_m", 0.0) - 25.0) < 1e-4 for e in expanded), expanded)

# 3c. extract_spline_mesh_params exports start_offset and end_offset (#1)
sp_params_obj = _PropObj(
    start_pos=_Vec(0.0, 0.0, 0.0),
    start_tangent=_Vec(100.0, 0.0, 0.0),
    end_pos=_Vec(200.0, 0.0, 0.0),
    end_tangent=_Vec(100.0, 0.0, 0.0),
    start_scale=_Vec2(1.0, 1.0),
    end_scale=_Vec2(1.0, 1.0),
    start_roll=0.0,
    end_roll=0.0,
    start_offset=_Vec2(50.0, -25.0),
    end_offset=_Vec2(10.0, 20.0),
)
sp_comp = _SplineMeshComp(spline_params=sp_params_obj, spline_up_dir=_Vec(0.0, 0.0, 1.0))
sp_dict = EL.extract_spline_mesh_params(sp_comp)
check("spline_mesh exports start_offset in metres",
      sp_dict["start_offset"] == [0.5, -0.25], sp_dict.get("start_offset"))
check("spline_mesh exports end_offset in metres",
      sp_dict["end_offset"] == [0.1, 0.2], sp_dict.get("end_offset"))

# 3d. _get_skeletal_mesh UE 5.1+ fallback (#14c)
sk_asset = u.SkeletalMesh("SK_Hero", "/Game/SK_Hero.SK_Hero")
sk_comp_new = _FakeComp(skeletal_mesh_asset=sk_asset)
sk_comp_skinned = _FakeComp(skinned_asset=sk_asset)
sk_comp_legacy = _FakeComp(skeletal_mesh=sk_asset)
check("EL._get_skeletal_mesh reads skeletal_mesh_asset", EL._get_skeletal_mesh(sk_comp_new) is sk_asset)
check("EL._get_skeletal_mesh reads skinned_asset", EL._get_skeletal_mesh(sk_comp_skinned) is sk_asset)
check("EL._get_skeletal_mesh reads legacy skeletal_mesh", EL._get_skeletal_mesh(sk_comp_legacy) is sk_asset)
check("EX._get_skeletal_mesh reads skeletal_mesh_asset", EX._get_skeletal_mesh(sk_comp_new) is sk_asset)

# 3e. Cancelled level export returns False without clobbering output JSON (#17)
tmp_cancel = tempfile.mkdtemp(prefix="ue2g_cancel_")
try:
    out_json = os.path.join(tmp_cancel, "level_layout.json")
    with open(out_json, "w", encoding="utf-8") as f:
        f.write('{"sentinel": true}')

    class _CancellingSlowTask:
        def __init__(self, *a, **kw):
            pass
        def __enter__(self):
            return self
        def __exit__(self, *a):
            return False
        def make_dialog(self, can_cancel=True):
            pass
        def should_cancel(self):
            return True
        def enter_progress_frame(self, *a, **kw):
            pass

    class _FakeActorSubsys:
        def get_all_level_actors(self):
            return [_FakeActor("Actor1")]

    u.ScopedSlowTask = _CancellingSlowTask
    u.EditorActorSubsystem = _FakeActorSubsys
    u.get_editor_subsystem = lambda cls: cls()
    u.EditorLevelLibrary = type("ELL", (), {"get_all_level_actors": staticmethod(lambda: [_FakeActor("Actor1")])})
    u.Paths = type("Paths", (), {"project_dir": staticmethod(lambda: tmp_cancel)})

    res_cancel = EL.export_level_to_json(save_path=out_json, show_dialogs=False)
    check("cancelled export_level_to_json returns False", res_cancel is False, res_cancel)
    with open(out_json, "r", encoding="utf-8") as f:
        check("cancelled export does NOT overwrite existing JSON",
              json.load(f) == {"sentinel": True})
finally:
    shutil.rmtree(tmp_cancel, ignore_errors=True)


# =============================================================================
print("\n=== 4. export_static_meshes_to_gltf: LOD subsystem & cross-feature tex map ===")
# 4a. get_mesh_lod_count uses StaticMeshEditorSubsystem when EditorStaticMeshLibrary is absent (#14b)
class _FakeSMES:
    def get_lod_count(self, mesh):
        return 4


u.StaticMeshEditorSubsystem = _FakeSMES
u.get_editor_subsystem = lambda cls: cls()
if hasattr(u, "EditorStaticMeshLibrary"):
    delattr(u, "EditorStaticMeshLibrary")
lod_mesh = _FakeStaticMesh("SM_Lod", "/Game/SM_Lod.SM_Lod", num_lods=2)
check("get_mesh_lod_count prefers StaticMeshEditorSubsystem",
      EX.get_mesh_lod_count(lod_mesh) == 4, EX.get_mesh_lod_count(lod_mesh))
delattr(u, "StaticMeshEditorSubsystem")
check("get_mesh_lod_count falls back to mesh.get_num_lods() when subsystems absent",
      EX.get_mesh_lod_count(lod_mesh) == 2, EX.get_mesh_lod_count(lod_mesh))

# 4b. build_texture_name_map with extra_textures disambiguates cross-feature collisions (#15)
t_mesh = _FakeTex("T_Noise", "/Game/Meshes/T_Noise.T_Noise")
t_decal = _FakeTex("T_Noise", "/Game/Decals/T_Noise.T_Noise")
m_with_t = _FakeMat("M_Base", "/Game/M_Base.M_Base", texparams={"BaseColor": t_mesh})
sm_with_t = _FakeStaticMesh("SM_A", "/Game/SM_A.SM_A", mats=[m_with_t])
t_map, t_by_path = EX.build_texture_name_map([sm_with_t], extra_textures=[t_decal])
check("cross-feature colliding textures get distinct disambiguated names",
      t_map[t_mesh] != t_map[t_decal] and t_map[t_mesh].startswith("T_Noise_") and t_map[t_decal].startswith("T_Noise_"),
      (t_map.get(t_mesh), t_map.get(t_decal)))


# =============================================================================
print("\n=== 5. export_foliage: actor-level hidden flag ===")
fol_mesh = _FakeStaticMesh("SM_Bush", "/Game/SM_Bush.SM_Bush")
fol_comp = _FoliageInstComp(
    name="FoliageComp",
    instances=[_Transform()],
    static_mesh=fol_mesh,
    visible=True,
)
fol_actor = u.InstancedFoliageActor("FoliageActor", comps=[fol_comp], hidden_ed=True)
fol_entries = EF.collect_foliage([fol_actor], lambda _m: "SM_Bush")
check("foliage entry respects actor is_hidden_ed()",
      len(fol_entries) == 1 and fol_entries[0]["visible"] is False, fol_entries)


# =============================================================================
print("\n=== 6. tscn_writer: multi-component flags, probe blend_distance, audio ===")
tmp_tscn = tempfile.mkdtemp(prefix="ue2g_tscn_fixes_")
try:
    models_dir = os.path.join(tmp_tscn, "models")
    os.makedirs(models_dir, exist_ok=True)
    with open(os.path.join(models_dir, "SM_Part.gltf"), "w", encoding="utf-8") as f:
        json.dump({"asset": {"version": "2.0"}}, f)
    with open(os.path.join(models_dir, "SM_Part_LOD1.gltf"), "w", encoding="utf-8") as f:
        json.dump({"asset": {"version": "2.0"}}, f)

    layout_path = os.path.join(tmp_tscn, "layout.json")
    out_tscn = os.path.join(tmp_tscn, "out.tscn")
    layout_data = {
        "format_version": 2,
        "meshes": {
            "SM_Part": {
                "collision": {"boxes": [{"size": [100, 100, 100], "godot_local_transform": {"translation": [0, 0, 0], "rotation_quat": [0, 0, 0, 1], "scale": [1, 1, 1]}}]},
                "lods": [
                    {"lod_index": 0, "export_name": "SM_Part", "screen_size": 1.0},
                    {"lod_index": 1, "export_name": "SM_Part_LOD1", "screen_size": 0.2},
                ],
            }
        },
        "actors": [{
            "name": "MultiActor",
            "godot_transform": {"translation": [0, 0, 0], "rotation_quat": [0, 0, 0, 1], "scale": [1, 1, 1]},
            "components": [
                {
                    "name": "HiddenNoCol",
                    "mesh_name": "SM_Part",
                    "mesh_key": "SM_Part",
                    "visible": False,
                    "collision_enabled": False,
                    "godot_world_transform": {"translation": [0, 0, 0], "rotation_quat": [0, 0, 0, 1], "scale": [1, 1, 1]},
                },
                {
                    "name": "SplinePart",
                    "mesh_name": "SM_Part",
                    "mesh_key": "SM_Part",
                    "collision_enabled": True,
                    "spline_mesh": {"start_pos": [0, 0, 0], "end_pos": [1, 0, 0]},
                    "godot_world_transform": {"translation": [2, 0, 0], "rotation_quat": [0, 0, 0, 1], "scale": [1, 1, 1]},
                },
            ],
        }],
        "reflection_probes": [{
            "name": "Probe1",
            "shape": "box",
            "size_m": [4.0, 4.0, 4.0],
            "blend_distance_m": 1.25,
            "godot_transform": {"translation": [0, 1, 0], "rotation_quat": [0, 0, 0, 1], "scale": [1, 1, 1]},
        }],
        "audio": [
            {
                "name": "BGM_2D",
                "is_spatialized": False,
                "looping": True,
                "volume_db": -6.0,
                "pitch_scale": 1.0,
                "auto_play": True,
                "godot_transform": {"translation": [5, 0, 0], "rotation_quat": [0, 0, 0, 1], "scale": [1, 1, 1]},
            },
            {
                "name": "SFX_3D",
                "is_spatialized": True,
                "looping": False,
                "attenuation_radius_m": 25.0,
                "godot_transform": {"translation": [5, 0, 0], "rotation_quat": [0, 0, 0, 1], "scale": [1, 1, 1]},
            },
        ],
    }
    ok = TW.write_tscn(
        layout_data,
        out_tscn,
        {"models": "res://models/"},
        {
            "godot_project_dir": tmp_tscn,
            "reflection_probes": True,
            "audio": True,
            "metadata": True,
        },
    )
    check("write_tscn returns True", ok is True, ok)
    with open(out_tscn, "r", encoding="utf-8") as f:
        tscn = f.read()

    check("multi-component collision_enabled=False and spline_mesh skip StaticBody3D",
          'type="StaticBody3D"' not in tscn, tscn)
    check("multi-component visible=False writes visible = false",
          "visible = false" in tscn)
    check("multi-component spline_mesh writes metadata/unreal_spline_mesh = true",
          "metadata/unreal_spline_mesh = true" in tscn)
    check("multi-component writes LOD metadata",
          'metadata/unreal_lod_files = PackedStringArray("res://models/SM_Part_LOD1.gltf")' in tscn)
    check("ReflectionProbe writes blend_distance = 1.25",
          "blend_distance = 1.25" in tscn)
    check("non-spatialized audio emits AudioStreamPlayer",
          '[node name="BGM_2D" type="AudioStreamPlayer" parent="UnrealAudio"]' in tscn)
    check("looping audio writes metadata/unreal_looping = true",
          "metadata/unreal_looping = true" in tscn)
    check("spatialized audio emits AudioStreamPlayer3D",
          '[node name="SFX_3D" type="AudioStreamPlayer3D" parent="UnrealAudio"]' in tscn)
finally:
    shutil.rmtree(tmp_tscn, ignore_errors=True)


# =============================================================================
print("\n=== 7. ue2g_diagnose: shader texture_paths included in layout_texture_names ===")
diag_layout = {
    "meshes": {
        "SM_Blend": {
            "materials": [{
                "parameters": {
                    "shader": {
                        "code": "shader_type spatial;",
                        "tex_0": "T_LayerA",
                        "tex_1": "T_LayerB",
                        "texture_paths": {
                            "tex_0": "/Game/T_LayerA.T_LayerA",
                            "tex_1": "/Game/T_LayerB.T_LayerB",
                        },
                    }
                }
            }]
        }
    }
}
shader_texs = DIAG.layout_texture_names(diag_layout)
check("layout_texture_names collects shader textures",
      "T_LayerA" in shader_texs and "T_LayerB" in shader_texs, shader_texs)


# =============================================================================
print("\n=== 8. Godot GDScript regression markers ===")
def _read_gd(filename):
    with open(os.path.join(ADDON_DIR, filename), "r", encoding="utf-8") as f:
        return f.read()


gd_spline = _read_gd("import_spline_mesh.gd")
check("import_spline_mesh.gd fixes z-axis frame handedness via ref.cross(fwd)",
      "right = ref.cross(fwd).normalized()" in gd_spline)
check("import_spline_mesh.gd applies start_offset/end_offset",
      "start_offset" in gd_spline and "end_offset" in gd_spline)
check("import_spline_mesh.gd rotates vertex tangents",
      "get_vertex_tangent" in gd_spline and "set_vertex_tangent" in gd_spline)

gd_skel = _read_gd("import_skeletal_physics.gd")
check("import_skeletal_physics.gd initializes PhysicalBone3D transform from get_bone_global_rest",
      "get_bone_global_rest" in gd_skel)

gd_fol = _read_gd("import_foliage.gd")
check("import_foliage.gd resolves library materials by name before slot index",
      "_find_material_entry" in gd_fol and "surface_slots" in gd_fol)

gd_ter = _read_gd("import_terrain.gd")
check("import_terrain.gd persists packed splatmap via save_generated_texture and FORMAT_RGBA8",
      "FORMAT_RGBA8" in gd_ter and "Common.save_generated_texture(packed" in gd_ter)

gd_dock = _read_gd("importer_dock.gd")
check("importer_dock.gd restores nested owners on Redo via _reown_subtree",
      "_reown_subtree" in gd_dock)

gd_env = _read_gd("import_environment.gd")
check("import_environment.gd flips normal channels on mirrored decal crop",
      "is_normal and (flip_u or flip_v)" in gd_env and "c.r = 1.0 - c.r" in gd_env)
check("import_environment.gd flips green channel on external decal normal maps",
      "_load_normal_texture" in gd_env and "TextureLimit.invert_green_channel" in gd_env)
check("import_environment.gd wires ReflectionProbe blend_distance",
      'probe.set("blend_distance"' in gd_env)
check("import_environment.gd wires Audio is_spatialized, looping, and external WAV",
      "AudioStreamPlayer.new()" in gd_env and "AudioStreamWAV.load_from_file" in gd_env and "LOOP_FORWARD" in gd_env)

gd_layout = _read_gd("import_unreal_layout.gd")
check("import_unreal_layout.gd detects MISSING_ placeholder on re-import",
      'contains("MISSING_")' in gd_layout)
check("import_unreal_layout.gd uses reparent() and rebuilds collision on scale change",
      "existing_node.reparent(desired_parent, false)" in gd_layout and "_populate_collision_shapes(" in gd_layout)
check("import_unreal_layout.gd guards prefab sharing on uniform scale and uses full actor_transform inverse",
      "actor_extra_scale == Vector3.ONE" in gd_layout and "actor_transform.affine_inverse()" in gd_layout)
check("import_unreal_layout.gd clamps LOD visibility_range_end against max_draw_distance_m",
      "mi.visibility_range_begin >= max_d" in gd_layout)
check("import_unreal_layout.gd resets transparency, cull_mode, and emission when duplicating materials",
      "TRANSPARENCY_DISABLED" in gd_layout and "CULL_BACK" in gd_layout and "mat.emission_enabled = false" in gd_layout)


print("\n" + "=" * 60)
if FAIL:
    print("FAILED (%d): %s" % (len(FAIL), ", ".join(FAIL)))
    sys.exit(1)
print("ALL CODE SWEEP REGRESSION CHECKS PASSED")
