# blender_hand_rig.py — JAMN fretting-hand reference pipeline.
#
# Human Base Meshes (CC0) "Hand - Realistic" → auto-landmarked finger
# skeleton → auto-skin → physically dimensioned guitar neck → IK chord
# poses (G, D, Em, C) → front-orthographic silhouette renders + bone-
# transform JSON.
#
# Run:
#   Blender --background human_base_meshes_bundle.blend --python blender_hand_rig.py -- /out/dir
#
# Guitar space (Blender metres): origin nut at RIGHT end of the board
# section; board runs along -X (frets increase leftward, matching the
# JAMN nut-right UI). Strings run along X in the XZ plane at y=0; low E
# is the TOP string (audience view). +Y points AWAY from the camera
# (camera looks +Y), so the thumb behind the neck is occluded by the
# board.

import bpy, bmesh, json, math, sys
from mathutils import Vector

OUT = sys.argv[sys.argv.index("--") + 1] if "--" in sys.argv else "/tmp/handrig"

SCALE_LEN = 0.648           # 648 mm
NECK_THICK = 0.021
STRING_SPAN_NUT = 0.035
STRING_SPAN_12 = 0.0435     # halfway to saddle taper
BOARD_W = 0.052             # fingerboard width (6 strings + margins)
WINDOW = 5                  # frets modelled

def wire(n):
    return SCALE_LEN * (1 - 2 ** (-n / 12))

def finger_x(fret):                       # contact 30% behind the wire
    lo, hi = wire(fret - 1), wire(fret)
    return -(hi - (hi - lo) * 0.30)       # board runs along -X

def string_z(s, x_abs):                   # s: 0 = low E (TOP) … 5 high e
    t = min(1.0, x_abs / (SCALE_LEN / 2))
    span = STRING_SPAN_NUT + (STRING_SPAN_12 - STRING_SPAN_NUT) * t
    return (2.5 - s) * span / 5           # low E highest z

# ---------------------------------------------------------------- scene
def keep_only_hand():
    hand = bpy.data.objects.get("Hand  - Realistic") or bpy.data.objects.get("Hand - Realistic")
    assert hand, "hand mesh not found"
    for o in list(bpy.data.objects):
        if o is not hand:
            bpy.data.objects.remove(o, do_unlink=True)
    hand.modifiers.clear()                # drop Multires (base level fine)
    hand.parent = None
    # Local axes: fingers +Z, palm normal ±Y, thumb side ±X.
    # Move to origin, mirror X → LEFT hand.
    bpy.context.view_layer.objects.active = hand
    hand.select_set(True)
    bpy.ops.object.transform_apply(location=True, rotation=True, scale=True)
    # Center the mesh DATA at the origin (apply bakes world offsets
    # into the vertices), then mirror X → LEFT hand.
    from mathutils import Matrix
    vs = hand.data.vertices
    cx = sum(v.co.x for v in vs) / len(vs)
    cy = sum(v.co.y for v in vs) / len(vs)
    z_lo = min(v.co.z for v in vs)
    hand.data.transform(Matrix.Translation((-cx, -cy, -z_lo)))
    # Orient fingers +Z: the finger end has the wider x-spread (four
    # splayed digits); the forearm end is a compact stump.
    zs = sorted(v.co.z for v in vs)
    z0, z1 = zs[0], zs[-1]
    h = z1 - z0
    top = [v.co.x for v in vs if v.co.z > z1 - h * 0.12]
    bot = [v.co.x for v in vs if v.co.z < z0 + h * 0.12]
    if (max(bot) - min(bot)) > (max(top) - min(top)):
        hand.data.transform(Matrix.Rotation(math.pi, 4, "X"))
        z_lo2 = min(v.co.z for v in vs)
        hand.data.transform(Matrix.Translation((0, 0, -z_lo2)))
    hand.scale.x = -1
    bpy.ops.object.transform_apply(scale=True)
    return hand

