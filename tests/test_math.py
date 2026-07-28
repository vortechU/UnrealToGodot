"""
Numerically verifies the core coordinate math in ue2g_common.py by stubbing the
`unreal` module. Tests the claims the whole pipeline rests on:
  1. translation/scale conversion
  2. R_godot == C * R_unreal * C^T  (the central rotation claim)
  3. godot_transform_basis (packed 12-float foliage layout) agrees with
     unreal_to_godot_transform (quat+scale) -- catches row/column packing bugs
  4. matrix_to_quat round-trip
  5. the decal fix-up quaternion actually aligns Godot -Y with UE -X, and
     carries the scale with it so the decal box keeps its UE dimensions
  6. build_export_name_map collision handling
  7. the schema's `scale` is a LOCAL scale: consumers must scale basis columns
"""
import sys, os, math, types, random

# ---------------------------------------------------------------- unreal stub
u = types.ModuleType("unreal")


class Vector:
    def __init__(self, x=0.0, y=0.0, z=0.0):
        self.x, self.y, self.z = float(x), float(y), float(z)


class Rotator:
    def __init__(self, roll=0.0, pitch=0.0, yaw=0.0):
        self.roll, self.pitch, self.yaw = roll, pitch, yaw


class Quat:
    def __init__(self, x=0.0, y=0.0, z=0.0, w=1.0):
        self.x, self.y, self.z, self.w = float(x), float(y), float(z), float(w)

    def rotator(self):
        return Rotator()


class Transform:
    def __init__(self, translation, rotation, scale3d):
        self.translation, self.rotation, self.scale3d = translation, rotation, scale3d


class LinearColor:
    def __init__(self, r=0.0, g=0.0, b=0.0, a=1.0):
        self.r, self.g, self.b, self.a = r, g, b, a


class Color:
    def __init__(self, r=0, g=0, b=0, a=255):
        self.r, self.g, self.b, self.a = r, g, b, a


u.Vector, u.Quat, u.Transform, u.Rotator = Vector, Quat, Transform, Rotator
u.LinearColor, u.Color = LinearColor, Color
u.log = lambda *a: None
u.log_warning = lambda *a: None
u.log_error = lambda *a: None
sys.modules["unreal"] = u

import os, sys
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                                "UnrealToGodot", "Content", "Python"))
import ue2g_common as C

FAIL = []


def check(name, cond, detail=""):
    if cond:
        print("  PASS  %s" % name)
    else:
        print("  FAIL  %s  %s" % (name, detail))
        FAIL.append(name)


def close(a, b, eps=1e-6):
    return abs(a - b) < eps


def mclose(A, B, eps=1e-6):
    return all(close(A[r][c], B[r][c], eps) for r in range(3) for c in range(3))


def quat_to_mat(q):
    """(x,y,z,w) -> row-major 3x3. Same convention as ue2g_common."""
    qx, qy, qz, qw = q
    return [
        [1 - 2 * (qy * qy + qz * qz), 2 * (qx * qy - qw * qz), 2 * (qx * qz + qw * qy)],
        [2 * (qx * qy + qw * qz), 1 - 2 * (qx * qx + qz * qz), 2 * (qy * qz - qw * qx)],
        [2 * (qx * qz - qw * qy), 2 * (qy * qz + qw * qx), 1 - 2 * (qx * qx + qy * qy)],
    ]


def matmul(A, B):
    return [[sum(A[i][k] * B[k][j] for k in range(3)) for j in range(3)] for i in range(3)]


def transpose(A):
    return [[A[j][i] for j in range(3)] for i in range(3)]


def matvec(A, v):
    return [sum(A[i][k] * v[k] for k in range(3)) for i in range(3)]


def rand_quat(rng):
    while True:
        q = [rng.uniform(-1, 1) for _ in range(4)]
        n = math.sqrt(sum(c * c for c in q))
        if n > 1e-3:
            return tuple(c / n for c in q)


# The change-of-basis matrix C: C * (ux,uy,uz) = (uy, uz, -ux)
CB = [[0.0, 1.0, 0.0],
      [0.0, 0.0, 1.0],
      [-1.0, 0.0, 0.0]]

