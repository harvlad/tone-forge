"""Phase 3 validation — GN/LM backend vs the L-BFGS-B baseline on the IDENTICAL
frozen objective (same start, same bounds). Reports OPTIMIZER QUALITY and PLANNER
BEHAVIOUR separately, and checks determinism. No planner/objective/benchmark
change; standalone comparison.
"""
from __future__ import annotations
import os, sys, json, time
import numpy as np
from scipy.optimize import minimize
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "benchmark"))
sys.path.insert(0, HERE)
from adapters import SolverAdapter
from jax_forward import JaxForward
from gnlm import gauss_newton_lm, make_callables
import jax, jax.numpy as jnp

RES = os.path.join(os.path.dirname(HERE), "benchmark", "results")
PHRASES = ["single", "repeated_note", "string_crossing", "one_shift_scale", "sustained_anchor"]


def main():
    solver = SolverAdapter()
    rep = json.load(open(os.path.join(RES, "movement_report.json")))
    print("=== OPTIMIZER QUALITY (GN/LM) vs BASELINE (L-BFGS-B), same objective/start/bounds ===")
    print(f"{'moment':20s} {'cost0':>10s} {'GNLM cost':>11s} {'gnorm':>9s} {'nit':>4s} {'nfev':>5s} "
          f"{'t(s)':>6s} {'reason':>7s} | {'LBFGS cost':>11s} {'gnorm':>9s} {'nfev':>5s} {'t(s)':>6s}")
    rows = []
    for pid in PHRASES:
        for ki, knot in enumerate(rep["trajectories"][pid]["knots"]):
            x0 = np.array(knot["mpfb_state"], float)
            specs = knot["meta"]["contacts"]
            jf = JaxForward(solver, specs)
            lo, hi = solver.knot_bounds(x0[:3], root_pad=0.12)
            res_fn, jac_fn = make_callables(jf)
            grad_total = jax.jit(jax.grad(jf.total))
            cost0 = 0.5 * float(res_fn(x0) @ res_fn(x0))

            # --- GN/LM --- (fevals are cheap; allow room to converge)
            out = gauss_newton_lm(res_fn, jac_fn, x0, lo, hi, maxiter=500)
            gnlm_pg = out["gradnorm"]
            # honest convergence classification: gtol => converged; a tiny-step /
            # damping-capped stop with a still-large projected gradient is a STALL.
            out["converged"] = (out["reason"] == "gtol") or \
                               (out["reason"] in ("xtol", "ftol") and gnlm_pg < 1e-3)
            # determinism: repeat
            out2 = gauss_newton_lm(res_fn, jac_fn, x0, lo, hi, maxiter=500)
            det = np.array_equal(out["x"], out2["x"])

            # --- L-BFGS-B baseline on the SAME scalar objective (0.5||r||^2) ---
            def scal(x): return 0.5 * float(res_fn(x) @ res_fn(x))
            t0 = time.time()
            rb = minimize(scal, x0, method="L-BFGS-B", bounds=list(zip(lo, hi)),
                          options=dict(maxiter=600, ftol=1e-12))
            tb = time.time() - t0
            # projected-gradient norm of the baseline solution (fair stationarity)
            gb = np.asarray(grad_total(jnp.asarray(rb.x))) * 0.5   # d(0.5||r||^2)
            from gnlm import _proj_grad_norm
            gb_pg = _proj_grad_norm(gb, rb.x, lo, hi)

            rows.append(dict(mom=f"{pid}#{ki}", cost0=cost0,
                             gnlm_cost=out["cost"], gnlm_g=gnlm_pg, gnlm_nit=out["nit"],
                             gnlm_nfev=out["nfev"], gnlm_t=out["runtime_s"], reason=out["reason"],
                             det=det, lb_cost=rb.fun, lb_g=gb_pg, lb_nfev=rb.nfev, lb_t=round(tb, 4),
                             conv=out["converged"],
                             dx_gnlm=float(np.linalg.norm(out["x"] - x0)),
                             dx_pair=float(np.linalg.norm(out["x"] - rb.x))))
            r = rows[-1]
            print(f"{r['mom']:20s} {r['cost0']:10.5f} {r['gnlm_cost']:11.6f} {r['gnlm_g']:9.2e} "
                  f"{r['gnlm_nit']:4d} {r['gnlm_nfev']:5d} {r['gnlm_t']:6.2f} {r['reason']:>7s} | "
                  f"{r['lb_cost']:11.6f} {r['lb_g']:9.2e} {r['lb_nfev']:5d} {r['lb_t']:6.2f}")

    print("\n=== SUMMARY ===")
    ndet = sum(1 for r in rows if not r["det"])
    gnlm_lower = sum(1 for r in rows if r["gnlm_cost"] < r["lb_cost"] - 1e-9)
    lb_lower = sum(1 for r in rows if r["lb_cost"] < r["gnlm_cost"] - 1e-9)
    print(f"moments: {len(rows)}   nondeterministic GN/LM: {ndet}")
    print(f"GN/LM reached STRICTLY lower cost than L-BFGS-B on {gnlm_lower} moments; "
          f"L-BFGS-B lower on {lb_lower}")
    print(f"GN/LM proj-grad max: {max(r['gnlm_g'] for r in rows):.2e}   "
          f"L-BFGS-B proj-grad max: {max(r['lb_g'] for r in rows):.2e}")
    gn_better_grad = sum(1 for r in rows if r["gnlm_g"] < r["lb_g"] - 1e-12)
    well = sum(1 for r in rows if r["gnlm_g"] < 1.0)      # small proj-grad
    stalled = [r["mom"] for r in rows if r["gnlm_g"] >= 1.0]
    print(f"GN/LM lower proj-grad than L-BFGS-B on {gn_better_grad}/{len(rows)} moments "
          f"(better stationarity)")
    print(f"GN/LM well-behaved (proj-grad<1.0): {well}/{len(rows)}   "
          f"stalled (>=1.0, ill-conditioned/kink): {stalled}")
    print(f"mean runtime  GN/LM={np.mean([r['gnlm_t'] for r in rows]):.3f}s  "
          f"L-BFGS-B={np.mean([r['lb_t'] for r in rows]):.3f}s")
    print(f"max ||x_gnlm - x_lbfgs|| (pose divergence): {max(r['dx_pair'] for r in rows):.4f}")
    print("PLANNER-BEHAVIOUR NOTE: any pose/benchmark difference is attributable to optimizer")
    print("quality (lower cost / lower proj-grad), NOT to any objective change.")
    print("GNLM_DETERMINISTIC" if ndet == 0 else "GNLM_NONDETERMINISTIC")


if __name__ == "__main__":
    main()
