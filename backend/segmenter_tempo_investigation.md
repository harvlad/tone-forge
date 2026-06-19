# Segmenter Tempo Handling — Investigation

Follow-up to `boundary_stability_report.md` (recommendation B: freeze
classifier work and investigate section generation). Research only — no
production code changes.

## Scope

Three questions raised by the report's "Suggested next investigation":

1. Does the segmenter use the tempo hint only to skip beat tracking, or
   does it use it inside the boundary decision rule?
2. Is the MIDI-derived tempo different from the segmenter's own
   beat-track tempo?
3. Could the segmenter's beat tracker be replaced by the
   pipeline-hoisted beat tracker (`_track_beats`) so both code paths see
   the same beats?

## Q1. How `SectionDetector` uses the tempo hint

**Finding: the tempo hint feeds the boundary quantisation rule, not only
the beat-track skip.**

`SectionDetector.detect_sections` (`tone_forge/analysis/sections.py:201`)
accepts an optional `tempo` argument:

```python
# sections.py:222-227
if tempo is None:
    tempo, _ = librosa.beat.beat_track(y=audio, sr=sr)
    if hasattr(tempo, "__iter__"):
        tempo = float(tempo[0]) if len(tempo) > 0 else 120.0
    tempo = float(tempo) if tempo > 0 else 120.0
```

If the caller supplies `tempo`, the internal `beat_track` call is
skipped. So far this matches the "skip the redundant beat track"
intuition.

The tempo value is then forwarded to `_detect_boundaries`
(`sections.py:279, 313-327`):

```python
# sections.py:313-327
# Quantize to bar boundaries if we have tempo
beats_per_bar = 4
bar_duration = (60.0 / tempo) * beats_per_bar

quantized = [0.0]
for b in boundaries[1:-1]:
    bar_num = round(b / bar_duration)
    quantized_time = bar_num * bar_duration
    if quantized_time >= duration:
        continue
    if quantized_time > quantized[-1] + self.min_section_duration:
        quantized.append(quantized_time)
quantized.append(duration)
```

Every candidate boundary (energy-novelty peak) is snapped to the nearest
4/4 bar grid derived from `tempo`. **The grid spacing is
`bar_duration = (60/tempo)*4`. Different tempos produce different
grids.** Worked example at the corpus values:

| Tempo (BPM) | bar_duration (s) | Grid spacing diff vs 120 BPM |
|------------:|-----------------:|-----------------------------:|
| 120.0       | 2.000            | —                            |
| 123.0       | 1.951            | -2.4%                        |
| 129.2       | 1.857            | -7.1%                        |
| 161.5       | 1.486            | -25.7%                       |

A 25% grid-spacing shift at 161.5 BPM means the same energy-novelty peak
at, say, 87s snaps to either bar 43 (`43 × 2.00 = 86s`, +1s drift) or
bar 59 (`59 × 1.486 = 87.7s`, +0.7s drift) depending on which tempo was
supplied. Across a 3-minute song that compounds into the 1.6–3.2s
median drifts the audit measured.

The tempo hint is *not* a no-op when the segmenter would have computed
the same value internally — it's a structural input to the boundary
rule. Two calls with the same audio but different tempos *will*
produce different boundaries even if the energy curve is bit-identical.

## Q2. MIDI tempo vs segmenter beat-track tempo on the 5 songs

Measured values (corpus full-pipeline output is the tempo the segmenter
*used*; direct-probe is what `SectionDetector` *would* have computed
from full-mix audio with no hint):

| Song | Full-pipeline tempo (BPM) | Direct-probe tempo (BPM) | Δ (BPM) | bar_duration Δ |
|------|--------------------------:|-------------------------:|--------:|---------------:|
| Simulated Life     | 117.5 | 120.2 | +2.7   | -2.2%           |
| Disco Of Doom      | 161.5 | 120.2 | -41.3  | +34.4%          |
| Mr Dance           | 123.0 | 120.2 | -2.8   | +2.3%           |
| Death by Nanobots  | 123.0 | 123.0 | 0.0    | 0.0%            |
| Chill Zone         | 129.2 | 30.0  | -99.2† | +331%†          |

† Chill Zone direct-probe tempo at 30.0 BPM is below the pipeline's
40–240 BPM sanity window (`unified_pipeline.py:1595-1596`). In
production it would be discarded ("degraded") and `tempo` would fall
through to `None`. The 99.2 BPM gap is an artefact of running the
segmenter standalone without that floor; treat as "no probe tempo" for
correlation purposes.

