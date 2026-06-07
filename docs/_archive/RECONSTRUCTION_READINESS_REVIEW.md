# Reconstruction Readiness Review

**Inputs:** 99 Analog presets, 128-dim `StemFingerprint` embeddings,
20-query top-5 retrieval sample + full 99-preset leave-one-out sweep.
**Question:** Is the current fingerprint space sufficient to drive
preset retrieval for reconstruction, or is more work required first?

---

## 1. Headline numbers

| Measurement (k=5)                                | 20-query sample | Full catalog (LOO) |
|--------------------------------------------------|----------------:|-------------------:|
| Top-1 category accuracy                          | 50.0%           | **51.5%**          |
| Top-1 sound_type accuracy                        | 65.0%           | **59.6%**          |
| Average category hit-rate over the top-5         | 49.0%           | —                  |
| Average sound_type hit-rate over the top-5       | 62.0%           | —                  |

For reference, the catalog distribution is dominated by Bass (30/99 = 30%);
a category-prior baseline (always predict "Bass") would score ~30% top-1
category accuracy. The embedding beats that, but only modestly.

**Retrieval success rate** (defined as top-1 sharing the query's
sound_type) is **~60%** at full catalog scale.

## 2. Why retrieval is mediocre: the embedding space is collapsed

Pairwise cosine over all 99 presets:

| Statistic    | Value       |
|--------------|------------:|
| min          | 0.9703      |
| 5th pctile   | 0.9703      |
| median       | 0.9893      |
| 95th pctile  | 1.0000      |
| max          | 1.0000      |
| mean ± std   | 0.988 ± 0.009 |

Every preset sits within a 0.03 cosine band of every other preset. The
table in `retrieval_report.md` shows similarity rounded to 4 decimals,
which is why all values display as `1.0000`; the *true* fifth-decimal
spread is the only signal we have.

Intra- vs cross-class separation is correspondingly tiny:

| Grouping   | intra mean | cross mean | margin (intra − cross) | std (both ~) |
|------------|-----------:|-----------:|-----------------------:|-------------:|
| category   | 0.9965     | 0.9860     | **+0.0106**            | ~0.008       |
| sound_type | 0.9967     | 0.9854     | **+0.0113**            | ~0.008       |

The class-discriminative signal margin (~0.011) is barely larger than the
within-class standard deviation (~0.007–0.009). That is exactly the
regime where top-1 accuracy floats in the 50–60% band — there *is* a
signal, but it is not robust.

## 3. Where the variance lives (dimension utilization)

The 128-dim embedding is constructed as:
- **dims 0–20:** ~21 hand-crafted scalar features
- **dims 21–84:** 64-dim mel-spectrogram summary (every other mean band,
  every 4th std band)
- **dims 85–127:** zero-padded tail

Variance per block:

| Block                       | Sum of variances |
|-----------------------------|-----------------:|
| Hand-crafted features (0–20) | 0.0019           |
| Mel summary (21–84)         | **0.0099**       |
| Zero-pad tail (85–127)      | 0.0004           |

- **81 of 128 dimensions** have any non-zero values across the catalog.
- **Only 26 dimensions** carry meaningful variance.
- The mel-spectrogram summary is doing ~84% of the discriminative work;
  the hand-crafted timbral features are effectively at the noise floor.

## 4. Major failure modes observed

From `preset_catalog_output/retrieval/retrieval_report.md`:

1. **Hub presets dominate retrieval.** Bass queries almost always return
   the same 5 neighbors (PowerGrid Synbass, Muted Wow Bass, Dual OSC
   Plastic Bass, Dual OSC Buzz Octaves Bass, Uniform Bass / Dalmation Bass).
   Lead/brass queries collapse to the same 5 leads regardless of timbre.
   This is the classic *hubness* pathology of high-dimensional, low-margin
   embeddings.
2. **Category confusion across `lead`/`bass` boundaries.** "Blowout Bass
   Hit" returns Synth Lead and Brass presets at rank 1 with cosine = 1.0;
   "Brindali Brass" returns Synth Lead at ranks 1–2.
3. **Percussion queries route to keys/effects.** "Noise Hit Perc" and
   "Kick Complex Perc" return Effects, Synth Rhythmic, Synth Keys, and
   Guitar & Plucked at the top. The catalog has only 2 percussion
   presets, so this is partly a **coverage** problem layered onto
   fingerprint weakness.
