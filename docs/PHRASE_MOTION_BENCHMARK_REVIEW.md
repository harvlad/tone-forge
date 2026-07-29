# Adversarial Review — Phrase-Motion Benchmark v2

Independent review. I did not write this benchmark; my job is to reject it if it is
scientifically weak. No code, no implementation — critique only. Verdict at the end.

---

## 1. Attacking the benchmark

**Where it can be gamed.** The aggregate Movement Score is a weighted sum gated by a
multiplicative hard tier. Weighted sums are the classic gaming surface: a planner trades a
cheap-to-win metric for a hard-to-win one and nets positive. The hard gate stops
*infeasible* gaming but not *stylistic* gaming — everything in Tier 2/3 is buyable against
everything else. The A↔B gap report helps, but it is descriptive, not gated: nothing
FAILS a planner for sitting at Reference A.

**Goodhart survivors.**
- *Idle Stability* rewards stillness; a planner minimizes motion → dead, robotic hand that
  scores high. The "scoped to root + planted fingers" fix reduces but does not remove this:
  a hand can be root-still and planted-still and STILL look embalmed because free-finger
  liveness is unmetricized (it's an unmeasured good, so it will be optimized away).
- *Positional Commitment* rewards not-moving; combined with Idle Stability, the benchmark
  has TWO metrics that both pay for less motion and ZERO metrics that pay for necessary
  liveliness. Net bias: toward a frozen hand. That is a structural imbalance.
- *Commitment Confidence* rewards not re-lifting; a planner that never lifts (even when a
  human would re-articulate a repeated note by lifting) scores well but looks legato-locked.
- *Planning Horizon* credits early motion; capped, but the cap is a magic number — pick it
  wrong and you reward either twitchy-early or reactive-late.

**Metrics that improve while believability drops:** any of the four "less motion" metrics
above, pushed to the limit, improve while the animation stiffens. Smoothness (SPARC/LDLJ)
can improve by making EVERYTHING slow and gliding — smooth but lifeless, and a real
fast run is NOT maximally smooth.

**Missing metrics (real gaps):**
- **Timing/rhythmic accuracy** — nothing checks the hand ARRIVES on time. A planner can
  hit every contact position but reach it early/late relative to the note onset and the
  benchmark barely cares (preparation lead is about earliness, not on-beat arrival). For
  MUSIC this is the biggest omission.
- **Contact RELEASE timing** — when a note ends, the finger should lift appropriately;
  unmeasured.
- **Biomechanical plausibility of VELOCITY** — the hard gate caps velocity, but nothing
  rewards human velocity PROFILES (bell-shaped, Fitts-consistent); a constant-velocity
  glide passes.
- **Collision-with-strings mid-flight** — hard gate checks penetration at contact, but a
  finger sweeping through an open ringing string between events is unmeasured.
- **Left-right-hand coordination** — deferred, but its absence means "believable" is only
  half-judged.

## 2. Hidden coupling / dependency map

The four Tier-3 additions are NOT independent:

```
                 less-motion axis (shared latent)
        ┌───────────────┼───────────────┬──────────────┐
 Idle Stability   Positional Commit   Commitment Conf   (Planning Horizon, inverse)
        │               │                   │
        └── all decrease as total motion decreases ──┘
```

- **Positional Commitment ⟷ Planning Horizon**: partially anti-correlated / co-measuring.
  Committing to a position AND repositioning early are both "few, well-timed moves." A
  planner with one usually has the other; they are two views of the same shift-scheduling
  behaviour. ~50% shared information.
- **Idle Stability ⟷ Commitment Confidence**: heavy overlap. A still hand doesn't re-lift
  fingers; a decisive hand doesn't drift. Both penalize small unnecessary movements — one
  at the wrist/planted-finger, one at the fingertip. They differ only in WHICH body part
  fidgets. Likely r>0.7 in practice.
- **Movement Score double-counts** the less-motion latent: travel-economy (Tier 2), Idle
  Stability, Positional Commitment, and Commitment Confidence all load on "move less." Four
  weighted terms, one underlying factor → the aggregate over-weights stillness ~3–4×
  without anyone intending it.
- **Anchor retention ⟷ Commitment Confidence** also overlap (both about keeping a finger
  down).

Recommendation: run a PCA / correlation study on the metric vectors across the suite BEFORE
fixing weights, and orthogonalize — the current weights assume independence that does not
exist.

## 3. Ten adversarial planners that score well and look wrong

1. **The Statue.** Minimize all motion; move only when a contact hard-constraint forces it,
   as late as allowed. Wins Idle Stability, Positional Commitment, Commitment Confidence,
   travel-economy. Looks dead. *Exploits the less-motion cluster.* *Fix:* a liveness floor
   + human velocity-profile reward + timing gate.
2. **The Glider.** Move everything at constant low velocity between events. Wins SPARC/LDLJ
   (maximally smooth). Looks underwater; real runs aren't smooth. *Exploits smoothness
   metrics' bias toward slow.* *Fix:* score smoothness only within human speed bands; reward
   bell-shaped profiles.
3. **The Economist.** Sit exactly on Reference A everywhere. Wins Tier 2 outright. Flagged
   by A↔B gap but NOT gated → no failure. Looks mechanically correct, musically robotic.
   *Fix:* gate on minimum musicianship, or penalize A-collapse where B diverges.
4. **The Early Bird.** Reposition at the maximum allowed lead every time. Wins Planning
   Horizon. Looks like the hand is racing ahead of the music. *Exploits the horizon cap
   ceiling.* *Fix:* reward lead that MATCHES the human range, not maximizes it (target, not
   maximize).
5. **The Never-Lifter.** Keep every finger down forever. Wins Commitment Confidence + anchor
   retention. Fails real re-articulation; a repeated note needs a lift. *Fix:* require
   lifts where the music demands them (release-timing metric).
6. **The Label Overfitter.** Memorize the hand-labelled Reference B trajectories (the suite
   is small, ~34). Wins everything by replay, generalizes to nothing. *Exploits a tiny fixed
   suite.* *Fix:* held-out phrases, procedurally-varied transpositions, no scoring on
   seen-exact contacts.
7. **The Shift-Refuser.** Never shift (max Positional Commitment) even when B shifts,
   stretching fingers instead. Wins commitment; ROM gate MIGHT catch it, but sub-limit
   over-stretch passes. *Fix:* strain ceiling + divergence-from-B shift schedule penalty.
8. **The Jerk-Hider.** Concentrate all motion into one knot the metric samples between (alias
   the discretization). Wins smoothness by hiding jerk between samples. *Exploits knot
   sampling.* *Fix:* evaluate metrics on a dense resample, not solver knots.
9. **The Contact-Grazer.** Put the tip at the region EDGE (barely legal) to minimize travel
   to the next note. Wins travel; looks like it's not actually pressing. *Exploits the
   region tolerance.* *Fix:* reward central/behind-fret contact, not edge-legal.
10. **The Thumb-Ignorer.** Park the thumb in one fixed spot regardless of finger span (thumb
    barely scored beyond behind-neck). Wins by not spending effort on thumb. Looks
    disconnected from the hand's work. *Fix:* score thumb opposition tracking the span.
11. *(bonus)* **The Demo-Tuner.** Optimize appearance on the single demonstration phrase
    (it's rendered every report and humans watch it). *Fix:* the demo must never be in any
    tuning/training loop; rotate a hidden demo periodically.

The recurring theme: **the benchmark pays for LESS almost everywhere and for MATCHING-A on
economy, with no metric that pays for the right AMOUNT of the right motion at the right
TIME.** That is the central weakness.

## 4. Attacking the A/B reference model

A single Human-optimum trajectory B is the weakest scientific choice in the spec.
- **B is not a point.** Expert playing is multi-modal — several standard fingerings/motions,
  each valid. A point reference (even with "ranges") secretly privileges one and penalizes
  legitimate others. The v2 "sets/ranges" hedge is under-specified.
- **Better formulations, ranked:**
  1. **B as a distribution / acceptance region** — score by likelihood/containment within a
     set of expert-acceptable trajectories, not distance to one. Requires several labels per
     phrase (or captured takes) but is the scientifically honest form.
  2. **Constraint ranges instead of trajectories** — specify B behaviourally ("≤1 shift, guide
     finger on the D string through it, index anchored events 3–6") and score satisfaction,
     not trajectory distance. More robust to the non-uniqueness problem; harder to make
     fully quantitative.
  3. **Style-conditioned references** — B = B(style); see §5.
  A is fine as a computed lower bound; keep it, but stop pretending B is singular. **Adopt
  (1)+(2): acceptance regions defined by behavioural constraints.** This is the single most
  important change.

## 5. Style

Movement differs by style more than the spec admits:
- **Classical**: minimal motion, planted fingers, guide fingers, low wrist, near-zero idle
  motion. Closest to the current metrics' bias.
- **Metal (legato/shred)**: economy AND speed; fingers hover very close, hammer/pull heavy,
  large fast position shifts — HIGH motion that is still "expert." The current less-motion
  bias would score a great shred hand as *worse* than a lazy one.
- **Blues**: expressive bends/vibrato (unmetricized), fewer fingers, lots of one-finger
  work, deliberate "inefficient" repositioning for feel.
- **Jazz**: complex chords, rolls, thumb-over, frequent position changes for voicings.
- **Country (chicken pickin'/hybrid)**: fast string skipping, open strings, bends.
- **Fusion**: hybrid of jazz + shred.

**Style-invariant** (should NEVER become style-dependent): Tier 1 hard correctness (contacts,
ROM, collisions, feasibility), and basic biomechanical plausibility. **Style-dependent**:
Positional Commitment, Planning Horizon, Idle Stability, shift economy, smoothness bands,
and Reference B itself — a metal player commits less to position and moves more, legitimately.
**Verdict:** the four new Tier-3 metrics are implicitly encoding a CLASSICAL aesthetic.
Unlabelled, the benchmark will certify "classical-looking" as "expert" and penalize other
valid idioms. Reference B and the Tier-3 targets must be style-conditioned before this is
"canonical," or it is canonical only for classical.

## 6. Generalization to other instruments

- **Bass**: works — same string/fret/finger contract, wider spacing (geometry param), more
  one-finger-per-fret + position shifts. Metrics transfer; economy bias fits.
- **Ukulele / mandolin / banjo**: works structurally (fewer/more strings, N-string param),
  but mandolin/banjo are played fast with idioms (rolls, tremolo) the less-motion bias would
  misjudge — same style problem as metal.
- **7/8-string**: works IF string count is truly parameterized (the spec flags this; verify
  no 6 is hard-coded in geometry/collision).
- **Fretless**: **breaks the core contract.** The benchmark keys contacts on (string, FRET).
  Fretless has no frets — contact is a continuous position with intonation tolerance, and
  the whole "fret region" primitive collapses. Fretless would need a re-formulation to
  continuous-pitch contact with an intonation-error metric. Document as out-of-scope; do not
  claim generality the fret contract can't support.
Net: general across FRETTED string instruments with a string-count + spacing param and
style-conditioned Tier 3; NOT general to fretless without a contact re-model.

## 7. Scientific rigour vs the fields

What robotics / trajopt / motor-control / animation / RL do that this spec does NOT:
- **Uncertainty & significance.** None of our metrics carry confidence intervals or
  significance tests. Robotics benchmarks report mean±std over seeds/trials; RL reports over
  many seeds with significance (the reproducibility literature is explicit that single-run
  claims are unsound). Our solver is deterministic per phrase, so variance must come from
  MULTIPLE PHRASES and MULTIPLE ACCEPTABLE REFERENCES — report score distributions with CIs
  across the suite, not point numbers.
- **Inter-rater agreement.** The Human-optimum labels and the mandatory "visual gate" are
  subjective. Motor-control/animation user studies report inter-rater reliability (ICC,
  Krippendorff's α). We need ≥2–3 independent guitarist labellers per phrase and a reported
  agreement statistic; a single labeller (us) is a fatal validity hole for a "canonical"
  benchmark.
- **Human-study protocol for believability.** Animation papers validate "looks real" with a
  formal perceptual study (2AFC / MOS, N raters, power analysis). Our "human visual gate" is
  ad hoc. To be canonical it needs a defined 2AFC protocol (planner vs baseline vs human
  capture), N, and significance.
- **Held-out / cross-validation.** RL/ML separate train/test. Our 34 phrases are all
  "test." A planner tuned on them is overfit; need held-out phrases and transposition
  augmentation.
- **Ablations.** Standard in all these fields: report each metric/term's marginal
  contribution. Spec doesn't require ablations.
**Verdict:** the metric ENGINEERING is solid; the STATISTICAL methodology is absent. For
internal iteration that's tolerable; for "canonical / publication," it is the biggest gap
after the single-reference problem.

## 8. Longitudinal validity (5-year view)

Most fragile assumptions, likeliest to date first:
1. **Single/classical-biased Human reference** — will look parochial as soon as styles are
   taken seriously (year 1–2).
2. **The less-motion aesthetic baked into Tier 3** — as learned motion models improve,
   "believable" will be defined by data, and hand-crafted stillness metrics will look naive.
3. **Fret/string contact contract** — fine for guitars forever, but blocks fretless/
   continuous-pitch and any microtonal/expressive extension.
4. **Static per-pose solver as the authority** — if a physics/dynamics or learned motion
   model supersedes it, the benchmark's "render==solver" invariant (a current strength)
   becomes a constraint tying the benchmark to one solver generation.
5. **Hand-labelled ground truth** — will be superseded by captured corpora; labels will be
   seen as the weak link.
Robust parts likely to survive: Tier 1 hard correctness; SPARC/LDLJ (field standards);
Fitts normalization; the three-tier separation principle; the failure taxonomy.

## 9. Simplicity — cut 30%, keep 95% of value

Least unique information (candidates to cut/merge):
- **Merge Idle Stability + Commitment Confidence → one "Quiet Hand" metric** (both measure
  unnecessary micro-motion; ~0.7 correlated). Saves one metric, loses ~nothing.
- **Merge Positional Commitment into shift-economy** (both about shift scheduling vs
  Reference B) — or keep Commitment and drop the separate shift-count, they co-measure.
- **Drop "Average Anticipation"** as separate from Planning Horizon (same signal).
- **Drop Hand Stability variance** if Idle Stability is kept (overlap).
- **Collapse the 34 phrases toward ~20** — several categories (3 string-crossings, 3
  shifts, 3 chord→melody) are over-sampled; 1–2 each covers the behaviour. The demonstration
  phrase + ~20 scored phrases retains coverage.
What NOT to cut: Tier 1 hard gates, SPARC/LDLJ, the A↔B gap, one metric each for
{commitment, preparation, quiet-hand, guide-finger, anchor}, the failure taxonomy. That's
~70% of the current apparatus for ~95% of the value.

## 10. Verdict

**APPROVE WITH MAJOR REVISIONS.** Not reject — the architecture (three tiers, hard-gate
multiplier, A/B split, failure taxonomy, reuse of validated smoothness metrics,
render==solver correctness invariant) is sound and above typical internal-benchmark
quality. But it is NOT yet "canonical/publishable," for four blocking reasons:

1. **Single Human reference.** Must become an acceptance REGION / behavioural-constraint set
   (multi-modal), or it certifies one arbitrary fingering as truth. (§4 — the top fix.)
2. **Less-motion / classical bias with metric coupling.** Four Tier-3 metrics load on one
   "move less" factor and encode a classical aesthetic; the aggregate over-weights stillness
   and would score a valid metal/blues hand as worse. Orthogonalize the metrics (PCA study),
   add a liveness/velocity-profile counter-term, and style-condition Tier 3. (§2, §3, §5.)
3. **No statistical methodology.** No inter-rater agreement on labels, no CIs, no perceptual
   study protocol, no held-out set. A canonical benchmark cannot rest on one labeller and an
   ad-hoc visual check. (§7.)
4. **The central missing axis: TIMING.** Nothing scores on-beat arrival, release, or human
   velocity profiles — for a MUSIC-motion benchmark this is a first-order omission. (§1.)

Do these four now, before any planner, and adopt the §9 simplifications so the coupling
shrinks. Then it is defensible as canonical for FRETTED, STYLE-CONDITIONED guitar motion —
explicitly not fretless, not (yet) right-hand, not expressive tone. Approve on those
revisions; reject the claim of generality or canonical status without them.
