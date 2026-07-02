# Boundary re-detection inside long ANCHOR sections (Fix C)

Status: **DRAFT — needs review before implementation**

## Problem

The Stage-0 RMS-novelty boundary detector
(`analysis/sections.py:SectionDetector._detect_boundaries`) under-
segments songs whose chorus riff runs over multiple structurally
distinct sections. Reference case: session `c3687f79` (Linkin Park —
"One Step Closer"), where the detector produces one 70s CHORUS block
spanning verse2 + prechorus2 + chorus2 + bridge:

```
0: intro       0.00 - 12.54 ( 12.5s) UNIQUE
1: verse      12.54 - 27.59 ( 15.0s) DEVELOPMENT
2: prechorus  27.59 - 62.69 ( 35.1s) DEVELOPMENT    ← 35s, ate real chorus 1
3: chorus     62.69 - 72.72 ( 10.0s) ANCHOR
4: chorus     72.72 - 142.94 ( 70.2s) ANCHOR         ← 70s, ate v2+pc2+c2+bridge
5: chorus    142.94 - 160.50 ( 17.6s) ANCHOR
6: chorus    160.50 - 173.04 ( 12.5s) ANCHOR
7: outro     173.04 - 176.73 (  3.7s) UNIQUE
```

Fix B (already shipped) surfaces this in the UI as a dashed amber
pill so the user knows the label is untrusted. Fix C actually
redraws the missing internal boundaries.

## Signal analysis

RMS-novelty alone can't find the missing boundaries in this case —
the chorus riff dominates the mix during the "eaten" spans, so the
mixed-RMS shape stays roughly constant. The evidence for a real
boundary lives in signals the detector doesn't currently read:

| Signal | What it says at v2→pc2 boundary | Available today? |
|---|---|---|
| Vocal RMS | Verse vocals stop, pre-chorus vocal enters ~2 bars later | ✅ per-stem MIDI + `SectionFeatures` |
| Drum onset density | Kick pattern often shifts kick-heavy → 4-on-floor at chorus entry | ✅ per-stem MIDI |
| H2 chord-trigram | Chord vector inside the 70s block shifts even when the RMS doesn't | ✅ `extract_h2` |
| Bar-grid periodicity | Sub-section boundaries land on 8- or 16-bar multiples | ✅ `beats_s` / `downbeats_s` |

## Approach options

### Option A — Lower novelty threshold on flagged spans only

Re-run `_detect_boundaries` inside each `duration_flag`-tagged span
with a threshold multiplier of ~0.5× baseline.

**Pros:** Reuses existing code path. No new signal wiring.
**Cons:** RMS-novelty has no signal here — that's why the baseline
missed it. Almost certainly a no-op on the reference case.
**Verdict:** ❌ Doesn't address the actual failure mode.

### Option B — Vocal-stem RMS novelty on flagged spans

Compute a per-frame RMS on the vocals stem, run novelty peak-
finding on it inside the flagged window. Vocal entries/exits at
verse→pre-chorus and pre-chorus→chorus boundaries are strong.

**Pros:** Directly reads the signal that carries the boundary.
**Cons:** Needs vocal-stem waveform available at flag time
(currently only MIDI notes flow through the pipeline; the vocals WAV
lives inside the stems dict and would need to be re-loaded).
**Verdict:** Viable but has a signal-wiring cost.

### Option C — H2 chord-trigram divergence inside the span

Split the flagged span into 4-bar or 8-bar windows, compute a chord
trigram vector for each, look for windows where the vector diverges
from the surrounding "riff-body" vector by more than the H2 sep
threshold.

**Pros:** Uses signal already computed elsewhere. Directly aligned
with how Stage A labels roles.
**Cons:** Needs `extract_h2` to be callable on a sub-region (not
just a full pre-defined section list). H2 was designed around the
detector's existing sections; extending it costs some refactor.
**Verdict:** Strongest signal but highest engineering cost.

### Option D — Bar-grid regular subdivision

If a section > 32s and typed as CHORUS or PRECHORUS, split at
`floor(duration / target_size)` even bar-multiples, defaulting to
16-bar chunks. Re-run Stage A/B labeling per chunk.

