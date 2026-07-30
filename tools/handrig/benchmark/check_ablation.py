"""Resist-only root-movement ABLATION — three-way frozen evaluation.

Systems: NAIVE, ORIGINAL TRAJOPT, RESIST-ONLY TRAJOPT.
Exact frozen benchmark: same 5 phrases, fingerings, references, solver, metrics,
scoring, weights, thresholds. Naive + original trajopt are RE-SCORED from their
committed trajectories (FK only, no re-solve); resist is solved fresh.

Outputs: full per-phrase metrics + deltas, the frozen V1 gate for each trajopt
vs naive (A), the narrow ablation verdict (B: PASS/MIXED/FAIL), and explicit
shift-refusal / new-pathology probes.
"""
from __future__ import annotations
import os, sys, json
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import numpy as np

from loader import load_suite, load_phrase, load_reference
from adapters import SolverAdapter, Trajectory, Knot
from report import build_suite_report, write_report
import evaluate as EV

HERE = os.path.dirname(os.path.abspath(__file__))
RES = os.path.join(HERE, "results")
REUSE_FIXTURES = ["repeated_note", "string_crossing", "one_shift_scale"]


def rescore(src, planner_name):
    suite = load_suite(HERE); solver = SolverAdapter()
    stored = json.load(open(src))["trajectories"]; phrases = {}
    for pid in suite.phrases:
        tj = stored[pid]
        knots = [Knot(t=k["t"], mpfb_state=k["mpfb_state"], meta=k["meta"]) for k in tj["knots"]]
        tr = Trajectory(pid, knots, tj["contact_schedule"], tj["planner"], tj["solver_version"])
        ph = load_phrase(HERE, pid); ref = load_reference(HERE, pid)
        phrases[pid] = EV.evaluate_trajectory(tr, ph, ref, solver, weights=suite.weights)
    return dict(metadata=dict(planner=planner_name), phrases=phrases)


def M(rep, pid, metric, key):
    return rep["phrases"][pid]["metrics"][metric][key]


def v1_gates(base, cand):
    suite = load_suite(HERE); pids = suite.phrases
    def mean(rep, metric, key, subset=None):
        return float(np.mean([M(rep, p, metric, key) for p in (subset or pids)]))
    g = {}
    g[1] = all(cand["phrases"][p]["hard_gate_pass"] for p in pids)
    g[2] = all((not base["phrases"][p]["hard_gate_pass"]) or cand["phrases"][p]["hard_gate_pass"]
               for p in pids)
    g[3] = mean(cand, "shift_count", "count") <= mean(base, "shift_count", "count")
    g[4] = (mean(cand, "finger_reuse", "reuse", REUSE_FIXTURES)
            - mean(base, "finger_reuse", "reuse", REUSE_FIXTURES)) >= 0.25
    g[5] = (mean(cand, "fingertip_travel", "total_mm") <= 0.8 * mean(base, "fingertip_travel", "total_mm")
            and mean(cand, "root_travel", "total_mm") <= 0.8 * mean(base, "root_travel", "total_mm"))
    g[6] = cand["phrases"]["sustained_anchor"]["metrics"]["finger_reuse"]["reuse"] >= 0.9
    sb = float(np.mean([base["phrases"][p]["score"] for p in pids]))
    sc = float(np.mean([cand["phrases"][p]["score"] for p in pids]))
    g[7] = sc >= sb + 0.15
    return g


