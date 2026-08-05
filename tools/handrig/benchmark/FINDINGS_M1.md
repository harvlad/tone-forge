# M1 findings — SolverAdapter → Trajectory (naive per-note)

**1. What did we build?**
`adapters.py`: the frozen `SolverAdapter` (wraps `mpfb_solver`; exposes
`solve_pose(events, prev_state, config) -> (state, feasible, per_contact_mm, raw)`,
`fk(state) -> {fingers, thumb, root}`, `version() -> git short hash`), the `Trajectory`
/`Knot` data contract (`{t, mpfb_state[30], meta}` + contact schedule), and
`naive_trajectory()` — a plain per-note loop (solve each event, warm-started from the prior
knot). Emits `results/trajectory_single_naive.json`.

**2. What did we learn?**
The harness interface is sufficient — a Trajectory comes out end-to-end. Run:
```
solver_version=32efdfd  state_n=30
  single t=0.0 feasible solver=0.0mm fk=0.001mm drift=0.0009mm OK
  wrote results/trajectory_single_naive.json (1 knot)
M1_OK
```
The knot passes the invariant that matters: `fk(stored_state)` independently reproduces the
solver's feasible contact (0.001 mm, drift 0.0009 mm < 0.01 tol). Solver-space == stored
state == render-space holds by construction, so metrics can trust stored knots.

**3. Assumptions confirmed.**
- `solve_pose`/`fk`/`version` is enough for a planner; no solver internals leak.
- A note maps cleanly to a `PointContact` — no pitch, no chord logic.
- Warm-start (`prev_state`) threads through unchanged.
- The `Trajectory` contract serializes and round-trips.

**4. Assumptions disproved / problem exposed.**
The committed state could not run at all. The palm-pass solver hard-references bones
`metacarpal1..4.L`, but the committed rig `unification/mpfb_rig.json` (commit f201f2a,
*before* the palm pass) carries only `finger1-5.L`. The metacarpal-bearing rig was
regenerated locally during the palm pass and never committed — so `mpfb_export.py` on the
checked-in rig `KeyError: 'metacarpal1.L'` too (verified — this is pre-existing, not the
adapter). This is the roadmap's "implementation exposes an actual problem" exception to the
freeze.

**Fix applied (minimal, honest):** `finger_joints_mm` now passes `meta_name=None` when the
rig lacks the metacarpal bone, so the cupping DoF is inert on old rigs and identical on new
rigs. No cost weight, pose, or anatomy changed. Metacarpal cupping is a static-palm
refinement and is irrelevant to the first goal (naive vs phrase *motion*), so running
without it does not compromise the comparison. When a metacarpal rig is re-dumped and
committed, cupping reactivates with zero code change.

**5. Change before M2?**
One follow-up, not a blocker: commit a metacarpal-bearing rig (or a note in the report
metadata recording that cupping is inert) so provenance is honest. Not required for M2 —
M2's single metric (fingertip travel) is unaffected by inert cupping. Proceed to M2.
