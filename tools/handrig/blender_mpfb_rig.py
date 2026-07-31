# blender_mpfb_rig.py — JAMN fretting-hand reference pipeline, MPFB edition.
#
# MakeHuman (MPFB2, CC0 exports) human with the built-in "default" rig
# and PROFESSIONAL finger weights replaces the hand-rolled skinning of
# blender_hand_rig.py. Everything else is unchanged in spirit:
#
#   chords.json (exported from GuitarVoicing/ChordFingering — never
#   hand-typed) → physical neck → per-finger IK → silhouette renders +
#   debug renders (target dots + fingertip markers) + pose JSON export.
#
# Run:
#   Blender --background --python blender_mpfb_rig.py -- /out/dir chords.json
#
# Guitar space: origin at the nut, board along -X (nut RIGHT), strings
# in the XZ plane at y=0, low E TOP. Camera looks +Y; thumb goes
# BEHIND the neck (y>0) and is occluded.

import bpy, json, math, sys
from mathutils import Vector, Matrix, Euler

argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
OUT = argv[0] if len(argv) > 0 else "/tmp/mpfbrig"
CHORDS_PATH = argv[1] if len(argv) > 1 else "chords.json"
# Optional pre-solved states (from solver/solve_chords.py). When given, each
# chord is POSED by applying its solved joint angles (natural comfort/strain
# solver) instead of this file's naive Blender IK — then exported as usual.
STATES_PATH = argv[2] if len(argv) > 2 else None
try:
    STATES = json.load(open(STATES_PATH)) if STATES_PATH else {}
except Exception:
    STATES = {}   # e.g. when imported by mpfb_render with an unrelated argv

SCALE_LEN = 0.648
NECK_THICK = 0.021
STRING_SPAN_NUT = 0.035
STRING_SPAN_12 = 0.0435
BOARD_W = 0.052
WINDOW = 7

def wire(n):
    return SCALE_LEN * (1 - 2 ** (-n / 12))

def finger_x(fret):
    lo, hi = wire(fret - 1), wire(fret)
    return -(hi - (hi - lo) * 0.30)

def string_z(s, x_abs):
    t = min(1.0, x_abs / (SCALE_LEN / 2))
    span = STRING_SPAN_NUT + (STRING_SPAN_12 - STRING_SPAN_NUT) * t
    return (2.5 - s) * span / 5

def load_chords(path):
    with open(path) as f:
        raw = json.load(f)
    chords = {}
    barres = {}
    for symbol, entry in raw.items():
        m = {1: None, 2: None, 3: None, 4: None}
        for n in entry["notes"]:
            m[n["finger"]] = (n["string"], n["fret"])
        if "barre" in entry:
            b = entry["barre"]
            # Index lies flat across the strings: tip at the TOP
            # (lowest-index) barred string.
            m[1] = (b["loString"], b["fret"])
            barres[symbol] = b
        chords[symbol] = m
    return chords, barres

try:
    CHORDS, BARRES = load_chords(CHORDS_PATH)
except (FileNotFoundError, IsADirectoryError, OSError):
    CHORDS, BARRES = {}, {}   # importable without a chords arg (bridge reuse)

# Musical finger → MakeHuman digit (fixed anatomy, left hand).
FINGER_BONE = {1: "finger2", 2: "finger3", 3: "finger4", 4: "finger5"}

# ------------------------------------------------------------- human
def build_human():
    import addon_utils
    addon_utils.enable("bl_ext.user_default.mpfb", default_set=True)
    from bl_ext.user_default.mpfb.services.humanservice import HumanService
    HS = HumanService
    basemesh = HS.create_human(mask_helpers=True)
    arm = HS.add_builtin_rig(basemesh, "default", import_weights=True)
    # Show ONLY the left hand + forearm: mask by summed bone weights.
    keep = {g.index: g.name for g in basemesh.vertex_groups
            if g.name.endswith(".L") and any(
                g.name.startswith(k) for k in
                ("finger", "wrist", "hand", "lowerarm", "metacarpal"))}
    mg = basemesh.vertex_groups.new(name="jamn_handmask")
    idxs = []
    for v in basemesh.data.vertices:
        w = sum(ge.weight for ge in v.groups if ge.group in keep)
        if w > 0.25:
            idxs.append(v.index)
    mg.add(idxs, 1.0, "REPLACE")
    mask = basemesh.modifiers.new("JamnMask", "MASK")
    mask.vertex_group = "jamn_handmask"
    # Mask must run BEFORE the armature deform.
    while basemesh.modifiers.find("JamnMask") > 0:
        bpy.ops.object.select_all(action="DESELECT")
        basemesh.select_set(True)
        bpy.context.view_layer.objects.active = basemesh
        bpy.ops.object.modifier_move_up(modifier="JamnMask")
    return basemesh, arm

