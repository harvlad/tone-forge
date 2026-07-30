# Hand-Rig Optimizer — Target Design

Design document. **No code, no experiments, no implementation.** Answers: *what optimizer
should we build, and why?* Premise: the audit (`HANDRIG_OPTIMIZER_AUDIT.md`, commit ca122e2)
is correct — the current L-BFGS-B + finite-difference implementation suffers mixed-unit
scaling, ill-conditioning (~10¹³–10¹⁴), budget-termination, and non-converged solutions.
M7 (MIXED) and M7A (MIXED) verdicts, the planner objective, the benchmark, and the solver are
all frozen; this document does not change them — it specifies their *replacement solver*.

Recommended target in one line: **a scaled, analytic-Jacobian Gauss-Newton / Levenberg-Marquardt
least-squares solver with contacts enforced by an augmented-Lagrangian outer loop and box bounds
by trust-region reflection — a lightweight KOMO for our exact structure — reached by a 4-phase
incremental migration whose first two low-risk phases (scaling, analytic gradients) are
prerequisites and independently valuable.**

---

## 1. Requirements

**Required**
- **Convergence reliability**: terminate on a true stationarity/optimality criterion (gradient / KKT), NOT on an evaluation budget; no ABNORMAL aborts. Reach ‖∇‖ (or projected-gradient) below a set tolerance on all 5 frozen phrases.
- **Deterministic**: identical inputs → byte-identical solution (the benchmark's `write_report` is byte-reproducible; the optimizer must not break that).
- **Numerical stability**: well-conditioned steps; no reliance on curvature learned from noisy gradients. Handle the redundant null space without drifting.
- **Constraint support**: box bounds (joint limits, root box) satisfied exactly; contact targets driven to ≈0 residual (≤ the 6 mm gate, ideally ≪).
- **Reproducibility of the scientific record**: must be able to re-score the frozen phrases and produce comparable metrics; old planner kept runnable behind a flag during migration.
- **Debuggability**: expose per-iteration objective, gradient norm, step size, damping, condition estimate — so "is it converging?" is answerable directly (the current one hides this).

**Preferred**
- **Scalability** to longer phrases (34-phrase suite; N knots × 30). Cost should grow ~linearly in N (exploit the banded/Markov cross-knot coupling), not cubically.
- **Performance**: interactive-ish solve (seconds), not the current 15–23 s of FD thrash.
- **Warm-startable** (from naive, or from a previous solve) — retain the mechanism that gives the M7 pose-conditioning advantage.

**Nice-to-have**
- Sparse linear algebra for large N.
- A pluggable cost/residual registry so new biomechanical terms drop in without touching the solver.
- GPU/vectorized Jacobian (only if N or suite size later demands it).

---

## 2. Design candidates

| Candidate | Advantages | Disadvantages | Impl. complexity | Compat w/ current planner | Robustness |
|---|---|---|---|---|---|
| **A. Improved L-BFGS-B** (add state scaling + analytic `jac=`, fix termination) | Smallest change; keeps scalar objective + existing `minimize` call; removes the two measured killers (FD gradient, unscaled units) | Still a black-box quasi-Newton that discards the sum-of-squares structure; null space still only weakly handled; won't reach GN-class conditioning | LOW | Drop-in (same objective) | MED — likely converges once scaled+analytic, but not structure-optimal |
| **B. Gauss-Newton (least-squares)** on residual vector `Σ‖φ‖²` | Uses the real structure; `JᵀJ` is a free, well-conditioned pseudo-Hessian; small-residual (contact→0) is exactly where GN excels | Needs the residual Jacobian; bare GN has no damping → fragile on the rank-deficient null space | MED | Requires re-expressing objective as residuals (mechanical; terms already are squares) | MED-HIGH |
| **C. Levenberg-Marquardt** (GN + adaptive `λI` damping) | B's strengths **plus** damping that directly regularises the null-space wander and large-residual steps; standard, well-understood | Damping schedule to tune; still needs bounds handling | MED | As B | HIGH |
| **D. KOMO-style** (banded GN + augmented-Lagrangian, Markov cliques) | Structurally ideal; banded `JᵀJ` ⇒ linear-in-N; exact constraints; the literature's nearest match | Most machinery; either adopt a library or build the banded factorization + AL loop | HIGH | Needs residual+constraint feature interface; larger rewrite | HIGHEST |
| **E. Augmented Lagrangian** (outer loop; any inner solver) | Exact contact satisfaction without penalty→∞; fixes the "residual violation at finite μ" defect | It's an *outer loop*, not a full solver — needs an inner method (best paired with C/D) | MED (on top of C) | Additive to C/D | HIGH for constraints |
| **F. SQP** (trust-region convex QP subproblems, e.g. TrajOpt-style) | Hard constraints native; large basin; escalating exact penalty | Needs a QP solver + constraint Jacobians; heaviest general NLP path | HIGH | Larger rewrite; new deps | HIGH but overkill for box+soft-contact |
| **G. Interior-point NLP (IPOPT) / real transcription** | Most general, robust, sparse | External dep; must supply sparse Jacobians + scaling; heavyweight | HIGH | Full re-formulation | HIGH but overkill now |

Pragmatic embodiment note: **C (LM) is available off-the-shelf as `scipy.optimize.least_squares(method='trf', jac=<analytic>)`** — Trust-Region-Reflective is a bound-constrained Gauss-Newton/LM that accepts an analytic Jacobian, giving B+C+box-bounds in one well-tested routine with no new dependency. This makes the recommended target reachable at MED complexity.

---

## 3. Recommended target architecture

**Levenberg-Marquardt Gauss-Newton least-squares on a scaled residual vector, with box bounds
via trust-region reflection and contacts escalated by an augmented-Lagrangian outer loop
(candidates C + E, box-constrained; a lightweight KOMO/D without the full banded machinery).**

Justification:
- **Mathematical structure**: the objective is already `Σ‖φᵢ‖²` (contact box-distance², board/collision hinge-squares, strain/splay/coupling/manifold/arch quadratics). GN's `JᵀJ` is the correct curvature model *for free* from first derivatives — precisely the case (small residual at the solution: contact→0) where GN is provably strong. LM damping `λI` regularises the measured rank-deficiency (the ~2–10 mm null-space valley) that a bare position penalty leaves flat.
- **Literature**: Ratliff (GN is fundamental because motion costs are sums-of-squares), KOMO/Toussaint (GN + augmented Lagrangian on exactly this trajectory structure), damped-least-squares/task-priority IK (LM damping for redundancy), Nocedal & Wright + SE(3)-metric refs (scale the mixed-unit state), augmented-Lagrangian penalty theory (exact contacts at moderate μ). Every audited defect maps to a piece of this design.
- **Engineering complexity**: MED, not HIGH — `least_squares(trf)` supplies GN/LM + bounds; we add (a) a state-scaling reparameterization, (b) an analytic/AD Jacobian, (c) an AL outer loop for contacts. No new heavy dependency; the full banded/IPOPT path (D-full/G) is deferred unless long phrases demand it.
- **Future extensibility**: new costs are new residual blocks (rows of `φ`/`J`); new hard constraints join the AL loop; longer phrases later swap the dense `JᵀJ` solve for the banded one without changing the formulation.

---

## 4. Migration plan (incremental, each phase independently testable)

**Phase 0 — State scaling (reparameterize `y = W·x`, `W` diagonal so 1 rad ↔ L≈0.05–0.1 m).**
- Goal: remove the measured mixed-unit ill-conditioning (curvature ratio up to 2.8e5) with zero objective change.
- Deliverables: scaling map + its inverse; existing L-BFGS-B path runs on `y`.
- Validation: condition number of the (numerically probed) Hessian drops by ≥10³; L-BFGS-B stops on `gtol` not `maxfun`; frozen phrase metrics unchanged within noise.
- Rollback: `W = I` (identity) → exactly today's behaviour.

**Phase 1 — Analytic / AD gradients (`jac=`), still scalar L-BFGS-B.**
- Goal: kill the finite-difference failure (100 %-wrong `one_shift` gradient, ABNORMAL abort, 30N-eval cost).
- Deliverables: differentiable objective (hand-derived or autodiff over the pure-numpy FK+penalties) feeding `jac=`.
- Validation: ‖analytic − central-difference gradient‖/‖g‖ < 1e-6 on all phrases; no ABNORMAL; ‖∇J‖ at the solution → small; nfev per solve drops sharply.
- Rollback: drop `jac=` (revert to FD).

**Phase 2 — Switch to Gauss-Newton/LM least-squares (residual formulation, bounded TRF).**
- Goal: the structurally-correct solver; converge to stationarity on all phrases.
- Deliverables: objective re-expressed as a residual vector `φ(y)` + analytic residual Jacobian; solve via bound-constrained LM/GN; old scalar path retained behind a flag.
- Validation: reaches optimality tolerance (not budget); ‖∇‖ ≈ 0; solution objective ≤ the budget-lifted L-BFGS-B reference from the audit; solve time ↓; deterministic; frozen phrases re-scored and compared (must match-or-beat, no hard-gate regression).
- Rollback: flag back to the Phase-1 L-BFGS-B path.

**Phase 3 — Augmented-Lagrangian contacts (outer multiplier loop).**
- Goal: exact contact satisfaction without raising penalty weight into ill-conditioning.
- Deliverables: contact residuals moved to an AL outer loop around the Phase-2 inner solve.
- Validation: worst-contact → ≈0 (≪6 mm) at moderate μ; conditioning stays healthy; no new pathology tags.
- Rollback: revert contacts to fixed-weight residual (Phase-2 behaviour).

**Phase 4 (optional, deferred) — null-space-projected posture and/or banded sparse solve.**
- Goal: deterministically consume redundant DoF (address the residual modeling-side wander) and scale to long phrases.
- Validation: still-hand phrases show near-naive root travel; solve cost ~linear in N.
- Rollback: omit projection / use dense solve.

Sequencing rationale: Phases 0–1 are LOW risk, independently valuable, and *prerequisite* — they likely resolve most non-convergence on their own and are the point at which we re-measure before committing to the larger Phase-2 rewrite.

---

## 5. Validation strategy (optimizer quality, NOT benchmark score)

Per solve / per phrase, measured on the 5 frozen phrases (and later the 34-suite):
- **Convergence**: terminates on optimality (gtol/KKT), not `maxfun`/`maxiter`; report the stop reason.
- **Gradient norm at solution**: ‖∇J‖ (or projected gradient) → below tolerance (today: 52 / 555 / 5.1e5 — must collapse toward ~0).
- **Conditioning**: estimated cond(H) or cond(JᵀJ) after scaling (today ~10¹³–10¹⁴ → target ≤ ~10⁶).
- **Solution optimality**: objective ≤ the audit's budget-lifted reference (e.g. `sustained_anchor` ≤ 1.2884); no lower solution reachable by more budget.
- **Reproducibility**: byte-identical re-solves; deterministic across runs.
- **Solve time**: wall-clock and nfev/njev per phrase (today 15–23 s / 15k–18k nfev).
- **Robustness / basin**: perturb the warm-start; solution should return to the same optimum (or report multiplicity honestly).
- **Monotonic progress**: objective decreases each accepted step; damping/step-size traces sane.

Gate to declare the new optimizer "better": on every frozen phrase it (a) converges on optimality, (b) reaches objective ≤ the budget-lifted reference, (c) with ‖∇‖ below tolerance and healthy conditioning, (d) deterministically, (e) without hard-gate regression when its solutions are re-scored. Benchmark Movement Score is a downstream *consequence*, explicitly not the optimizer's success metric.

---

## 6. Risk analysis

| Risk | Likelihood | Impact | Mitigation |
|---|---|---|---|
| Differentiating FK + hinge/`max(0,·)²` penalties (kinks) for analytic/AD Jacobian | MED | HIGH (blocks Phase 1–2) | Kinks are measure-zero (subgradient fine for GN); use autodiff (JAX) over the pure-numpy solver, or hand-derive; verify vs central-difference (<1e-6) as the Phase-1 gate |
| Residual re-formulation subtly changes the objective (drifts from frozen planner) | MED | HIGH (breaks comparability) | Assert `Σφ² == E` numerically to machine precision before switching solver; keep old path behind a flag; re-score frozen phrases and diff |
| LM/GN still struggles on large-residual terms (far-from-feasible warm start) | LOW-MED | MED | Warm-start from naive (small residual by construction); GN/BFGS hybrid fallback for large-residual blocks; LM damping |
| Augmented-Lagrangian μ / multiplier schedule mis-tuned | MED | MED | Standard AL update rules; start from literature defaults; Phase 3 is isolated + rollback-able |
| `scipy.least_squares` bound handling (TRF) insufficient for our box + AL | LOW | MED | TRF is designed for bound-constrained least-squares; if inadequate, projected-LM or move to a real NLP (deferred G) |
| Scaling constant L chosen poorly | LOW | LOW | L is a single characteristic length; sweep a few offline; conditioning metric tells us directly |
| Migration destabilizes the frozen scientific record | LOW | HIGH | Every phase flagged + rollback; M7/M7A artifacts already committed & frozen; new solver additive |
| Determinism lost (autodiff/library nondeterminism) | LOW | MED | Pin versions; fixed seeds; assert byte-identical re-solves in CI |

---

## 7. Future compatibility (evaluate only — do NOT design these)

The residual + Gauss-Newton/LM + augmented-Lagrangian architecture is *additive*, which is the key compatibility property:
- **Guide fingers / anchors**: new soft residual blocks (hold a finger near a target) or AL hard constraints (anchor must stay planted) — rows added to `φ`/`J` / the multiplier set. No solver change. GOOD.
- **Anticipation**: expressible as look-ahead coupling residuals across knots — same banded/Markov structure the design already targets. GOOD.
- **Timing optimization**: promotes the currently-fixed knot times to decision variables; least-squares/collocation handles augmented state; the residual formulation extends, though it grows the variable set and may motivate the Phase-4 banded solve. GOOD, with effort.
- **Expressive playing / style**: additional cost residuals (vibrato room, dynamics posture) — additive blocks. GOOD.
- **Richer biomechanical costs**: the current `E` is already a weighted residual sum; new tendon/torque/fatigue terms slot in as residuals, and GN benefits as long as they're (piecewise) smooth. GOOD; non-smooth terms would need care (subgradient/smoothing).

No candidate feature appears to *conflict* with the recommended architecture; several (anchors, anticipation, timing) are more natural in it than in the current black-box scalar minimizer. The one caution: heavily non-smooth future costs would push toward SQP/exact-penalty (F) — but that is a later, evidence-driven decision, not a reason to change the target now.

---

## Conclusion — what to build, and why
Build a **scaled, analytic-Jacobian Gauss-Newton / Levenberg-Marquardt least-squares solver with
augmented-Lagrangian contacts and box bounds** (pragmatically: `scipy.least_squares` TRF + a
state-scaling reparameterization + analytic/AD Jacobian + an AL outer loop), reached by the
4-phase migration above. Rationale: it is the solver the problem's mathematical structure and
the optimization literature both call for, it directly removes every defect the audit measured,
it is reachable at medium engineering cost without a heavy new dependency, its quality is
verifiable by optimizer-level metrics independent of the benchmark, and it is additively
compatible with every plausible future planner feature. Phases 0–1 (scaling + analytic
gradients) are low-risk prerequisites and the natural point to re-measure before committing to
the Phase-2 rewrite.

This document is implementation-ready pending approval. Nothing was implemented, prototyped, or
benchmarked.
