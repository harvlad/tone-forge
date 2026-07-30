# Hand-Rig Optimizer Audit — is the optimizer faithfully solving the frozen planner?

Phase after M7/M7A (both frozen, untouched). No code changed, no planner/optimizer/benchmark
modified. All measurements come from read-only diagnostic probes that reconstruct the FROZEN
`TrajoptPlanner` objective exactly and instrument it (`/tmp/m8_optaudit.py`, committed as
`benchmark/opt_audit.py` for the record). Central question:

**Is the remaining multi-contact wander an OPTIMIZATION failure or a MODELING failure?**

## HEADLINE
**Primarily OPTIMIZATION.** The optimizer is provably NOT faithfully solving the planner we
already have — it stops on its evaluation budget (or aborts abnormally) far from stationarity,
on an objective that is extremely ill-conditioned because of a mixed-unit (metres vs radians)
state vector solved with finite-difference gradients. A modeling contribution exists (a shallow
null-space valley lets the hand drift ~2–10 mm at ~zero objective cost) but it **cannot be
isolated or trusted until the optimizer converges**, because today it does not. Per the phase
rule ("do not improve the planner until the optimizer faithfully solves it"), the optimizer is
the primary bottleneck and must be addressed first.

---

## STAGE 1 — Optimization problem (as implemented, frozen)
- **State** `s ∈ ℝ³⁰` per knot: `[0:3]` root translation (**metres**); `[3:6]` root-rotation euler offset (**rad**); `[6:22]` 4 fingers×4 DoF, `[22:26]` thumb, `[26:30]` metacarpal cup (all **rad**).
- **Decision variable** `x = concat(s_0..s_{N-1}) ∈ ℝ^{30N}`. All components free (nothing pinned). `N==1` short-circuits to naive.
- **Constraints**: per-DoF **box bounds** only (root ±0.15 m about a shared centre; RROT ±0.6 rad; physiological finger/thumb/meta limits). No equality/contact constraints — contacts are penalties.
- **Objective** `J = Σᵢ E(sᵢ,Cᵢ) + W_ROOT·Σ_{i≥1}‖rᵢ−r_{i−1}‖² + W_SMOOTH·Σ_{i≥1}‖qᵢ−q_{i−1}‖²`, with `r=s[:3]` (m), `q=s[6:]` (24 rad DoF; **root rotation 3:6 is in neither cross-knot term**). `W_ROOT=300`, `W_SMOOTH=1.5` (set once, not tuned).
- **Per-knot `E`** = weighted sum of heterogeneous terms mixing **mm²** (contact, board penetration, collision, conform, thumb) and **rad²** (strain, splay, coupling, manifold, wrist, arch, individ). Weights `contact 8, board 40, collision 30, strain 2.4, …`.
- **Scaling**: NONE. Raw metres and radians share one vector; the weights rescale objective *contributions*, not *variable* scales.
- **Solver**: SciPy `L-BFGS-B`, **no `jac` → 2-point finite-difference gradient**, `maxiter=600, ftol=1e-9`, defaults `gtol=1e-5, maxfun=15000`. Warm-started from the naive solve (itself a 5-restart L-BFGS-B static solve). `res.x` used with no `res.success` check.
- Full index/units/weights spec: see the code cartography (planners.py:92–162, adapters.py:116/124/146, solver/mpfb_solver.py:44–56,105–185). Docstring says "State (29)" — actually 30 (meta block added); off-by-one trap for reimplementers.

---

## STAGE 2 — Numerical health audit (measured)
Probe over `sustained_anchor` (unfixed multi-contact), `string_crossing` (fixed by resist), `one_shift_scale` (the win).

| Category | Verdict | Measurement |
|---|---|---|
| Convergence | **BROKEN** | All phrases stop at `maxfun` (nfev 15015 / 15246 / 18029) after only 46–74 iterations; `sustained` msg "F,G EVALS EXCEEDS LIMIT", `one_shift` msg **"ABNORMAL"**. |
| Stationarity | **BROKEN** | ‖∇J‖ at the returned "solution" = **52 / 555 / 5.1e5** (a real optimum needs ≈0). Not local minima. |
| Gradient correctness | **BROKEN** (worst on the win phrase) | forward-vs-central FD relative error = 4.7e-3 / 1.3e-3 / **1.00** (one_shift FD gradient is 100 % wrong). |
| Conditioning | **BROKEN** | cond(H) ≈ **4.3e13 / 7.2e12 / 3.6e14** (≳1e8 already severe in double precision). |
| Scaling (m vs rad) | **BROKEN** | diag-Hessian magnitude ratio root-translation : joint = **2.8e5 / 5.0e3 / 3.5e4**. Root (metres) directions carry 10³–10⁵× the curvature of joint (radians) directions — the direct cause of the ill-conditioning. |
| Hessian-model quality (L-BFGS) | **Broken (consequence)** | quasi-Newton curvature built from FD gradients on a 1e13-conditioned objective is meaningless. |
| Line search / stopping | **Questionable→Broken** | budget (`maxfun`), not `gtol`/`ftol`, is the binding stop; `one_shift` aborts ABNORMAL. |
| Reproducibility | **HEALTHY** | identical re-solves, ‖Δx‖=0 (deterministic warm start). |
| Null-space (modeling) | **Questionable (real, secondary)** | rigid along-neck shift of the whole trajectory: `sustained` Δobj = 0.000 at −5/−2/0 mm then +31.8 at +2 mm — a **flat valley** for ~5 mm of nut-ward drift; `string_crossing` flat for +2..+10 mm; `one_shift` flat ±2 mm. The residual wander lives in these ~2–10 mm near-flat directions the objective barely penalises. |