**Correlation with measured median boundary drift:**

| Song | Δtempo (BPM) | Median drift (s) |
|------|-------------:|-----------------:|
| DbN          | 0.0   | 2.00 |
| Sim Life     | 2.7   | 1.60 |
| Mr Dance     | 2.8   | 0.80 |
| DoD          | 41.3  | 1.80 |
| Chill Zone   | †     | 3.20 |

The correlation between Δtempo and drift is **weaker than expected**.
DbN has zero tempo delta but still 2.0s median drift; Mr Dance has the
smallest delta and the best drift; DoD has the largest measurable delta
but only middling drift. This means **tempo is not the only driver**.

**Likely additional drivers:**

- **Audio source difference.** Production beat tracking runs on the
  demucs `other` stem (`unified_pipeline.py:1572-1581`); the audit's
  direct probe ran `SectionDetector` on the **mixed-mix audio** with
  no stem isolation. Different audio sources → different
  `librosa.beat.beat_track` output → different bar grids. This alone
  explains the DoD gap (161.5 vs 120.2).
- **Energy curve source.** The segmenter computes its energy curve
  (`_compute_energy_curve`) on whatever audio is passed to
  `detect_sections`. Both runs in the audit fed the same mixed audio
  there, so the energy curves are identical, but the snapping grid
  differs.
- **`min_section_duration` and `quantized[-1]` interactions.** After
  bar-snapping, boundaries within `min_section_duration` of the
  previous accepted boundary are dropped (`sections.py:324-325`).
  Different bar grids accept/reject different candidates in different
  orders, so the set of surviving boundaries can diverge even where the
  bar grids agree locally.
- **Last-boundary handling.** `quantized.append(duration)` is
  unconditional — the final boundary is always the audio duration, not
  a bar-snapped value. Two runs with different penultimate boundaries
  can therefore produce a final section of very different length even
  if everything else aligns. This is consistent with the Chill Zone
  pattern (7 unmatched full-pipeline sections, all in the back third).

## Q3. Could `_track_beats` feed the segmenter? (Already does.)

**Finding: the hoist is already wired in production.**

`unified_pipeline.py:677-695`:

```python
beat_grid = await self._track_beats(audio_data, stems)
tempo_bpm = beat_grid["tempo_bpm"]
beats_s = beat_grid["beats_s"]
downbeats_s = beat_grid["downbeats_s"]
...
section_result = await self._detect_sections(
    audio_data, midi_stems, tempo_hint=tempo_bpm,
)
```

And `_detect_sections` (`unified_pipeline.py:1646-1656`):

```python
tempo = tempo_hint if tempo_hint and tempo_hint > 0 else None
if tempo is None:
    for stem_type, midi_data in midi_stems.items():
        if midi_data.get("tempo"):
            tempo = midi_data["tempo"]
            break

detector = SectionDetector(sr=audio_data.sr)
result = detector.detect_sections(
    audio_data.audio,
    sr=audio_data.sr,
    tempo=tempo,
)
```

The tempo fallback chain in production is:

1. **`_track_beats` tempo** (librosa beat track on the demucs `other`
   stem), clamped to 40–240 BPM. *Used in 5/5 corpus songs.*
2. **MIDI tempo** from the first stem whose extractor returned one.
   Only used when (1) degraded.
3. **`None`** → `SectionDetector` runs its own internal
   `librosa.beat.beat_track` on the mixed audio. Not used in any of
   the 5 corpus songs (all had valid hoisted tempos).

So the segmenter's internal beat-track call is **not the live failure
mode** in the corpus. The pipeline-hoisted tempo is what landed in the
audit's "full pipeline" column, and the audit's "direct probe" column
is the *counterfactual* where neither hoisted tempo nor MIDI tempo was
supplied. The two columns therefore measure:

> drift between *production behaviour* and *the segmenter-standalone
> fallback path*.

This **reframes what the boundary stability audit was actually
measuring.** It is not measuring "MIDI-informed vs MIDI-uninformed
runs of the same pipeline" — it's measuring "production-with-hoisted-
'other'-stem-beats vs segmenter-standalone-with-internal-mixed-mix-
beats". The 1.9s median drift is the gap between two scenarios that
don't both run in production.

## What the audit's drift actually tells us about calibration stability