print("\n=== 1. Translation / scale conversion ===")
t = Transform(Vector(100, 200, 300), Quat(0, 0, 0, 1), Vector(1, 2, 3))
g = C.unreal_to_godot_transform(t)
check("UE (100,200,300)cm -> Godot (2,3,-1)m",
      [round(v, 9) for v in g["translation"]] == [2.0, 3.0, -1.0], g["translation"])
check("scale (1,2,3) -> (2,3,1)", g["scale"] == [2.0, 3.0, 1.0], g["scale"])
check("identity rotation stays identity",
      mclose(quat_to_mat(g["rotation_quat"]), [[1, 0, 0], [0, 1, 0], [0, 0, 1]]))

print("\n=== 2. R_godot == C * R_unreal * C^T (200 random rotations) ===")
rng = random.Random(1234)
bad = 0
for _ in range(200):
    q = rand_quat(rng)
    tr = Transform(Vector(0, 0, 0), Quat(*q), Vector(1, 1, 1))
    out = C.unreal_to_godot_transform(tr)
    expected = matmul(matmul(CB, quat_to_mat(q)), transpose(CB))
    actual = quat_to_mat(out["rotation_quat"])
    if not mclose(expected, actual, 1e-5):
        bad += 1
check("all 200 rotations satisfy C*R*C^T", bad == 0, "%d mismatches" % bad)

print("\n=== 3. UE forward +X maps to Godot -Z ===")
# For identity UE rotation, a UE local +X direction must land on Godot -Z.
check("C * (1,0,0) == (0,0,-1)", matvec(CB, [1, 0, 0]) == [0.0, 0.0, -1.0])
check("C * (-1,0,0) == (0,0,1)  (decal projection dir)",
      matvec(CB, [-1, 0, 0]) == [0.0, 0.0, 1.0])

print("\n=== 4. godot_transform_basis agrees with unreal_to_godot_transform ===")
bad = 0
for _ in range(200):
    q = rand_quat(rng)
    sc = [rng.uniform(0.2, 3.0) for _ in range(3)]
    pos = [rng.uniform(-5000, 5000) for _ in range(3)]
    tr = Transform(Vector(*pos), Quat(*q), Vector(*sc))
    packed = C.godot_transform_basis(tr)
    ref = C.unreal_to_godot_transform(tr)
    # Reconstruct M = R(quat) * diag(scale) from the dict form
    R = quat_to_mat(ref["rotation_quat"])
    S = ref["scale"]
    M_ref = [[R[r][c] * S[c] for c in range(3)] for r in range(3)]
    # packed is [col0, col1, col2, origin] -> M_packed[r][c] = packed[c*3 + r]
    M_pk = [[packed[c * 3 + r] for c in range(3)] for r in range(3)]
    if not mclose(M_ref, M_pk, 1e-5):
        bad += 1
        continue
    if not all(close(packed[9 + i], ref["translation"][i], 1e-6) for i in range(3)):
        bad += 1
check("packed 12-float basis == quat*scale basis (200 cases)", bad == 0,
      "%d mismatches" % bad)

print("\n=== 5. matrix_to_quat round-trip ===")
bad = 0
for _ in range(300):
    q = rand_quat(rng)
    M = quat_to_mat(q)
    q2 = C.matrix_to_quat(M)
    # q and -q represent the same rotation; compare matrices instead
    if not mclose(M, quat_to_mat(q2), 1e-5):
        bad += 1
check("300 random matrices round-trip", bad == 0, "%d mismatches" % bad)

# Degenerate / edge rotations exercise every branch of matrix_to_quat
edge = [
    ("identity", (0, 0, 0, 1)),
    ("180 about X", (1, 0, 0, 0)),
    ("180 about Y", (0, 1, 0, 0)),
    ("180 about Z", (0, 0, 1, 0)),
    ("90 about X", (math.sin(math.pi / 4), 0, 0, math.cos(math.pi / 4))),
    ("90 about Y", (0, math.sin(math.pi / 4), 0, math.cos(math.pi / 4))),
    ("90 about Z", (0, 0, math.sin(math.pi / 4), math.cos(math.pi / 4))),
]
for name, q in edge:
    M = quat_to_mat(q)
    check("edge rotation: %s" % name, mclose(M, quat_to_mat(C.matrix_to_quat(M)), 1e-5))

print("\n=== 6. Decal fix-up quaternion ===")
FIX = (-0.7071067811865476, 0.0, 0.0, 0.7071067811865476)


