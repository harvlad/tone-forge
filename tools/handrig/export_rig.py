# export_rig.py — export the rigged MPFB left hand (mesh + skeleton + skin
# weights) to USDZ for the in-app runtime 3D hand (SceneKit).
#
# Keeps the WHOLE hand incl. the thumb (3D depth handles occlusion at runtime,
# unlike the flat 2D render which hid it). Rest pose only — the app poses the
# bones each frame from solved joint angles.
#
# Run: blender -b --python export_rig.py -- <out.usdz>

import bpy, sys, os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import blender_mpfb_rig as R

argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
OUT = argv[0] if argv else "/tmp/hand.usdz"


def build_hand_with_thumb():
    """Same as R.build_human but the hand mask KEEPS the thumb (finger1)."""
    import addon_utils
    addon_utils.enable("bl_ext.user_default.mpfb", default_set=True)
    from bl_ext.user_default.mpfb.services.humanservice import HumanService
    HS = HumanService
    basemesh = HS.create_human(mask_helpers=False)   # no joint helper cubes in the export
    arm = HS.add_builtin_rig(basemesh, "default", import_weights=True)
    keep = {g.index for g in basemesh.vertex_groups
            if g.name.endswith(".L") and any(g.name.startswith(k) for k in
                ("finger", "wrist", "hand", "lowerarm", "metacarpal"))}
    mg = basemesh.vertex_groups.new(name="jamn_handmask")
    idxs = [v.index for v in basemesh.data.vertices
            if sum(ge.weight for ge in v.groups if ge.group in keep) > 0.25]
    mg.add(idxs, 1.0, "REPLACE")
    mask = basemesh.modifiers.new("JamnMask", "MASK")
    mask.vertex_group = "jamn_handmask"
    while basemesh.modifiers.find("JamnMask") > 0:
        bpy.ops.object.select_all(action="DESELECT")
        basemesh.select_set(True); bpy.context.view_layer.objects.active = basemesh
        bpy.ops.object.modifier_move_up(modifier="JamnMask")
    return basemesh, arm


basemesh, arm = build_hand_with_thumb()
# BAKE the mask into the mesh (USD export doesn't evaluate the modifier — without
# this it exports the whole body). Delete non-hand geometry destructively.
bpy.ops.object.select_all(action="DESELECT")
basemesh.select_set(True); bpy.context.view_layer.objects.active = basemesh
if basemesh.data.shape_keys:
    basemesh.shape_key_clear()          # MPFB shape keys block modifier_apply
bpy.ops.object.modifier_apply(modifier="JamnMask")

# MPFB leaves helper geometry in the base mesh that survives the weight-based
# mask: 23 free-floating 8-vertex "JointCubes" (one rigidly skinned to each
# finger-joint bone) plus a "HelperGeometry" tights chunk. At rest they hide
# at the joints, but each cube rigidly follows its bone and flings out as a
# floating box when the finger flexes — that is the "cube tear". They are all
# disconnected islands, so keep ONLY the largest connected island (the hand)
# and delete everything else. This removes the cubes while preserving the hand
# mesh, its skeleton and its skin weights.
def keep_largest_island(obj):
    import bmesh
    bm = bmesh.new()
    bm.from_mesh(obj.data)
    bm.verts.ensure_lookup_table()
    remaining = set(bm.verts)
    best = None
    while remaining:
        seed = next(iter(remaining))
        stack = [seed]; comp = set()
        while stack:
            v = stack.pop()
            if v in comp:
                continue
            comp.add(v)
            for e in v.link_edges:
                ov = e.other_vert(v)
                if ov not in comp:
                    stack.append(ov)
        remaining -= comp
        if best is None or len(comp) > len(best):
            best = comp
    to_delete = [v for v in bm.verts if v not in best]
    print("keep_largest_island: kept", len(best), "verts, deleted",
          len(to_delete), "stray verts (JointCubes/HelperGeometry)")
    bmesh.ops.delete(bm, geom=to_delete, context="VERTS")
    bm.to_mesh(obj.data)
    bm.free()
    obj.data.update()

keep_largest_island(basemesh)
# Normalize skin weights on the remaining hand so LBS is well-behaved after the
# stray-geometry cull (weights that referenced deleted groups no longer skew).
bpy.ops.object.select_all(action="DESELECT")
basemesh.select_set(True); bpy.context.view_layer.objects.active = basemesh
bpy.ops.object.vertex_group_normalize_all(lock_active=False)

# The MPFB base hand is coarse (~1.8k polys), so hard finger flex collapses the
# tube cross-section at the joints ("candy-wrapper" LBS pinch). One Catmull-Clark
# subdivision level densifies the mesh (weights interpolate to the new verts),
# giving a smooth finger curl at the joints instead of pinched shards.
subsurf = basemesh.modifiers.new("JamnSubsurf", "SUBSURF")
subsurf.levels = 1
subsurf.render_levels = 1
bpy.ops.object.modifier_apply(modifier="JamnSubsurf")

# Orient into the fretting pose basis so the exported rest pose already faces
# the way the app expects (same rotation the solver's root uses).
arm.rotation_euler = R.fretting_rotation(arm).to_euler()
bpy.context.view_layer.update()

bpy.ops.object.select_all(action="DESELECT")
basemesh.select_set(True); arm.select_set(True)
bpy.context.view_layer.objects.active = arm

os.makedirs(os.path.dirname(OUT) or ".", exist_ok=True)
bpy.ops.wm.usd_export(
    filepath=OUT,
    selected_objects_only=True,
    export_animation=False,
    export_materials=True,
    export_armatures=True,
    root_prim_path="/hand",
)
print("EXPORTED", OUT, os.path.getsize(OUT) if os.path.exists(OUT) else "MISSING")
