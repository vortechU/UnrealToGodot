"""
tscn_writer.py  --  Direct Godot 4 text scene (.tscn) writer for the Unreal to Godot toolchain.

This module runs INSIDE Unreal Engine's embedded Python (no Godot available). It consumes a
layout JSON dict (schema v2, see docs/SCHEMA_V2.md) and writes a byte-valid Godot 4 text
scene file that opens in the editor without errors.

Entry point:
    write_tscn(layout_data, output_path, res_paths, options) -> bool

  * layout_data : the full schema-v2 dict (meshes, actors, lights, post_process, height_fog,
                  sky_light, decals, foliage, navigation, landscapes).
  * output_path : absolute filesystem path of the .tscn to write (inside the Godot project).
  * res_paths   : {"models": "res://models/", "textures": "res://textures/",
                   "terrain": "res://terrain/"}.
  * options     : {"scene_name": str, "godot_project_dir": absolute path,
                   "light_energy_scale": float (default 1.0),
                   "lights","decals","foliage","navigation","metadata","landscape": bool}.
                   A missing/False feature flag skips that feature.

The module is importable and unit-testable WITHOUT `unreal` (the import is guarded); it never
raises out of write_tscn (all failures are logged and return False).

MATERIALS: this writer instances the exported .gltf scenes as-is and applies NO material
overrides of its own. Whatever the .gltf carries is what renders. That is why the glTF
export writes baseColorFactor/roughnessFactor/metallicFactor and the base-colour and normal
uris into each material (export_static_meshes_to_gltf.inject_texture_references) rather than
leaving glTF's spec defaults in place -- metallicFactor defaults to 1.0, which would render
every mesh in a .tscn chrome. The packed roughness/metallic/AO map still cannot travel this
way (glTF fixes the channel order and Unreal's packing varies), nor can UV tiling, so a
scene that needs those must go through the Godot-side layout importer, which rebuilds
materials from the layout JSON.

------------------------------------------------------------------------------------------------
Godot 4 .tscn format decisions (verified against godotengine/godot-docs tscn.rst and the
RenderingServer / Transform3D / MultiMesh source semantics):

  * Header: [gd_scene load_steps=N format=3]  where N = 1 + ext_resource_count + sub_resource_count.
    `format=3` marks a Godot 4 scene. `uid` is intentionally omitted -- Godot mints one on first
    save. `load_steps` is advisory (deprecated in 4.6 but still parsed); we emit the classic value.

  * ext_resource ids are "<index>_<token>" strings (e.g. "1_a3f9c"); sub_resource ids are
    "<GodotType>_<token>" (e.g. "BoxShape3D_a3f9c"). Tokens are short base36 counters, guaranteed
    unique within the file. References use ExtResource("id") / SubResource("id").

  * Transform3D(...) serialization is 12 reals: the 3x3 basis in ROW-MAJOR order followed by the
    origin, i.e. [m00,m01,m02, m10,m11,m12, m20,m21,m22, ox,oy,oz]. The basis COLUMN j is the
    rotated+scaled local axis j -> column0 = (m00,m10,m20). We build the basis from the schema's
    quaternion (columns = rotated axes) and scale each column by scale[j] -- the schema's `scale`
    is a LOCAL scale, so it multiplies columns (Godot's Basis.scaled_local), NOT rows (Basis.scaled,
    which applies it in the parent frame). Then compose parent*child as Transform3D multiplication.

  * MultiMesh buffer (transform_format = 1 / TRANSFORM_3D) is a PackedFloat32Array with 12 floats
    per instance laid out as a 3x4 ROW-MAJOR matrix: [m00,m01,m02,ox, m10,m11,m12,oy, m20,m21,m22,oz]
    (origin components live at indices 3, 7, 11). The schema's foliage `godot_transforms` stores
    basis COLUMNS then origin, so we transpose per instance when filling the buffer.

  * MultiMesh mesh binding decision: a Mesh living inside an instanced glTF PackedScene CANNOT be
    referenced from a .tscn (there is no stable sub-resource path to it). We therefore emit each
    MultiMesh with its transform data intact (transform_format, instance_count, buffer) but WITHOUT
    a `mesh` property, and tag the MultiMeshInstance3D node with metadata/source_model (the res://
    glTF path) and metadata/unreal_foliage = true. The Godot addon dock's "bind foliage meshes"
    pass resolves the glTF, extracts its first Mesh, and assigns it to each MultiMesh -- that is the
    orchestrator's job; ours is only to emit valid transform data. Emitting the raw buffer (rather
    than the instance_count=0 + metadata fallback) is safe because the 3x4 layout is well-defined
    and round-trips through Godot's MultiMesh loader.

  * A missing ext_resource file makes the WHOLE scene fail to load, so every ext_resource path is
    verified on disk (case-insensitive) under options["godot_project_dir"] before it is emitted.
    Missing meshes become a Marker3D placeholder ("MISSING_<mesh_key>" + metadata/missing_mesh);
    missing textures simply drop that texture property.
------------------------------------------------------------------------------------------------
"""

import os
import math

try:
    import unreal  # only used for logging when running inside Unreal

    def _log(msg):
        unreal.log("tscn_writer: " + str(msg))

    def _warn(msg):
        unreal.log_warning("tscn_writer: " + str(msg))

    def _err(msg):
        unreal.log_error("tscn_writer: " + str(msg))
except Exception:  # pragma: no cover - exercised only outside Unreal
    def _log(msg):
        print("tscn_writer: " + str(msg))

    def _warn(msg):
        print("tscn_writer [WARN]: " + str(msg))

    def _err(msg):
        print("tscn_writer [ERROR]: " + str(msg))


# ----------------------------------------------------------------------------------------------
# Small local math / formatting helpers (deliberately NOT imported from ue2g_common, which does
# `import unreal` at module top and is therefore unusable without Unreal).
# ----------------------------------------------------------------------------------------------

def _num(value, default=0.0):
    """Coerce a possibly-None/str JSON scalar to a finite float."""
    try:
        f = float(value)
        if math.isfinite(f):
            return f
        return default
    except (TypeError, ValueError):
        return default


def _clamp(value, low, high):
    """Clamps to [low, high]; the .tscn twin of GDScript's clampf()."""
    return low if value < low else (high if value > high else value)


# ---------------------------------------------------------------------------
# Environment constants. These MIRROR addons/unreal_importer/import_environment.gd
# -- the .tscn writer and the runtime addon build the same WorldEnvironment from
# the same layout, and they are only consistent because these agree. Change one,
# change the other; tests/test_tscn_writer.py checks them against each other.
# ---------------------------------------------------------------------------
_TONEMAP_ACES = 3               # Environment.TONE_MAPPER_ACES
_UE_DEFAULT_EXPOSURE_BIAS = 1.0
_MIDDLE_GREY = 0.18
_ISO_REFERENCE = 100.0
_EXPOSURE_SPEED_SCALE = 0.5 / 3.0


