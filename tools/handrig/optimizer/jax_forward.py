"""Phase 2 — JAX forward model (mathematical re-expression of the FROZEN NumPy
forward model in mpfb_solver.evaluate).

This changes NOTHING about the planner, objective, or weights. It re-expresses
the SAME mathematics in jax.numpy so that (a) the residual vector consumed by a
future Gauss-Newton/LM backend is defined, and (b) analytic Jacobians are
available via autodiff. All constants (rig matrices, synergy basis, weights,
neutral pose) are pulled from a live MPFBSolver so there is zero transcription
drift from the frozen implementation.

The GN RESIDUAL VECTOR r is defined so that  sum(r_i^2) == evaluate() total
(with prev=None): each frozen cost term c_k is already a sum of (hinge-)squares,
so r stacks sqrt(W_k) * {that term's residual elements}. Term order is fixed and
deterministic.

Scope note: the committed rig has no metacarpal bones, so FK uses the
meta_name=None branch (metacarpal cupping inert), exactly as the frozen solver
does. prev-dependent terms (move/root) are zero and omitted (prev=None).
"""
from __future__ import annotations
import os, sys
import numpy as np
import jax
jax.config.update("jax_enable_x64", True)   # match NumPy float64 exactly
import jax.numpy as jnp

HERE = os.path.dirname(os.path.abspath(__file__))
SOLVER = os.path.join(os.path.dirname(HERE), "solver")
HANDRIG = os.path.dirname(HERE)
for p in (SOLVER, HANDRIG):
    if p not in sys.path:
        sys.path.insert(0, p)

import mpfb_solver as M
import contacts as CN
import guitar as G
import anatomy as A

M2MM = 1000.0
FINGERS = list(M.FINGERS)              # index, middle, ring, pinky
NEUTRAL = np.array([28.0, 0.0, 45.0, 30.0]) * A.D2R
STRAINW = np.array([1.0, 2.0, 0.9, 0.6])
COMFORT = 4.0 * A.D2R
CBAND = A.COUPLING_BAND_DEG * A.D2R
CRATIO = A.COUPLING_RATIO
FRAD = M.FINGER_RADIUS_MM
NECK = G.NECK_THICK
ZLO, ZHI = G.board_z_range()


def _euler(a):
    rx, ry, rz = a[0], a[1], a[2]
    cx, sx = jnp.cos(rx), jnp.sin(rx)
    cy, sy = jnp.cos(ry), jnp.sin(ry)
    cz, sz = jnp.cos(rz), jnp.sin(rz)
    Rx = jnp.array([[1, 0, 0], [0, cx, -sx], [0, sx, cx]])
    Ry = jnp.array([[cy, 0, sy], [0, 1, 0], [-sy, 0, cy]])
    Rz = jnp.array([[cz, -sz, 0], [sz, cz, 0], [0, 0, 1]])
    return Rz @ Ry @ Rx


def _basis(a):
    R = _euler(a)
    return jnp.block([[R, jnp.zeros((3, 1))], [jnp.zeros((1, 3)), jnp.ones((1, 1))]])


