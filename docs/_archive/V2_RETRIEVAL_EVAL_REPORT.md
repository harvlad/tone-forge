# V2 Retrieval Evaluation — Report

**Date:** 2026-06-06
**Scope:** Live 12 Standard preset universe (4 engines, 268 presets)
**Status:** V2-H1 PASS (all engines) · V2-H2 PASS (overall)

## 1. Corpus

| Engine    | Presets | WAVs OK | Clip warnings | SHA verified |
|-----------|--------:|--------:|--------------:|:------------:|
| Analog    |      99 |      99 |             9 | yes          |
| Drift     |      89 |      89 |             3 | yes          |
| Collision |      63 |      63 |             3 | yes          |
| Electric  |      17 |      17 |             1 | yes          |
| **Total** | **268** | **268** |        **16** | **yes**      |

- Audio root: `preset_catalog_output/audio_v2/` (mono-decoded to 48 kHz for SHA).
- All decoded-audio SHA-1s in catalogs match the source WAVs (full integrity
  chain `.adv → .als → .wav → decoded` verified).
- 16 presets render hot enough to clip (peak ≥ 0.999); recorded as warnings,
  not failures. Fingerprints still extract cleanly.
- Suite-only engines (Operator, Wavetable, Meld, Tension) are excluded — the
  pilot demonstrated that Live 12 Standard reports "instrument not available"
  and the M4L recorder never produces a staging WAV. The Suite-only universe
  (~981 presets) is out of scope for this V2 cycle.

## 2. Catalog construction

Builder: `scripts/build_preset_catalog_v2.py`
- Reuses `extract_preset_fingerprint(wav, preset, als_path=...)` from
  `tone_forge/preset_catalog/catalog_builder.py:146` for schema parity with V1.
- Output:
  - `preset_catalog_output/catalog/catalog_<engine>_v2.json` (per-engine)
  - `preset_catalog_output/catalog/catalog_v2.json` (union, 268 fingerprints)
- 0 missing WAVs, 0 fingerprint extraction errors across all 268 presets.

## 3. Corpus validation

Validator: `scripts/validate_v2_corpus.py`
- Hard-fail gates: WAV missing, decode failure, silent (RMS < 1e-3),
  non-finite features, decoded-audio SHA mismatch.
- Warning gates: clipping (peak ≥ 0.999), unexpected duration, missing
  provenance fields, fingerprint collision.
- Result: **268/268 OK · 0 failures · 16 warnings · 0 SHA mismatches.**

Reports:
- `preset_catalog_output/catalog/v2_corpus_validation.json`
- `preset_catalog_output/catalog/v2_corpus_validation.md`

## 4. V2-H1 — Within-engine LOO retrieval

For each preset P in engine E, query catalog E with P's 8-feature fingerprint
(self-match auto-skipped by `PresetCatalog.find_similar`). A query "hits" the
sound-type label at top-K if any of the top-K neighbours share P's
`sound_type`. Threshold: top-5 hit rate ≥ 60% per engine.

| Engine    |   N | Top-1 hit |  Top-5 hit | Pass |
|-----------|----:|----------:|-----------:|:----:|
| Analog    |  99 |    57.6 % |    83.8 %  | PASS |
| Drift     |  89 |    73.0 % |    87.6 %  | PASS |
| Collision |  63 |    82.5 % |    92.1 %  | PASS |
| Electric  |  17 |   100.0 % |   100.0 %  | PASS |

### Sound-type breakdown (top-5 hit rate)

| Engine    | bass    | fx    | keys     | lead    | other     | pad      | percussion |
|-----------|---------|-------|----------|---------|-----------|----------|------------|
| Analog    | 28/30 (93%) | 2/6 (33%) | 11/12 (92%) | 25/26 (96%) | 8/8 (100%) | 9/15 (60%) | 0/2 (0%) |
| Drift     | 13/13 (100%) | 0/3 (0%) | 17/20 (85%) | 8/8 (100%) | 23/24 (96%) | 17/21 (81%) | — |
| Collision | 3/4 (75%) | 0/3 (0%) | 9/9 (100%) | — | 46/47 (98%) | — | — |
| Electric  | — | — | 17/17 (100%) | — | — | — | — |

