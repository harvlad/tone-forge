# Song-Form Phase 0 — Similarity Feasibility Study

**Date:** 2026-06-20
**Companion to:** `song_form_label_audit.md` (`e26aed2`) +
`song_form_strategy.md` (uncommitted).
**Scope:** Read-only measurement. No code modifications outside the
self-contained analysis script at
`backend/song_form_feasibility/run_feasibility.py`.

**Question Phase 0 answers:** can choruses, verses, and bridges be
identified naturally from the existing analysed-bundle data, or is
additional feature extraction required before any song-form
classifier work is justified?

**Headline finding:** **Decision B.** Across both songs measured, the
existing feature set produces SSMs that are either saturated to ~1.0
(repetition, vocal) or noisy/flat below 0.55 (chord). Human
inspection of the heatmaps reveals **no recognisable verse / chorus
/ bridge clusters** without imposed thresholds. The signals as
currently computed are insufficient for a classifier. Concrete gap
inventory and minimum addition list in §5.

---

## 1. Corpus coverage

### 1.1 Songs measured

| Slug | Title | Session ID | Section count | In named trial corpus? |
|---|---|---|---|---|
| `sex_on_fire` | Sex On Fire — Kings of Leon (canonical post-fix Run A) | `b640c78a` | 20 | Yes |
| `whats_my_age_again` | What's My Age Again? — blink-182 | `29b31695` | 8 | No (added to broaden the sample) |

### 1.2 Corpus songs NOT measured (no analysed bundle exists yet)

These are listed in `backend/tests/fixtures/song_trial_corpus.json`
but have not been pushed through the pipeline:

* `stairway_to_heaven`
* `hotel_california`
* `wish_you_were_here`
* `romance_de_amor`
* `disco_of_doom` *(not present in the corpus JSON either)*
* `simulated_life` *(not present in the corpus JSON either)*

The corpus expansion gate from `song_form_strategy.md §7.1, Phase 2
Commit 1` is therefore still open. Phase 0 measurement is bounded by
this — n=2 distinct real songs. Findings are directional, not
generalisable. The pattern is, however, consistent across both
songs and consistent with the structural claims made by the audit
doc, so the directional read is robust enough to act on.

### 1.3 Raw output location

All matrices, heatmaps, and per-song findings live under
`backend/song_form_feasibility/`:

```
backend/song_form_feasibility/
├── run_feasibility.py              # standalone read-only script
├── corpus_coverage.json            # this section's data
├── per_song_findings.json          # full structured findings
├── per_song_summary.csv            # one-row-per-song summary
├── chord_ssm__sex_on_fire.{png,json}
├── repetition_ssm__sex_on_fire.{png,json}
├── vocal_ssm__sex_on_fire.{png,json}
├── composite_ssm__sex_on_fire.{png,json}
├── chord_ssm__whats_my_age_again.{png,json}
├── repetition_ssm__whats_my_age_again.{png,json}
├── vocal_ssm__whats_my_age_again.{png,json}
└── composite_ssm__whats_my_age_again.{png,json}
```

The script ingests `backend/data/history.json` directly and does
NOT touch any production module. It is safe to re-run, delete, or
move.

---

## 2. Methodology

For each song, four section-by-section similarity matrices were
built from the existing analysed bundle.

| Matrix | Per-section signal extracted | Pairwise score |
|---|---|---|
| **Chord SSM** | Symbol sequence of chord regions whose midpoint falls inside `[start_time, end_time)` | Mean of {Jaccard over unique symbols, normalised Levenshtein over the sequence, Jaccard over consecutive-symbol bigrams} |
| **Repetition SSM** | Per-stem `repetition_score` from `debug_features` | `1 − mean(|a_s − b_s|)` over stems |
| **Vocal Activity SSM** | `(voiced_frame_ratio, note_density, lead_activity_score)` of the `vocals` stem | `1 − mean(|a − b|)` over the three signals (note_density soft-capped at 10 notes/s) |
| **Composite SSM** | Equal-weight average of the above three | n/a (already a similarity in [0,1]) |

No thresholds are applied to label sections. No clustering is
applied. The matrices are summary statistics describing what's in
the bundles today, plus visual heatmaps for human inspection.