if __name__ == "__main__":
    suite = load_suite(HERE); pids = suite.phrases
    naive = rescore(os.path.join(RES, "movement_report.json"), "naive")
    # re-score naive scores from the M5 baseline (movement_report has stub scores)
    scored_naive = json.load(open(os.path.join(RES, "baseline_naive.json")))["phrases"]
    for p in pids:
        naive["phrases"][p]["score"] = scored_naive[p]["score"]
        naive["phrases"][p]["scoring"] = scored_naive[p]["scoring"]
    trajopt = rescore(os.path.join(RES, "trajopt_report.json"), "trajopt")
    resist = build_suite_report(planner_name="trajopt_resist")
    write_report(resist, os.path.join(RES, "trajopt_resist_report.json"))

    reps = {"naive": naive, "trajopt": trajopt, "trajopt_resist": resist}
    print("=" * 96)
    for p in pids:
        print(f"\n### {p}")
        print(f"{'':22s} {'naive':>12s} {'trajopt':>12s} {'resist':>12s}")
        def row(label, metric, key, fmt="{:.3f}"):
            vals = [M(reps[s], p, metric, key) for s in reps]
            print(f"{label:22s} " + " ".join(fmt.format(v).rjust(12) for v in vals))
        print(f"{'hard_gate':22s} " + " ".join(str(reps[s]['phrases'][p]['hard_gate_pass']).rjust(12) for s in reps))
        row("shift_count", "shift_count", "count", "{:.0f}")
        row("root_travel_mm", "root_travel", "total_mm", "{:.1f}")
        row("fingertip_travel_mm", "fingertip_travel", "total_mm", "{:.1f}")
        row("worst_contact_mm", "contact_fidelity", "worst_contact_mm")
        row("finger_reuse", "finger_reuse", "reuse")
        row("timing", "timing", "timing_score")
        print(f"{'tier2_score':22s} " + " ".join(str(reps[s]['phrases'][p]['scoring']['tier2_score']).rjust(12) for s in reps))
        print(f"{'tier3_score':22s} " + " ".join(str(reps[s]['phrases'][p]['scoring']['tier3_score']).rjust(12) for s in reps))
        print(f"{'movement_score':22s} " + " ".join(f"{reps[s]['phrases'][p]['score']:.3f}".rjust(12) for s in reps))
        for s in reps:
            tags = ",".join(f["tag"] for f in reps[s]["phrases"][p]["failures"]) or "-"
            print(f"   failures[{s}]: {tags}")

    # ---- A. frozen V1 gate (unchanged), both trajopt variants vs naive ----
    print("\n" + "=" * 96)
    print("A. FROZEN V1 GATE (unchanged) vs naive baseline")
    for cand_name in ("trajopt", "trajopt_resist"):
        g = v1_gates(naive, reps[cand_name])
        print(f"  {cand_name:16s} gates 1-7: " + " ".join(f"{k}:{'P' if g[k] else 'F'}" for k in sorted(g)))

    # ---- B. ablation hypothesis ----
    print("\nB. ABLATION HYPOTHESIS: resist-only preserves the win, removes the regressions")
    win = M(resist, "one_shift_scale", "shift_count", "count")
    win_feas = resist["phrases"]["one_shift_scale"]["hard_gate_pass"]
    win_wc = M(resist, "one_shift_scale", "contact_fidelity", "worst_contact_mm")
    sc_root = M(resist, "string_crossing", "root_travel", "total_mm")
    an_root = M(resist, "sustained_anchor", "root_travel", "total_mm")
    sc_root_orig = M(trajopt, "string_crossing", "root_travel", "total_mm")
    an_root_orig = M(trajopt, "sustained_anchor", "root_travel", "total_mm")
    sc_root_naive = M(naive, "string_crossing", "root_travel", "total_mm")
    an_root_naive = M(naive, "sustained_anchor", "root_travel", "total_mm")
    preserved_win = (win == 1) and win_feas
    fixed_sc = sc_root <= sc_root_naive + 2.0     # back to ~naive (<=1mm+margin)
    fixed_an = an_root <= an_root_naive + 2.0
    print(f"  one_shift_scale: shift={win} feasible={win_feas} worst={win_wc}mm  -> win preserved={preserved_win}")
    print(f"  string_crossing root: naive {sc_root_naive:.1f} | orig {sc_root_orig:.1f} | resist {sc_root:.1f}  -> fixed={fixed_sc}")
    print(f"  sustained_anchor root: naive {an_root_naive:.1f} | orig {an_root_orig:.1f} | resist {an_root:.1f}  -> fixed={fixed_an}")

    # ---- shift-refusal / new-pathology probes ----
    print("\nSHIFT-REFUSAL / NEW-PATHOLOGY PROBES")
    refusal = (win == 0) or (not win_feas) or (win_wc > EV.CONTACT_TOL_MM)
    print(f"  shift_refusal (one_shift needs a shift; resist refused/infeasible?): {refusal}")
    for p in pids:
        dc = M(resist, p, "contact_fidelity", "worst_contact_mm") - M(naive, p, "contact_fidelity", "worst_contact_mm")
        dt = M(resist, p, "fingertip_travel", "total_mm") - M(naive, p, "fingertip_travel", "total_mm")
        near = M(resist, p, "contact_fidelity", "worst_contact_mm") > 0.8 * EV.CONTACT_TOL_MM
        print(f"  {p:16s} d_worst_contact={dc:+.3f}mm d_fingertip={dt:+.1f}mm near_feas_limit={near}")

    if preserved_win and fixed_sc and fixed_an and not refusal:
        verdict = "PASS"
    elif preserved_win and (fixed_sc or fixed_an) and not refusal:
        verdict = "MIXED"
    elif not preserved_win:
        verdict = "FAIL"
    else:
        verdict = "MIXED"
    print(f"\nABLATION VERDICT (B): {verdict}")
