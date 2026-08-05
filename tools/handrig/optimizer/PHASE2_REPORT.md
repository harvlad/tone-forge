# Optimizer Migration — Phase 2 engineering report (JAX forward model)

Scope: validate the mathematical infrastructure only. Reimplement the FROZEN NumPy forward
model in JAX, prove residual equivalence and analytic-Jacobian correctness. No optimizer
integration, no finite-difference replacement, no planner/objective/weight/benchmark change.

## Deliverables (all under `tools/handrig/optimizer/`, additive & isolated)
- `jax_forward.py` — JAX re-expression of `mpfb_solver.evaluate`. All constants (rig matrices,
  synergy basis, weights, neutral pose, contact-region boxes) pulled from a live MPFBSolver →
  zero transcription drift. Defines the GN RESIDUAL VECTOR `r` with `sum(r_i^2) == evaluate()`
  total (prev=None). meta_name=None FK branch (matches the committed metacarpal-free rig).
- `test_residual_equivalence.py` — permanent Proof-A regression suite.
- `test_jacobian.py` — Proof-B analytic-vs-central-difference, per residual family.
- `test_jacobian_smoothpoints.py` — proves contact/board Jacobians correct off the kink.

## Proof A — forward-model equivalence (PASS)
16 knots across all 5 frozen phrases:
```
worst |total_np - total_jx|   = 5.0e-12
worst block diff (cb_k)       = 6.3e-13   (one_shift_scale contact)
worst |sum(r^2) - total_jx|   = 4.4e-16   (residual-vector internal contract)
worst fingertip geometry diff = 1.1e-13 mm
nondeterministic knots        = 0
gate TOL = 1e-9  ->  RESIDUAL_EQUIVALENCE_PASS
```
Element, block, whole-vector, total-objective, and determinism all validated. The JAX forward
model is mathematically identical to the frozen NumPy model to ~1e-12 (float64 round-off).

## Proof B — analytic Jacobians
Per-family analytic (JAX autodiff, forward mode) vs high-quality central differences (eps=1e-6),
16 knots, 0 nondeterministic Jacobians. Smooth families:
```
strain   max_abs 1.5e-10   splay   3.3e-11   coupling 0 (inactive)
manifold 1.6e-10           thumb   2.6e-08   wrist    1.7e-11
arch     4.8e-11           individ 5.2e-11   conform  0 (inactive)
```
All smooth families match to 1e-8–1e-10. **The autodiff Jacobian is correct.**

Two hinge families — `contact` and `board` — exceed the blanket 1e-4 gate, but ONLY at
`max(0,.)` boundary points: at a solved knot a contact tip can sit exactly on its region edge
and a joint exactly on the board plane. The signatures are diagnostic of a kink, not an error:
analytic 0 vs central-diff 1203 (subgradient on the flat side) and analytic 6324 vs
central-diff 3177 (exact factor-2: central diff averages the active slope with the inactive
side). `test_jacobian_smoothpoints.py` confirms the derivative code is correct off the kink:
```
contact strictly exterior (miss=28.3mm, smooth) : max|Ja-Jfd| = 3.0e-7
board   strictly active   (126mm penetrating)   : max|Ja-Jfd| = 5.1e-7
```
So the disagreement is the FROZEN model's known C0/C1 non-smoothness (documented in the
falsification study), surfaced by a blanket central-difference comparison — NOT a defect in the
JAX infrastructure. For a Gauss-Newton/LM step the residual and its Jacobian are evaluated at
the current point; the autodiff subgradient is the correct pointwise linearisation there.

## PASS-criteria comparison
| criterion | result |
|---|---|
| forward-model equivalence proven | **PASS** (~1e-12) |
| residual-equivalence suite passes | **PASS** |
| analytic Jacobians match central differences | **PASS on all smooth families and on contact/board off-kink (~1e-7); blanket gate disagrees ONLY at hinge boundaries — model non-smoothness, autodiff proven correct** |
| deterministic results | **PASS** (0 nondeterministic in both proofs) |
| frozen benchmark remains executable | **PASS** (no benchmark/planner/solver change this phase) |
| planner behaviour unchanged | **PASS** (additive `optimizer/` only) |

## Change control / discipline
No planner objective, weights, architecture, benchmark, renderer, or evaluation change. No
optimizer integrated, no finite differences replaced. Determinism preserved. The only nuance
requiring a judgement call is the Jacobian behaviour at `max(0,.)` kinks — reported honestly,
gate NOT weakened.

## Rollback
Delete `tools/handrig/optimizer/`. Nothing else references it; the project is unchanged.

## GO / NO-GO request
Phase 2 objectives are met: the JAX forward model is proven mathematically identical, its
analytic Jacobians proven correct wherever the model is differentiable, and everything is
deterministic. The one open judgement is whether the reviewer accepts autodiff subgradients at
the frozen model's `max(0,.)` boundaries (contact-region edge, board plane) as the linearisation
GN/LM will use — the alternative would be smoothing those hinges, which is a MODEL change and
therefore out of Phase-2 scope and explicitly forbidden.

Requesting explicit GO / NO-GO for Phase 3 (Gauss-Newton / Levenberg-Marquardt backend). Not
proceeding automatically.
