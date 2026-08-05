# M7A — Root-Resist Ablation

Separate experiment following M7. **M7 is frozen at B — MIXED and is not modified, amended,
re-run, or superseded by this.** M7A is: M7 → observation (root wander) → hypothesis
(centroid attraction is the cause) → this ablation. Three planners, frozen benchmark
(same 5 phrases, fingerings, references, solver, metrics, weights, thresholds). Naive and
original trajopt re-scored from their committed M7 trajectories (FK only, no re-solve);
resist solved fresh. Reproduces the prior `FINDINGS_ABLATION_RESIST.md` numbers exactly.
Verdict: **MIXED** (partial mechanism isolation). Nothing retuned.

## 1. Question
Is centroid/root attraction responsible for the root-wander regression observed in M7 — i.e.
can that wander be reduced **without destroying** the two genuine advantages the original
trajopt planner demonstrated?

(Distinct from M7's question "does whole-phrase optimization beat memoryless planning?",
which stays answered: MIXED.)

## 2. Evidence

**The one mechanism changed.** Original trajopt's root term
`W_ROOT · Σ‖r_i − r_{i-1}‖²` is translation-invariant, so the rigid chain drifts at zero root
cost toward the per-knot comfort optimum inside a **centroid-centred** bounds box — an
implicit "prefer the geometric centre" attraction. Resist-only anchors absolute position to
the established incumbent `r_est` (naive root at moment 0, where the hand already is):
`W_ROOT·[‖r_0 − r_est‖² + Σ‖r_i − r_{i-1}‖²]`, and drops the centroid from bounds and seed.
Staying put costs 0; any displacement costs > 0. Same weights (W_ROOT=300, W_SMOOTH=1.5),
same maxiter. **Not retuned.**

**Advantage 1 — global rescue of the bad local minimum: PRESERVED and strengthened.**
one_shift_scale note-2 worst contact: naive 3.734 mm → orig trajopt 0.122 mm → resist
**0.034 mm**. Phrase context still pulls note-2 out of the poor basin without the centroid.

**Advantage 2 — shift reduction: PRESERVED.** one_shift_scale shift_count: naive 2 → orig 1 →
resist **1**, feasible (worst 0.034 mm), no shift-refusal. Positional commitment survives
removing the centroid → the commitment win was **not** a centroid artifact.

**Still-hand root travel (the regression under test), mm:**
| phrase | naive | orig trajopt | resist | effect |
|---|---|---|---|---|
| single | 0.0 | 0.0 | 0.0 | n/a |
| repeated_note | 0.0 | 4.7 | 4.6 | **unchanged** — not reduced |
| string_crossing | 0.5 | 9.8 | **2.6** | **−73%** — materially reduced |
| one_shift_scale | 91.6 | 93.5 | 92.0 | slightly reduced (win phrase) |
| sustained_anchor | 0.3 | 9.1 | 10.2 | **worse** — newly flagged position_wander |

**Frozen V1 gate (A) — unchanged, reported as-is:** trajopt `1:P 2:P 3:P 4:F 5:F 6:F 7:F`;
trajopt_resist identical. Resist does not move the M7 gate.

**Probes:** no shift-refusal; every phrase worst-contact ≤ naive or ~equal (max Δ +0.003 mm),
none near the feasibility limit; fingertip travel within +0.4 mm of naive. Only new failure
tag is sustained_anchor `position_wander` — the unfixed regression itself.

## 3. Conclusion
Centroid attraction is **a real cause, not the whole cause** — the mechanism is partially
isolated. Removing it:
- **removed the single-contact centroid drift** on string_crossing (9.8 → 2.6 mm, −73%; 2.1 mm
  residual above naive),
- **preserved positional commitment** (shift 2 → 1) and **preserved/strengthened the local-
  minimum rescue** (0.122 → 0.034 mm) — proving both M7 advantages are independent of the
  centroid term,
- **did not reduce** repeated_note wander (4.7 → 4.6, ~unchanged), and
- **did not fix** sustained_anchor (9.1 → 10.2, slightly worse) — a MULTI-contact phrase whose
  two-contact per-knot comfort gradient in E overrides the resist inertia at W_ROOT=300. A
  distinct cause, not addressed by this single mechanism and not chased by retuning.

So root wander has **at least two sources**: (i) centroid attraction (isolated here, removable
on single-contact phrases) and (ii) per-knot comfort gradient on multi-contact moments
(untouched). Describing resist as "better" is unsupported; describing it precisely: *removed
centroid drift, reduced unnecessary single-contact translation, preserved positional
commitment and the pose rescue, did not solve the multi-contact regression.*

## 4. Implications (architectural, not roadmap)
The experiment establishes that two planner mechanisms are **separable**:
1. **Phrase-level positional commitment / global pose conditioning** — the genuine advantage.
   It persists with the centroid removed, so it is a distinct, real capability of whole-phrase
   optimization, not a side-effect of geometric attraction.
2. **Centroid-induced root wander** — a genuine objective-function mistake, isolable and
   removable on single-contact phrases.

That separation is the result: the good behaviour and (one source of) the bad behaviour are
independent terms in the objective, not entangled. It also shows the remaining multi-contact
wander is a **third, separate** mechanism (comfort gradient), so "root wander" was never a
single phenomenon. What this says about the architecture: whole-phrase optimization's value
(conditioning + commitment) does not require the centroid coupling that caused the most
visible regression — the objective can carry the former without the latter. Whether the
commitment advantage generalizes to a broad phrase win remains unproven (unchanged from M7).
