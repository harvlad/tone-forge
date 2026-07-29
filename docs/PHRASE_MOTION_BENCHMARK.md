# Phrase-Motion Benchmark & Evaluation Methodology

Design/spec only — no planner code, no solver change. Goal: make phrase-level movement
planning as measurable as transcription became. Benchmark FIRST, metrics FIRST,
pre-register success, THEN optimize. The static MPFB solver is FROZEN and is the
authoritative per-pose authority; this document evaluates MOTION over time, layered
above it.

---

## 1. Benchmark suite (34 phrases, each with a reason)

Contacts only (string/fret/finger + note times); NO chord names to the solver. Each
phrase is short (4–16 events) and isolates ONE behaviour so a metric change is
attributable. Grouped by the technique it stresses; count in brackets.

**Scalar runs (4)** — one-position major scale; two-position scale needing exactly one
shift; three-octave run (multiple shifts); chromatic run (finger-per-fret). *Reason:
finger reuse, position stability, shift economy, per-finger independence.*

**Repeated notes (2)** — same string/fret repeated; same note re-attacked across a rest.
*Reason: the finger must STAY DOWN (zero lift); a planner that re-presses each note fails.*

**String crossings (3)** — adjacent-string alternation same fret; skip-string; inside vs
outside picking-hand-agnostic crossings. *Reason: the fretting hand should hold position
and reuse fingers across strings, not re-shape per note.*

**Slides (2)** — same-finger ascending slide; descending slide into a shift. *Reason:
tests the MOVING-contact / same-finger-fret-change primitive and shift-as-slide.*

**Position shifts (3)** — clean shift up; shift down; shift with a beat of rest to prepare
in. *Reason: minimum-shift, wrist-leading, preparation-lead.*

**Open-string transitions (3)** — fretted→open→fretted (the open note frees the hand to
pre-move); open drone under a moving voice; open-string pivot for a big jump. *Reason:
the hand should EXPLOIT the open note to relocate/prepare — strong anticipation signal.*

**Hammer-on opportunities (2)** — ascending pair on one string; three-note ascending run.
*Reason: fingers should prepare/plant above the target before the hammer; tests planting.*

**Pull-off opportunities (2)** — descending pair; descending run. *Reason: the lower
finger must be pre-planted (anchor) before the pull-off; tests simultaneous readiness.*

**Alternating fingers (2)** — index↔middle trill same string; ring↔pinky alternation.
*Reason: differentiated finger motion, ring/pinky dependence, no whole-hand wobble.*

**Wide stretches (2)** — 1-fret-per-finger over 5 frets (index-pinky span); a stretch that
should trigger a micro-shift instead. *Reason: reach vs relocate trade; grotesque-stretch
avoidance.*

**Sustained anchor notes (2)** — hold a bass note while a melody moves above; pedal tone.
*Reason: anchor retention — a finger stays planted for many events while others move.*

**Guide-finger opportunities (2)** — shift where one finger can stay lightly on its string
through the move; ascending sequence sharing a guide finger. *Reason: guide-finger rate.*

**Barre preparation (2)** — approach to a barre from an open shape; barre release into an
open shape. *Reason: the index should flatten/prepare BEFORE the barre beat, and lift
cleanly after; tests preparation of a whole-finger contact. (Barre pose itself remains a
known solver limitation — evaluated for PREPARATION timing, not barre contact quality.)*

**Chord-to-melody transitions (3)** — strummed chord → single-note melody (fingers peel
off in order); melody → chord (fingers gather/prepare); arpeggiated chord where fingers
plant progressively. *Reason: realistic song context; finger reuse + progressive
planting/peeling.*

Total 34 (inside the 20–50 target; trim/extend per category as data accrues).

## 2. "Expert playing", quantified per phrase

Each benchmark ships a REFERENCE EXPECTATION file (not a single pose sequence — see the
critique in §9 — but measurable expectations + acceptable ranges):

- **min_practical_shifts**: integer, hand-labelled (the fewest position shifts a competent
  player uses). e.g. one-position scale = 0; two-position scale = 1.
- **required_finger_reuse**: which fingers should serve ≥N consecutive events (e.g.
  repeated-note: the finger stays for all repeats).
- **guide_finger_events**: shifts where a named finger should remain in contact through
  the move.
- **anchor_holds**: (finger, event-range) a finger must stay planted (sustained-note
  fixtures).
- **max_unnecessary_lifts**: a finger needed again within K events should not lift
  (repeated/anchor fixtures → 0).
- **preparation**: for named notes, the serving finger should be within X mm of its target
  ≥ Δ ms before onset.
- **wrist/position_stability**: bounded root travel; position changes only at labelled shift
  points.
- **economy band**: total fingertip + root travel within [expert_min, expert_max]
  (hand-estimated range, not a point).

These are authored per phrase, reviewed, and version-pinned — the movement analogue of the
curated transcription references.

## 3. Objective metrics

