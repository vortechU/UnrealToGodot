"""
Numerically verifies the core coordinate math in ue2g_common.py by stubbing the
`unreal` module. Tests the claims the whole pipeline rests on:
  1. translation/scale conversion
  2. R_godot == C * R_unreal * C^T  (the central rotation claim)
  3. godot_transform_basis (packed 12-float foliage layout) agrees with
     unreal_to_godot_transform (quat+scale) -- catches row/column packing bugs
  4. matrix_to_quat round-trip
  5. the decal fix-up quaternion actually aligns Godot -Y with UE -X
  6. build_export_name_map collision handling
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

print("\n" + "=" * 60)
if FAIL:
    print("FAILURES (%d): %s" % (len(FAIL), FAIL))
    sys.exit(1)
print("ALL MATH CHECKS PASSED")
