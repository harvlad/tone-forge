"""Phase 4 — classify planner behaviour with the validated GN/LM optimizer.

Re-solves every frozen benchmark moment with the Phase-3 GN/LM backend EXACTLY as
delivered (no tuning), warm-started from the committed naive (L-BFGS-B) pose and
using the same contacts + bounds, then scores the resulting trajectory with the
FROZEN benchmark metrics. Compares against the committed naive to determine which
behaviours are optimizer artefacts vs objective artefacts.

Does NOT modify planner / optimizer / benchmark / objective. Investigation only.
"""
from __future__ import annotations
import os, sys, json
import numpy as np
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "benchmark"))
sys.path.insert(0, HERE)
from adapters import SolverAdapter, Trajectory, Knot
from loader import load_suite, load_phrase, load_reference
from jax_forward import JaxForward
from gnlm import gauss_newton_lm, make_callables
import evaluate as EV

RES = os.path.join(os.path.dirname(HERE), "benchmark", "results")
PHRASES = ["single", "repeated_note", "string_crossing", "one_shift_scale", "sustained_anchor"]


def gnlm_trajectory(solver, phrase, naive_knots):
    """One GN/LM knot per moment, warm-started from the committed naive pose."""
    knots = []; per = []
    for knot in naive_knots:
        x0 = np.array(knot["mpfb_state"], float)
        specs = knot["meta"]["contacts"]
        jf = JaxForward(solver, specs)
        lo, hi = solver.knot_bounds(x0[:3], root_pad=0.12)
        res_fn, jac_fn = make_callables(jf)
        out = gauss_newton_lm(res_fn, jac_fn, x0, lo, hi, maxiter=500)
        state = out["x"].tolist()
        cmm = solver.contact_error_mm(state, specs)
        knots.append(Knot(t=knot["t"], mpfb_state=state,
                          meta=dict(feasible=bool(cmm <= 6.0), contact_mm=round(cmm, 3),
                                    contacts=specs)))
        per.append(dict(cost=out["cost"], g=out["gradnorm"], reason=out["reason"],
                        wc=round(cmm, 3)))
    return knots, per


def main():
    solver = SolverAdapter()
    suite = load_suite(os.path.join(os.path.dirname(HERE), "benchmark"))
    naive_rep = json.load(open(os.path.join(RES, "movement_report.json")))
    base = json.load(open(os.path.join(RES, "baseline_naive.json")))["phrases"]
    BDIR = os.path.join(os.path.dirname(HERE), "benchmark")

    print("=== FROZEN METRICS: naive(L-BFGS-B, committed)  vs  naive(GN/LM) ===")
    print(f"{'phrase':16s} {'metric':16s} {'L-BFGS-B':>10s} {'GN/LM':>10s}")
    summary = {}
    for pid in PHRASES:
        ph = load_phrase(BDIR, pid); ref = load_reference(BDIR, pid)
        nk = naive_rep["trajectories"][pid]["knots"]
        gk, per = gnlm_trajectory(solver, ph, nk)
        tr = Trajectory(pid, gk, naive_rep["trajectories"][pid]["contact_schedule"],
                        "naive_gnlm", solver.version())
        ev = EV.evaluate_trajectory(tr, ph, ref, solver, weights=suite.weights)
        b = base[pid]
        def g(rep, m, k): return rep["metrics"][m][k]
        for label, m, k in (("shift_count", "shift_count", "count"),
                            ("root_travel", "root_travel", "total_mm"),
                            ("fingertip_trav", "fingertip_travel", "total_mm"),
                            ("worst_contact", "contact_fidelity", "worst_contact_mm"),
                            ("finger_reuse", "finger_reuse", "reuse")):
            print(f"{pid:16s} {label:16s} {g(b,m,k):10.3f} {g(ev,m,k):10.3f}")
        print(f"{pid:16s} {'movement_score':16s} {b['score']:10.3f} {ev['score']:10.3f}")
        stalls = [i for i, p in enumerate(per) if p["g"] >= 1.0]
        print(f"{'':16s} GN/LM per-moment: " +
              " ".join(f"m{i}(c{p['cost']:.2f},g{p['g']:.1e},{p['reason']},wc{p['wc']})"
                       for i, p in enumerate(per)))
        summary[pid] = dict(b=b, ev=ev, per=per, stalls=stalls)
    return summary


if __name__ == "__main__":
    main()