The corpus output we calibrate against is produced by path (1):
`_track_beats` on the `other` stem → segmenter with that tempo. The
calibration is stable as long as path (1) is stable, which depends on:

- **`other`-stem availability.** Every corpus song had it. Songs with
  missing stems would fall back to the mixed mix and land somewhere
  along the audit's probe distribution. *Mitigation: hard-fail
  pipeline on missing stems rather than silently changing tempo
  source.*
- **`librosa.beat.beat_track` stability** on the `other` stem across
  librosa versions. Not measured. *Suggested follow-up: run the same
  audio twice in identical environment to confirm bit-identical
  tempos; then bump librosa minor version and remeasure.*
- **Demucs version.** A demucs upgrade changes the `other` stem
  content, which changes the beat-track tempo, which changes the bar
  grid, which moves boundaries. *Mitigation: pin demucs and document
  the dependency in calibration provenance.*
- **The `(60/tempo)*4` quantiser itself.** A 1 BPM tempo shift moves
  a boundary at 90s by ~0.75s (90s / 60 bars × `1/120 - 1/121` per
  bar ≈ 0.75s cumulative drift). This is the *inherent* sensitivity
  of the rule; even bit-stable inputs would still shift boundaries
  under a 1 BPM tempo nudge.

The cdens delta the audit reported (median 0.07, max 0.17) **is the
right order of magnitude** for a 1.9s median window shift on
chord-dense sections. The classifier-input drift is real, but its
upper bound is bounded by the audit's measurement.

## Recommendation

**Keep the classifier freeze in place, but redirect the investigation.**

The originally-suggested next step ("trace why `SectionDetector`
produces different segmentations with tempo hint vs without") has been
answered: the bar-quantisation rule at `sections.py:313-327` is the
mechanism; the rule is fed by `_track_beats`-on-`other`-stem in
production, and the audit's "probe" column was a counterfactual, not a
second production run.

The classifier-calibration stability question — **is the bar grid the
calibration was anchored to reproducible?** — is *not yet measured*.
The boundary stability audit answered a different question.

### Concrete next probes (in order of cost)

1. **Determinism check on the same code, same input, same env.** Run
   `_track_beats` on each corpus song's `other` stem twice; confirm
   bit-identical tempo and beats. Cost: cheap (~minutes per song,
   stems are already cached). Outcome: either the calibration is
   stable for the *current* code/env (good; documents the baseline)
   or it is not (smoking gun for a different bug entirely).
2. **Audio-source isolation test.** Re-run the boundary audit, but
   force the direct probe to use the **`other` stem**, not the mixed
   mix. Expected result: drift collapses substantially. If drift
   collapses below the 1.0s / 3.0s decision thresholds, that confirms
   the audit's headline numbers were dominated by audio-source choice
   and the production path is more stable than the report suggests.
3. **Sensitivity sweep on the `(60/tempo)*4` quantiser.** Perturb the
   hoisted tempo by ±0.5, ±1, ±2 BPM and remeasure boundary drift.
   Outcome: the inherent sensitivity of the rule, independent of any
   beat-tracker variance. If even ±1 BPM produces ≥0.5s median drift,
   the rule itself is the calibration risk, not the inputs.
4. **Bar-grid-free segmentation experiment.** Disable the bar-snap
   step (`sections.py:313-327`) entirely; rerun audit on the corpus.
   Outcome: how much of the production segmentation's stability
   actually comes from the bar grid vs the energy-novelty rule? If
   stability is similar without snapping, the snap step is a candidate
   to remove for calibration robustness.

### What stays frozen

Per the original recommendation:
- `GuidanceThresholds` defaults
- `chord_density_weight`
- Per-stem riff/lead/chord score functions
- Tie-margin and aggregation logic
- Chord detector internals

These remain frozen pending probes (1)–(3). Probe (4) is a segmenter
change, not a classifier change, and is consistent with the
"investigate section generation" directive.

## Caveats

- All tempo values for the 5 corpus songs come from the cached corpus
  output and the direct probe; no fresh runs were made for this
  investigation.
- Worked grid-spacing examples (Q1) assume an idealised
  energy-novelty peak; in practice the `prominence` and
  `min_section_duration` filters can absorb small grid shifts. The
  net measured drift (1.9s median) is the empirical answer; the
  worked examples are illustrative of the mechanism, not predictive of
  the magnitude.
- The "production path is more stable than the audit suggests"
  hypothesis is *not yet proven* — probe (2) is the test that would
  confirm or refute it.
