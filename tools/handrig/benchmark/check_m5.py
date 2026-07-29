"""M5 done-when check (binary).

1. Pin the naive baseline (re-scored from the stored M4 trajectories — FK only,
   no re-solve, so this is fast).
2. Run the deliberately-worse `recenter` planner (no warm-start) and score it.
3. Diff recenter vs naive: assert NO phrase improved and the aggregate regressed
   -> the soft Movement Score moves monotonically with an obvious quality drop.
4. Hand-construct an INFEASIBLE trajectory (offset the root so the contact
   misses) and assert the classifier tags `infeasible_contact` and score == 0.

Prints M5_OK iff all clauses hold.
"""
from __future__ import annotations
import os, sys, json
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import numpy as np

from loader import load_suite, load_phrase, load_reference
from adapters import SolverAdapter, Trajectory, Knot, _traj_json
from planners import get_planner
import evaluate as EV
import mpfb_solver as M
from report import build_suite_report, diff_reports, write_report

HERE = os.path.dirname(os.path.abspath(__file__))
RES = os.path.join(HERE, "results")


def rescore_stored(planner_name, src):
    """Re-score stored trajectories (from a prior report) under current scoring
    — no re-solve. Returns a report dict shaped like build_suite_report's."""
    suite = load_suite(HERE)
    solver = SolverAdapter()
    stored = json.load(open(src))["trajectories"]
    phrases = {}
    for pid in suite.phrases:
        tj = stored[pid]
        knots = [Knot(t=k["t"], mpfb_state=k["mpfb_state"], meta=k["meta"])
                 for k in tj["knots"]]
        tr = Trajectory(pid, knots, tj["contact_schedule"], tj["planner"],
                        tj["solver_version"])
        ph = load_phrase(HERE, pid); ref = load_reference(HERE, pid)
        phrases[pid] = EV.evaluate_trajectory(tr, ph, ref, solver,
                                              weights=suite.weights)
    return dict(metadata=dict(planner=planner_name), phrases=phrases)


def infeasible_probe():
    """Take the naive `single` knot, shove the root 50mm along the neck so the
    fingertip misses the fret, and classify it."""
    solver = SolverAdapter()
    ph = load_phrase(HERE, "single"); ref = load_reference(HERE, "single")
    suite = load_suite(HERE)
    good = get_planner("naive").plan(ph, ref, solver, {"restarts": 5})
    bad_state = list(good.knots[0].mpfb_state)
    bad_state[0] += 0.050    # +50mm on root x  -> contact misses
    bad = Trajectory("single", [Knot(t=0.0, mpfb_state=bad_state,
                                     meta=good.knots[0].meta)],
                     good.contact_schedule, "handcrafted_bad", solver.version())
    return EV.evaluate_trajectory(bad, ph, ref, solver, weights=suite.weights)


if __name__ == "__main__":
    ok = True

    # 1. pin naive baseline (fast re-score of M4 trajectories)
    base = rescore_stored("naive", os.path.join(RES, "movement_report.json"))
    write_report(base, os.path.join(RES, "baseline_naive.json"))
    print("naive baseline:")
    for pid, r in base["phrases"].items():
        print(f"  {pid:16s} score={r['score']:.3f}")

    # 2. deliberately-worse run (real planner, needs solving)
    worse = build_suite_report(planner_name="recenter")
    write_report(worse, os.path.join(RES, "recenter_report.json"))
    print("recenter (worse) run:")
    for pid, r in worse["phrases"].items():
        print(f"  {pid:16s} score={r['score']:.3f} gate={r['hard_gate_pass']}")

    # 3. monotonicity: no phrase improved; aggregate regressed
    d = diff_reports(base, worse)
    improved = [r["phrase"] for r in d["rows"] if r["improved"]]
    agg_base = sum(r["baseline"] for r in d["rows"])
    agg_worse = sum(r["current"] for r in d["rows"])
    print(f"diff: any_regressed={d['any_regressed']} improved={improved} "
          f"agg {agg_base:.3f} -> {agg_worse:.3f}")
    mono = (not improved) and (agg_worse < agg_base) and d["any_regressed"]
    ok &= mono
    print(f"  MONOTONIC_WORSE={mono}")

    # 4. failure tagging on a hand-constructed infeasible trajectory
    probe = infeasible_probe()
    tags = [f["tag"] for f in probe["failures"]]
    tagged = ("infeasible_contact" in tags) and (probe["score"] == 0.0) \
             and (not probe["hard_gate_pass"])
    ok &= tagged
    print(f"infeasible probe: score={probe['score']} gate={probe['hard_gate_pass']} "
          f"tags={tags}  TAGGED_OK={tagged}")

    print("M5_OK" if ok else "M5_FAIL")
