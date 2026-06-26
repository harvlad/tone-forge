# Phase 0C — Corpus Expansion & Signal Stability

> **Status:** Research-only. No code in `backend/tone_forge/` was modified.
> No classifier, no thresholds, no labels, no UI. This document is a
> validation report for the Phase 0B finding that Vocal RMS (signal D)
> and Drum Density (signal F) are the strongest section-similarity
> signals available to ToneForge today.

---

## 1. Question this phase had to answer

Phase 0B (Decision A, two songs) concluded that D and F discriminate
section identity more reliably than chord (A), chroma (B), MFCC (C),
or instrumentation (E). Phase 0C asks the next-tier question:

> Do D + F generalise across multiple songs and multiple genres, or
> were Phase 0B's results an artefact of two indie/pop-punk songs?

Decision rubric (from the brief):

- **A** — D + F remain robust across an expanded, genre-diverse corpus.
  Proceed to classifier architecture design.
- **B** — D + F are genre-specific, unstable, or both. Additional
  signal discovery required.

---

## 2. Corpus coverage

### 2.1 Intended target corpus (from the brief)

| Slug                       | Genre target          | Bundle available?       |
|----------------------------|-----------------------|-------------------------|
| `sex_on_fire`              | indie rock            | ✅ 5 runs               |
| `whats_my_age_again`       | pop punk              | ✅ 2 runs (shared stems) |
| `stairway_to_heaven`       | classic rock          | ❌ corpus blocker        |
| `hotel_california`         | classic rock          | ❌ corpus blocker        |
| `wish_you_were_here`       | classic rock          | ❌ corpus blocker        |
| `romance_de_amor`          | classical solo guitar | ❌ corpus blocker        |
| `disco_of_doom`            | (project original)    | ❌ no bundle in history  |
| `simulated_life`           | (project original)    | ❌ no bundle in history  |
| `lets_make_it_pain`        | (project original)    | ❌ no bundle in history  |
| `jump_and_die`             | (project original)    | ❌ no bundle in history  |

### 2.2 Effective corpus actually analysed

| Song-run                              | Section count | Stems on disk |
|---------------------------------------|---------------|---------------|
| Sex On Fire (22ba2ff1)                | 20            | 6-stem ✅      |
| Sex On Fire (b640c78a)                | 20            | 6-stem ✅      |
| Sex On Fire (fcc38d5c)                | 20            | 6-stem ✅      |
| Sex On Fire (07e61e2a)                | 22            | 6-stem ✅      |
| Sex On Fire (126c9515)                | 22            | 6-stem ✅      |
| What's My Age Again? (6a32035b)       | 8             | 4-stem ✅      |
| What's My Age Again? (29b31695)       | 8             | 4-stem ✅      |

**Two distinct songs in two genres (indie rock + pop punk). Seven
song-runs total. The corpus is essentially Phase 0B's corpus with
duplicate runs.**

Coverage manifest: `backend/song_form_phase0c/corpus_coverage.json`.

### 2.3 Corpus-expansion blocker (root-cause summary)

Stairway, Hotel California, Wish You Were Here, and Romance de Amor
have YouTube URLs in `backend/tests/fixtures/song_trial_corpus.json`,
so re-analysis was attempted via the `/api/analyze-url` endpoint.

Two distinct failures stacked:

1. **Detection misclassifies acoustic-leaning intros as
   single-instrument.** `unified_pipeline.py:602` only runs Demucs
   when `detection.is_full_mix` is true OR
   `config.force_stem_separation` is true. With
   `analysis_mode: studio`, neither condition fires for these songs;
   the pipeline returns `detected_type: "guitar"` and skips Demucs.
   The bundle is produced with `stems_paths: None` and the section
   detector runs on a single-channel "other" stem.
2. **`analysis_mode: deep` (which forces stem separation) gets
   killed mid-run by the dev-server file watcher.** Demucs `htdemucs_6s`
   takes ~6–8 min per 300 s preview on Apple silicon. The FastAPI
   reloader (`WatchFiles`) recursively watches `backend/`, so any
   write into the working tree during a deep analysis tears the
   subprocess down. Two attempts at Stairway under `deep` mode both
   produced only `_other.wav` before being interrupted.