def qmul(q1, q2):
    x1, y1, z1, w1 = q1
    x2, y2, z2, w2 = q2
    return (w1 * x2 + x1 * w2 + y1 * z2 - z1 * y2,
            w1 * y2 - x1 * z2 + y1 * w2 + z1 * x2,
            w1 * z2 + x1 * y2 - y1 * x2 + z1 * w2,
            w1 * w2 - x1 * x2 - y1 * y2 - z1 * z2)


check("R_fix * (0,1,0) == (0,0,-1)",
      all(close(a, b, 1e-9) for a, b in zip(matvec(quat_to_mat(FIX), [0, 1, 0]), [0, 0, -1])))
# The real requirement: R_final * (0,-1,0) == R_std * (0,0,1) for ANY actor rotation
bad = 0
for _ in range(200):
    q = rand_quat(rng)
    tr = Transform(Vector(0, 0, 0), Quat(*q), Vector(1, 1, 1))
    std = C.unreal_to_godot_transform(tr)
    q_std = tuple(std["rotation_quat"])
    q_final = qmul(q_std, FIX)
    lhs = matvec(quat_to_mat(q_final), [0, -1, 0])   # Godot Decal projects along -Y
    rhs = matvec(quat_to_mat(q_std), [0, 0, 1])      # where UE -X actually landed
    if not all(close(a, b, 1e-5) for a, b in zip(lhs, rhs)):
        bad += 1
check("Godot Decal -Y aligns with UE -X for 200 rotations", bad == 0,
      "%d mismatches" % bad)

print("\n=== 7. build_export_name_map collision handling ===")


class FakeMesh:
    def __init__(self, name, path):
        self._n, self._p = name, path

    def get_name(self):
        return self._n

    def get_path_name(self):
        return self._p


a = FakeMesh("SM_Rock", "/Game/A/SM_Rock.SM_Rock")
b = FakeMesh("SM_Rock", "/Game/B/SM_Rock.SM_Rock")
c = FakeMesh("SM_Tree", "/Game/A/SM_Tree.SM_Tree")
m = C.build_export_name_map([a, b, c])
check("unique name kept as-is", m[c] == "SM_Tree", m[c])
check("colliding names BOTH get a hash suffix",
      m[a] != "SM_Rock" and m[b] != "SM_Rock" and m[a] != m[b], (m[a], m[b]))
check("suffix is deterministic",
      C.build_export_name_map([a, b, c])[a] == m[a])
check("order-independent",
      C.build_export_name_map([b, a, c])[a] == m[a])
check("sanitize_name strips unsafe chars",
      C.sanitize_name("SM Rock/01:x") == "SM_Rock_01_x", C.sanitize_name("SM Rock/01:x"))

print("\n=== 8. Collision uses the glTF axis convention (hugs the mesh) ===")
# Collision shapes ride under the glTF mesh, so they must convert with the glTF
# axis order (Godot = ux, uz, uy) -- NOT the level-layout order (uy, uz, -ux),
# which lands them rotated 90 deg about vertical and displaced. Regression guard
# for the container-collision misalignment.

def gltf_point(p):
    """How Unreal's glTF exporter maps a mesh-local UE point into Godot (x0.01)."""
    return [p[0] * 0.01, p[2] * 0.01, p[1] * 0.01]

# translation follows the glTF axis order
gt = C.gltf_local_shape_transform(Vector(100.0, 200.0, 300.0), Quat(0, 0, 0, 1))
check("gltf shape translation == (ux, uz, uy) * 0.01",
      all(close(a, b) for a, b in zip(gt["translation"], [1.0, 3.0, 2.0])),
      gt["translation"])
check("gltf shape identity rotation stays identity",
      all(close(a, b) for a, b in zip(gt["rotation_quat"], [0.0, 0.0, 0.0, 1.0])),
      gt["rotation_quat"])
check("gltf_collision_axis_swap([x,y,z]) == [x,z,y]",
      C.gltf_collision_axis_swap([7.0, 8.0, 9.0]) == [7.0, 9.0, 8.0],
      C.gltf_collision_axis_swap([7.0, 8.0, 9.0]))

