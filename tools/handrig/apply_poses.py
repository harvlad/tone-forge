"""BRIDGE — drive the MPFB rig from the biomechanical solver's joint
angles via forward kinematics (NO Blender IK), then render at Camera A
with the mockup render language. Produces, per fixture:
  best-<name>.png        hand + fretboard, dot-free
  handonly-<name>.png    hand ONLY (guitar removed) — the harsh gate

Run: Blender --background --python apply_poses.py -- <poses.json> <out_dir>
"""
import bpy, math, sys, os, json
from mathutils import Vector, Euler

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import blender_mpfb_rig as R

argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
POSES = argv[0]
OUT = argv[1] if len(argv) > 1 else "/tmp/applied"
os.makedirs(OUT, exist_ok=True)
poses = json.load(open(POSES))

FINGER_BONE = {"index": "finger2", "middle": "finger3",
               "ring": "finger4", "pinky": "finger5"}

# Flex sign: MPFB local-X rotation direction that curls the finger onto
# the board. Calibrated on single-note (see FLEX_SIGN below).
FLEX_SIGN = 1.0
ABD_SIGN = 1.0

basemesh, arm = R.build_human()
R.build_neck()
R.setup_render(basemesh)
# Render language.
hmat = bpy.data.materials["handmat"]
hmat.node_tree.nodes["Emission"].inputs["Color"].default_value = (0.10, 0.105, 0.14, 1)
sm = basemesh.modifiers.new("JamnSmooth", "SMOOTH")
sm.factor = 0.6; sm.iterations = 8
bpy.context.scene.render.line_thickness = 1.1
ls = bpy.context.view_layer.freestyle_settings.linesets["outline"]
ls.select_silhouette = True; ls.select_border = True; ls.select_crease = False
ls.linestyle.color = (0.72, 0.74, 0.82); ls.linestyle.thickness = 1.1
bpy.context.scene.world.node_tree.nodes["Background"].inputs[0].default_value = (0.05, 0.05, 0.07, 1)

scene = bpy.context.scene
scene.render.resolution_x = 1200
scene.render.resolution_y = 1200

# Neck objects (to hide for hand-only renders).
neck_objs = [o for o in bpy.data.objects
             if o.type == "MESH" and o is not basemesh]

tgt_empty = bpy.data.objects.new("camtarget", None)
scene.collection.objects.link(tgt_empty)
cam = None


def place(contacts):
    """Global placement: same orientation as the pipeline (fingers up,
    palm to board); position so the middle-finger MCP sits below the
    pressed-cluster centre. Contact-geometry driven, not chord."""
    rot = R.fretting_rotation(arm)
    press = [(c["string"], c["fret"]) for c in contacts]
    cxs = [R.finger_x(fr) for _, fr in press]
    cx = sum(cxs) / len(cxs)
    czs = [R.string_z(s, abs(R.finger_x(fr))) for s, fr in press]
    cz = sum(czs) / len(czs)
    mcp = arm.data.bones["finger3-1.L"].head_local
    want = Vector((cx, -0.024, cz - R.BOARD_W / 2 - 0.020))
    loc = want - (rot @ mcp.to_4d()).to_3d()
    arm.rotation_euler = rot.to_euler()
    arm.location = loc
    return cx, cz


def apply_angles(pose):
    for fg, digit in FINGER_BONE.items():
        mcpF, mcpA, pip, dip = pose["fingers"][fg]
        b1 = arm.pose.bones[f"{digit}-1.L"]
        b1.rotation_mode = "XYZ"
        b1.rotation_euler = Euler((FLEX_SIGN * mcpF, 0.0, ABD_SIGN * mcpA), "XYZ")
        b2 = arm.pose.bones[f"{digit}-2.L"]
        b2.rotation_mode = "XYZ"
        b2.rotation_euler = Euler((FLEX_SIGN * pip, 0.0, 0.0), "XYZ")
        b3 = arm.pose.bones[f"{digit}-3.L"]
        b3.rotation_mode = "XYZ"
        b3.rotation_euler = Euler((FLEX_SIGN * dip, 0.0, 0.0), "XYZ")
    # Thumb: curl behind the neck (fixed ready posture).
    for i, bn in enumerate(("finger1-1.L", "finger1-2.L", "finger1-3.L")):
        b = arm.pose.bones[bn]
        b.rotation_mode = "XYZ"
        b.rotation_euler = Euler((FLEX_SIGN * math.radians(20), 0, 0), "XYZ")


def cam_A(cx, cz):
    global cam
    tgt_empty.location = Vector((cx, 0.004, cz - 0.02))
    if cam is None:
        cd = bpy.data.cameras.new("A")
        cd.type = "ORTHO"; cd.ortho_scale = 0.17
        cam = bpy.data.objects.new("A", cd)
        scene.collection.objects.link(cam)
        c = cam.constraints.new("TRACK_TO")
        c.target = tgt_empty; c.track_axis = "TRACK_NEGATIVE_Z"; c.up_axis = "UP_Y"
        scene.camera = cam
    cam.location = tgt_empty.location + Vector((0.0, -0.45, 0.30))


WHICH = argv[2:] if len(argv) > 2 else list(poses.keys())
for name in WHICH:
    pose = poses[name]
    cx, cz = place(pose["contacts"])
    apply_angles(pose)
    bpy.context.view_layer.update()
    cam_A(cx, cz)
    bpy.context.view_layer.update()
    for o in neck_objs:
        o.hide_render = False
    scene.render.filepath = f"{OUT}/best-{name}.png"
    bpy.ops.render.render(write_still=True)
    for o in neck_objs:
        o.hide_render = True
    scene.render.filepath = f"{OUT}/handonly-{name}.png"
    bpy.ops.render.render(write_still=True)
    for o in neck_objs:
        o.hide_render = False
    print("RENDERED", name)
print("APPLY_DONE")