Diagonals are always 1.0 by construction; the off-diagonal mean,
p95, max, and pair-count statistics in §3 ignore the diagonal.

---

## 3. Per-song findings

### 3.1 Sex On Fire (`b640c78a`, 20 sections)

**Ground-truth (from `song_trial_corpus.json`):** 5 coarse sections
in E major — Intro / Verse 1 riff / Chorus 1 + Verse 2a / Verse 2
riff / Chorus 2 + Bridge + Outro.

**Pipeline's section labels (engine output):**
1×intro, 18×verse, 1×outro. Chorus = 0, bridge = 0 — consistent
with the audit's headline finding that chorus and bridge labels are
operationally dead. This is the labelling state we're testing
against — does the *data underneath* already support better?

**Statistics:**

| Matrix | Off-diag mean | p95 | Max | Pairs ≥0.6 | Pairs ≥0.8 |
|---|---|---|---|---|---|
| Chord SSM | 0.20 | 0.39 | 0.55 | **0** | **0** |
| Repetition SSM | 0.95 | 1.00 | 1.00 | 190 | 190 |
| Vocal Activity SSM | 0.92 | 0.99 | 1.00 | 190 | 187 |
| Composite SSM | 0.69 | 0.76 | 0.81 | 190 | 2 |

(Pair counts are over the C(20,2)=190 possible off-diagonal pairs.)