def landmarks(hand):
    """Cluster fingertip/knuckle landmarks from the mesh.
    Returns per-digit (name, tip, mcp) in hand-local coords."""
    vs = [v.co.copy() for v in hand.data.vertices]
    zs = sorted(v.z for v in vs)
    z_lo, z_hi = zs[0], zs[-1]
    h = z_hi - z_lo
    # The four fingers live in the top 40%; cluster by x into 4 bins.
    top = [v for v in vs if v.z > z_lo + h * 0.62]
    xs = sorted(v.x for v in top)
    x_min, x_max = xs[0], xs[-1]
    bins = [[] for _ in range(4)]
    for v in top:
        i = min(3, int((v.x - x_min) / (x_max - x_min + 1e-9) * 4))
        bins[i].append(v)
    digits = []
    names = ["index", "middle", "ring", "pinky"]
    # LEFT hand mirrored: index at max x? Establish by length — middle
    # finger longest. Sort bins by x centre; index side = nearest thumb.
    thumb_verts = [v for v in vs if v.z < z_lo + h * 0.62 and abs(v.y) < 0.05]
    # Thumb = extreme x cluster in the lower-mid region.
    tx = sorted(v.x for v in thumb_verts)
    thumb_side = 1 if abs(tx[-1] - x_max) < abs(tx[0] - x_min) else -1
    order = range(3, -1, -1) if thumb_side == 1 else range(4)
    for name, bi in zip(names, order):
        b = bins[bi]
        b.sort(key=lambda v: v.z)
        tip = b[-1]
        base_z = min(v.z for v in b)
        cx = sum(v.x for v in b) / len(b)
        cy = sum(v.y for v in b) / len(b)
        digits.append((name, Vector((tip.x, tip.y, tip.z)),
                       Vector((cx, cy, base_z - 0.012))))
    # Thumb tip: extreme x on thumb side.
    tv = max(thumb_verts, key=lambda v: v.x * thumb_side)
    return digits, Vector((tv.x, tv.y, tv.z)), thumb_side, (z_lo, z_hi)

def build_armature(hand):
    digits, thumb_tip, thumb_side, (z_lo, z_hi) = landmarks(hand)
    arm_data = bpy.data.armatures.new("HandRig")
    arm = bpy.data.objects.new("HandRig", arm_data)
    bpy.context.collection.objects.link(arm)
    bpy.context.view_layer.objects.active = arm
    bpy.ops.object.mode_set(mode="EDIT")
    eb = arm_data.edit_bones

    wrist_head = Vector((0, 0, z_lo + 0.01))
    wrist = eb.new("wrist")
    wrist.head = wrist_head
    wrist.tail = wrist_head + Vector((0, 0, 0.05))

    chains = {}
    for name, tip, mcp in digits:
        segs = [0.46, 0.30, 0.24]         # proximal/middle/distal
        prev = wrist
        p0 = mcp
        d = tip - mcp
        acc = 0.0
        for si, frac in enumerate(segs):
            b = eb.new(f"{name}.{si}")
            b.head = p0 + d * acc
            acc += frac
            b.tail = p0 + d * min(1.0, acc)
            b.parent = prev
            b.use_connect = si > 0
            prev = b
        chains[name] = prev.name          # distal bone name
        print(f"DIGIT {name}: mcp={tuple(round(v,4) for v in mcp)} tip={tuple(round(v,4) for v in tip)}")
    # Thumb: wrist→CMC→tip, 2 bones.
    cmc = wrist_head + (thumb_tip - wrist_head) * 0.35
    t1 = eb.new("thumb.0"); t1.head = cmc; t1.tail = cmc + (thumb_tip - cmc) * 0.55
    t1.parent = wrist
    t2 = eb.new("thumb.1"); t2.head = t1.tail; t2.tail = thumb_tip
    t2.parent = t1; t2.use_connect = True
    bpy.ops.object.mode_set(mode="OBJECT")

    # Scripted skinning: bone-heat auto weights tear this mesh (open
    # wrist boundary), so weight each vertex to its two nearest bone
    # segments with inverse-square falloff — robust for a hand.
    bones = [(b.name, Vector(b.head_local), Vector(b.tail_local),
              b.parent.name if b.parent else None)
             for b in arm.data.bones]
    related = {}
    for name, _, _, parent in bones:
        rel = {name}
        if parent: rel.add(parent)
        for other, _, _, op in bones:
            if op == name: rel.add(other)
        related[name] = rel
    vgs = {name: hand.vertex_groups.new(name=name) for name, _, _, _ in bones}

    def seg_dist(p, a, b):
        ab = b - a
        t = max(0.0, min(1.0, (p - a).dot(ab) / max(1e-9, ab.length_squared)))
        return (p - (a + ab * t)).length

    for v in hand.data.vertices:
        ds = sorted(
            ((seg_dist(v.co, a, b), name) for name, a, b, _ in bones))
        d0, n0 = ds[0]
        # Blend only with a bone RELATED to the nearest (parent/child)
        # — never across neighbouring fingers, which tears the webbing
        # when one finger moves and its neighbour doesn't.
        w1 = 0.0; n1 = None
        for d, n in ds[1:]:
            if n in related[n0]:
                w1 = max(0.0, 1.0 - (d - d0) / max(1e-9, d0 + 0.004)) * 0.5
                n1 = n
                break
        vgs[n0].add([v.index], 1.0 - w1, "REPLACE")
        if n1 and w1 > 0.01:
            vgs[n1].add([v.index], w1, "REPLACE")

    mod = hand.modifiers.new("Armature", "ARMATURE")
    mod.object = arm
    mod.use_deform_preserve_volume = True
    cs = hand.modifiers.new("CorrectiveSmooth", "CORRECTIVE_SMOOTH")
    cs.factor = 0.6
    cs.iterations = 8
    sub = hand.modifiers.new("Subsurf", "SUBSURF")
    sub.levels = 2
    sub.render_levels = 2
    hand.parent = arm
    return arm, chains

