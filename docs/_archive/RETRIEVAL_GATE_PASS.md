# Retrieval Gate Pass — Workhorse Classes

**Status:** PASSED for workhorse classes (bass, lead, pad, other).
**Date:** 2026-06-04.
**Embedding under test:** seg8-mean mel temporal pooling, mean-centered,
L2-normalised. Computed inline from rendered WAVs at 22.05 kHz,
`n_mels=128`, `fmin=20`, `fmax=11000`, segments split as
`np.array_split(mel_db, 8, axis=1)`, segment means concatenated to a
1024-dim vector, the corpus mean subtracted, then L2-normalised.

This document records the validation evidence and the decision to lift
retrieval as a blocker for reconstruction work on the workhorse classes.
It does **not** promote the seg8 embedding into production code yet
(production `StemFingerprint` is unchanged) — see "Scope" below.

---

## 1. Gate criteria

The gate has two halves, both of which must hold:

1. **Statistical stability** of the retrieval score under resampling.
2. **Human usefulness** of the top-5 list for reconstruction work, on a
   prepared 24-query rating set.

Workhorse-class threshold:

| Cohort                                    | Threshold                       |
|-------------------------------------------|---------------------------------|
| Workhorse (bass, lead, pad, other)        | `any_top5_usable >= 70%`        |
| Minor    (keys, fx, percussion)           | diagnostic only, no gate        |

The minor cohort is informational. Class-imbalance and label-noise
issues (see §4) make it unsuitable as a pass criterion at present.

---

## 2. V1 — stability validation

LOO retrieval on the 99-preset Analog corpus, seg8 embedding:

| Metric                              | Value                              |
|-------------------------------------|------------------------------------|
| Top-1 sound_type accuracy (n=99)    | 0.828                              |
| Wilson 95% CI                       | [0.742, 0.890], width 0.148        |
| K-fold sound_type (K=5,  200 reps)  | 0.811 ± 0.021                      |
| K-fold sound_type (K=10, 200 reps)  | 0.823 ± 0.012                      |
| K-fold sound_type (K=20, 200 reps)  | 0.826 ± 0.008                      |
| Top-1 category accuracy (n=99)      | 0.667, 95% CI [0.569, 0.752]       |

Bootstrap result (0.923 ± 0.023) was flagged biased: with-replacement
resampling produces duplicate rows that share cosine=1.0 with themselves
at non-diagonal positions, inflating the rate. Wilson + K-fold are the
authoritative numbers.

**Per-class breakdown (LOO point estimates):**

| sound_type   | n  | top-1 acc | notes                                  |
|--------------|----|-----------|----------------------------------------|
| bass         | 30 | 1.000     | workhorse; saturated                   |
| lead         | 26 | 1.000     | workhorse; saturated                   |
| pad          | 15 | 0.933     | workhorse                              |
| other        |  8 | 0.750     | workhorse; small n                     |
| keys         | 12 | 0.500     | minor cohort; corpus-density limited   |
| fx           |  6 | 0.000     | minor cohort; representation / labels  |
| percussion   |  2 | 0.000     | minor cohort; sample count too low     |

Workhorse-only weighted average: **76/79 = 0.962**.

**Conclusion (V1):** the seg8 result is stable under K-fold and well above
the workhorse threshold. The minor-cohort failures are not random
fluctuation — they reflect a structural class-imbalance / representation
issue that is treated separately (see §4).

---

## 3. V3 — human usefulness validation

24 stratified queries × top-5 neighbours = 120 rating items. Operator
rated each neighbour `would_start_from_this` (true/false/null) and each
query `any_top5_usable` (yes/no) plus `best_rank`.

**Workhorse cohort (n=19):**

| Metric                                  | Result                             |
|-----------------------------------------|------------------------------------|
| `any_top5_usable`                       | **19/19 = 100.0%**                 |
| Threshold                               | 70%                                |
| Gate decision                           | **PASS** (clears by 30 points)     |

**Best-rank distribution (workhorse):**

| Best rank   | Count | % of n=19 |
|-------------|------:|----------:|
| 1           |    10 |    52.6%  |
| 2           |     4 |    21.1%  |
| 4           |     2 |    10.5%  |
| 5           |     1 |     5.3%  |
| unspecified |     2 |    10.5%  |

**Per-rank hit rate ("would start from this"):**

| Rank | true | false | null | hit rate |
|------|-----:|------:|-----:|---------:|
| 1    |  13  |   5   |  1   |   72.2%  |
| 2    |   5  |  14   |  0   |   26.3%  |
| 3    |   4  |  15   |  0   |   21.1%  |
| 4    |   5  |  14   |  0   |   26.3%  |
| 5    |   1  |  18   |  0   |    5.3%  |

