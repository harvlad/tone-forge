# Signal Taxonomy Report — Structural Backbone Investigation

**Phase:** Step 4 (Re-run signal discovery), after corpus expansion completed
in commit `8fcc621` (Phase 0D) and validated in Step 3 of the workflow.
**Scope:** Decide whether **G** (chord-lane self-similarity), **H** (repetition /
motif recurrence), or **I** (harmonic stability) can serve as the primary
**structural** signal for a future song-form classifier. D (vocal RMS) and F
(drum density) are treated as known **arrangement** signals; this report measures
G/H/I against the same six canonical songs to determine which, if any, qualifies
as a UNIVERSAL backbone.

**Constraints honoured:** no classifier, no thresholds, no UI, no SSM design.
Pure measurement and classification.

**Methodology source:** `/tmp/phase0c_scripts/step4_signal_taxonomy.py` (executed
2026-06-21). Inputs are the persisted JSON bundles in `backend/data/history.json` —
no audio re-decode. Per-section signal vectors are dumped to
`/tmp/phase0c_scripts/step4_taxonomy_persection.json` for traceability.

---

## A. Metrics table for G / H / I

Two G formulations are evaluated to give the signal a fair shot (Phase 0B
showed canonical chord-symbol similarity performed poorly — we re-test that
plus a pitch-class-histogram variant). Two H formulations: H1 captures
song-level repetition, H2 captures cross-section recurrence. I uses
Krumhansl-Schmuckler key correlation on per-section duration-weighted
pitch-class histograms.

All separability scores use the same shape as Phase 0C numbers
(SSM off-diagonal std for G; std/(mean+eps) clipped to [0, 1] for H2 and I).
Higher = more discriminating.

| Song                  | G1 (edit-distance SSM) | G2 (cosine PC-hist SSM) | H1 (song repeat frac) | H2 (per-section recurrence sep) | I (key-strength sep) | I_mean (saturation) |
|-----------------------|:----------------------:|:-----------------------:|:---------------------:|:-------------------------------:|:--------------------:|:-------------------:|
| stairway_to_heaven    | 0.127 | 0.283 | 0.258 | 0.808 | 0.113 | 0.726 |
| hotel_california      | 0.182 | 0.297 | 0.194 | 0.845 | 0.086 | 0.740 |
| wish_you_were_here    | 0.107 | 0.220 | 0.242 | 0.670 | 0.196 | 0.733 |
| romance_de_amor       | 0.193 | 0.313 | 0.218 | 0.837 | 0.147 | 0.690 |
| sex_on_fire           | 0.147 | 0.295 | 0.420 | 0.702 | 0.132 | 0.728 |
| whats_my_age_again    | 0.108 | 0.122 | 0.462 | **0.198** | 0.069 | 0.795 |

Cross-corpus aggregates (rounded to 3 dp):

| Signal | min   | median | max   | range |
|--------|------:|-------:|------:|------:|
| G1 (edit-dist SSM) | 0.107 | 0.137 | 0.193 | 0.086 |
| G2 (cosine SSM)    | 0.122 | 0.289 | 0.313 | 0.191 |
| **G_max** (best of G1, G2 per song) | 0.122 | 0.289 | 0.313 | 0.191 |
| H1 (song repeat frac, scalar) | 0.194 | 0.250 | 0.462 | 0.268 |
| **H2** (per-section recurrence sep) | 0.198 | 0.755 | 0.845 | 0.647 |
| I (key-strength sep)               | 0.069 | 0.122 | 0.196 | 0.127 |
| I_mean (raw correlation magnitude) | 0.690 | 0.730 | 0.795 | 0.105 |

---

## B. Comparison against D and F (Step 3 numbers)

| Signal | Class       | min   | median | max   | range |
|--------|-------------|------:|-------:|------:|------:|
| D (vocal RMS)        | arrangement | 0.330 | 0.527 | 0.735 | 0.405 |
| F (drum density)     | arrangement | 0.128 | 0.394 | 0.709 | 0.581 |
| G_max (chord-lane SSM) | structural? | 0.122 | 0.289 | 0.313 | 0.191 |
| H2 (chord recurrence) | structural? | 0.198 | 0.755 | 0.845 | 0.647 |
| I (key-strength)     | structural? | 0.069 | 0.122 | 0.196 | 0.127 |

**Ranking by min separability** (worst-case robustness — the metric that matters
for a UNIVERSAL signal):
```
  D  (0.330) > H2 (0.198) > F  (0.128) > G  (0.122) > I  (0.069)
```

**Ranking by median separability** (typical discrimination):
```
  H2 (0.755) > D  (0.527) > F  (0.394) > G  (0.289) > I  (0.122)
```

H2 has the **highest median** of any signal measured to date — it discriminates
sections more strongly than D or F on five of the six canonical songs. But H2
has a **worse minimum** than D, driven entirely by one song (Whats My Age
Again at 0.198). G has a worse minimum than F. I is worst on both axes.

---

## C. Structural vs Arrangement taxonomy

### G — chord-lane self-similarity: **DEAD**

