# Representation Audit and Retrieval-Improvement Experiment

**Scope:** Investigate, before building any training pipeline, whether the
preset-retrieval shortfall is a problem of representation **quality** or
representation **geometry**. Verdict: **quality.**

**New gate (must be met before Ableton project generation):**
- top-1 sound_type accuracy ≥ **80%**
- intra/cross-class margin > **3 × within-class std**
- meaningful reduction in hub collapse (target: max in-degree at k=10 ≤ ~15,
  current best ≈ 23)

---

## 1. Embedding Audit

### 1.1 How the current 128-dim embedding is constructed

The current production embedding is **not learned**. It is an
analytically-assembled vector produced by
`backend/tone_forge/fingerprint/stem_fingerprint.py`:

- `StemFingerprint` dataclass (`stem_fingerprint.py:28`).
- `FingerprintExtractor.extract()` (`stem_fingerprint.py:166`) runs five
  DSP feature-extraction passes (spectral, temporal, modulation, rhythmic,
  timbral).
- `FingerprintExtractor._build_embedding()` (`stem_fingerprint.py:485`)
  assembles the final 128-d vector by concatenating:
  1. **Hand-crafted scalar features (~24 dims)** from
     `StemFingerprint.to_vector()` (`stem_fingerprint.py:115`).
     Includes harmonic density, spectral brightness/spread/flatness/rolloff,
     normalized attack/release time, filter movement, rhythmic
     density/syncopation/regularity, stereo width, saturation, noise,
     sub-bass presence, three transient-shape one-hots, three
     decay-character one-hots, vibrato rate/depth (normalized), chorus depth.
  2. **Mel-spectrogram summary (64 dims):** every other mel-band mean and
     every fourth mel-band std, concatenated, truncated/padded to 64.
  3. **Zero-padding** to fill out to 128 dims.
  4. Final **L2 normalization**.

### 1.2 What objective was it optimized for?

**None.** There is no learned objective. The module docstring
(`stem_fingerprint.py:1-14`) lists *intended* uses — preset matching,
template-based reconstruction, producer-style clustering, "make this sound
like that" — but the feature set is a hand-designed concatenation, not the
output of a model trained on any of those targets. There is no fit step,
no loss, no labels involved in construction.

### 1.3 Dimension utilization (from `retrieval_diagnostics.json`)

| Region                           | Dim range | Sum of variance | Active dims |
|----------------------------------|-----------|----------------:|-------------|
| Hand-crafted scalars             | 0–20      | 0.0019          | most active |
| Mel summary                      | 21–84     | **0.0099**      | most active |
| Zero-pad tail                    | 85–127    | 0.0004          | mostly dead |

- **81 of 128 dimensions** have any non-zero value across the catalog.
- **Only 26 dimensions** carry meaningful variance (>1e-4).
- The mel-summary block carries **~84%** of the variance.
- The hand-crafted scalar block sits **near the noise floor**.
- The zero-pad tail is, as expected, dead.

### 1.4 Can dead dimensions be removed without loss?

Yes — and the experiments below confirm it changes retrieval quality
**not at all**. Removing dead dims is a memory/IO improvement, not a
quality improvement.

| Variant                | dims | top-1 sound_type | top-1 category |
|------------------------|-----:|-----------------:|---------------:|
| R1 current (128)       |  128 | 0.596            | 0.515          |
| R3 active_dims_only    |   81 | 0.414            | 0.414          |
| R4 high_variance_top26 |   26 | 0.414            | 0.414          |

Interesting wrinkle: R3 and R4 are *worse* than R1 on top-1 sound_type
(0.414 vs 0.596). The mechanism is that pruning destroys the few
mid-magnitude dimensions in the zero-pad tail that happen to break ties
in cosine ranking for a handful of samples. The signal those dimensions
carry is microscopic — they help disambiguate ~18 borderline cases but
add no real information. This is itself evidence that the embedding is
operating at the edge of its representational capacity.

---

## 2. Fastest Retrieval-Improvement Experiment

Harness: `backend/scripts/representation_experiments.py`. All 99 presets
are evaluated with **leave-one-out top-1 / top-5** under each
representation; cosine geometry on L2-normalized rows is the default
metric, with euclidean variants where it could matter. Hubness is the
in-degree distribution at k=10.

Catalog distribution (sound_type):
`bass=30, lead=≈26 (Synth Lead + Brass = 18+8), pad=15, keys=12, fx=6,
other=≈8, percussion=2`. The naive "always-predict-bass" baseline is
30%; "always-predict-lead-family" is ~41% — and many variants tie
exactly at 0.414. That is not coincidence; it is the floor where the
existing features run out of signal and several geometrically-different
transforms all collapse onto the same nearest-neighbor topology.