def _exposure_for_settings(settings):
    """Maps a volume's exposure settings to (tonemap_exposure | None, camera-attr lines).

    The Python twin of import_environment.gd::_apply_exposure -- see that function
    for why locked / ranged / bias-only are three different shapes. The second
    element is non-empty only for a real adaptation range, since Godot models auto
    exposure on CameraAttributes rather than on Environment.
    """
    has_bias = settings.get("exposure_bias") is not None
    bias = _num(settings.get("exposure_bias"), _UE_DEFAULT_EXPOSURE_BIAS)
    bias_scale = 2.0 ** bias

    min_raw = settings.get("exposure_min_brightness")
    max_raw = settings.get("exposure_max_brightness")
    if min_raw is None or max_raw is None:
        return (bias_scale if has_bias else None), []

    min_lum = max(_num(min_raw, 1.0), 0.0001)
    max_lum = max(_num(max_raw, 1.0), 0.0001)

    if abs(min_lum - max_lum) <= 1e-6 * max(1.0, abs(min_lum), abs(max_lum)):
        # Locked exposure ("manual"): no auto exposure, just the fixed multiplier.
        return _clamp(_MIDDLE_GREY / min_lum * bias_scale, 0.0, 64.0), []

    # Sensitivity is inverse to target luminance, so min/max swap sides.
    lines = [
        "auto_exposure_enabled = true",
        "auto_exposure_min_sensitivity = " + _f(_clamp(_ISO_REFERENCE / max_lum, 0.0, 64000.0)),
        "auto_exposure_max_sensitivity = " + _f(_clamp(_ISO_REFERENCE / min_lum, 0.0, 64000.0)),
    ]
    if settings.get("exposure_speed_up") is not None:
        lines.append("auto_exposure_speed = "
                     + _f(max(_num(settings.get("exposure_speed_up"), 3.0)
                              * _EXPOSURE_SPEED_SCALE, 0.01)))
    return bias_scale, lines


def _f(value, default=0.0):
    """Format a float compactly but precisely (shortest round-trip repr). Godot accepts
    plain decimals and scientific notation."""
    f = _num(value, default)
    # repr() gives the shortest string that round-trips; ensure a decimal point / exponent
    # so Godot parses it as a float rather than an int where that matters.
    s = repr(f)
    return s


def _quat_to_matrix(quat):
    """Unit quaternion [x, y, z, w] -> 3x3 rotation matrix (row-major, columns = rotated axes).
    Matches Godot's Basis(Quaternion) and ue2g_common's convention."""
    x = _num(quat[0]) if len(quat) > 0 else 0.0
    y = _num(quat[1]) if len(quat) > 1 else 0.0
    z = _num(quat[2]) if len(quat) > 2 else 0.0
    w = _num(quat[3]) if len(quat) > 3 else 1.0
    n = (x * x + y * y + z * z + w * w) ** 0.5
    if n > 1e-12:
        x, y, z, w = x / n, y / n, z / n, w / n
    else:
        x, y, z, w = 0.0, 0.0, 0.0, 1.0
    xx, yy, zz = x * x, y * y, z * z
    xy, xz, yz = x * y, x * z, y * z
    wx, wy, wz = w * x, w * y, w * z
    return [
        [1.0 - 2.0 * (yy + zz), 2.0 * (xy - wz), 2.0 * (xz + wy)],
        [2.0 * (xy + wz), 1.0 - 2.0 * (xx + zz), 2.0 * (yz - wx)],
        [2.0 * (xz - wy), 2.0 * (yz + wx), 1.0 - 2.0 * (xx + yy)],
    ]


def _transform_dict_to_mat(t):
    """Schema transform dict -> (rows3x3, origin3). Column j of the basis is scaled by scale[j]:
    the schema's `scale` is LOCAL to the node, so it scales columns (Godot Basis.scaled_local),
    not rows (Basis.scaled, which would apply the scale in the parent frame)."""
    if not isinstance(t, dict):
        return ([[1.0, 0.0, 0.0], [0.0, 1.0, 0.0], [0.0, 0.0, 1.0]], [0.0, 0.0, 0.0])
    trans = t.get("translation") or [0.0, 0.0, 0.0]
    quat = t.get("rotation_quat") or [0.0, 0.0, 0.0, 1.0]
    scale = t.get("scale") or [1.0, 1.0, 1.0]
    sx = _num(scale[0], 1.0) if len(scale) > 0 else 1.0
    sy = _num(scale[1], 1.0) if len(scale) > 1 else 1.0
    sz = _num(scale[2], 1.0) if len(scale) > 2 else 1.0
    r = _quat_to_matrix(quat)
    rows = [
        [r[0][0] * sx, r[0][1] * sy, r[0][2] * sz],
        [r[1][0] * sx, r[1][1] * sy, r[1][2] * sz],
        [r[2][0] * sx, r[2][1] * sy, r[2][2] * sz],
    ]
    origin = [
        _num(trans[0]) if len(trans) > 0 else 0.0,
        _num(trans[1]) if len(trans) > 1 else 0.0,
        _num(trans[2]) if len(trans) > 2 else 0.0,
    ]
    return (rows, origin)


def _mat_compose(parent, child):
    """Godot Transform3D multiplication: result = parent * child (parent applied last)."""
    pr, po = parent
    cr, co = child
    rows = [[sum(pr[i][k] * cr[k][j] for k in range(3)) for j in range(3)] for i in range(3)]
    origin = [sum(pr[i][k] * co[k] for k in range(3)) + po[i] for i in range(3)]
    return (rows, origin)


_IDENTITY_MAT = ([[1.0, 0.0, 0.0], [0.0, 1.0, 0.0], [0.0, 0.0, 1.0]], [0.0, 0.0, 0.0])

# Unreal's glTF exporter bakes mesh geometry in the (X, Z, Y) axis order, but the
# layout places actors/components with the (Y, Z, -X) convention. The two right-
# handed maps differ by a +90 deg yaw about up, so a mesh dropped at its layout
# transform yaws about its own pivot -- invisible on centre-pivoted props, but it
# scatters every off-centre modular piece (the "scattered building" bug). This is
# a pure rotation (columns = rotated axes): local X -> -Z, local Y -> Y, local Z -> X.
# Post-multiplying a placement basis by it re-seats the mesh (and the collision that
# rides under it) where it belongs. Mirrors Common.gltf_mesh_placement in
# import_common.gd; proof in tests/test_math.py section 9.
_MESH_AXIS_FIX = ([[0.0, 0.0, 1.0], [0.0, 1.0, 0.0], [-1.0, 0.0, 0.0]], [0.0, 0.0, 0.0])


