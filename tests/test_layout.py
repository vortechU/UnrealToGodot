"""
Reproduces the double-transform layout bug and verifies the fix.

Models what Unreal actually reports for a plain StaticMeshActor:
  actor.get_actor_transform()      -> world transform W
  rootcomponent.get_relative_transform() -> ALSO W (a root component has no
                                             parent component, so its relative
                                             transform IS its world transform)
Composing actor * relative therefore yields W*W. That is the bug.
"""
import sys, os, math, random

import os, sys
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                                "UnrealToGodot", "Content", "Python"))
import tscn_writer as T

FAIL = []


def check(name, cond, detail=""):
    print(("  PASS  " if cond else "  FAIL  ") + name + ("  " + str(detail) if not cond else ""))
    if not cond:
        FAIL.append(name)


def mat_close(a, b, eps=1e-9):
    ra, oa = a
    rb, ob = b
    return (all(abs(ra[i][j] - rb[i][j]) < eps for i in range(3) for j in range(3))
            and all(abs(oa[i] - ob[i]) < eps for i in range(3)))


def tdict(translation, quat=(0, 0, 0, 1), scale=(1, 1, 1)):
    return {"translation": list(translation), "rotation_quat": list(quat), "scale": list(scale)}


print("\n=== 1. Reproduce the bug: plain StaticMeshActor ===")
# Buoy sitting at Godot (20, 3, -50). UE reports the SAME transform for the actor
# and for its root StaticMeshComponent.
world = tdict((20.0, 3.0, -50.0))
actor_mat = T._transform_dict_to_mat(world)
comp_rel_mat = T._transform_dict_to_mat(world)  # what UE reports for a root component

old_result = T._mat_compose(actor_mat, comp_rel_mat)  # the pre-fix code path
check("OLD code doubles the position (this is the reported bug)",
      [round(v, 6) for v in old_result[1]] == [40.0, 6.0, -100.0], old_result[1])

comp = {"godot_world_transform": world, "godot_relative_transform": world}
new_result = T._component_world_mat(comp, actor_mat)
check("NEW code places it at the true world position",
      [round(v, 6) for v in new_result[1]] == [20.0, 3.0, -50.0], new_result[1])

print("\n=== 2. Nested Blueprint component (2 levels deep) ===")
# Actor root at (10,0,0); child pier at +(5,0,0); plank nested under pier at +(1,0,0).
# UE reports the plank's RELATIVE transform against the PIER, i.e. (1,0,0) --
# composing actor*relative gives (11,0,0), losing the pier's offset. True: (16,0,0).
actor_mat2 = T._transform_dict_to_mat(tdict((10.0, 0.0, 0.0)))
plank_rel = T._transform_dict_to_mat(tdict((1.0, 0.0, 0.0)))
plank_world = tdict((16.0, 0.0, 0.0))

old2 = T._mat_compose(actor_mat2, plank_rel)
check("OLD code drops the intermediate parent transform",
      [round(v, 6) for v in old2[1]] == [11.0, 0.0, 0.0], old2[1])
new2 = T._component_world_mat({"godot_world_transform": plank_world}, actor_mat2)
check("NEW code honours the full nested chain",
      [round(v, 6) for v in new2[1]] == [16.0, 0.0, 0.0], new2[1])

print("\n=== 3. affine_inverse correctness (200 random transforms) ===")
rng = random.Random(99)
bad = 0
for _ in range(200):
    q = [rng.uniform(-1, 1) for _ in range(4)]
    n = math.sqrt(sum(c * c for c in q))
    if n < 1e-3:
        continue
    q = [c / n for c in q]
    m = T._transform_dict_to_mat(tdict(
        (rng.uniform(-500, 500), rng.uniform(-500, 500), rng.uniform(-500, 500)),
        q,
        (rng.uniform(0.2, 4.0), rng.uniform(0.2, 4.0), rng.uniform(0.2, 4.0)),
    ))
    if not mat_close(T._mat_compose(m, T._mat_affine_inverse(m)), T._IDENTITY_MAT, 1e-6):
        bad += 1
check("M * affine_inverse(M) == identity for 200 random transforms", bad == 0, "%d bad" % bad)

print("\n=== 4. Multi-component round trip: actor_inv * world reparents exactly ===")
bad = 0
for _ in range(200):
    q = [rng.uniform(-1, 1) for _ in range(4)]
    n = math.sqrt(sum(c * c for c in q))
    if n < 1e-3:
        continue
    q = [c / n for c in q]
    a_mat = T._transform_dict_to_mat(tdict(
        (rng.uniform(-100, 100), rng.uniform(-100, 100), rng.uniform(-100, 100)), q,
        (rng.uniform(0.5, 2.0), rng.uniform(0.5, 2.0), rng.uniform(0.5, 2.0))))
    w_dict = tdict((rng.uniform(-100, 100), rng.uniform(-100, 100), rng.uniform(-100, 100)))
    w_mat = T._transform_dict_to_mat(w_dict)
    # local = actor^-1 * world ; re-composing under the actor must give world back
    local = T._mat_compose(T._mat_affine_inverse(a_mat), T._component_world_mat(
        {"godot_world_transform": w_dict}, a_mat))
    if not mat_close(T._mat_compose(a_mat, local), w_mat, 1e-6):
        bad += 1
check("actor * (actor^-1 * world) == world for 200 cases", bad == 0, "%d bad" % bad)

print("\n=== 5. Singular basis falls back instead of raising ===")
degenerate = ([[0.0, 0.0, 0.0], [0.0, 1.0, 0.0], [0.0, 0.0, 1.0]], [5.0, 5.0, 5.0])
try:
    inv = T._mat_affine_inverse(degenerate)
    check("zero-scale actor returns identity rather than raising", inv == T._IDENTITY_MAT, inv)
except Exception as e:
    check("zero-scale actor returns identity rather than raising", False, repr(e))

print("\n=== 6. v1 fallback: no godot_world_transform present ===")
fb = T._component_world_mat({"godot_relative_transform": tdict((9.0, 9.0, 9.0))}, actor_mat2)
check("falls back to the actor transform (no doubling)",
      [round(v, 6) for v in fb[1]] == [10.0, 0.0, 0.0], fb[1])

print("\n" + "=" * 60)
if FAIL:
    print("FAILURES (%d): %s" % (len(FAIL), FAIL))
    sys.exit(1)
print("ALL LAYOUT CHECKS PASSED")