- **Per-song separability:** G_max ranges 0.122 → 0.313. Every song in the
  corpus produces a low value. The G2 (cosine over per-section pitch-class
  histograms) variant is consistently the stronger formulation, but its peak
  (Romance at 0.313) is still below the typical D value and well below any
  H2 value.
- **Cross-song variance:** narrow (range 0.191). The signal is uniformly
  weak rather than genre-conditional.
- **Saturation behaviour:** G1 saturates because normalised edit distance
  between dissimilar pitch-class sequences clusters around 0.7–1.0
  regardless of content, suppressing off-diagonal std. G2 partially
  recovers by using duration-weighted histograms but is still dominated by
  the diatonic-overlap floor — most sections share most of the diatonic
  pitch classes, so cosine similarity stays high.
- **Failure modes:**
  - Romance de Amor produces G_max = 0.313 (best in corpus) because the
    piece's Em → E modulation moves the histogram materially. But that's
    a 0.313 best case across all six songs — still mediocre.
  - Whats My Age Again collapses to G2 = 0.122 because pop-punk verses
    and choruses all use the same I-V-vi-IV root set; cosine similarity
    is ~1 everywhere; off-diagonal std → 0.
  - Stairway acoustic sections behave the same: every section is in the
    Am-family chord set, so cosine similarity is uniform.
- **On the named failure cases:**
  - Romance de Amor — `G_max = 0.313`. Moves but to a low ceiling.
  - Stairway acoustic intro — sections 0..N share PC histograms; G2 is flat.
  - Constant-vocal songs — irrelevant axis for G (vocals not used).
  - Constant-drum songs — irrelevant axis for G.
- **Verdict:** G as constructed from chord-lane data is **DEAD**. Confirms the
  Phase 0B finding. A radically different formulation (per-beat chroma
  vectors from the audio, not from the chord-lane post-processing) might
  recover signal, but that is no longer "chord-lane self-similarity" — it
  is audio-domain chroma SSM, a different signal that would require
  re-decoding stems and is out of scope for this taxonomy.

### H — repetition / motif recurrence: **CONDITIONAL**

- **Per-song separability:** H2 ranges 0.198 → 0.845. Five of six songs
  sit between 0.670 and 0.845 — strongly discriminating. One song collapses
  (Whats My Age Again at 0.198).
- **Cross-song variance:** very wide (range 0.647). Signal is excellent
  when the substrate exists and degenerate when it does not.
- **Saturation behaviour:** H2 saturates **high** for songs with structural
  variety (verses recur, bridge is novel) and saturates **low** for songs
  where every section reuses essentially the same chord pattern. H1
  (song-level repeat fraction) correlates inversely with this: Whats My Age
  has the highest H1 (0.462 — most overall repetition) precisely because
  the song is structurally homogeneous, which is what kills H2.
- **Failure modes:**
  - Pop-punk-style songs whose verses, choruses, and bridges all draw from
    the same three- or four-chord palette. H2 = 0.198 on Whats My Age is
    not just low — it is in the same band as the worst G and I numbers,
    meaning H provides no useful signal for that song class at all.
  - Songs with very few sections (<4) have insufficient pairwise
    structure for H2 to be statistically meaningful. None of the canonical
    six fall here (min is 8 sections), but it bounds future inclusion.
- **On the named failure cases:**
  - Romance de Amor — `H2 = 0.837`. Survives the instrumental case
    because the piece is sectional (over-segmented into 15 sections, many
    of which share the same arpeggio-based chord pattern).
  - Stairway acoustic intro — `H2 = 0.808`. Survives. The acoustic-vs-band
    transition is captured because the chord pattern repeats inside the
    acoustic section but not outside it.
  - Constant-vocal songs — Hotel California is vocal-throughout once the
    intro ends; H2 = 0.845 (highest in corpus). Constant vocals do not
    break H.
  - Constant-drum songs — Sex on Fire has consistent drum pattern after
    the intro; H2 = 0.702. Constant drums do not break H.
- **Verdict:** H is **CONDITIONAL**. The substrate H requires is *chord-pattern
  diversity across sections*. Where that exists (5/6 of the canonical
  corpus), H is the strongest measured signal by a wide margin. Where it
  does not (pop-punk-style harmonic homogeneity), H collapses harder than
  the arrangement signals do. H cannot stand alone as the structural
  backbone, but it is the only candidate worth building on.

### I — harmonic stability: **DEAD**

- **Per-song separability:** I_sep ranges 0.069 → 0.196. Every song
  produces a low value. No song exceeds 0.20.