**Confirmatory (budget lifted, read-only, not shipped):** `sustained_anchor` shipped stops at nfev=15015, f=1.3383, ‖g‖=58; lifting maxfun→400k continues to nfev=46319 and reaches a **lower** f=1.2884 (Δf=0.050) with a **materially different pose (‖Δx‖=0.033, ~32 mm in state space)** — yet ‖g‖ is still 96, so even 46k evals do not reach stationarity (the conditioning defeats it). `one_shift_scale` returns identical ABNORMAL result regardless of budget (broken gradient, not budget). ⇒ the shipped "solutions" are stopped-on-budget / aborted, **not optima**.

---

## STAGE 3 — The bottleneck (demonstrated, not assumed)
If the planner objective is frozen today, what prevents the optimizer from reaching the intended
solution? **A compounding optimization failure, in causal order:**
1. **Mixed-unit state (metres vs radians), unscaled** → Hessian condition number ~10¹³–10¹⁴ (curvature ratio up to 2.8e5). 
2. **Finite-difference gradients** on that ill-conditioned objective → corrupted L-BFGS curvature model; on `one_shift_scale` the FD gradient is 100 % wrong and L-BFGS-B aborts ABNORMAL; elsewhere each gradient costs ~30N evals so the run exhausts `maxfun=15000` at 46–74 iterations.
3. **Result:** every shipped solution stops on budget / abnormally with ‖∇J‖ far from 0 — not a local minimum. A better, different solution provably exists (Δf=0.05, 32 mm pose change) but the conditioning prevents reaching even that.
4. **Secondary (modeling):** a shallow null-space valley (~2–10 mm) means part of the residual wander is genuinely un-penalised by the objective — but this is **entangled with and dominated by** the non-convergence and cannot be attributed cleanly until (1)–(3) are fixed.

**Answer to the central question: the optimizer is NOT faithfully solving the frozen planner. The wander is primarily an optimization failure; a modeling (null-space) component exists but is currently unquantifiable because the solver does not converge.**

---

## STAGE 4 — Literature (methods matching this structure)
The problem is, in field terms, a **discrete-time direct trajectory optimization**: sum-of-(mostly)-squares objective, banded cross-knot coupling, box constraints, kinematic redundancy, soft-penalty task targets on an SE(3)×joint state. Each observed defect is a named phenomenon with a standard fix.
- **KOMO / k-order Newton (Toussaint 2014)** — nearest match: objective is `Σ‖φᵢ‖²` over knot cliques, solved by **Gauss-Newton (JᵀJ) in an augmented-Lagrangian outer loop**; Markov cliques ⇒ banded, well-conditioned Hessian. *Our objective is already a KOMO problem solved with the wrong solver.*
- **Ratliff, "Fundamental importance of Gauss-Newton in motion optimization"** — motion costs are sums-of-squares ⇒ `JᵀJ` is a free, well-conditioned pseudo-Hessian; the theoretical backbone under KOMO.
- **CHOMP (2013)** — the smoothness term defines a metric `A`; the covariant update `A⁻¹∇U` is a preconditioner that a raw L-BFGS-B never sees. Confirms: our smoothness term implies a natural preconditioner we don't use.
- **TrajOpt (2014)** — sequential convex + **escalating exact-penalty**; the canonical fix for "fixed quadratic penalty leaves residual violation."
- **Damped least squares / task-priority null-space IK (Nakamura, Chiaverini, Dietrich et al.)** — LM damping `λI` regularises the rank-deficient null space; a null-space-projected posture term consumes redundant DoF deterministically instead of letting the hand drift.
- **Penalty-method theory (Nocedal & Wright; augmented-Lagrangian)** — a fixed quadratic penalty has residual `≈ −λ*/μ` (never zero at finite μ) and cond ∝ μ; **augmented Lagrangian** reaches exact satisfaction at moderate μ.
- **Scaling (Nocedal & Wright §2.2; SE(3)-metric refs)** — quasi-Newton is NOT scale-invariant; mixing m and rad in one metric is a known distortion; fix by a characteristic length `1 rad ↔ L≈0.05–0.1 m` (diagonal state scaling).
- **FD-gradient failure (documented)** — at 150–300 dim, FD gradients cost ~4N evals and corrupt L-BFGS curvature; a cited case stalled at 44 iterations vs 365 with autodiff. Matches our 46–74-iteration budget stall.

