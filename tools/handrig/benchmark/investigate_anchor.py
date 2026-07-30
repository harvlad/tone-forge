"""sustained_anchor — mechanism investigation (READ-ONLY, no fixes).

Does NOT modify planner/solver/benchmark. Re-runs the resist objective with
logging and runs counterfactual cost analyses to identify the ONE dominant
remaining mechanism behind the unnecessary root motion (naive 0.3mm ->
resist 10.2mm).

Sections mirror the brief: (1/2) per-iteration root trace, (3) contact/finger
responsibility, (4/5) rigid-body test by counterfactual reasoning, (6)
conditioning/symmetry, (7) causal chain, (9) verdict.
"""
from __future__ import annotations
import os, sys, json
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import numpy as np
from scipy.optimize import minimize

from loader import load_phrase, load_reference
from adapters import SolverAdapter
from planners import NaivePlanner, ResistTrajoptPlanner, group_moments
import mpfb_solver as M

HERE = os.path.dirname(os.path.abspath(__file__))
PH = "sustained_anchor"
FINGERS = M.FINGERS


def cost_breakdown(solver, state, specs):
    cl = solver._contacts(specs)
    tot, cb, fingers, tt, tj = solver._s.evaluate(np.asarray(state, float), cl, None)
    contrib = {k: round(M.W[k] * v, 4) for k, v in cb.items() if M.W[k] * v > 1e-6}
    return tot, contrib, fingers


def per_contact_err(solver, state, specs):
    return {f"{c['finger']}": round(solver.contact_error_mm(state, [c]), 3) for c in specs}


