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

    def _contacts(self, events):
        """A note is a physical PointContact. No chord/pitch logic — the
        contact carries only geometry (string, fret, assigned finger)."""
        return [CN.PointContact(string=e.string, fret=e.fret, finger=e.finger,
                                articulation=e.articulation) for e in events]

    def solve_pose(self, events, prev_state=None, config=None):
        cfg = config or {}
        pv = np.array(prev_state) if prev_state is not None else None
        r = self._s.solve(self._contacts(events), prev=pv,
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

    def contact_error_mm(self, state, events):
        """Worst required-contact miss recomputed purely from fk(state)."""
        fkr = self.fk(state)
        worst = 0.0
        for e in events:
            tip = np.array(fkr["fingers"][e.finger][-1])
            c = CN.PointContact(string=e.string, fret=e.fret, finger=e.finger)
            miss = c.error_mm(type("fk", (), {"tip": tip}))
            worst = max(worst, miss)
        return worst

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
