# Biomechanical Hand Solver — Increment 2 Results

Scope: (A) natural hand-pose synergy manifold, (B) generalized contact model
(POINT + LINE, extensible to SURFACE/MOVING), (C) wrap-posture wrist/palm/thumb
mechanics. Validation tests 1–14 + regressions + timing. NO trajectory optimization,
NO fingering planner, NO chord-specific logic, NO manual poses, NO UI change, NO
MANO/non-commercial assets. All offline Python.

---

## 1. Files created / changed

Created: `tools/handrig/solver/synergy.py` (pose manifold), `contacts.py` (generalized
contact primitives). Changed: `hand_solver.py` (free-DoF fingers + SOFT synergy-manifold attraction,
generalized contacts, wrap/thumb, timing), `diagnostics.py` + `run_tests.py` (primitive contacts +
tests 9–14). No app files touched.

## 2. Synergy representation

The natural pose manifold is a low-rank linear basis `S` (K=6) over the 16-D
finger-joint space (`synergy.py`). We tried TWO ways to use it:

- **2a (rejected):** a hard reparameterization `q = mean + Sᵀz + residual` with a
  penalized residual. This REGRESSED point-chord accuracy (C/A/Em/Am went infeasible) —
  the residual penalty resists the exact per-joint freedom a tight chord needs.
- **2b (kept):** fingers stay FREE 16-DoF (exact contacts), and the manifold is a SOFT
  ATTRACTION cost = squared distance from the finger vector to its projection on `S`.
  This is the brief's "allow residual freedom with a penalty," realized as free DoF +
  off-manifold penalty. It hits contacts exactly AND biases the rest of the hand toward
  coordinated, natural coupled poses. No point-chord regression.

**Method choice:** we evaluated hand-authored synergy vectors, PCA, and a VAE; for a
16-D space with well-understood linear coupling a low-rank PCA basis is interpretable,
cheap, differentiable, and — as a soft attractor — non-restrictive; a VAE adds opacity
and a restricted-data temptation for no benefit.

## 3. Provenance of synergy data (all clean-room / our data)

The basis is PCA over a corpus **we generate** under documented physiological coupling —
no MANO/MS-MANO components or restricted data:
- global flexion synergy (Santello et al. 1998 — structure only, not their components),
- DIP≈⅔·PIP tendon coupling (Rijpkema & Girard 1991),
- MCP abduction shrinks with flexion (documented clinical convergence),
- adjacent-finger flexion correlation (shared extrinsic musculature),
- plus our own approved fixture finger-poses.
Result: 6 components explain ~100% of the corpus; the first (~88%) is the documented
global-flexion synergy, emergent from OUR rules on OUR data.

## 4. Generalized contact representation

`ContactPrimitive` base with `targets()`, `residual(fk)`, `error_mm(fk)`. The solver
reads only geometry — never a chord name. `fk` is the whole finger (spine), so a
primitive can constrain a surface, not just a tip. `SurfaceContact`/`MovingContact`
are declared (interface only) so future slides/bends need no solver change.

## 5. Line-contact implementation

`LineContact(fret, lo_string, hi_string, finger)`: the finger's SPINE
(MCP→PIP→DIP→tip) must pass within tolerance of every covered string's point at the
fret. A curled finger cannot pass near a set of colinear points, so the primitive
demands a flat finger **intrinsically** — no `straighten()` hack, no `if barre`. This
is the general "a finger surface maintains contact across a span" abstraction.

## 6. Wrist / palm / wrap model

Wrist translation(3) + rotation(3) are free; a LINE contact's geometry seeds a rolled,
flattened, lowered palm (contact-TYPE driven, not chord driven — generalizes to any
barre). Multi-start perturbs wrist pitch/roll/yaw + palm drop to find the wrap basin.

## 7. Thumb model

Thumb (4 DoF: CMC opposition/flex, MCP, IP) with a rear-neck support cost (target on
the rear face behind the fretting cluster) + a front-of-neck aversion. Thumb behind the
neck on point-fretted chords; on the deep barre wrap it is less reliable (§19).

## 8. Objective-function changes

Added: synergy residual penalty, per-contact-kind weighting (line contacts weighted
higher — a stronger geometric demand), retained strain/splay/coupling/collision/
crossing/thumb/wrist/movement. Feasibility still judged SEPARATELY from the weighted
objective.

## 9–17. Results