**Pros:** Zero-signal, dead-simple, bounded output. Catches "the
detector merged everything" as a fallback.
**Cons:** Naïve — will split legitimately long final choruses into
fake sub-sections. Guardrail: skip if the block is the final section.

**Verdict:** Good as a safety net **behind** a smarter signal;
alone it introduces false-positive splits.

## Recommendation

**Two-stage approach: B + D fallback.**

1. **Stage 1:** Vocal-stem RMS novelty on the flagged span. If it
   produces ≥1 candidate boundary with confidence above a floor,
   use those splits and re-run Stage A/B on the sub-sections.
2. **Stage 2 (fallback):** If Stage 1 produces no candidates and
   the span is still ≥ 2× the median chorus duration for the song,
   fall through to 16-bar regular subdivision (D).

This keeps the primary path grounded in real signal (vocals) while
providing a safety net for songs where the vocal signal itself is
degraded.

Feature-flagged behind a pipeline config option so it can be A/B'd
against Fix-B-only output.

## Implementation sketch

**Where to hook in:**

`unified_pipeline.py:1010` — right after the Fix B duration-guard
flags each section. Sections needing splitting are already tagged
by `duration_flag ∈ {chorus_too_long, prechorus_too_long}`.

**New module:** `analysis/section_resegment.py`

```python
def resegment_flagged_sections(
    sections: list[dict],
    stems: dict,               # for vocal WAV access
    beats_s: np.ndarray,       # for bar-grid fallback
    chords: list[Chord],
    tempo_bpm: float,
    thresholds: ResegmentThresholds,
) -> list[dict]:
    """Return a new sections list where flagged spans have been split.

    Non-flagged sections are passed through unchanged.
    For each flagged span:
      1. Try vocal-stem novelty (Option B)
      2. Fall back to bar-grid subdivision (Option D) if vocal has
         no signal AND the span is very long
      3. If both produce zero splits, leave the section alone but
         keep the duration_flag so the UI still shows the warning
    """
```

**Re-labeling sub-sections:**

Once a section is split into N children, each child needs a Stage A
label. Two options:

- Re-run `classify_roles` + `derive_section_types` on the full
  sections list (cheap; just re-runs H2 which is already cached)
- Copy the parent's label to all children, mark children as
  `duration_flag=""` (let downstream refinements catch mis-labels)

Recommendation: re-run classify_roles. It's the cleanest guarantee
that Stage A/B invariants hold on the new segmentation.

## Fixture set

Location: `backend/tests/fixtures/resegment/`

Minimum viable fixture set:

1. **`c3687f79_synth.json`** — Synthetic sections + vocal-RMS
   samples reproducing the "One Step Closer" boundary miss. Truth:
   3 boundaries should be inserted inside section 4 (at ~90s, ~110s,
   ~130s).

2. **`legitimate_long_chorus.json`** — Real long final chorus
   (Bohemian Rhapsody style ending). Truth: should NOT be split
   because it IS one section. Guards against false-positive splits.

3. **`vocal_silent_span.json`** — A `chorus_too_long` span where
   vocals are completely absent (instrumental jam). Stage 1 (vocals)
   yields nothing; Stage 2 (bar-grid) either splits at 16-bar
   intervals or (better) leaves the span alone. Documents which
   behaviour we want.

## Tests

- `test_resegment_c3687f79_synth`: 70s span split into 3+ children
- `test_resegment_no_op_when_no_flags`: pass-through unchanged
- `test_resegment_preserves_non_flagged_sections`: only touches
  flagged spans
- `test_resegment_fallback_bar_grid`: vocal-silent long span
  triggers Option D
- `test_resegment_legitimate_long_chorus`: final chorus with real
  vocal signal doesn't get split

## Open questions (need Matt's input)

1. **Which stems' RMS do we novelty-search?** Vocals is the
   obvious primary. Do we also probe the drums stem for kick-
   pattern shifts?
2. **Threshold tuning:** vocal-RMS novelty threshold — start at
   `mean + 0.5*std`, tune against the fixture set?
3. **Config flag:** ship enabled by default, or hidden behind an
   env var / config option for the first release?
4. **UI behaviour on re-segmented sections:** should the split
   children carry the parent's duration_flag (as a "this was
   auto-split" hint), or should we clear it because the split
   presumably fixed the problem?
