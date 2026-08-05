# M7 findings — first REAL planner + first trustworthy eval

## The core question
> Does whole-phrase trajectory optimization measurably beat memoryless per-note solving?

**Answer (measured, honest): YES — but narrowly and only where naive makes an unnecessary
shift. It does NOT clear the full pre-registered V1 bar.**

## What we built
`TrajoptPlanner`: whole-phrase kinematic optimization. Per knot the frozen solver cost still
demands feasible contacts; two cross-knot terms are added over the whole phrase —
positional-commitment (penalize inter-moment root motion) and smoothness (penalize
inter-moment angle change). Hyperparameters (W_ROOT=300, W_SMOOTH=1.5) set ONCE from
reasoning, NOT tuned to pass. `check_m7.py` evaluates against the pre-registered V1 gates
(design doc §8, frozen before any planner existed).

## Results (5-phrase V1 subset; naive baseline vs trajopt)
```
phrase              naive                         trajopt
single           shift0 root0  tip0   sc1.000 |  shift0 root0  tip0   sc1.000
repeated_note    shift0 root0  tip14  sc1.000 |  shift0 root5  tip14  sc1.000
string_crossing  shift0 root1  tip253 sc0.782 |  shift0 root10 tip257 sc0.782
one_shift_scale  shift2 root92 tip889 sc0.661 |  shift1 root94 tip887 sc0.782  <-- WIN
sustained_anchor shift0 root0  tip52  sc1.000 |  shift0 root9  tip44  sc1.000

gate1 contact non-regression : PASS
gate2 no hard-gate regression: PASS
gate3 shift economy          : PASS   mean 0.40 -> 0.20 (halved)
gate4 finger reuse +25%      : FAIL   0.33 -> 0.33   (fingering FIXED — unmeetable)
gate5 travel -20% each       : FAIL   tip 242->240 (-1%),  root 18->23 (WORSE)
gate6 anchor retention >=0.9 : FAIL   0.50 -> 0.50   (fingering FIXED — unmeetable)
gate7 MovementScore +0.15    : FAIL   0.889 -> 0.913 (+0.024)
gate8 visual                 : DEFERRED (M8)
```

## The three findings that matter
1. **The win is real and specific.** On `one_shift_scale`, trajopt sees the whole phrase and
   refuses the unnecessary f9→f11 hand relocation naive makes — shift 2→1, contact 3.73→0.12
   mm, score 0.661→0.782. Suite-mean shifts halved. Positional commitment works exactly as
   designed. That is genuine evidence whole-phrase beats memoryless — for the specific defect
   of memoryless over-shifting.

2. **Naive-with-warm-start is a much STRONGER baseline than the design assumed.** §8 expected
   "naive baseline ≫" the shift bar (i.e. naive shifts a lot). Measured reality: naive's
   warm-start already holds position on 4 of 5 phrases (root ≈ 0). So the headroom for a
   motion planner is SMALL — the only clear win is the occasional unnecessary shift. This is
   why gates 5/7 fail: there is little wasted motion left to remove. An honest, and initially
   surprising, result.

3. **Two pre-registered gates are mis-specified for a fixed-fingering V1.** `finger_reuse`
   (gate 4) and `anchor_retention` (gate 6) are functions of the FINGERING, which is a FIXED
   input in V1 (§9: "fingering is an input, not a variable"). No motion planner can move them
   — naive and trajopt score identically (0.33, 0.50) by construction. These gates belong to
   a future fingering planner, not a motion planner. They should be removed from the motion-
   V1 bar, not "failed".

## Honest verdict
Trajopt clears the HARD gates and shift economy; it does not clear travel/aggregate margins,
and cannot clear the two fingering gates. As an MVP it is a **narrow, real, but not yet
decisive** improvement. Its centroid-centering even slightly WORSENS root travel on phrases
where naive already commits (repeated/string_crossing/anchor: root 0→5..10 mm) — the MVP
moves the hand toward a shared centre when it should only resist motion, not induce it. I did
NOT re-tune to hide this.

## Answers to the 5 questions
1. **Built:** trajopt whole-phrase optimizer + pre-registered gate harness + trustworthy diff.
2. **Learned:** whole-phrase optimization beats memoryless ONLY on unnecessary-shift phrases;
   the warm-started naive baseline is already strong; two gates can't discriminate motion.
3. **Confirmed:** the M3 interface sufficed unchanged; NLP converged at phrase scale (≤120
   vars) with no fight — the 3a/3b/3c split was not needed.
4. **Disproved:** the design's implicit assumption that naive wastes a lot of motion. It does
   not (warm-start). And the assumption that all 7 soft gates apply to a motion planner.
5. **What should change** (decisions for the user — NOT changed unilaterally; benchmark is
   frozen):
   - Re-scope the motion-V1 bar to the gates a motion planner CAN move: {1,2,3,5,7}; move
     {4,6} to a future fingering-planner benchmark.
   - Fix trajopt's root term to be resist-only (penalize motion, never pull toward a centroid)
     so it stops adding travel on already-committed phrases.
   - Revisit `string_crossing`'s fingertip band [0,120] — 253 mm is inherent 4-finger reach,
     not waste; the band wrongly penalizes it (reference-authoring error, the flagged risk).
   - Only then is a fair re-run of gates 5/7 meaningful.

M7 done-when is satisfied: a trustworthy measured verdict against pre-registered thresholds
(it clearly does NOT clear the full bar, and exactly why is measured). The empirical track's
central question now has an evidence-based answer.