def add_ik(arm):
    """IK per left-hand finger distal bone + pole empties."""
    targets = {}
    for fi, digit in FINGER_BONE.items():
        em = bpy.data.objects.new(f"tgt.{digit}", None)
        em.empty_display_size = 0.004
        bpy.context.collection.objects.link(em)
        pole = bpy.data.objects.new(f"pole.{digit}", None)
        pole.empty_display_size = 0.004
        bpy.context.collection.objects.link(pole)
        pb = arm.pose.bones[f"{digit}-3.L"]
        con = pb.constraints.new("IK")
        con.target = em
        con.pole_target = pole
        con.chain_count = 3
        con.use_stretch = False
        targets[fi] = (em, pole)
    # Thumb: track behind the neck.
    tem = bpy.data.objects.new("tgt.thumb", None)
    tem.empty_display_size = 0.004
    bpy.context.collection.objects.link(tem)
    pb = arm.pose.bones["finger1-3.L"]
    con = pb.constraints.new("IK")
    con.target = tem
    con.chain_count = 3
    con.use_stretch = False
    targets["thumb"] = tem
    return targets

# ------------------------------------------------------------- guitar
def build_neck():
    smat = bpy.data.materials.new("stringmat")
    smat.use_nodes = True
    nt = smat.node_tree
    nt.nodes.clear()
    o = nt.nodes.new("ShaderNodeOutputMaterial")
    e = nt.nodes.new("ShaderNodeEmission")
    e.inputs["Color"].default_value = (0.55, 0.55, 0.6, 1)
    e.inputs["Strength"].default_value = 1.0
    nt.links.new(e.outputs[0], o.inputs[0])
    nmat = bpy.data.materials.new("neckmat")
    nmat.use_nodes = True
    nmat.node_tree.nodes["Principled BSDF"].inputs["Base Color"].default_value = (0.05, 0.035, 0.025, 1)
    span = wire(WINDOW)
    bpy.ops.mesh.primitive_cube_add()
    board = bpy.context.object
    board.scale = (span / 2, NECK_THICK / 2, BOARD_W / 2)
    board.location = (-span / 2, 0.004 + NECK_THICK / 2, 0)
    bpy.ops.object.transform_apply(scale=True)
    board.data.materials.append(nmat)
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
def mcp_offsets(arm, rot):
    mid = arm.data.bones["finger3-1.L"].head_local
    offs = {}
    for fi, digit in FINGER_BONE.items():
        d = arm.data.bones[f"{digit}-1.L"].head_local - mid
        offs[fi] = (rot @ d.to_4d()).to_3d().x
    return offs

def fretting_rotation(arm):
    """Rotation mapping the rig's rest left hand into fretting
    orientation, derived from BONE GEOMETRY (no guessed eulers):
      knuckle line index→pinky  → -X  (index at the nut)
      palmar normal             → +Y  (palm toward the board)
      middle finger direction   → +Z  (fingers up)
    """
    b = arm.data.bones
    d = (b["finger3-3.L"].tail_local - b["finger3-1.L"].head_local).normalized()
    k = (b["finger5-1.L"].head_local - b["finger2-1.L"].head_local).normalized()
    # n = k×d keeps the basis RIGHT-handed; for the left hand in this
    # rest pose that IS the palmar side (a thumb-based sign flip makes
    # the basis improper and the rotation a silent reflection).
    n = k.cross(d).normalized()
    k = d.cross(n).normalized()
    L = Matrix((k, n, d)).transposed()     # columns = local basis
    W = Matrix(((-1, 0, 0), (0, 1, 0), (0, 0, 1))).transposed()
    R3 = W @ L.inverted()
    assert abs(R3.determinant() - 1.0) < 1e-3, f"improper rotation det={R3.determinant()}"
    return R3.to_4x4()