These are infrastructure problems, not signal problems. They prevent
us from answering the cross-genre question without first hardening
the ingestion path. The implications are spelled out in §5.

---

## 3. Per-song findings

All numbers below come from
`backend/song_form_phase0c/per_song_findings.json`. "Separability" is
`min(1.0, off_diag_std * 2.0)` — higher means the SSM has more
contrast between same-type and different-type sections.

### 3.1 Sex On Fire (most recent 20-section runs — 22ba2ff1, b640c78a, fcc38d5c)

| Signal          | mean  | std   | p10   | p50   | p90   | pairs ≥0.6 / ≥0.8 | sep   |
|-----------------|-------|-------|-------|-------|-------|-------------------|-------|
| **D vocal RMS** | 0.563 | 0.334 | 0.124 | 0.679 | 0.936 | 102 / 75          | **0.668** |
| **F drum density** | 0.675 | 0.286 | 0.244 | 0.763 | 0.943 | 133 / 78          | **0.572** |
| **C = ½D + ½F** | 0.619 | 0.245 | 0.270 | 0.708 | 0.896 | 103 / 59          | **0.490** |

All three runs produced **bit-identical** D/F/C statistics — the
section boundaries, stem audio, and feature extraction are
reproducible.

**Heatmap observations** (`C_composite_DF__sex_on_fire__b640c78a.png`):

- Intro (1) is visibly distinct from the rest of the song
  (similarities 0.11–0.37 against all verses).
- Outro (20) is also distinct (0.22–0.39 against verses).
- The 18 "verse" sections split into two visually coherent blocks:
  - Verses 2–3 form a tight pair (0.94 with each other, but 0.40–0.60
    against the rest of the verses). These are the quiet pre-build
    verses.
  - Verses 5–19 form a hot block (most pairwise similarities 0.70–0.95)
    with verse 13 as a notable outlier (0.55–0.78 against the cluster).
- Verse 4 is a singleton: 0.02 against intro, 0.15 against outro,
  0.29–0.52 against everything else.

D + F is plainly visualising arrangement structure that the section
detector's monolithic "verse" label collapses.

### 3.2 Sex On Fire (older 22-section runs — 07e61e2a, 126c9515)

| Signal          | mean  | std   | sep   |
|-----------------|-------|-------|-------|
| D vocal RMS     | 0.617 | 0.301 | **0.603** |
| F drum density  | 0.692 | 0.234 | **0.468** |
| C = ½D + ½F     | 0.655 | 0.219 | **0.438** |

Different section detector output (22 vs 20 sections) → slightly
different separability, but the ordering D > F > C is preserved and
the absolute values stay in the same band.

### 3.3 What's My Age Again? (6a32035b, 29b31695 — share the same stems dir)

| Signal          | mean  | std   | sep   |
|-----------------|-------|-------|-------|
| D vocal RMS     | 0.664 | 0.192 | **0.385** |
| F drum density  | 0.670 | 0.258 | **0.515** |
| C = ½D + ½F     | 0.667 | 0.186 | **0.372** |

**Heatmap observations** (`C_composite_DF__blink_182_what_s_my_age_again_of.png`):

- Verse / drop alternation is unmistakable. Drops (3, 5, 7) cluster at
  0.94–0.96 against each other. Verses (2, 4) cluster at 0.94 against
  each other and 0.79–0.83 against the drops.
- Intro (1) and buildup (8) are the structural odd ones (similarities
  in the 0.36–0.61 range).
- Verse 6 is the outlier verse — it scores 0.66–0.70 against the other
  verses and 0.68 against the drops, suggesting a partial transition
  passage that the section detector labelled "verse" but D + F treat
  as something between verse and drop.

Note that on this song **F is more discriminative than D** (0.515 vs
0.385), the opposite of the Sex On Fire ordering. This is a real
finding — vocal energy is more uniform across blink-182's sections
than drum onsets are — and it justifies keeping both signals rather
than collapsing to D alone.

### 3.4 Stairway / Hotel California / Wish You Were Here / Romance de Amor

**No findings.** See §2.3 — the bundles do not contain stem audio.

---

## 4. Cross-song stability