def _apply_mesh_axis_fix(mat):
    """Post-multiply a layout placement by the glTF axis correction so a glTF mesh
    sitting at identity beneath it lands aligned. Origin is preserved."""
    return _mat_compose(mat, _MESH_AXIS_FIX)


def _mat_affine_inverse(mat):
    """Godot Transform3D.affine_inverse(): inverts a basis that may carry scale/shear.

    Falls back to identity on a singular basis (an actor scaled to zero on some
    axis), which keeps the export running instead of raising mid-write.
    """
    rows, origin = mat
    a, b, c = rows[0]
    d, e, f = rows[1]
    g, h, i = rows[2]
    det = a * (e * i - f * h) - b * (d * i - f * g) + c * (d * h - e * g)
    if abs(det) < 1e-12:
        _warn("actor basis is singular (zero scale?); using identity inverse")
        return _IDENTITY_MAT
    inv_det = 1.0 / det
    inv = [
        [(e * i - f * h) * inv_det, (c * h - b * i) * inv_det, (b * f - c * e) * inv_det],
        [(f * g - d * i) * inv_det, (a * i - c * g) * inv_det, (c * d - a * f) * inv_det],
        [(d * h - e * g) * inv_det, (b * g - a * h) * inv_det, (a * e - b * d) * inv_det],
    ]
    inv_origin = [-sum(inv[r][k] * origin[k] for k in range(3)) for r in range(3)]
    return (inv, inv_origin)


def _component_world_mat(comp, actor_mat):
    """A component's absolute placement, taken from the exporter rather than composed.

    Unreal's get_relative_transform() is measured against a component's IMMEDIATE
    parent component, and an actor's ROOT component has none -- its relative
    transform is already the world transform. Composing actor * relative therefore
    doubles the placement of every plain StaticMeshActor and drops intermediate
    transforms for deeply nested Blueprint components. Mirrors
    component_world_transform() in import_unreal_layout.gd.
    """
    if isinstance(comp, dict) and isinstance(comp.get("godot_world_transform"), dict):
        return _transform_dict_to_mat(comp["godot_world_transform"])
    return actor_mat


def _mat_to_transform3d(mat):
    """(rows3x3, origin3) -> 'Transform3D(m00,m01,m02, m10,m11,m12, m20,m21,m22, ox,oy,oz)'."""
    rows, origin = mat
    vals = [
        rows[0][0], rows[0][1], rows[0][2],
        rows[1][0], rows[1][1], rows[1][2],
        rows[2][0], rows[2][1], rows[2][2],
        origin[0], origin[1], origin[2],
    ]
    return "Transform3D(" + ", ".join(_f(v) for v in vals) + ")"


def _escape_string(s):
    """Escape a Python string for a Godot quoted string literal."""
    s = "" if s is None else str(s)
    s = s.replace("\\", "\\\\").replace('"', '\\"')
    s = s.replace("\n", "\\n").replace("\r", "\\r").replace("\t", "\\t")
    return s


_NODE_NAME_BAD = set('.:@/%"\'')


def _sanitize_node_name(name):
    """Godot node names must be non-empty and free of . : @ / % (and we also drop quotes/spaces
    edges). Invalid chars become underscores."""
    out = []
    for ch in str(name):
        if ch in _NODE_NAME_BAD or ord(ch) < 32:
            out.append("_")
        else:
            out.append(ch)
    result = "".join(out).strip().strip("_")
    return result if result else "Node"


def _color_literal(rgb, alpha=1.0):
    """[r,g,b(,a)] -> 'Color(r, g, b, a)'."""
    if not isinstance(rgb, (list, tuple)):
        rgb = [1.0, 1.0, 1.0]
    r = _num(rgb[0], 1.0) if len(rgb) > 0 else 1.0
    g = _num(rgb[1], 1.0) if len(rgb) > 1 else 1.0
    b = _num(rgb[2], 1.0) if len(rgb) > 2 else 1.0
    a = _num(rgb[3], alpha) if len(rgb) > 3 else alpha
    return "Color(" + ", ".join(_f(v) for v in (r, g, b, a)) + ")"


def _vec3_literal(vec):
    x = _num(vec[0]) if len(vec) > 0 else 0.0
    y = _num(vec[1]) if len(vec) > 1 else 0.0
    z = _num(vec[2]) if len(vec) > 2 else 0.0
    return "Vector3(" + ", ".join(_f(v) for v in (x, y, z)) + ")"


def _vec2_literal(vec):
    x = _num(vec[0]) if len(vec) > 0 else 0.0
    y = _num(vec[1]) if len(vec) > 1 else 0.0
    return "Vector2(" + ", ".join(_f(v) for v in (x, y)) + ")"


def _kelvin_to_rgb(kelvin):
    """Tanner Helland blackbody approximation -> linear-ish RGB multiplier (0..1)."""
    t = max(1000.0, min(15000.0, _num(kelvin, 6500.0))) / 100.0

    def clamp(v):
        return max(0.0, min(255.0, v))

    if t <= 66.0:
        r = 255.0
        g = clamp(99.4708025861 * math.log(t) - 161.1195681661) if t > 0 else 0.0
    else:
        r = clamp(329.698727446 * ((t - 60.0) ** -0.1332047592))
        g = clamp(288.1221695283 * ((t - 60.0) ** -0.0755148492))
    if t >= 66.0:
        b = 255.0
    elif t <= 19.0:
        b = 0.0
    else:
        b = clamp(138.5177312231 * math.log(t - 10.0) - 305.0447927307)
    return [r / 255.0, g / 255.0, b / 255.0]


# ----------------------------------------------------------------------------------------------
# The writer
# ----------------------------------------------------------------------------------------------

_MODEL_EXTS = (".gltf", ".glb")
_TEXTURE_EXTS = (".png", ".tga", ".jpg", ".jpeg", ".dds", ".exr", ".webp")


