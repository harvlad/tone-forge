"""Phase 1 — characterize the MPFB fretting-hand rig to JSON.

Extracts every quantity the Python FK will need, straight from Blender
(never hand-copied): per bone the armature-space rest matrix
(matrix_local), parent, head/tail, length, and the finger/thumb chain
mapping. Also dumps a rest-pose fingertip table as a first FK anchor.

Run: Blender --background --python mpfb_characterize.py -- <out.json>
"""
import bpy, sys, os, json
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import blender_mpfb_rig as R

argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
OUT = argv[0] if argv else "/tmp/mpfb_rig.json"

basemesh, arm = R.build_human()
bpy.context.view_layer.update()

# Bones we care about for fretting FK: 5 digits × 3 phalanges + wrist.
DIGITS = ["finger1", "finger2", "finger3", "finger4", "finger5"]
want = set()
for d in DIGITS:
    for s in (1, 2, 3):
        want.add(f"{d}-{s}.L")
want.add("wrist.L")

def m2list(m):
    return [list(row) for row in m]  # 4x4 row-major

bones = {}
for b in arm.data.bones:
    if b.name not in want:
        continue
    bones[b.name] = dict(
        parent=b.parent.name if b.parent else None,
        length=b.length,
        head_local=list(b.head_local),
        tail_local=list(b.tail_local),
        # Armature-space rest matrix (bone → armature). This is what
        # Blender's pose FK composes; the Python FK must use exactly it.
        matrix_local=m2list(b.matrix_local),
    )

# Rest-pose world fingertip anchors (armature at identity object xform).
arm.location = (0, 0, 0)
arm.rotation_euler = (0, 0, 0)
bpy.context.view_layer.update()
rest_tips = {}
from mathutils import Vector
for d in DIGITS:
    pb = arm.pose.bones[f"{d}-3.L"]
    tip = arm.matrix_world @ pb.matrix @ Vector((0, pb.length, 0))
    rest_tips[d] = list(tip)

# Base fretting orientation (the pipeline's, so the Python solver can
# place the armature identically) + middle-MCP rest head for anchoring.
fret_rot = R.fretting_rotation(arm)          # 4x4
mid_mcp_rest = list(arm.data.bones["finger3-1.L"].head_local)

out = dict(
    digits=DIGITS,
    finger_map={"index": "finger2", "middle": "finger3",
                "ring": "finger4", "pinky": "finger5", "thumb": "finger1"},
    bones=bones,
    rest_tips=rest_tips,
    armature_matrix_world=m2list(arm.matrix_world),
    fretting_rotation=m2list(fret_rot),
    mid_mcp_rest_local=mid_mcp_rest,
)
with open(OUT, "w") as f:
    json.dump(out, f, indent=1)
print("BONES", len(bones))
for n, b in bones.items():
    print(f"  {n:14s} parent={b['parent']:14s} len={b['length']*1000:.1f}mm")
print("WROTE", OUT)
