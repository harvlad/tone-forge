# render_scene.py — 3D "hero" neck+hand scene for the Perform view.
#
# Renders the neck AND hand together under ONE fixed angled camera (so the neck
# is stable across chords and reads as a hand naturally holding it), applying
# pre-solved MPFB states (solver/solve_chords.py). Also exports neck_projection
# .json: (string,fret) -> pixel through the SAME camera, so the app can overlay
# the interactive dots / playhead onto the angled neck.
#
# Run: blender -b --python render_scene.py -- <states.json> <outdir> [names...]

import bpy, sys, os, json
from mathutils import Vector, Euler, Matrix
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import blender_mpfb_rig as R
from bpy_extras.object_utils import world_to_camera_view

argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
STATES, OUT = argv[0], argv[1]
states = json.load(open(STATES))
names = argv[2:] if len(argv) > 2 else [k for k in states if not k.startswith("__")]

basemesh, arm = R.build_human()
R.build_neck()
R.setup_render(basemesh)
scene = bpy.context.scene
scene.render.resolution_x = 1600
scene.render.resolution_y = 900
fret_rot = Matrix([r for r in R.fretting_rotation(arm)])

FMAP = {"index": "finger2", "middle": "finger3", "ring": "finger4", "pinky": "finger5"}

def apply(state):
    v = state["state"]
    M = fret_rot @ Euler(tuple(v[3:6]), "XYZ").to_matrix().to_4x4()
    arm.location = Vector(v[0:3]); arm.rotation_euler = M.to_euler()
    META = ["metacarpal1.L", "metacarpal2.L", "metacarpal3.L", "metacarpal4.L"]
    meta = v[26:30] if len(v) >= 30 else [0, 0, 0, 0]
    def setb(bn, e):
        pb = arm.pose.bones[bn]; pb.rotation_mode = "XYZ"; pb.rotation_euler = Euler(e, "XYZ")
    for i, f in enumerate(("index", "middle", "ring", "pinky")):
        mcpF, mcpA, pip, dip = v[6 + i*4:6 + i*4 + 4]; d = FMAP[f]
        setb(META[i], (meta[i], 0, 0))
        setb(f"{d}-1.L", (mcpF, 0, mcpA)); setb(f"{d}-2.L", (pip, 0, 0)); setb(f"{d}-3.L", (dip, 0, 0))
    mcpF, mcpA, ip, _ = v[22:26]
    setb("finger1-1.L", (mcpF, 0, mcpA)); setb("finger1-2.L", (ip, 0, 0)); setb("finger1-3.L", (ip*0.6, 0, 0))
    bpy.context.view_layer.update()

# HERO camera — fixed, angled (front + slightly above), framing frets 0..WINDOW.
WIN = R.WINDOW
cx = (R.finger_x(0) + R.finger_x(WIN)) / 2
cz = R.string_z(2.5, abs(R.finger_x(3)))
tgt = bpy.data.objects.new("herotgt", None); scene.collection.objects.link(tgt)
tgt.location = Vector((cx, 0.0, cz))
cd = bpy.data.cameras.new("hero"); cd.type = "ORTHO"; cd.ortho_scale = 0.30
cam = bpy.data.objects.new("hero", cd); scene.collection.objects.link(cam); scene.camera = cam
con = cam.constraints.new("TRACK_TO"); con.target = tgt
con.track_axis = "TRACK_NEGATIVE_Z"; con.up_axis = "UP_Y"
cam.location = tgt.location + Vector((0.0, -0.45, 0.20))
bpy.context.view_layer.update()

for name in names:
    apply(states[name])
    scene.render.film_transparent = False
    scene.render.filepath = f"{OUT}/scene-{name}.png"
    bpy.ops.render.render(write_still=True)
    print("RENDERED", name)

# Projection grid (fixed camera → one mapping for all chords).
bpy.context.view_layer.update()
rx, ry = scene.render.resolution_x, scene.render.resolution_y
grid = []
for s in range(6):
    row = []
    for f in range(WIN + 1):
        x = R.finger_x(f); wp = Vector((x, 0.0, R.string_z(s, abs(x))))
        co = world_to_camera_view(scene, cam, wp)
        row.append([round(co.x * rx, 2), round((1 - co.y) * ry, 2)])
    grid.append(row)
json.dump({"res": [rx, ry], "frets": WIN, "grid": grid},
          open(f"{OUT}/neck_projection.json", "w"), indent=1)
print("WROTE_PROJECTION")
