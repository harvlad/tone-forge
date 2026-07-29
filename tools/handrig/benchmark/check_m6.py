"""M6 done-when check (binary) — timing metric.

- On the NAIVE `single` trajectory: timing reports a defined value (1.0 —
  vacuously perfect, poses sit at onsets).
- On HAND-BUILT trajectories where the fingertip reaches the contact region
  on-time / late / very-late, the metric penalizes monotonically.

Builds the late variants by prepending an OFF-CONTACT knot (finger lifted just
off the board) at t=0, then the real (in-region) pose at a delayed time — so
the earliest in-region time = the delay = the arrival.
"""
from __future__ import annotations
import os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import numpy as np

from loader import load_phrase, load_reference, load_suite
from adapters import SolverAdapter, Trajectory, Knot
from planners import get_planner
import evaluate as EV
import mpfb_solver as M

HERE = os.path.dirname(os.path.abspath(__file__))


def lifted(state):
    """Same pose, fingertips pulled off the board (root -Y) so no contact."""
    s = list(state)
    s[1] -= 0.030          # root y: 30mm away from the neck -> out of region
    return s


if __name__ == "__main__":
    solver = SolverAdapter()
    ph = load_phrase(HERE, "single"); ref = load_reference(HERE, "single")
    suite = load_suite(HERE)
    good = get_planner("naive").plan(ph, ref, solver, {"restarts": 5})
    contact_state = good.knots[0].mpfb_state
    sched = good.contact_schedule

    def timing_of(knots):
        tr = Trajectory("single", knots, sched, "handbuilt", solver.version())
        return EV.timing(tr, ph, ref, solver)

    # naive: single knot at onset t=0, in region -> vacuously perfect
    naive_t = EV.timing(good, ph, ref, solver)

    # on-time: lifted at t=-... not allowed (t>=0). Use: in-region at t=0.
    on_time = timing_of([Knot(0.0, contact_state, good.knots[0].meta)])
    # late: off-contact at t=0, arrives in region at t=0.15 (>16th at 90bpm=167ms? 150<167)
    late = timing_of([Knot(0.0, lifted(contact_state), good.knots[0].meta),
                      Knot(0.15, contact_state, good.knots[0].meta)])
    # very late: arrives at t=0.30 (well past a 16th)
    very_late = timing_of([Knot(0.0, lifted(contact_state), good.knots[0].meta),
                           Knot(0.30, contact_state, good.knots[0].meta)])

    qn = naive_t["timing_score"]; q0 = on_time["timing_score"]
    ql = late["timing_score"]; qv = very_late["timing_score"]
    print(f"naive(defined)   = {qn}")
    print(f"on_time (0ms)    = {q0}   late[0]={on_time['per_note'][0]['late_ms']}ms")
    print(f"late    (150ms)  = {ql}   late[0]={late['per_note'][0]['late_ms']}ms")
    print(f"very_late(300ms) = {qv}   late[0]={very_late['per_note'][0]['late_ms']}ms")
    print(f"decay_ms={late['decay_ms']} perfect_ms={late['perfect_ms']}")

    defined = isinstance(qn, float) and qn == 1.0
    monotonic = q0 > ql > qv and q0 == 1.0 and qv < 0.5
    ok = defined and monotonic
    print(f"DEFINED={defined} MONOTONIC_PENALTY={monotonic}")
    print("M6_OK" if ok else "M6_FAIL")
