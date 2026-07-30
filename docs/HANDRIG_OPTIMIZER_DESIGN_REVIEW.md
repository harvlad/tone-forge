# Adversarial Review — Target Optimizer Design

Mandate: try to REJECT the proposed optimizer (`HANDRIG_OPTIMIZER_DESIGN.md`, commit 4a97474).
Do not defend or improve it. No code, no experiments. Everything frozen. Proposal under review:
scaled state + analytic Jacobians + Gauss-Newton/Levenberg-Marquardt + trust-region-reflective
least-squares + augmented-Lagrangian contacts (lightweight KOMO).

**Verdict up front: APPROVE WITH REVISIONS.** The core direction survives a genuine attempt to
break it, but three claims are over-stated or mis-prioritized and must be corrected before
implementation (§7). I found no reason to reject, and two of my strongest attacks (large-residual
GN, "objective isn't really residual-based") failed on inspection — reported honestly below.

---

## 1. Assumption audit

| Assumption | Status | Adversarial finding |
|---|---|---|
| Objective is truly residual/sum-of-squares | **Supported** | Attacked and failed to break: `E` = contact box-dist² + board/collision hinge² + strain/splay/coupling/arch `(θ−c)²` + manifold `‖q−proj q‖²` + thumb/conform. Every term is a square of a residual; `Σφ²=E` holds by construction (coefficients fold as `√w`). |
| Residuals sufficiently **smooth** | **Questionable** | Real hole. board `max(0,pen)²`, collision `max(0,2·4.5−d)²`, splay-excess, conform, and the contact box-distance are **C¹ with kinks** at activation (finger entering/leaving penetration, tip crossing a fret-box face). At those kinks the Jacobian is discontinuous → GN/LM steps can chatter/stall. "Measure-zero, subgradient is fine" (design's dismissal) is true generically but NOT at an active contact boundary, which is exactly where a fretting solution sits. |
| Gauss-Newton approximation appropriate | **Supported (surprised me)** | I attacked this hardest ("large-residual posture terms make GN bad"). It **fails as an objection**: the non-contact posture terms are `(θ−neutral)²` / linear-projection residuals ⇒ residual Jacobian is *constant* ⇒ GN is **exact** for them regardless of magnitude. The only nonlinear residuals (contact/board through FK) are **small at the solution** — GN's best case. So GN is genuinely well-matched. |
| Contacts effectively handled by augmented Lagrangian | **Questionable / possibly unnecessary** | The audit shows contacts are already ≈0 (0.03–0.12 mm) at the non-converged solutions — contacts are NOT the failing constraint; the **null-space wander** is. AL adds an outer loop to perfect a constraint that is already satisfied, while the measured pathology is elsewhere. Solving the wrong problem. |
| Jacobians practical to derive/maintain | **Questionable → the real risk** | Design says "autodiff over the pure-numpy solver" — **numpy is not auto-differentiable**. Real options: (a) rewrite the FK+penalty stack in JAX/autograd — but that touches the frozen solver and is a large rewrite; (b) hand-derive analytic Jacobians through MPFB-rig FK + synergy PCA + guitar geometry — substantial and brittle to maintain as costs change. Neither is "MED." This is the make-or-break, under-costed. |
| State scaling sufficient to fix conditioning | **Unsupported (as stated)** | Design promises cond ≤ ~10⁶ from scaling. But part of the measured 10¹³–10¹⁴ is **structural rank-deficiency** (the null-space flat valley = a near-zero Hessian eigenvalue). Diagonal scaling cannot lift a zero eigenvalue — it fixes the unit-mismatch (m vs rad) part only. The residual near-singularity is removed by **LM damping (Phase 2)**, not scaling (Phase 0). So Phase 0's stated gate over-promises. |

---

## 2. Failure modes (realistic)
- **sustained_anchor / many simultaneous contacts (the ACTUAL unfixed case):** multi-contact + active posture gradient is where GN's small-residual advantage is thinnest and where contact-activation kinks are most likely — the design's hardest target may be where the recommended solver helps least.
- **Discontinuous contact activation:** a finger making/breaking board penetration flips a hinge on; the residual Jacobian jumps; LM can oscillate around the boundary. Contact-implicit trajectory optimization literature treats this as *complementarity*, not smooth least-squares (see §3).
- **Bound-active solutions at high frets (f9–f11):** a stretched hand likely sits with joint limits ACTIVE. Then the interior GN model is secondary to active-set/bound handling; TRF's bound machinery becomes the critical path, not GN — a regime the design underweights.
- **Large positional shifts / basin:** GN/LM have a smaller basin than trust-region quasi-Newton for the nonlinear FK residuals; the one_shift_scale f9→f11 jump could start outside the basin (warm-start from naive mitigates, but naive itself is a separate non-converged solve).
- **Long phrases (34-suite):** dense `JᵀJ` is O((30N)³); scipy `least_squares` TRF won't auto-exploit our band structure without a supplied sparsity pattern. Phase-2 dense GN could be *slower* than today for large N; the banded solve is deferred to Phase 4.
- **Poor init from the naive warm-start:** the naive solve uses the same defective FD-L-BFGS-B; a bad warm start propagates into GN's basin.

---

## 3. Literature contradictions (sought disagreement, not support)
- **Contact-rich → complementarity/SQP, not GN least-squares.** Posa & Tedrake (contact-implicit trajectory optimization) and MuJoCo-based methods formulate making/breaking contact as *linear complementarity / mixed-integer or SQP*, explicitly because smooth least-squares mishandles contact activation. If we ever go multi-finger make/break, bare GN is the field's *wrong* tool.
- **SQP/IPOPT preferred for many active constraints.** Betts and the direct-collocation canon favor sparse SQP/interior-point when inequality constraints are numerous/active — plausibly our high-fret joint-limit-active regime — over unconstrained-style GN with reflected bounds.
- **LM instability near rank-deficient Jacobians.** Classic result: LM stalls/oscillates when `JᵀJ` is near-singular unless damping is scheduled well; heavy damping → gradient-descent-slow. Our null space is exactly this near-singularity — the very thing we hope LM fixes is also where LM is known to misbehave.
- **AL is finicky and low-value when constraints already hold.** Augmented Lagrangian's multiplier/penalty schedule is fiddly; for near-satisfied constraints (our contacts) it adds iterations for little gain — several practitioners prefer keeping such terms as plain penalties.
- Net: the counter-literature does not say "don't use GN/LM here"; it says "GN/LM is right for *smooth small-residual* problems and *wrong* for *contact-rich complementarity* ones." We are near the smooth end **today** but drift toward the contact-rich end with future expressive/multi-contact features (§7 caveat).

---

## 4. Implementation risks (concrete, ranked)
1. **[HIGHEST] Analytic/AD Jacobian feasibility.** "AD over numpy" is not a thing; real path is a JAX rewrite (touches frozen solver, large) or hand-derivation (brittle). If this stalls, the whole migration stalls at Phase 1. Under-costed as MED.
2. **[HIGH] Conditioning not actually fixed by scaling.** Structural null-space eigenvalue survives scaling; if Phase 0's gate is "cond drops," it can *pass on the unit part while the singular direction remains* — a misleading green light.
3. **[HIGH] Contact-activation kinks** breaking GN/LM step quality on exactly the multi-contact target case.
4. **[MED] Residual-reformulation drift.** Folding `√w` coefficients, the dead-code `individ` term, mixed-unit terms (mm² vs rad²) into one residual vector without changing `E` — must assert equality to machine precision; asymmetric/legacy terms are fragile.
5. **[MED] Determinism** through TRF heuristics + JAX; byte-reproducibility (required for the frozen record) asserted but unverified.
6. **[MED] AL parameter instability** for a low-value use (contacts already satisfied) — risk without clear payoff.
7. **[LOW] Trust-region / scaling-constant tuning** (single characteristic length; cheap to sweep).

---

## 5. Decision gates (objective go/no-go per phase)
- **Phase 0 (scaling):** GO only if the *unit-mismatch* condition ratio (root-block vs joint-block diagonal Hessian, measured 2.8e5) drops to O(1). **Do NOT** gate on total cond reaching 10⁶ (structural null-space forbids it). If total cond stays ~10¹³ *after* the unit ratio is O(1) → that confirms the residual ill-conditioning is the null space → proceed knowing LM must handle it. STOP only if scaling changes frozen-phrase metrics beyond noise.
- **Phase 1 (analytic Jacobian):** GO only if ‖J_analytic − J_central‖/‖J‖ < 1e-6 on all 5 phrases AND at least one phrase's ‖∇‖ at the old solution now computes stably (no 100%-error / ABNORMAL). **STOP** (and re-scope differentiation approach) if the JAX-rewrite/hand-derivation cannot reach that tolerance, or requires modifying frozen solver semantics.
- **Phase 2 (GN/LM switch):** GO only if the solver terminates on optimality (not maxfun/maxiter), ‖∇‖ below tol, objective ≤ the audit's budget-lifted reference (e.g. sustained_anchor ≤ 1.2884), deterministic re-solve, and re-scored frozen phrases show no hard-gate regression. **STOP/rollback** to Phase-1 path otherwise.
- **Phase 3 (AL contacts):** GO only if worst-contact → ≪6 mm at moderate μ WITHOUT conditioning regression AND only if Phase 2 left residual contact error worth removing. **Skip** if Phase 2 already yields ≈0 contact (likely — reprioritize toward null-space handling instead).
- **Phase 4 (null-space / banded):** GO if still-hand root travel is not already near-naive after Phase 2, or if N-scaling shows super-linear solve time.

---

## 6. Strongest argument against
The design correctly diagnoses the disease (unit-mismatch ill-conditioning + FD gradients) but its two headline remedies rest on softer ground than presented, and its constraint machinery targets a non-problem. **State scaling is sold as the conditioning fix, yet the measured ill-conditioning is partly a structural rank-deficiency (the null-space flat valley) that no diagonal scaling can remove** — so the promised cond ≤10⁶ is unreachable at Phase 0, and the singular direction is left to LM damping, which the classical literature flags as unstable precisely near rank-deficient Jacobians. **Gauss-Newton is justified by a small-residual/smooth assumption that the objective violates at exactly the target case:** the hardest measured phrase (sustained_anchor) is multi-contact, where contact-activation introduces Jacobian discontinuities and the field uses complementarity/SQP, not smooth least-squares. And **augmented-Lagrangian contacts add tuning-sensitive machinery to perfect a constraint the audit shows is already satisfied (0.03–0.12 mm)**, while the actual failure — residual wander — is deferred to an optional Phase 4. Finally, the enabling step (analytic Jacobians) is costed as MED but has no low-effort path: numpy is not auto-differentiable, so it demands either a forbidden-adjacent JAX rewrite of the frozen solver or brittle hand-derivation. A reviewer could reasonably say: the low-risk 80% (scaling + analytic gradients feeding a bound-constrained quasi-Newton or a general NLP that assumes nothing about residual size) is worth doing, but committing now to the *full* GN/LM+AL/KOMO stack over-fits the architecture to an assumption set the measurements only partially support.

**Why this argument does NOT reach REJECT:** on inspection the posture terms are linear-residual (GN exact), the nonlinear residuals are small at the solution (GN's best case), LM damping *is* the standard and correct tool for the null space (the "instability" is a tuning caveat, not a disqualifier), and every objection above is addressable by revising *scope/prioritization/gates* rather than abandoning the architecture. The counter-literature says GN/LM is wrong for *contact-rich complementarity* problems — which ours is not today, and the §7 review flags the drift for later.

---

## 7. Final judgment — APPROVE WITH REVISIONS

Evidence-based, not preference. The architecture survives; three mandatory revisions:

1. **Front-load a Jacobian-differentiation feasibility spike as Phase 1's first act, and correct the "autodiff over numpy" claim.** Decide explicitly: JAX reimplementation of the cost stack (and how it stays semantically identical to the frozen solver, verified by `Σφ²=E`) vs hand-derived analytic Jacobians. This is the true make-or-break and is currently under-costed; if it fails, the migration must not proceed past Phase 1.
2. **Correct the conditioning promise.** Scaling (Phase 0) fixes the *unit-mismatch* ratio only; the structural null-space near-singularity is handled by LM damping (Phase 2), not scaling. Re-word Phase 0's gate accordingly (gate on the root-vs-joint block ratio → O(1), not total cond → 10⁶) so a partial success isn't misread as full.
3. **Reprioritize contacts vs wander.** Contacts are already ~0; the measured problem is the null-space wander. Make AL-contacts (old Phase 3) *conditional/optional* and elevate null-space handling (LM damping tuning + null-space-projected posture, old Phase 4) to the primary post-convergence objective. Do not build AL unless Phase 2 leaves real contact residual.

With these, the plan is implementation-ready. The core bet — scaled, analytic-Jacobian GN/LM least-squares — is the right one for the problem as measured *today*; the revisions fix an over-promised conditioning claim, an under-costed enabling step, and a mis-prioritized constraint mechanism. Watch item for the future: if expressive/multi-finger make-break contact is added, revisit whether a complementarity/SQP formulation should supersede smooth GN (a re-review trigger, not a present blocker).

No design was modified; nothing implemented.
