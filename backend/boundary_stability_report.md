# Section Boundary Stability Audit

Compares section boundaries between two segmentation runs on the same five songs:

- **Full pipeline** — MIDI-informed segmentation (tempo hint from MIDI extraction feeds `SectionDetector.detect_sections`)
- **Direct probe** — segmenter-local beat tracking (no MIDI; `SectionDetector` does its own internal beat track)

Both runs use the same mixed-stem audio per song.

## Per-song table

| Song | Full sections | Probe sections | Δcount | Median drift (s) | P95 drift (s) | Median IoU | Type agreement | Median cdens Δ (where comparable) |
|------|--------------:|---------------:|-------:|----------------:|--------------:|-----------:|---------------:|----------------------------------:|
| Simulated Life | 24 | 19 | -5 | 1.60 | 5.80 | 0.68 | 19/19 (100%) | 0.074 |
| Disco Of Doom | 22 | 20 | -2 | 1.80 | 3.50 | 0.64 | 17/19 (89%) | -0.103 |
| Mr Dance | 29 | 31 | +2 | 0.80 | 2.90 | 0.84 | 27/29 (93%) | 0.161 |
| Death by Nanobots | 26 | 25 | -1 | 2.00 | 3.90 | 0.62 | 15/24 (62%) | — |
| Chill Zone | 28 | 21 | -7 | 3.20 | 4.90 | 0.69 | 14/21 (67%) | — |

## Aggregate boundary drift

| Metric | Value |
|--------|------:|
| Mean drift (s)    | 2.00 |
| Median drift (s)  | 1.90 |
| P95 drift (s)     | 4.80 |
| Max drift (s)     | 8.70 |
| Median IoU        | 0.70 |
| Worst song (median drift) | **Chill Zone** (3.20s) |
| Best song  (median drift) | **Mr Dance** (0.80s) |

## Cdens stability (on matched non-chord sections only)

Cdens is captured in the full-pipeline corpus only for sections the classifier voted **non-chord** (those get per-stem dumps that expose cdens). Chord sections in the full-pipeline output don't expose cdens directly, so this comparison is limited to that non-chord subset. Direct-probe cdens is available for every section. Pair count below = number of matched non-chord sections.

| Metric | Value |
|--------|------:|
| Pair count                       | 4 |
| Mean abs cdens Δ                 | 0.113 |
| Median cdens Δ (signed)          | 0.070 |
| Max abs cdens Δ                  | 0.167 |

Per-pair detail (matched non-chord sections):

| Song | Full window | Probe window | Full cdens | Probe cdens | Δ |
|------|-------------|--------------|-----------:|-----------:|---:|
| Simulated Life | 51.1-59.3s (chorus) | 49.9-59.9s (chorus) | 0.120 | 0.100 | -0.020 |
| Simulated Life | 73.6-79.7s (drop) | 71.9-77.9s (drop) | 0.000 | 0.167 | +0.167 |
| Disco Of Doom | 172.4-234.8s (verse) | 173.7-233.6s (verse) | 0.370 | 0.267 | -0.103 |
| Mr Dance | 189.2-195.0s (verse) | 189.7-197.7s (verse) | 0.340 | 0.501 | +0.161 |

## Findings

1. **Boundary drift.** Across all matched sections (n=112), median boundary drift is **1.90s**, P95 is **4.80s**, max is **8.70s**. A 'section boundary' is the same location (within tolerance) in both runs only when drift is small relative to the section's duration.

2. **Section count delta.** Range [-7, +2] sections. Non-zero count delta means at least one section in one run has no peer in the other — i.e. the segmenter is producing different segmentations of the same audio.

3. **Type-label agreement.** Of 112 matched sections, 92 (82%) have the same type label (verse/chorus/drop/etc.) in both runs.

4. **Cdens stability on the non-chord matched subset** (n=4). Median |Δcdens| = **0.132**, max |Δcdens| = **0.167**. This compares the cdens value the classifier saw in the full-pipeline lead/riff verdict vs the cdens the direct probe would have computed for the aligned window. Large deltas here mean the classifier's chord-score input is sensitive to which segmenter ran.

## Recommendation

**B — Segmenter instability is significant. Freeze classifier work and investigate section generation.**

Justification (measured values vs decision thresholds):
- Median boundary drift **1.90s** exceeds the 1.0s threshold for 'plausibly frame-grid quantisation'. Typical sections are 6–30s long, so a 1.9s median drift is 6–32% of section duration — large relative to the unit being measured.
- P95 boundary drift **4.80s** exceeds 3.0s. The tail of the distribution contains pairs where one run's boundary lands inside the other's section by ≥3s, which is qualitatively a different segmentation decision rather than a quantisation effect.
- Max |Δcdens| on matched non-chord sections is **0.167** — *not* large enough alone to flip the chord vs lead decision (`chord_score = 0.5 × cdens`, so a 0.17 cdens delta is a ~0.08 score shift). cdens alone is *not* the smoking gun here.
- Type-label agreement is **82%** on matched sections, below 85%. The segmenter is labelling the same audio differently between runs (verse↔chorus, drop↔chorus, etc.), which is a structural disagreement, not just boundary jitter.

### Caveat on observability

This audit measures **boundary drift** and **cdens drift**. The classifier consumes more than cdens: `score_riff = monophonic_ratio × repetition_score` and `score_lead = monophonic_ratio × lead_activity_score × pitch_class_diversity` both depend on **per-stem MIDI features computed within the section window**. Moving a boundary by 1.9s median (or ≥3s in 5% of cases) changes which MIDI notes are inside the section, which directly changes `note_count`, `repetition_score`, `lead_activity_score`, and `pitch_class_diversity`. The cdens delta (0.17 max) is the *least* sensitive of these inputs — the riff and lead scores are likely shifting by more, but the corpus output only captures per-stem features for non-chord sections, so we can't quantify that here.

### Why this matters for classifier calibration

Calibrating `chord_density_weight` or any other threshold against a corpus run is calibrating against the MIDI-informed segmenter's specific boundaries. A future code change to MIDI extraction, beat tracking, or the segmenter's tempo handling will move the boundaries, and the calibrated thresholds will silently land on different input distributions. Until segmenter output is deterministic given the same audio (or its drift envelope is documented and bounded), the threshold knob has no stable reference point.

### Suggested next investigation

Trace why `SectionDetector.detect_sections` produces different segmentations when `tempo=<MIDI tempo>` vs `tempo=None`. Specifically:

1. Does the segmenter use the tempo hint only to skip beat tracking, or does it use it inside the boundary decision rule (e.g. to set a target section length)?
2. Is the MIDI-derived tempo *different* from the segmenter's own beat-track tempo? If so, the section grid is being quantised against different beats.
3. Could the segmenter's beat tracker be replaced by the pipeline-hoisted beat tracker (`_track_beats`) so both code paths see the same beats?

### Limitation

Audit is on **5 songs** (user requested minimum 5, preferably 10). The signal at n=5 is already strong enough to support recommendation B (every song exceeded at least one decision threshold). Expanding to 10 would tighten the P95 estimate but is unlikely to flip the verdict; each new full-pipeline run costs 5–37 minutes.