Weak spots: `fx` sound-type (only 0–33% recall) and Analog percussion (2
presets, both miss). These categories have very small sample sizes; not a
blocker, but flagged for V3.

## 5. V2-H2 — Cross-engine LOO retrieval

For each preset P, query the union catalog (268 presets). Two metrics:

- **Same-engine bias** = mean(`|top-5 from P.engine| / 5`). Lower is more
  diverse. Threshold: ≤ 70%.
- **Cross-engine top-5 hit rate** = fraction of queries whose top-5 contains
  at least one preset from a *different* engine that shares P's sound_type.
  Threshold: ≥ 50%.

Overall (N = 268):

| Metric                     | Value  | Threshold | Pass |
|----------------------------|-------:|----------:|:----:|
| Mean same-engine bias      | 45.2 % | ≤ 70 %    | PASS |
| Cross-engine top-5 hit-rate| 65.7 % | ≥ 50 %    | PASS |

Per-engine breakdown:

| Engine    |   N | Same-engine bias | Cross-engine top-5 hit |
|-----------|----:|-----------------:|-----------------------:|
| Analog    |  99 |          52.9 %  |                48.5 %  |
| Drift     |  89 |          37.1 %  |                80.9 %  |
| Collision |  63 |          43.8 %  |                63.5 %  |
| Electric  |  17 |          48.2 %  |                94.1 %  |

Reading:
- **Drift** generalises best (lowest bias, highest cross-engine recall). Its
  fingerprint distribution overlaps both Analog (subtractive) and Collision
  (resonant) regions of feature space.
- **Analog** is the hardest case: its `bass`/`lead`/`pad` clusters are dense
  and self-similar, pulling top-5 toward in-engine neighbours. Cross-engine
  recall (48.5%) sits just below the global mean but the engine still passes
  the overall H2 thresholds.
- **Electric** is keys-only (17/17 presets are `keys`). Its 94.1%
  cross-engine recall is largely a property of having matching `keys`
  presets in Analog (12), Drift (20), and Collision (9).

## 6. Acceptance summary

| Hypothesis | Result | Notes                                                |
|------------|--------|------------------------------------------------------|
| V2-H1      | PASS   | All 4 engines exceed the 60% top-5 threshold by ≥24pp |
| V2-H2      | PASS   | Bias 45.2% (<<70%); cross-engine recall 65.7% (>50%)  |

## 7. Reproducibility

```bash
# 1. Build V2 catalogs from the 268-preset audio corpus
python3 scripts/build_preset_catalog_v2.py \
    --instruments Analog Drift Collision Electric \
    --audio-dir preset_catalog_output/audio_v2 \
    --als-dir   preset_catalog_output/als_v2 \
    --catalog-dir preset_catalog_output/catalog

# 2. Validate the corpus (SHA-1 chain + clipping / duration / silence gates)
python3 scripts/validate_v2_corpus.py \
    --catalog-dir preset_catalog_output/catalog \
    --audio-dir   preset_catalog_output/audio_v2

# 3. Run V2-H1 (within-engine) and V2-H2 (cross-engine) LOO retrieval eval
python3 scripts/retrieval_eval_v2.py \
    --catalog-dir preset_catalog_output/catalog \
    --report-dir  preset_catalog_output/catalog
```

Artifacts:
- `preset_catalog_output/catalog/catalog_v2.json`
- `preset_catalog_output/catalog/v2_corpus_validation.{json,md}`
- `preset_catalog_output/catalog/v2_retrieval_eval.{json,md}`

## 8. Next steps (not in this V2 cycle)

- **Calibrate `fx` and `percussion`**: re-render with longer tails / different
  MIDI sequences to lift those buckets out of <40% recall.
- **Reduce clipping**: apply a small post-render headroom check or master
  bus trim to bring the 16 clipped presets below 0.99 peak without changing
  perceived tone.
- **Suite engines** (Operator, Wavetable, Meld, Tension) require a Live Suite
  license or a separate render host; deferred until that is available.
- **V2-H3 (human rating)**: optional perceptual validation pass to confirm
  the cosine/Euclidean retrieval ranking matches musician judgement on a
  sampled subset.