# ------------------------------------------------------------ guitar
def build_neck():
    span = wire(WINDOW)
    board = bpy.data.meshes.new("board")
    bm = bmesh.new()
    x0, x1 = -span, 0.0
    y0, y1 = 0.004, 0.004 + NECK_THICK
    z0, z1 = -BOARD_W / 2, BOARD_W / 2
    verts = [bm.verts.new((x, y, z)) for x in (x0, x1) for y in (y0, y1) for z in (z0, z1)]
    import itertools
    bmesh.ops.convex_hull(bm, input=bm.verts)
    bm.to_mesh(board); bm.free()
    ob = bpy.data.objects.new("neck", board)
    bpy.context.collection.objects.link(ob)
    mat = bpy.data.materials.new("neckmat")
    mat.use_nodes = True
    bsdf = mat.node_tree.nodes["Principled BSDF"]
    bsdf.inputs["Base Color"].default_value = (0.05, 0.035, 0.025, 1)
    ob.data.materials.append(mat)
    # Strings + fret wires as thin cylinders.
    smat = bpy.data.materials.new("stringmat")
    smat.use_nodes = True
    smat.node_tree.nodes["Principled BSDF"].inputs["Base Color"].default_value = (0.7, 0.7, 0.7, 1)
    for s in range(6):
        z = string_z(s, span / 2)
        bpy.ops.mesh.primitive_cylinder_add(radius=0.0007, depth=span,
            location=(-span / 2, 0.003, z), rotation=(0, math.pi / 2, 0))
        bpy.context.object.data.materials.append(smat)
    for f in range(WINDOW + 1):
        x = -wire(f)
        bpy.ops.mesh.primitive_cylinder_add(radius=0.001, depth=BOARD_W,
            location=(x, 0.0035, 0), rotation=(math.pi / 2, 0, 0))
        bpy.context.object.data.materials.append(smat)

# ------------------------------------------------------------- posing
CHORDS = {
    # (finger 1-4 -> (string 0=lowE, fret) or None)
    "G":  {1: (4, 2), 2: (5, 3), 3: (0, 3), 4: None},
    "D":  {1: (3, 2), 2: (5, 2), 3: (4, 3), 4: None},
    "Em": {1: None, 2: (1, 2), 3: (2, 2), 4: None},
    "C":  {1: (4, 1), 2: (3, 2), 3: (1, 3), 4: None},
}
FINGERS = {1: "index", 2: "middle", 3: "ring", 4: "pinky"}