def place_hand(arm, chord, rot_euler, lift=0.0):
    _ = rot_euler
    rot = fretting_rotation(arm)
    offs = mcp_offsets(arm, rot)
    barre = chord in BARRES
    terms = []
    for fi in (1, 2, 3, 4):
        tgt = CHORDS[chord].get(fi)
        # Anchor a barre on the ARCHING fingers (2..4) only; the index
        # lies flat and shouldn't drag the cluster centre.
        if tgt and not (barre and fi == 1):
            terms.append(finger_x(tgt[1]) - offs[fi])
    cx = sum(terms) / len(terms)
    mcp = arm.data.bones["finger3-1.L"].head_local
    # Barre: the hand sits deeper/lower so fingers 2..4 arch steeply
    # over the flat index onto the higher frets.
    depthY = -0.030 if barre else -0.024
    dropZ = -0.070 if barre else -0.058
    want = Vector((cx, depthY, -BOARD_W / 2 + dropZ + lift))
    loc = want - (rot @ mcp.to_4d()).to_3d()
    arm.rotation_euler = rot.to_euler()
    arm.location = loc

def pose_chord(arm, targets, chord):
    barre = chord in BARRES
    press = [t for fi, t in CHORDS[chord].items() if t and not (barre and fi == 1)]
    cx = sum(finger_x(f) for _, f in press) / len(press)
    targets["thumb"].location = (cx + 0.018, 0.032, -0.020)
    for fi in (1, 2, 3, 4):
        digit = FINGER_BONE[fi]
        tgt = CHORDS[chord].get(fi)
        em, pole = targets[fi]
        con = arm.pose.bones[f"{digit}-3.L"].constraints[-1]
        if barre and fi == 1:
            # Flat barre index: lies ACROSS the strings at the barre
            # fret. Target the LOW-E (top) string so the extended
            # finger reaches full length (near-straight), pole pushed
            # in FRONT so the slight bend bows toward the viewer, never
            # sideways into the other fingers.
            b = BARRES[chord]
            x = finger_x(b["fret"])
            z = string_z(b["loString"], abs(x))
            em.location = (x, 0.002, z)
            pole.location = (x, -0.12, z + 0.02)
            con.influence = 1.0
        elif tgt:
            s, fret = tgt
            x = finger_x(fret)
            z = string_z(s, abs(x))
            em.location = (x, 0.0015, z)
            mcp_w = arm.matrix_world @ arm.data.bones[f"{digit}-1.L"].head_local
            # Pole consistently in FRONT and BELOW the MCP so every
            # finger bends the same way (toward the viewer / down) —
            # prevents the elbow-flip that crossed fingers on barres.
            pole.location = (x, -0.08, mcp_w.z - 0.02)
            con.influence = 1.0
        else:
            con.influence = 0.0

def apply_state(arm, state, f):
    """Apply a pre-solved MPFB state vector (joint angles + root xform) to the
    rig 1:1 (like mpfb_render.apply) and keyframe it at frame f. Bypasses the
    naive IK entirely — the pose is whatever the comfort/strain solver produced."""
    v = state["state"]
    fret_rot = fretting_rotation(arm)
    M = fret_rot @ Euler(tuple(v[3:6]), "XYZ").to_matrix().to_4x4()
    arm.location = Vector(v[0:3])
    arm.rotation_euler = M.to_euler()
    arm.keyframe_insert("location", frame=f)
    arm.keyframe_insert("rotation_euler", frame=f)
    META_BONE = ["metacarpal1.L", "metacarpal2.L", "metacarpal3.L", "metacarpal4.L"]
    meta = v[26:30] if len(v) >= 30 else [0, 0, 0, 0]
    def setkey(bn, e):
        pb = arm.pose.bones[bn]; pb.rotation_mode = "XYZ"
        pb.rotation_euler = Euler(e, "XYZ"); pb.keyframe_insert("rotation_euler", frame=f)
    for i in range(4):
        mcpF, mcpA, pip, dip = v[6 + i*4:6 + i*4 + 4]
        d = FINGER_BONE[i + 1]
        setkey(META_BONE[i], (meta[i], 0, 0))
        setkey(f"{d}-1.L", (mcpF, 0, mcpA)); setkey(f"{d}-2.L", (pip, 0, 0)); setkey(f"{d}-3.L", (dip, 0, 0))
    mcpF, mcpA, ip, _ = v[22:26]
    setkey("finger1-1.L", (mcpF, 0, mcpA)); setkey("finger1-2.L", (ip, 0, 0)); setkey("finger1-3.L", (ip * 0.6, 0, 0))


