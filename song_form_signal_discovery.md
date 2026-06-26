# Song Form — Phase 0B: Similarity Signal Discovery

> **Scope**: Phase 0B is research-only. It re-runs the Phase 0
> Similarity Feasibility Study with a richer set of section-level
> signals computed from the actual stem audio (not just the analysed
> bundle). The deliverable is a decision: do at least one of the
> six candidate signals consistently reveal musical structure when
> plotted as a Section Similarity Matrix (SSM), or do they all fail?
>
> **Constraints (enforced)**: NO classifier, NO `SongFormThresholds`,
> NO clustering, NO bundle schema changes, NO API surface, NO UI.
> No live thresholds were tuned during this study; the only judgement
> applied is "does a human looking at the heatmap see structure?"

---

## 1. TL;DR

**Decision: A — proceed.**

Two signals — **D. Vocal RMS** and **F. Drum Density** — consistently
produce SSMs with **visible verse-vs-chorus-shaped block structure on
both available songs**, without any threshold tuning. Both signals
have:

- mean similarity in the 0.47–0.68 range (NOT saturated at ~1.0),
- off-diagonal std ≥ 0.27 (wide spread, room for clustering),
- the same shape signature across both songs (a dark "intro stripe"
  + a high-similarity body block + outro decay).

The four other candidate signals fail in characteristic ways:

| Signal | Failure mode |
|---|---|
| A. Canonical chord SSM | Still flat. Canonicalisation lifts the off-diagonal mean from 0.20 → 0.22 on Sex On Fire and to 0.48 on Blink-182, but std stays small and zero pairs reach ≥0.8 on either song. |
| B. Beat-aligned chroma SSM | **Fully saturated** at ~0.98 mean, std ≤0.02. Both songs stay in a single key; per-section mean chroma vectors are near-identical. |
| C. MFCC embedding SSM | Mostly saturated (mean 0.78–0.90) for stems that share the same recording timbre. Picks up timbre, not form. |
| E. Instrumentation SSM | Mostly saturated (mean 0.88–0.91). Most sections use the same stem palette. Only intros/outros that drop a stem stand out. |

The next milestone (Phase 0C / Path C ramp) can proceed using D + F
as the structural-similarity substrate.

---

## 2. Corpus Coverage

Phase 0B uses the same corpus inventory as Phase 0. Only sessions
whose stem audio is still present on disk **and** whose
SongUnderstanding bundle persists were eligible.

| Slug | Session | Sections | Stems on disk | Status |
|---|---|---|---|---|
| `sex_on_fire` | `b640c78a` | 20 | drums, bass, other, vocals, guitar, piano | ✅ included |
| `whats_my_age_again` | `29b31695` | 8 | drums, bass, vocals, other | ✅ included |
| `stairway_to_heaven` | — | — | — | ❌ no bundle |
| `hotel_california` | — | — | — | ❌ no bundle |
| `wish_you_were_here` | — | — | — | ❌ no bundle |
| `romance_de_amor` | — | — | — | ❌ no bundle |

The two-song corpus is the same limitation called out in Phase 0
§5.5. It is sufficient to **discover candidate signals** because we
only need a signal to fail-or-not on each song; we do not need a
statistical population to make a per-song decision. The follow-up
(Phase 0C) will need ≥4 analysed songs covering different forms
before we set thresholds.

Source of truth for what was loaded:
`backend/song_form_signal_discovery/corpus_coverage.json`.

---

## 3. Methodology

### 3.1 Inputs

For each song, the discovery script loads:

- The analysed bundle JSON (sections, chord lane, stem list).
- The on-disk stem WAVs from the original separation tempdir
  (`/var/folders/…/toneforge_stems_*`), resampled to **22050 Hz mono**
  to match `result.sample_rate`.
- A synthetic full-mix built by summing all stems and peak-normalising
  to 0.99. Used as the substrate for chroma / MFCC / mix-level RMS.

No bundle field is modified, no classifier runs, no thresholds are
applied at any point.

### 3.2 Six candidate signals