class JaxForward:
    def __init__(self, solver, specs):
        """solver: a live SolverAdapter (wraps MPFBSolver); specs: list of
        contact dicts {string,fret,finger} for ONE moment."""
        s = solver._s
        self.fret_rot = jnp.array(s.fret_rot)
        self.specs = specs
        # per-digit chain matrices (meta_name=None branch)
        self.chain = {}
        for d in FINGERS + ["thumb"]:
            base = M.FMAP[d]
            names = [f"{base}-1.L", f"{base}-2.L", f"{base}-3.L"]
            b = s.hand.bones
            self.chain[d] = dict(
                M0=jnp.array(b[names[0]]["M"]), M1=jnp.array(b[names[1]]["M"]),
                M2=jnp.array(b[names[2]]["M"]),
                Minv0=jnp.array(b[names[0]]["Minv"]), Minv1=jnp.array(b[names[1]]["Minv"]),
                L2=float(b[names[2]]["length"]))
        # synergy basis
        self.mean = jnp.array(M.BASIS.mean)
        self.comp = jnp.array(M.BASIS.components)      # (K,16)
        # per-contact region boxes (constants; precomputed via the frozen code)
        self.regions = []
        for c in specs:
            prim = CN.PointContact(string=c["string"], fret=c["fret"], finger=c["finger"])
            (xl, xh), (zl, zh), (yl, yh) = prim._region()
            self.regions.append((jnp.array([xl, yl, zl]), jnp.array([xh, yh, zh])))
        self.W = {k: float(M.W[k]) for k in M.W}

    # ---- FK ----
    def _AW(self, root_loc, root_rot):
        R = _euler(root_rot)
        AW = self.fret_rot @ jnp.block([[R, jnp.zeros((3, 1))],
                                        [jnp.zeros((1, 3)), jnp.ones((1, 1))]])
        AW = AW.at[:3, 3].set(root_loc)
        return AW

    def _chain_world(self, AW, ch, a0, a1, a2):
        Mp = ch["M0"] @ _basis(a0)
        j0 = Mp[:3, 3]
        Mp = Mp @ ch["Minv0"] @ ch["M1"] @ _basis(a1)
        j1 = Mp[:3, 3]
        Mp = Mp @ ch["Minv1"] @ ch["M2"] @ _basis(a2)
        j2 = Mp[:3, 3]
        tip = (Mp @ jnp.array([0.0, ch["L2"], 0.0, 1.0]))[:3]
        pts = jnp.stack([j0, j1, j2, tip])                      # (4,3) local metres
        w = (AW @ jnp.concatenate([pts, jnp.ones((4, 1))], axis=1).T).T[:, :3]
        return w * M2MM                                          # mm

    def fingers_mm(self, v):
        AW = self._AW(v[0:3], v[3:6])
        out = {}
        for i, d in enumerate(FINGERS):
            mcpF, mcpA, pip, dip = v[6+i*4], v[6+i*4+1], v[6+i*4+2], v[6+i*4+3]
            a0 = jnp.array([mcpF, 0.0, mcpA]); a1 = jnp.array([pip, 0.0, 0.0]); a2 = jnp.array([dip, 0.0, 0.0])
            out[d] = self._chain_world(AW, self.chain[d], a0, a1, a2)
        return out, AW

    def thumb_mm(self, v, AW):
        mcpF, mcpA, ip = v[22], v[23], v[24]
        a0 = jnp.array([mcpF, 0.0, mcpA]); a1 = jnp.array([ip, 0.0, 0.0]); a2 = jnp.array([ip*0.6, 0.0, 0.0])
        w = self._chain_world(AW, self.chain["thumb"], a0, a1, a2)
        return w[-1]

    # ---- residual blocks (each block's sum-of-squares == W_k * cb_k) ----
    def blocks(self, v):
        fg, AW = self.fingers_mm(v)
        b = {}
        # contact
        cr = []
        for c, (lo, hi) in zip(self.specs, self.regions):
            tip = fg[c["finger"]][-1]
            miss = jnp.maximum(0.0, jnp.maximum(lo - tip, tip - hi))   # per-axis miss
            cr.append(jnp.sqrt(self.W["contact"]) * miss)
        b["contact"] = jnp.concatenate(cr) if cr else jnp.zeros(0)
        # board
        allj = jnp.concatenate([fg[d] for d in FINGERS], axis=0)       # (16,3)
        inz = (allj[:, 2] >= ZLO - 5) & (allj[:, 2] <= ZHI + 5)
        depth = jnp.where(inz, jnp.maximum(0.0, allj[:, 1]), 0.0)
        b["board"] = jnp.sqrt(self.W["board"]) * depth
        # collision: adjacent finger pairs, mid+distal joints (indices 1,2,3)
        cc = []
        for i in range(len(FINGERS) - 1):
            aa = fg[FINGERS[i]][1:]; bb = fg[FINGERS[i+1]][1:]
            d = jnp.sqrt(jnp.sum((aa[:, None, :] - bb[None, :, :]) ** 2, axis=-1))
            dmin = jnp.min(d)
            cc.append(jnp.sqrt(self.W["collision"]) * jnp.maximum(0.0, 2*FRAD - dmin))
        b["collision"] = jnp.stack(cc)
        # strain (angle)
        fv = v[6:22]
        st = []
        for i in range(4):
            d = fv[i*4:i*4+4] - NEUTRAL
            st.append(jnp.sqrt(self.W["strain"]) * jnp.sqrt(STRAINW) * d)
        b["strain"] = jnp.concatenate(st)
        # splay
        abds = jnp.array([fv[i*4+1] for i in range(4)])
        sp_self = jnp.sqrt(self.W["splay"]) * jnp.maximum(0.0, jnp.abs(abds) - COMFORT)
        sp_nb = jnp.sqrt(self.W["splay"]) * jnp.sqrt(0.5) * (abds[:-1] - abds[1:])
        b["splay"] = jnp.concatenate([sp_self, sp_nb])
        # coupling
        cp = []
        for i in range(4):
            pip, dip = fv[i*4+2], fv[i*4+3]
            d = jnp.abs(dip - CRATIO * pip)
            cp.append(jnp.sqrt(self.W["coupling"]) * jnp.maximum(0.0, d - CBAND))
        b["coupling"] = jnp.stack(cp)
        # manifold
        z = self.comp @ (fv - self.mean)
        proj = self.mean + self.comp.T @ z
        b["manifold"] = jnp.sqrt(self.W["manifold"]) * (fv - proj)
        # thumb
        tt = self.thumb_mm(v, AW)
        midx = fg["middle"][0][0]
        tgt = jnp.array([midx, NECK, 0.0])
        th_pos = jnp.sqrt(self.W["thumb"]) * jnp.sqrt(0.01) * (tt - tgt)
        short = NECK - tt[1]
        th_short = jnp.sqrt(self.W["thumb"]) * jnp.sqrt(1.5) * jnp.maximum(0.0, short) / 10.0
        b["thumb"] = jnp.concatenate([th_pos, jnp.array([th_short])])
        # wrist
        b["wrist"] = jnp.array([jnp.sqrt(self.W["wrist"]) * jnp.sqrt(0.5) * v[5]])
        # arch
        meta = v[26:30]
        flexavg = jnp.mean(jnp.array([v[6+i*4] for i in range(4)]))
        cupwant = jnp.array([0.05, 0.09, 0.20, 0.30]) * jnp.maximum(0.4, flexavg / 0.7)
        b["arch"] = jnp.sqrt(self.W["arch"]) * (meta - cupwant)
        # individ
        mcp = jnp.array([v[6+i*4] for i in range(4)])
        b["individ"] = jnp.array([jnp.sqrt(self.W["individ"]) * jnp.sqrt(1.5) * (mcp[3] - mcp[2])])
        # conform
        base_y = jnp.array([fg[d][0][1] for d in FINGERS])
        arc = (base_y[0] + base_y[3]) / 2 - (base_y[1] + base_y[2]) / 2
        b["conform"] = jnp.array([jnp.sqrt(self.W["conform"]) * jnp.sqrt(0.02) * jnp.maximum(0.0, -arc + 4.0)])
        return b

    ORDER = ["contact", "board", "collision", "strain", "splay", "coupling",
             "manifold", "thumb", "wrist", "arch", "individ", "conform"]

    def residual(self, v):
        b = self.blocks(v)
        return jnp.concatenate([b[k] for k in self.ORDER])

    def total(self, v):
        r = self.residual(v)
        return jnp.sum(r * r)

    def cost_terms(self, v):
        """Unweighted cb_k (== frozen evaluate's cb) for block-equivalence:
        cb_k = sum(block_k^2) / W_k."""
        b = self.blocks(v)
        return {k: float(jnp.sum(b[k] * b[k]) / self.W[k]) for k in self.ORDER}