**HARD constraints (never violate — a phrase FAILS if any breach):**
- per-event contact feasibility (tip in fret region, ≤ contact tol; render==solver as today)
- joint ROM within physiological limits
- no board / finger-finger penetration
- no faster-than-humanly-possible motion (per-joint velocity/accel ceiling)
- causal/temporal validity (a contact exists exactly on its event window)
- impossible schedule → correctly INFEASIBLE

**SOFT metrics (what we optimize; report each separately):**
- SHIFT_COUNT (root position changes) vs min_practical_shifts
- FINGERTIP_TRAVEL (Σ tip path length, mm)
- WRIST/ROOT_TRAVEL (mm)
- FINGER_REUSE_RATE (fraction of events where the serving finger was already down)
- GUIDE_FINGER_RATE (labelled shift opportunities taken)
- ANCHOR_RETENTION (% of required anchor spans actually held)
- UNNECESSARY_LIFT_COUNT (lifts of a soon-needed finger)
- PREPARATION_LEAD (avg ms/mm a finger is ready before its note)
- AVERAGE_ANTICIPATION (how early the hand starts relocating for a shift)
- HAND_STABILITY (variance of root pose between shift points; lower better)
- MOVEMENT_SMOOTHNESS (SPARC + log-dimensionless-jerk on tip & root paths — §4)
- TOTAL_BIOMECHANICAL_EFFORT (Σ strain + ∫‖q̇‖² + ∫‖q̈‖²)

## 4. Reuse existing motion metrics (literature)

Do NOT invent smoothness math — motor-control has validated, dimensionless, scale-
invariant metrics:
- **SPARC (Spectral Arc Length)** and **LDLJ (Log Dimensionless Jerk)** — the two metrics
  with excellent reliability (ICC>0.9, CoV<10%) for movement smoothness; both dimensionless
  and noise-robust. Use SPARC on speed profiles + LDLJ on tip/root trajectories.
- **Minimum-jerk model (Flash & Hogan 1985)** — human point-to-point reaches minimize
  integrated jerk; our smoothness objective + metric should reference it.
- **Fitts's Law** — reach difficulty ∝ log2(distance/target-width); use to normalize
  "expected" travel/time for shifts so economy is judged against reach difficulty, not
  absolute mm.
- Guitar pedagogy supplies the guitar-specific expectations (economy of motion, guide
  fingers, anchoring, plan-ahead) — quantified in §2, no established numeric standard, so
  hand-labelled.