| Code | Name | Computation | Pairwise comparison |
|---|---|---|---|
| **A** | Canonical chord | Per-section chord-symbol list, each symbol canonicalised to `root + (m?)` (drop power-chord suffix, sevenths, slash bass). | Mean of {Jaccard, normalised Levenshtein, bigram Jaccard}. |
| **B** | Beat-aligned chroma | `librosa.feature.chroma_cqt` on the full mix, per-frame L2 normalised, then per-section mean chroma vector. | Cosine similarity. |
| **C** | MFCC embedding | `librosa.feature.mfcc(n_mfcc=13)` on the full mix; embedding = concat(mean, std) → 26-d. | Cosine similarity. |
| **D** | Vocal RMS | `librosa.feature.rms` on the vocals stem → per-section `(rms_mean, rms_p95, rms_std, dyn_range_db)`. | `1 − mean(|Δ| over normalised channels)`, clipped to [0, 1]. |
| **E** | Instrumentation | Per-stem mean RMS for each section → unit-normalised vector across stems (a "balance" descriptor). | Cosine similarity. |
| **F** | Drum density | `librosa.onset.onset_detect` on the drums stem (or full mix fallback) → onsets per second per section. | Relative density inverse: `1 − |Δ| / max(a, b, ε)`. |

Each signal produces an `N × N` SSM with diagonal pinned to 1.0 and
the matrix forced symmetric.

### 3.3 Statistics per SSM

For every SSM, off-diagonal statistics were captured:

`mean, std, p10, p50, p90, min, max, pairs ≥ 0.6, pairs ≥ 0.8,
separability = std × 2 (capped at 1.0)`.

A "good" SSM should have mid-range mean (not saturated),
non-trivial std (not flat), and a visible block pattern under the
diagonal.

Outputs:

- `*_<song>.png` — heatmap with annotated values + section labels.
- `*_<song>.json` — raw matrix + statistics.
- `per_song_signal_summary.json` — all stats for every (signal, song)
  pair.
- `cross_song_ranking.csv` — flat table for cross-song ranking.

---

## 4. Numerical Results

### 4.1 Per-song statistics

#### Sex On Fire (20 sections)

| Signal | mean | std | p10–p90 gap | sep. | pairs ≥0.6 | pairs ≥0.8 |
|---|---:|---:|---:|---:|---:|---:|
| A_chord_canonical | 0.216 | 0.130 | 0.304 | 0.261 | 1 | 1 |
| B_chroma | 0.978 | 0.024 | 0.047 | 0.047 | 190 | 190 |
| C_mfcc | 0.899 | 0.112 | 0.216 | 0.224 | 184 | 164 |
| **D_vocal_rms** | **0.472** | **0.396** | **0.922** | **0.791** | 97 | 63 |
| E_instrumentation | 0.880 | 0.191 | 0.259 | 0.382 | 171 | 167 |
| **F_drum_density** | **0.674** | **0.280** | **0.704** | **0.559** | 141 | 80 |

#### What's My Age Again? (8 sections)

| Signal | mean | std | p10–p90 gap | sep. | pairs ≥0.6 | pairs ≥0.8 |
|---|---:|---:|---:|---:|---:|---:|
| A_chord_canonical | 0.480 | 0.135 | 0.325 | 0.269 | 7 | 0 |
| B_chroma | 0.979 | 0.015 | 0.040 | 0.030 | 28 | 28 |
| C_mfcc | 0.783 | 0.171 | 0.462 | 0.342 | 23 | 14 |
| **D_vocal_rms** | **0.490** | **0.288** | **0.760** | **0.576** | 12 | 6 |
| E_instrumentation | 0.908 | 0.062 | 0.184 | 0.123 | 28 | 28 |
| **F_drum_density** | **0.681** | **0.275** | **0.753** | **0.550** | 21 | 14 |

### 4.2 Cross-song separability ranking

Higher separability score (capped 2σ) = wider spread of pairwise
values = more usable structural signal.

| Rank | Signal | Sex On Fire sep. | Blink-182 sep. | Consistent? |
|---:|---|---:|---:|---|
| 1 | **D_vocal_rms** | **0.791** | **0.576** | ✅ both >0.55 |
| 2 | **F_drum_density** | **0.559** | **0.550** | ✅ near-identical |
| 3 | E_instrumentation | 0.382 | 0.123 | ❌ collapses on blink |
| 4 | C_mfcc | 0.224 | 0.342 | ❌ saturation noise |
| 5 | A_chord_canonical | 0.261 | 0.269 | ⚠️ flat both songs |
| 6 | B_chroma | 0.047 | 0.030 | ❌ saturated both songs |

Only D and F have **both** a high separability score AND consistency
across the two songs. They are the only two candidates that survive
the "works on multiple songs" filter.

---

## 5. Per-Song Visual Findings