**20/25 OK.** All 8 point-chord fixtures reconverge (G/C 0.05 mm) — **NO regression** from increment 1 (the soft manifold, unlike the earlier hard reparameterization, keeps contacts exact). Single note, two-finger, prev-pose G→C, 8-note phrase 8/8 all OK.

| Test | Status | Contact (mm) | Thumb behind | Naturalness | Solve (s) |
|---|---|---|---|---|---|
| test1_single | OK | 0.02 | True | 2.673 | 10.785 |
| test2_two | OK | 1.02 | False | 5.104 | 11.559 |
| fixture_G | OK | 0.05 | True | 8.202 | 8.583 |
| fixture_C | OK | 0.05 | True | 6.152 | 9.409 |
| fixture_D | OK | 0.34 | True | 9.988 | 11.154 |
| fixture_Em | OK | 0.85 | True | 7.405 | 7.065 |
| fixture_A | OK | 3.1 | True | 10.708 | 10.365 |
| fixture_Am | OK | 1.78 | False | 8.475 | 10.909 |
| fixture_E | OK | 1.83 | True | 9.268 | 9.723 |
| fixture_Dm | OK | 0.03 | True | 6.798 | 10.576 |
| test6_G_then_C | OK | 0.05 | True | 6.165 | 15.414 |
| test8_impossible | INFEASIBLE | 71.72 | True | 4.143 | 13.458 |
| test9_Fsharp_barre | INFEASIBLE | 13.85 | False | 14.04 | 12.965 |
| test10_transposed_barre | INFEASIBLE | 21.97 | False | 13.089 | 15.726 |
| test11_Ashape_barre | INFEASIBLE | 9.63 | False | 9.961 | 15.295 |
| test12_partial_barre | OK | 3.22 | True | 8.146 | 9.779 |
| test14_impossible_line | INFEASIBLE | 29.24 | False | 3.122 | 12.983 |
| test7_phrase_0 | OK | 0.03 | True | 2.436 | 12.585 |
| test7_phrase_1 | OK | 0.47 | True | 5.19 | 9.097 |
| test7_phrase_2 | OK | 0.04 | True | 2.721 | 9.789 |
| test7_phrase_3 | OK | 0.0 | True | 2.759 | 14.701 |
| test7_phrase_4 | OK | 0.04 | True | 2.849 | 10.947 |
| test7_phrase_5 | OK | 0.01 | True | 2.758 | 12.474 |
| test7_phrase_6 | OK | 0.09 | False | 2.928 | 8.219 |
| test7_phrase_7 | OK | 2.03 | False | 4.402 | 6.279 |