**Visual inspection — Chord SSM** (`chord_ssm__sex_on_fire.png`):
The matrix is uniformly dark. The single warmest off-diagonal pair
is section 3 ↔ section 19 at 0.55 (both labelled "verse" by the
engine, both fall inside the song's recurring chord region). Every
other off-diagonal cell sits below 0.5. **No cluster is visible.**

**Visual inspection — Repetition SSM** (`repetition_ssm__sex_on_fire.png`):
Uniformly yellow at 0.85–1.00 across every off-diagonal entry.
This is **saturation, not similarity**. The per-stem
`repetition_score` is dominated by drums (which repeat in every
section of a 4/4 rock song) and by guitar (which has one held note
per section in this bundle's MIDI). Saturated signals carry zero
discriminative information.

**Visual inspection — Vocal Activity SSM** (`vocal_ssm__sex_on_fire.png`):
Also nearly saturated at 0.80–1.00. The faint variance is driven
by section 16 (visibly slightly darker row) — that section happens
to be one of the shortest, so `note_density` lands in a different
bin. **No verse / chorus structure is visible.** The vocal stem is
active throughout the song; the three vocal signals do not
distinguish chorus from verse.

**Visual inspection — Composite SSM** (`composite_ssm__sex_on_fire.png`):
A flat green field, mean 0.69, with a handful of mildly warmer
cells (3↔19, 5↔11, 5↔9 around 0.77–0.81) and no organised
structure. The composite inherits the chord SSM's lack of
discrimination and the saturation of the other two — averaging a
noisy-flat signal with two saturated signals produces a uniformly
high-ish flat signal.

**Outlier counts (row max < 0.5):**
* Chord SSM: 18 of 20 sections — i.e. almost every section is an
  outlier under chord similarity. The exception is the section 3 ↔
  19 pair noted above.
* Composite SSM: 0 — because saturation lifts every row max above
  0.5.

**Verdict for Sex On Fire:** None of the four matrices reveal the
ground-truth structure. The Verse 1 riff (section ~3–4 in engine
indexing) and Verse 2 riff (section ~9–10) do not cluster. The two
chorus regions (engine sections ~5–7 and ~13–19) do not cluster.
No bridge candidate is visible as an outlier (everything is
either an outlier or saturated). **A classifier built on this
feature set today would have nothing to grip.**

### 3.2 What's My Age Again? (`29b31695`, 8 sections)

**Ground truth:** not in the trial corpus. From listening: intro
riff → verse → pre-chorus → chorus → verse → pre-chorus → chorus
→ outro. (Engine boundaries appear to fire at slightly different
points but the section count is in the right ballpark.)

**Pipeline's section labels:**
intro / verse / drop / verse / drop / verse / drop / buildup. The
`drop` label is the engine's energy-driven proxy for chorus on
loud-quiet-loud arrangements; `verse` and `buildup` cover the rest.

**Statistics:**

| Matrix | Off-diag mean | p95 | Max | Pairs ≥0.6 | Pairs ≥0.8 |
|---|---|---|---|---|---|
| Chord SSM | 0.36 | 0.55 | 0.59 | **0** | **0** |
| Repetition SSM | 1.00 | 1.00 | 1.00 | 28 | 28 |
| Vocal Activity SSM | 1.00 | 1.00 | 1.00 | 28 | 28 |
| Composite SSM | 0.79 | 0.85 | 0.86 | 28 | 9 |

(Pair counts are over the C(8,2)=28 possible off-diagonal pairs.)

**Visual inspection — Chord SSM** (`chord_ssm__whats_my_age_again.png`):
Slightly more structure than Sex On Fire — the three "drop"
sections (3, 5, 7) sit at 0.45–0.59 with each other vs. 0.22–0.35
with the intro. That is the hint of a chorus-like cluster, but
**still well below the 0.6 / 0.8 thresholds a real classifier
would need**, and the discrimination overlaps the "verse vs drop"
boundary (verse 4 ↔ drop 5 = 0.55, drop 3 ↔ drop 5 = 0.59 — i.e.
the drop-pair similarity is barely higher than a verse-drop
similarity).

**Visual inspection — Repetition SSM + Vocal SSM:** Both fully
saturated at 1.00 across every off-diagonal entry. This is even
more pathological than Sex On Fire — with only 8 sections the
per-stem and per-vocal aggregates simply do not vary.

**Visual inspection — Composite SSM:** Mean 0.79, max 0.86, no
visible cluster. Again the chord-SSM noise is averaged with two
saturated signals to produce uniformly green.

**Verdict for What's My Age Again?:** The chord SSM hints at the
three-drop cluster, but the hint is too weak to call without
hand-tuned thresholds, and the other two signals are saturated.

### 3.3 Cross-song pattern

The two songs differ in tempo (153 vs 165 bpm), genre (Southern
rock vs pop-punk), and section count (20 vs 8). They produce
**identical structural failure modes**:

* Chord SSM: noisy and flat, max under 0.6, no clusters ≥0.6
* Repetition SSM: fully saturated → zero discrimination
* Vocal Activity SSM: nearly or fully saturated → near-zero
  discrimination
* Composite SSM: cosmetic green field, no visible structure

This consistency across two very different songs raises the
generalisability of the negative finding above n=2. If the
existing features could surface song form, we would expect at
least one of the two songs to show some structure. Neither does.

---

## 4. Cross-song findings

Single-paragraph answers to the brief's required questions.

* **Do repeated clusters emerge naturally?**
  No. Neither chord SSM has any off-diagonal pair ≥0.6. The
  repetition and vocal SSMs are saturated, which is the opposite
  failure mode of "no cluster": *everything* clusters with
  everything, which is equivalent to no signal.

* **Do verse sections cluster together?**
  No. On Sex On Fire the 18 sections labelled "verse" produce no
  cluster on any matrix. On What's My Age Again? the three verse
  sections (2, 4, 6) do not cluster with each other (0.29–0.33).

* **Do chorus sections cluster together?**
  No identifiable cluster on Sex On Fire. The hint of a drop-drop
  cluster on What's My Age Again? (3 ↔ 5 = 0.59, 3 ↔ 7 = 0.46, 5
  ↔ 7 = 0.45) is real but too weak and overlaps with verse-drop
  pairs.

* **Are bridges outliers?**
  Sex On Fire's ground-truth bridge is engine-merged into the
  closing chord-wash region. No row of the composite SSM presents
  as a structural outlier — saturation prevents outliers from
  forming. The opposite happens on the chord SSM, where *almost
  every* row is an outlier (row max < 0.5 on 18 of 20 sections).
  Both extremes fail to surface a bridge.

* **Are there visually obvious groupings without any classifier?**
  **No.** The heatmaps look like uniform fields — either dark
  (chord) or yellow (rep / vocal) — with no organised block
  structure. A classifier would require both hand-tuned thresholds
  and additional signal extraction to recover structure that is
  not visually present today.

* **How often do repeated clusters appear?**
  Zero in two songs (chord), saturated in two songs (rep / vocal).
  The composite returns 2 strong pairs in Sex On Fire (1.1% of
  the 190 possible pairs) and 9 in What's My Age Again? (32% — a
  saturation artefact, not real cluster structure).