# The money check: every corner of a box collider, taken through the collision
# export+import path, must land exactly where the glTF mesh convention puts the
# same UE point -- for arbitrary centre, rotation and extents.
worst = 0.0
for _ in range(300):
    cx, cy, cz = (rng.uniform(-500, 500) for _ in range(3))
    hx, hy, hz = (rng.uniform(1, 300) for _ in range(3))
    q = rand_quat(rng)
    Rm = quat_to_mat(q)

    # collision path: local transform (glTF conv) + rotation of the box shape
    gt = C.gltf_local_shape_transform(Vector(cx, cy, cz), Quat(*q))
    T = gt["translation"]
    Rg = quat_to_mat(tuple(gt["rotation_quat"]))

    centre = [cx, cy, cz]
    for sx in (-1, 1):
        for sy in (-1, 1):
            for sz in (-1, 1):
                s = [sx * hx, sy * hy, sz * hz]
                # mesh side: UE corner = centre + R*s, then glTF-converted
                ue_corner = [centre[i] + matvec(Rm, s)[i] for i in range(3)]
                mesh = gltf_point(ue_corner)
                # collision side: the same UE half-extent vector, axis-swapped
                # into the box's Godot-local frame (x0.01), rotated + offset.
                sl = [v * 0.01 for v in C.gltf_collision_axis_swap(s)]
                col = [T[i] + matvec(Rg, sl)[i] for i in range(3)]
                worst = max(worst, max(abs(mesh[i] - col[i]) for i in range(3)))
check("box collider corners match the glTF mesh convention (300 random boxes)",
      worst < 1e-6, "worst corner error = %.3e m" % worst)

print("\n=== 9. Placement axis fix re-seats the glTF mesh (scattered-building bug) ===")
# The glTF mesh geometry is baked in the (X,Z,Y) order (gltf_point), but actors are
# PLACED with the (Y,Z,-X) layout convention (unreal_to_godot_transform). Those two
# right-handed maps differ by a +90 deg yaw about up. Placing a mesh at its layout
# transform therefore yaws it about its OWN pivot -- invisible on centre-pivoted
# props, but it flings off-centre modular pieces metres away. The importer folds a
# fix (Common.gltf_mesh_placement / tscn_writer._MESH_AXIS_FIX) into every mesh
# placement. This guards that fix constant and proves it actually re-aligns geometry.
L_FIX = [[0.0, 0.0, 1.0], [0.0, 1.0, 0.0], [-1.0, 0.0, 0.0]]   # Ry(+90 deg)
B_MAP = [[1.0, 0.0, 0.0], [0.0, 0.0, 1.0], [0.0, 1.0, 0.0]]    # glTF axis map (x,z,y)
check("fix constant == A * B (layout map composed with glTF map)",
      mclose(matmul(CB, B_MAP), L_FIX))

worst = 0.0
for _ in range(300):
    q = rand_quat(rng)
    Ru = quat_to_mat(q)
    tu = [rng.uniform(-3000, 3000) for _ in range(3)]   # actor world pos, cm
    v = [rng.uniform(-400, 400) for _ in range(3)]       # mesh-local vertex, cm
    tr = Transform(Vector(*tu), Quat(*q), Vector(1, 1, 1))
    g = C.unreal_to_godot_transform(tr)
    layout_basis = quat_to_mat(g["rotation_quat"])
    fixed_basis = matmul(layout_basis, L_FIX)
    mesh_local = gltf_point(v)                            # how the mesh vertex lives in Godot
    fixed = [matvec(fixed_basis, mesh_local)[i] + g["translation"][i] for i in range(3)]
    # ground truth: UE world point taken through the layout convention (x0.01)
    ue_world = [sum(Ru[i][k] * v[k] for k in range(3)) + tu[i] for i in range(3)]
    correct = [c * 0.01 for c in matvec(CB, ue_world)]
    worst = max(worst, max(abs(fixed[i] - correct[i]) for i in range(3)))
check("fixed placement matches layout-correct world position (300 cases)",
      worst < 1e-6, "worst = %.3e m" % worst)

