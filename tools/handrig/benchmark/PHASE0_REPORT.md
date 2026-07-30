# Phase 0 — State Scaling: implementation report

Migration phase 0 of the approved optimizer rebuild (design 4a97474, review 0cae414).
Scope discipline: **frozen planner / objective / weights / benchmark / solver untouched.**

## Deliverable
`benchmark/opt_scaling.py` — a self-contained, verified state-scaling reparameterization
`y = x / w`, `w = L` on the 3 root-translation (metre) dims per knot and `w = 1` on all
rotation/joint (radian) dims. `L = 2⁻⁸ m = 3.90625 mm` (power of two ⇒ `x/w*w` is bit-exact ⇒
byte-reproducibility preserved when adopted). Pure change of variables: objective, feasible set,
and optimum are unchanged; only the solver's landscape conditioning changes.

NOT wired into the live planner. Phase 0 delivers + proves the transform; adoption happens in
the Phase-3 backend swap. This keeps the frozen benchmark byte-identical and makes rollback
trivial (delete one unimported file; nothing depends on it).

## Validation gates (all PASS)
| Gate | Result | Evidence |
|---|---|---|
| Scaling verified mathematically | PASS | selftest: round-trip bit-exact; bounds membership preserved; objective preserved on a nonlinear test fn to <1e-15 |
| Objective unchanged after inverse transform | PASS | on the REAL trajopt objective, `|obj_scaled(y) − obj(x)| = 0.00e+00` (bit-identical, dyadic L) |
| Block-conditioning materially improved | PASS | root-vs-joint diagonal-Hessian ratio, ORIG → SCALED: single 9.0e5→23, repeated_note 3.6e5→0.077, string_crossing 4.8e5→8.3, one_shift_scale 9.7e5→15, sustained_anchor 7.4e5→0.073 — **~10⁵–10⁶ → O(1)** on all 5 phrases |
| Benchmark still executes | PASS | all benchmark modules import; no frozen file changed (git diff on planners/suite/evaluate/report/solver = empty) |

## Optimizer quality vs planner behaviour (kept separate)
- **Optimizer quality: improved** — the measured unit-mismatch component of the conditioning
  (block ratio up to 9.7e5) is removed (→ O(1)). This is the scaling-addressable part only.
- **Planner behaviour: unchanged** — scaling is not wired in; the frozen benchmark output is
  byte-identical; no planner/objective/weight/threshold touched.

## Honest scope limits (per the design review)
- Scaling fixes the **unit-mismatch** conditioning only. The **structural null-space
  near-singularity** (the ~2–10 mm flat valley from the audit) is NOT a scaling problem and is
  untouched here — it is deferred to LM damping in Phase 3, exactly as the review's corrected
  gate specified. So total condition number is NOT expected at 10⁶ yet; that was the over-promise
  the review flagged, and Phase 0 deliberately does not claim it.
- A single fixed `L` cannot equalise every phrase/point (scaled ratios span 0.07–23, not exactly
  1); this is expected and sufficient — the 4–5 order-of-magnitude reduction is the goal.

## Rollback
Delete `opt_scaling.py`. Nothing imports it; frozen benchmark unaffected.

## Go/No-Go
Phase 0 gates all PASS. Recommend **GO to Phase 1** (differentiation feasibility spike — the
highest-risk item per the review; "autodiff over numpy" is not viable, so Phase 1 must decide
between a JAX reimplementation of the cost stack vs hand-derived analytic Jacobians, prototyping
only enough to answer GO/NO-GO). Awaiting explicit decision — not proceeding automatically.
