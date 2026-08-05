"""Camera study — isolate the VIEWING GEOMETRY variable.

Poses the SAME D chord once (no pose change), then renders it from a
controlled matrix of cameras. Hand + neck, NO dots/labels — each output
is a recognition-gate render for that camera. Current dark-fill material
kept so the ONLY variable is the camera.

Run:
  Blender --background --python camera_study.py -- /out/dir chords.json
"""

import bpy, math, sys, os
from mathutils import Vector

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import blender_mpfb_rig as R

argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
OUT = argv[0] if argv else "/tmp/camstudy"
os.makedirs(OUT, exist_ok=True)
if len(argv) > 1:
    R.CHORDS, R.BARRES = R.load_chords(argv[1])

CHORD = "D"

# --- Build + pose D exactly as the pipeline does (no pose edits) ---
basemesh, arm = R.build_human()
R.build_neck()
R.setup_render(basemesh)          # sets flat handmat + freestyle + a camera

# --- RENDER LANGUAGE PASS (mockup style) ---------------------------------
# Translucent-reading dark fill + thin clean rim; kill micro-silhouette
# noise from the dense curled mesh with a Smooth modifier; flat bg.
import bpy as _b
# Lighter fill so it reads as a subtle overlay, not a black blob.
hmat = _b.data.materials["handmat"]
em = hmat.node_tree.nodes.get("Emission")
if em: em.inputs["Color"].default_value = (0.10, 0.105, 0.14, 1)
# Smooth the mesh so tightly-curled folds don't each throw a silhouette.
sm = basemesh.modifiers.new("JamnSmooth", "SMOOTH")
sm.factor = 0.6; sm.iterations = 8
# Thin, calm rim.
_b.context.scene.render.line_thickness = 1.1
ls = _b.context.view_layer.freestyle_settings.linesets["outline"]
ls.select_silhouette = True
ls.select_border = True      # masked-hand boundary (wrist/finger edges)
ls.select_crease = False
ls.linestyle.color = (0.72, 0.74, 0.82)
ls.linestyle.thickness = 1.1
# Flat dark world (no gradient/corner artifacts).
_b.context.scene.world.node_tree.nodes["Background"].inputs[0].default_value = (0.05, 0.05, 0.07, 1)
targets = R.add_ik(arm)
ROT = (0, math.radians(-90), 0)
R.place_hand(arm, CHORD, ROT)
R.pose_chord(arm, targets, CHORD)
bpy.context.view_layer.update()

scene = bpy.context.scene
scene.render.resolution_x = 1200
scene.render.resolution_y = 1200

# Target = pressing-cluster centre in guitar space (m).
press = [t for fi, t in R.CHORDS[CHORD].items() if t]
cx = sum(R.finger_x(f) for _, f in press) / len(press)
cz = sum(R.string_z(s, abs(R.finger_x(f))) for s, f in press) / len(press)
target = Vector((cx, 0.004, cz - 0.02))

tgt_empty = bpy.data.objects.new("camtarget", None)
scene.collection.objects.link(tgt_empty)
tgt_empty.location = target

# Camera matrix: (name, offset-from-target (m), type, ortho_scale|lens_mm).
# Offsets in guitar space: +X toward bridge (along neck), -Y viewer front,
# +Z up-strings (toward low E / above the hand), -Z below (palm side).
CAMS = [
    ("00_current_deadfront", (0.0, -0.55, 0.0),  "ORTHO", 0.14),
    ("A_elevated_front",     (0.0, -0.45, 0.30), "ORTHO", 0.17),
    ("B_alongneck_3q",       (0.30, -0.45, 0.05),"ORTHO", 0.17),
    ("C_elev_alongneck",     (0.26, -0.42, 0.24),"PERSP", 42),
    ("D_strong_alongneck",   (0.42, -0.34, 0.14),"PERSP", 40),
    ("E_below_front_up",     (0.0, -0.45, -0.26),"ORTHO", 0.17),
]


def make_cam(name, offset, ctype, param):
    cam_data = bpy.data.cameras.new(name)
    cam_data.type = ctype
    if ctype == "ORTHO":
        cam_data.ortho_scale = param
    else:
        cam_data.lens = param
    cam = bpy.data.objects.new(name, cam_data)
    scene.collection.objects.link(cam)
    cam.location = target + Vector(offset)
    c = cam.constraints.new("TRACK_TO")
    c.target = tgt_empty
    c.track_axis = "TRACK_NEGATIVE_Z"
    c.up_axis = "UP_Y"
    return cam


for name, offset, ctype, param in CAMS:
    cam = make_cam(name, offset, ctype, param)
    scene.camera = cam
    bpy.context.view_layer.update()
    scene.render.filepath = f"{OUT}/cam-{name}.png"
    bpy.ops.render.render(write_still=True)
    print(f"RENDERED {name}")

print("CAMERA_STUDY_DONE")