Sources: [SPARC/LDLJ smoothness comparison](https://www.ncbi.nlm.nih.gov/pmc/articles/PMC9919347/) ·
[LDLJ reliability](https://link.springer.com/article/10.1186/s12984-024-01382-1) ·
[smoothness from IMUs (SPARC)](https://www.frontiersin.org/journals/bioengineering-and-biotechnology/articles/10.3389/fbioe.2020.558771/full).

## 5. Scoring system — the "Movement Score" (transcription-F1 analogue)

One comparable number per planner version, from normalized sub-scores:

```
MovementScore = 100 · Π (gate passes)                       # hard gates are multiplicative
              × Σ wᵢ · sᵢ                                    # weighted soft sub-scores
```
- Hard gates are a MULTIPLIER in {0,1}: any hard violation on a phrase zeroes that phrase
  (you cannot buy realism with an infeasible pose — mirrors "wrong note = wrong").
- Each soft metric → sᵢ ∈ [0,1] by normalizing against its reference band (1.0 at or
  better than expert-min, 0 at a defined-bad bound; Fitts-normalized where travel-based).
- Weights wᵢ pre-registered (proposed: shift-economy 0.20, finger-reuse 0.15, anchor 0.10,
  unnecessary-lift 0.10, preparation/anticipation 0.15, smoothness 0.10, stability 0.08,
  travel-economy 0.07, effort 0.05). Sum = 1.
- Report the aggregate AND every sᵢ — the aggregate is for tracking versions, the
  breakdown is for diagnosis. Never collapse to only the aggregate (Goodhart guard, §9).

## 6. Validation data — options evaluated

| Option | Pros | Cons | Verdict |
|---|---|---|---|
| **Hand-labelled by us** | full control, cheap, matches our contact/finger schema, versionable | our own bias; "expert" is our judgment | **Primary.** Same path transcription used (curated refs). Small, vetted, reviewed. |
| Captured from expert guitarists (mocap / MediaPipe offline) | real ground truth, external validity | rig-mismatch (their hand ≠ MPFB), licensing/consent, retarget error, expensive | **Secondary, later.** A few captures via the already-scoped MediaPipe-offline path for EXTERNAL cross-check of the hand-labelled set — validation, not primary. |
| Synthesized (from our own solver) | free, unlimited | circular — grades the planner against itself | **Reject** as ground truth (fine as unit-test scaffolding only). |
| Etudes (Giuliani/Carcassi etc.) | pedagogically canonical, fingering often published | copyright on some editions; still need motion labels | **Augment.** Use public-domain etude fragments as realistic phrase sources; we add the motion expectations. |
| GuitarPro / tab corpora | huge, real songs, includes fingering/position hints sometimes | licensing; fingering quality varies; no motion ground truth | **Augment, cautiously.** Public/owned tabs as phrase SOURCES; never as motion ground truth. |

Recommendation: hand-labelled primary set (34 phrases) + public-domain etude fragments as
realistic sources + a small later MediaPipe-captured external-validation set. Never grade
the planner against solver-generated "truth".

## 7. Regression harness (transcription-report analogue)

Every planner change auto-produces:
- **Overall Movement Score** + delta vs the pinned baseline.
- **Per-metric table** (each sᵢ, this version vs baseline, ↑/↓).
- **Regression flags**: any phrase whose score dropped > ε, any hard-gate that flipped to
  fail, any metric regressing beyond tolerance.
- **Phrase-by-phrase breakdown** (score + metrics + which expectations met/missed).
- **Visual reports**: per phrase, the discrete-pose contact sheet (as built for the 8-note
  phrase) + trajectory plots (tip/root path, velocity, jerk) + a red/green expectation
  checklist. Same style as the separator/transcription reports.
- Machine-readable `movement_report.json` for trend tracking across versions.

Pinned baseline = the naive independent-pose planner (today's per-note solve with the weak
move term) so every improvement is measured against the memoryless status quo.

## 8. Pre-registered success — Planner V1 PASS

Set BEFORE any planner exists. V1 passes only if ALL hold vs the naive baseline on the
34-phrase suite:
1. **Contact fidelity non-regression**: every phrase's per-event contact stays ≤ current
   tol; render==solver invariant intact. (Hard.)
2. **No hard-gate regressions**: no phrase that was feasible becomes infeasible; impossible
   phrases still INFEASIBLE. (Hard.)
3. **Shift economy**: mean SHIFT_COUNT ≤ 1.1 × min_practical_shifts across the suite (naive
   baseline expected ≫ that).
4. **Finger reuse**: FINGER_REUSE_RATE ≥ +25% absolute over baseline on the
   repeated-note / string-crossing / scale fixtures.
5. **Unnecessary movement**: FINGERTIP_TRAVEL and ROOT_TRAVEL each reduced ≥ 20% vs
   baseline, suite-mean.
6. **Anchor**: ANCHOR_RETENTION ≥ 90% on sustained-note fixtures (baseline ~0).
7. **Aggregate**: MovementScore ≥ baseline + a pre-set margin (propose +15 points).
8. **No visual regression**: human review of the contact sheets shows no phrase that got
   quantitatively better but visually worse (Goodhart guard).
Any hard failure (1–2) = V1 fails regardless of soft gains.

## 9. Critique of this methodology (honest)

- **"Expert playing" is not unique.** Many valid fingerings/motions exist for a phrase.
  Scoring against a single hand-labelled solution would wrongly penalize legitimate
  alternatives. Mitigation (baked into §2/§5): grade against RANGES and a SET of acceptable
  expectations, not one exact trajectory; where multiple fingerings are standard, label all
  and score the best match. This is the biggest risk and the main reason not to use a
  single mocap take as truth.
- **Goodhart / metric-gaming.** A weighted score can be optimized into unnatural motion
  that scores well. Guards: keep the human visual gate as a non-gameable PASS requirement
  (§8.8); report all sub-metrics, never just the aggregate; keep hard gates multiplicative.
- **Ground-truth bootstrapping.** min_practical_shifts etc. are our judgments; risk of
  anchoring the planner to our bias. Mitigation: the later MediaPipe external-validation set
  checks our labels against real players; treat labels as revisable, version-pinned.
- **Circularity if we ever grade against solver output** — explicitly rejected (§6).
- **Fingering-planner coupling.** Motion quality depends on the fingering CHOSEN upstream.
  This benchmark must FIX the fingering per phrase (hand-labelled) so it isolates MOTION;
  otherwise a bad motion score could be an upstream fingering fault. Stated as a
  benchmark invariant: fingering is an input, not a variable, in this suite.
- **Smoothness ≠ correctness.** SPARC/LDLJ reward smooth motion but a phrase can be smooth
  and wrong; that's why smoothness is one soft term among many and gated by contact
  fidelity.
- **Stronger alternative considered:** a purely learned/data-driven evaluator (train a
  discriminator on real vs synthetic motion). Rejected for now: needs a large licensed
  capture corpus, is opaque (hard to attribute regressions), and risks non-commercial data.
  The hand-labelled + metric approach is more transparent and matches how transcription
  succeeded here. Revisit if/when a clean capture corpus exists.

## 10. Recommendation

Build this benchmark + harness FIRST (hand-labelled 34-phrase suite, the metric set with
SPARC/LDLJ reused, the Movement Score, the regression report, the pinned naive baseline),
pre-register the V1 thresholds above, THEN write the trajectory planner against it —
exactly the discipline that made transcription measurable. Fingering is fixed per phrase;
the static solver stays frozen as the per-pose authority. No planner code until the
benchmark exists and you approve the thresholds.
