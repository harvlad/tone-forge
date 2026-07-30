# sustained_anchor — falsification of the numerical hypothesis (diagnosis only)

This report supersedes the confidence in `FINDINGS_ANCHOR_MECHANISM.md` (kept for the record,
NOT rewritten). A serious falsification attempt broke one piece of that evidence and
reframed the conclusion.

## 1. What we attempted to falsify
Leading hypothesis (~80%): the residual root wander is caused PRIMARILY by numerical
ill-conditioning + optimizer non-convergence, not planner/objective behaviour. Goal: prove it
wrong. Instruments (all read-only, no planner/solver/objective/weight/optimizer change):
determinism audit, reproducibility x{6 full plan, 12 optimizer-only}, scipy termination,
conditioning, null-space map, smoothness scan, and numerical experiments (tighter tol, higher
budget, root rescaling) — run under default AND single-threaded BLAS.

## 2. Evidence gathered
**Determinism — the earlier "clincher" is FALSIFIED.**
`ResistTrajoptPlanner.plan()` x6 → root_travel = **10.237 mm every time, identical state
hash** (22aecc1c), and byte-identical under `OPENBLAS/OMP/MKL/VECLIB=1`. Optimizer-only from a
fixed x0 x12 → **3.3088 mm every time**, identical objective/grad/nit. There is NO
nondeterminism. The prior "10.2 vs 3.3 mm across runs ⇒ non-identifiable" was comparing two
DIFFERENT deterministic computations (end-to-end plan vs a fixed-x0 re-solve), not run-to-run
noise. **Retracted.**

**Termination — non-converged, confirmed.**
`status=1, nit=67, nfev=15015, message "STOP: TOTAL NO. OF F,G EVALUATIONS EXCEEDS LIMIT"`,
final ‖proj grad‖ = 261.3 (converged would be ~0). The solve stops on the function-eval
budget, not on a gradient/tolerance criterion. Tighter ftol(1e-15)/gtol(1e-12)/maxiter(8000)
changed nothing (nfev still capped at the 15000 maxfun default — the binding limit).

**Conditioning — real.**
root curvature d²J/droot² ≈ 8.0e6 vs angle d²J/dangle² ≈ 2.1e4 (metre units); at termination
‖grad‖ on root DoFs = 206 vs angle DoFs = 13. Root is ~350× stiffer per unit.

**Null-space — the reframing evidence.**
Per-knot cost E(root_x offset), fingers re-optimized, moment 1:
```
  offset  -8..-2 mm : E = 0.1424  (FLAT plateau)   contact 0.001 mm
  offset    -1 mm   : E = 0.147
  offset     0 mm   : E = 0.170   (at the established anchor r_est)
  offset  +1..+4 mm : E = 0.22..0.57
  offset    +8 mm   : E = 81.7    (contact breaks, 3.2 mm)
```
The frozen comfort cost's minimum is genuinely OFFSET from the anchor (0.142 < 0.170), on a
shallow flat basin from −2 to −8 mm. Root is poorly constrained by comfort on the − side.

**Weight balance — objective genuinely favours drifting.**
Comfort gain of leaving r_est ≈ 0.170 − 0.142 = 0.028. Resist cost at a 5 mm drift =
W_ROOT·(0.005)² = 300·2.5e-5 = 0.0075. Gain (0.028) ≫ penalty (0.0075), so the TRUE combined
minimum sits a few mm off r_est. Confirmed by objective values: the drifted solve reaches
obj 1.388; a solve that stays near r_est (root-rescaled change-of-variables) reaches obj
1.453 — i.e. **drifting LOWERS the objective.** Root drift is objective-favourable, not a
spurious numerical excursion.

**Smoothness — no pathological kink.** Fine E(root_x) scan over ±1 mm: smooth, max |2nd-diff|
0.04, no discontinuity. The C1-kink sub-hypothesis is not supported near the solution.

## 3. Current best explanation
The root leaves the established anchor because the FROZEN per-knot comfort cost
(strain/manifold/thumb/arch) has its minimum a few mm OFFSET from the anchor — it tracks the
moving upper voice (moment 1's ring at s3f7 reaches furthest, so its comfort optimum pulls the
hand) — and the resist term (W_ROOT=300) is TOO WEAK to hold the hand at the anchor against
that comfort gradient. This is an OBJECTIVE-BALANCE / weak-constraint property, not a solver
bug: the optimizer is correctly descending toward a lower-cost, offset position.

Non-convergence and ill-conditioning are REAL but SECONDARY: they only set the exact
magnitude WITHIN the shallow −2…−8 mm flat basin (why the committed run lands at −5 mm rather
than −2 mm), and they make the un-rescaled solve budget-bound. They do not create the drift;
they blur its size.

So this is NOT purely "a solver problem." It is primarily the objective landscape (comfort
basin tracks the voice, resist under-weighted), compounded by a flat/ill-conditioned root
direction that a budget-limited quasi-Newton solve cannot pin.

## 4. Remaining uncertainty
- **Confidence UPDATE.** "Primarily numerical (ill-conditioning + non-convergence)": DOWN from
  ~80% to **~35%**. "Primarily objective-balance (comfort basin offset from the anchor + resist
  too weak), with non-convergence setting the magnitude": **~55%** — new leading explanation.
  Compound statement "objective genuinely favours a few-mm drift AND non-convergence blurs its
  size": confidence **~80%**.
- I could not fully reconcile WHY the end-to-end plan (10.24 mm) and the fixed-x0 re-solve
  (3.31 mm) land at different deterministic points; both are non-converged budget-bound stops,
  so a small path difference suffices, but I did not isolate it. This weakens any argument that
  rests on that number (already retracted).
- The 8e6 curvature is a finite-difference estimate; a true Hessian/scaling analysis would
  confirm the exact conditioning number.
- Whether the comfort-basin offset is "wrong" (a hand should stay planted and stretch the
  ring) or "right" (a real player does nudge) is a MODELLING judgment about the frozen
  solver's comfort cost — outside this diagnosis.

**Conclusion for the next step:** do NOT treat this as a purely numerical/optimization problem.
The dominant driver is the objective balance between the frozen comfort cost and the resist
term. The numerical non-convergence is a genuine secondary effect. STOP — diagnosis only, no
fix proposed.
