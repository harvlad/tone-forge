# Specialist Routing Validation Report

**Date:** 2026-07-26
**Status:** Complete
**Decision Gate:** A - SIMPLE BASS SPECIALIST

## Executive Summary

Validated hypothesis: **Bass → MT3, Everything else → Basic Pitch**

MT3 robustly dominates Bass transcription. Synth Lead routing rejected (insufficient sample size, CI not significant). Production recommendation: Route B.

---

## Routing Architectures Comparison

| Architecture | Recall | F1 | Octave Error | Notes |
|-------------|--------|-----|--------------|-------|
| A (BP everywhere) | 42.3% | 0.313 | 16.3% | Baseline |
| **B (MT3 Bass)** | **44.9%** | **0.342** | **11.9%** | **RECOMMENDED** |
| C (MT3 Bass+SynthLead) | 45.1% | 0.347 | 11.0% | Marginal gain |
| D (Best per class) | 45.1% | 0.347 | 11.0% | Same as C |

**Route B gains over baseline:** +2.6% recall, +0.029 F1, -4.4% octave error

---

## Bass Analysis

### Per-Stem Consistency
- MT3 wins: **6/8 stems (75%)**
- BP wins: 0/8 stems
- Ties: 2/8 stems
- Median improvement: +14.8%
- Mean improvement: +18.8%

### Bootstrap Confidence Intervals (1000 resamples, 8 stems)

| Metric | MT3 - BP | 95% CI | Significant? |
|--------|----------|--------|--------------|
| Recall | +19.0% | [+7.1%, +32.9%] | **YES** |
| F1 | +0.290 | [+0.137, +0.435] | **YES** |
| Octave Error | -44.7% | [-60.7%, -26.4%] | **YES** |

### Register Analysis

| Register | MIDI Range | GT Notes | BP Recall | MT3 Recall | MT3 Advantage |
|----------|------------|----------|-----------|------------|---------------|
| sub_bass | <36 | 0 | - | - | - |
| low | 36-43 | 0 | - | - | - |
| mid_low | 44-51 | 992 | 24.0% | 58.1% | +34.1% |
| mid | 52-59 | 2013 | 22.9% | 47.3% | +24.4% |
| high | 60+ | 816 | 2.9% | 12.4% | +9.5% |

### Octave Error Direction
- **BP:** 1623 octave errors (91% down = subharmonic confusion)
- **MT3:** 97 octave errors (balanced up/down)

### Routing Oracles

| Strategy | Recall | Precision | F1 |
|----------|--------|-----------|-----|
| BP only | 18.8% | 11.9% | 0.146 |
| MT3 only | 42.7% | 47.2% | 0.448 |
| Stem oracle | 42.9% | 41.6% | 0.422 |
| Note oracle | 47.3% | - | - |

**Stem oracle adds only 4.6% over MT3-only** - validates simple routing.

---

## Synth Lead Analysis

### Sample Size Warning
- Only **532 GT notes** across **3 stems**
- Insufficient for robust conclusions

### Bootstrap Confidence Intervals (1000 resamples, 3 stems)

| Metric | MT3 - BP | 95% CI | Significant? |
|--------|----------|--------|--------------|
| Recall | +3.4% | [-2.2%, +13.2%] | **NO** |
| F1 | +0.044 | [-0.013, +0.153] | **NO** |
| Octave Error | -22.5% | [-73.0%, +2.2%] | **NO** |

### Head-to-Head
- Both correct: 0
- BP only: 5
- MT3 only: 56
- Both wrong: 471

**Conclusion:** Synth Lead routing NOT justified by data.

---

## Organ Control Case

Organ selected as control: MT3 has lower recall but vastly better octave accuracy.

| Metric | BP | MT3 |
|--------|-----|-----|
| Recall | 20.3% | 12.9% |
| Precision | 9.6% | 35.9% |
| Octave Error | **53.8%** | **0.9%** |
| n_pred | 686 | 117 |

MT3 conservative but accurate. BP overpredicts with many octave errors.

---

## Oracle Gap Analysis

Total MT3-only correct notes: **1699**

### By Class
| Class | MT3-only | % of Gap |
|-------|----------|----------|
| Bass | 1088 | **64.0%** |
| Guitar | 236 | 13.9% |
| Piano | 189 | 11.1% |
| Synth Lead | 56 | 3.3% |
| Brass | 43 | 2.5% |
| Ensemble | 42 | 2.5% |
| Organ | 32 | 1.9% |
| Synth Pad | 10 | 0.6% |
| Pipe | 2 | 0.1% |
| Reed | 1 | 0.1% |

**Key insight:** 64% of oracle gap captured by Bass routing alone.

---

## Runtime Analysis

| Model | Mean Time | Std Dev | Relative |
|-------|-----------|---------|----------|
| Basic Pitch | 6.1s | 2.6s | 1x |
| MT3 | 168.3s | 81.3s | **27x** |

Bass stems ~11% of dataset, so MT3 cost is bounded.

---

## Production Routing

### Recommended: Route B

```
if demucs_stem_label == "bass":
    use MT3
else:
    use Basic Pitch
```

### Justification
1. MT3 robustly dominates Bass (75% stem win rate, significant CIs)
2. Simple architecture: Demucs label routes directly (no classifier)
3. Route C/D adds ~0.2% recall for significant complexity
4. MT3 runtime penalty only paid for Bass stems

### NOT Recommended: Synth Lead Routing
- CI not significant
- Requires separate classifier (not available from Demucs)
- Marginal gain doesn't justify complexity

---

## Decision Gate Classification

**Classification: A - SIMPLE BASS SPECIALIST**

Criteria met:
- MT3 wins >70% of stems for target class
- Stem oracle improvement <5% over pure MT3 routing
- Bootstrap CI significant at 95% level
- Single class routing via existing infrastructure (Demucs)

---

## Files

- Results JSON: `experiments/mt3_comparison/specialist_routing_validation.json`
- Validation script: `scripts/specialist_routing_validation.py`
- Comparison data: `experiments/mt3_comparison/comparison_results.json`

---

## Next Steps

1. Implement Route B in production transcription pipeline
2. Add Demucs bass stem detection to routing logic
3. Benchmark end-to-end latency with routing
4. Consider MT3 batch processing for bass stems