Full table at `backend/song_form_phase0c/cross_song_stability.csv`.

### 4.1 Run-to-run stability (same song, repeated analyses)

| Song                  | Runs | D sep range     | F sep range     | C sep range     |
|-----------------------|------|-----------------|-----------------|-----------------|
| Sex On Fire (20-sec)  | 3    | 0.668 / 0.668   | 0.572 / 0.572   | 0.490 / 0.490   |
| Sex On Fire (22-sec)  | 2    | 0.603 / 0.603   | 0.468 / 0.471   | 0.438 / 0.441   |
| Whats My Age Again    | 2    | 0.385 / 0.385   | 0.515 / 0.515   | 0.372 / 0.372   |

Run-to-run stability is **perfect** when the section detector
produces the same boundaries (Δ ≤ 0.003 across the 22-section pair,
where the difference is rounding in onset times). This rules out any
non-determinism in D + F themselves.

### 4.2 Cross-song stability (different songs, same signal)

| Signal       | Sex On Fire (20-sec) | Whats My Age | Δ        |
|--------------|----------------------|--------------|----------|
| D vocal RMS  | 0.668                | 0.385        | **0.283** |
| F drum density | 0.572              | 0.515        | 0.057    |
| C = ½D + ½F  | 0.490                | 0.372        | 0.118    |

D drops by ~40% relative across the two songs. F is far more stable
across songs (~10% relative). This is a meaningful signal-specific
observation: **D's strength depends on the dynamic vocal envelope of
the song** (Sex On Fire has loud-loud-quiet pre-builds; Whats My Age
Again has uniformly energetic vocals), while F is closer to a
"section identity" baseline because drum patterns change between
sections regardless of vocal dynamics.

The equal-weight D + F composite under-performs both individual
signals (0.490 < 0.572 < 0.668 on Sex On Fire; 0.372 < 0.385 < 0.515
on Whats My Age). This is unsurprising: averaging two signals
suppresses the dominant one. It is **not** an argument against using
both — it is an argument against the naïve equal-weight composite.
Any future classifier will need a per-song or per-stem-availability
weighting policy.

---

## 5. Failure cases

| Failure                                              | Where                                                | What it means for the signal study |
|------------------------------------------------------|------------------------------------------------------|------------------------------------|
| `analyze-url` returns `stems_paths: None` for guitar-led songs | `unified_pipeline.py:602` detection gate          | We cannot expand the corpus through the public API without first refactoring the detection→separation gate. |
| `analysis_mode: deep` Demucs job killed by file watcher | FastAPI `WatchFiles` recursion on `backend/`        | Long-running analyses are not safe to run while editing the working tree. Phase 0D / corpus expansion needs its own non-watched runner. |
| Section detector collapses 18 musically distinct "verses" of Sex On Fire into one label | `arrangement_sections` output                        | D + F heatmap is more granular than the labelling. This is evidence the section labels themselves are coarser than the underlying acoustic structure — and that D + F could later inform a re-labelling pass. Out of scope for 0C. |
| D drops 40% between Sex On Fire and Whats My Age Again | §4.2                                                | D's separability is song-dependent. Not a refutation of Phase 0B, but a constraint on any classifier that treats D as a fixed-strength signal. |
| Composite C = ½D + ½F under-performs both individual signals on every song-run | §4.2                                                | Equal weighting is not the right aggregator. A future classifier must learn (or be hand-set per stem-availability) weights — it cannot just average. |

---

## 6. Decision

**Decision: B — Additional infrastructure is required before D + F can be
claimed to generalise.** The signal evidence so far is encouraging,
not conclusive, and the deciding question (cross-genre stability)
remains unanswered for reasons that have nothing to do with the
signals themselves.

Specifically:

- D + F continue to produce visibly structured, intuition-matching
  SSMs on every song where stems are available (§3.1, §3.3). The
  heatmaps surface arrangement structure that the current section
  labels paper over.
- D + F are **perfectly reproducible** run-to-run (§4.1). Whatever
  noise they have is in the input, not the pipeline.
