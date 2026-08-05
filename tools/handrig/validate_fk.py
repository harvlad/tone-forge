"""Phase 2 gate — validate Python MPFB FK against Blender, sub-mm.

Sets random physiologically-plausible finger rotations on the MPFB rig,
reads Blender's evaluated fingertip/joint world positions, computes the
same with mpfb_fk (Python), and reports max discrepancy. Pre-registered
tolerance: 0.5 mm. If it fails, the port must NOT proceed.

Run: Blender --background --python validate_fk.py -- <rig.json>
"""
import bpy, sys, os, math
import numpy as np
from mathutils import Euler, Vector
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import blender_mpfb_rig as R
import mpfb_fk

argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
RIG = argv[0]
TOL_MM = 0.5

basemesh, arm = R.build_human()
# Deterministic pseudo-random via a fixed grid (no Date/random in Blender).
def prand(seed):
    x = math.sin(seed * 12.9898) * 43758.5453
    return (x - math.floor(x))          # 0..1

hand = mpfb_fk.MPFBHand(RIG)
DIGITS = ["finger1", "finger2", "finger3", "finger4", "finger5"]

# Place the armature at a non-trivial transform to exercise world xform.
arm.location = (0.1, -0.05, 0.2)
arm.rotation_euler = (math.radians(20), math.radians(-35), math.radians(10))
bpy.context.view_layer.update()          # recompute matrix_world BEFORE capture
AW = [list(r) for r in arm.matrix_world]

max_err = 0.0
worst = None
n_tests = 40
for t in range(n_tests):
    # Random plausible per-phalanx rotations.
    poses = {}
    for di, d in enumerate(DIGITS):
        angs = []
        for s in range(3):
            base = t * 5 + di * 3 + s
            rx = (prand(base) * 1.6 - 0.2)       # flex-ish −0.2..1.4 rad
            ry = (prand(base + 100) * 0.2 - 0.1)
            rz = (prand(base + 200) * 0.4 - 0.2)  # abduction-ish
            angs.append((rx, ry, rz))
        poses[d] = angs
    # Apply in Blender.
    for d in DIGITS:
        for s in range(3):
            pb = arm.pose.bones[f"{d}-{s+1}.L"]
            pb.rotation_mode = "XYZ"
            pb.rotation_euler = Euler(poses[d][s], "XYZ")
    bpy.context.view_layer.update()
    # Compare tips (and pip/dip joints).
    for d in DIGITS:
        py = hand.finger_fk(d, poses[d], AW)
        pbs = [arm.pose.bones[f"{d}-{s+1}.L"] for s in range(3)]
        bl = [arm.matrix_world @ pbs[0].matrix.translation,  # base(pip head)
              arm.matrix_world @ pbs[1].matrix.translation,
              arm.matrix_world @ pbs[2].matrix.translation,
              arm.matrix_world @ pbs[2].matrix @ Vector((0, pbs[2].length, 0))]
        # py returns [base(-1 head), pip, dip, tip]; bl[0] is -1's head too.
        for ji, (pj, bj) in enumerate(zip(py, bl)):
            e = float(np.linalg.norm(np.array(pj) - np.array(bj))) * 1000
            if e > max_err:
                max_err = e; worst = (t, d, ji, [round(v,4) for v in pj], [round(float(v),4) for v in bj])

print(f"FK VALIDATION: max discrepancy {max_err:.4f} mm over {n_tests} random poses")
print(f"worst at {worst}")
print("GATE", "PASS" if max_err < TOL_MM else "FAIL", f"(tol {TOL_MM} mm)")
