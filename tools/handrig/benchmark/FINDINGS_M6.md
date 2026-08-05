# M6 findings — timing metric (on-beat arrival)

**1. What did we build?**
A `timing` soft metric (registered). Per note, the responsible fingertip must be in its
contact region BY the onset; **lateness** is the graded defect (a late attack buzzes/mutes),
while early/held is fine. The tolerance is GROUNDED, not arbitrary: perfect within ~30 ms
(perceptual onset-asynchrony JND), decaying to zero one 16th-note late (167 ms at 90 bpm).
Weight held at 0.0 for now, so it reports without perturbing the M5 aggregate.

**2. What did we learn?**
```
naive(defined)   = 1.0
on_time (0ms)    = 1.0
late    (150ms)  = 0.122
very_late(300ms) = 0.0        decay_ms=166.7  perfect_ms=30.0
DEFINED=True  MONOTONIC_PENALTY=True   M6_OK
```
Per-phrase on the naive suite: timing = 1.0 everywhere, and every aggregate score is
byte-for-byte the M5 baseline (1.000/1.000/0.782/0.661/1.000). The metric penalizes a
hand-built late arrival monotonically and is stable.

**3. Assumptions confirmed.**
- Timing IS measurable pre-planner, but only as a **lower bound**: the metric scans whatever
  knots exist and reads arrival from them, so it upgrades automatically when M7 emits dense
  approach dynamics — no metric change needed.
- Grounding the window in perception + tempo (not a magic constant) answers the review's
  "arbitrary tolerance" objection.

**4. Assumption disproved — the M6 risk was real and shaped the design.**
The roadmap flagged "timing may have nothing meaningful to grade until the real planner
exists." Confirmed: naive places every pose exactly at its onset, so onset timing is
**vacuously perfect** (1.0) — there is genuinely nothing to grade yet. Worse, my first cut
folded in a RELEASE/hold term; on naive's sparse one-knot-per-moment trajectory a missing
knot at note-end read as an early release and wrongly dropped naive to 0.75. Fixed by scoring
**onset lateness only** and reporting release informationally until dense trajectories exist.
This is the honest read: timing's real value is latent until M7.

**5. Change before M7?**
No further code change. Two carried notes:
- Timing weight stays 0.0 until M7 produces trajectories with actual approach/arrival
  dynamics; only then is a non-vacuous timing score available to weight (and to fold the
  release term back in). Activating it earlier would score noise.
- When M7 lands, re-pin the baseline with timing active and bump `metric_version`.
Proceed to M7 — the first REAL planner and the first trustworthy naive-vs-phrase eval, the
overarching question.