### 5.1 Sex On Fire — D. Vocal RMS

The heatmap (`D_vocal_rms__sex_on_fire.png`) shows a **textbook
tri-block pattern**:

- **Block 1** (rows 1–4 + 20): the intro, first three verses, and
  outro cluster with each other at 0.56–0.93. These are the
  vocally-quiet stretches.
- **Hard cross-stripe**: rows 1–4 vs rows 5–19 are exactly **0.00**
  (the vocal-quiet regions have effectively zero RMS overlap with
  the body). This is the kind of separation a clustering algorithm
  would lock onto trivially.
- **Block 2** (rows 5–19): the vocally-active body, similarities
  mostly 0.79–0.97 with each other.
- **Outliers**: section 13 (0.55–0.65 against the rest of block 2)
  and especially section 16 (0.34–0.64 against block 2). Section 16
  shows up as an outlier in F as well — see §5.2 — suggesting an
  actual structural anomaly rather than a signal artefact.

This is the **first time in either Phase 0 or Phase 0B that an SSM
visually separates "chorus-style" from "verse-style" passages with
no threshold tuning**.

### 5.2 Sex On Fire — F. Drum Density

The heatmap (`F_drum_density__sex_on_fire.png`) shows:

- Section 1 (intro) at **0.00 with every other section** — drums
  are tacit. A useful dark vertical/horizontal stripe.
- Section 20 (outro) at 0.27–0.55 against the body — drums end
  before the song does.
- Sections 2–19 form a single dense block (0.55–0.99). Within that
  block, section 16 reads slightly cooler (0.48–0.85), echoing the
  same anomaly D flagged.

D and F flag the **same** section-16 anomaly independently. That
cross-signal agreement is exactly the kind of evidence Phase 0C
will need to make robust per-song decisions.

### 5.3 Blink-182 — D. Vocal RMS

`D_vocal_rms__whats_my_age_again.png` is the cleanest result of the
study. With 8 sections we can read the matrix directly:

- **Drop cluster {3, 5, 7}**: similarities 0.88 / 0.91 / 0.93 with
  each other (all three "drop" labels by Madmom's section detector).
- **Intro/late-verse/buildup cluster {1, 6, 8}**: similarities
  0.80–0.88 with each other.
- **Cross-cluster pairs**: 0.03–0.27, deeply separated.
- The two ambiguous sections (verse 2, verse 4) sit in the middle
  band as expected for transitional content.

The labels Madmom assigned line up perfectly with the D-clustering:
the three "drop" sections cluster together, the intro/verse-6/
buildup cluster together, and verses 2 & 4 are correctly the
mid-band cases. **This is the closest thing to ground-truth
validation we have in the available corpus.**

### 5.4 Blink-182 — F. Drum Density

`F_drum_density__whats_my_age_again.png`:

- Section 1 (intro) drums tacit (0.19–0.31).
- Sections 2–7 form a single high-density block (0.62–0.99) — the
  body, with consistent drum onset rate.
- Section 8 (buildup) at 0.62–0.83 — drums present but at a
  different density.

The intro vs body separation that D shows on Sex On Fire repeats
here on blink, and the buildup is correctly flagged as transitional.

### 5.5 The four failing signals

- **A. Canonical chord** (Sex On Fire): off-diagonal mean 0.22, max
  0.83 (a single pair), no visible block structure. Even after
  canonicalisation, the chord vocabulary is too long and too
  variable per section to support symbol-set similarity.
- **B. Beat-aligned chroma** (both songs): SSM is uniformly bright
  green at 0.94–1.00. Both songs stay in a single key throughout;
  averaging chroma across a section washes out everything except
  "we're in key X". This signal is **structurally incapable** of
  finding song form without segmentation finer than per-section
  means.
- **C. MFCC embedding** (Sex On Fire): mean 0.90, std 0.11. Picks up
  some recording-quality variation but mostly tells us "it's the
  same microphone signal chain". Note section 16 is again the
  visible cool spot (min 0.36), which is interesting evidence-of-
  structure but not enough on its own.
- **E. Instrumentation** (Sex On Fire): mean 0.88. Intro (row 1)
  separates cleanly (0.17–0.48 against everything else) because the
  intro drops the drum stem. But within the body, every section
  uses the same stem palette so the SSM collapses to ~0.95.

---

## 6. Why D and F succeed

The four failing signals all rely on **content** descriptors
(chords, chroma, timbre, stem palette). For a single song in a
single key with a single mix, content is roughly constant across
sections — which is why those SSMs saturate near 1.0.

D and F instead measure **energy and event-rate dynamics**. Form-
defining moments — quiet intros, vocal entries, drum drops, builds
— are exactly the moments where these dynamics change. The signal
is naturally bimodal or multi-modal on song-form boundaries
without any need to threshold or label.

This matches the song_form_strategy.md §6 hypothesis that
"vocal energy" and "drum activity" are the highest-information
section descriptors for popular music.

---

## 7. Decision

**A — proceed.** At least one signal — in fact two complementary
signals — consistently reveals song-form structure on the available
corpus without hand-tuned thresholds.

Phase 0C should build on:

1. **Primary**: D (vocal RMS) — strongest separability, cleanest
   visual block structure on both songs.
2. **Secondary / corroborating**: F (drum density) — independently
   confirms the same boundaries, especially intros and outros.
3. **Combined SSM** (deferred to 0C): an unweighted average of D
   and F SSMs is the natural first composite to try. Phase 0C can
   compare composite-vs-D-alone-vs-F-alone before committing to a
   weighting.

**Discarded for Phase 0C structural similarity**: B (chroma) and
E (instrumentation) — both saturated, structurally incapable.

**Held in reserve as supporting evidence**: A (canonical chord) and
C (MFCC) — neither is strong enough as a primary signal, but A
gives a small lift on songs with broader chord vocabularies
(Blink-182 went 0.22 → 0.48 mean vs Sex On Fire), and C flags the
same Sex-On-Fire section-16 anomaly that D and F both flag. They
may earn a place as confidence boosters once the primary signals
prove out.

---

## 8. What this does NOT do

To be explicit about the boundary:

- No `SongFormClassifier`, `SongFormThresholds`, or clustering code.
- No bundle schema change. No new field reaches the JAM UI.
- No threshold for "this is a chorus" was set. We only inspected
  whether the SSM shape is informative.
- No live-song calibration. The two-song corpus is sufficient to
  shortlist signals; expanding the corpus is a Phase 0C
  precondition before any classifier is built.
- No connection drawn to the riff-vs-chord guidance-mode milestone
  (the `effervescent-twirling-neumann` plan). Song-form similarity
  and per-section guidance-mode are independent concerns; this
  study is exclusively about the former.

---

## 9. Open Questions for Phase 0C

1. **Corpus expansion**: at least 4 songs (preferably 6) covering
   distinct forms (verse-chorus, AABA, through-composed, riff-loop)
   need real-song bundles before D+F thresholds are calibrated.
   Suggested additions: a Beatles AABA, a Pink Floyd through-
   composed piece, a single-riff song (Seven Nation Army), and a
   classical/fingerstyle piece (Romance de Amor, Wish You Were Here
   intro).
2. **Section size sensitivity**: D's "0.00" pairs come from sections
   where vocals are absent on one side. This is a useful signal but
   also means very short sections will be noisy. Phase 0C should
   investigate a minimum-section-duration gate.
3. **Drum-tacit detection**: F flags drum-tacit intros and outros
   cleanly. Worth treating as a first-class feature rather than as
   a side-effect of the SSM.
4. **Composite weighting**: equal-weight D+F is the obvious first
   attempt. Other weightings (e.g. weight by per-song separability
   score) may be more robust.
5. **Anomaly cross-check**: D, F, and C all flagged Sex-On-Fire
   section 16 as different. A future investigation should check
   whether that section corresponds to an actual structural moment
   (bridge / breakdown) in the recording.

---

## 10. Artefacts

All under `backend/song_form_signal_discovery/`:

- `run_discovery.py` — standalone read-only script (no library
  imports from the runtime pipeline; reproducible from a fresh
  checkout).
- `corpus_coverage.json` — which sessions were eligible vs skipped
  and why.
- `per_song_signal_summary.json` — full statistics, ready for
  Phase 0C to ingest.
- `cross_song_ranking.csv` — flat ranking table (the source of §4.2).
- 12 heatmap PNGs — A/B/C/D/E/F × {sex_on_fire,
  whats_my_age_again}.
- 12 raw matrix JSONs — preserved so Phase 0C can re-compute
  derived metrics without re-running the audio pipeline.

This document is the formal Phase 0B deliverable. The next
research milestone is Phase 0C, which should be opened as a new
plan referencing this decision.
