"""M3 — planner plugin interface + registry.

The ONE tight contract that must stay stable forever:

    Planner.plan(phrase, references, solver, config) -> Trajectory

It carries everything a *future* trajectory planner needs, on purpose:
  - `phrase`     : the WHOLE phrase (all events + fixed fingering), so a
                   planner can look ahead across notes — not one note at a time.
  - `references` : {A: mechanical, B: acceptance-region}, so a planner can
                   optimize toward the behavioural region, not just contacts.
  - `solver`     : the SolverAdapter (a planner may call solve_pose per knot,
                   or use fk() to score candidate states).
  - `config`     : run knobs (restarts, planner-specific params).

The naive per-note planner ignores `references` and look-ahead — that is the
whole point: it is the baseline the phrase planner must beat. Registering it
through the same interface proves the interface fits the trivial case; the
whole-phrase signature proves it will fit the hard case (caught here, cheaply,
before a real trajectory planner exists — the M3 risk).
"""
from __future__ import annotations
from adapters import Trajectory, Knot


class Planner:
    name = "abstract"

    def plan(self, phrase, references, solver, config) -> "Trajectory":
        raise NotImplementedError


class NaivePlanner(Planner):
    """Per-note baseline: solve each event independently, warm-started from the
    previous knot. No look-ahead, no reference awareness. One knot per event."""
    name = "naive"

    def plan(self, phrase, references, solver, config):
        cfg = config or {}
        knots, prev = [], None
        for e in phrase.events:
            state, feasible, cmm, _ = solver.solve_pose([e], prev_state=prev, config=cfg)
            knots.append(Knot(t=e.t, mpfb_state=state,
                              meta=dict(feasible=feasible, contact_mm=cmm)))
            prev = state
        sched = [dict(t=e.t, string=e.string, fret=e.fret, finger=e.finger,
                      articulation=e.articulation) for e in phrase.events]
        return Trajectory(phrase_id=phrase.id, knots=knots, contact_schedule=sched,
                          planner=self.name, solver_version=solver.version())


PLANNERS = {NaivePlanner.name: NaivePlanner}


def get_planner(name):
    if name not in PLANNERS:
        raise KeyError(f"unknown planner {name!r}; registered: {sorted(PLANNERS)}")
    return PLANNERS[name]()