# ------------------------------------------------------------- render
def setup_render(basemesh):
    scene = bpy.context.scene
    scene.render.engine = "BLENDER_EEVEE"
    scene.render.resolution_x = 1440
    scene.render.resolution_y = 840
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
    cam.rotation_euler = (math.pi / 2, 0, 0)
    scene.camera = cam
    scene.render.use_freestyle = True
    scene.render.line_thickness = 1.1
    vl = bpy.context.view_layer
    vl.use_freestyle = True
    ls = vl.freestyle_settings.linesets.new("outline")
    ls.select_silhouette = True
    ls.select_border = False
    ls.select_crease = False
    ls.linestyle.color = (0.85, 0.85, 0.9)
    hmat = bpy.data.materials.new("handmat")
    hmat.use_nodes = True
    nt = hmat.node_tree
    nt.nodes.clear()
    out = nt.nodes.new("ShaderNodeOutputMaterial")
    em = nt.nodes.new("ShaderNodeEmission")
    em.inputs["Color"].default_value = (0.13, 0.135, 0.17, 1)   # light pose-sheet grey (was 0.012 dark)
    nt.links.new(em.outputs[0], out.inputs[0])
    basemesh.data.materials.clear()
    basemesh.data.materials.append(hmat)
    # Smooth the mesh like mpfb_render (the clean phrase-sheet look): relaxes the
    # facets so Freestyle traces a clean silhouette instead of every wrinkle.
    sm = basemesh.modifiers.new("JamnSmooth", "SMOOTH"); sm.factor = 0.6; sm.iterations = 8
    for p in basemesh.data.polygons:
        p.use_smooth = True

def emissive(name, col, strength=4):
    m = bpy.data.materials.new(name)
    m.use_nodes = True
    nt = m.node_tree
    nt.nodes.clear()
    o = nt.nodes.new("ShaderNodeOutputMaterial")
    e = nt.nodes.new("ShaderNodeEmission")
    e.inputs["Color"].default_value = (*col, 1)
    e.inputs["Strength"].default_value = strength
    nt.links.new(e.outputs[0], o.inputs[0])
    return m

