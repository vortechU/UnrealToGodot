"""
Translates vertex-painted Unreal materials into Godot shaders.

A StandardMaterial3D holds one layer. A material that blends layers by vertex paint
(puddles, moss, broken plaster) has no equivalent there, so its graph is written out as
a Godot spatial shader: same textures, same arithmetic, same parameters.

Only graphs that read vertex colors and only the nodes below. Anything else returns None
and the material keeps its single-layer approximation. See docs/SCHEMA_V2.md ("shader").
"""

import base64
import re

import unreal

import ue2g_common

# Unreal material output -> (GLSL target, width, Unreal's value when nothing is wired in).
_OUTPUTS = (
    ("MP_BASE_COLOR", "ALBEDO", 3, "vec3(0.0)"),
    ("MP_METALLIC", "METALLIC", 1, None),
    ("MP_SPECULAR", "SPECULAR", 1, None),
    ("MP_ROUGHNESS", "ROUGHNESS", 1, "0.5"),
    ("MP_AMBIENT_OCCLUSION", "AO", 1, None),
    ("MP_EMISSIVE_COLOR", "EMISSION", 3, None),
    ("MP_NORMAL", "NORMAL_MAP", 3, None),
    ("MP_OPACITY_MASK", "ALPHA", 1, None),
)

_BINARY = {"Add": "+", "Subtract": "-", "Multiply": "*", "Divide": "/"}
_MIN_MAX = {"Min": "min", "Max": "max"}
_UNARY = {
    "OneMinus": "(1.0 - {0})", "Saturate": "clamp({0}, 0.0, 1.0)", "Abs": "abs({0})",
    "Floor": "floor({0})", "Frac": "fract({0})", "SquareRoot": "sqrt(max({0}, 0.0))",
    "Reroute": "{0}",
}
_SWIZZLE = {"R": ".r", "G": ".g", "B": ".b", "A": ".a", "RGB": ".rgb", "RGBA": ""}


class _Unsupported(Exception):
    pass


def _num(value):
    return repr(float(value))


def _vec(values):
    return "vec%d(%s)" % (len(values), ", ".join(_num(v) for v in values))


def _broadcast(expr, width, target):
    if width == target:
        return expr
    if width == 1:
        return "vec%d(%s)" % (target, expr)
    if width > target:
        return expr + ("." + "xyzw"[:target])
    raise _Unsupported("a %d-component value where %d are needed" % (width, target))