def add_ik(arm, chains):
    """IK constraint per finger distal bone toward an empty, with a
    pole target on the viewer side so the bend plane never flips."""
    targets = {}
    for fi, name in FINGERS.items():
        em = bpy.data.objects.new(f"tgt.{name}", None)
        em.empty_display_size = 0.004
        bpy.context.collection.objects.link(em)
        pole = bpy.data.objects.new(f"pole.{name}", None)
        pole.empty_display_size = 0.004
        bpy.context.collection.objects.link(pole)
        pb = arm.pose.bones[chains[name]]
        con = pb.constraints.new("IK")
        con.target = em
        con.pole_target = pole
        con.chain_count = 3
        con.use_stretch = False
        targets[fi] = (em, pole)
    # Thumb: no chord target — damped-track it to a point BEHIND the
    # neck so it opposes the fingers and the board occludes it.
    tem = bpy.data.objects.new("tgt.thumb", None)
    tem.empty_display_size = 0.004
    bpy.context.collection.objects.link(tem)
    tcon = arm.pose.bones["thumb.0"].constraints.new("DAMPED_TRACK")
    tcon.target = tem
    targets["thumb"] = tem
    return targets

def pose_chord(arm, targets, chord, rest_curl):
    _ = rest_curl
    press = [t for t in CHORDS[chord].values() if t]
    cx = sum(finger_x(f) for _, f in press) / len(press)
    targets["thumb"].location = (cx + 0.015, 0.030, -0.015)
    for fi, name in FINGERS.items():
        tgt = CHORDS[chord].get(fi)
        em, pole = targets[fi]
        con = arm.pose.bones[f"{name}.2"].constraints[0]
        if tgt:
            s, fret = tgt
            x = finger_x(fret)
            z = string_z(s, abs(x))
            em.location = (x, 0.0015, z)   # pad on the string
            # Pole toward the viewer: PIP bows out of the board plane.
            mcp_w = arm.matrix_world @ arm.data.bones[f"{name}.0"].head_local
            pole.location = (x, -0.06, (mcp_w.z + z) / 2)
            con.influence = 1.0
        else:
            con.influence = 0.0            # relaxed rest = rig rest pose

def place_hand(arm, hand, chord):
    """Position the hand so the MIDDLE-finger MCP sits a few cm below
    the board's low edge, centred on the pressed cluster — the IK then
    bends fingers up onto the strings."""
    press = [t for t in CHORDS[chord].values() if t]
    cx = sum(finger_x(f) for _, f in press) / len(press)
    mcp = arm.data.bones["middle.0"].head_local
    want = Vector((cx, 0.012, -BOARD_W / 2 - 0.058))
    # Dorsal side to the camera, palm to the board, thumb swings behind
    # the neck toward the nut: 180° about Z. Hand mass sits on the
    # camera side (y<0); fingertips curl to the strings at y≈0.
    from mathutils import Matrix
    rot = Matrix.Rotation(math.pi, 4, "Z")
    want.y = -0.024
    loc = want - (rot @ mcp.to_4d()).to_3d()
    hand.location = (0, 0, 0)
    hand.rotation_euler = (0, 0, 0)
    arm.rotation_euler = (0, 0, math.pi)
    arm.location = loc
    print(f"PLACE {chord}: cx={cx:.4f} mcp_local={tuple(round(v,4) for v in mcp)} loc={tuple(round(v,4) for v in loc)}")

