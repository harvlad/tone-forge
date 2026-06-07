# Audio collapse in Analog preset catalog — blocking finding

**Status:** Blocker for ALL retrieval evaluation against `catalog_analog.json`.
All previous DSP / spectral / CLAP / OpenL3 retrieval numbers in this directory
are invalid downstream consequences of this defect.

## Headline numbers

| Layer | Distinct entities |
|---|---:|
| Preset records in `catalog_analog.json` | **99** |
| Distinct `audio_path` strings | 99 |
| Distinct audio **file contents** (SHA‑1 of bytes) | **45** |
| Distinct **decoded waveforms** at 48 kHz mono | **7** |

The 99 preset rows collapse to **7 unique audio signals** after librosa
decoding. The four largest decoded buckets contain **30, 29, 22, 15** presets —
that's 96 / 99 ≈ **97 %** of the catalog mapped to four waveforms.

## Why this is fatal for the retrieval gate

- Top‑1 sound_type accuracy is bounded above by the per-class **purity of the
  largest duplicate bucket**. If a 30-preset bucket contains a mixture of
  `bass`, `pad`, `lead`, `perc`, no embedding (DSP, learned, or human) can
  retrieve correctly within that bucket — the inputs are literally identical.
- The 60 % top‑1 ceiling observed across all geometric transforms in
  `representation_experiments.md` (R1–R10) and the ~41 % CLAP / centered‑CLAP
  result in `representation_experiments_v2.py` are now both attributable to the
  same underlying audio collapse, not to representation quality.
- The conclusion drawn in `REPRESENTATION_AUDIT.md` ("representation quality is
  the bottleneck, not geometry") was reached on broken data and must be
  **re‑opened**. Geometry vs. representation cannot be distinguished until the
  catalog is fixed.

## Where the duplication enters

`audio_collapse_diagnostic.json` shows two separable layers of duplication:

1. **File‑level** (99 → 45): Many distinct preset records reference distinct
   audio paths whose file bytes are nevertheless identical. Likely caused by
   the rendering / capture pipeline writing the same audio to multiple
   filenames (preset_id used as filename, but render result was cached
   incorrectly).
2. **Decode‑level** (45 → 7): Of the 45 unique file blobs, librosa decoding at
   48 kHz mono produces only 7 unique waveforms. This means many "different"
   files differ only in container metadata or in channels that mono downmix
   discards. Bucket sizes after decoding: 30, 29, 22, 15, 1, 1, 1.

Examples (full list in `preset_catalog_output/retrieval/audio_collapse_diagnostic.json`):

- bucket size 30 contains a heterogeneous mix: `analog_noise_hit_perc`,
  `analog_shutter_games`, `analog_e_piano_muted_pure`, `analog_kick_complex_perc`,
  `synth_essentials_analog_phil's_sine_pad`, `analog_synth_organ_long_release`, ...
- bucket size 29 contains mainly basses: `synth_essentials_analog_powergrid_synbass`,
  `analog_saw_filter_bass`, `analog_dual_osc_sample_&_hold_bass`,
  `analog_smooth_squ_bass`, `synth_essentials_analog_deep_pure_sub`, ...

Mixing across categories within a single bucket confirms the issue is in audio
generation/export, not in the catalog labels.

## Required upstream fix (in `tone_forge/preset_catalog/` rendering)

1. Confirm that the renderer **does** invoke Ableton per preset and **does**
   capture distinct audio for each preset. Inspect the renderer's caching
   layer: a likely root cause is a cache key that collides (e.g. only the
   preset *category* is hashed, not the full preset path).
2. Re‑render the full Analog catalog with cache disabled and verify in‑situ
   that decoded SHA‑1 of each output WAV is unique across all 99 presets.
3. Re‑run `scripts/preset_retrieval_validation.py` and
   `scripts/representation_experiments.py` end‑to‑end. Only after the catalog
   has ≥ 95 of 99 unique decoded waveforms should the DSP vs. CLAP comparison
   be considered meaningful.

## Effect on outstanding gates

- The **≥ 80 % top‑1 sound_type gate** must be re‑evaluated on the corrected
  catalog. Current numbers (DSP 60 %, CLAP 41 %, geometry experiments ≤ 62 %)
  are all measurements of how well an embedding can disambiguate **7 inputs**,
  not 99.
- The **human‑labeled perceptual benchmark** is also blocked: there is no
  point asking human raters to label perceptual similarity when 96 / 99
  audio renders are identical to one of four prototypes.

## Recommended next steps

1. **Stop** evaluating new embeddings against this catalog.
2. **Audit** `tone_forge/preset_catalog/` renderer for cache‑key collisions
   and re‑render with cache disabled.
3. **Verify** uniqueness of decoded waveforms (assert ≥ 95 / 99 distinct
   SHA‑1) before any further retrieval work.
4. **Reopen** the representation audit: re‑run `representation_experiments.py`
   on the corrected catalog; only then is the 80 % gate vs. geometry vs.
   learned‑embedding comparison defensible.

## Artifacts

- `preset_catalog_output/retrieval/audio_collapse_diagnostic.json` — full
  decoded‑hash buckets, file hashes, and preset_id ↔ audio_path mapping.
- This file: `backend/AUDIO_COLLAPSE_FINDING.md`.