* **How often do bridge candidates appear?**
  Zero structural outliers under the composite SSM in either song.

* **Does vocal activity improve separability?**
  No. The vocal SSM is one of the *most* saturated signals, near
  1.00 across every off-diagonal entry. The three vocal sub-signals
  (`voiced_frame_ratio`, `note_density`, `lead_activity_score`)
  drift over a small enough range that section-level vocal
  fingerprints look identical even when the source music doesn't.

* **Is chord similarity alone sufficient?**
  No. Even where chord-SSM hints at structure (blink-182 drops),
  the signal sits below 0.6 and overlaps verse-drop scores. A
  threshold-based decision rule would have to choose between
  high false-positive verse→chorus flips or high false-negative
  chorus-misses. Neither is acceptable.

---

## 5. Why the signals fail, and what's actually needed

### 5.1 Why chord SSM is noisy

The chord lane today is symbol-per-region with full chord-quality
labels (`E`, `E5`, `E7`, `C#m`, `G#5`, `Em`, `F#7`…). The same
harmonic loop in two different sections gets surfaced with slightly
different symbols because of inversions, voicing weight, and
chord-quality threshold noise:

* Sex On Fire section 5 chord sequence: `E C#m`
* Sex On Fire section 18 chord sequence: `E E5 E E7 C#m G#5`

Musically the same E↔C#m progression; the SSM scoring them at
0.33 because the symbol surface differs.

**Minimum addition needed:** chord-symbol canonicalisation (root +
quality only, drop power-chord suffixes, drop sevenths, snap to bar
grid) BEFORE pairwise similarity is computed. Alternatively,
abandon symbol matching and switch to a beat-aligned chroma SSM
(the standard MIR approach) — that requires chroma access from the
pipeline, which is computed at chord-detection time but not
persisted.

### 5.2 Why repetition SSM is saturated

The `repetition_score` field in `debug_features` is computed
per-stem-per-section for the riff-first guidance-mode classifier.
Its purpose there is to detect *within-section* repetition. Across
sections of the same song, drums repeat in every section, bass
patterns are consistent, and the per-stem score vectors converge.
Section-to-section similarity over those vectors saturates.

**Minimum addition needed:** a *cross-section* repetition signal —
e.g. "does section j's chord set ⊇ section i's chord set?" or "does
section j's beat-aligned chroma loop match section i's?" The
existing within-section field cannot answer that.

### 5.3 Why vocal activity SSM is saturated

`voiced_frame_ratio`, `note_density`, and `lead_activity_score` are
all derived from MIDI-extracted vocal notes. On a vocally-active
song like Sex On Fire, the vocals stem has notes throughout, and
the three signals vary within a small range. They are insensitive
to "chorus is louder and brighter than verse" — the discriminating
property.

**Minimum addition needed:** raw vocal-stem RMS energy aggregated
per section (chorus = louder vocal stem), and spectral-centroid
uplift per section (chorus = brighter spectrum because cymbals
open). Neither is currently aggregated at section level.

### 5.4 Summary of additions before Path C becomes viable

This is the inventory implied by the Phase 0 negative result.
**None of this is being implemented in Phase 0** — it is the
shopping list Phase 2 of the strategy doc would need to deliver
before clustering or threshold work makes sense.

| Addition | Status today | Required for |
|---|---|---|
| Canonicalised chord symbols (root+quality only) | Not done | Useful chord SSM |
| Beat-aligned chord-set per section | Not done | Cross-section chord matching |
| Beat-aligned chroma SSM | Not computed | Standard MIR approach (alternative to chord-symbol SSM) |
| Per-section vocal-stem RMS | Not aggregated | Chorus detection ("loudest vocal") |
| Per-section drum-stem onset rate | Not aggregated | Intro/outro tacit detection, chorus density uplift |
| Per-section spectral centroid (already passed to labeller but unread) | Computed but unused | Chorus brightness uplift |
| Cross-section repetition signal | Not computed | Bridge outlier detection |
| Corpus expansion to ≥4 analysed songs | Only 2 today | Calibration sanity |

---

## 6. Decision