# ------------------------------------------------------------ render
def setup_render():
    scene = bpy.context.scene
    scene.render.engine = "BLENDER_EEVEE"
    scene.render.resolution_x = 1440
    scene.render.resolution_y = 840
    scene.render.film_transparent = False
    world = bpy.data.worlds.new("w")
    world.use_nodes = True
    world.node_tree.nodes["Background"].inputs[0].default_value = (0.03, 0.03, 0.045, 1)
    scene.world = world
    cam_data = bpy.data.cameras.new("cam")
    cam_data.type = "ORTHO"
    cam_data.ortho_scale = 0.55
    cam = bpy.data.objects.new("cam", cam_data)
    bpy.context.collection.objects.link(cam)
    cam.location = (-wire(WINDOW) / 2, -0.8, -0.04)
    cam.rotation_euler = (math.pi / 2, 0, 0)   # look +Y
    scene.camera = cam
    # Freestyle outline.
    scene.render.use_freestyle = True
    scene.render.line_thickness = 1.6
    vl = bpy.context.view_layer
    vl.use_freestyle = True
    ls = vl.freestyle_settings.linesets.new("outline")
    ls.select_silhouette = True
    ls.select_border = False
    ls.select_crease = False
    ls.linestyle.color = (0.85, 0.85, 0.9)
    # Flat dark hand material.
    hmat = bpy.data.materials.new("handmat")
    hmat.use_nodes = True
    nt = hmat.node_tree
    nt.nodes.clear()
    out = nt.nodes.new("ShaderNodeOutputMaterial")
    em = nt.nodes.new("ShaderNodeEmission")
    em.inputs["Color"].default_value = (0.012, 0.012, 0.02, 1)
    nt.links.new(em.outputs[0], out.inputs[0])
    return hmat

def export_pose(arm, chord, out):
    data = {}
    for pb in arm.pose.bones:
        m = arm.matrix_world @ pb.matrix
        data[pb.name] = {
            "head": list(m.translation),
            "quat": list(pb.matrix.to_quaternion()),
        }
    with open(f"{out}/pose-{chord}.json", "w") as f:
        json.dump(data, f, indent=1)

def main():
    hand = keep_only_hand()
    arm, chains = build_armature(hand)
    build_neck()
    hmat = setup_render()
    hand.data.materials.clear()
    hand.data.materials.append(hmat)
    targets = add_ik(arm, chains)
    import os
    os.makedirs(OUT, exist_ok=True)
    # Rest-pose sanity render: skinning must reproduce the mesh intact.
    place_hand(arm, hand, "G")
    for fi in FINGERS:
        targets[fi][0].location = (0, 0, -1)
        arm.pose.bones[f"{FINGERS[fi]}.2"].constraints[0].influence = 0.0
    bpy.context.view_layer.update()
    bpy.context.scene.render.filepath = f"{OUT}/rig-REST.png"
    bpy.ops.render.render(write_still=True)

    # Chord stills + keyframes for the transition animation: the IK
    # target empties and the armature location carry the animation —
    # the SKELETON moves, the silhouette follows.
    frame_of = {"G": 1, "D": 25, "Em": 50, "C": 75}
    for chord in ("G", "D", "Em", "C"):
        place_hand(arm, hand, chord)
        pose_chord(arm, targets, chord, rest_curl=(0.01, 0.02))
        bpy.context.view_layer.update()
        bpy.context.scene.render.filepath = f"{OUT}/rig-{chord}.png"
        bpy.ops.render.render(write_still=True)
        export_pose(arm, chord, OUT)
        f = frame_of[chord]
        arm.keyframe_insert("location", frame=f)
        for key, val in targets.items():
            obs = val if isinstance(val, tuple) else (val,)
            for ob in obs:
                ob.keyframe_insert("location", frame=f)
        for fi in FINGERS:
            con = arm.pose.bones[f"{FINGERS[fi]}.2"].constraints[0]
            con.keyframe_insert("influence", frame=f)
    # Mid-transition frames: skeletal interpolation between poses.
    for f in (13, 37, 62):
        bpy.context.scene.frame_set(f)
        bpy.context.view_layer.update()
        bpy.context.scene.render.filepath = f"{OUT}/anim-f{f:02d}.png"
        bpy.ops.render.render(write_still=True)
    bpy.ops.wm.save_as_mainfile(filepath=f"{OUT}/handrig.blend")
    print("DONE_RIG_PIPELINE")

main()