- **The cross-genre question is undecided.** Two indie/pop-punk
  songs is not a "multiple genres" corpus, no matter how many times
  we re-analyse them. The brief's success criterion was "multiple
  songs from multiple genres exhibit recognizable repeated structures
  and outliers using D + F alone" — we have multiple songs (2) and
  multiple genres (2), but only one data point per genre, and both
  genres are vocally driven. The Phase 0B null on chord-canonical
  could still re-appear on classical solo guitar (Romance de Amor)
  or chord-progression-heavy classic rock (Hotel California).
- D's separability varies by ~40% between the two available songs
  (§4.2). That is a meaningful sensitivity to song character. A
  classifier that consumes D needs to know this before fixed
  thresholds get baked in.
- The equal-weight D + F composite is the wrong aggregator (§4.2,
  §5). Any classifier built on D + F will need a smarter combiner —
  almost certainly per-stem-availability gated, possibly per-song
  calibrated.

### What "Decision B" means concretely (for Phase 0D scoping)

Phase 0D should not start designing classifier thresholds or
SongFormThresholds. It should do **one** thing:

> Build a corpus-expansion runner that bypasses the detection→
> separation gate and the FastAPI file watcher, so we can analyse
> Stairway, Hotel California, Wish You Were Here, Romance de Amor,
> and the four project-original songs end-to-end with htdemucs_6s
> stems on disk.

Concretely the blockers are:

1. A CLI or non-watched HTTP entry point that calls
   `unified_pipeline.run(...)` with a `PipelineConfig` that hard-sets
   `force_stem_separation=True` (and ideally
   `separate_stems=True`) regardless of detection, for jobs marked
   "corpus expansion." The dev server's `analysis_mode: deep` path
   already does this — it just isn't survivable next to the watcher.
2. A way to keep Demucs alive for 6–8 minutes while the rest of the
   repo is being edited. Either run the job from a fresh shell where
   the FastAPI reloader is disabled, or move the corpus-expansion
   runner into its own non-watched binary (e.g. a CLI under
   `backend/bench/` invoked directly, not via the API).

Once those two are in place, re-run this exact Phase 0C script
(`/tmp/phase0c_scripts/run_phase0c.py` — it makes no assumptions
about how the bundles arrived; it only reads `history.json` +
`stems_paths`) on the expanded corpus. **Then** we have the data to
choose A or B for real.

### What "Decision B" does NOT mean

It does not mean D + F should be discarded. The Phase 0B finding
still stands as the working hypothesis. It only means we have not
yet proved that hypothesis on enough genres to commit to a
classifier architecture.

---

## 7. Artefacts produced

All in `backend/song_form_phase0c/` unless noted:

- `corpus_coverage.json` — every song-run considered, with status
  (`included` / `skipped:no_stem_dir`) and stems-found list.
- `per_song_findings.json` — statistics per song-run for D, F, and
  the D+F composite (mean, std, p10/50/90, min, max, pairs ≥0.6,
  pairs ≥0.8, separability).
- `cross_song_stability.csv` — flat table of separability per song
  per signal, suitable for spreadsheet inspection.
- `D_vocal_rms__<slug>.{png,json}` — 7 heatmap PNGs and the raw
  NxN matrices.
- `F_drum_density__<slug>.{png,json}` — same, for drum density.
- `C_composite_DF__<slug>.{png,json}` — same, for the equal-weight
  composite.

Script: `/tmp/phase0c_scripts/run_phase0c.py` (kept outside the
working tree to avoid the dev-server reloader). Idempotent — safe
to re-run once the corpus is expanded.

---

## 8. Out of scope (explicit, to honour the brief)

This document does not propose, design, sketch, or hint at:

- A SongFormThresholds dataclass.
- A SongFormClassifier or any verse/chorus/drop/lead label rules.
- A weighting policy for D + F beyond noting that equal weighting is
  wrong.
- Any UI surface, JAM widget, ribbon, lane, or visualisation.
- Any modifications to `backend/tone_forge/analysis/`,
  `unified_pipeline.py`, or the existing chord / chroma / MFCC paths.
- The Riff-First / guidance-mode plan currently sitting in plan mode
  — that plan is downstream of corpus expansion completing.

Phase 0D will decide, with a real cross-genre corpus in hand,
whether to proceed to classifier design or to search for additional
signals.
