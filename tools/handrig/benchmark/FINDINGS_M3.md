# M3 findings — planner plugin interface + registry

**1. What did we build?**
`planners.py`: the frozen `Planner.plan(phrase, references, solver, config) -> Trajectory`
interface, a `PLANNERS` registry (dict), and `NaivePlanner` — the M1 loop refactored into
the first registered plugin. `report.py` selects the planner by name (`--planner naive`) and
records planner name + commit; `config_hash` now folds in the planner name (a first-class
input). `adapters.naive_trajectory` is a thin shim over the plugin (keeps the M1 self-test).

**2. What did we learn?**
The planner is now swappable by name and the interface holds:
```
--planner naive  -> hard_gate=PASS score=1.0 worst_contact=0.001mm
--planner bogus  -> KeyError: unknown planner 'bogus'; registered: ['naive']
registry: ['naive']   re-run: byte-identical
```
Substantive output equals M2: the pose states are bit-identical (max abs diff 0.0) and the
verdict matches. The only trajectory-field delta is `solver_version` (a git hash — provenance
that legitimately advances as commits land), not behaviour.

**3. Assumptions confirmed — the M3 risk is retired.**
The signature carries what a *future trajectory planner* needs, verified before one exists:
- **whole phrase** — `plan` receives all events, not one note; a look-ahead planner can see
  the full note sequence to plan shifts/anchors.
- **references** — both A (mechanical) and B (acceptance region) are passed in; a planner can
  optimize toward the behavioural region. The naive plugin simply ignores them — which is
  exactly the baseline definition.
- **solver** — passed as the adapter, so a planner can call `solve_pose` per candidate knot
  or `fk` to score states.
The interface fits both the trivial (naive) and the hard (trajectory) case with no shape
change anticipated for M7.

**4. Assumptions disproved.**
None. `config_hash` changed vs M2 (5671935c → 60cd525378da9d73) BY DESIGN: planner moved from
a free-form `config` field to a first-class hashed input. Substantive result unchanged;
this is the correct provenance for M5 regression (different planner ⇒ different hash).

**5. Change before M4?**
Nothing. Interface frozen. Proceed to M4 (core deterministic metrics + a few phrases).