def main():
    basemesh, arm = build_human()
    build_neck()
    setup_render(basemesh)
    targets = add_ik(arm)

    import os
    os.makedirs(OUT, exist_ok=True)

    # Orientation: MakeHuman T-pose, left arm along +X, fingers +X,
    # palm down (-Z). Yaw/pitch/roll chosen so fingers point UP with
    # the dorsal side to the camera; iterate via ORIENT renders.
    ROT = (0, math.radians(-90), 0)

    dotmat = emissive("dotmat", (0.1, 0.6, 1.0))
    dots = []
    for _ in range(4):
        bpy.ops.mesh.primitive_uv_sphere_add(radius=0.004)
        d = bpy.context.object
        d.data.materials.append(dotmat)
        d.hide_render = True
        dots.append(d)

    tailmats = {1: emissive("tm1", (0, 1, 0.2)), 2: emissive("tm2", (1, 1, 0)),
                3: emissive("tm3", (1, 0.5, 0)), 4: emissive("tm4", (1, 0, 0))}
    tails = {}
    for fi in (1, 2, 3, 4):
        bpy.ops.mesh.primitive_uv_sphere_add(radius=0.0025)
        tm = bpy.context.object
        tm.data.materials.append(tailmats[fi])
        tm.hide_render = True
        tails[fi] = tm

    def show_dots(chord, on):
        i = 0
        for fi in (1, 2, 3, 4):
            tgt = CHORDS[chord].get(fi)
            if not tgt: continue
            s, fret = tgt
            x = finger_x(fret)
            dots[i].location = (x, -0.004, string_z(s, abs(x)))
            dots[i].hide_render = not on
            i += 1
        for j in range(i, 4):
            dots[j].hide_render = True

    def show_tails(on):
        for fi, tm in tails.items():
            if on:
                pb = arm.pose.bones[f"{FINGER_BONE[fi]}-3.L"]
                tail = arm.matrix_world @ pb.matrix @ Vector((0, pb.length, 0))
                tm.location = (tail.x, -0.006, tail.z)
            tm.hide_render = not on

    order = ["G", "D", "Em", "C", "A", "Am", "E", "Dm"]
    order += sorted(c for c in CHORDS if c not in order)
    frame_of = {c: 1 + 25 * i for i, c in enumerate(order)}
    LIBRARY = {}

    def keyframe_all(f):
        arm.keyframe_insert("location", frame=f)
        arm.keyframe_insert("rotation_euler", frame=f)
        for val in targets.values():
            obs = val if isinstance(val, tuple) else (val,)
            for ob in obs:
                ob.keyframe_insert("location", frame=f)
        for fi in (1, 2, 3, 4):
            con = arm.pose.bones[f"{FINGER_BONE[fi]}-3.L"].constraints[-1]
            con.keyframe_insert("influence", frame=f)

    def tip_errors(chord):
        errs = {}
        for fi in (1, 2, 3, 4):
            tgt = CHORDS[chord].get(fi)
            if not tgt: continue
            pb = arm.pose.bones[f"{FINGER_BONE[fi]}-3.L"]
            tail = arm.matrix_world @ pb.matrix @ Vector((0, pb.length, 0))
            em = targets[fi][0]
            errs[fi] = (Vector(em.location) - tail).length
        return errs

    for chord in order:
        f = frame_of[chord]
        bpy.context.scene.frame_set(f)
        if chord in STATES:
            # Natural pose from the comfort/strain solver — disable IK, apply the
            # solved joint angles, keyframe. No adaptive re-solve needed.
            for fi in (1, 2, 3, 4):
                arm.pose.bones[f"{FINGER_BONE[fi]}-3.L"].constraints[-1].influence = 0.0
            apply_state(arm, STATES[chord], f)
            keyframe_all(f)
            bpy.context.scene.frame_set(f)
            bpy.context.view_layer.update()
            print(f"STATE {chord} applied")
        else:
            # Adaptive convergence: residual tip error means the knuckle
            # row sits too deep for this chord's reach — raise the hand by
            # the worst error and re-solve (keyframes rewritten in place;
            # keyframe BEFORE evaluating or curves override the pose).
            lift = 0.0
            for _ in range(4):
                place_hand(arm, chord, ROT, lift=lift)
                pose_chord(arm, targets, chord)
                keyframe_all(f)
                bpy.context.scene.frame_set(f)
                bpy.context.view_layer.update()
                errs = tip_errors(chord)
                worst = max(errs.values()) if errs else 0.0
                if worst < 0.0015:
                    break
                lift += worst * 0.85
            for fi, err in tip_errors(chord).items():
                print(f"TIPERR {chord} f{fi}: {err*1000:.1f}mm")

        show_dots(chord, False)
        show_tails(False)
        bpy.context.scene.render.filepath = f"{OUT}/rig-{chord}.png"
        bpy.ops.render.render(write_still=True)
        show_dots(chord, True)
        show_tails(True)
        bpy.context.scene.render.filepath = f"{OUT}/debug-{chord}.png"
        bpy.ops.render.render(write_still=True)
        show_dots(chord, False)
        show_tails(False)

        # Pose export for the app.
        def world(v):
            return list(arm.matrix_world @ v)
        fingers = {}
        for fi, digit in FINGER_BONE.items():
            pbs = [arm.pose.bones[f"{digit}-{s}.L"] for s in (1, 2, 3)]
            joints = [list(arm.matrix_world @ pb.matrix.translation) for pb in pbs]
            tail = arm.matrix_world @ pbs[2].matrix @ Vector((0, pbs[2].length, 0))
            joints.append(list(tail))
            fingers[str(fi)] = joints
        press = [t for t in CHORDS[chord].values() if t]
        cxx = sum(finger_x(fr) for _, fr in press) / len(press)
        LIBRARY[chord] = {
            "fingers": fingers,
            "wrist": list(arm.matrix_world @ arm.pose.bones["wrist.L"].matrix.translation),
            "thumbTip": list(arm.matrix_world @ arm.pose.bones["finger1-3.L"].matrix.translation),
            "clusterX": cxx,
            "boardBottomZ": -BOARD_W / 2,
        }
        with open(f"{OUT}/handposes.json", "w") as fjson:
            json.dump(LIBRARY, fjson, indent=1)

    # --- Silhouette contour export -------------------------------
    # Per chord: render a hand-only mask and trace its contours into
    # guitar-space polygon rings — the app fills these directly, so
    # the in-app hand IS the projected MPFB mesh, not an approximation.
    def render_mask(chord):
        f = frame_of[chord]
        bpy.context.scene.frame_set(f)
        bpy.context.view_layer.update()
        scene = bpy.context.scene
        hidden = []
        board = bpy.data.objects.get("Cube")
        for o in bpy.data.objects:
            if o.type in ("MESH",) and o is not basemesh and o is not board \
               and not o.hide_render:
                hidden.append(o)
                o.hide_render = True
        # Board stays as a HOLDOUT: it cuts the thumb (behind the neck)
        # out of the alpha mask exactly as it occludes it in render.
        if board:
            board.is_holdout = True
        old_fs = scene.render.use_freestyle
        scene.render.use_freestyle = False
        scene.render.film_transparent = True
        path = f"{OUT}/mask-{chord}.png"
        scene.render.filepath = path
        bpy.ops.render.render(write_still=True)
        for o in hidden:
            o.hide_render = False
        if board:
            board.is_holdout = False
        scene.render.use_freestyle = old_fs
        scene.render.film_transparent = False
        return path

    def trace_contours(path):
        img = bpy.data.images.load(path)
        w, h = img.size
        px = list(img.pixels)  # RGBA float rows bottom-up
        def solid(x, y):
            if x < 0 or y < 0 or x >= w or y >= h: return False
            return px[(y * w + x) * 4 + 3] > 0.5
        # Marching-squares boundary walk on cell corners.
        visited = set()
        rings = []
        for yy in range(h - 1):
            for xx in range(w - 1):
                a, bcell = solid(xx, yy), solid(xx + 1, yy)
                if a == bcell or (xx, yy) in visited:
                    continue
                # Trace boundary starting between (xx,yy) and (xx+1,yy)
                ring = []
                # Simple pixel-boundary follow (Moore tracing).
                start = None
                sx, sy = (xx + 1, yy) if bcell else (xx, yy)
                if not solid(sx, sy):
                    continue
                start = (sx, sy)
                cur = start
                back = (sx - 1, sy)
                moore = [(-1,-1),(0,-1),(1,-1),(1,0),(1,1),(0,1),(-1,1),(-1,0)]
                for _ in range(w * h):
                    if cur in visited and cur == start and ring:
                        break
                    visited.add(cur)
                    ring.append(cur)
                    bi = moore.index((back[0]-cur[0], back[1]-cur[1]))
                    nxt = None
                    for k in range(1, 9):
                        d = moore[(bi + k) % 8]
                        cand = (cur[0] + d[0], cur[1] + d[1])
                        if solid(*cand):
                            nxt = cand
                            back = (cur[0] + moore[(bi + k - 1) % 8][0],
                                    cur[1] + moore[(bi + k - 1) % 8][1])
                            break
                    if nxt is None or (nxt == start and len(ring) > 2):
                        break
                    cur = nxt
                if len(ring) > 40:
                    rings.append(ring)
        bpy.data.images.remove(img)
        # Largest ring only (outer silhouette); RDP simplify.
        rings.sort(key=len, reverse=True)
        out = []
        for ring in rings[:1]:
            out.append(rdp([(float(x), float(y)) for x, y in ring], 0.9))
        return out, w, h

    def rdp(pts, eps):
        if len(pts) < 3: return pts
        def simplify(lo, hi):
            dmax, idx = 0.0, lo
            ax, ay = pts[lo]; bx, by = pts[hi]
            dx, dy = bx - ax, by - ay
            norm = max(1e-9, (dx*dx + dy*dy) ** 0.5)
            for i in range(lo + 1, hi):
                pxx, pyy = pts[i]
                d = abs(dy*pxx - dx*pyy + bx*ay - by*ax) / norm
                if d > dmax: dmax, idx = d, i
            if dmax > eps:
                return simplify(lo, idx)[:-1] + simplify(idx, hi)
            return [pts[lo], pts[hi]]
        import sys as _s
        _s.setrecursionlimit(10000)
        return simplify(0, len(pts) - 1)

    # Hand SPRITE per chord: transparent render of ONLY the hand with
    # Freestyle rim + interior lines, board as holdout (thumb clipped
    # by the neck exactly as occluded). This is the exact Blender look
    # the app composites, with anchor metadata for re-projection.
    # Occluder slab: everything BEHIND the neck (thumb) is clipped
    # from sprites; fingers in front of the board stay visible (the
    # app layers wood -> hand -> strings).
    bpy.ops.mesh.primitive_cube_add()
    slab = bpy.context.object
    slab.name = "jamn_slab"
    slab.scale = (2, 0.5, 1)
    slab.location = (0, 0.5 + 0.024, 0)
    bpy.ops.object.transform_apply(scale=True)
    slab.hide_render = True

    def render_sprite(chord):
        f = frame_of[chord]
        bpy.context.scene.frame_set(f)
        bpy.context.view_layer.update()
        scene = bpy.context.scene
        board = bpy.data.objects.get("Cube")
        hidden = []
        for o in bpy.data.objects:
            if o.type == "MESH" and o is not basemesh and o.name != "jamn_slab" \
               and not o.hide_render:
                hidden.append(o)
                o.hide_render = True
        slab.hide_render = False
        slab.is_holdout = True
        scene.render.film_transparent = True
        scene.render.filepath = f"{OUT}/sprite-{chord}.png"
        bpy.ops.render.render(write_still=True)
        for o in hidden:
            o.hide_render = False
        slab.hide_render = True
        slab.is_holdout = False
        scene.render.film_transparent = False

    cam = bpy.context.scene.camera
    ortho = cam.data.ortho_scale
    rx, ry = 1440, 840
    for chord in order:
        render_sprite(chord)
        LIBRARY[chord]["sprite"] = {
            "camX": cam.location.x, "camZ": cam.location.z,
            "ortho": ortho, "w": rx, "h": ry,
        }
        path = render_mask(chord)
        rings, w, h = trace_contours(path)
        world_rings = []
        for ring in rings:
            wr = []
            for (pxx, pyy) in ring:
                xm = cam.location.x + (pxx / w - 0.5) * ortho
                zm = cam.location.z + (pyy / h - 0.5) * ortho * (ry / rx)
                wr.append([round(xm, 5), round(zm, 5)])
            world_rings.append(wr)
        LIBRARY[chord]["outline"] = world_rings
        print(f"OUTLINE {chord}: rings={[len(r) for r in world_rings]}")
    with open(f"{OUT}/handposes.json", "w") as fjson:
        json.dump(LIBRARY, fjson, indent=1)

    for f in (13, 37, 62):
        bpy.context.scene.frame_set(f)
        bpy.context.view_layer.update()
        bpy.context.scene.render.filepath = f"{OUT}/anim-f{f:02d}.png"
        bpy.ops.render.render(write_still=True)

    bpy.ops.wm.save_as_mainfile(filepath=f"{OUT}/mpfbrig.blend")
    print("DONE_MPFB_PIPELINE")


if __name__ == "__main__":
    main()