print("\n=== 10. Schema scale is a LOCAL scale (column scaling, not row) ===")
# Consumers rebuild a basis from (rotation_quat, scale). The only composition that
# reproduces the true converted basis C * (R_ue * S_ue) * C^T is R * diag(scale)
# -- i.e. scale each basis COLUMN (Godot's Basis.scaled_local / tscn_writer's
# _transform_dict_to_mat). Godot's Basis.scaled() scales ROWS instead, applying
# the scale in the PARENT frame; rotated actors with a non-uniform scale then come
# out stretched along the wrong world axes. Guard for that mix-up.
worst_col = worst_row = 0.0
for _ in range(300):
    q = rand_quat(rng)
    sc = [rng.choice([-1, 1]) * rng.uniform(0.2, 4.0) for _ in range(3)]
    tr = Transform(Vector(0, 0, 0), Quat(*q), Vector(*sc))
    g = C.unreal_to_godot_transform(tr)
    R = quat_to_mat(g["rotation_quat"])
    s = g["scale"]
    # truth: C * (R_ue * diag(scale_ue)) * C^T
    Ru = quat_to_mat(q)
    RS = [[Ru[i][j] * sc[j] for j in range(3)] for i in range(3)]
    truth = matmul(matmul(CB, RS), transpose(CB))
    by_col = [[R[i][j] * s[j] for j in range(3)] for i in range(3)]
    by_row = [[R[i][j] * s[i] for j in range(3)] for i in range(3)]
    worst_col = max(worst_col, max(abs(by_col[i][j] - truth[i][j]) for i in range(3) for j in range(3)))
    worst_row = max(worst_row, max(abs(by_row[i][j] - truth[i][j]) for i in range(3) for j in range(3)))
check("column scaling (R * S) reproduces C*(R_ue*S_ue)*C^T (300 cases)",
      worst_col < 1e-6, "worst = %.3e" % worst_col)
check("row scaling (S * R) does NOT -- the mix-up is observable",
      worst_row > 0.1, "worst = %.3e" % worst_row)

print("\n=== 11. Decal box: the fix-up rotation must carry the scale with it ===")
# _decal_transform folds Rx(-90) into the rotation so Godot's -Y projection axis
# lines up with UE's -X. That re-labels the node's local Y and Z, so the scale has
# to be conjugated by the same rotation (its Y and Z swap). Forgetting that swaps
# a decal's projection depth with its height. End-to-end check: reproduce the box
# Godot renders (0.5 * size[j] * basis column j) and compare it to the UE decal box.
import export_environment as EE


class FakeDecalActor:
    def __init__(self, tr):
        self._tr = tr

    def get_actor_transform(self):
        return self._tr


# UE default decal_size, half-extents cm: (X=projection depth, Y=width, Z=height)
DS = (128.0, 256.0, 256.0)
SIZE_M = [DS[1] * 2 * 0.01, DS[0] * 2 * 0.01, DS[2] * 2 * 0.01]   # what _build_decal_entry emits

worst = 0.0
for _ in range(300):
    q = rand_quat(rng)
    sc = [rng.choice([-1, 1]) * rng.uniform(0.1, 3.0) for _ in range(3)]
    d = EE._decal_transform(FakeDecalActor(Transform(Vector(0, 0, 0), Quat(*q), Vector(*sc))))
    basis = quat_to_mat(tuple(d["rotation_quat"]))
    s = d["scale"]
    basis = [[basis[i][j] * s[j] for j in range(3)] for i in range(3)]   # column scaling

    # Godot renders half-extent along local axis j as 0.5 * size[j] * column j.
    got = {
        "width":  [basis[i][0] * 0.5 * SIZE_M[0] for i in range(3)],
        "depth":  [basis[i][1] * 0.5 * SIZE_M[1] for i in range(3)],
        "height": [basis[i][2] * 0.5 * SIZE_M[2] for i in range(3)],
    }
    # Truth: UE half-extent vectors DS[i]*scale[i] along the actor's local axes,
    # converted with the layout map. UE local X=depth, Y=width, Z=height.
    Ru = quat_to_mat(q)
    want = {}
    for name, axis in (("depth", 0), ("width", 1), ("height", 2)):
        ue_vec = [Ru[i][axis] * DS[axis] * sc[axis] * 0.01 for i in range(3)]
        want[name] = matvec(CB, ue_vec)
    for name in want:
        worst = max(worst, max(abs(got[name][i] - want[name][i]) for i in range(3)))
check("decal box matches the UE decal box under rotation + mirrored scale (300 cases)",
      worst < 1e-6, "worst half-extent error = %.3e m" % worst)

print("\n" + "=" * 60)
if FAIL:
    print("FAILURES (%d): %s" % (len(FAIL), FAIL))
    sys.exit(1)
print("ALL MATH CHECKS PASSED")
