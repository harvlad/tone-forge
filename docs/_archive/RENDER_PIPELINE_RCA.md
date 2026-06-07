# Root Cause Analysis — Analog preset catalog renders are not preset-specific

**Status:** Root cause **identified and confirmed by evidence**.
Catalog `preset_catalog_output/catalog/catalog_analog.json` is **unfit for any
retrieval, embedding, ranking, recommendation, or reconstruction-readiness
work** until the rendering pipeline is repaired and the catalog re-rendered.

## 1. Observed symptoms

- 99 preset records in `catalog_analog.json`, 99 distinct `audio_path` strings.
- Only **45** unique audio files by raw byte SHA‑1.
- Only **7** unique decoded waveforms at 48 kHz mono.
- Bucket sizes after librosa decode: **30, 29, 22, 15, 1, 1, 1**.
- The four large buckets correlate strongly with the preset `sound_type`:
  - 30-bucket: heterogeneous catch-all (percussion, fx, keys, pad, other, lead)
  - 29-bucket: 27/29 = 93 % bass
  - 22-bucket: 20/22 = 91 % lead
  - 15-bucket: 11/15 = 73 % pad
- Three orphan buckets of size 1: `analog_dual_osc_reso_noise_bass`,
  `analog_thick_chord_pad`, `analog_80s_vco_brass`.
- Catalog feature columns are mostly 0.0 (fingerprint pipeline also degraded
  by the upstream collapse).

## 2. Confirmed facts (from direct inspection)

### Stage uniqueness table

| Stage | Quantity | Unique / Total | Status |
|---|---|---:|---|
| S1 | `preset_id` in catalog | 99 / 99 | ✅ |
| S1 | source `.adv` path field in catalog row | — | **MISSING from schema** |
| S2 | ALS filename (`<preset_id>.als`) | 99 / 99 | ✅ |
| S2 | ALS file byte SHA‑1 | 99 / 99 | ✅ |
| S2 | `<LastPresetRef>.<Path>` inside `<UltraAnalog>` (25-sample) | 25 / 25 | ✅ |
| S2 | `<UltraAnalog>` device parameter children (25-sample) | **0 across all 25** | ❌ defect |
| S3 | Ableton populates `<UltraAnalog>` from `LastPresetRef` annotation | — | ❌ No |
| S4 | WAV target path (`<preset_id>.wav`) | 99 / 99 | ✅ |
| S4 | WAV byte SHA‑1 | 45 / 99 | ❌ |
| S4 | WAV decoded-audio SHA‑1 | **7 / 99** | ❌ |
| S5 | Fingerprint feature values in catalog rows | mostly 0.0 | ❌ downstream |

### Code evidence

- `tone_forge/preset_catalog/preset_als_generator.py:237-278` — the ALS template
  emits an `<UltraAnalog>` block that contains only `<On>`, `<LomId>`,
  `<ParametersListWrapper>`, `<LastPresetRef>`, `<UserName>`, and
  `<OverwriteProtectionNumber>`. **No `<Voice>`, `<Osc1>`, `<Osc2>`,
  `<Filter1>`, `<Filter2>`, `<Lfo1>`, `<GlobalAmp>`, `<Pitch>`, or
  `<Glide>` children are emitted.** The source comment on line 240–241 reads
  "Minimal UltraAnalog device — Ableton loads params from the .adv file",
  which is the assumption that turns out to be false.
- `tone_forge/preset_catalog/preset_als_generator.py:162-234` — the
  `_build_preset_ref_xml` function correctly writes the absolute and
  relative path to the `.adv`. The catalog's S2 LastPresetRef paths are
  unique and correct (verified for 25/25 in the sample).