class _Translator:
    def __init__(self, base, material):
        self.graph = ue2g_common.MaterialGraph(base)
        self.material = material
        self.mel = unreal.MaterialEditingLibrary
        self.uniforms = {}      # name -> declaration
        self.uniform_types = {} # name -> glsl_type
        self.textures = {}      # uniform name -> texture
        self.lines = []
        self.done = {}

    # -- parameters ---------------------------------------------------------

    def _parameter(self, kind, node):
        """The value this material (not its base) gives a parameter node."""
        name = ue2g_common.safe_get_prop(node, "parameter_name", "")
        default = ue2g_common.safe_get_prop(
            node, "texture" if kind == "texture" else "default_value", None)
        if not isinstance(self.material, unreal.MaterialInstance):
            return default
        for owner, getter in ((self.mel, "get_material_instance_%s_parameter_value" % kind),
                              (None, "k2_get_%s_parameter_value" % kind)):
            try:
                value = (getattr(owner, getter)(self.material, name) if owner
                         else getattr(self.material, getter)(name))
            except Exception:
                continue
            if value is not None:
                return value
        return default

    def _uniform(self, node, glsl_type, value):
        name = "p_" + re.sub(r"\W+", "_", str(
            ue2g_common.safe_get_prop(node, "parameter_name", ""))).strip("_").lower()
        if name in self.uniform_types and self.uniform_types[name] != glsl_type:
            name = "%s_%s" % (name, glsl_type)
        self.uniform_types[name] = glsl_type
        self.uniforms[name] = "uniform %s %s = %s;" % (glsl_type, name, value)
        return name

    # -- nodes --------------------------------------------------------------

    def value(self, node, out):
        """(GLSL expression, width) of one output pin."""
        key = ue2g_common.safe_get_name(node)
        if key not in self.done:
            expr, width, pins = self._node(node)
            name = "n%d" % len(self.done)
            self.lines.append("%s %s = %s;" % ("float" if width == 1 else "vec%d" % width,
                                               name, expr))
            self.done[key] = (name, width, pins)
        name, width, pins = self.done[key]
        if not pins:
            return name, width
        if out is None:
            raise _Unsupported("an ambiguous connection to %s" % ue2g_common.node_kind(node))
        pin = out if out in _SWIZZLE else "RGB"
        if width == 3 and pin == "RGBA":
            pin = "RGB"
        swizzle = _SWIZZLE[pin]
        if width == 3 and pin == "RGB":
            swizzle = ""
        if width == 3 and pin == "A":
            raise _Unsupported("the alpha of a normal map")
        return name + swizzle, (len(pin))

    def _input(self, node, wanted, const_prop=None, default=0.0):
        for name, child, out in self.graph.inputs(node):
            if name.lower() in wanted:
                return self.value(child, out)
        return _num(ue2g_common.safe_get_prop(node, const_prop, default)
                    if const_prop else default), 1

    def _only(self, node):
        inputs = self.graph.inputs(node)
        if not inputs:
            raise _Unsupported("an unconnected %s" % ue2g_common.node_kind(node))
        return self.value(inputs[0][1], inputs[0][2])

    def _input_excluding(self, node, excluded):
        for name, child, out in self.graph.inputs(node):
            if name.lower() not in excluded:
                return self.value(child, out)
        raise _Unsupported("an unconnected %s" % ue2g_common.node_kind(node))

    def _uv(self, node):
        for name, child, out in self.graph.inputs(node):
            if name.lower() in ("uvs", "uv", "coordinates", "coordinate"):
                # shortcut: parallax only shifts the coordinates, so it is dropped;
                # translate it if a pack depends on the depth.
                if ue2g_common.node_kind(child) in ("MaterialFunctionCall", "BumpOffset"):
                    return self._uv(child)
                expr, width = self.value(child, out)
                return _broadcast(expr, width, 2)
        return "UV"

    def _node(self, node):
        """(expression, width, whether it has channel pins)."""
        kind = ue2g_common.node_kind(node)
        prop = lambda name, default=None: ue2g_common.safe_get_prop(node, name, default)

        if kind == "Constant":
            return _num(prop("r", 0.0)), 1, False
        if kind == "Constant2Vector":
            return _vec((prop("r", 0.0), prop("g", 0.0))), 2, False
        if kind in ("Constant3Vector", "Constant4Vector"):
            c = prop("constant")
            values = (c.r, c.g, c.b, c.a)[:int(kind[8])]
            return _vec(values), len(values), True
        if kind == "ScalarParameter":
            return self._uniform(node, "float", _num(self._parameter("scalar", node))), 1, False
        if kind == "VectorParameter":
            c = self._parameter("vector", node)
            return self._uniform(node, "vec4", _vec((c.r, c.g, c.b, c.a))), 4, True
        if kind == "VertexColor":
            return "COLOR", 4, True
        if kind == "TextureCoordinate":
            index = int(prop("coordinate_index", 0))
            if index > 1:
                raise _Unsupported("UV channel %d" % index)
            return "%s * %s" % ("UV2" if index else "UV", _vec(
                (prop("u_tiling", 1.0), prop("v_tiling", 1.0)))), 2, False
        if kind in ("TextureSample", "TextureSampleParameter2D"):
            return self._sample(node, kind)
        if kind in _BINARY or kind in _MIN_MAX:
            a, wa = self._input(node, ("a",), "const_a", 0.0)
            b, wb = self._input(node, ("b",), "const_b", 1.0)
            width = max(wa, wb) if 1 in (wa, wb) else min(wa, wb)
            a, b = _broadcast(a, wa, width), _broadcast(b, wb, width)
            if kind in _MIN_MAX:
                return "%s(%s, %s)" % (_MIN_MAX[kind], a, b), width, False
            return "%s %s %s" % (a, _BINARY[kind], b), width, False
        if kind == "LinearInterpolate":
            a, wa = self._input(node, ("a",), "const_a", 0.0)
            b, wb = self._input(node, ("b",), "const_b", 1.0)
            t, wt = self._input(node, ("alpha",), "const_alpha", 0.5)
            width = max(wa, wb) if 1 in (wa, wb) else min(wa, wb)
            if width == 1 and wt > 1:
                width = wt
            return "mix(%s, %s, %s)" % (_broadcast(a, wa, width), _broadcast(b, wb, width),
                                        _broadcast(t, wt, width)), width, False
        if kind == "Power":
            a, wa = self._input(node, ("base",))
            e, we = self._input(node, ("exp", "exponent"), "const_exponent", 2.0)
            width = max(wa, we) if 1 in (wa, we) else min(wa, we)
            return "pow(max(%s, 0.0), %s)" % (_broadcast(a, wa, width), _broadcast(e, we, width)), width, False
        if kind == "Clamp":
            a, wa = self._only(node)
            lo, wl = self._input(node, ("min",), "min_default", 0.0)
            hi, wh = self._input(node, ("max",), "max_default", 1.0)
            width = wa
            for w in (wl, wh):
                width = max(width, w) if 1 in (width, w) else min(width, w)
            return "clamp(%s, %s, %s)" % (_broadcast(a, wa, width), _broadcast(lo, wl, width),
                                          _broadcast(hi, wh, width)), width, False
        if kind == "ComponentMask":
            a, wa = self._only(node)
            kept = [i for i, c in enumerate("rgba") if prop(c, False)]
            if wa == 1:
                return a, 1, False
            if not kept or kept[-1] >= wa:
                raise _Unsupported("a component mask past its input")
            return a + "." + "".join("xyzw"[i] for i in kept), len(kept), False
        if kind == "AppendVector":
            a, wa = self._input(node, ("a",))
            b, wb = self._input(node, ("b",))
            return "vec%d(%s, %s)" % (wa + wb, a, b), wa + wb, False
        if kind == "Desaturation":
            a, wa = self._input_excluding(node, ("fraction",))
            f, wf = self._input(node, ("fraction",), None, 1.0)
            return "mix(%s, vec3(dot(%s, vec3(0.3, 0.59, 0.11))), %s)" % (
                _broadcast(a, wa, 3), _broadcast(a, wa, 3), _broadcast(f, wf, 1)), 3, False
        if kind in ("StaticSwitch", "StaticSwitchParameter"):
            if kind == "StaticSwitchParameter":
                on = self._parameter("static_switch", node)
            else:
                on = prop("default_value", True)
                for name, child, _out in self.graph.inputs(node):
                    if name.lower() == "value":
                        ckind = ue2g_common.node_kind(child)
                        if ckind == "StaticBoolParameter":
                            val = self._parameter("static_switch", child)
                            on = bool(val) if val is not None else bool(ue2g_common.safe_get_prop(child, "default_value", False))
                        elif ckind == "StaticBool":
                            on = bool(ue2g_common.safe_get_prop(child, "value", False))
                        elif ckind == "Constant":
                            on = bool(ue2g_common.safe_get_prop(child, "r", 0.0))
                        break
            for name, child, out in self.graph.inputs(node):
                if name == str(bool(on)):
                    expr, width = self.value(child, out)
                    return expr, width, False
            raise _Unsupported("a static switch with nothing on its %s side" % bool(on))
        if kind in _UNARY:
            a, wa = self._only(node)
            return _UNARY[kind].format(a), wa, False
        raise _Unsupported(kind)

    def _sample(self, node, kind):
        if any(ue2g_common.node_kind(c).startswith("TextureObject")
               for _n, c, _o in self.graph.inputs(node)):
            raise _Unsupported("a texture object input")
        texture = (self._parameter("texture", node) if kind == "TextureSampleParameter2D"
                   else ue2g_common.safe_get_prop(node, "texture", None))
        if texture is None:
            raise _Unsupported("a sample without a texture")
        path = texture.get_path_name()
        name = next((n for n, t in self.textures.items() if t.get_path_name() == path), None)
        normal = "NORMAL" in str(ue2g_common.safe_get_prop(node, "sampler_type", "")).upper()
        if name is None:
            name = "tex_%d" % len(self.textures)
            self.textures[name] = texture
            hint = ("hint_normal, " if normal else
                    "source_color, " if ue2g_common.safe_get_prop(texture, "srgb", False) else "")
            self.uniforms[name] = ("uniform sampler2D %s : %sfilter_linear_mipmap_anisotropic, "
                                   "repeat_enable;" % (name, hint))
        expr = "texture(%s, %s)" % (name, self._uv(node))
        if normal:
            return "unpack_normal(%s)" % expr, 3, True
        return expr, 4, True

    # -- the shader ---------------------------------------------------------

    def shader(self, outputs, two_sided, clip_value):
        body = []
        for prop_name, target, width, unwired in _OUTPUTS:
            if prop_name == "MP_OPACITY_MASK" and clip_value is None:
                continue
            if prop_name not in outputs:
                if unwired:
                    body.append("%s = %s;" % (target, unwired))
                continue
            expr, w = self.value(*outputs[prop_name])
            expr = _broadcast(expr, w, width)
            if target == "NORMAL_MAP":
                expr = "%s * 0.5 + 0.5" % expr
            elif target != "EMISSION":
                expr = "clamp(%s, 0.0, 1.0)" % expr
            body.append("%s = %s;" % (target, expr))
            if target == "ALPHA":
                body.append("ALPHA_SCISSOR_THRESHOLD = %s;" % _num(clip_value))
        head = ["shader_type spatial;"]
        if two_sided:
            head.append("render_mode cull_disabled;")
        helper = []
        if any("unpack_normal(" in line for line in self.lines):
            helper = ["", "vec3 unpack_normal(vec4 texel) {",
                      "\tvec2 xy = texel.rg * 2.0 - 1.0;",
                      "\treturn vec3(xy, sqrt(max(0.0, 1.0 - dot(xy, xy))));", "}"]
        return "\n".join(head + [""] + [self.uniforms[k] for k in sorted(self.uniforms)] + helper
                         + ["", "void fragment() {"]
                         + ["\t" + line for line in self.lines + body] + ["}", ""])