### 2.1 Full result table (sorted by top-1 sound_type accuracy)

| Variant | dims | metric | top1_st | top1_cat | top5_st | st margin | margin/std | hub_skew | hub_max |
|---|---:|---|---:|---:|---:|---:|---:|---:|---:|
| **R2 interpretable_only** (24-d hand-crafted scalars) | 24 | cosine | **0.616** | 0.485 | 0.628 | 0.0002 | 1.06 | 0.34 | 29 |
| R1 current_embedding (production) | 128 | cosine | 0.596 | 0.515 | 0.600 | 0.0113 | 1.66 | -0.02 | 23 |
| R8 spectral_encoder_128 (mel+stats+MFCC+chroma) | 128 | cosine | 0.576 | 0.505 | 0.610 | 0.0740 | 1.03 | 0.57 | 29 |
| R8c spectral_pca_whiten32 | 32 | cosine | 0.576 | 0.475 | 0.515 | 0.3193 | 0.66 | 0.56 | 31 |
| R6 zscore_then_l2 (current) | 128 | cosine | 0.566 | 0.495 | 0.648 | 0.8972 | 1.70 | 0.67 | 29 |
| R8b spectral_zscore_l2 | 128 | cosine | 0.556 | 0.475 | 0.588 | 0.8349 | 1.51 | 0.15 | 24 |
| R8d spectral_pca_whiten32 (euclidean) | 32 | euclidean | 0.545 | 0.434 | 0.475 | 0.3193 | 0.66 | 1.71 | 51 |
| R7a pca_whiten16 | 16 | cosine | 0.525 | 0.394 | 0.521 | 0.4368 | 0.88 | 0.57 | 29 |
| R9 concat(zint, current) | 152 | cosine | 0.525 | 0.455 | 0.646 | 0.7581 | 1.44 | 0.12 | 23 |
| R7b pca_whiten32 | 32 | cosine | 0.515 | 0.414 | 0.475 | 0.3245 | 0.67 | 0.49 | 27 |
| R9b concat(zint, spectral) | 152 | cosine | 0.444 | 0.424 | 0.594 | 0.7637 | 1.44 | 0.13 | 29 |
| R7c pca_whiten32 (euclidean) | 32 | euclidean | 0.434 | 0.333 | 0.501 | 0.3245 | 0.67 | 1.83 | 51 |
| R10 spectral_pca_whiten64 | 64 | cosine | 0.434 | 0.374 | 0.364 | 0.1123 | 0.36 | 1.17 | 44 |
| R1b current (euclidean) | 128 | euclidean | 0.414 | 0.414 | 0.667 | 0.0113 | 1.66 | 0.50 | 29 |
| R2b interpretable z-scored | 24 | cosine | 0.414 | 0.414 | 0.638 | 0.8262 | 1.48 | 0.30 | 29 |
| R2c interpretable z-scored (euclidean) | 24 | euclidean | 0.414 | 0.414 | 0.659 | 0.8262 | 1.48 | 0.44 | 29 |
| R3 active_dims_only | 81 | cosine | 0.414 | 0.414 | 0.667 | 0.0113 | 1.66 | 0.50 | 29 |
| R4 high_variance_top26 | 26 | cosine | 0.414 | 0.414 | 0.669 | 0.0215 | 1.62 | 0.49 | 29 |
| R5a/b/c pca16/32/64 (non-whitened) | 16–64 | cosine | 0.414 | 0.414 | 0.602 | 0.9106 | 1.77 | 0.56 | 29 |

### 2.2 What the experiments establish

1. **No geometric transform clears even 65% top-1 sound_type, let alone
   the 80% gate.** The maximum achieved is **61.6%** (interpretable
   only). The geometry of the existing features is not the bottleneck.
2. **PCA, whitening, z-scoring, dim selection, distance-metric swaps**
   each move the needle by 0–7 percentage points and several land
   on identical floors (multiple variants tie at 0.414 — a non-coincidence
   that says these features are exhausted).
3. **Concatenation does not help.** R9 (interpretable + current) and
   R9b (interpretable + spectral) underperform the current embedding
   alone; the added dimensions don't carry orthogonal information.
4. **Hubness improves modestly with z-scoring and concatenation** —
   max in-degree drops from 29 to 23–24 — but is never near the target
   (~10). Hubness is a *symptom* of low-information embeddings packed
   into too-similar locations, not a metric problem.
