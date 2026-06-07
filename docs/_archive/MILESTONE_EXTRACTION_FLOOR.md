# Milestone: Extraction Floor Achieved

**Status:** ACHIEVED. Extraction is frozen at the current baseline.
**Decision owner:** Project lead.
**Supersedes:** Any prior plan to land hybrid merge as the production extractor.

---

## What this milestone declares

The MIDI extraction subsystem has reached a usable floor. Further engineering
investment in extraction is **paused** until downstream evidence (retrieval and
reconstruction) demonstrates that extraction quality — not retrieval, ranking,
or reconstruction — is the primary blocker to the end-to-end product.

This is a deliberate scoping decision, not an admission that extraction is
"done." It is "good enough to validate the next stage."

---

## Current F1 performance (bass benchmark, 16 samples)

| Metric                     | Value     |
|----------------------------|-----------|
| Passing (≥ pass threshold) | 7/16 (43.8%) |
| Average F1                 | 65.5%     |
| Samples ≥ 80% F1           | 7         |
| Samples at 100% F1         | 3         |

Lead F1 is tracked separately and is materially lower; prior analysis suggests
it is architecturally bounded without a model upgrade.

## Why the baseline pipeline is the chosen production path

1. **It is the strongest measured configuration.** Every alternative we have
   evaluated to date (notably hybrid merge) underperforms it on the benchmark
   suite.
2. **It is composable.** Each pass (octave validation, gap filling, profile
   cleanup) can be toggled independently, which keeps the surface area for
   regressions small.
3. **It is the only configuration with multiple 100% F1 samples,** indicating
   that the architectural ceiling for at least some content categories is at
   or above the target.
4. **It is the configuration that produces our 3 known perfect extractions,**
   giving us trustable anchor cases for downstream validation work.
5. **It carries the lowest maintenance cost.** It uses widely understood DSP
   primitives (pYIN, harmonic re-analysis) and avoids the segment-stitching
   fragility we observed in hybrid merge.

## Why hybrid merge is deferred

1. **Direct regression evidence.** 2/16 passing and 36.8% avg F1 vs. baseline
   7/16 and 65.5%.
2. **Root cause is not yet isolated.** We have hypotheses (segment-boundary
   thrash, posterior misalignment) but no fix with measured uplift.
3. **The fix is open-ended.** A correct hybrid architecture likely requires
   redesigning fusion to operate on aligned posteriors, not on stitched
   note-level outputs — a multi-week effort with no committed payoff.
4. **Opportunity cost is concrete.** Engineering hours are better spent
   answering the retrieval / reconstruction question, because if retrieval is
   the bottleneck, extraction improvements would not unblock the product.
5. **It is preserved, not deleted.** The code remains in
   `backend/tone_forge/midi/gpu_extractor.py:150` for future research; only
   defaults and documentation have been changed.

## Exit criteria for revisiting extraction work

We will reopen extraction investment only if **at least one** of the following
holds after the retrieval/reconstruction validation:

- Retrieval is demonstrably gated by extraction noise (e.g., neighbor quality
  improves substantially when run against ground-truth MIDI vs. extracted MIDI).
- Reconstruction outputs are perceptually unacceptable in a way traceable to
  pitch/timing errors, not preset selection or arrangement.
- A new benchmark surfaces a content category where baseline performance is
  catastrophic (sub-30% F1) and is in our addressable user content distribution.

Until then: **no further hybrid merge work, no further detector research.**

## Cross-references

- Status report: `backend/EXTRACTION_STATUS.md`
- Hybrid merge implementation (experimental, disabled):
  `backend/tone_forge/midi/gpu_extractor.py:150`
- Production bass entrypoint:
  `backend/tone_forge/midi/gpu_extractor.py:1260`
- Production lead entrypoint:
  `backend/tone_forge/midi/gpu_extractor.py:1424`
- Prior roadmap (pre-freeze): `backend/EXTRACTION_ROADMAP.md`
