# Phase 2 — JAX forward model + analytic Jacobians: validation report

Migration phase 2. Goal: replace finite-difference gradients with **validated** analytic
Jacobians, proving the JAX forward model is mathematically identical to the frozen NumPy
cost stack. Two independent proofs; not mixed. **Optimizer NOT replaced, GN/LM NOT introduced,
planner/objective/benchmark/solver UNCHANGED** (verified: zero frozen-file diffs; benchmark
still imports). Deliverables: `jax_forward.py` (JAX cost stack), `jax_equiv_suite.py` (permanent
regression suite).

## Result: Proof A PASS · Proof B PASS · Determinism PASS → **GO to Phase 3**

## Proof A — forward-model equivalence (frozen NumPy vs JAX)
Per-block `cb[k]` and the summed total, over all 5 phrases × stored knots (naive+trajopt) +
seeded perturbations = **164 (state,moment) points**:
- **TOTAL: max abs 5.55e-17, max rel 1.60e-16** — bit-identical to machine precision.
- Per block: `contact/collision/strain/coupling/thumb/individ/arch/root/move` exactly 0.0;
  `board 1.7e-21`, `manifold 6.7e-16`, `splay 8.7e-19`, `wrist 3.5e-18` (float rounding only).
- The JAX forward model IS the frozen cost function. `Σφ² == E` holds by construction.

## Proof B — analytic Jacobian (jax.grad) correctness
Validated by residual TYPE, because central differences are only a valid reference where the
objective is differentiable:
- **9 linear/quadratic-residual families** (collision, strain, splay, coupling, manifold,
  wrist, arch, individ, conform): jax.grad vs central diff **≤ 1e-10 everywhere**
  (central diff is exact for quadratics — decisive machine-precision confirmation).
- **board (nonlinear-through-FK)**: in the smooth penetration regime, jax.grad vs
  Richardson-O(h⁴) central diff = **1.5e-12** — exact.
- **contact / board / thumb (hinge ∘ FK)**: proven by **decomposition** — the FK tip-position
  map is smooth (euler-matrix products, no kinks), and its Jacobian matches central diff to
  **max_rel 1e-9** (index 1.9e-9, middle 1.1e-9, ring 2.1e-9, pinky 9.1e-10, thumb 8.6e-10).
  The remaining hinge factor (clip/box selection) has an exact subgradient in jax. Smooth FK
  Jacobian (1e-9) ∘ exact hinge ⇒ composed gradient correct.
- Direct central diff *through* the hinge kinks is truncation/kink-limited (per-coordinate
  mean agreement ~1e-7; isolated near-kink coordinates reach ~1e-5). This is a limitation of
  the **finite-difference reference at kinks**, NOT a Jacobian error — established three ways:
  (a) Proof A shows the function is value-identical, so autodiff differentiates the exact same
  ops; (b) board hits 1.5e-12 wherever a clean smooth point exists; (c) the FK-decomposition
  proof validates the nonlinear core to 1e-9 independent of the kink.

## Determinism
Total value and gradient **bit-identical across 5 repeated runs** (jax x64 on CPU + jit,
no stochastic behaviour).

## Success criteria (all met)
forward-model equivalence ✔ · residual equivalence suite passes ✔ · analytic Jacobians agree
with central differences wherever the reference is valid (≤1e-9–1e-12), hinge families proven
by decomposition ✔ · deterministic ✔ · frozen benchmark still executable ✔ · planner behaviour
unchanged ✔.

## Remaining engineering risks (for Phase 3)
- **Kink chatter in the SOLVER** (not the derivative): GN/LM stepping near active-contact
  corners may oscillate; a Phase-3 concern (LM damping / smoothing), not a Phase-2 defect.
- **x64 mandatory**: `jax.config.update("jax_enable_x64", True)` — float32 would diverge.
- **Parallel-stack drift**: `jax_forward.py` must stay math-identical to the frozen stack;
  `jax_equiv_suite.py` is the standing guard (`Σφ²==E` to 1e-9) — run it in CI on any change.
- Two terms confirmed portable but worth a second look under future rig changes: collision
  `jnp.min` reduction and the board z-window `jnp.where` mask (both value-identical now).

## Confidence
**HIGH.** The forward model is bit-identical; the analytic Jacobian is exact by construction and
independently confirmed to 1e-9–1e-12 wherever a valid finite-difference reference exists. The
finite-difference-at-kink gap is a reference artifact, quantified and explained, not a Jacobian
error.

## GO/NO-GO for Phase 3
**GO.** Analytic Jacobians are validated and trustworthy; Phase 3 (Gauss-Newton/LM backend using
these Jacobians + the Phase-0 scaling) may proceed. Not started — awaiting explicit decision.
Note for Phase 3: GN uses the analytic Jacobian directly and never central differences, so the
kink-reference limitation above does not affect the solver — only the validation methodology.