def main():
    solver = SolverAdapter()
    ph = load_phrase(HERE, PH); ref = load_reference(HERE, PH)
    moments = group_moments(ph.events)
    specs_per = [[dict(string=e.string, fret=e.fret, finger=e.finger,
                       articulation=e.articulation) for e in evs] for _, evs in moments]
    N = len(moments)

    naive = NaivePlanner().plan(ph, ref, solver, {"restarts": 5})
    init = [np.asarray(k.mpfb_state, float) for k in naive.knots]
    S = init[0].shape[0]
    r_est = init[0][:3].copy()
    W_ROOT, W_SMOOTH = ResistTrajoptPlanner.W_ROOT, ResistTrajoptPlanner.W_SMOOTH

    print("=" * 90)
    print(f"sustained_anchor: {N} moments; contacts per moment:")
    for i, sp in enumerate(specs_per):
        print(f"  m{i}: " + ", ".join(f"{c['finger']}(s{c['string']}f{c['fret']})" for c in sp))
    print(f"established root r_est (naive m0) = {np.round(r_est*1000,1)} mm")
    print(f"naive per-moment roots (mm): " +
          " | ".join(str(np.round(init[i][:3]*1000,1)) for i in range(N)))

    # ---- resist objective (replicated for instrumentation only) ----
    maxd = max(float(np.linalg.norm(init[i][:3] - r_est)) for i in range(N))
    pad = max(0.15, maxd + 0.05)
    los, his = zip(*[solver.knot_bounds(r_est, root_pad=pad) for _ in range(N)])
    lo = np.concatenate(los); hi = np.concatenate(his)
    x0 = np.clip(np.concatenate(init), lo, hi)

    def objective(x):
        states = [x[i*S:(i+1)*S] for i in range(N)]
        total = sum(solver.evaluate_state(states[i], specs_per[i]) for i in range(N))
        dr0 = states[0][:3] - r_est
        total += W_ROOT * float(dr0 @ dr0)
        for i in range(1, N):
            dr = states[i][:3] - states[i-1][:3]
            dq = states[i][6:] - states[i-1][6:]
            total += W_ROOT * float(dr @ dr) + W_SMOOTH * float(dq @ dq)
        return total

    # ---- (1/2) per-iteration root trace ----
    trace = []
    def cb(xk):
        roots = [xk[i*S:i*S+3] for i in range(N)]
        trace.append([np.round((r - r_est)*1000, 2) for r in roots])
    res = minimize(objective, x0, method="L-BFGS-B", bounds=list(zip(lo, hi)),
                   options=dict(maxiter=600, ftol=1e-9), callback=cb)
    xopt = res.x
    fin = [xopt[i*S:(i+1)*S] for i in range(N)]

    print("\n--- (1/2) ROOT MOTION TRACE (mm displacement from r_est, per accepted iter) ---")
    print(f"iters logged: {len(trace)}")
    for j in [0, len(trace)//4, len(trace)//2, 3*len(trace)//4, len(trace)-1] if trace else []:
        print(f"  iter {j:3d}: " + " | ".join(f"m{i}={np.linalg.norm(trace[j][i]):.2f}" for i in range(N)))
    print("  final roots disp (mm): " +
          " | ".join(f"m{i}={np.linalg.norm((fin[i][:3]-r_est)*1000):.2f}" for i in range(N)))
    interknot = [np.linalg.norm((fin[i][:3]-fin[i-1][:3])*1000) for i in range(1, N)]
    print(f"  inter-moment root moves (mm): {[round(x,2) for x in interknot]}")

    # ---- (4/5) RIGID-BODY TEST: free vs root-pinned, per moment ----
    print("\n--- (4/5) RIGID-BODY TEST: min cost with root FREE vs root PINNED to r_est ---")
    fidx = slice(6, S)   # finger+thumb+meta DoF
    for i in range(N):
        # free (naive already root-free optimum for this moment)
        st_free = init[i]
        tot_f, contrib_f, _ = cost_breakdown(solver, st_free, specs_per[i])
        cerr_f = per_contact_err(solver, st_free.tolist(), specs_per[i])
        # pinned: optimize only fingers, root fixed at r_est
        seed = init[i].copy(); seed[:3] = r_est
        lo_i, hi_i = solver.knot_bounds(r_est, root_pad=pad)
        def obj_pin(fv):
            s = seed.copy(); s[6:] = fv
            return solver.evaluate_state(s, specs_per[i])
        rp = minimize(obj_pin, seed[6:], method="L-BFGS-B",
                      bounds=list(zip(lo_i[6:], hi_i[6:])), options=dict(maxiter=400, ftol=1e-9))
        st_pin = seed.copy(); st_pin[6:] = rp.x
        tot_p, contrib_p, _ = cost_breakdown(solver, st_pin, specs_per[i])
        cerr_p = per_contact_err(solver, st_pin.tolist(), specs_per[i])
        droot = np.linalg.norm((st_free[:3]-r_est)*1000)
        print(f"  m{i}: free root disp={droot:.2f}mm  E_free={tot_f:.3f}  E_pinned={tot_p:.3f}  dE={tot_p-tot_f:+.3f}")
        print(f"       contact_err free={cerr_f}  pinned={cerr_p}")
        # which terms drive the gap
        keys = set(contrib_f) | set(contrib_p)
        deltas = {k: round(contrib_p.get(k,0)-contrib_f.get(k,0),4) for k in keys}
        deltas = {k:v for k,v in sorted(deltas.items(), key=lambda kv:-abs(kv[1])) if abs(v)>1e-3}
        print(f"       cost-term change (pinned - free): {deltas}")

    # ---- (3) CONTACT / FINGER RESPONSIBILITY: root sensitivity ----
    print("\n--- (3) FINGER RESPONSIBILITY: per-contact error vs root perturbation (moment 1) ---")
    m = 1
    st = init[m].copy()
    base = per_contact_err(solver, st.tolist(), specs_per[m])
    print(f"  at naive root, contact err: {base}")
    for axis, name in ((0,'x/along-neck'),(2,'z/across-strings')):
        for d in (+0.01, -0.01):
            s2 = st.copy(); s2[axis] += d
            e2 = per_contact_err(solver, s2.tolist(), specs_per[m])
            dd = {f: round(e2[f]-base[f],2) for f in base}
            print(f"  root {name} {d*1000:+.0f}mm -> d_contact_err {dd}")

    # ---- (6) CONDITIONING: objective curvature along root vs finger dirs ----
    print("\n--- (6) CONDITIONING / SYMMETRY at the resist solution ---")
    def J_full(x): return objective(x)
    J0 = J_full(xopt)
    eps = 1e-3
    # curvature along moment-1 root-x and a moment-1 finger angle
    def curv(idx, e):
        xp = xopt.copy(); xp[idx]+=e; xm=xopt.copy(); xm[idx]-=e
        return (J_full(xp)-2*J0+J_full(xm))/(e*e)
    ridx = 1*S + 0          # moment1 root x
    aidx = 1*S + 6          # moment1 index MCP flex
    print(f"  d2J/d(root_x)^2  (m1) = {curv(ridx, eps):.1f}")
    print(f"  d2J/d(angle)^2   (m1) = {curv(aidx, 0.01):.1f}")
    # gradient magnitude at solution (should be ~0 if converged)
    g = np.zeros(len(xopt))
    for k in range(len(xopt)):
        xp=xopt.copy(); xp[k]+=eps; xm=xopt.copy(); xm[k]-=eps
        g[k]=(J_full(xp)-J_full(xm))/(2*eps)
    print(f"  ||grad|| at solution = {np.linalg.norm(g):.4f}  (converged if ~0)")
    print(f"  |grad| on root dofs = {np.linalg.norm(g[[i*S+j for i in range(N) for j in range(3)]]):.4f}")


if __name__ == "__main__":
    main()
