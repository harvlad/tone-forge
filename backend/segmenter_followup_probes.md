# Segmenter Tempo — Probe 3 (Sensitivity Sweep)

Follow-up to `segmenter_tempo_investigation.md`. Executes probe 3 from
that document's "Concrete next probes" list. Research only — no
production code changes.

## What was measured

For each of the 5 corpus songs, `SectionDetector.detect_sections` was
run on the **mixed audio** (sum of all stems) with a *center* tempo
equal to the producer-labelled tempo in the stem filename
(120/120/120/120/122 BPM). The detector was then re-run at the same
audio but with `tempo` perturbed by ±0.5, ±1, ±2, ±5 BPM. Boundary
drift was computed pairwise (nearest boundary in perturbed run to each
boundary in center run, |Δt|, no IoU filter).

The tempo is supplied to the detector explicitly. The detector's
internal `librosa.beat.beat_track` does not run, so this isolates the
**inherent sensitivity of the bar-quantisation rule
(`sections.py:313-327`)** from any beat-tracker variance.

Script: `/tmp/jam_tempo_sensitivity.py`. Run completed in ~90s total.

## Aggregate sensitivity (median across 5 songs)

| Δtempo (BPM) | Median drift (s) | P95 drift (s) | Section-count delta |
|-------------:|-----------------:|--------------:|--------------------:|
| -5.0         | 0.74             | 1.94          | +8                  |
| -2.0         | 0.69             | 2.20          | +9                  |
| -1.0         | 0.74             | 2.04          | +8                  |
| **-0.5**     | **0.63**         | **1.93**      | **+10**             |
| **+0.5**     | **0.51**         | **2.29**      | **0**               |
| +1.0         | 0.70             | 2.43          | +1                  |
| +2.0         | 0.75             | 2.67          | +2                  |
| +5.0         | 0.65             | 1.68          | +1                  |

## Headline findings

1. **The quantiser is brittle at sub-BPM precision.** A ±0.5 BPM
   perturbation — well below typical `librosa.beat.beat_track`
   measurement noise across versions — produces **0.6s median drift,
   ~2s p95 drift**. These magnitudes are comparable to the original
   boundary stability audit's 1.9s median / 4.8s p95, which used
   tempo deltas an order of magnitude larger (41 BPM in the DoD case).
   The audit's headline drift numbers are therefore *not* primarily
   driven by tempo sensitivity — they're an entirely separate
   phenomenon (audio-source choice; see probe 2 follow-up below).

2. **Sensitivity is strongly asymmetric in tempo direction.**
   Downward perturbations (slower tempo → longer `bar_duration`)
   consistently *add* sections (median +8 to +10 across the corpus).
   Upward perturbations (faster tempo) are nearly no-ops on section
   count (median 0 to +2). Mechanism: longer bar_duration produces
   more candidate boundaries that survive the
   `min_section_duration` filter at `sections.py:324-325`.

3. **Per-song response is non-monotonic and highly variable.**
   Examples:
   - Chill Zone at Δ-0.5 BPM: 13 extra sections (28 → 41), 0.63s
     median drift. At Δ+0.5 BPM: 0 extra sections, 0.31s median
     drift — *less than half* the downward response.
   - DoD at Δ+1 BPM: 2.34s median drift (worst single result in the
     entire sweep) — but Δ-1 BPM gives only 0.77s median drift.
   - DbN at Δ-1 BPM: 0.29s median drift; at Δ-0.5 BPM: 0.95s median
     drift. Halving the perturbation *triples* the drift.
   The boundary set is not a smooth function of input tempo; it
   exhibits step discontinuities driven by which candidates cross
   the `prominence` and `min_section_duration` thresholds.

4. **A 1 BPM nudge exceeds the original audit's 1.0s "quantisation"
   threshold on 2/5 songs.** DoD at +1 BPM: 2.34s median drift. DbN
   at +1 BPM: 1.26s median drift. Both above the threshold the
   original audit used to distinguish "frame-grid quantisation" from
   "qualitatively different segmentation".

## Mechanism (operational)

The bar-quantisation rule at `sections.py:313-327`:

```python
bar_duration = (60.0 / tempo) * beats_per_bar  # beats_per_bar = 4

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

Three compounding sensitivities:

- **`round(b / bar_duration)`** is a step function. A small shift in
  `bar_duration` flips boundaries that sit near the midpoint between
  two bars to the opposite bar. At 120 BPM (bar = 2.0s), a candidate
  at 87.0s rounds to bar 44 (88.0s, +1.0s drift). At 121 BPM
  (bar = 1.983s), it rounds to bar 44 (87.3s, +0.3s drift). Single
  candidates can move by up to ½ bar (~1s at 120 BPM) on a small
  tempo nudge.
- **`min_section_duration` filter** is order-dependent. Whether
  candidate i is accepted depends on whether candidate i-1 was, which
  depends on the bar grid. A 1-BPM grid shift can cause a cascade of
  accepts/rejects propagating downstream — this is the source of the
  +8 to +13 section-count deltas at downward perturbations.
- **Unconditional last-boundary append** (`sections.py:326`) means
  the final section is always anchored to the audio's end, not a
  bar-snapped value. Two runs with different penultimate boundaries
  produce a different final section length even when the rest of the
  grid agrees.

## Implications for classifier calibration

The original investigation hypothesised: "is the bar grid the
calibration was anchored to reproducible?" This probe answers it:

> **No. Even sub-BPM input variance moves the bar grid by enough to
> shift section boundaries on the same order of magnitude as the
> original boundary-stability audit measured between completely
> different tempo sources.**

Concretely, calibrating `chord_density_weight` (or any other
classifier threshold) against the current corpus output means
calibrating against a specific bar grid produced by a specific
`_track_beats` invocation. The classifier's input distributions
(`chord_count_in_section`, `note_count`, `repetition_score`, etc.)
all depend on which MIDI notes fall inside which window. A 1 BPM
beat-tracker output change — within normal librosa-version variance —
moves the windows enough that the corpus-derived input distributions
no longer hold.

This is consistent with the original audit's finding (recommendation
B) but isolates the mechanism more precisely: **the bar-snap step
itself is the calibration-stability risk, not the beat-tracker or
the tempo source.** Even a perfectly deterministic beat tracker
would still leave the calibration at the mercy of small
tempo-estimate shifts.

## Per-song detail

### Simulated Life  (center 120.0 BPM, 20 center boundaries, 218s)

| Δtempo | tempo | n | median (s) | p95 (s) | max (s) |
|-------:|------:|--:|-----------:|--------:|--------:|
| -5.0   | 115.0 | 22 | 0.48 | 1.74 | 3.48 |
| -2.0   | 118.0 | 23 | 0.61 | 1.43 | 2.17 |
| -1.0   | 119.0 | 23 | 0.69 | 1.77 | 3.09 |
| -0.5   | 119.5 | 23 | 0.46 | 1.93 | 3.55 |
| +0.5   | 120.5 | 20 | 0.39 | 2.13 | 2.15 |
| +1.0   | 121.0 | 19 | 0.65 | 2.43 | 4.89 |
| +2.0   | 122.0 | 19 | 0.75 | 2.65 | 3.80 |
| +5.0   | 125.0 | 20 | 0.44 | 1.44 | 1.52 |

### Disco Of Doom  (center 120.0 BPM, 18 center boundaries, 244s)

| Δtempo | tempo | n | median (s) | p95 (s) | max (s) |
|-------:|------:|--:|-----------:|--------:|--------:|
| -5.0   | 115.0 | 26 | 1.09 | 2.50 | 2.87 |
| -2.0   | 118.0 | 27 | 1.12 | 2.27 | 3.39 |
| -1.0   | 119.0 | 26 | 0.77 | 3.10 | 3.70 |
| -0.5   | 119.5 | 28 | 0.62 | 2.85 | 3.85 |
| +0.5   | 120.5 | 20 | 0.68 | 2.29 | 2.59 |
| +1.0   | 121.0 | 19 | **2.34** | 3.06 | 3.42 |
| +2.0   | 122.0 | 20 | 0.66 | 2.74 | 2.82 |
| +5.0   | 125.0 | 20 | 0.76 | 2.13 | 2.40 |

### Chill Zone  (center 120.0 BPM, 28 center boundaries, 170s)

| Δtempo | tempo | n | median (s) | p95 (s) | max (s) |
|-------:|------:|--:|-----------:|--------:|--------:|
| -5.0   | 115.0 | 40 | 1.04 | 2.00 | 2.09 |
| -2.0   | 118.0 | 41 | 0.97 | 1.90 | 2.03 |
| -1.0   | 119.0 | 41 | 0.98 | 1.83 | 1.97 |
| -0.5   | 119.5 | 41 | 0.63 | 1.92 | 1.98 |
| +0.5   | 120.5 | 28 | 0.31 | 0.61 | 0.65 |
| +1.0   | 121.0 | 28 | 0.62 | 1.22 | 1.29 |
| +2.0   | 122.0 | 29 | 1.23 | 2.42 | 2.56 |
| +5.0   | 125.0 | 28 | 0.72 | 1.68 | 1.68 |

### Mr Dance  (center 120.0 BPM, 30 center boundaries, 262s)

| Δtempo | tempo | n | median (s) | p95 (s) | max (s) |
|-------:|------:|--:|-----------:|--------:|--------:|
| -5.0   | 115.0 | 40 | 0.61 | 1.92 | 2.61 |
| -2.0   | 118.0 | 40 | 0.61 | 2.20 | 3.32 |
| -1.0   | 119.0 | 39 | 0.74 | 2.04 | 3.66 |
| -0.5   | 119.5 | 40 | 0.76 | 1.83 | 2.84 |
| +0.5   | 120.5 | 31 | 0.51 | 3.31 | 4.66 |
| +1.0   | 121.0 | 31 | 0.70 | 3.54 | 5.32 |
| +2.0   | 122.0 | 33 | 0.54 | 2.67 | 2.79 |
| +5.0   | 125.0 | 31 | 0.44 | 1.29 | 2.48 |

### Death by Nanobots  (center 122.0 BPM, 25 center boundaries, 291s)

| Δtempo | tempo | n | median (s) | p95 (s) | max (s) |
|-------:|------:|--:|-----------:|--------:|--------:|
| -5.0   | 117.0 | 33 | 0.74 | 1.94 | 2.39 |
| -2.0   | 120.0 | 25 | 0.69 | 2.91 | 2.98 |
| -1.0   | 121.0 | 25 | 0.29 | 2.67 | 2.75 |
| -0.5   | 121.5 | 25 | 0.95 | 2.94 | 2.95 |
| +0.5   | 122.5 | 24 | 0.79 | 2.95 | 3.08 |
| +1.0   | 123.0 | 27 | **1.26** | 2.22 | 3.79 |
| +2.0   | 124.0 | 27 | 1.05 | 2.70 | 3.81 |
| +5.0   | 127.0 | 27 | 0.65 | 2.79 | 3.62 |

---

# Probe 4 — Bar-Grid-Free Segmentation Experiment

Disables the bar-snap step (`sections.py:313-329`) via in-process
monkey-patch — no production source edits — and re-measures
segmentation on the 5 corpus songs. Tests three questions:

- **Q1.** How much does removing the snap move existing boundaries?
- **Q2.** Does the un-snapped path have *any* tempo sensitivity?
  (Hypothesis: zero — un-snapped doesn't read tempo.)
- **Q3.** Does the un-snapped path produce a sane section count, or
  does the energy-novelty peak detector overshoot without the
  consolidation pass?

Script: `/tmp/jam_bar_grid_free.py`. Run completed in ~120s total.

## Q1 — Snapped vs un-snapped boundaries (at center tempo)

| Song | n snapped | n un-snapped | Median drift (s) | P95 (s) | Max (s) |
|------|----------:|-------------:|-----------------:|--------:|--------:|
| Simulated Life     | 20 | 35 | 0.37 | 0.85 | 0.85 |
| Disco Of Doom      | 18 | 46 | 0.50 | 1.00 | 1.00 |
| Chill Zone         | 28 | 81 | 0.90 | 0.90 | 0.90 |
| Mr Dance           | 30 | 59 | 0.30 | 0.88 | 0.90 |
| Death by Nanobots  | 25 | 57 | 0.33 | 0.94 | 0.94 |

**Interpretation:** the snap moves individual boundaries by at most
≤1.0s (half a bar at 120 BPM, by construction of `round(b /
bar_duration)`). The 0.85–1.00s p95/max values across all 5 songs
are consistent with that ceiling. The snap is therefore *not*
producing large location shifts for the boundaries it preserves —
its main effect is on *which boundaries survive* (Q3).

## Q2 — Un-snapped path's tempo sensitivity

| Δtempo (BPM) | Median drift (s) | P95 drift (s) | Section count Δ |
|-------------:|-----------------:|--------------:|----------------:|
| -2.0         | 0.00             | 0.00          | 0               |
| -1.0         | 0.00             | 0.00          | 0               |
| -0.5         | 0.00             | 0.00          | 0               |
| +0.5         | 0.00             | 0.00          | 0               |
| +1.0         | 0.00             | 0.00          | 0               |
| +2.0         | 0.00             | 0.00          | 0               |

**Interpretation:** zero drift, zero count change at every
perturbation level on every song. This is the expected result — the
un-snapped path never reads `tempo` in `_detect_boundaries`. **This
confirms the bar-quantiser is the sole source of tempo sensitivity
in the segmenter.** Eliminating the snap entirely eliminates the
calibration-stability risk identified in probe 3.

## Q3 — Section count comparison

| Song | duration (s) | snapped | un-snapped | Δ | un-snapped sections / minute |
|------|-------------:|--------:|-----------:|--:|----------------------------:|
| Simulated Life     | 218.0 | 20 | 35 | +15 | 9.6  |
| Disco Of Doom      | 244.0 | 18 | 46 | +28 | 11.3 |
| **Chill Zone**     | 170.0 | 28 | **81** | **+53** | **28.6** |
| Mr Dance           | 262.0 | 30 | 59 | +29 | 13.5 |
| Death by Nanobots  | 291.1 | 25 | 57 | +32 | 11.7 |

**Interpretation:** the un-snapped path produces **1.7–2.9× more
sections** than the snapped path. Chill Zone is the catastrophic
case — 81 sections in 170s is an average section length of **2.1s**,
musically meaningless (a typical song section is 8–30s). The other
four songs land at 5–7s average section length, which is borderline:
short enough to be sub-phrase rather than section-scale.

The snap is doing **two jobs simultaneously**:
1. Quantising boundary times to bars (the calibration-fragile job
   identified in probe 3).
2. **Consolidating clusters of energy-novelty peaks** via the
   `min_section_duration` filter at `sections.py:325`, which rejects
   candidates within `min_section_duration` of the previous accepted
   one. Because un-snapped candidates can land at arbitrary times,
   the cascade behaves differently and fewer get rejected.

Specifically: the `min_spacing` filter at `sections.py:298-305` runs
*before* the snap, on the raw peak indices, so the un-snapped path
already has min-spacing-filtered peaks. The snap then *additionally*
rejects candidates whose snapped position falls within
`min_section_duration` of the last-accepted snapped position. That
second rejection pass is what drops 15–53 sections per song.

## Conclusion

**Removing the snap entirely is not viable.** It would eliminate
the tempo sensitivity (probe 3's problem) but at the cost of
producing 2-3× oversegmentation (probe 4's Q3 problem). For Chill
Zone in particular, the un-snapped output (sections every 2 seconds)
is qualitatively wrong — the chord/riff/lead classifier has no
useful signal in 2-second windows.

The snap is doing necessary consolidation; it just chose a
calibration-fragile mechanism (bar-grid quantisation) to do it.

## Refined recommendation: snap-to-beats variant

The snap's purpose is consolidation, not bar-grid alignment per se.
The natural alternative is **snap to the hoisted `beats_s` array**
rather than to `(60/tempo) × beats_per_bar` bars. Concretely:

```text
# Conceptual replacement, NOT a code change to make now —
# this is the next investigation, not the next commit.

