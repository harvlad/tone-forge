# Phrase-Motion Benchmark — v2 (canonical evaluation framework)

Supersedes `PHRASE_MOTION_BENCHMARK.md` where noted. Design/spec only — no planner code,
no solver change, no harness code yet. Goal is no longer "is V2 > V1"; it is **the
definitive evaluation suite for believable phrase-level guitar motion**, the movement
analogue of our transcription benchmark. The 34-phrase suite, hard/soft metric split,
SPARC/LDLJ reuse, Movement Score, validation-data strategy, and pre-registered V1 gates
from v1 all stand. This revision adds the twelve architectural changes below.

---

## 1. Two references: MECHANICAL optimum vs HUMAN optimum

Every phrase carries TWO reference solutions, not one "expert" conflation.

- **Reference A — Mechanical optimum.** The objectively minimal-cost motion satisfying all
  hard constraints (min travel + jerk + effort). Computable/derivable; a LOWER BOUND on
  pure economy. The planner should never be *worse* than this on economy metrics without a
  musical reason.
- **Reference B — Human optimum.** A musically-believable motion an experienced player
  would choose — which deliberately departs from A: staying in position rather than chasing
  minimum distance, familiar fingerings, preparing for later notes, favouring tone/phrase
  stability over local optimality. Hand-labelled (with ranges/sets, per v1's non-uniqueness
  guard).

The planner TARGETS Reference B. Reference A is the sanity floor. **The divergence A↔B is
itself a first-class signal:** phrases where they differ are the most valuable tests —
they measure musicianship, not just optimization. Report per phrase: score vs B, and the
A↔B gap (how much the human sacrifices economy for musicality). A planner that collapses
onto A everywhere is mechanically optimal and musically wrong.

## 2. Fixed fingering is an intentional V1 isolation (documented)

V1 fixes the fingering per phrase ON PURPOSE — to isolate MOTION from fingering choice, so
a motion regression can't be an upstream fingering fault. This is a stated benchmark
invariant for V1, NOT the long-term architecture. Long-term:

```
Song → candidate fingerings → phrase planner → Movement Score → best solution
```

The suite must stay compatible: each phrase's contact schema already carries
(string, fret, finger, time); the fingering field becomes a VARIABLE later (multiple
candidate fingerings per phrase, each scored, best chosen), without changing the metric
definitions or the harness. Documented as forward-compatible; nothing in v1 hard-codes
"one fingering."

## 3. NEW metric — Positional Commitment (Tier 3)

Measures unnecessary oscillation between equivalent hand positions. Experienced players
COMMIT: they stay, or shift once, rather than bouncing 5th→4th→5th→4th.
- Definition: over the phrase, detect position "reversals" — the hand returns to a fret
  position it just left within a window of N events when a committed alternative existed.
  `PositionalCommitment = 1 − reversal_shifts / max(1, total_position_changes)`, further
  penalized for A→B→A churn cycles.
- Guarded so it does NOT reward refusing genuinely-necessary shifts (fast runs need to
  move): normalized against Reference B's shift schedule — only shifts BEYOND B's, or that
  reverse, count against commitment. (Better name candidates: "Position Churn" inverse, or
  "Committal". Keeping *Positional Commitment*.)

## 4. NEW metric — Planning Horizon (Tier 3)

Measures demonstrated look-ahead: does the hand reposition EARLY when that yields a
smoother whole-phrase motion (e.g. an ascending 5→7→9→12 where the hand should start
travelling before the last note)?
- Definition: for each shift, `lead = onset_time(triggering_note) − time_root_motion_begins`.
  Credit lead that provably lowers peak velocity/jerk over the reactive (move-at-the-last-
  moment) baseline. `PlanningHorizon = normalized(mean effective lead, capped)`.
- Capped at a human-plausible range so it does NOT reward moving *absurdly* early
  (anticipation, not teleporting ahead of the music).

## 5. NEW metric — Idle Stability (Tier 3)

Professionals are remarkably still when nothing must move.
- Definition: over "idle" spans (consecutive events requiring NO re-fingering / no shift),
  measure ROOT and COMMITTED-finger travel per unit time; lower is better.
  `IdleStability = 1 − normalized(idle_root_travel + idle_committed_finger_travel)`.
- Explicitly scoped to ROOT + committed fingers, NOT free-finger micro-liveness — an idle
  hand should be still, not frozen-dead (see the critique, §12: freezing risk). Free
  fingers staying "alive/ready" is allowed; the wrist and planted fingers should not drift.

