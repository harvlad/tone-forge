"""M1 — SolverAdapter + Trajectory + naive per-note planner.

Two frozen interfaces from the harness design:
  Solver.solve_pose(contacts, prev_state, config) -> (state, feasible, per_contact_mm)
  Solver.fk(state) -> {finger: joints_mm, thumb, root}
  Solver.version() -> commit hash

The Trajectory is the planner-output contract: ordered knots
[{t, mpfb_state[30], meta}] + the contact schedule it was solved for.

M1 has NO plugin registry yet (that is M3). `naive_trajectory` is a plain
loop: solve each note independently, warm-started from the previous knot —
the per-note baseline the phrase planner will be compared against.

Run: PYTHONPATH=... python3 adapters.py   # solves `single`, emits results/,
     and checks the FK-reproduces-solver-contact invariant.
"""
from __future__ import annotations
from dataclasses import dataclass, field, asdict
import os, sys, json, subprocess

HERE = os.path.dirname(os.path.abspath(__file__))
HANDRIG = os.path.dirname(HERE)
SOLVER = os.path.join(HANDRIG, "solver")
for p in (SOLVER, HANDRIG):
    if p not in sys.path:
        sys.path.insert(0, p)

import numpy as np
import mpfb_solver as M
import contacts as CN
from loader import load_suite, load_phrase, load_reference, Event

RIG = os.path.join(HANDRIG, "unification", "mpfb_rig.json")


@dataclass
class Knot:
    t: float
    mpfb_state: list          # 30 floats
    meta: dict = field(default_factory=dict)


@dataclass
class Trajectory:
    phrase_id: str
    knots: list               # list[Knot]
    contact_schedule: list    # [{t, string, fret, finger, articulation}]
    planner: str = ""
    solver_version: str = ""


class SolverAdapter:
    """Thin wrapper over the frozen MPFBSolver. Adds nothing; only exposes
    the harness interface so planners never touch the solver internals."""

    def __init__(self, rig_path=RIG):
        self._s = M.MPFBSolver(rig_path)
        self.state_n = M.STATE_N

    @staticmethod
    def _spec(c):
        """Normalize a contact spec: an Event, or a dict with string/fret/
        finger[/articulation]."""
        if isinstance(c, dict):
            return c["string"], c["fret"], c["finger"], c.get("articulation", "fret")
        return c.string, c.fret, c.finger, getattr(c, "articulation", "fret")

    def _contacts(self, specs):
        """A note is a physical PointContact. No chord/pitch logic — the
        contact carries only geometry (string, fret, assigned finger).
        Accepts Events or dicts; a list is one simultaneous MOMENT."""
        out = []
        for c in specs:
            s, fr, fg, art = self._spec(c)
            out.append(CN.PointContact(string=s, fret=fr, finger=fg, articulation=art))
        return out

    def solve_pose(self, specs, prev_state=None, config=None):
        """Solve one MOMENT: `specs` is a list of simultaneous contacts."""
        cfg = config or {}
        pv = np.array(prev_state) if prev_state is not None else None
        r = self._s.solve(self._contacts(specs), prev=pv,
                           restarts=cfg.get("restarts", 5))
        return r["state"], (r["status"] == "OK"), r["max_contact_mm"], r

    def fk(self, state):
        """Recompute world joints (mm) from a 30-DoF state via the validated
        FK. Independent of solve() — this is what proves a stored knot
        reproduces its contact."""
        v = np.asarray(state, float)
        AW = self._s.armature_world(v[M.RLOC], v[M.RROT])
        fingers = {}
        for i, f in enumerate(M.FINGERS):
            four = v[6 + i * 4:6 + i * 4 + 4]
            cup = v[M.META][i]
            fingers[f] = [p.tolist() for p in
                          self._s.finger_joints_mm(f, four, AW, meta_cup=cup)]
        thumb_tip, thumb_js = self._s.thumb_tip_mm(v[M.THUMB], AW)
        return dict(fingers=fingers, thumb=[p.tolist() for p in thumb_js],
                    root=v[M.RLOC].tolist())

    def contact_error_mm(self, state, specs):
        """Worst required-contact miss recomputed purely from fk(state).
        `specs`: Events or dicts (one moment)."""
        fkr = self.fk(state)
        worst = 0.0
        for c in specs:
            s, fr, fg, _ = self._spec(c)
            tip = np.array(fkr["fingers"][fg][-1])
            prim = CN.PointContact(string=s, fret=fr, finger=fg)
            miss = prim.error_mm(type("fk", (), {"tip": tip}))
            worst = max(worst, miss)
        return worst

    def evaluate_state(self, state, specs, prev=None):
        """Frozen per-knot cost (contact+strain+manifold+board+collision+...)
        for one moment. `prev` optional (kept None in trajopt; cross-knot
        coupling is added by the planner, not double-counted here)."""
        v = np.asarray(state, float)
        pv = np.asarray(prev, float) if prev is not None else None
        return float(self._s.evaluate(v, self._contacts(specs), pv)[0])

    def knot_bounds(self, root_center, root_pad=0.15):
        """Per-DoF box bounds for one knot, replicating the frozen solver's
        limits, but with the root centred on a SHARED committed position (so a
        whole-phrase planner can keep the hand in one place)."""
        A = M.A
        lo = np.full(M.STATE_N, -np.inf); hi = np.full(M.STATE_N, np.inf)
        rc = np.asarray(root_center, float)
        lo[M.RLOC] = rc - root_pad; hi[M.RLOC] = rc + root_pad
        lo[M.RROT] = -0.6; hi[M.RROT] = 0.6
        for i, f in enumerate(M.FINGERS):
            L = A.HandModel.default().finger_limits[f]
            b = 6 + i * 4
            lo[b] = L["mcp_flex"].lo; hi[b] = L["mcp_flex"].hi
            lo[b+1] = L["mcp_abd"].lo; hi[b+1] = L["mcp_abd"].hi
            lo[b+2] = L["pip_flex"].lo; hi[b+2] = L["pip_flex"].hi
            lo[b+3] = L["dip_flex"].lo; hi[b+3] = L["dip_flex"].hi
        lo[M.THUMB] = np.array([-10, -10, -15, -30]) * A.D2R
        hi[M.THUMB] = np.array([55, 40, 80, 30]) * A.D2R
        lo[M.META] = np.array([-2, -2, -2, -2]) * A.D2R
        hi[M.META] = np.array([14, 12, 26, 36]) * A.D2R
        return lo, hi

    def root_of(self, state):
        return np.asarray(state, float)[M.RLOC]

    def version(self):
        try:
            h = subprocess.check_output(
                ["git", "-C", HANDRIG, "rev-parse", "--short", "HEAD"],
                stderr=subprocess.DEVNULL).decode().strip()
            return h
        except Exception:
            return "unknown"


