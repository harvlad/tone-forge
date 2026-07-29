"""Phase 3-6 — biomechanical solver on the MPFB skeleton (unified).

There is ONE hand: the MPFB rig. The solver optimizes MPFB joint state
using the FK validated (0.0002 mm) against Blender, so the fingertips it
reasons about ARE the rendered fingertips. No bridge, no angle transfer,
no per-finger correction.

State (29): root_loc(3) + root_rot(3) + per finger [MCP flex, MCP abd,
PIP, DIP]×4 (16) + thumb [MCP flex, MCP abd, IP, extra](4). Finger DoF
map onto MPFB bone eulers exactly as the FK consumes them:
  finger{d}-1 euler = (flex, 0, abd)   (+flex curls onto the board)
  finger{d}-2 euler = (pip, 0, 0)
  finger{d}-3 euler = (dip, 0, 0)

Reuses the validated physiological pieces: contact REGIONS, fretting-
ready neutral strain, synergy manifold, splay/coupling, collision. No
MANO data. No chord logic.
"""
from __future__ import annotations
import sys, os, time, json
import numpy as np
from scipy.optimize import minimize

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import mpfb_fk
import anatomy as A
import guitar as G
import contacts as CN
from synergy import SynergyBasis

BASIS = SynergyBasis()
M2MM = 1000.0

CONTACT_TOL_MM = 6.0
COLLISION_TOL_MM = 2.0
BOARD_TOL_MM = 3.0
FINGER_RADIUS_MM = 4.5

FINGERS = A.FINGERS   # index, middle, ring, pinky
FMAP = {"index": "finger2", "middle": "finger3", "ring": "finger4",
        "pinky": "finger5", "thumb": "finger1"}

# State layout.
RLOC = slice(0, 3)
RROT = slice(3, 6)
FING = slice(6, 22)          # 4 fingers × 4
THUMB = slice(22, 26)
META = slice(26, 30)         # metacarpal cupping (index..pinky), rx each
STATE_N = 30

# Metacarpal→finger and the metacarpal bone names.
META_BONE = ["metacarpal1.L", "metacarpal2.L", "metacarpal3.L", "metacarpal4.L"]

W = dict(contact=8.0, board=40.0, collision=30.0, strain=2.4, splay=2.0,
         coupling=1.4, thumb=2.0, wrist=0.4, manifold=2.2, move=0.5, root=0.02,
         arch=2.5, conform=1.5, individ=0.8)


def _euler_m(rx, ry, rz):
    cx, sx = np.cos(rx), np.sin(rx); cy, sy = np.cos(ry), np.sin(ry)
    cz, sz = np.cos(rz), np.sin(rz)
    Rx = np.array([[1, 0, 0], [0, cx, -sx], [0, sx, cx]])
    Ry = np.array([[cy, 0, sy], [0, 1, 0], [-sy, 0, cy]])
    Rz = np.array([[cz, -sz, 0], [sz, cz, 0], [0, 0, 1]])
    return Rz @ Ry @ Rx


