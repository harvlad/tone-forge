# sustained_anchor — mechanism investigation (understanding, not a fix)

Question: with centroid attraction removed, what ONE dominant mechanism still produces the
unnecessary root motion (naive 0.3 mm → resist 10.2 mm)? No code was changed; this is a
read-only instrumentation of the resist objective (`investigate_anchor.py`).

## The rigid-body hypothesis is REJECTED
Counterfactual (section 4/5): pin the root to the established incumbent `r_est` and optimize
only the fingers, vs the free per-moment solve.
```
        free root disp    E_free    E_pinned    dE(pinned-free)   contacts (pinned)
  m0        0.00 mm        0.491      0.411        -0.080          index 0.001, middle 0.004
  m1        0.26 mm        0.693      0.171        -0.522          index 0.001, ring   0.003
  m2        0.28 mm        0.370      0.317        -0.053          index 0.001, middle 0.001
```
Holding the root is **cheaper** (dE < 0 everywhere; m1 by −0.52) AND fully feasible. So the
optimizer is NOT translating the hand because "moving one fingertip is cheaper than
articulating the fingers" — the opposite is true: articulating from a held root is the
lowest-cost, feasible solution. Comfort/rigid-body does not drive the drift.

## The solve does not converge in the root direction
Section 6, at the resist "solution":
```
  ||grad||  at solution      = 37,771       (converged would be ~0)
  |grad| on the root DoFs    = 32,814       (87% of the gradient is on root)
  d2J/d(root_x)^2 (m1)       = 7.98e6
  d2J/d(angle)^2  (m1)       = 2.29e4        (root ~350x stiffer than finger angles)
```
The objective is severely ill-conditioned: the root direction is ~350× stiffer than the
finger-angle directions (W_ROOT=300 builds a stiff pin-valley, and the contact term moves all
fingertips 1:1 with the root, so root displacement is penalised very steeply). L-BFGS-B
(quasi-Newton, assumes a smooth well-scaled bowl) terminates on ftol/maxiter with a large
residual gradient concentrated on the root — it never reaches the valley floor.

## The root position is non-identifiable (the clincher)
Two runs of the SAME planner at the SAME config produced different root drift:
```
  committed resist run:   m1 root -5.1 mm in x, then back  -> root_travel 10.24 mm
  investigation re-solve:  m1 root  ~1.9 mm                 -> inter-moment ~3.3 mm
```
Identical deterministic code giving 10.2 mm vs 3.3 mm is only possible if the root is not
identifiably determined — the optimizer stops at different points on a flat/ill-conditioned,
non-converged landscape (floating-point/BLAS reduction-order noise, invisible on a
well-conditioned problem, is amplified here into millimetres). A converged unique minimum
would reproduce exactly. It does not.

## Finger responsibility (section 3)
At moment 1, perturbing the root shows both contacts are stiff in root: +10 mm along-neck
blows out ring by 10 mm; ±10 mm across-strings blows out BOTH index and ring by 6–7 mm. No
finger "wins" a tug-of-war — every contact resists root motion strongly, which is exactly why
the true optimum is the held position and why the root valley is so stiff. There is no
behavioural puller.

## Causal graph
```
  W_ROOT=300 pin term            contact term ties all fingertips 1:1 to the root
        \                                   /
         \                                 /
          v                               v
   objective is a STIFF, ~350:1 ill-conditioned valley in the root direction
          |
          v
   L-BFGS-B (quasi-Newton, smooth-bowl assumption) cannot resolve the stiff valley
          |
          v
   terminates NON-converged: ||grad|| ~ 3.8e4, 87% on root DoFs
          |
          v
   root left a few mm off the true (held) optimum; exact offset is run-dependent
          |
          v
   root_travel ~= 10 mm barely crosses the [0,10] economy band  ->  position_wander flag
```
The behavioural optimum (pinned root) is genuinely the cheapest; the residual motion is
numerical drift, not a decision to relocate the hand.

## FINAL VERDICT
**The dominant remaining mechanism causing sustained_anchor's unnecessary root motion is
NUMERICAL: severe ill-conditioning of the objective in the root direction combined with
optimizer non-convergence, leaving small, run-dependent root drift off the true (held)
optimum.** It is NOT a behavioural/objective error (the held position is provably cheapest)
and NOT the rigid-body "translate instead of articulate" effect (rejected — articulating from
a held root is cheaper).

Confidence: ~80%.
Evidence ranking of remaining plausible mechanisms:
1. **Ill-conditioning + non-convergence / non-identifiable root (numerical)** — ~80%.
   Supported by: pinned strictly cheaper, ||grad|| huge and root-dominated, 350:1 curvature
   ratio, and non-reproducible root across identical runs.
2. Contact-region penalty is C1-but-not-C2 (kinks at the zero-penalty box boundary), which
   defeats quasi-Newton convergence — a specific sub-cause of (1), ~15%.
3. Rigid-body comfort translation — REJECTED, <5%.

The ~20% residual uncertainty: the 8e6 curvature is a finite-difference estimate at a
possibly non-smooth point and could overstate the true stiffness; a proper Hessian/scaling
analysis would confirm. But the non-reproducibility across runs is hard to explain by
anything other than non-convergence.

STOP — mechanism identified. No fix proposed.