- `scripts/auto_export_presets.py:32-40` — `get_pending_presets()` only
  decides what to render by checking whether `<stem>.wav` already exists.
  No content verification, no manifest, no preset → rendered audio link.
  Not the root cause here (filenames don't collide in this catalog) but is a
  latent skip-if-exists risk for any future re-render.
- `scripts/auto_export_presets.py:58-72` — exporting is done by
  `pyautogui.hotkey('command','shift','r')` then typing a filename and
  pressing Enter. There is no verification that (a) the export dialog
  actually opened, (b) the focused project is the one we just opened, or
  (c) Ableton's UltraAnalog device contains the intended parameters.

### XML evidence (representative)

A sample ALS at `preset_catalog_output/als/synth_essentials_analog_snarky_stab.als`:

```
<UltraAnalog Id="500">
  <LomId Value="0" />
  <LomIdView Value="0" />
  <IsExpanded Value="true" />
  <On> ... </On>
  <ParametersListWrapper LomId="0" />
  <LastPresetRef>
    <Value>
      <FilePresetRef Id="0">
        <FileRef>
          <RelativePathType Value="5" />
          <RelativePath Value="Instruments/Analog/Synth Lead/Snarky Stab.adv" />
          <Path Value="/Users/.../Snarky Stab.adv" />
          <Type Value="2" />
          ...
        </FileRef>
      </FilePresetRef>
    </Value>
  </LastPresetRef>
  <LockedScripts />
  <IsFolded Value="false" />
  <ShouldShowPresetName Value="true" />
  <UserName Value="Snarky Stab" />
  <Annotation Value="" />
  <OverwriteProtectionNumber Value="2819" />
</UltraAnalog>
```

There are **no oscillator, filter, envelope, LFO, voice, or amp children**.
A real `.adv` payload would contain those subtrees inside the device block.

## 3. Hypotheses considered

| # | Hypothesis | Outcome |
|---|---|---|
| H1 | CLAP encoder bug — model emits identical embeddings for distinct audio | **Rejected.** CLAP correctly emits 7 unique embeddings for 7 unique audio inputs. The audio itself is collapsed. |
| H2 | `safe_name` slugging collapses different preset_ids to same ALS filename | **Rejected.** Catalog has 99 unique preset_ids and 99 unique slugged ALS/WAV filenames. 0 collisions. |
| H3 | `auto_export_presets.py` skip-if-exists logic skipping renders | **Not the dominant cause.** All 99 target WAVs exist, but their content is collapsed. A latent risk for re-renders, not the source of this defect. |
| H4 | Ableton renders silently fail and write a default `.wav` | Partially correct — but Ableton **does** run; output varies with the embedded MIDI clip, just not with the preset. |
| H5 | ALS files are byte-identical | **Rejected.** All 99 ALS files have distinct SHA‑1s. |
| H6 | ALS file references a wrong/empty `.adv` path | **Rejected.** 24/25 sampled ALSes carry `<LastPresetRef>.<Path>` whose stem matches `preset_name` exactly; the 25th is a near-miss attributable to punctuation in the original `.adv` filename. |
| H7 | ALS file does not contain the preset's parameter values; Ableton's `LastPresetRef` is a display/annotation field, not an auto-load directive | **Confirmed.** This is the root cause. |
| H8 | The bucket-vs-`sound_type` correlation is driven by the embedded test MIDI clip (which varies per `sound_type`), so what we're hearing is the **default UltraAnalog patch** played through the per-`sound_type` test sequence | **Confirmed.** 4 large buckets correspond directly to the 4 test sequences in `test_sequence.py` (default, bass, pad, lead). |

## 4. Tests performed (and their results)

1. **Decoded-waveform bucketing** of all 99 WAVs — produced 7 buckets,
   archived in `preset_catalog_output/retrieval/audio_collapse_diagnostic.json`.
2. **Cross-tabulation of buckets vs `sound_type`** — confirmed buckets align
   with test sequences, not with presets. See section 1 percentages.
3. **ALS file uniqueness audit** — all 99 ALS files have distinct SHA‑1.
   Archived in `preset_catalog_output/retrieval/render_integrity_report.json`.
4. **XML structural inspection of 3 ALS files of different `sound_type`
   classes** — each `<UltraAnalog>` block contains 0 of every expected
   parameter child (`<Voice>`, `<Osc1>`, `<Osc2>`, `<Filter1>`, `<Filter2>`,
   `<Lfo1>`).
5. **25-preset loading validation (Item A)**:
   - A.1 ALS `LastPresetRef.<Path>` stem == `preset_name`: **24 / 25**
   - A.2 unique `LastPresetRef.<Path>` paths in sample: **25 / 25**
   - A.3 ALSes with empty `<UltraAnalog>` parameter body: **25 / 25**
   - A.4 unique decoded WAVs in sample: **5 / 25** (20 %)
   Archived in `preset_catalog_output/retrieval/preset_loading_validation.json`.
6. **Cache / collision audit (Item B)** — 0 `preset_id` collisions, 0
   `safe_name` collisions, 1 stray `.skip` marker, 8 `preset_id`s with
   reserved punctuation that survive into filenames (latent risk, not
   currently triggered). Archived in
   `preset_catalog_output/retrieval/cache_collision_audit.json`.
7. **Render integrity report (Item C)** — full
   `preset_id → preset_path → rendered_wav_path → file_sha1 → decoded_sha1`
   table for all 99 presets. Archived in
   `preset_catalog_output/retrieval/render_integrity_report.json`.

## 5. Root cause

**The ALS template in
`tone_forge/preset_catalog/preset_als_generator.py:_build_analog_device_xml`
emits an `<UltraAnalog>` device that contains a `<LastPresetRef>` *pointer*
to the source `.adv` but does not embed the preset's actual parameter
subtrees. When Ableton opens the ALS, it instantiates `UltraAnalog` with
its **default factory parameters** (the `LastPresetRef` is a display /
recall annotation, not an auto-load directive). Each render therefore
produces the default Analog patch driven by whatever MIDI clip is embedded
in that ALS. The clip is selected per `sound_type` in
`tone_forge/preset_catalog/test_sequence.py`, so the output collapses to
one waveform per `sound_type` (4 large buckets), plus a small number of
outliers from earlier render runs.**

The traceability gap that masked this for so long: `catalog_analog.json`
records `preset_id`, `preset_name`, `audio_path`, but **not** the source
`.adv` path. Without a preset → audio link in the manifest, the rendering
pipeline cannot be audited for content-level correctness.

**Stage where diversity is lost:** logically Stage 2 (the ALS lacks the
parameter payload); observably Stage 3/4 (Ableton renders default
parameters → 4 distinct WAVs based on the test sequence; further collapsed
to 7 by file headers / metadata that disappear at decode time).

## 6. Remediation plan

### R1 — Embed real preset parameters into the ALS (primary fix)

Make `create_preset_als()` embed the actual `.adv` parameter content into
the `<UltraAnalog>` device block rather than only writing a `LastPresetRef`
pointer.

`.adv` files are gzipped XML. The plan:
1. Read the `.adv` for the target preset, gunzip if needed, parse out the
   `<UltraAnalog>` element (or in newer Live versions, the device-specific
   root element).
2. Splice that full element into the ALS in place of the current minimal
   block. Preserve a `<LastPresetRef>` for display purposes; rely on the
   embedded parameter subtree for actual rendering.
3. Renumber `Id="…"` attributes inside the embedded block so they do not
   collide with the rest of the ALS document.
4. Verify by re-extracting the embedded block from a generated ALS and
   confirming it contains non-zero `<Voice>` / `<Osc1>` / `<Osc2>` /
   `<Filter1>` / `<Filter2>` / `<Lfo1>` / `<GlobalAmp>` children.

Alternative if (1)–(3) is fragile: drive Ableton's preset loader
programmatically (Max for Live / Live API) to load the `.adv` into the
device after the ALS opens, before exporting. This is more invasive than
the XML splice and depends on UI automation we already know is brittle.

### R2 — Add preset → audio provenance to catalog manifest

In `catalog_builder.py`, every preset row must persist:
- `preset_id`
- `preset_path` (absolute path to source `.adv`)
- `adv_sha1` (SHA‑1 of the source `.adv` contents)
- `als_path`, `als_sha1`
- `rendered_wav_path`, `wav_sha1`, `decoded_audio_sha1`
- `test_sequence_name` (which sequence the render used)

Without this, future regressions of the same shape will go undetected.

### R3 — Replace UI-automation render driver with a checked driver

The current `scripts/auto_export_presets.py` is unverified GUI automation.
Add either:
- A content check after each render — recompute `decoded_audio_sha1` and
  refuse to mark a preset "rendered" if its SHA‑1 collides with any other
  preset's already-rendered SHA‑1, OR
- Replace `pyautogui` automation with a programmatic Ableton-export path
  (e.g., Live's own export API via M4L, or `xrun`-style headless render).

### R4 — Diversify the MIDI test clip per preset

Even with R1 in place, retrieval evaluation will be confounded if every
"bass" preset uses the same test clip. Either:
- Drive each preset with multiple test clips and concatenate the renders,
  or
- Use a multi-octave, multi-velocity sweep that adequately excites the
  preset's frequency range and amplitude envelope.

### R5 — Add a uniqueness gate to the build pipeline

The `build_preset_catalog.py` flow must end with an assertion:
- `# unique decoded waveform SHA-1 across catalog >= 0.95 * preset_count`
- `# zero heterogeneous category buckets among the largest 10 SHA-1 groups`

If those gates fail, the catalog must be rejected and the build halted.

## 7. Validation criteria (before retrieval / embedding work resumes)

For the rebuilt catalog the following must all hold:

1. **Audio uniqueness gate (primary).**
   Decoded-audio SHA‑1 unique count ≥ **95 / 99** for the Analog catalog
   (i.e. < 5 % duplicates).
2. **No heterogeneous buckets.**
   Any decoded-audio-SHA‑1 bucket of size ≥ 2 must contain presets of a
   single `sound_type` and single `category`.
3. **ALS embeds preset parameters.**
   For all generated ALS files, the `<UltraAnalog>` element must contain
   ≥ 1 `<Voice>`, ≥ 1 `<Osc1>`, ≥ 1 `<Filter1>`, ≥ 1 `<GlobalAmp>`
   children (or whatever the corresponding payload is for the Ableton
   version in use).
4. **Manifest integrity.**
   Every catalog row carries `preset_path`, `adv_sha1`, `als_sha1`,
   `wav_sha1`, `decoded_audio_sha1`.
5. **Render driver verification.**
   The driver records the `decoded_audio_sha1` of each render immediately
   after export, and aborts the build if any two preset_ids share that
   SHA‑1.
6. **Spot-check perceptual diversity.**
   ≥ 20 randomly sampled pairs from the rebuilt catalog must have
   pairwise cosine distance > 0.05 on a baseline mel-spectrum feature
   (current pipeline yields cosine ≈ 0 between most pairs).

Once **all six** criteria pass, the previously frozen workstreams may
resume:
- Re-run DSP retrieval benchmarks (`scripts/preset_retrieval_validation.py`,
  `scripts/representation_experiments.py`)
- Re-evaluate CLAP / OpenL3 (`scripts/clap_embed_presets.py`,
  `scripts/representation_experiments_v2.py`)
- Re-open the representation audit (`REPRESENTATION_AUDIT.md`)
- Re-assess reconstruction readiness
  (`RECONSTRUCTION_READINESS_REVIEW.md`)

## 8. Artifacts

All under `backend/preset_catalog_output/retrieval/`:

- `audio_collapse_diagnostic.json` — decoded-hash buckets + preset_id ↔
  audio_path mapping for all 99 presets.
- `render_integrity_report.json` — full
  `preset_id → adv path → wav path → file_sha1 → decoded_sha1` table.
- `preset_loading_validation.json` — 25-sample Stage-2 audit
  (ALS structure, LastPresetRef path, UltraAnalog body emptiness, decoded
  uniqueness).
- `cache_collision_audit.json` — preset_id and safe_name collision
  scans, reserved-punctuation watchlist, stage-uniqueness summary.

Companion documents:

- `AUDIO_COLLAPSE_FINDING.md` — initial discovery and stop-the-line
  notice.
- This file: `RENDER_PIPELINE_RCA.md`.
