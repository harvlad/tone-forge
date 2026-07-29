"""Best-system recognition renders: camera A (elevated front) + mockup
render language, on several fixtures, DOT-FREE. For the visual gate.

Run: Blender --background --python render_best.py -- /out chords.json
"""
import bpy, math, sys, os
from mathutils import Vector

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import blender_mpfb_rig as R

argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
OUT = argv[0] if argv else "/tmp/best"
os.makedirs(OUT, exist_ok=True)
if len(argv) > 1:
    R.CHORDS, R.BARRES = R.load_chords(argv[1])

basemesh, arm = R.build_human()
R.build_neck()
R.setup_render(basemesh)

# Render language (mockup style).
hmat = bpy.data.materials["handmat"]
hmat.node_tree.nodes["Emission"].inputs["Color"].default_value = (0.10, 0.105, 0.14, 1)
sm = basemesh.modifiers.new("JamnSmooth", "SMOOTH")
sm.factor = 0.6; sm.iterations = 8
bpy.context.scene.render.line_thickness = 1.1
ls = bpy.context.view_layer.freestyle_settings.linesets["outline"]
ls.select_silhouette = True; ls.select_border = True; ls.select_crease = False
ls.linestyle.color = (0.72, 0.74, 0.82); ls.linestyle.thickness = 1.1
bpy.context.scene.world.node_tree.nodes["Background"].inputs[0].default_value = (0.05, 0.05, 0.07, 1)

targets = R.add_ik(arm)
ROT = (0, math.radians(-90), 0)
scene = bpy.context.scene
scene.render.resolution_x = 1200
scene.render.resolution_y = 1200

tgt_empty = bpy.data.objects.new("camtarget", None)
scene.collection.objects.link(tgt_empty)

# The single "note" fixture is a lone middle-finger fret. Others are the
# solver's chord contacts (via the Blender IK pipeline).
FIXTURES = ["single", "D", "G", "C"]


def pose(chord):
    if chord == "single":
        # temporarily install a one-finger chord
        R.CHORDS["single"] = {1: None, 2: (3, 2), 3: None, 4: None}
        R.BARRES.pop("single", None)
    R.place_hand(arm, chord, ROT)
    R.pose_chord(arm, targets, chord)
    bpy.context.view_layer.update()


def cam_A():
    press = [t for fi, t in R.CHORDS[C].items() if t]
    cx = sum(R.finger_x(f) for _, f in press) / len(press)
    cz = sum(R.string_z(s, abs(R.finger_x(f))) for s, f in press) / len(press)
    tgt = Vector((cx, 0.004, cz - 0.02))
    tgt_empty.location = tgt
    cam_data = bpy.data.cameras.new("A")
    cam_data.type = "ORTHO"; cam_data.ortho_scale = 0.17
    cam = bpy.data.objects.new("A", cam_data)
    scene.collection.objects.link(cam)
    cam.location = tgt + Vector((0.0, -0.45, 0.30))
    c = cam.constraints.new("TRACK_TO")
    c.target = tgt_empty; c.track_axis = "TRACK_NEGATIVE_Z"; c.up_axis = "UP_Y"
    scene.camera = cam
    bpy.context.view_layer.update()


for C in FIXTURES:
    pose(C)
    cam_A()
    scene.render.filepath = f"{OUT}/best-{C}.png"
    bpy.ops.render.render(write_still=True)
    print("RENDERED", C)
print("BEST_DONE")
