"""M7 — first trustworthy eval: trajopt vs naive against the PRE-REGISTERED
V1 gates (design doc PHRASE_MOTION_BENCHMARK.md section 8, frozen before any
planner existed).

Gates (naive baseline vs trajopt, our 5-phrase V1 subset of the 34-phrase suite):
  1 contact non-regression (hard): every trajopt phrase feasible (<=6mm).
  2 no hard-gate regression (hard): no feasible phrase becomes infeasible.
  3 shift economy: suite-mean shift_count reduced (target <= 1.1x min_practical).
  4 finger reuse >= +25% absolute.
  5 fingertip + root travel each -20% suite-mean.
  6 anchor retention (finger_reuse on sustained_anchor) >= 0.9.
  7 Movement Score >= baseline + 0.15 (the "+15 points" margin on the 0-1 scale).
  8 visual (deferred to M8).

Prints each gate PASS/FAIL and an HONEST verdict — no tuning to pass.
"""
from __future__ import annotations
import os, sys, json
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import numpy as np

from loader import load_suite, load_phrase, load_reference
from adapters import SolverAdapter, Trajectory, Knot
from report import build_suite_report, write_report, diff_reports
import evaluate as EV

HERE = os.path.dirname(os.path.abspath(__file__))
RES = os.path.join(HERE, "results")
REUSE_FIXTURES = ["repeated_note", "string_crossing", "one_shift_scale"]


def suite_metric(rep, pid, metric, key):
    return rep["phrases"][pid]["metrics"][metric][key]


def rescore_stored(src):
    suite = load_suite(HERE); solver = SolverAdapter()
    stored = json.load(open(src))["trajectories"]; phrases = {}
    for pid in suite.phrases:
        tj = stored[pid]
        knots = [Knot(t=k["t"], mpfb_state=k["mpfb_state"], meta=k["meta"]) for k in tj["knots"]]
        tr = Trajectory(pid, knots, tj["contact_schedule"], tj["planner"], tj["solver_version"])
        ph = load_phrase(HERE, pid); ref = load_reference(HERE, pid)
        phrases[pid] = EV.evaluate_trajectory(tr, ph, ref, solver, weights=suite.weights)
    return dict(metadata=dict(planner="naive"), phrases=phrases)


if __name__ == "__main__":
    suite = load_suite(HERE)
    base = rescore_stored(os.path.join(RES, "movement_report.json"))
    traj = build_suite_report(planner_name="trajopt")
    write_report(traj, os.path.join(RES, "trajopt_report.json"))

    pids = suite.phrases
    def mean(rep, metric, key, subset=None):
        ps = subset or pids
        return float(np.mean([suite_metric(rep, p, metric, key) for p in ps]))

    print(f"{'phrase':16s} {'naive':>28s}   {'trajopt':>28s}")
    for p in pids:
        b, t = base["phrases"][p], traj["phrases"][p]
        print(f"{p:16s} shift={suite_metric(base,p,'shift_count','count')} "
              f"root={suite_metric(base,p,'root_travel','total_mm'):.0f} "
              f"tip={suite_metric(base,p,'fingertip_travel','total_mm'):.0f} "
              f"sc={b['score']:.3f}  |  "
              f"shift={suite_metric(traj,p,'shift_count','count')} "
              f"root={suite_metric(traj,p,'root_travel','total_mm'):.0f} "
              f"tip={suite_metric(traj,p,'fingertip_travel','total_mm'):.0f} "
              f"sc={t['score']:.3f}  feasible={t['hard_gate_pass']}")

    g = {}
    g[1] = all(traj["phrases"][p]["hard_gate_pass"] for p in pids)
    g[2] = all((not base["phrases"][p]["hard_gate_pass"]) or traj["phrases"][p]["hard_gate_pass"]
               for p in pids)
    sh_b, sh_t = mean(base, "shift_count", "count"), mean(traj, "shift_count", "count")
    g[3] = sh_t <= sh_b
    ru_b = mean(base, "finger_reuse", "reuse", REUSE_FIXTURES)
    ru_t = mean(traj, "finger_reuse", "reuse", REUSE_FIXTURES)
    g[4] = (ru_t - ru_b) >= 0.25
    ft_b, ft_t = mean(base, "fingertip_travel", "total_mm"), mean(traj, "fingertip_travel", "total_mm")
    rt_b, rt_t = mean(base, "root_travel", "total_mm"), mean(traj, "root_travel", "total_mm")
    g[5] = (ft_t <= 0.8 * ft_b) and (rt_t <= 0.8 * rt_b)
    an_b = base["phrases"]["sustained_anchor"]["metrics"]["finger_reuse"]["reuse"]
    an_t = traj["phrases"]["sustained_anchor"]["metrics"]["finger_reuse"]["reuse"]
    g[6] = an_t >= 0.9
    sc_b = float(np.mean([base["phrases"][p]["score"] for p in pids]))
    sc_t = float(np.mean([traj["phrases"][p]["score"] for p in pids]))
    g[7] = sc_t >= sc_b + 0.15

    print()
    print(f"gate1 contact-nonregression : {'PASS' if g[1] else 'FAIL'}")
    print(f"gate2 no hard-gate regress  : {'PASS' if g[2] else 'FAIL'}")
    print(f"gate3 shift economy         : {'PASS' if g[3] else 'FAIL'}  mean {sh_b:.2f} -> {sh_t:.2f}")
    print(f"gate4 finger reuse +25%     : {'PASS' if g[4] else 'FAIL'}  {ru_b:.2f} -> {ru_t:.2f} (fingering FIXED)")
    print(f"gate5 travel -20% each      : {'PASS' if g[5] else 'FAIL'}  tip {ft_b:.0f}->{ft_t:.0f}  root {rt_b:.0f}->{rt_t:.0f}")
    print(f"gate6 anchor retention >=0.9: {'PASS' if g[6] else 'FAIL'}  {an_b:.2f} -> {an_t:.2f} (fingering FIXED)")
    print(f"gate7 MovementScore +0.15   : {'PASS' if g[7] else 'FAIL'}  {sc_b:.3f} -> {sc_t:.3f}")
    print("gate8 visual                : DEFERRED (M8)")
    print(f"HARD gates (1,2) hold: {g[1] and g[2]}")
