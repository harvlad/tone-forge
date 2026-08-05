# Phase 1 — Derivative Strategy Feasibility: GO/NO-GO

Feasibility study for the optimizer rebuild's highest-risk item (adversarial review 0cae414):
*can the project obtain analytic Jacobians in a way that stays practical for its lifetime?*
No optimizer implemented, no FD replaced, no planner/benchmark/objective changed. Only a
throwaway spike (`/tmp/phase1_jax_spike.py`, committed as `jax_deriv_spike.py`) was run; it does
not touch the frozen solver.

## Verdict: **GO** — JAX reverse-mode autodiff over a `jax.numpy` reimplementation of the cost stack. Confidence: HIGH.

## 1. Approaches evaluated
- **A. JAX rewrite** (recommended): reimplement `evaluate`+FK+penalties in `jax.numpy`; get exact reverse-mode gradients and the residual **Jacobian** (for Gauss-Newton) for free. JAX 0.6.2 already installed.
- **B. Hand-derived analytic Jacobians**: chain-rule through FK + each penalty by hand.
- **C. Symbolic (SymPy) + codegen**: differentiate symbolically, generate code. SymPy 1.14 present.
- **D. Hybrid**: autodiff the nonlinear FK/contact part, keep trivial quadratic-posture derivatives analytic.
- **E. Torch autograd**: tensor reimplementation with `torch.autograd`. Torch 2.12 present.
- **F. Complex-step differentiation**: `Im f(x+ih)/h`, near-analytic accuracy, minimal code change.

## 2. Comparison
| Approach | Feasible | Correct | Runtime | Maintainable | Extensible (V2) | Eng. risk | Long-term |
|---|---|---|---|---|---|---|---|
| **A JAX** | **YES (spike 4.95e-10)** | machine-precision | fast (~59µs jit grad) | **HIGH — new term = forward only, 0 derivative code** | **HIGH** | MED (mechanical rewrite + equivalence test; dep already present) | **STRONG** |
| B hand-derived | yes | error-prone (silent) | fast | **LOW — every term = new derivation** | LOW (per-feature derivative work) | HIGH (silent bugs) | WEAK |
| C symbolic/codegen | partial | ok if it generates | fast | LOW–MED (expression explosion; rig-fragile; opaque codegen) | MED | HIGH (brittleness) | WEAK |
| D hybrid | yes | ok | fast | MED (two mechanisms to keep straight) | MED | MED | MED |
| E torch | yes | machine-precision | fast | MED–HIGH | HIGH | MED (heavier dep; eager/graph nuances vs our functional style) | acceptable fallback |
| F complex-step | **NO** | **breaks at clip/norm/min/max — which we have everywhere** | ok | n/a | n/a | n/a | REJECT |

## 3. Strengths / weaknesses (recommended = A)
- **Strengths:** exact (verified 4.95e-10 vs central diff on the real op classes); produces the residual Jacobian `J` directly (`jax.jacobian`), which is the whole point — Gauss-Newton/LM needs `J`, not `∇scalar`; the conversion is mechanical (spike proved `.at[].set()` for in-place, `clip(x,0)²` for the `if x>0:+=x²` hinges, drop `float()` casts, `python min/max → jnp`); adding a residual six months out means writing only its **forward** function — **zero derivative code, no silent-derivative-bug surface**; JAX is mainstream + already a dependency.
- **Weaknesses:** requires a `jax.numpy` reimplementation of the cost stack (est. 1–3 days) that must stay math-identical to the frozen numpy version; `jax_enable_x64` is mandatory (else float32 diverges from the float64 solver); kinks (clip/norm/min at the exact kink) yield subgradients — consistent with the objective's own definition but a Phase-3 solver-chatter concern, not a derivative-correctness one.

## 4. Major risks
- **Parallel-stack drift (MED):** a jnp cost stack could silently diverge from the frozen numpy `E`. **Mitigation:** an equivalence assertion `|E_jnp(x) − E_numpy(x)| < 1e-10` over a battery of random+real states, run as a standing check — this is the primary new testing burden.
- **Rewrite scope (MED):** full stack = `evaluate` + FK + anatomy penalties + contacts + guitar geom. Spike shows all op classes convert mechanically; two spots need care in the full port — the collision `min()` reduction (→ `jnp.min`, differentiable a.e.) and confirming the synergy **SVD is at basis-construction time only (constant), not in the differentiated hot path** (must verify during port).
- **float64 / determinism (LOW):** set x64; JAX-on-CPU + jit is deterministic → byte-reproducibility achievable.
- **Dependency/lock-in (LOW–MED):** JAX API churn; CPU-only is fine. Torch (E) is the fallback if JAX becomes untenable.

## 5. Unknowns
- Exact effort to port the two non-trivial terms (collision min-reduction, manifold PCA projection) — expected small, not yet measured on the real code.
- Whether the full jnp stack hits the `1e-10` equivalence bar everywhere (spike hit `~5e-10` on its slice — encouraging, not a guarantee for all 14 terms).
- Phase-3-only: whether kinks cause GN/LM chatter near active contacts (a solver question, out of Phase-1 scope).

## 6. Recommended derivative strategy
**A — JAX reverse-mode autodiff over a `jax.numpy` reimplementation of the cost stack**, exposing the residual Jacobian for Gauss-Newton. Reject B (maintainability: per-term hand derivation, silent bugs), C (brittleness/opacity), F (non-holomorphic ops we use everywhere). Keep E (Torch) as a documented fallback.

## 7. Confidence
**HIGH** on feasibility + correctness (spike: 4.95e-10 match, Jacobian extraction, all op classes covered). **MEDIUM-HIGH** on total effort (1–3 days, two terms unmeasured). The maintainability case is the strongest and most durable point: autodiff removes the entire class of derivative-maintenance work that would otherwise recur for every future planner feature.

## 8. Planner V2 compatibility
**Excellent — the decisive argument for A.** Autodiff is agnostic to the cost's content: task hierarchy, null-space objectives, new comfort/biomechanical models, additional residuals, and new contact formulations are all just new forward `jnp` functions whose derivatives are automatic. No approach except autodiff (A/E) avoids per-feature derivative work; B and C would each require a fresh derivation or regeneration per new term — a recurring tax and silent-bug source. A supports V2 without another derivative rewrite.

## GO/NO-GO
All GO criteria met: technically feasible ✔, numerically trustworthy ✔, maintainable ✔,
extensible ✔, acceptable engineering risk ✔. **GO.** Recommended next: Phase 2 (implement the
JAX cost stack + the `1e-10` equivalence check, replace FD gradients, validate analytic-vs-central
per residual block) — NOT started; awaiting explicit go/no-go.
