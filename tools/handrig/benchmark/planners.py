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


def group_moments(events):
    """Group events by timestamp into MOMENTS (simultaneous contacts). A
    single note is a one-contact moment; a chord or a held-anchor+moving-note
    is a multi-contact moment. Deterministic order (sorted by t)."""
    by_t = {}
    for e in events:
        by_t.setdefault(round(e.t, 6), []).append(e)
    return [(t, by_t[t]) for t in sorted(by_t)]


class NaivePlanner(Planner):
    """Per-moment baseline: solve each moment independently, warm-started from
    the previous knot. No look-ahead across moments, no reference awareness —
    it cannot choose to HOLD a note between moments (that is what the phrase
    planner will exploit). One knot per moment."""
    name = "naive"

    def plan(self, phrase, references, solver, config):
        cfg = config or {}
        knots, prev = [], None
        for t, evs in group_moments(phrase.events):
            specs = [dict(string=e.string, fret=e.fret, finger=e.finger,
                          articulation=e.articulation) for e in evs]
            state, feasible, cmm, _ = solver.solve_pose(specs, prev_state=prev, config=cfg)
            knots.append(Knot(t=t, mpfb_state=state,
                              meta=dict(feasible=feasible, contact_mm=cmm,
                                        contacts=specs)))
            prev = state
        sched = [dict(t=e.t, string=e.string, fret=e.fret, finger=e.finger,
                      articulation=e.articulation) for e in phrase.events]
        return Trajectory(phrase_id=phrase.id, knots=knots, contact_schedule=sched,
                          planner=self.name, solver_version=solver.version())


class RecenterPlanner(NaivePlanner):
    """Deliberately WORSE baseline (for regression/monotonicity tests): solves
    each moment with NO warm-start, so the hand recentres from scratch on every
    note instead of holding position. Same contacts (still feasible) but more
    root travel and more shifts — economy strictly no better than naive."""
    name = "recenter"

    def plan(self, phrase, references, solver, config):
        cfg = config or {}
        knots = []
        for t, evs in group_moments(phrase.events):
            specs = [dict(string=e.string, fret=e.fret, finger=e.finger,
                          articulation=e.articulation) for e in evs]
            state, feasible, cmm, _ = solver.solve_pose(specs, prev_state=None, config=cfg)
            knots.append(Knot(t=t, mpfb_state=state,
                              meta=dict(feasible=feasible, contact_mm=cmm,
                                        contacts=specs)))
        sched = [dict(t=e.t, string=e.string, fret=e.fret, finger=e.finger,
                      articulation=e.articulation) for e in phrase.events]
        return Trajectory(phrase_id=phrase.id, knots=knots, contact_schedule=sched,
                          planner=self.name, solver_version=solver.version())


PLANNERS = {NaivePlanner.name: NaivePlanner, RecenterPlanner.name: RecenterPlanner}


def get_planner(name):
    if name not in PLANNERS:
        raise KeyError(f"unknown planner {name!r}; registered: {sorted(PLANNERS)}")
    return PLANNERS[name]()