## 6. NEW metric — Commitment Confidence (Tier 3)

Distinct from anchor retention. Anchor retention = "did a required-planted finger stay
down for its span." Commitment Confidence = "is a correctly-placed finger DECISIVE, or
does it fidget (lift-and-replace) while still needed."
- Definition: count spurious re-lift/replace events on a finger that is correctly placed
  and still required. `CommitmentConfidence = 1 − relifts / opportunities`.
- Captures the "finger flapping" tell that reads as amateur/robotic even when contacts are
  technically met.

## 7. Demonstration phrase (visual regression, NOT scored)

One iconic, unambiguously public-domain phrase every planner version auto-renders, so
progress is instantly visible to a human. Famous riffs are copyrighted → use classical
guitar public domain. **Recommendation: the opening phrase of J.S. Bach's Bourrée in E
minor (BWV 996)** — universally recognized among guitarists, Bach is unquestionably public
domain, and it exercises string crossings + position work + a guide-finger moment. Backup:
a Carcassi Op.60 or Sor study fragment (also PD). This phrase is EXCLUDED from the scored
suite; its sole job is a fixed side-by-side visual trend across every report. Same phrase,
same camera, same render, forever.

## 8. Three-tier report (never merged into one opaque number)

Every planner report separates:
- **Tier 1 — Hard correctness** (pass/fail, multiplicative): contacts, ROM, collisions,
  feasibility, causal validity, impossible→INFEASIBLE. Any breach zeroes the phrase.
- **Tier 2 — Mechanical efficiency**: fingertip/root travel, jerk (SPARC/LDLJ), effort,
  shift count, preparation. Compared to Reference A (the floor).
- **Tier 3 — Human musicianship**: Positional Commitment, guide-finger rate, Planning
  Horizon, Idle Stability, Commitment Confidence, anchor retention, phrase continuity.
  Compared to Reference B (the target).

Movement Score stays as a single tracking number (hard-gate multiplier × weighted
sub-scores), but **every subscore and every tier is always shown**. Proposed tier
weighting inside the aggregate: Tier 1 gate (×{0,1}), Tier 2 0.4, Tier 3 0.6 — musicianship
weighted above raw efficiency, because the whole point is Reference B, not A.

## 9. Failure-mode taxonomy (separator-style)

Every benchmark failure classifies into ≥1 category, reported per phrase:
`CONTACT_FAILURE`, `ROM_FAILURE`, `COLLISION`, `INFEASIBLE_MISS` (should-be-impossible
solved, or should-be-possible rejected) — Tier 1;
`EXCESS_TRAVEL`, `EXCESS_WRIST_TRAVEL`, `UNNECESSARY_SHIFT`, `LATE_PREPARATION`,
`NON_SMOOTH` (poor SPARC/LDLJ) — Tier 2;
`OSCILLATION` / `POSITION_CHURN`, `ANCHOR_BREAK`, `GUIDE_FINGER_LOSS`, `FINGER_FLAPPING`
(low commitment confidence), `HAND_INSTABILITY` (idle drift), `MECHANICAL_LOOKING`
(collapsed onto Reference A where B diverges) — Tier 3.
Each metric maps to the category it triggers, so a score drop names its cause.

## 10. "Musicianship" defined (behavioral realism, not artistry)

For this system, musicianship is NOT expression — it is the set of behaviours that make a
viewer, watching the hand with no audio and no labels, immediately think *"that's a
guitarist."* Concretely, the hand:
- **commits** to a position and stays there until a real reason to move (not per-note
  chasing);
- is **still when idle** — the wrist and planted fingers don't drift;
- **prepares** — fingers arrive near their next contact before the note, and the hand
  begins a shift early when the phrase demands it;
- **reuses and guides** — keeps fingers down when useful, keeps a guide finger through a
  shift;
- **moves the whole hand deliberately** — wrist leads, one economical shift rather than
  many twitches;
- keeps **unused fingers alive but quiet** — curled, ready, not flapping and not frozen;
- **is decisive** — a correctly placed finger stays confidently placed.
The recognizable "guitarist" read is the SUM of economy + commitment + anticipation +
stillness + decisiveness over TIME — not the shape of any single pose. That is why this
benchmark measures motion, and why Tier 3 outweighs Tier 2.

## 11. Future-compatibility review (do today's assumptions become obstacles?)

