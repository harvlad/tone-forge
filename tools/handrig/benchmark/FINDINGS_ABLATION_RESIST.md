# Ablation — resist-only root movement (one mechanism)

**Hypothesis:** replacing trajopt's implicit centroid attraction with a resist-only root
term preserves the useful positional commitment (one_shift_scale 2→1) while eliminating the
unnecessary root-motion regressions (string_crossing, sustained_anchor).

**Verdict (B): MIXED.**

## The mechanism, before and after (only this changed)

Original trajopt objective:
```
J = Sum_i E(s_i, C_i)  +  W_ROOT Sum_{i>=1} ||r_i - r_{i-1}||^2  +  W_SMOOTH Sum_{i>=1} ||q_i - q_{i-1}||^2
```
The root term is inter-knot resist but **translation-invariant** — adding a constant delta to
every root leaves it unchanged. So absolute position is pinned only by (a) a CENTROID-centred
bounds box and (b) per-knot comfort in E; the free rigid chain drifts toward the comfort
optimum ~ the centroid. That implicit "prefer the geometric centre" is the attraction.

Resist-only (`trajopt_resist`), one term changed:
```
J = Sum_i E(s_i, C_i)
  + W_ROOT [ ||r_0 - r_est||^2 + Sum_{i>=1} ||r_i - r_{i-1}||^2 ]      <-- anchor to incumbent
  + W_SMOOTH Sum_{i>=1} ||q_i - q_{i-1}||^2
```
`r_est` = the established incumbent (naive root at moment 0 — where the hand already is).
The anchor breaks translation-invariance: a rigid shift by delta now costs W_ROOT||delta||^2.
The centroid is removed from the bounds and seed entirely (bounds centred on r_est, padded to
keep every necessary shift reachable). No preferred root location — only inertia against
displacement. Same weights (W_ROOT=300, W_SMOOTH=1.5), same maxiter=600. **NOT retuned.**

## Three-way frozen benchmark (5 phrases, everything else unchanged)
```
phrase            metric            naive    trajopt   resist
single            (all)             identical across all three
repeated_note     root_travel_mm    0.0      4.7       4.6
string_crossing   root_travel_mm    0.5      9.8       2.6     <- regression reduced 73%
                  worst_contact_mm  0.012    0.020     0.015
one_shift_scale   shift_count       2        1         1       <- win preserved
                  worst_contact_mm  3.734    0.122     0.034   <- contact even tighter
                  movement_score    0.661    0.782     0.782
sustained_anchor  root_travel_mm    0.3      9.1       10.2    <- NOT fixed (slightly worse)
                  movement_score    1.000    1.000     0.998   (new position_wander tag)
```

## What the ablation establishes
1. **The one_shift_scale win is NOT an artifact of centroid attraction.** Resist preserves
   shift 2→1 and *improves* contact (0.122→0.034 mm). The positional-commitment benefit is
   real and survives removing the centroid.
2. **Centroid attraction was A cause of the regressions — for single-contact phrases.**
   string_crossing's unnecessary motion dropped 9.8→2.6 mm (−73%, materially removed; 2.1 mm
   residual above naive's 0.5 mm).
3. **It was NOT the whole story.** sustained_anchor (a MULTI-contact phrase: index anchor +
   moving voice) did NOT improve — 9.1→10.2 mm, now flagged `position_wander`. Its two-contact
   per-knot comfort gradient in E overrides the resist inertia at W_ROOT=300. A separate cause,
   not addressed by this single mechanism, and NOT chased by retuning (stop condition).

## Shift-refusal / new-pathology probes (all clear except the known non-fix)
- **No shift-refusal**: one_shift_scale still shifts (shift=1), feasible, worst 0.034 mm.
- **No contact/stretch pathology**: every phrase's worst contact ≤ naive (or ~equal); no
  phrase near the feasibility limit (all < 0.8·tol); fingertip travel within +0.4 mm of naive.
- **No wrist/strain blow-up** observed in contact or travel; the only new failure tag is
  `sustained_anchor: position_wander`, i.e. the unfixed regression itself.

## Frozen V1 gate (A) — unchanged, reported as-is
```
trajopt         gates 1-7:  1:P 2:P 3:P 4:F 5:F 6:F 7:F
trajopt_resist  gates 1-7:  1:P 2:P 3:P 4:F 5:F 6:F 7:F
```
Identical. Resist does not move the frozen gate: shift economy still PASS; travel (5) still
FAIL suite-mean (string_crossing fingertip travel is inherent, unchanged; sustained_anchor
root worse); reuse/anchor (4,6) still structurally unmeetable with frozen fingering; aggregate
(7) still short of +0.15. (Gate reported unchanged per instruction; NOT rewritten.)

## Conclusion for the narrow question
Resist-only is a **real, partial** improvement: it removes the single-contact regression and
strengthens the win, proving centroid attraction was a genuine objective-function mistake —
but a multi-contact regression persists from a distinct cause (per-knot comfort gradient).
The one_shift_scale advantage generalizes as far as *not being an artifact*; whether it
generalizes to a broad phrase win remains unproven (unchanged from M7). Next-step decision is
the user's.
