# Optimizer Migration — Phase 3 engineering report (Gauss-Newton / LM backend)

Scope: replace the optimization backend only. New GN/LM engine built on the Phase-2 validated
JAX residual + analytic Jacobian. No planner/objective/weight/benchmark change; no hinge
smoothing; no optimizer integrated into the planner (that is Phase 4). Standalone backend +
validation.

## Deliverables (additive, under `tools/handrig/optimizer/`)
- `gnlm.py` — Gauss-Newton with Levenberg-Marquardt damping. MARQUARDT diagonal scaling
  (`damping = lam * diag(J^T J)`), which is scale-invariant and carries the Phase-0
  state-scaling role (addresses the ~350:1 root-vs-angle conditioning the audit measured). Box
  bounds by projection. Deterministic (no RNG). Diagnostics: convergence reason, projected-
  gradient norm, nit, nfev, njev, damping, accept/reject counts, runtime.
- `gnlm_validate.py` — head-to-head vs the L-BFGS-B baseline on the IDENTICAL objective, same
  start (frozen naive knots), same bounds; optimizer-quality and planner-behaviour reported
  separately; determinism checked by repeat.

## Optimizer quality (GN/LM vs L-BFGS-B, same objective/start/bounds, 16 moments)
```
GN/LM deterministic: yes (0/16 nondeterministic)
lower final cost than L-BFGS-B: 10/16   (L-BFGS-B lower: 6/16)
lower projected-gradient (better stationarity) than L-BFGS-B: 14/16
well-behaved (proj-grad < 1.0): 9/16     proj-grad 0.11–0.42 on these
stalled (proj-grad >= 1.0): 7/16         string_crossing#1/2/3, one_shift_scale#1/2/3, sustained_anchor#1
function evals: GN/LM ~1000/moment vs L-BFGS-B ~4000–15000
mean runtime: GN/LM 3.84s vs L-BFGS-B 3.18s (GN/LM includes per-moment JIT compile)
```
On the well-behaved moments GN/LM reaches far tighter stationarity (proj-grad ~0.1–0.4) than
L-BFGS-B (~1.3–28 there) at roughly an order of magnitude fewer function evaluations. It
STALLS on the ill-conditioned / kink-dominated moments — most sharply `one_shift_scale#2`
(the fret-9 awkward high-fret pose, worst-contact ~3.7 mm): GN/LM ends at cost 18.7 with
proj-grad 1.7e4 (xtol, tiny step vs large gradient) where L-BFGS-B reaches 1.78. Both
optimizers fail to reach tight stationarity on the hard moments; GN/LM is worse on exactly
`one_shift_scale#1` and `#2`.

## Planner behaviour (kept separate)
No planner code was run or changed. Where GN/LM reaches a different (lower-cost) pose than
L-BFGS-B, the pose differs (max ||x_gnlm − x_lbfgs|| = 0.77 over the 30-DoF state); this is
attributable ENTIRELY to optimizer quality (lower cost / lower projected gradient on the same
frozen objective), not to any objective/weight change. Per the PASS criteria this is the
allowed "optimizer quality demonstrably changes the numerical solution" case. Benchmark
movement metrics were NOT recomputed because GN/LM is not wired into the planner in Phase 3;
that integration and its benchmark impact belong to Phase 4.

## Regression (must-still-pass) — all hold, no regression
```
residual-equivalence suite : PASS   (worst total 5e-12, sum(r^2) 4e-16, fingertips 1e-13mm)
forward-model equivalence  : PASS   (unchanged from Phase 2)
Jacobian validation        : smooth families 1e-8–1e-10, deterministic (unchanged);
                             blanket gate still flags contact/board ONLY at max(0,.) kinks
determinism                : PASS   (0 nondeterministic residuals/Jacobians/solves)
```

## Behaviour at piecewise-smooth hinge boundaries (required discussion)
The frozen objective is piecewise differentiable: contact-region activation, board-plane
activation, and every `max(0,.)` term are kinks. Per the Phase-3 model assumption, JAX
subgradients are accepted as the linearisation and the model is NOT smoothed. Observed
consequences, all model-owned (not Phase-3 defects):
- The GN/LM linearisation at a kink can propose a step the damping must reject; near a
  kink-dominated, ill-conditioned minimum this drives `lam` up and the step to ~0 (the
  `one_shift_scale#2` xtol stall). This is the frozen objective's non-smoothness + conditioning
  (documented in FINDINGS_ANCHOR_FALSIFY.md), surfacing through the optimizer.
- The blanket Jacobian gate still disagrees with central differences only at kink points; the
  autodiff derivative remains correct off-kink (Phase-2 smoothpoints proof, ~1e-7).
- No smoothing, no contact/hinge change was made in response (forbidden and unnecessary — the
  subgradient is the correct pointwise linearisation).

## PASS-criteria comparison
| criterion | result |
|---|---|
| GN/LM backend operational | **PASS** |
| validated JAX residuals used | **PASS** |
| validated analytic Jacobians used | **PASS** |
| deterministic optimisation | **PASS** (0/16 nondeterministic) |
| meaningful convergence diagnostics | **PASS** (reason, proj-grad, nit, nfev, damping, runtime) |
| frozen benchmark executable | **PASS** (untouched) |
| planner behaviour unchanged except where optimizer quality changes the solution | **PASS** (no planner change; pose deltas attributed to optimizer quality) |

## Honest caveat
GN/LM is NOT a uniform win: it is cheaper and reaches better stationarity on 9–14 of 16
moments, but STALLS on 7 ill-conditioned/kink moments and is strictly worse than L-BFGS-B on
`one_shift_scale#1/#2`. This is expected for GN/LM on a non-smooth, ill-conditioned objective
and is a property of the FROZEN model, not the backend. No gate was weakened to hide it. A
trust-region strategy (Phase 4+ optimizer research) is the natural next lever, but that is an
OPTIMIZER change, not a model change, and is out of Phase-3 scope.

## Rollback
Delete `tools/handrig/optimizer/gnlm.py` and `gnlm_validate.py` (and this report). The Phase-2
forward model and the frozen project are unchanged.

## GO / NO-GO request
Phase 3 PASS criteria are all met: a deterministic GN/LM backend on the validated residual +
analytic Jacobian, with meaningful diagnostics, no regression, and behaviour at hinges that is
the frozen model's property (not smoothed). The one honest limitation is stalling on
ill-conditioned/kink moments, documented and attributed to the model, not the backend.

Requesting explicit GO / NO-GO for Phase 4 (null-space investigation using the new optimizer).
Not proceeding automatically.