**Minor cohort (n=5):** 3/5 usable. Keys 3/3 usable (consistent with the
corpus-density-limited diagnosis); fx 0/1 usable (Zap); percussion 0/1
usable (Noise Hit Perc). These are diagnostic and do not gate.

**Conclusion (V3):** every workhorse query in the rating set yields at
least one credible starting point in its top-5. Retrieval is *useful*,
not merely accurate. The product question "does retrieval reduce
reconstruction effort?" is now answered with **directional yes** for
workhorse classes — the per-query rating measures perceived usefulness;
realised effort reduction will be measured by the reconstruction trial
harness (see RECONSTRUCTION_TRIAL_PLAN.md).

---

## 4. Remaining limitations

These are explicitly out of the gate decision; they bound the
applicability of the result.

### 4.1 FX (representation / taxonomy hypothesis open)

Per-class same-class top-5 fill rate: 10% vs ~6% random baseline. The
six fx-labeled presets scatter across nearly every other class — Movie
Pad retrieves pad neighbours, Rhythmic Iron retrieves bass neighbours,
Zap retrieves "other". Hypothesised mechanisms:

1. Label noise (some "fx" labels may be wrong).
2. Genuine representation gap (no transient / event-density features).
3. Class is acoustically incoherent (fx is a junk-drawer category).

A 6-item operator label audit is queued (`fx_label_audit_worksheet.json`)
and **must complete before** any fx-specific representation work begins.
Decision rule: if ≥ 3/6 fx items are relabeled away from fx, taxonomy
cleanup precedes feature engineering.

### 4.2 Percussion (insufficient sample count)

n=2 is too small to draw any conclusion. Cannot proceed to
representation vs. coverage diagnosis until n ≥ 10.

### 4.3 Keys (corpus-density limited)

43% same-class fill at n=12 (~4× random baseline). The embedding
recognises keys-to-keys when density allows. Expand to n=30 to test the
density hypothesis with falsifiable criteria:

- top-1 ≥ 0.75 → corpus expansion confirmed.
- top-1 < 0.55 → reclassify as representation problem.

### 4.4 Cross-instrument generalisation untested

V1/V3 evidence is on the Analog corpus only. The seg8 architecture has
not been tested on Operator, Wavetable, or Drift. Validation plan in
V2_CROSS_INSTRUMENT_PLAN.md.

### 4.5 Single-instrument LOO is a within-engine test

Every query and every neighbour in V1/V3 was rendered from an UltraAnalog
patch. The numbers describe the recommend-an-Analog-preset-given-Analog
flow. They do not describe arbitrary-input-audio retrieval.

### 4.6 Rank-1 alone is insufficient for product use

Per-rank hit rates show rank-1 is correct 72% of the time; the
best-rank distribution shows 3/19 workhorse queries had their best
starting point at rank 4 or 5. **Product implication: surface the
full top-5 in the UI; do not auto-select rank-1.**

---

## 5. Decision

The retrieval gate is PASSED for workhorse classes. Effective changes:

- Retrieval research is **deprioritised**. Further retrieval-metric
  improvements should not be undertaken until reconstruction trials
  surface a user-visible bottleneck.
- The next product question is **realised** usefulness: do reconstruction
  trials show measurable effort reduction when retrieval is available?
- Reconstruction testing on bass / lead / pad / other is **unblocked**.
- FX and percussion retrieval remain **blocked**; see §4 for the
  per-track conditions to lift those.

Production embedding (`stem_fingerprint.py:485-534`) is **unchanged**.
The seg8 result lives in evaluation tooling only. Promotion to
production requires: V2 cross-instrument PASS + reconstruction trials
showing seg8-driven retrieval improves outcomes. Premature promotion
risks regressing downstream pipeline stages that consume the existing
128-d vector without offsetting benefit.

---

## 6. Provenance

Reproducible evidence:

- Catalog source: `preset_catalog_output/catalog/catalog_analog.json`
  (99 presets, all Catalog Integrity Gate criteria passed —
  see `preset_catalog_output/retrieval/catalog_integrity_gate.json`).
- V1 script (inline): K-fold + Wilson + per-class breakdown.
  Output: `/tmp/v1_stability.txt` (transient).
- V3 input:
  `preset_catalog_output/retrieval/v3_top5_usefulness_rating.json`.
- V3 scorer: `backend/scripts/score_v3_usefulness.py`.
- V3 report:
  `preset_catalog_output/retrieval/v3_score_report.{json,md}`.

Document history:

| Date       | Change                                          |
|------------|-------------------------------------------------|
| 2026-06-04 | Initial gate pass record, workhorse cohort only |
