# MIDI Extraction Status Report

**Status:** Production-frozen baseline. Hybrid merge classified experimental.
**Last benchmark:** 16-sample bass benchmark (current internal suite).

---

## 1. Production Extraction Pipeline

The production pipeline is the **baseline** path. It is the only path
permitted in deployed extraction.

### Entry points
- Top-level: `backend/tone_forge/midi_extractor.py:1303` — `extract_midi(...)`
- Bass ensemble (production): `backend/tone_forge/midi/gpu_extractor.py:1260` —
  `extract_midi_bass_ensemble(..., use_hybrid_merge=False)`
- Lead ensemble (production): `backend/tone_forge/midi/gpu_extractor.py:1424` —
  `extract_midi_lead_ensemble(..., use_hca_for_polyphony=True, use_hybrid_merge=False)`
- Multi-pass scaffolding (used for cleanup): `backend/tone_forge/midi/extraction_pipeline.py`

### Production passes / stages
1. Monophonic detection — **pYIN** (DSP) as primary; **torchcrepe** as secondary for lead.
2. Polyphonic routing — **HCA** (Harmonic Cluster Analyzer) when `harm_ratio < 0.65` (lead only).
3. Count-based source arbitration (pYIN vs. torchcrepe / basic_pitch).
4. **Octave validation** against harmonic content (sub-harmonic correction).
5. **Chord-aware gap filling** for missing-note regions.
6. Profile-driven cleanup passes (harmonic suppression, delay cleanup, octave correction,
   subharmonic suppression, octave doubling, beat-grid filter, key conformity) — see
   `extraction_pipeline.create_extractor_for_profile()`.

### Current benchmark (bass, 16 samples)
| Metric                      | Baseline | Hybrid merge |
|-----------------------------|----------|--------------|
| Passing (≥ pass threshold)  | 7/16 (43.8%) | 2/16 (12.5%) |
| Average F1                  | 65.5%    | 36.8%        |
| Samples ≥ 80% F1            | 7        | —            |
| Samples at 100% F1          | 3        | —            |

---

## 2. Known Strengths

- **Strong on isolated monophonic bass.** pYIN + octave validation + gap filling
  yields 3 perfect (100% F1) extractions and 7 samples ≥ 80% F1 on the bass suite.
- **Octave-error resilience.** Harmonic re-validation reliably catches sub-octave
  detections that pYIN/torchcrepe produce on low-fundamental content.
- **Gap stability.** Chord-aware gap filling restores notes lost during silences
  and transient regions without exploding false positives.
- **Profile-driven cleanup is composable.** Cleanup passes are individually toggleable
  and ordered, so we can iterate without rewriting the pipeline.
- **HCA is effective on dense polyphony.** For `harm_ratio < 0.65` content, HCA
  outperforms basic_pitch on chord recovery.

## 3. Known Weaknesses

- **Hard ceiling on lead F1.** Internal lead performance trails bass significantly;
  prior roadmap analysis suggests 80% F1 is not reachable with this architecture
  on lead without a model upgrade.
- **No frame-level posterior fusion in production.** The hybrid merge experiment
  was intended to address this; it currently regresses.
- **No expressive features.** Velocity, articulation, micro-timing, and pitch-bend
  are not estimated with confidence; downstream reconstruction must not depend on
  them.
- **Brittle on heavily-effected sources.** Long reverbs/delays still leak through
  the cleanup passes for some samples.
- **Limited benchmark coverage.** 16 bass samples is a small evaluation surface;
  generalization beyond the in-distribution suite is unproven.

## 4. Outstanding Extraction Risks

- **Hybrid merge re-enablement.** Anyone toggling `use_hybrid_merge=True` will
  regress production. Mitigation: defaults are `False`, docstrings carry
  `[EXPERIMENTAL — DISABLED — KNOWN REGRESSION]`, and benchmark guard rails
  should run on PRs that touch `gpu_extractor.py`.
- **Lead extraction blocking reconstruction.** If reconstruction quality is
  gated on lead transcription accuracy, the current floor may be insufficient.
  This is the explicit question the retrieval validation work below is designed
  to answer.
- **Polyphonic content beyond HCA's comfort zone** (very wide voicings, fast
  chord changes) is not validated.
- **Drift between benchmark and real user content.** Production audio may
  contain mastering chains, stem bleed, or genres absent from the bench suite.
- **Cleanup pass interaction.** Some pass combinations can amplify errors;
  there is no automated regression check on individual pass toggles.

## 5. Recommended Future Work (only if extraction becomes the bottleneck)

These are deferred. Pursue only after retrieval/reconstruction evidence shows
extraction quality is the gating factor.

1. **Joint posterior fusion.** A redesign of `hybrid_merge` that operates on
   *aligned* posteriors from multiple detectors rather than ad-hoc segment
   stitching. Hypothesis: current regression is rooted in segment-boundary
   instability, not in the fusion concept.
2. **Lead-specific model.** A dedicated polyphonic-lead transcription model
   (e.g., transformer-based AMT) for `harm_ratio` ∈ [0.65, 0.85] content.
3. **Per-sample diagnostics dashboard.** Per-sample F1 deltas across pass
   toggles, with linked audio and piano-roll diff visualization.
4. **Expanded benchmark.** Triple the sample count and stratify by genre,
   key, tempo, and effect chain.
5. **Velocity / expression estimation.** Required if downstream reconstruction
   targets musical realism beyond pitch/timing.
6. **PR-time benchmark gate.** Auto-run the 16-sample suite on PRs that touch
   `tone_forge/midi/**` and block on F1 regression > 2 pp.

---

## 6. Hybrid Merge — Permanent Classification

- **Status:** Experimental.
- **Default:** Disabled (`use_hybrid_merge=False`) in
  `extract_midi_bass_ensemble` and `extract_midi_lead_ensemble`.
- **Known regression:** Yes — 2/16 vs 7/16 passing; 36.8% vs 65.5% avg F1.
- **Policy:** Code is retained for research only. Do not re-enable in
  production without (a) a re-run of the bass benchmark, (b) F1 parity or
  improvement, and (c) explicit sign-off referencing this document.
- **Code references:**
  - Implementation: `backend/tone_forge/midi/gpu_extractor.py:150`
    `hybrid_merge(...)`
  - Bass call site (gated, default off): `backend/tone_forge/midi/gpu_extractor.py:1260`
  - Lead call site (gated, default off): `backend/tone_forge/midi/gpu_extractor.py:1424`