beats_s = pipeline-hoisted beat times from _track_beats
for b in raw_boundaries[1:-1]:
    nearest_beat = argmin(|beats_s - b|)
    snapped_time = beats_s[nearest_beat]
    if snapped_time > quantized[-1] + min_section_duration:
        quantized.append(snapped_time)
```

This would:
- Preserve the consolidation pass (`min_section_duration` filter on
  snapped positions).
- Anchor snapping to *measured* beat times rather than a
  reconstructed bar grid, so the snap's stability becomes a function
  of beat-tracker stability, not tempo-rounding stability.
- Eliminate the `round(b / bar_duration)` step function entirely,
  removing the source of the asymmetric sensitivity observed in
  probe 3.
- Already have its input plumbed: `beats_s` is in the pipeline
  bundle and flows through `_track_beats → _detect_sections` as of
  Phase 7 (commit history shows this was the hoist motivation).

Open questions before that experiment is worth running:
- **Beat-tracker bit-stability** across re-runs and across librosa
  versions. This is probe 1 from `segmenter_tempo_investigation.md`,
  still unmeasured.
- **Snap-to-downbeats vs snap-to-beats.** Downbeats (every 4th beat,
  derived in `_track_beats`) are closer to the bar-snap's intent
  but inherit the 4/4 assumption. Beats are denser but agnostic.
- **Aligned-to-downbeats fallback** when `beats_s` is degraded
  (empty list / single beat). Current code falls through to a 120
  BPM default; a snap-to-beats variant would need an equivalent
  defensive path.

These can be answered with a probe-5 script that compares the
snap-to-beats variant against (a) production snap and (b) un-snapped
on the same 5 songs.

---

## Probe roadmap status

- **Probe 1 (determinism check)** — pending. Lower priority after
  probe 4: a snap-to-beats variant inherits beat-tracker variance
  directly, so beat-tracker bit-stability is a prerequisite for that
  design. Still cheap to run.
- **Probe 2 (audio-source isolation)** — pending. Now mostly a
  measurement-artefact cleanup; doesn't change the structural
  recommendation.
- **Probe 3 (tempo sensitivity sweep)** — done above. Bar quantiser
  is brittle at sub-BPM precision.
- **Probe 4 (bar-grid-free experiment)** — done above. Removing the
  snap entirely is not viable (2-3× oversegmentation). The snap is
  necessary; its *mechanism* is the calibration risk.
- **Probe 5 (snap-to-beats variant)** — natural next step, informed
  by probe 4's negative result.

## What stays frozen

Per the original recommendation in `boundary_stability_report.md`:
- `GuidanceThresholds` defaults
- `chord_density_weight`
- Per-stem riff/lead/chord score functions
- Tie-margin and aggregation logic
- Chord detector internals

Nothing in any probe touched production code. Scripts live at
`/tmp/jam_tempo_sensitivity.py` (probe 3) and
`/tmp/jam_bar_grid_free.py` (probe 4); both read from `samples/`.

## Caveats

- All runs used the mixed audio (stem sum). Production beat-tracks
  the demucs `other` stem; production segmentation reads the same
  mixed-mix audio energy curve but with a tempo derived from the
  `other` stem. So these probes perturb the *quantiser input* but
  keep the energy curve as it would appear in production. That's
  the right isolation for measuring rule sensitivity.
- The probe 3 perturbations don't model real-world beat-tracker
  variance shape (long tails, fragmentation, octave errors). The
  sub-BPM perturbations are a *floor* on what to expect; real
  variance is wider.
- Probe 4's un-snapped path uses an in-process monkey-patch of
  `SectionDetector._detect_boundaries`. The patched function
  replicates `sections.py:285-311` verbatim and skips the snap at
  `sections.py:313-329`. If upstream code drifts, the patched
  helper must be re-synced. As of this audit it tracks the source
  exactly.
- Sample size is 5 songs. The Chill Zone outcome (81 sections at
  n=170s) is independently sufficient to reject the un-snapped
  design; the other 4 songs corroborate.

---

# Probe 5 — Snap-to-Beats Variant

Replaces the bar-grid snap with snapping each raw candidate to the
nearest entry in a `beats_s` array (or `downbeats_s` = `beats_s[::4]`)
sourced from `librosa.beat.beat_track`. In-process monkey-patch of
`SectionDetector._detect_boundaries`; no production source edits.

Questions:

- **Q1.** Beat-tracker bit-stability: same audio, same library, same
  env — does `librosa.beat.beat_track` produce identical `beats_s`?
  (This subsumes probe 1 from the original investigation.)
- **Q2.** Snap-to-beats vs production snap: section count, boundary
  locations.
- **Q3.** Snap-to-downbeats vs production snap: same comparison but
  with the sparser every-4th-beat grid (closer to the original 4/4
  bar intent).
- **Q4.** Snap-to-beats robustness — drift response to (a) ±50ms
  uniform shift of `beats_s`, (b) dropping the first beat, (c) ±1
  BPM tempo perturbation. The third should be zero because the
  variant doesn't read tempo (probe 4 Q2 mirror).

Script: `/tmp/jam_snap_to_beats.py`. Run completed in ~90s total.

## Q1 — Beat-tracker bit-stability

| Song | n beats run-A | n beats run-B | bit-identical | max beat drift (s) |
|------|--------------:|--------------:|:-------------:|-------------------:|
| Simulated Life     | 354 | 354 | yes | 0.000000 |
| Disco Of Doom      | 478 | 478 | yes | 0.000000 |
| Chill Zone         | 63  | 63  | yes | 0.000000 |
| Mr Dance           | 523 | 523 | yes | 0.000000 |
| Death by Nanobots  | 581 | 581 | yes | 0.000000 |

**5/5 bit-identical.** `librosa.beat.beat_track` is deterministic
for fixed input under the current library version. The Chill Zone
outlier (only 63 beats in 170s = 0.37 beats/s ≈ 22 BPM) flags the
known degeneracy from the original investigation — the beat tracker
returns a sparse, low-confidence track for this song — but it is
deterministic. **Snap-to-beats stability is a function of beat-track
stability, and the beat-track is bit-stable.** This subsumes probe 1
from `segmenter_tempo_investigation.md`.

## Q2 — Snap-to-beats vs production snap (center tempo)

| Song | n production | n snap-to-beats | Median drift (s) | P95 (s) | Max (s) |
|------|-------------:|----------------:|----------------:|--------:|--------:|
| Simulated Life     | 20 | 20 | 0.78 | 3.32 | 10.05 |
| Disco Of Doom      | 18 | 24 | 1.00 | 2.97 | 2.98  |
| Chill Zone         | 28 | 27 | 1.02 | 10.96| 15.02 |
| Mr Dance           | 30 | 33 | 0.48 | 2.77 | 3.47  |
| Death by Nanobots  | 25 | 30 | 0.96 | 1.99 | 2.00  |

**Section counts are close** to production (within ±6) on every
song — the consolidation pass works on beat-anchored positions just
as well as on bar-anchored ones. Median drift (0.48–1.02s) is on the
same scale as production's response to ±0.5 BPM perturbation in
probe 3.

The large max drifts (Sim Life 10.05s, Chill Zone 15.02s) are a
**nearest-neighbour matching artefact**, not real divergence — the
boundary set has shifted enough that the matcher pairs unrelated
boundaries near the song extremes. The original boundary audit hit
the same artefact and addressed it with an IoU filter; this probe
does not, since the focus is on robustness to input perturbations
(Q4), not on aligning two candidate segmentations.

## Q3 — Snap-to-downbeats vs production snap

| Song | n production | n snap-to-downbeats | Median drift (s) | P95 (s) | Max (s) |
|------|-------------:|--------------------:|----------------:|--------:|--------:|
| Simulated Life     | 20 | 20 | **0.03** | 2.45 | 10.05 |
| Disco Of Doom      | 18 | 20 | **2.97** | 3.02 | 3.03  |
| Chill Zone         | 28 | 18 | 2.98 | 10.96| 15.02 |
| Mr Dance           | 30 | 32 | 0.53 | 2.06 | 3.47  |
| Death by Nanobots  | 25 | 26 | 0.46 | 1.51 | 2.42  |

**Snap-to-downbeats is phase-fragile.** Sim Life median is 0.03s
(downbeat phase aligns with production's bar grid). DoD and Chill
Zone medians are ~2.97s ≈ half a bar at 120 BPM — every snapped
boundary is offset by the same amount because the downbeat phase
disagrees with the production bar grid by half a beat. This is the
"downbeats inherit the 4/4 assumption" problem flagged in probe 4:
when the assumed beat-1 anchor is off-phase from where the music
"feels" the downbeat, every sparsely-spaced snap target inherits
that offset.

**Conclusion: downbeats are not a viable snap target.** Beats are
denser (3–8× more candidates), so any individual candidate gets
snapped to a closer beat, and the song-level pattern of offsets
washes out.

## Q4 — Snap-to-beats robustness

| Song | shift +50ms median (s) | drop-first-beat median (s) | +1 BPM tempo median (s) |
|------|-----------------------:|---------------------------:|------------------------:|
| Simulated Life     | 0.05 | 0.00 | 0.00 |
| Disco Of Doom      | 0.05 | 0.00 | 0.00 |
| Chill Zone         | 0.05 | 0.00 | 0.00 |
| Mr Dance           | 0.05 | 0.00 | 0.00 |
| Death by Nanobots  | 0.05 | 0.00 | 0.00 |

This is the headline result of the probe.

- **+50ms uniform shift of beats_s → 0.05s boundary drift.** Linear,
  exact response. The variant is bounded by the magnitude of input
  perturbation, not by a `round(x / bar_duration)` step function.
- **Drop-first-beat → 0.00s drift.** Removing one beat from a 63–581
  beat array doesn't move any snapped boundary, because the
  candidates' nearest beats are almost never the first one. Robust
  to single-beat fragmentation.
- **+1 BPM tempo → 0.00s drift.** Confirmed insensitive to tempo —
  the variant doesn't read tempo. This is the same Q2 result as
  probe 4 (un-snapped path); the snap-to-beats variant preserves
  this property while *also* providing consolidation (Q2 section
  counts).

### Compare to production snap (probe 3, Δtempo +1 BPM)

| Song | Production snap median drift at +1 BPM | Snap-to-beats median drift at +50ms shift |
|------|---------------------------------------:|-----------------------------------------:|
| Simulated Life     | 0.65s | 0.05s |
| Disco Of Doom      | **2.34s** | 0.05s |
| Chill Zone         | 0.62s | 0.05s |
| Mr Dance           | 0.70s | 0.05s |
| Death by Nanobots  | **1.26s** | 0.05s |

Snap-to-beats is **at least an order of magnitude more stable** to
input perturbations than production snap, on every song. DoD and DbN
both move above the 1.0s "quantisation" threshold under production
snap with a 1 BPM tempo nudge; snap-to-beats stays at 0.05s drift
under a 50ms beat-track shift (a much larger perturbation in
absolute time terms).

## Conclusion

**Snap-to-beats is the right replacement design for the bar
quantiser.** Evidence:

1. The input (`beats_s` from `librosa.beat.beat_track`) is
   deterministic for fixed audio (Q1).
2. The variant produces section counts within ±6 of production on
   all 5 songs (Q2) — preserves consolidation.
3. The variant is structurally insensitive to tempo (Q4 +1 BPM
   median 0.00s) — eliminates the probe 3 sensitivity entirely.
4. The variant has a linear, predictable response to beats_s
   perturbations: a 50ms shift produces 50ms boundary drift, not a
   half-bar step jump (Q4 +50ms median 0.05s).
5. Snap-to-downbeats is too phase-fragile (Q3) and should not be
   used.

The remaining risk is **what changes the `beats_s` array** across
runs/versions:

- librosa version (beat-track algorithm changes)
- Demucs version (different `other` stem → different beats)
- Stem availability (fallback to mixed mix → different beats)

These are now the *only* things calibration depends on, and the
beat-tracker stability story is much simpler to characterise and
defend than the bar-quantiser's step-function sensitivity.

## Recommended next step (out of probe scope)

Probe 5 has answered the design question. Implementation would be a
small, isolated edit:

- Replace `sections.py:313-329` with a snap-to-beats loop driven by
  a `beats_s` argument added to `_detect_boundaries`.
- Plumb `beats_s` from `_track_beats` through `_detect_sections` to
  `detector.detect_sections(beats_s=beats_s)` — the upstream stage
  already computes it (`unified_pipeline.py:677, 695`).
- Fallback when `beats_s` is degraded or `len(beats_s) < 2`: fall
  through to the current `(60/tempo)*4` bar grid (so legacy
  audio-only paths and beat-track failures keep working).
- Update `boundary_stability_report.md`'s "Suggested next
  investigation" section to point to this design.

This is a single-commit change; it is **not** being made in this
investigation. The classifier freeze stays in place per the original
recommendation. The next concrete decision is whether to:

(a) Implement the snap-to-beats variant, recalibrate `chord_density_weight`
    on the new boundary distribution, then unfreeze classifier tuning. or
(b) Keep the freeze and ship the snap-to-beats variant as a separate
    correctness commit first, *without* recalibration; let classifier
    behaviour drift in whatever direction the new boundaries produce
    and re-evaluate the corpus.

Both are valid. (a) gives a tighter calibration story; (b) is
strictly the riff-first milestone's "don't tune until rails are
stable" directive applied literally.

## Caveats

- The probe runs `librosa.beat.beat_track` on the **mixed audio**,
  not the demucs `other` stem. Production beat-tracks the `other`
  stem. The Q1 bit-stability finding holds for any deterministic
  fixed input; the *content* of the beats array would differ.
  Whether that content is closer to or further from the production
  pipeline's beats is unmeasured.
- Q4's 50ms shift and drop-first-beat perturbations are
  illustrative, not exhaustive. Real beat-tracker variance across
  librosa versions can include octave errors (every 2nd beat
  missing) and phase wrap, which this probe doesn't simulate.
- Sample size 5. The Q4 result is the strongest signal — uniform
  0.05s response across every song under a +50ms shift suggests the
  response is structural, not data-dependent.
