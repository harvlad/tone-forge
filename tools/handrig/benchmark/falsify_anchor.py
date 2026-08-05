"""Falsify the numerical hypothesis for sustained_anchor root wander (diagnosis
only; no planner/solver/objective/weight/optimizer changes).

Leading hypothesis (~80%): the residual root wander is numerical ill-conditioning
+ non-convergence, not planner behaviour. This script tries to PROVE IT WRONG.

Sections: (1) determinism audit, (2) reproducibility distribution, (3)
termination reason, (4) conditioning, (5) null-space around the solution,
(6) smoothness, (7) numerical experiments, (8) explain 10.2 vs 3.3 mm.
"""
from __future__ import annotations
import os, sys, json, hashlib
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import numpy as np
from scipy.optimize import minimize

from loader import load_phrase, load_reference
from adapters import SolverAdapter
from planners import NaivePlanner, ResistTrajoptPlanner, group_moments
import mpfb_solver as M

HERE = os.path.dirname(os.path.abspath(__file__))
PH = "sustained_anchor"


def root_travel(states, S):
    r = [np.asarray(s[:3]) for s in states]
    return sum(float(np.linalg.norm((r[i]-r[i-1])*1000)) for i in range(1, len(r)))


def build(solver, ph, ref):
    """Reconstruct the EXACT resist setup: moments, specs, warm-start x0, bounds,
    objective. Returns everything needed to re-solve and introspect."""
    moments = group_moments(ph.events)
    specs = [[dict(string=e.string, fret=e.fret, finger=e.finger,
                   articulation=e.articulation) for e in evs] for _, evs in moments]
    N = len(moments)
    naive = NaivePlanner().plan(ph, ref, solver, {"restarts": 5})
    init = [np.asarray(k.mpfb_state, float) for k in naive.knots]
    S = init[0].shape[0]
    r_est = init[0][:3].copy()
    maxd = max(float(np.linalg.norm(init[i][:3]-r_est)) for i in range(N))
    pad = max(0.15, maxd + 0.05)
    los, his = zip(*[solver.knot_bounds(r_est, root_pad=pad) for _ in range(N)])
    lo = np.concatenate(los); hi = np.concatenate(his)
    x0 = np.clip(np.concatenate(init), lo, hi)
    WR, WS = ResistTrajoptPlanner.W_ROOT, ResistTrajoptPlanner.W_SMOOTH

    def obj(x):
        st = [x[i*S:(i+1)*S] for i in range(N)]
        t = sum(solver.evaluate_state(st[i], specs[i]) for i in range(N))
        d0 = st[0][:3]-r_est; t += WR*float(d0@d0)
        for i in range(1, N):
            dr = st[i][:3]-st[i-1][:3]; dq = st[i][6:]-st[i-1][6:]
            t += WR*float(dr@dr) + WS*float(dq@dq)
        return t
    return dict(N=N, S=S, specs=specs, init=init, r_est=r_est, lo=lo, hi=hi,
                x0=x0, obj=obj, moments=moments)


def solve_once(B, ftol=1e-9, gtol=1e-5, maxiter=600, x0=None):
    x0 = B["x0"] if x0 is None else x0
    return minimize(B["obj"], x0, method="L-BFGS-B", bounds=list(zip(B["lo"], B["hi"])),
                    options=dict(maxiter=maxiter, ftol=ftol, gtol=gtol))


