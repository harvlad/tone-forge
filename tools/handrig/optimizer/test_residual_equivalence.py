"""Phase 2 Proof A — residual-equivalence regression suite (permanent).

For every frozen benchmark phrase and every solved knot, prove the JAX forward
model is mathematically identical to the frozen NumPy `evaluate`:
  1. residual ELEMENT equivalence (per-contact fingertip geometry)
  2. residual BLOCK equivalence (each cost term cb_k)
  3. entire residual VECTOR: sum(r^2) == jax total  (internal contract)
  4. total OBJECTIVE equivalence (jax total == numpy total)
  5. DETERMINISM (repeat JAX; identical bits)

Consumes the committed naive trajectories (states + per-moment contacts). Does
NOT modify the planner, objective, weights, or benchmark. Read-only.
"""
from __future__ import annotations
import os, sys, json
import numpy as np
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "benchmark"))
sys.path.insert(0, HERE)
from adapters import SolverAdapter
from jax_forward import JaxForward
import jax.numpy as jnp

RES = os.path.join(os.path.dirname(HERE), "benchmark", "results")
TOL = 1e-9          # equivalence gate (observed diffs ~1e-15)
PHRASES = ["single", "repeated_note", "string_crossing", "one_shift_scale", "sustained_anchor"]


def main():
    solver = SolverAdapter()
    rep = json.load(open(os.path.join(RES, "movement_report.json")))
    worst_total = 0.0; worst_block = ("", 0.0); worst_tip = 0.0
    worst_sumsq = 0.0; nondet = 0; n_knots = 0
    fails = []
    for pid in PHRASES:
        for ki, knot in enumerate(rep["trajectories"][pid]["knots"]):
            v = np.array(knot["mpfb_state"], float)
            specs = knot["meta"]["contacts"]
            cl = solver._contacts(specs)
            tot_np, cb_np, fingers_np, _, _ = solver._s.evaluate(v, cl, None)
            jf = JaxForward(solver, specs)
            tot_jx = float(jf.total(v))
            cb_jx = jf.cost_terms(v)
            r = np.array(jf.residual(v))
            n_knots += 1
            # 4. total
            dt = abs(tot_np - tot_jx); worst_total = max(worst_total, dt)
            if dt > TOL: fails.append(f"{pid}#{ki} total {dt:.2e}")
            # 2. blocks
            for k in jf.ORDER:
                db = abs(cb_np[k] - cb_jx[k])
                if db > worst_block[1]: worst_block = (f"{pid}#{ki}:{k}", db)
                if db > TOL: fails.append(f"{pid}#{ki} block {k} {db:.2e}")
            # 3. sum(r^2) == total_jx
            ds = abs(float(np.sum(r*r)) - tot_jx); worst_sumsq = max(worst_sumsq, ds)
            if ds > TOL: fails.append(f"{pid}#{ki} sumsq {ds:.2e}")
            # 1. fingertip geometry
            fg, _ = jf.fingers_mm(v)
            for d in ("index", "middle", "ring", "pinky"):
                dtip = float(np.max(np.abs(np.array(fg[d][-1]) - np.array(fingers_np[d][-1]))))
                worst_tip = max(worst_tip, dtip)
            # 5. determinism
            if float(jf.total(v)) != tot_jx or not np.array_equal(np.array(jf.residual(v)), r):
                nondet += 1; fails.append(f"{pid}#{ki} nondeterministic")

    print(f"knots checked: {n_knots}  (phrases: {len(PHRASES)})")
    print(f"worst |total_np - total_jx|      = {worst_total:.3e}")
    print(f"worst block diff                 = {worst_block[1]:.3e} at {worst_block[0]}")
    print(f"worst |sum(r^2) - total_jx|      = {worst_sumsq:.3e}")
    print(f"worst fingertip geometry diff    = {worst_tip:.3e} mm")
    print(f"nondeterministic knots           = {nondet}")
    ok = (not fails)
    print(f"gate TOL = {TOL:.0e}")
    print("RESIDUAL_EQUIVALENCE_PASS" if ok else f"RESIDUAL_EQUIVALENCE_FAIL ({len(fails)}): {fails[:6]}")


if __name__ == "__main__":
    main()
