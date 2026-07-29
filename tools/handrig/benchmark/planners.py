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
import numpy as np
from scipy.optimize import minimize
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


class TrajoptPlanner(Planner):
    """First REAL planner: whole-phrase kinematic trajectory optimization.

    Naive solves each moment MEMORYLESSLY — it re-centres the hand on every note
    and so shifts/travels more than needed. Trajopt optimizes ALL moment states
    JOINTLY: per-knot the frozen solver cost still demands feasible contacts, but
    two cross-knot terms are added over the whole phrase:
      - positional commitment: penalize inter-moment ROOT motion (keep the hand
        in one place unless a note is geometrically out of reach);
      - smoothness: penalize inter-moment finger/thumb ANGLE change (minimum-
        jerk-like; no thrashing between poses).
    Because it sees every note at once, it can commit to a hand position that
    covers them all — the mechanism that should beat memoryless solving.

    Hyperparameters are set ONCE from reasoning (economy), NOT tuned to pass:
      W_ROOT   large so a 5 cm hand move (0.0025 m^2) is comparable to the
               contact term (~8); 300 -> 0.05 m costs ~0.75.
      W_SMOOTH modest angle-smoothness in radians^2.
    """
    name = "trajopt"
    W_ROOT = 300.0
    W_SMOOTH = 1.5

    def plan(self, phrase, references, solver, config):
        cfg = config or {}
        moments = group_moments(phrase.events)
        specs_per = [[dict(string=e.string, fret=e.fret, finger=e.finger,
                           articulation=e.articulation) for e in evs]
                     for _, evs in moments]
        N = len(moments)

        # Warm start from the naive per-moment solutions (each feasible).
        naive = NaivePlanner().plan(phrase, references, solver, cfg)
        init = [np.asarray(k.mpfb_state, float) for k in naive.knots]
        S = init[0].shape[0]

        if N == 1:                      # nothing to couple; naive == optimum
            k = naive.knots[0]
            k.meta["planner_note"] = "single moment; trajopt == naive"
            return Trajectory(phrase.id, naive.knots, naive.contact_schedule,
                              self.name, solver.version())

        # Shared committed root centre = centroid of the naive roots, so every
        # knot's root box surrounds one position (enables commitment).
        centre = np.mean([solver.root_of(s) for s in init], axis=0)
        los, his = zip(*[solver.knot_bounds(centre, root_pad=0.15) for _ in range(N)])
        lo = np.concatenate(los); hi = np.concatenate(his)
        x0 = np.clip(np.concatenate(init), lo, hi)

        def objective(x):
            states = [x[i*S:(i+1)*S] for i in range(N)]
            total = sum(solver.evaluate_state(states[i], specs_per[i]) for i in range(N))
            for i in range(1, N):
                dr = states[i][:3] - states[i-1][:3]
                dq = states[i][6:] - states[i-1][6:]
                total += self.W_ROOT * float(dr @ dr)
                total += self.W_SMOOTH * float(dq @ dq)
            return total

        res = minimize(objective, x0, method="L-BFGS-B", bounds=list(zip(lo, hi)),
                       options=dict(maxiter=cfg.get("maxiter", 600), ftol=1e-9))
        xopt = res.x
        knots = []
        for i, (t, _) in enumerate(moments):
            state = xopt[i*S:(i+1)*S]
            cmm = solver.contact_error_mm(state.tolist(), specs_per[i])
            knots.append(Knot(t=t, mpfb_state=state.tolist(),
                              meta=dict(feasible=bool(cmm <= 6.0), contact_mm=round(cmm, 3),
                                        contacts=specs_per[i])))
        return Trajectory(phrase.id, knots, naive.contact_schedule,
                          self.name, solver.version())


PLANNERS = {NaivePlanner.name: NaivePlanner,
            RecenterPlanner.name: RecenterPlanner,
            TrajoptPlanner.name: TrajoptPlanner}


def get_planner(name):
    if name not in PLANNERS:
        raise KeyError(f"unknown planner {name!r}; registered: {sorted(PLANNERS)}")
    return PLANNERS[name]()