- **Slides / bends / vibrato**: these are TIME-VARYING contacts (a fret moving along a
  string; a string displaced across frets; oscillation). The current one-static-contact-
  per-event schema would block them. **Recommendation now:** define the contact schema to
  permit a contact TRAJECTORY (target as a function of time over the event window) even
  though V1 uses constant points — so the schema doesn't need breaking later. The
  MOVING/SURFACE primitive stubs already anticipate this.
- **Alternate tunings / capo**: the benchmark keys contacts on (string, fret), NOT pitch,
  so tuning is already agnostic; capo = a fret offset on the geometry. No obstacle. (If we
  ever key on pitch, this breaks — keep string/fret as the contract.)
- **7/8-string guitars**: do NOT hard-cap string index at 6 anywhere; string count is a
  neck-geometry parameter. Flag: current neck geometry assumes 6; make N a parameter.
- **Right-hand animation**: out of scope (this is the fretting hand). Document as a SEPARATE
  future benchmark; do not entangle metrics.
- **Tone articulation / musical intent**: a future Tier 4 (expressive), explicitly out of
  scope now; the tier structure leaves room without disturbing Tiers 1–3.
Net: the two obstacles to fix at the SPEC level now (cheap) are (a) allow contact
trajectories, (b) parameterize string count — both are documentation/schema, not code.

## 12. Final critique — attempt to break it

**Measures well:** hard correctness (unambiguous, reuses the render==solver invariant);
mechanical economy (SPARC/LDLJ/Fitts are validated); and — the new value — the *gap* to a
human reference, which is where realism lives. The three-tier split resists a single opaque
number hiding regressions.

**What it may accidentally optimize / Goodhart risks:**
- **Positional Commitment vs necessary shifts** — a planner could score commitment by
  REFUSING shifts a phrase needs. Guard: commitment is scored only relative to Reference
  B's shift schedule + hard contact fidelity; refusing a needed shift breaks Tier 1/contacts
  or diverges from B, both penalized. Still, mis-labelled B shift counts would mis-score —
  labels must be careful.
- **Idle Stability → frozen hand** — over-rewarding stillness could produce a dead, rigid
  hand. Guard: scoped to root + planted fingers only; a separate "liveness" sanity check on
  free fingers, and the human visual gate. This tension is real and worth watching.
- **Planning Horizon → premature motion** — crediting early moves could reward moving ahead
  of the music unnaturally. Guard: lead capped to human-plausible ranges; credit only when
  it lowers jerk.
- **Human-optimum labels are subjective and non-unique** — the deepest limitation. Until an
  external captured corpus exists, the benchmark measures "OUR model of a guitarist." Score
  against sets/ranges, version-pin labels as revisable, and treat the eventual MediaPipe/
  capture cross-check as the real external validity test.
- **Mechanical optimum is itself hard to define exactly** (true global cost min) — use it as
  a bound/reference, not an absolute; approximate it with the unconstrained-economy planner
  and label it as such.
- **One demonstration phrase can't represent the space** — it's a visual trend indicator
  only, never scored, and must not become the thing we tune toward.
- **Aggregate Movement Score can still be gamed** — hence hard gates multiplicative, all
  subscores visible, mandatory human visual gate, and the A↔B divergence report.

**What future evidence could prove this incomplete:** real expert capture disagreeing with
our Human-optimum labels; discovering that "believable" is phrase/genre-dependent (a
metal-legato hand ≠ a classical hand — the benchmark may need STYLE-conditioned Reference
B); or that viewers key on cues we haven't metricized (e.g. subtle finger-roll, nail angle,
micro-timing) — in which case Tier 3 grows. The framework is designed to grow (add metrics,
add reference sets, add tiers) without breaking existing comparisons.

**Decisions I'd change now (not protecting v1):**
- Elevate the A↔B gap to a headline reported quantity (done above) — it's more informative
  than either reference alone.
- Commit at the spec level to contact-trajectory + N-string schema now, so slides/bends and
  extended-range don't force a benchmark rewrite later.
- Add STYLE as a future axis on Reference B (flagged, not built).

## 13. Recommendation

This v2 spec is the canonical framework. Build the benchmark + harness to THIS (two
references per phrase, three-tier report, the four new Tier-3 metrics, failure taxonomy,
demonstration phrase, contact-trajectory-ready schema), pre-register the V1 gates (v1 §8,
still valid), THEN write the planner against it. Fingering fixed for V1 (isolation),
schema forward-compatible with fingering search. Static solver frozen. No planner code
until the benchmark exists and you approve.