---

## STAGE 5 — Gap analysis
- **We already do well:** direct transcription (all knots are variables — correct); a mostly sum-of-squares objective (GN-ready); a sensible smoothness/commitment structure; deterministic, reproducible runs; warm-starting.
- **Where we differ from the literature:** (a) scalar black-box L-BFGS-B instead of structure-exploiting **Gauss-Newton/LM**; (b) **finite-difference** instead of analytic/AD Jacobians; (c) **no state scaling** (raw m vs rad); (d) contacts as **fixed-weight quadratic penalties** instead of augmented-Lagrangian/exact; (e) redundant null space **unregularised** (no LM damping / null-space projection).
- **Are our issues common?** Yes — every one is a textbook failure mode with a canonical remedy. Nothing here is exotic.
- **Do established techniques exist for the exact pathology?** Yes, directly: GN/LM on the residual Jacobian (conditioning + null-space damping), state scaling (mixed-unit), augmented Lagrangian (penalty residual), analytic/AD gradients (FD stall). KOMO is a near-drop-in embodiment of our structure.

---

## STAGE 6 — Design options (architectural only; nothing implemented)
| Option | Risk | Expected benefit | Effort | Confidence |
|---|---|---|---|---|
| **State scaling** — optimize `y = W x` with `W` making 1 rad ↔ ~0.05 m (diagonal); no objective change | LOW | Cuts cond by ~10³–10⁵; likely lets L-BFGS actually converge | ~0.5 day | HIGH — measured scale ratio is the direct cause |
| **Analytic/AD gradients** (autograd/JAX or hand-Jacobian) feeding `jac=` | LOW–MED | Removes FD cost + the 100 %-wrong `one_shift` gradient + ABNORMAL aborts; unlocks GN | 1–3 days (FK/penalty differentiation) | HIGH — FD stall is measured |
| **Tighten/fix termination + `res.success` check, raise maxfun** (diagnostic-grade, not a real fix) | LOW | Confirms/《stops shipping non-optima; small quality gain | hours | HIGH it changes numbers; LOW it fixes conditioning (measured: still ‖g‖=96) |
| **Gauss-Newton / Levenberg-Marquardt** on the residual vector (needs Jacobian) | MED | Right solver for sum-of-squares; LM damping tames the null-space wander; big conditioning + convergence win | 3–7 days | MED–HIGH (literature strongly converges here) |
| **Augmented-Lagrangian contacts** (outer multiplier loop) instead of fixed quadratic penalty | MED | Exact contact satisfaction without raising μ into ill-conditioning | 2–4 days | MED |
| **Null-space-projected posture term** (task-priority) to pin redundant DoF | MED | Removes the modeling-side wander deterministically | 2–4 days | MED (addresses the secondary cause) |
| **Replace with a real NLP (IPOPT/SNOPT) or adopt KOMO** | HIGH | Structurally correct end state; most robust | 1–3 weeks + dep | MED (large surface; may be overkill) |

Natural sequencing (NOT a commitment): scaling + analytic gradients are prerequisites and likely
resolve most of the non-convergence at low risk; only after the optimizer converges can the
residual wander be re-measured to see whether a modeling fix (null-space projection / AL
contacts) is still needed.

---

## FINAL REPORT (the four required syntheses)
1. **Understanding of the planner:** M7 = MIXED (phrase-level commitment + pose conditioning are real, one-phrase win, pervasive still-hand wander). M7A = MIXED (centroid attraction is *a* wander cause, removable on single-contact phrases; a distinct multi-contact cause remains). Both frozen.
2. **Understanding of the optimizer:** it does NOT reliably solve the frozen planner. Extreme ill-conditioning (~10¹³–10¹⁴) from an unscaled mixed-unit (m vs rad) state, plus finite-difference gradients, make L-BFGS-B stop on evaluation budget or abort abnormally with a large gradient — the shipped poses are not optima. Reproducible, but reproducibly non-converged.
3. **Evidence:** Stage-2 table + confirmatory budget-lift (Δf=0.05, 32 mm pose change, ‖g‖ still 96; `one_shift` ABNORMAL regardless of budget); null-space flat-valley probe (0.000 Δobj over several mm on `sustained_anchor`).
4. **Comparison with literature:** every defect is a named, standard-fix phenomenon (GN/LM, AD gradients, state scaling, augmented Lagrangian, null-space projection); KOMO is a near-drop-in match to our structure.

**Prioritized directions** (benefit / risk / effort / confidence) are the Stage-6 table; the low-risk pair (state scaling + analytic gradients) is the highest-leverage next step and a precondition before any further planner or modeling work.

## Conclusion for the phase question
Optimization **is** the primary technical bottleneck: we have shown the optimizer is not faithfully
solving the planner we already have. No planner/modeling change is justified until the solver
converges. Nothing was implemented; this is the decision input for whether to commit to an
optimizer rebuild.