- **Cross-song variance:** narrow (range 0.127). Uniformly weak.
- **Saturation behaviour:** the root cause is visible in `I_mean_strength`
  — every song's per-section K-S correlations average 0.690 → 0.795. The
  signal has no headroom. Once a section is "in a key" (which essentially
  every section in the corpus is, including Romance's modulation), the
  K-S correlation saturates near its ceiling; variance across sections is
  bounded above by how much room remains. With a typical mean of 0.73,
  the mathematical maximum I_sep is ~0.27 — and the observed max (0.196)
  is close to that ceiling.
- **Failure modes:**
  - The signal does not distinguish between "section in key X" and
    "different section in key Y" because the K-S template fires strongly
    on any tonally-coherent pitch-class distribution. Tonic identity
    changes do not move I.
  - Romance de Amor's Em → E modulation is the closest test: I = 0.147,
    barely above noise.
- **On the named failure cases:** irrelevant — I fails on all six songs
  uniformly, not just on the named failure cases.
- **Verdict:** I as currently formulated is **DEAD**. A future "harmonic
  stability" signal would need a fundamentally different shape: explicit
  modulation detection (e.g. windowed tonic-tracking with a
  modulation-cost penalty), not K-S correlation strength. That is a new
  signal, not a tuning of this one.

### Summary

| Signal | Class       | Verdict     | Min sep | Median sep | Failure case            |
|--------|-------------|-------------|--------:|-----------:|-------------------------|
| G      | structural? | **DEAD**    | 0.122   | 0.289      | uniform across corpus   |
| H      | structural? | **CONDITIONAL** | 0.198 | 0.755   | constant chord vocab    |
| I      | structural? | **DEAD**    | 0.069   | 0.122      | uniform across corpus   |
| D      | arrangement | (Phase 0C: CONDITIONAL) | 0.330 | 0.527 | no-vocals / constant vocals |
| F      | arrangement | (Phase 0C: CONDITIONAL) | 0.128 | 0.394 | no-drums / acoustic intros |

No signal qualifies as UNIVERSAL by the bar implied by the question (useful
across essentially all genres). H is the only structural candidate that
exceeds the arrangement signals on its median and on five-of-six absolute
scores, but it has a documented collapse mode.

---

## D. Recommended shortlist

**Keep, in priority order:**

1. **H** — primary structural signal candidate. Track per-section recurrence
   (H2), with H1 retained as a song-level *fallback indicator* (low H1 means
   the song lacks the substrate H2 needs; high H1 with low H2 is the
   pop-punk-style homogeneity signature).
2. **D + F** — keep as arrangement signals. Their roles are already well-
   characterised by Phase 0C / Step 3, and they cover failure modes that
   H does not (D fires on vocal-entrance songs even when chord vocabulary
   is uniform; F fires on drum-pattern-shift songs even when chord
   vocabulary is uniform).

**Drop, with rationale:**

3. **G (chord-lane SSM)** — both formulations tested are bounded below
   either D's or F's minimum. Re-confirmation of the Phase 0B negative
   result. Any future investigation of chord-similarity-based structural
   signal should move to per-beat chroma SSM from audio, not from the
   post-processed chord lane.
4. **I (K-S key-strength)** — saturates near a ceiling that mathematically
   caps I_sep at ~0.27 across the corpus; observed max is 0.196. Cannot
   be salvaged by tuning. Any future harmonic-stability signal needs a
   different formulation entirely (explicit modulation detection).

---

## E. Decision

**Decision: B — H is the primary structural signal candidate.**

This decision is the correct one **only** if the next milestone is willing
to treat H as a conditional backbone rather than a universal one. Specifically:

- H is the only signal in the G/H/I set that exceeds the arrangement
  signals on the typical case (median 0.755 vs D's 0.527 and F's 0.394).
- H has a documented and characterisable failure mode (constant chord
  vocabulary, as in pop-punk). The failure mode is detectable from H1
  itself, so the system can know when H is unreliable.
- H is not UNIVERSAL. Building a classifier that pretends otherwise will
  fail on the pop-punk class, which is a meaningful share of the
  guitar-music corpus the product targets.
- Decision A (G is primary) is unsupported: G's best value across all
  six songs (0.313) is below D's worst value (0.330).
- Decision C (I is primary) is unsupported: I saturates near its ceiling
  on every song in the corpus.
- Decision D (no structural signal currently justifies classifier work)
  is defensible on a strict reading — no signal is UNIVERSAL — but
  would also reject D and F by the same standard, which Phase 0C already
  validated as useful. The empirical pattern is "every signal is
  conditional; combinations of complementary conditional signals are how
  this gets built."

**Implication for the next milestone:** Step 6 ("Design SSM") should be
re-scoped from "design THE SSM" to "design the chord-N-gram recurrence
extractor (H2) and its fallback-detection signal (H1)." The chord-PC SSM
formulation should be abandoned for this milestone; any future return to
SSM-style structural signal should use audio-domain chroma, not the chord
lane.

If the user disagrees with treating H's pop-punk collapse as acceptable
under the CONDITIONAL classification, the alternative is **Decision D** —
do not develop a classifier yet, and instead invest in a sixth signal
(probably audio-domain chroma SSM) before re-running this taxonomy.

---

## Artifacts

- `/tmp/phase0c_scripts/step4_signal_taxonomy.py` — measurement harness
- `/tmp/phase0c_scripts/step4_taxonomy_table.csv` — aggregated per-song
  numbers backing Section A
- `/tmp/phase0c_scripts/step4_taxonomy_persection.json` — per-section
  signal vectors (H2 per section, I per section with K-S labels, section
  type strings) backing the failure-mode commentary in Section C
