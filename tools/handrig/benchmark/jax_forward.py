"""Phase 2 — JAX forward model of the FROZEN cost stack.

Mathematically identical reimplementation of solver.mpfb_solver.MPFBSolver.evaluate
(prev=None, i.e. the per-knot term the trajopt objective sums) in jax.numpy, for
Proof A (forward-model equivalence) and Proof B (analytic Jacobians via jax autodiff).

Design rule for zero constant-drift: ALL static data (rig bone matrices M/Minv/length,
fretting rotation, synergy basis mean/components, per-contact region boxes, guitar
targets, neutral/weights/limits) is harvested from the frozen NumPy objects at __init__.
Only the ARITHMETIC is re-expressed in jnp. Not wired into any planner; validation only.

x64 is mandatory (the solver is float64).
"""
from __future__ import annotations
import os, sys
import numpy as np
import jax, jax.numpy as jnp
jax.config.update("jax_enable_x64", True)

HANDRIG = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HANDRIG)
sys.path.insert(0, os.path.join(HANDRIG, "solver"))
import mpfb_solver as MS
import anatomy as A
import guitar as G

FINGERS = ("index", "middle", "ring", "pinky")
RLOC = slice(0, 3); RROT = slice(3, 6); FING = slice(6, 22); THUMB = slice(22, 26); META = slice(26, 30)
D2R = A.D2R
NECK_THICK = float(G.NECK_THICK)
FINGER_RADIUS_MM = float(MS.FINGER_RADIUS_MM)
M2MM = float(MS.M2MM)


def _euler_m(rx, ry, rz):
    cx, sx = jnp.cos(rx), jnp.sin(rx); cy, sy = jnp.cos(ry), jnp.sin(ry); cz, sz = jnp.cos(rz), jnp.sin(rz)
    Rx = jnp.array([[1.,0.,0.],[0.,cx,-sx],[0.,sx,cx]])
    Ry = jnp.array([[cy,0.,sy],[0.,1.,0.],[-sy,0.,cy]])
    Rz = jnp.array([[cz,-sz,0.],[sz,cz,0.],[0.,0.,1.]])
    return Rz @ Ry @ Rx

def _basis(rx, ry, rz):
    return jnp.eye(4).at[:3,:3].set(_euler_m(rx, ry, rz))