class _TscnWriter(object):
    def __init__(self, layout_data, res_paths, options):
        self.layout = layout_data or {}
        self.res_paths = res_paths or {}
        self.options = options or {}
        self.project_dir = self.options.get("godot_project_dir") or ""
        self.light_energy_scale = _num(self.options.get("light_energy_scale", 1.0), 1.0)

        self.models_res = self._norm_res_dir(self.res_paths.get("models", "res://models/"))
        self.textures_res = self._norm_res_dir(self.res_paths.get("textures", "res://textures/"))
        self.terrain_res = self._norm_res_dir(self.res_paths.get("terrain", "res://terrain/"))

        self._token_counter = 0
        self._ext_index = 0
        self._ext_by_path = {}          # res_path -> ext id
        self._ext_resources = []        # list of (id, type, res_path)
        self._sub_resources = []        # list of (id, type, [body_lines]) in dependency order
        self._nodes = []                # list of node text blocks (each a list of lines)
        self._child_names = {}          # parent_path -> set(used names)

        # cache of on-disk directory listings for case-insensitive existence checks
        self._dir_cache = {}

    # --- id / token generation ---------------------------------------------------------------
    def _token(self):
        self._token_counter += 1
        n = self._token_counter
        # base36 encode, keep it short and alphanumeric
        digits = "0123456789abcdefghijklmnopqrstuvwxyz"
        s = ""
        while True:
            s = digits[n % 36] + s
            n //= 36
            if n == 0:
                break
        return s.rjust(5, "0")

    def _new_ext_id(self):
        self._ext_index += 1
        return "%d_%s" % (self._ext_index, self._token())

    def _new_sub_id(self, godot_type):
        return "%s_%s" % (godot_type, self._token())

    # --- res:// <-> filesystem ---------------------------------------------------------------
    @staticmethod
    def _norm_res_dir(d):
        d = str(d or "").replace("\\", "/")
        if not d:
            return "res://"
        if not d.endswith("/"):
            d += "/"
        return d

    def _res_to_fs(self, res_path):
        """Map a res:// path to an absolute filesystem path under the Godot project dir."""
        rp = str(res_path).replace("\\", "/")
        if rp.startswith("res://"):
            rp = rp[len("res://"):]
        rp = rp.lstrip("/")
        if not self.project_dir:
            return rp
        return os.path.join(self.project_dir, *[p for p in rp.split("/") if p])

    def _dir_listing_lower(self, folder):
        key = os.path.normcase(os.path.abspath(folder)) if folder else ""
        if key in self._dir_cache:
            return self._dir_cache[key]
        listing = {}
        try:
            if folder and os.path.isdir(folder):
                for name in os.listdir(folder):
                    listing[name.lower()] = name
        except Exception:
            listing = {}
        self._dir_cache[key] = listing
        return listing

    def _resolve_existing_res(self, res_dir, base_name, exts):
        """Return a res:// path for the first existing file <res_dir><base_name><ext>, matching
        case-insensitively on disk. Returns None if nothing exists (or project dir unknown)."""
        res_dir = self._norm_res_dir(res_dir)
        fs_dir = self._res_to_fs(res_dir)
        # If we cannot verify (no project dir), be conservative and treat as missing.
        if not self.project_dir or not os.path.isdir(fs_dir):
            return None
        # 1. Exact case
        for ext in exts:
            candidate = base_name + ext
            if os.path.exists(os.path.join(fs_dir, candidate)):
                return res_dir + candidate
        # 2. Case-insensitive scan
        listing = self._dir_listing_lower(fs_dir)
        for ext in exts:
            want = (base_name + ext).lower()
            if want in listing:
                return res_dir + listing[want]
        return None

    # --- resource registration ---------------------------------------------------------------
    def _register_ext(self, godot_type, res_path):
        existing = self._ext_by_path.get(res_path)
        if existing:
            return existing
        eid = self._new_ext_id()
        self._ext_by_path[res_path] = eid
        self._ext_resources.append((eid, godot_type, res_path))
        return eid

    def _register_sub(self, godot_type, body_lines):
        sid = self._new_sub_id(godot_type)
        self._sub_resources.append((sid, godot_type, list(body_lines)))
        return sid

    # --- node emission -----------------------------------------------------------------------
    def _unique_child_name(self, parent_path, name):
        name = _sanitize_node_name(name)
        used = self._child_names.setdefault(parent_path, set())
        if name not in used:
            used.add(name)
            return name
        i = 2
        while ("%s_%d" % (name, i)) in used:
            i += 1
        final = "%s_%d" % (name, i)
        used.add(final)
        return final

    @staticmethod
    def _child_path(parent_path, name):
        if parent_path is None:
            return "."
        if parent_path == ".":
            return name
        return parent_path + "/" + name

    def _add_node(self, name, godot_type=None, parent_path=".", instance_id=None,
                  props=None, metadata=None):
        """Append a node block. Returns the path used by this node's children as their `parent`.
        `parent_path=None` marks the scene root (no parent attr)."""
        uname = self._unique_child_name(parent_path, name)
        header = '[node name="%s"' % _escape_string(uname)
        if godot_type is not None:
            header += ' type="%s"' % godot_type
        if parent_path is not None:
            header += ' parent="%s"' % _escape_string(parent_path)
        if instance_id is not None:
            header += ' instance=ExtResource("%s")' % instance_id
        header += "]"

        lines = [header]
        for line in (props or []):
            lines.append(line)
        for line in (metadata or []):
            lines.append(line)
        self._nodes.append(lines)
        return self._child_path(parent_path, uname)

    # --- metadata helpers --------------------------------------------------------------------
    @staticmethod
    def _meta_value_literal(value):
        if isinstance(value, bool):
            return "true" if value else "false"
        if isinstance(value, int):
            return str(value)
        if isinstance(value, float):
            return _f(value)
        return '"%s"' % _escape_string(value)

    def _actor_metadata_lines(self, actor):
        lines = []
        cls = actor.get("class")
        if cls:
            lines.append('metadata/unreal_class = "%s"' % _escape_string(cls))
        tags = actor.get("tags")
        if isinstance(tags, (list, tuple)) and tags:
            lines.append("metadata/unreal_tags = [%s]"
                         % ", ".join('"%s"' % _escape_string(t) for t in tags))
        props = actor.get("properties")
        if isinstance(props, dict):
            for k, v in props.items():
                if v is None:
                    continue
                key = _sanitize_node_name(k)
                lines.append("metadata/%s = %s" % (key, self._meta_value_literal(v)))
        return lines

    @staticmethod
    def _component_metadata_lines(comp):
        lines = []
        tags = comp.get("tags")
        if isinstance(tags, (list, tuple)) and tags:
            lines.append("metadata/unreal_tags = [%s]"
                         % ", ".join('"%s"' % _escape_string(t) for t in tags))
        return lines

    # --- collision -> shape sub_resources ----------------------------------------------------
    def _emit_collision(self, parent_path, collision):
        """Emit CollisionShape3D child nodes (+ shape sub_resources) mirroring the GDScript
        importer's setup_physics_body math. `parent_path` is the StaticBody3D."""
        if not isinstance(collision, dict):
            return
        for box in collision.get("boxes") or []:
            size = box.get("size") or [0.0, 0.0, 0.0]
            # Full extents cm -> m with glTF [x, z, y] axis remap (matches importer BoxShape3D.size)
            gx = _num(size[0]) * 0.01 if len(size) > 0 else 0.0
            gy = _num(size[2]) * 0.01 if len(size) > 2 else 0.0
            gz = _num(size[1]) * 0.01 if len(size) > 1 else 0.0
            sid = self._register_sub("BoxShape3D", ["size = " + _vec3_literal([gx, gy, gz])])
            self._add_shape_node(parent_path, "BoxCollision",
                                 box.get("godot_local_transform"), sid)
        for sphere in collision.get("spheres") or []:
            radius = _num(sphere.get("radius")) * 0.01
            sid = self._register_sub("SphereShape3D", ["radius = " + _f(radius)])
            self._add_shape_node(parent_path, "SphereCollision",
                                 sphere.get("godot_local_transform"), sid)
        for cap in collision.get("capsules") or []:
            radius = _num(cap.get("radius")) * 0.01
            length = _num(cap.get("length"))
            height = (length + 2.0 * _num(cap.get("radius"))) * 0.01
            sid = self._register_sub("CapsuleShape3D",
                                     ["radius = " + _f(radius), "height = " + _f(height)])
            self._add_shape_node(parent_path, "CapsuleCollision",
                                 cap.get("godot_local_transform"), sid)
        for convex in collision.get("convex_hulls") or []:
            verts = convex.get("vertices") or []
            if not verts:
                continue
            floats = []
            for v in verts:
                # per-vertex glTF [x, z, y] * 0.01 remap (matches importer ConvexPolygonShape3D)
                vx = _num(v[0]) * 0.01 if len(v) > 0 else 0.0
                vy = _num(v[2]) * 0.01 if len(v) > 2 else 0.0
                vz = _num(v[1]) * 0.01 if len(v) > 1 else 0.0
                floats.extend((vx, vy, vz))
            body = ["points = PackedVector3Array(%s)" % ", ".join(_f(x) for x in floats)]
            sid = self._register_sub("ConvexPolygonShape3D", body)
            self._add_shape_node(parent_path, "ConvexCollision",
                                 convex.get("godot_local_transform"), sid)

    def _add_shape_node(self, parent_path, name, local_transform, shape_id):
        props = []
        if isinstance(local_transform, dict):
            props.append("transform = " + _mat_to_transform3d(_transform_dict_to_mat(local_transform)))
        props.append('shape = SubResource("%s")' % shape_id)
        self._add_node(name, "CollisionShape3D", parent_path, props=props)

    # --- mesh instance node / placeholder ----------------------------------------------------
    def _mesh_res_path(self, mesh_key):
        """Return a verified res:// glTF path for mesh_key, or None if missing on disk."""
        return self._resolve_existing_res(self.models_res, str(mesh_key), _MODEL_EXTS)

    # =========================================================================================
    # Section builders
    # =========================================================================================
    def build(self, scene_name):
        root_name = _sanitize_node_name(scene_name or "Scene")
        root_path = self._add_node(root_name, "Node3D", parent_path=None)

        self._build_actors(root_path)
        if self.options.get("lights"):
            self._build_lights(root_path)
        self._build_environment(root_path)  # post_process/fog/sky are always environment-worthy
        if self.options.get("decals"):
            self._build_decals(root_path)
        if self.options.get("foliage"):
            self._build_foliage(root_path)
        if self.options.get("navigation"):
            self._build_navigation(root_path)
        if self.options.get("landscape"):
            self._build_landscapes(root_path)

    # --- actors ------------------------------------------------------------------------------
    def _build_actors(self, root_path):
        meshes_lib = self.layout.get("meshes") or {}
        emit_meta = bool(self.options.get("metadata"))
        for actor in self.layout.get("actors") or []:
            if not isinstance(actor, dict):
                continue
            components = actor.get("components") or []
            if not components:
                continue
            actor_name = actor.get("name") or "Actor"
            actor_mat = _transform_dict_to_mat(actor.get("godot_transform"))
            actor_meta = self._actor_metadata_lines(actor) if emit_meta else []

            if len(components) == 1:
                self._build_single_component_actor(root_path, actor_name, actor_mat,
                                                   components[0], meshes_lib, actor_meta, emit_meta)
            else:
                self._build_multi_component_actor(root_path, actor_name, actor_mat,
                                                  components, meshes_lib, actor_meta, emit_meta)

    def _component_mesh_key(self, comp):
        return comp.get("mesh_key") or comp.get("mesh_name") or ""

    def _build_single_component_actor(self, root_path, actor_name, actor_mat, comp,
                                      meshes_lib, actor_meta, emit_meta):
        mesh_key = self._component_mesh_key(comp)
        world = _component_world_mat(comp, actor_mat)
        res_path = self._mesh_res_path(mesh_key)

        if res_path is None:
            # Missing mesh -> Marker3D placeholder at the component's world transform
            props = ["transform = " + _mat_to_transform3d(world)]
            meta = ['metadata/missing_mesh = "%s"' % _escape_string(mesh_key)] + actor_meta
            self._add_node("MISSING_" + _sanitize_node_name(mesh_key), "Marker3D",
                           root_path, props=props, metadata=meta)
            _warn("missing mesh '%s' for actor '%s' -> Marker3D placeholder" % (mesh_key, actor_name))
            return

        ext_id = self._register_ext("PackedScene", res_path)
        collision = (meshes_lib.get(mesh_key) or {}).get("collision")

        # Re-seat the glTF mesh from the glTF axis order into the placement
        # convention (see _apply_mesh_axis_fix). Applied at the body, it carries
        # the mesh-local collision shapes along, keeping them hugging the mesh.
        mesh_place = _apply_mesh_axis_fix(world)

        if collision:
            # Collision shapes are mesh-local, so the body carries the component's
            # (axis-corrected) world transform and the mesh instance sits at
            # identity under it.
            body_props = ["transform = " + _mat_to_transform3d(mesh_place)]
            body_path = self._add_node(actor_name, "StaticBody3D", root_path,
                                       props=body_props, metadata=actor_meta)
            self._emit_collision(body_path, collision)
            inst_props = ["transform = " + _mat_to_transform3d(_IDENTITY_MAT)]
            inst_meta = self._component_metadata_lines(comp) if emit_meta else []
            self._add_node(_mesh_node_name(comp, mesh_key), None, body_path,
                           instance_id=ext_id, props=inst_props, metadata=inst_meta)
        else:
            # Direct visual-only instance at the component's world transform
            props = ["transform = " + _mat_to_transform3d(mesh_place)]
            meta = list(actor_meta)
            if emit_meta:
                meta = self._component_metadata_lines(comp) + meta
            self._add_node(actor_name, None, root_path, instance_id=ext_id,
                           props=props, metadata=meta)

    def _build_multi_component_actor(self, root_path, actor_name, actor_mat, components,
                                     meshes_lib, actor_meta, emit_meta):
        parent_props = ["transform = " + _mat_to_transform3d(actor_mat)]
        actor_path = self._add_node(actor_name, "Node3D", root_path,
                                    props=parent_props, metadata=actor_meta)
        # Components are emitted under actor_path, so their world placement has to
        # be re-expressed relative to the actor.
        actor_inverse = _mat_affine_inverse(actor_mat)
        for comp in components:
            mesh_key = self._component_mesh_key(comp)
            comp_name = comp.get("name") or "Component"
            comp_mat = _mat_compose(actor_inverse, _component_world_mat(comp, actor_mat))
            res_path = self._mesh_res_path(mesh_key)
            comp_meta = self._component_metadata_lines(comp) if emit_meta else []

            if res_path is None:
                props = ["transform = " + _mat_to_transform3d(comp_mat)]
                meta = ['metadata/missing_mesh = "%s"' % _escape_string(mesh_key)] + comp_meta
                self._add_node("MISSING_" + _sanitize_node_name(mesh_key), "Marker3D",
                               actor_path, props=props, metadata=meta)
                continue

            ext_id = self._register_ext("PackedScene", res_path)
            collision = (meshes_lib.get(mesh_key) or {}).get("collision")
            # Re-seat the glTF mesh (and its collision) into the placement convention.
            mesh_place = _apply_mesh_axis_fix(comp_mat)
            if collision:
                body_props = ["transform = " + _mat_to_transform3d(mesh_place)]
                body_path = self._add_node(comp_name, "StaticBody3D", actor_path,
                                           props=body_props, metadata=comp_meta)
                self._emit_collision(body_path, collision)
                # mesh instance sits at identity under the body (body already carries comp xform)
                self._add_node(_mesh_node_name(comp, mesh_key), None, body_path,
                               instance_id=ext_id)
            else:
                props = ["transform = " + _mat_to_transform3d(mesh_place)]
                self._add_node(comp_name, None, actor_path, instance_id=ext_id,
                               props=props, metadata=comp_meta)

    # --- lights ------------------------------------------------------------------------------
    def _build_lights(self, root_path):
        for light in self.layout.get("lights") or []:
            if not isinstance(light, dict):
                continue
            ltype = str(light.get("type") or "point").lower()
            name = light.get("name") or "Light"
            mat = _transform_dict_to_mat(light.get("godot_transform"))
            props = ["transform = " + _mat_to_transform3d(mat)]

            color = list(light.get("color") or [1.0, 1.0, 1.0])[:3] or [1.0, 1.0, 1.0]
            while len(color) < 3:
                color.append(1.0)
            if light.get("use_temperature") and light.get("temperature_kelvin") is not None:
                k = _kelvin_to_rgb(light.get("temperature_kelvin"))
                color = [color[i] * k[i] for i in range(3)]
            props.append("light_color = " + _color_literal(color))

            energy = _num(light.get("godot_energy"), 1.0) * self.light_energy_scale
            props.append("light_energy = " + _f(energy))
            props.append("shadow_enabled = %s"
                         % ("true" if light.get("cast_shadows", True) else "false"))

            metadata = []
            if ltype == "directional":
                gtype = "DirectionalLight3D"
            elif ltype == "spot":
                gtype = "SpotLight3D"
                props.append("spot_range = " + _f(_num(light.get("attenuation_radius_m"), 5.0)))
                props.append("spot_angle = " + _f(_num(light.get("outer_cone_angle_deg"), 45.0)))
            elif ltype == "rect":
                gtype = "OmniLight3D"
                props.append("omni_range = " + _f(_num(light.get("attenuation_radius_m"), 5.0)))
                metadata.append("metadata/unreal_rect_light = true")
            else:  # point (and unknown)
                gtype = "OmniLight3D"
                props.append("omni_range = " + _f(_num(light.get("attenuation_radius_m"), 5.0)))

            if light.get("visible") is False:
                props.append("visible = false")

            self._add_node(name, gtype, root_path, props=props, metadata=metadata)

    # --- environment (post_process + height_fog + sky) ---------------------------------------
    def _build_environment(self, root_path):
        post_list = self.layout.get("post_process") or []
        height_fog = self.layout.get("height_fog")
        sky_light = self.layout.get("sky_light")
        has_atmosphere = bool(self.layout.get("has_sky_atmosphere"))

        # Highest-priority unbound post-process volume drives the WorldEnvironment.
        chosen = None
        for pv in post_list:
            if not isinstance(pv, dict):
                continue
            if not pv.get("unbound", False):
                continue
            if chosen is None or _num(pv.get("priority")) > _num(chosen.get("priority")):
                chosen = pv
        settings = (chosen or {}).get("settings") or {}

        want_sky = bool(sky_light) or has_atmosphere
        want_env = bool(chosen) or bool(height_fog) or want_sky
        if not want_env:
            return

        env_lines = []

        sky_id = None
        if want_sky:
            psm_id = self._register_sub("ProceduralSkyMaterial", [
                "sky_horizon_color = Color(0.64625, 0.65575, 0.67075, 1)",
                "ground_horizon_color = Color(0.64625, 0.65575, 0.67075, 1)",
            ])
            sky_id = self._register_sub("Sky", ['sky_material = SubResource("%s")' % psm_id])
            env_lines.append("background_mode = 2")
            env_lines.append('sky = SubResource("%s")' % sky_id)
            env_lines.append("ambient_light_source = 3")
            if isinstance(sky_light, dict):
                if sky_light.get("color") is not None:
                    env_lines.append("ambient_light_color = " + _color_literal(sky_light.get("color")))
                env_lines.append("ambient_light_energy = "
                                 + _f(_clamp(_num(sky_light.get("intensity"), 1.0), 0.0, 16.0)))
        else:
            env_lines.append("background_mode = 1")
            env_lines.append("background_color = Color(0, 0, 0, 1)")

        # Tonemapper: unconditional, because Unreal never renders linear. Mirrors
        # import_environment.gd's UNREAL_TONEMAP (Environment.TONE_MAPPER_ACES == 3).
        env_lines.append("tonemap_mode = %d" % _TONEMAP_ACES)

        # Exposure. Mirrors import_environment.gd::_apply_exposure -- see the
        # constants there for why each branch does what it does.
        cam_attr_id = None
        exposure, cam_attr_lines = _exposure_for_settings(settings)
        if exposure is not None:
            env_lines.append("tonemap_exposure = " + _f(exposure))
        if cam_attr_lines:
            cam_attr_id = self._register_sub("CameraAttributesPractical", cam_attr_lines)

        # Glow (bloom)
        if settings.get("bloom_intensity") is not None:
            env_lines.append("glow_enabled = true")
            env_lines.append("glow_intensity = " + _f(_num(settings.get("bloom_intensity"), 0.8)))
            bt = settings.get("bloom_threshold")
            if bt is not None and _num(bt) >= 0.0:
                env_lines.append("glow_hdr_threshold = " + _f(_num(bt)))

        # SSAO. The *2.0 and the 0.01 m floor match import_environment.gd; this
        # path used to write the raw UE intensity, so the same level came out
        # half as occluded through the .tscn exporter as through the addon.
        if settings.get("ao_intensity") is not None:
            env_lines.append("ssao_enabled = true")
            env_lines.append("ssao_intensity = "
                             + _f(_clamp(_num(settings.get("ao_intensity"), 0.5) * 2.0, 0.0, 16.0)))
            if settings.get("ao_radius") is not None:
                env_lines.append("ssao_radius = "
                                 + _f(max(0.01, _num(settings.get("ao_radius"), 200.0) * 0.01)))

        # Color adjustments (saturation / contrast are stored as [r,g,b,a]; Godot wants scalars)
        sat = settings.get("saturation")
        con = settings.get("contrast")
        if sat is not None or con is not None:
            env_lines.append("adjustment_enabled = true")
            if isinstance(sat, (list, tuple)) and sat:
                env_lines.append("adjustment_saturation = " + _f(_avg3(sat)))
            if isinstance(con, (list, tuple)) and con:
                env_lines.append("adjustment_contrast = " + _f(_avg3(con)))

        # Height fog -> Godot depth fog. The heuristic is `fog_density * 0.5`, so
        # UE's default 0.02 lands on Godot's default 0.01 (SCHEMA_V2.md). This
        # path had *5.0, making every .tscn export TEN TIMES foggier than the
        # same layout imported through the addon.
        if isinstance(height_fog, dict):
            env_lines.append("fog_enabled = true")
            env_lines.append("fog_density = "
                             + _f(_clamp(_num(height_fog.get("fog_density"), 0.02) * 0.5, 0.0, 1.0)))
            if height_fog.get("color") is not None:
                env_lines.append("fog_light_color = " + _color_literal(height_fog.get("color")))
            env_lines.append("fog_height_density = "
                             + _f(_clamp(_num(height_fog.get("fog_height_falloff"), 0.2), 0.0, 1.0)))

        env_id = self._register_sub("Environment", env_lines)
        env_props = ['environment = SubResource("%s")' % env_id]
        if cam_attr_id is not None:
            env_props.append('camera_attributes = SubResource("%s")' % cam_attr_id)
        self._add_node("WorldEnvironment", "WorldEnvironment", root_path, props=env_props)

    # --- decals ------------------------------------------------------------------------------
    def _build_decals(self, root_path):
        for decal in self.layout.get("decals") or []:
            if not isinstance(decal, dict):
                continue
            name = decal.get("name") or "Decal"
            mat = _transform_dict_to_mat(decal.get("godot_transform"))
            props = ["transform = " + _mat_to_transform3d(mat)]
            size = decal.get("size_m")
            if isinstance(size, (list, tuple)) and size:
                props.append("size = " + _vec3_literal(size))
            if decal.get("sort_order") is not None:
                props.append("sorting_offset = " + _f(_num(decal.get("sort_order"))))

            textures = decal.get("textures") or {}
            for tex_key, godot_prop in (("albedo", "texture_albedo"), ("normal", "texture_normal"),
                                        ("orm", "texture_orm"), ("emission", "texture_emission")):
                tex_name = textures.get(tex_key)
                if not tex_name:
                    continue
                res_path = self._resolve_existing_res(self.textures_res, str(tex_name), _TEXTURE_EXTS)
                if res_path is None:
                    _warn("decal '%s': texture '%s' missing on disk -> property omitted"
                          % (name, tex_name))
                    continue
                tid = self._register_ext("Texture2D", res_path)
                props.append('%s = ExtResource("%s")' % (godot_prop, tid))

            self._add_node(name, "Decal", root_path, props=props)

    # --- foliage -----------------------------------------------------------------------------
    def _build_foliage(self, root_path):
        for entry in self.layout.get("foliage") or []:
            if not isinstance(entry, dict):
                continue
            name = entry.get("name") or "Foliage"
            mesh_key = entry.get("mesh_key") or entry.get("mesh_name") or ""
            flat = entry.get("godot_transforms") or []
            count = len(flat) // 12
            if entry.get("instance_count") is not None:
                count = int(entry.get("instance_count"))
                count = min(count, len(flat) // 12)

            buffer_vals = []
            for i in range(count):
                base = i * 12
                f = flat[base:base + 12]
                if len(f) < 12:
                    break
                # schema stores basis COLUMNS then origin; MultiMesh wants 3x4 ROW-major.
                # Post-multiply the basis by the glTF axis correction (_MESH_AXIS_FIX)
                # so foliage meshes match the layout meshes: local X -> -Z, Y -> Y,
                # Z -> X means new column X = -old column Z, new Y = old Y, new Z = old X.
                bxx, bxy, bxz = -f[6], -f[7], -f[8]   # column X <- -column Z
                byx, byy, byz = f[3], f[4], f[5]      # column Y (unchanged)
                bzx, bzy, bzz = f[0], f[1], f[2]      # column Z <- column X
                ox, oy, oz = f[9], f[10], f[11]
                buffer_vals.extend((
                    bxx, byx, bzx, ox,   # row 0 + origin.x
                    bxy, byy, bzy, oy,   # row 1 + origin.y
                    bxz, byz, bzz, oz,   # row 2 + origin.z
                ))
            actual_count = len(buffer_vals) // 12

            mm_lines = [
                "transform_format = 1",
                "instance_count = %d" % actual_count,
                "visible_instance_count = -1",
                "buffer = PackedFloat32Array(%s)" % ", ".join(_f(v) for v in buffer_vals),
            ]
            mm_id = self._register_sub("MultiMesh", mm_lines)

            props = ['multimesh = SubResource("%s")' % mm_id]
            metadata = ["metadata/unreal_foliage = true"]
            # Point the dock's "bind foliage meshes" pass at the source glTF (verified if present).
            res_path = self._mesh_res_path(mesh_key)
            if res_path is None:
                res_path = self.models_res + str(mesh_key) + ".gltf"  # best-effort hint
            metadata.append('metadata/source_model = "%s"' % _escape_string(res_path))
            metadata.append('metadata/unreal_mesh_key = "%s"' % _escape_string(mesh_key))
            self._add_node(name, "MultiMeshInstance3D", root_path,
                           props=props, metadata=metadata)

    # --- navigation --------------------------------------------------------------------------
    def _build_navigation(self, root_path):
        nav = self.layout.get("navigation")
        if not isinstance(nav, dict):
            return
        agent_radius = _num(nav.get("agent_radius_m"), 0.5)
        agent_height = _num(nav.get("agent_height_m"), 1.8)
        agent_climb = _num(nav.get("agent_max_step_height_m"), 0.25)
        agent_slope = _num(nav.get("max_slope_deg"), 45.0)
        cell_size = _num(nav.get("cell_size_m"), 0.25)

        volumes = nav.get("bounds_volumes") or []
        if not volumes:
            volumes = [{"name": "NavRegion", "godot_transform": None, "extent_m": [10.0, 10.0, 10.0]}]

        for idx, vol in enumerate(volumes):
            if not isinstance(vol, dict):
                continue
            extent = vol.get("extent_m") or [10.0, 10.0, 10.0]
            ex = _num(extent[0], 10.0) if len(extent) > 0 else 10.0
            ey = _num(extent[1], 10.0) if len(extent) > 1 else 10.0
            ez = _num(extent[2], 10.0) if len(extent) > 2 else 10.0
            aabb = "AABB(%s)" % ", ".join(_f(v) for v in
                                          (-ex, -ey, -ez, 2.0 * ex, 2.0 * ey, 2.0 * ez))
            nm_lines = [
                "agent_radius = " + _f(agent_radius),
                "agent_height = " + _f(agent_height),
                "agent_max_climb = " + _f(agent_climb),
                "agent_max_slope = " + _f(agent_slope),
                "cell_size = " + _f(cell_size),
                "filter_baking_aabb = " + aabb,
            ]
            nm_id = self._register_sub("NavigationMesh", nm_lines)
            props = ['navigation_mesh = SubResource("%s")' % nm_id]
            gt = vol.get("godot_transform")
            if isinstance(gt, dict):
                props.insert(0, "transform = " + _mat_to_transform3d(_transform_dict_to_mat(gt)))
            self._add_node(vol.get("name") or ("NavRegion_%d" % idx),
                           "NavigationRegion3D", root_path, props=props)

    # --- landscapes --------------------------------------------------------------------------
    def _build_landscapes(self, root_path):
        for lscape in self.layout.get("landscapes") or []:
            if not isinstance(lscape, dict):
                continue
            name = lscape.get("name") or "Landscape"
            props = []
            gt = lscape.get("godot_transform")
            if isinstance(gt, dict):
                props.append("transform = " + _mat_to_transform3d(_transform_dict_to_mat(gt)))

            metadata = ["metadata/unreal_landscape = true"]
            hm = lscape.get("heightmap_file")
            if hm:
                heightmap_res = self.terrain_res + os.path.basename(str(hm).replace("\\", "/"))
                metadata.append('metadata/heightmap = "%s"' % _escape_string(heightmap_res))
            ws = lscape.get("world_size_m")
            if isinstance(ws, (list, tuple)) and ws:
                metadata.append("metadata/world_size_m = " + _vec2_literal(ws))
            hr = lscape.get("height_range_m")
            if isinstance(hr, (list, tuple)) and hr:
                metadata.append("metadata/height_range_m = " + _vec2_literal(hr))
            enc = lscape.get("height_encoding")
            if enc:
                metadata.append('metadata/height_encoding = "%s"' % _escape_string(enc))
            layers = lscape.get("layers")
            if isinstance(layers, (list, tuple)) and layers:
                layer_names = [l.get("name", "") if isinstance(l, dict) else str(l) for l in layers]
                metadata.append("metadata/layers = [%s]"
                                % ", ".join('"%s"' % _escape_string(n) for n in layer_names))

            self._add_node(name, "Node3D", root_path, props=props, metadata=metadata)

    # =========================================================================================
    # Serialization
    # =========================================================================================
    def serialize(self):
        load_steps = 1 + len(self._ext_resources) + len(self._sub_resources)
        out = []
        out.append("[gd_scene load_steps=%d format=3]" % load_steps)
        out.append("")

        for eid, gtype, res_path in self._ext_resources:
            out.append('[ext_resource type="%s" path="%s" id="%s"]'
                       % (gtype, _escape_string(res_path), eid))
        if self._ext_resources:
            out.append("")

        for sid, gtype, body in self._sub_resources:
            out.append('[sub_resource type="%s" id="%s"]' % (gtype, sid))
            out.extend(body)
            out.append("")

        for node_lines in self._nodes:
            out.extend(node_lines)
            out.append("")

        text = "\n".join(out).rstrip("\n") + "\n"
        return text


def _avg3(arr):
    vals = [_num(x) for x in arr[:3]] if isinstance(arr, (list, tuple)) else []
    if not vals:
        return 1.0
    return sum(vals) / len(vals)


def _mesh_node_name(comp, mesh_key):
    """Name for the instanced-mesh child node under a StaticBody3D."""
    return _sanitize_node_name(mesh_key or comp.get("name") or "Mesh")


def write_tscn(layout_data, output_path, res_paths, options):
    """Write a Godot 4 .tscn scene from a schema-v2 layout dict. Never raises; returns True/False."""
    try:
        options = options or {}
        scene_name = options.get("scene_name") or (layout_data or {}).get("level_name") or "ImportedLevel"

        writer = _TscnWriter(layout_data, res_paths, options)
        writer.build(scene_name)
        text = writer.serialize()

        out_dir = os.path.dirname(output_path)
        if out_dir and not os.path.isdir(out_dir):
            os.makedirs(out_dir, exist_ok=True)
        with open(output_path, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(text)

        _log("wrote scene '%s' (%d ext, %d sub, %d nodes) -> %s"
             % (scene_name, len(writer._ext_resources), len(writer._sub_resources),
                len(writer._nodes), output_path))
        return True
    except Exception as exc:  # never raise out of the entry point
        _err("write_tscn failed: %s" % exc)
        try:
            import traceback
            _err(traceback.format_exc())
        except Exception:
            pass
        return False
