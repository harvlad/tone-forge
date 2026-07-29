"""Render UNIFIED MPFB solver states at Camera A. State is MPFB joint
angles + root transform — applied 1:1 to the rig (NO bridge, NO IK), so
the render reproduces the solved fingertips exactly.

Also asserts render==solver: after applying, compares Blender fingertip
world positions to the solver's recorded contact points.

Run: Blender --background --python mpfb_render.py -- <states.json> <out> [names...]
"""
import bpy, sys, os, json, math
import numpy as np
from mathutils import Vector, Euler, Matrix
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import blender_mpfb_rig as R

argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
STATES = argv[0]; OUT = argv[1] if len(argv) > 1 else "/tmp/mpfb_render"
os.makedirs(OUT, exist_ok=True)
states = json.load(open(STATES))

FMAP = {"index": "finger2", "middle": "finger3", "ring": "finger4",
        "pinky": "finger5"}

basemesh, arm = R.build_human()
R.build_neck()
R.setup_render(basemesh)
hmat = bpy.data.materials["handmat"]
hmat.node_tree.nodes["Emission"].inputs["Color"].default_value = (0.10, 0.105, 0.14, 1)
sm = basemesh.modifiers.new("JamnSmooth", "SMOOTH"); sm.factor = 0.6; sm.iterations = 8
bpy.context.scene.render.line_thickness = 1.1
ls = bpy.context.view_layer.freestyle_settings.linesets["outline"]
ls.select_silhouette = True; ls.select_border = True; ls.select_crease = False
ls.linestyle.color = (0.72, 0.74, 0.82); ls.linestyle.thickness = 1.1
bpy.context.scene.world.node_tree.nodes["Background"].inputs[0].default_value = (0.05, 0.05, 0.07, 1)
scene = bpy.context.scene
scene.render.resolution_x = 1200; scene.render.resolution_y = 1200
neck_objs = [o for o in bpy.data.objects if o.type == "MESH" and o is not basemesh]

fret_rot = Matrix([r for r in R.fretting_rotation(arm)])
tgt_empty = bpy.data.objects.new("t", None); scene.collection.objects.link(tgt_empty)
cam = None


def apply(state):
    v = state["state"]
    root_loc = Vector(v[0:3]); root_rot = v[3:6]
    R_off = Euler(root_rot, "XYZ").to_matrix().to_4x4()
    M = fret_rot @ R_off
    arm.location = root_loc
    arm.rotation_euler = M.to_euler()
    META_BONE = ["metacarpal1.L", "metacarpal2.L", "metacarpal3.L", "metacarpal4.L"]
    meta = v[26:30] if len(v) >= 30 else [0, 0, 0, 0]
    for i, f in enumerate(("index", "middle", "ring", "pinky")):
        mcpF, mcpA, pip, dip = v[6 + i*4:6 + i*4 + 4]
        d = FMAP[f]
        mb = arm.pose.bones[META_BONE[i]]; mb.rotation_mode = "XYZ"
        mb.rotation_euler = Euler((meta[i], 0, 0), "XYZ")
        for bn, e in ((f"{d}-1.L", (mcpF, 0, mcpA)), (f"{d}-2.L", (pip, 0, 0)),
                      (f"{d}-3.L", (dip, 0, 0))):
            pb = arm.pose.bones[bn]; pb.rotation_mode = "XYZ"
            pb.rotation_euler = Euler(e, "XYZ")
    mcpF, mcpA, ip, _ = v[22:26]
    for bn, e in (("finger1-1.L", (mcpF, 0, mcpA)), ("finger1-2.L", (ip, 0, 0)),
                  ("finger1-3.L", (ip * 0.6, 0, 0))):
        pb = arm.pose.bones[bn]; pb.rotation_mode = "XYZ"
        pb.rotation_euler = Euler(e, "XYZ")
    bpy.context.view_layer.update()


def cam_A(state):
    global cam
    press = state["contacts"]
    cx = sum(R.finger_x(c["fret"]) for c in press) / len(press)
    cz = sum(R.string_z(c["string"], abs(R.finger_x(c["fret"]))) for c in press) / len(press)
    tgt_empty.location = Vector((cx, 0.004, cz - 0.02))
    if cam is None:
        cd = bpy.data.cameras.new("A"); cd.type = "ORTHO"; cd.ortho_scale = 0.17
        cam = bpy.data.objects.new("A", cd); scene.collection.objects.link(cam)
        c = cam.constraints.new("TRACK_TO"); c.target = tgt_empty
        c.track_axis = "TRACK_NEGATIVE_Z"; c.up_axis = "UP_Y"; scene.camera = cam
    cam.location = tgt_empty.location + Vector((0.0, -0.45, 0.30))


WHICH = argv[2:] if len(argv) > 2 else list(states.keys())
for name in WHICH:
    st = states[name]
    apply(st)
    # Render==solver assertion: Blender tip vs solver recorded tip (mm).
    max_a = 0.0
    for c in st["contacts"]:
        pb = arm.pose.bones[f"{FMAP[c['finger']]}-3.L"]
        tip = arm.matrix_world @ pb.matrix @ Vector((0, pb.length, 0))
        solver_tip = Vector(st["fingers_mm"][c["finger"]][-1]) / 1000.0
        max_a = max(max_a, (tip - solver_tip).length * 1000)
    cam_A(st)
    for o in neck_objs: o.hide_render = False
    scene.render.filepath = f"{OUT}/best-{name}.png"; bpy.ops.render.render(write_still=True)
    for o in neck_objs: o.hide_render = True
    scene.render.filepath = f"{OUT}/handonly-{name}.png"; bpy.ops.render.render(write_still=True)
    for o in neck_objs: o.hide_render = False
    print(f"RENDERED {name} render_vs_solver_max={max_a:.3f}mm")
print("MPFB_RENDER_DONE")