class MPFBSolver:
    def __init__(self, rig_path):
        self.hand = mpfb_fk.MPFBHand(rig_path)
        d = json.load(open(rig_path))
        self.fret_rot = np.array(d["fretting_rotation"])       # 4x4
        self.mid_mcp = np.array(d["mid_mcp_rest_local"])       # m

    def armature_world(self, root_loc, root_rot):
        """T(root_loc) @ base-fretting-rot @ small-offset-rot. root_loc
        in metres; root_rot small euler offset on top of the fretting
        orientation."""
        R_off = np.eye(4); R_off[:3, :3] = _euler_m(*root_rot)
        M = self.fret_rot @ R_off
        M[:3, 3] = root_loc
        return M

    # ---- FK: finger tips + full joint chains in guitar-mm space ----
    def finger_joints_mm(self, digit_name, four_dof, AW, meta_cup=0.0):
        mcpF, mcpA, pip, dip = four_dof
        angles = [(mcpF, 0.0, mcpA), (pip, 0.0, 0.0), (dip, 0.0, 0.0)]
        idx = ["index", "middle", "ring", "pinky"].index(digit_name)
        js = self.hand.finger_fk(FMAP[digit_name], angles, AW,
                                 meta_name=META_BONE[idx],
                                 meta_angle=(meta_cup, 0.0, 0.0))
        return [np.array(p) * M2MM for p in js]

    def thumb_tip_mm(self, thumb_dof, AW):
        mcpF, mcpA, ip, _ = thumb_dof
        angles = [(mcpF, 0.0, mcpA), (ip, 0.0, 0.0), (ip * 0.6, 0.0, 0.0)]
        js = self.hand.finger_fk("finger1", angles, AW)
        return np.array(js[-1]) * M2MM, [np.array(p) * M2MM for p in js]

    # ---- costs ----
    def evaluate(self, v, contact_list, prev):
        root_loc = v[RLOC]; root_rot = v[RROT]
        AW = self.armature_world(root_loc, root_rot)
        fvec = v[FING]; meta = v[META]
        fingers = {}
        for i, f in enumerate(FINGERS):
            fingers[f] = self.finger_joints_mm(f, fvec[i*4:i*4+4], AW,
                                               meta_cup=meta[i])
        thumb_tip, thumb_js = self.thumb_tip_mm(v[THUMB], AW)

        cb = {}
        # Contact regions (tip = last joint).
        ce = 0.0
        for c in contact_list:
            tip = fingers[c.finger][-1]
            fk = type("fk", (), {"tip": tip})
            ce += (2.2 if c.kind == "line" else 1.0) * c.residual(fk)
        cb["contact"] = ce
        # Board / neck penetration on every finger joint.
        bp = 0.0
        for f in FINGERS:
            for p in fingers[f]:
                bp += G.penetrates_board(p) ** 2
        cb["board"] = bp
        # Finger/finger collision (adjacent mid segments).
        coll = 0.0
        order = FINGERS
        for i in range(len(order) - 1):
            a = fingers[order[i]][1:]; b = fingers[order[i+1]][1:]
            dmin = min(np.linalg.norm(pa - pb) for pa in a for pb in b)
            ov = 2 * FINGER_RADIUS_MM - dmin
            if ov > 0: coll += ov * ov
        cb["collision"] = coll
        # Physiological finger costs (angle-space).
        st = self._as_state(v)
        cb["strain"] = A.strain_penalty(st)
        cb["splay"] = A.splay_penalty(st)
        cb["coupling"] = A.coupling_penalty(st)
        cb["manifold"] = self._manifold(v[FING])
        # Thumb: behind neck, opposing the middle finger.
        midx = fingers["middle"][0][0]
        tgt = np.array([midx, G.NECK_THICK, 0.0])
        cb["thumb"] = float(np.sum((thumb_tip - tgt) ** 2)) * 0.01
        short = G.NECK_THICK - thumb_tip[1]
        if short > 0: cb["thumb"] += (short / 10.0) ** 2 * 1.5
        cb["wrist"] = root_rot[2] ** 2 * 0.5

        # --- PALM / METACARPAL PASS ---
        meta = v[META]
        # Transverse metacarpal arch (documented): the ulnar metacarpals
        # (ring, pinky) are mobile and cup more than the fixed radial
        # ones (index, middle). Reward a graded arch cup + palm cupping,
        # scaled up when the hand must wrap (fingers flexed). This makes
        # the palm non-flat and the MCP row non-rigid.
        flexavg = np.mean([v[FING][i*4] for i in range(4)])
        cupwant = np.array([0.05, 0.09, 0.20, 0.30]) * max(0.4, flexavg / 0.7)
        cb["arch"] = float(np.sum((meta - cupwant) ** 2))
        # Finger individuality: index most independent, ring↔pinky most
        # coupled (shared musculature). Penalise index for MATCHING its
        # neighbour's flex, and ring/pinky for DIVERGING from each other.
        mcp = [v[FING][i*4] for i in range(4)]
        cb["individ"] = ((mcp[3] - mcp[2]) ** 2 * 1.5      # pinky follows ring
                         - min(0.0, (mcp[0] - mcp[1]) ** 2) )  # (index free)
        cb["individ"] = (mcp[3] - mcp[2]) ** 2 * 1.5
        # Neck conformity: the MCP heads (base joints) across the four
        # fingers should WRAP the neck cylinder — outer fingers (index,
        # pinky) sit deeper behind the board (+y) than the middle pair,
        # so the palm cups around the neck instead of lying flat.
        base_y = [fingers[f][0][1] for f in FINGERS]   # index..pinky MCP y (mm)
        # Convex arc: middle pair nearer the board (smaller y), ends back.
        arc = (base_y[0] + base_y[3]) / 2 - (base_y[1] + base_y[2]) / 2
        cb["conform"] = max(0.0, -arc + 4.0) ** 2 * 0.02   # want ends ≥ ~4mm back
        # Root motion regulariser (prefer small hand moves over stretch —
        # cheap so the hand relocates rather than contorting).
        cb["root"] = 0.0
        cb["move"] = 0.0
        if prev is not None:
            cb["move"] = float(np.sum((v[FING] - prev[FING]) ** 2)) \
                + float(np.sum((v[RLOC] - prev[RLOC]) ** 2)) * 1e6
        total = sum(W[k] * cb[k] for k in cb)
        return total, cb, fingers, thumb_tip, thumb_js

    def _as_state(self, v):
        fvec = v[FING]
        finger = {f: fvec[i*4:i*4+4].copy() for i, f in enumerate(FINGERS)}
        return A.HandState(v[RLOC], v[RROT], finger, v[THUMB])

    def _manifold(self, fvec):
        z = BASIS.components @ (fvec - BASIS.mean)
        proj = BASIS.mean + BASIS.components.T @ z
        d = fvec - proj
        return float(np.sum(d * d))

    # ---- solve ----
    def solve(self, contact_list, prev=None, restarts=5):
        t0 = time.time()
        # Seed: place middle-MCP below the pressed cluster (metres).
        press = [(c.string, c.fret) for c in contact_list]
        cx = np.mean([G.finger_x(fr) for _, fr in press]) / M2MM
        cz = np.mean([G.string_z(s, abs(G.finger_x(fr))) for s, fr in press]) / M2MM
        # Solve root_loc so the middle-MCP world ~ (cx, small, cz-below).
        seed = np.zeros(STATE_N)
        # place: AW @ mid_mcp = want  → root_loc = want - fret_rot@mid_mcp
        want = np.array([cx, -0.024, cz - G.BOARD_W / M2MM / 2 - 0.030])
        seed[RLOC] = want - (self.fret_rot[:3, :3] @ self.mid_mcp)
        neutral = np.array([28, 0, 45, 30]) * A.D2R
        for i in range(4):
            seed[6 + i*4:6 + i*4 + 4] = neutral
        seed[THUMB] = np.array([25, 15, 20, 0]) * A.D2R
        seed[META] = np.array([3, 6, 12, 18]) * A.D2R    # gentle rest arch
        if prev is not None:
            seed[FING] = prev[FING]; seed[THUMB] = prev[THUMB]; seed[META] = prev[META]

        lo = seed - 0.0; hi = seed + 0.0    # placeholder, set below
        lo = np.full(STATE_N, -np.inf); hi = np.full(STATE_N, np.inf)
        lo[RLOC] = seed[RLOC] - 0.12; hi[RLOC] = seed[RLOC] + 0.12
        lo[RROT] = -0.6; hi[RROT] = 0.6
        for i, f in enumerate(FINGERS):
            L = A.HandModel.default().finger_limits[f]
            base = 6 + i * 4
            lo[base] = L["mcp_flex"].lo; hi[base] = L["mcp_flex"].hi
            lo[base+1] = L["mcp_abd"].lo; hi[base+1] = L["mcp_abd"].hi
            lo[base+2] = L["pip_flex"].lo; hi[base+2] = L["pip_flex"].hi
            lo[base+3] = L["dip_flex"].lo; hi[base+3] = L["dip_flex"].hi
        lo[THUMB] = np.array([-10, -10, -15, -30]) * A.D2R
        hi[THUMB] = np.array([55, 40, 80, 30]) * A.D2R
        # Metacarpal cupping ROM: ulnar (ring/pinky) mobile, radial fixed.
        lo[META] = np.array([-2, -2, -2, -2]) * A.D2R
        hi[META] = np.array([14, 12, 26, 36]) * A.D2R

        def obj(v):
            return self.evaluate(v, contact_list, prev)[0]

        rperturb = [0, 0.15, -0.15, 0.25, -0.2]
        zper = [0, 0.3, -0.3, 0.5, -0.4]
        best, bcost = None, np.inf
        for r in range(restarts):
            v0 = seed.copy()
            v0[3] += rperturb[r % len(rperturb)]
            v0[4] += zper[r % len(zper)] * 0.3
            for k in range(6, 22):
                v0[k] += zper[r % len(zper)] * 0.3
            v0 = np.clip(v0, lo, hi)
            res = minimize(obj, v0, method="L-BFGS-B", bounds=list(zip(lo, hi)),
                           options=dict(maxiter=300, ftol=1e-9))
            if res.fun < bcost:
                bcost, best = res.fun, res.x

        total, cb, fingers, thumb_tip, thumb_js = self.evaluate(best, contact_list, prev)
        # Render-space contact metrics (THE point of unification).
        acc, maxc = {}, 0.0
        for c in contact_list:
            tip = fingers[c.finger][-1]
            fk = type("fk", (), {"tip": tip})
            e = c.error_mm(fk)
            acc[f"{c.kind}:{c.finger}"] = round(e, 2)
            if c.required: maxc = max(maxc, e)
        coll = np.sqrt(cb["collision"]) if cb["collision"] > 0 else 0.0
        board = np.sqrt(cb["board"]) if cb["board"] > 0 else 0.0
        status = "OK"
        if maxc > CONTACT_TOL_MM or coll > COLLISION_TOL_MM or board > BOARD_TOL_MM:
            status = "INFEASIBLE"
        return dict(
            status=status, state=best.tolist(), max_contact_mm=round(maxc, 2),
            contact_acc=acc, collision_mm=round(coll, 2), board_mm=round(board, 2),
            thumb_behind=bool(thumb_tip[1] > G.NECK_THICK - 3),
            costs={k: round(v, 3) for k, v in cb.items()},
            solve_s=round(time.time() - t0, 2),
            fingers_mm={f: [p.tolist() for p in fingers[f]] for f in FINGERS},
            thumb_mm=[p.tolist() for p in thumb_js])