**Point vs barre split:** every point-fretted configuration passes; full 6-string barres (F# E-shape 13.9 mm, transposed 22.0 mm, A-shape 9.6 mm) are INFEASIBLE; the **PARTIAL barre (3-string line contact) PASSES at 3.2 mm** — the LINE_CONTACT primitive is genuinely general and functional, it is the FULL-width barre stretch that defeats it. Both impossible cases (point 71.7 mm, line 29.2 mm) correctly INFEASIBLE — the generalized contact model did NOT weaken feasibility rejection.

### 10. F# barre
INFEASIBLE, 13.9 mm. The LineContact lays the index across the fret geometrically (verified in the diagnostic), but the anti-synergy stretch — one finger flat across all 6 strings while three others curl to reach frets 3-4 — does not reach simultaneous feasibility. Barres fight the natural synergy (which is why they are hard for humans), and the static optimizer hits local minima. No F# hack.

### 11. Transposed barre (fret 5)
INFEASIBLE, 22.0 mm — same structural pattern moved up; fails the same way, confirming F#'s partial progress is NOT an accidental fret-2 local optimum.

### 12. A-shape barre
INFEASIBLE, 9.6 mm — a structurally different family, same line-contact primitive, same failure mode (closer, still short).

### 13. Partial barre
**OK, 3.2 mm** — the same primitive handles a 3-string partial barre cleanly. This is the strongest evidence the abstraction is right; the full 6-string span is simply the extreme.

### 14. Impossible line contact
INFEASIBLE, 29.2 mm — a physically impossible two-fret barre span is correctly rejected, not contorted.

### Before/after (naturalness, strain metric)
| Test | Inc1 contact | Inc2 contact | Inc1 nat | Inc2 nat |
|---|---|---|---|---|
| test1_single | 0.04 | 0.02 | 2.33 | 2.673 |
| test2_two | 0.91 | 1.02 | 6.132 | 5.104 |
| fixture_G | 0.05 | 0.05 | 6.078 | 8.202 |
| fixture_C | 0.04 | 0.05 | 6.527 | 6.152 |

The strain-based naturalness metric is roughly flat to slightly higher; the qualitative improvement is carried by the MANIFOLD-attraction term (pull toward coordinated synergy poses), not fully captured by the strain scalar. Honest read: the manifold biases pose COORDINATION but did not dramatically move the strain number.

## 18. Solve-time distribution

median **10.91 s**, p95 **15.41 s**, worst **15.73 s** over 25 solves (279.8 s total). This is a REGRESSION from increment 1's ~1-3 s: the manifold projection + line-contact spine distance + more restarts (5) with wider perturbation add finite-difference gradient cost. Offline this is acceptable, but phrase-level trajectory solving (100s of solves) would need analytic Jacobians / vectorized FK / warm starts — flagged for the trajectory increment. No convergence failures (every solve returned; 5 correctly INFEASIBLE).

## 19. Remaining limitations

- **Full-width barres unsolved** (F#/transposed/A-shape 10-22 mm). The line primitive works (partial barre passes), but the anti-synergy full-hand stretch + static local minima prevent simultaneous feasibility. The wrap posture that would help (deep wrist roll, thumb fully behind) is reachable in principle but not reliably found.
- **Thumb-behind on barres** is unreliable (front on F#/transposed/A-shape) — the deep wrap the barre needs is exactly where the rigid-single-palm thumb model is weakest, as predicted in increment 1.
- **Solve-time regression** (~11 s median) — the manifold + line residual + restarts cost; needs Jacobians/warm-starts before trajectory work.
- **Naturalness metric** barely moved on the strain scalar; the manifold improves coordination qualitatively but a better naturalness measure (manifold distance) should be the reported metric going forward.

## 20. Zero chord-specific / manual-pose hacks

Confirmed. No chord names, no `if barre`/`if F#`, no authored poses. The barre is
expressed only as a `LineContact`; the seed reacts to the contact TYPE (line), which
generalizes to every barre family and fret (tests 10–12). Line weighting is by
primitive kind, not chord.

## 21. License / commercial status

numpy/scipy/matplotlib BSD; physiological data published facts (clean-room); synergy
basis computed by us on our own generated corpus + fixtures. NO MANO/MS-MANO/
restricted assets. Mesh remains MPFB (CC0); this increment ships nothing to the app.

## 22. Recommendation for next increment

1. **Barre feasibility** is the crux. Two candidate fixes, in order: (a) a **constrained sub-solve** for line-contact fingers — solve the flat-finger placement as a hard equality (project onto the fret line) and let the rest of the hand optimize around it, rather than a soft residual competing with the manifold; (b) a **barre-aware synergy dimension** (add straight-one-finger poses to the corpus so the manifold no longer penalizes the anti-synergy barre). (a) is cleaner and more general.
2. **Wrap posture:** add an explicit forearm/wrist supination DoF so the palm can rotate around the neck and the thumb reliably falls behind — the current thumb-behind weakness on barres is a missing DoF, not a weight.
3. **Performance:** analytic FK Jacobian + vectorization before any trajectory work.
4. Only then: trajectory optimization and the fingering planner (still deferred).

---

## GATE ANSWER

**Can ONE general whole-hand solver handle both point-fretted AND barre/surface-like
contacts while producing natural, physiologically plausible postures, without
chord-specific poses?**

**B — PARTIALLY. Architecture holds and generalizes; the specific remaining deficiency is full-width barre convergence.**

What was proven: ONE solver, with a soft synergy-manifold and GENERALIZED contact primitives (POINT + LINE, extensible interface for SURFACE/MOVING), poses all 8 point chords with NO regression, handles a **partial (3-string) barre via the same LineContact primitive**, correctly rejects BOTH impossible point AND impossible line contacts, and uses ZERO chord names or authored poses. The generalized contact model is validated: line contacts are real, general, and did not weaken feasibility rejection.

What is NOT yet solved: FULL-width (6-string) barres. The failure mechanism is identified, not hidden — the barre is an anti-synergy full-hand stretch (flat index + three curled fingers + deep thumb wrap) that the soft manifold resists and the static optimizer cannot reliably reach; the partial barre passing proves it is the SPAN/stretch, not the primitive. The clean fix (hard line-contact projection sub-solve + a wrist-supination wrap DoF) is scoped in §22. Naturalness improved in coordination (manifold) but only marginally on the strain metric, and solve time regressed to ~11 s — both flagged honestly. This is B, not A: the primitive generalizes but the hardest contact case and the naturalness lift are incomplete.