def _traced_outputs(base):
    """{material property name: (node, output pin)} for everything wired in."""
    mel = unreal.MaterialEditingLibrary
    result = {}
    for prop_name, _target, _width, _unwired in _OUTPUTS:
        prop = getattr(unreal.MaterialProperty, prop_name, None)
        node = mel.get_material_property_input_node(base, prop) if prop is not None else None
        if node is not None:
            result[prop_name] = (node, str(
                mel.get_material_property_input_node_output_name(base, prop)))
    return result


_reads_cache = {}


def clear_cache():
    """Call once per export: a material may have been edited since the last one."""
    _reads_cache.clear()


def reads_vertex_color(material):
    """True when the material's graph uses vertex paint. False when it cannot be told."""
    try:
        base = material.get_base_material()
        key = base.get_path_name()
        if key not in _reads_cache:
            graph = ue2g_common.MaterialGraph(base)
            pending = [node for node, _out in _traced_outputs(base).values()]
            seen = set()
            found = False
            while pending and not found:
                node = pending.pop()
                name = ue2g_common.safe_get_name(node)
                if name in seen:
                    continue
                seen.add(name)
                found = ue2g_common.node_kind(node) == "VertexColor"
                pending.extend(child for _n, child, _o in graph.inputs(node))
            _reads_cache[key] = found
        return _reads_cache[key]
    except Exception:
        return False