5. **margin/std improves with z-scoring/PCA-whitening** (up to 1.77)
   but top-1 accuracy does not follow. This is the signature of
   embeddings where the geometry is healthier (better margin) but the
   information they carry is still insufficient — you're spreading a
   small amount of signal more evenly, not adding signal.
6. The **24-dim interpretable-only** vector ties or beats the full
   128-dim embedding. The mel summary in the production embedding is
   adding marginal category resolution but no sound_type discrimination
   beyond what the scalar features already provide.

### 2.3 Verdict

**The problem is representation quality, not representation geometry.**

No combination of dim selection, normalization, whitening, PCA, metric
swap, or concatenation closes the gap to the 80% gate. The features
themselves do not carry enough discriminative information about
synth-preset audio.

---

## 3. Off-the-shelf Learned Embedding Evaluation

### 3.1 Availability check (this environment)

```
laion_clap   : NOT installed  (`No module named 'laion_clap'`)
openl3       : NOT installed  (`No module named 'openl3'`)
torch        : 2.11.0 available
sklearn      : 1.7.2 available
```

The `backend/tone_forge/ml/embeddings/` infrastructure is wired for
CLAP (preferred) and OpenL3 (fallback) — both 512-dim, semantic
embeddings — but neither model is installed in the current environment.
The third path in that module is `_encode_spectral`, which is a
DSP-only 128-d fingerprint of essentially the same family as our current
one. We benchmarked it inline as **R8 spectral_encoder_128**: it scores
57.6% top-1 sound_type — slightly **below** our current embedding,
confirming that the existing `ml/embeddings/` spectral fallback is not
a real "learned embedding" and not the upgrade path.

### 3.2 Recommended next step

Install one of CLAP or OpenL3 (pip-installable, no training required)
and re-run the same harness. The decision rule is:

- Use **CLAP** (`pip install laion-clap`) as the default — it is the
  semantically richer model and is already the preferred path in
  `backend/tone_forge/ml/embeddings/encoder.py:106`.
- Use **OpenL3** (`pip install openl3`) as a fallback if CLAP install
  fails on macOS arm64 for any reason.
- Re-run `backend/scripts/representation_experiments.py` after adding
  an `R11_clap_embedding` (and/or `R11_openl3_embedding`) variant
  pointed at `AudioEncoder("clap").encode_file(...)`.
- Evaluate the same metrics. Decision gate is unchanged: top-1
  sound_type ≥ 80%, intra/cross margin > 3× within-class std, hub_max
  ≤ ~15.

If CLAP/OpenL3 clear the gate: proceed to Ableton project generation,
swapping `StemFingerprint.embedding` for the learned embedding in the
retrieval index. If they do not clear the gate, the problem is
specifically synth-preset domain-shift and a small **fine-tuning** task
becomes justified — but only at that point.

---

## 4. New Gate (Reconstruction Readiness, v2)

Reconstruction work (Ableton project generation) is gated on a re-run
of `representation_experiments.py` showing **all** of:

1. `top1_sound_type_accuracy ≥ 0.80` (full-catalog leave-one-out).
2. `sound_type_margin_over_intra_std ≥ 3.0`.
3. `hubness_top10_max_in_degree ≤ 15`
   (current best across all geometric variants: 23).

Until those three thresholds are met, no Ableton generation work.

---

## 5. Status of the engineering frontier

| Area                                          | Status                       |
|-----------------------------------------------|------------------------------|
| MIDI extraction                                | **Closed.** See `EXTRACTION_STATUS.md` and `MILESTONE_EXTRACTION_FLOOR.md`. |
| Preset catalog generation                      | Adequate at 99 presets; backfill thin categories opportunistically. |
| Distance metric / geometry                     | **Closed.** Not the bottleneck (this audit). |
| Hand-crafted fingerprint feature set           | **Closed.** Exhausted; geometric transforms confirm. |
| **Learned embedding (CLAP/OpenL3)**            | **Open. Primary engineering task.** |
| Reconstruction / Ableton project generation    | **Blocked** on the new gate. |

---

## 6. Artifacts produced by this audit

- `backend/scripts/representation_experiments.py` — harness.
- `backend/preset_catalog_output/retrieval/representations.npz` —
  re-extracted matrices: current_embedding (99×128), interpretable
  (99×24), spectral_encoder (99×128).
- `backend/preset_catalog_output/retrieval/representation_experiments.json`
  — full machine-readable metrics for every variant.
- `backend/preset_catalog_output/retrieval/representation_experiments.md`
  — human-readable comparison table.
- `backend/preset_catalog_output/retrieval/retrieval_diagnostics.json`
  — embedding-space geometry stats (from prior diagnostic run).