### **B) Existing signals are insufficient. Additional feature extraction is required before classifier work.**

The Phase 0 success criterion was explicit:

> A positive result is: "human inspection of the matrices reveals
> recognizable verse/chorus/bridge structure across multiple songs
> without needing hand-tuned thresholds."

Across both songs measured:

* No chord-SSM cluster reaches 0.6 on any off-diagonal pair.
* No repetition-SSM or vocal-SSM cluster discriminates — both are
  saturated.
* No composite-SSM block structure is visible.
* No bridge candidate emerges as an outlier on any matrix.

Human inspection of the eight heatmaps in
`backend/song_form_feasibility/` reveals uniform fields rather than
recognisable block structure. Designing
`song_form_classifier.py` against this feature set today would
either produce hand-tuned thresholds chasing the noise floor of the
chord SSM, or simply re-apply the saturated signals as a no-op.
Either outcome is worse than the audit's recommended Phase 1 (Path
B — visual de-emphasis of placeholder labels), which ships honesty
without depending on infrastructure that doesn't yet exist.

### Implications for the strategy doc

`song_form_strategy.md` recommended Phased B → C. The Phase 0
result tightens that recommendation:

* **Phase 1 (Path B, UI de-emphasis):** unchanged — still
  ship-able today, independent of Phase 0.
* **Phase 2 (Path C, real classifier):** **explicitly gated** on
  the §5.4 feature-extraction additions, in this order:
  1. Canonicalised chord symbols *or* beat-aligned chroma SSM
  2. Per-section vocal-stem RMS aggregation
  3. Per-section drum-stem onset-rate aggregation
  4. Corpus expansion to ≥4 analysed songs
  5. Re-run Phase 0 against the expanded corpus + new features
  6. If Phase 0 re-run shows visible cluster structure, then
     proceed to classifier design

Phase 2 cannot be started in the form sketched by the strategy
doc (SSM module → verse/chorus discriminator → bridge detector →
threshold freeze) until step 1 of the feature work is done,
because there is nothing for the SSM module to consume that would
produce discriminating output. The strategy doc's Phase 2 ordering
needs amending in a follow-up.

### Implications for the riff-first milestone

Phase 0 incidentally confirms an architectural fact noted in the
audit: the riff-first milestone's per-section feature vectors
(`section_features.py`) are necessary for guidance-mode but not
sufficient for song form. The two classifiers need overlapping
but distinct signal sets. Treating them as a single feature
namespace and reusing the same fields across both classifiers
will continue to fail until the song-form additions in §5.4 land.

---

## 7. Constraints honoured

This Phase 0 task **did not**:

* modify classifier code
* create `SongFormThresholds`
* add clustering logic
* add UI changes
* add API fields
* propose specific threshold values
* commit anything to git

The only filesystem additions were:

* `backend/song_form_feasibility/` (new directory, all read-only
  outputs + one analysis script)
* this document

No production code path consumes any of those files.

---

## 8. Outputs at a glance

| File | Purpose |
|---|---|
| `backend/song_form_feasibility/run_feasibility.py` | Standalone read-only analysis script. Safe to re-run. |
| `backend/song_form_feasibility/corpus_coverage.json` | Which corpus songs were measurable, which are still un-analysed. |
| `backend/song_form_feasibility/per_song_findings.json` | Full structured findings (stats + chord sequences + outlier indices). |
| `backend/song_form_feasibility/per_song_summary.csv` | One-row-per-song summary statistics. |
| `backend/song_form_feasibility/chord_ssm__*.png` + `.json` | Chord-progression similarity matrices. |
| `backend/song_form_feasibility/repetition_ssm__*.png` + `.json` | Per-stem repetition_score similarity matrices. |
| `backend/song_form_feasibility/vocal_ssm__*.png` + `.json` | Vocal-activity similarity matrices. |
| `backend/song_form_feasibility/composite_ssm__*.png` + `.json` | Equal-weight composite similarity matrices. |

Per-song summary (also in `per_song_summary.csv`):

```
slug                  sections  chord_mean  chord_p95  chord_pairs≥0.8  comp_mean  comp_pairs≥0.7
sex_on_fire           20        0.199       0.387      0                0.689      2
whats_my_age_again    8         0.357       0.553      0                0.786      9
```