class JaxForward:
    def __init__(self, rig_path=None):
        # instantiate the frozen solver to harvest exact constants
        if rig_path is None:
            rig_path = os.path.join(HANDRIG, "unification", "mpfb_rig.json")
        self.solver = MS.MPFBSolver(rig_path)
        hand = self.solver.hand
        self.fret_rot = jnp.array(self.solver.fret_rot)
        self.W = dict(MS.W)
        # rig bone constants per finger chain (+ optional metacarpal), static
        self.chain = {}
        self.meta = {}
        for i, f in enumerate(FINGERS):
            digit = MS.FMAP[f]
            names = [f"{digit}-1.L", f"{digit}-2.L", f"{digit}-3.L"]
            self.chain[f] = [dict(M=jnp.array(hand.bones[n]["M"]),
                                  Minv=jnp.array(hand.bones[n]["Minv"]),
                                  length=float(hand.bones[n]["length"])) for n in names]
            mb = MS.META_BONE[i]
            if mb in hand.bones:
                self.meta[f] = dict(M=jnp.array(hand.bones[mb]["M"]), Minv=jnp.array(hand.bones[mb]["Minv"]))
            else:
                self.meta[f] = None
        # thumb chain
        tn = ["finger1-1.L", "finger1-2.L", "finger1-3.L"]
        self.thumb_chain = [dict(M=jnp.array(hand.bones[n]["M"]), Minv=jnp.array(hand.bones[n]["Minv"]),
                                 length=float(hand.bones[n]["length"])) for n in tn]
        # synergy basis
        self.syn_mean = jnp.array(MS.BASIS.mean); self.syn_comp = jnp.array(MS.BASIS.components)
        # anatomy constants
        self.strain_neutral = jnp.array([28.,0.,45.,30.]) * D2R
        self.strain_w = jnp.array([1.,2.,0.9,0.6])
        self.splay_comfort = 4.0 * D2R
        self.coupling_band = A.COUPLING_BAND_DEG * D2R
        self.coupling_ratio = A.COUPLING_RATIO
        self.board_zlo, self.board_zhi = G.board_z_range()

    # ---- FK (bone chain), jnp ----
    def armature_world(self, root_loc, root_rot):
        R = jnp.eye(4).at[:3,:3].set(_euler_m(*root_rot))
        M = self.fret_rot @ R
        return M.at[:3,3].set(root_loc)

    def finger_joints_mm(self, f, four_dof, AW, meta_cup):
        mcpF, mcpA, pip, dip = four_dof
        angs = [(mcpF, 0., mcpA), (pip, 0., 0.), (dip, 0., 0.)]
        ch = self.chain[f]; meta = self.meta[f]
        if meta is not None:
            Mprev = meta["M"] @ _basis(meta_cup, 0., 0.)
            Mprev = Mprev @ meta["Minv"] @ ch[0]["M"] @ _basis(*angs[0])
        else:
            Mprev = ch[0]["M"] @ _basis(*angs[0])
        joints = [Mprev[:3,3]]
        prev = ch[0]
        for i in (1,2):
            b = ch[i]
            Mprev = Mprev @ prev["Minv"] @ b["M"] @ _basis(*angs[i])
            joints.append(Mprev[:3,3]); prev = b
        tip = (Mprev @ jnp.array([0., ch[2]["length"], 0., 1.]))[:3]
        joints.append(tip)
        return [(AW @ jnp.concatenate([p, jnp.array([1.])]))[:3] * M2MM for p in joints]

    def thumb_tip_mm(self, thumb_dof, AW):
        mcpF, mcpA, ip, _ = thumb_dof
        angs = [(mcpF,0.,mcpA),(ip,0.,0.),(ip*0.6,0.,0.)]
        ch = self.thumb_chain
        Mprev = ch[0]["M"] @ _basis(*angs[0]); prev = ch[0]
        for i in (1,2):
            b = ch[i]; Mprev = Mprev @ prev["Minv"] @ b["M"] @ _basis(*angs[i]); prev = b
        tip = (Mprev @ jnp.array([0., ch[2]["length"], 0., 1.]))[:3]
        return (AW @ jnp.concatenate([tip, jnp.array([1.])]))[:3] * M2MM

    def _region_const(self, contact):
        """Harvest the static fretting box from the frozen PointContact."""
        (xlo,xhi),(zlo,zhi),(ylo,yhi) = contact._region()
        return jnp.array([xlo,ylo,zlo]), jnp.array([xhi,yhi,zhi])

    # ---- the evaluate() reimplementation, per-block; prev=None ----
    def blocks(self, v, contact_list):
        v = jnp.asarray(v)
        root_loc, root_rot = v[RLOC], v[RROT]
        AW = self.armature_world(root_loc, root_rot)
        fvec = v[FING]; meta = v[META]
        fingers = {f: self.finger_joints_mm(f, fvec[i*4:i*4+4], AW, meta[i]) for i,f in enumerate(FINGERS)}
        thumb_tip = self.thumb_tip_mm(v[THUMB], AW)
        cb = {}
        # contact (PointContact box-distance^2; line weight 2.2 unused by the 5 phrases)
        ce = 0.0
        for c in contact_list:
            tip = fingers[c.finger][-1]
            lo, hi = self._region_const(c)
            over = jnp.clip(lo - tip, 0.) + jnp.clip(tip - hi, 0.)
            w = 2.2 if getattr(c, "kind", "point") == "line" else 1.0
            ce = ce + w * jnp.sum(over*over)
        cb["contact"] = ce
        # board penetration (z-window mask, then clip depth)
        bp = 0.0
        for f in FINGERS:
            for p in fingers[f]:
                inwin = (p[2] >= self.board_zlo - 5) & (p[2] <= self.board_zhi + 5)
                depth = jnp.clip(p[1], 0.)
                bp = bp + jnp.where(inwin, depth*depth, 0.)
        cb["board"] = bp
        # collision (adjacent mid-segments joints[1:], min pairwise, clip)
        coll = 0.0
        for i in range(len(FINGERS)-1):
            a = fingers[FINGERS[i]][1:]; b = fingers[FINGERS[i+1]][1:]
            dists = jnp.array([jnp.linalg.norm(pa-pb) for pa in a for pb in b])
            ov = 2*FINGER_RADIUS_MM - jnp.min(dists)
            coll = coll + jnp.clip(ov, 0.)**2
        cb["collision"] = coll
        # strain / splay / coupling (angle-space quadratics + clip)
        strain = 0.0
        for i in range(4):
            d = fvec[i*4:i*4+4] - self.strain_neutral
            strain = strain + jnp.sum(self.strain_w * d * d)
        cb["strain"] = strain
        abds = jnp.array([fvec[i*4+1] for i in range(4)])
        splay = jnp.sum(jnp.clip(jnp.abs(abds) - self.splay_comfort, 0.)**2)
        for i in range(3):
            splay = splay + 0.5*(abds[i]-abds[i+1])**2
        cb["splay"] = splay
        coup = 0.0
        for i in range(4):
            pip = fvec[i*4+2]; dip = fvec[i*4+3]
            d = jnp.abs(dip - self.coupling_ratio*pip)
            coup = coup + jnp.clip(d - self.coupling_band, 0.)**2
        cb["coupling"] = coup
        # manifold (linear projection residual)
        z = self.syn_comp @ (fvec - self.syn_mean)
        proj = self.syn_mean + self.syn_comp.T @ z
        dm = fvec - proj
        cb["manifold"] = jnp.sum(dm*dm)
        # thumb
        midx = fingers["middle"][0][0]
        tgt = jnp.array([midx, NECK_THICK, 0.0])
        th = jnp.sum((thumb_tip - tgt)**2) * 0.01
        short = NECK_THICK - thumb_tip[1]
        th = th + jnp.where(short > 0, (jnp.clip(short,0.)/10.0)**2 * 1.5, 0.)
        cb["thumb"] = th
        cb["wrist"] = root_rot[2]**2 * 0.5
        # arch
        flexavg = jnp.mean(jnp.array([fvec[i*4] for i in range(4)]))
        cupwant = jnp.array([0.05,0.09,0.20,0.30]) * jnp.maximum(0.4, flexavg/0.7)
        cb["arch"] = jnp.sum((meta - cupwant)**2)
        # individ (line 168 overwrites 166 in frozen -> just this)
        mcp = jnp.array([fvec[i*4] for i in range(4)])
        cb["individ"] = (mcp[3]-mcp[2])**2 * 1.5
        # conform
        base_y = jnp.array([fingers[f][0][1] for f in FINGERS])
        arc = (base_y[0]+base_y[3])/2 - (base_y[1]+base_y[2])/2
        cb["conform"] = jnp.clip(-arc + 4.0, 0.)**2 * 0.02
        # root/move = 0 (prev=None)
        cb["root"] = jnp.array(0.0); cb["move"] = jnp.array(0.0)
        return cb

    def total(self, v, contact_list):
        cb = self.blocks(v, contact_list)
        return sum(self.W[k]*cb[k] for k in cb)