def build_shader(material, blend_mode, two_sided, clip_value):
    """
    ({"code", "tex_0": texture name, ..., "texture_paths": {...}}, textures) for a
    vertex-painted material, or (None, []). Same slots as a parameters dict, so colliding
    texture names are resolved by the same code.
    """
    if blend_mode not in ("opaque", "masked") or not reads_vertex_color(material):
        return None, []
    try:
        base = material.get_base_material()
        translator = _Translator(base, material)
        code = translator.shader(_traced_outputs(base), two_sided,
                                 clip_value if blend_mode == "masked" else None)
    except _Unsupported as e:
        unreal.log_warning(
            "Material %s blends layers by vertex paint using %s, which cannot be translated; "
            "only its base layer is exported." % (ue2g_common.safe_get_name(material), e))
        return None, []
    except Exception as e:
        unreal.log_warning("Could not translate material %s: %s"
                           % (ue2g_common.safe_get_name(material), e))
        return None, []
    shader = {"code": code, "texture_paths": {}}
    for name, texture in translator.textures.items():
        shader[name] = texture.get_name()
        shader["texture_paths"][name] = texture.get_path_name()
    return shader, list(translator.textures.values())


# ---------------------------------------------------------------------------
# The paint itself: per-component vertex colors, which no mesh file carries
# ---------------------------------------------------------------------------

_warned = set()


def extract_vertex_paint(comp):
    """
    (positions, colors) painted on one placed mesh, or None when it carries no paint.
    Positions are glTF mesh-local meters, [x, y, z, x, ...]; colors base64 RGBA bytes.
    """
    if not any(m and reads_vertex_color(m) for m in comp.get_materials()):
        return None
    scene_utils = getattr(unreal, "GeometryScript_SceneUtils", None)
    if scene_utils is None:
        if "plugin" not in _warned:
            _warned.add("plugin")
            unreal.log_warning("Vertex paint was not exported: it is read through Unreal's "
                               "Geometry Script plugin, which is disabled in this project.")
        return None
    options = unreal.GeometryScriptCopyMeshFromComponentOptions()
    options.set_editor_property("want_instance_colors", True)
    lod = unreal.GeometryScriptMeshReadLOD()
    lod.lod_type = unreal.GeometryScriptLODType.RENDER_DATA   # the only LODs with paint
    lod.lod_index = 0
    options.set_editor_property("requested_lod", lod)
    mesh = unreal.DynamicMesh()
    scene_utils.copy_mesh_from_component(comp, mesh, options, False)
    lists = unreal.GeometryScript_List
    colors = lists.convert_color_list_to_array(
        unreal.GeometryScript_VertexColors.get_mesh_per_vertex_colors(mesh)[1])
    if not colors or all(min(c.r, c.g, c.b, c.a) >= 1.0 for c in colors):
        return None
    positions = lists.convert_vector_list_to_array(
        unreal.GeometryScript_MeshQueries.get_all_vertex_positions(mesh, False)[1])
    if len(positions) != len(colors):
        return None
    scale = ue2g_common.CM_TO_M
    flat = [round(v * scale, 5) for p in positions for v in (p.x, p.z, p.y)]
    raw = bytes(min(255, max(0, int(round(v * 255.0))))
                for c in colors for v in (c.r, c.g, c.b, c.a))
    return flat, base64.b64encode(raw).decode("ascii")