4. **Pluck queries collapse to bass.** "Unjazzy Pluck" (Guitar &
   Plucked) returns all 5 bass hubs. Plucks share spectral envelope
   shape with bass under the current feature set.
5. **Clean intra-category wins exist.** "Dual OSC Buzz Octaves Bass",
   "Muted Wow Bass", "Deep Air Bass", "Ratchet Zag Bass", and "Square
   Percussive Bass" all return 5/5 Bass neighbors. Bass is the one
   category where the embedding behaves well — likely because the mel
   summary captures sub-100 Hz energy effectively.

## 5. Failure attribution

Per the original four hypotheses:

| Hypothesis                       | Verdict | Evidence |
|----------------------------------|---------|----------|
| Insufficient catalog coverage    | **Contributing** for small categories (Synth Percussion = 2, Synth Rhythmic = 2, Synth Misc = 3, Guitar & Plucked = 3). Not the dominant cause. |
| Fingerprint limitations          | **Primary cause.** Effective dimensionality ~26; intra/cross margin ~0.011 vs std ~0.008; mel summary is 84% of the signal; hand-crafted timbral features are near zero variance. |
| Distance metric issues           | **Not the cause.** Cosine on L2-normed vectors is appropriate; row norms are exactly 1.0. The geometry, not the metric, is the problem. |
| Preset categorization issues     | **Not the cause** for this analysis (labels are ground truth). Category granularity may matter downstream (`category` vs `sound_type` differ by ~5 pp). |

## 6. Recommendation

**Do NOT proceed directly to Ableton project generation with the current
fingerprint space.** A 50–60% top-1 retrieval rate will produce reconstructions
that are categorically wrong on roughly 4 out of every 10 user inputs, and
within-category neighbors are dominated by 5–6 hub presets, so even the
"correct" results will feel monotonous.

**Also do NOT reopen extraction work.** This validation explicitly shows
extraction quality is *not* the limiting factor — the limiting factor is
the embedding's representational power on synth-preset audio. Hybrid merge
and detector research would not improve retrieval.

### Recommended next step (lowest-effort, highest-information)

A **fingerprint upgrade**, scoped narrowly:

1. **Replace the mel summary block with a learned audio embedding** for
   the Analog catalog. Existing infra already supports this — see
   `backend/tone_forge/ml/embeddings/` which has a CLAP/OpenL3 path with
   FAISS. Re-run this exact validation harness against the CLAP/OpenL3
   embeddings to measure uplift on the same 20-query suite and the same
   full-catalog leave-one-out.
2. **Decision gate:** If learned-embedding top-1 sound_type accuracy
   reaches **≥ 80%** with intra/cross margin > 3× std, proceed to
   Ableton project generation. Otherwise, scope an embedding-training
   task before any reconstruction work.
3. **Coverage backfill** for Synth Percussion, Synth Rhythmic, Synth
   Misc, and Guitar & Plucked (each ≤ 3 presets today) is cheap and
   should be done in parallel; it does not require model work.

### What to deprioritize

- Any further hybrid merge investigation (already documented in
  `EXTRACTION_STATUS.md` and `MILESTONE_EXTRACTION_FLOOR.md`).
- Any detector / pYIN / basic_pitch research.
- Algorithmic tuning of the existing hand-crafted fingerprint features
  (variance budget shows they are not the bottleneck).

## 7. Artifacts

- `backend/scripts/preset_retrieval_validation.py` — fingerprint + top-k
  retrieval harness.
- `backend/scripts/preset_retrieval_diagnose.py` — embedding-space
  diagnostics.
- `backend/preset_catalog_output/retrieval/embeddings.npz` — 99 × 128
  fingerprints.
- `backend/preset_catalog_output/retrieval/retrieval_top5.json` — 20
  query results.
- `backend/preset_catalog_output/retrieval/retrieval_summary.json` —
  per-query and aggregate hit-rates.
- `backend/preset_catalog_output/retrieval/retrieval_report.md` —
  human-readable neighbor tables.
- `backend/preset_catalog_output/retrieval/retrieval_diagnostics.json`
  — embedding-space geometry stats.