def main():
    print("=" * 90)
    print("### (1) DETERMINISM AUDIT")
    for k in ("OPENBLAS_NUM_THREADS", "OMP_NUM_THREADS", "MKL_NUM_THREADS",
              "VECLIB_MAXIMUM_THREADS", "NUMEXPR_NUM_THREADS"):
        print(f"  env {k} = {os.environ.get(k, '<unset>')}")
    try:
        import numpy.distutils.system_info as si  # noqa
    except Exception:
        pass
    print(f"  numpy {np.__version__}")

    solver = SolverAdapter()
    ph = load_phrase(HERE, PH); ref = load_reference(HERE, PH)

    # (A) ACTUAL planner reproducibility across full plan() calls
    print("\n### ACTUAL ResistTrajoptPlanner.plan() x6 (end-to-end)")
    rts = []
    for i in range(6):
        tr = ResistTrajoptPlanner().plan(ph, ref, solver, {"restarts": 5})
        rt = root_travel([k.mpfb_state for k in tr.knots], 30)
        h = hashlib.md5(json.dumps([list(np.round(k.mpfb_state, 9)) for k in tr.knots]).encode()).hexdigest()[:8]
        rts.append(rt); print(f"  run {i}: root_travel={rt:.3f}mm  state_hash={h}")
    print(f"  -> distinct root_travel values: {sorted(set(round(x,3) for x in rts))}")

    # (2) REPRODUCIBILITY of the OPTIMIZER alone (same x0, N solves)
    B = build(solver, ph, ref)
    print(f"\n### (2) OPTIMIZER-ONLY reproducibility from a FIXED x0 (x12)")
    rec = []
    for i in range(12):
        r = solve_once(B)
        rec.append((root_travel([r.x[j*B['S']:(j+1)*B['S']] for j in range(B['N'])], B['S']),
                    r.fun, float(np.linalg.norm(r.jac)), r.nit, r.status))
    rt_vals = sorted(set(round(x[0], 4) for x in rec))
    fun_vals = sorted(set(round(x[1], 6) for x in rec))
    print(f"  distinct root_travel: {rt_vals}")
    print(f"  distinct final obj:   {fun_vals}")
    print(f"  gradnorm range: {min(x[2] for x in rec):.2f}..{max(x[2] for x in rec):.2f}")
    print(f"  nit range: {min(x[3] for x in rec)}..{max(x[3] for x in rec)}   status set: {set(x[4] for x in rec)}")

    # (3) TERMINATION
    r = solve_once(B)
    print("\n### (3) TERMINATION (default ftol=1e-9, gtol=1e-5, maxiter=600)")
    print(f"  status={r.status}  nit={r.nit}  nfev={r.nfev}")
    print(f"  message: {r.message}")
    print(f"  final obj={r.fun:.6f}  ||proj grad||={np.linalg.norm(r.jac):.3f}")

    # (4) CONDITIONING
    print("\n### (4) CONDITIONING")
    S, N = B["S"], B["N"]
    x = r.x
    scales = {"root(m)": np.abs(x[:3]).mean(), "angles(rad)": np.abs(x[6:S]).mean()}
    print(f"  variable magnitudes: root~{scales['root(m)']:.3f} m, angles~{scales['angles(rad)']:.3f} rad")
    # per-group gradient
    groot = np.linalg.norm(r.jac[[i*S+j for i in range(N) for j in range(3)]])
    gang = np.linalg.norm(r.jac[[i*S+j for i in range(N) for j in range(6, S)]])
    print(f"  ||grad|| root DoFs={groot:.1f}   angle DoFs={gang:.1f}")
    # diagonal Hessian estimate via central 2nd diff, root_x(m1) vs an angle(m1)
    def d2(idx, e):
        xp = x.copy(); xp[idx]+=e; xm = x.copy(); xm[idx]-=e
        return (B["obj"](xp)-2*B["obj"](x)+B["obj"](xm))/(e*e)
    for e in (1e-2, 1e-3, 1e-4):
        print(f"  e={e:.0e}: d2/droot_x2(m1)={d2(S+0,e):.1f}   d2/dangle2(m1)={d2(S+6,max(e,1e-4)):.1f}")

    # (5) NULL-SPACE: pin root_x(m1) at offsets, re-opt fingers of m1, map objective
    print("\n### (5) NULL-SPACE: moment-1 objective vs pinned root_x offset (fingers re-optimized)")
    m = 1
    base_state = B["init"][m].copy()
    lo_i, hi_i = solver.knot_bounds(B["r_est"], root_pad=0.15)
    for off_mm in (-8, -4, -2, -1, -0.5, 0, 0.5, 1, 2, 4, 8):
        seed = base_state.copy(); seed[0] = B["r_est"][0] + off_mm/1000.0
        def of(fv):
            s = seed.copy(); s[6:] = fv
            return solver.evaluate_state(s, B["specs"][m])
        rp = minimize(of, seed[6:], method="L-BFGS-B",
                      bounds=list(zip(lo_i[6:], hi_i[6:])), options=dict(maxiter=300, ftol=1e-11))
        s = seed.copy(); s[6:] = rp.x
        ce = solver.contact_error_mm(s.tolist(), B["specs"][m])
        print(f"  root_x offset {off_mm:+5.1f}mm -> min E={rp.fun:.4f}  worst_contact={ce:.3f}mm")

    # (6) SMOOTHNESS: fine scan, fingers FIXED at solution, root_x(m1)
    print("\n### (6) SMOOTHNESS: E(root_x) fine scan (fingers fixed), 2nd-diff kink scan")
    xm1 = x.copy()
    offs = np.arange(-1.0, 1.0001, 0.05)/1000.0
    vals = []
    for o in offs:
        xx = xm1.copy(); xx[S+0] = x[S+0] + o
        vals.append(B["obj"](xx))
    vals = np.array(vals)
    d2n = np.abs(vals[2:]-2*vals[1:-1]+vals[:-2])
    print(f"  E range over +-1mm: {vals.min():.4f}..{vals.max():.4f}  (span {vals.max()-vals.min():.4f})")
    print(f"  max |2nd-diff| along scan: {d2n.max():.4f} at offset {offs[1+int(np.argmax(d2n))]*1000:+.2f}mm")

    # (7) NUMERICAL EXPERIMENTS
    print("\n### (7) NUMERICAL EXPERIMENTS (does the reported solution actually converge?)")
    for label, kw in (("tighter ftol=1e-15", dict(ftol=1e-15)),
                      ("tighter gtol=1e-12", dict(gtol=1e-12)),
                      ("maxiter=8000", dict(maxiter=8000)),
                      ("ftol=1e-15+gtol=1e-12+maxiter=8000", dict(ftol=1e-15, gtol=1e-12, maxiter=8000))):
        rr = solve_once(B, **kw)
        rt = root_travel([rr.x[j*S:(j+1)*S] for j in range(N)], S)
        print(f"  {label:42s}: obj={rr.fun:.6f} ||grad||={np.linalg.norm(rr.jac):.2f} "
              f"nit={rr.nit} status={rr.status} root_travel={rt:.3f}mm")
    # root RESCALING change-of-variables: optimize root in mm (scale 1000x)
    print("  -- root rescaling (optimize root in mm; pure change of variables) --")
    S_, N_ = B["S"], B["N"]
    scale = np.ones(len(B["x0"]))
    for i in range(N_):
        scale[i*S_:i*S_+3] = 1000.0     # root in mm
    def obj_s(y):
        return B["obj"](y/scale)
    y0 = B["x0"]*scale
    lo_s = B["lo"]*scale; hi_s = B["hi"]*scale
    rs = minimize(obj_s, y0, method="L-BFGS-B", bounds=list(zip(lo_s, hi_s)),
                  options=dict(maxiter=8000, ftol=1e-15, gtol=1e-12))
    xs = rs.x/scale
    rt = root_travel([xs[j*S_:(j+1)*S_] for j in range(N_)], S_)
    print(f"  rescaled(root=mm) ftol1e-15 maxiter8000 : obj={rs.fun:.6f} ||grad_scaled||={np.linalg.norm(rs.jac):.4f} "
          f"nit={rs.nit} status={rs.status} root_travel={rt:.3f}mm")


if __name__ == "__main__":
    main()