def naive_trajectory(phrase, solver: SolverAdapter, config=None):
    """Thin shim kept for the M1 self-test. Since M3 the naive loop lives in
    the registered `NaivePlanner` plugin; this delegates to it."""
    from planners import NaivePlanner
    return NaivePlanner().plan(phrase, None, solver, config or {})


def _traj_json(tr: Trajectory):
    return dict(phrase_id=tr.phrase_id, planner=tr.planner,
                solver_version=tr.solver_version,
                contact_schedule=tr.contact_schedule,
                knots=[asdict(k) for k in tr.knots])


if __name__ == "__main__":
    suite = load_suite(HERE)
    solver = SolverAdapter()
    print(f"solver_version={solver.version()}  state_n={solver.state_n}")
    os.makedirs(os.path.join(HERE, "results"), exist_ok=True)
    ok_all = True
    for pid in suite.phrases:
        ph = load_phrase(HERE, pid)
        tr = naive_trajectory(ph, solver)
        # Invariant: FK on each knot reproduces the solver's feasible contact.
        for k, e in zip(tr.knots, ph.events):
            recomputed = solver.contact_error_mm(k.mpfb_state, [e])
            drift = abs(recomputed - k.meta["contact_mm"])
            feas = "feasible" if k.meta["feasible"] else "INFEASIBLE"
            ok = drift < 0.01
            ok_all &= ok and k.meta["feasible"]
            print(f"  {pid} t={k.t} {feas} solver={k.meta['contact_mm']}mm "
                  f"fk={recomputed:.3f}mm drift={drift:.4f}mm {'OK' if ok else 'MISMATCH'}")
        out = os.path.join(HERE, "results", f"trajectory_{pid}_naive.json")
        json.dump(_traj_json(tr), open(out, "w"), indent=1)
        print(f"  wrote {out}  ({len(tr.knots)} knot(s))")
    print("M1_OK" if ok_all else "M1_FAIL")
