"""Comfort-model study for sustained_anchor (analysis only; NO changes).

Treats the frozen comfort objective as a scientific model and asks WHY it
prefers a slightly translated hand. Decomposes contributions, measures finger
authority, and tests whether the model distinguishes anchored vs moving
contacts. Read-only.
"""
from __future__ import annotations
import os, sys, json
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import numpy as np
from scipy.optimize import minimize

from loader import load_phrase, load_reference
from adapters import SolverAdapter
from planners import NaivePlanner, group_moments
import mpfb_solver as M
import anatomy as A

HERE = os.path.dirname(os.path.abspath(__file__))
PH = "sustained_anchor"
NEUTRAL = np.array([28.0, 0.0, 45.0, 30.0]) * A.D2R
SW = np.array([1.0, 2.0, 0.9, 0.6])   # strain per-joint weights


def per_finger_strain(state):
    v = np.asarray(state, float)
    out = {}
    for i, f in enumerate(M.FINGERS):
        d = v[6+i*4:6+i*4+4] - NEUTRAL
        out[f] = float(np.sum(SW * d * d))
    return out


def comfort_terms(solver, state, specs):
    v = np.asarray(state, float)
    cl = solver._contacts(specs)
    tot, cb, fingers, tt, tj = solver._s.evaluate(v, cl, None)
    comfort_keys = ("strain", "splay", "coupling", "manifold", "thumb", "wrist",
                    "arch", "individ", "conform")
    return {k: round(M.W[k]*cb[k], 4) for k in comfort_keys if M.W[k]*cb[k] > 1e-6}


def opt_fingers_at_root(solver, seed, root, specs):
    """Min total cost over finger DoF with root fixed at `root`."""
    s0 = np.asarray(seed, float).copy(); s0[:3] = root
    lo, hi = solver.knot_bounds(root, root_pad=0.15)
    r = minimize(lambda fv: solver.evaluate_state(np.concatenate([s0[:6], fv]), specs),
                 s0[6:], method="L-BFGS-B", bounds=list(zip(lo[6:], hi[6:])),
                 options=dict(maxiter=400, ftol=1e-11))
    s = s0.copy(); s[6:] = r.x
    return s


def main():
    solver = SolverAdapter()
    ph = load_phrase(HERE, PH); ref = load_reference(HERE, PH)
    moments = group_moments(ph.events)
    specs = [[dict(string=e.string, fret=e.fret, finger=e.finger) for e in evs]
             for _, evs in moments]
    naive = NaivePlanner().plan(ph, ref, solver, {"restarts": 5})
    init = [np.asarray(k.mpfb_state, float) for k in naive.knots]
    r_est = init[0][:3].copy()

    print("=" * 88)
    print("### (0) STRUCTURAL: does root translation change comfort at FIXED angles?")
    s = init[1].copy()
    c0 = comfort_terms(solver, s, specs[1])
    s2 = s.copy(); s2[0] += 0.005   # move root 5mm, keep angles
    c1 = comfort_terms(solver, s2, specs[1])
    print(f"  comfort at angles, root+0mm : {c0}")
    print(f"  comfort at SAME angles, +5mm: {c1}")
    print("  => comfort terms are ANGLE-only; root affects comfort ONLY via the")
    print("     finger angles needed to keep fingertips on the frets.")

    print("\n### (2) PER-KNOT COMFORT CONTRIBUTION (weighted, W*cb)")
    for i in range(len(moments)):
        desc = ", ".join("{}(s{}f{})".format(c['finger'], c['string'], c['fret']) for c in specs[i])
        print(f"  m{i} [{desc}]")
        print(f"      terms: {comfort_terms(solver, init[i], specs[i])}")
        print(f"      per-finger strain: { {k:round(v,4) for k,v in per_finger_strain(init[i]).items()} }")

    print("\n### (3) FINGER AUTHORITY (moment 1): who gains when the root shifts to the comfort min?")
    m = 1
    # comfort-min root: scan root_x, re-opt fingers, pick min total cost
    best = None
    for off in np.arange(-8, 4.01, 0.5)/1000.0:
        s = opt_fingers_at_root(solver, init[m], r_est + np.array([off,0,0]), specs[m])
        tot = solver.evaluate_state(s, specs[m])
        if best is None or tot < best[0]:
            best = (tot, off, s)
    s_est = opt_fingers_at_root(solver, init[m], r_est, specs[m])
    s_min = best[2]
    pf_est = per_finger_strain(s_est); pf_min = per_finger_strain(s_min)
    print(f"  comfort-min root offset = {best[1]*1000:+.1f}mm (total cost {best[0]:.4f} vs r_est {solver.evaluate_state(s_est,specs[m]):.4f})")
    print(f"  per-finger strain at r_est: { {k:round(v,4) for k,v in pf_est.items()} }")
    print(f"  per-finger strain at min  : { {k:round(v,4) for k,v in pf_min.items()} }")
    delta = {k: round(pf_min[k]-pf_est[k],4) for k in pf_est}
    print(f"  strain change (min - r_est): {delta}")
    winner = min(delta, key=delta.get)
    print(f"  => finger that GAINS most from the shift (largest strain drop): {winner}")

    print("\n### (4) ANCHORED vs MOVING: is the index anchor treated specially?")
    print("  index contact spec is identical across m0,m1,m2:",
          all(any(c['finger']=='index' and c['string']==5 and c['fret']==5 for c in specs[i]) for i in range(3)))
    print("  Comfort terms referencing an anchor/held/stationary contact: NONE")
    print("  (strain/splay/coupling/manifold/arch/individ/conform are angle-only;")
    print("   thumb targets the MIDDLE-MCP, not the anchor; no term weights a contact")
    print("   by whether it is sustained). The word 'anchor' exists only in the")
    print("   benchmark REFERENCE, not in the solver objective.")


if __name__ == "__main__":
    main()
