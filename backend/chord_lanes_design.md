# Per-Stem Chord Lanes (C-milestone)

> **Status**: Shipped via commits C1 (pipeline), C2 (bundle), C3 (JAM
> UI), C4 (pipeline-order note), C5 (this doc).
> **Default behaviour**: bit-identical to pre-milestone. The
> `chords_by_stem` dict is *additive*; the legacy `chords` /
> `chords_beat_snapped` arrays stay populated with the `other`-stem
> lane.

## Problem

The JAM chord ribbon historically reflected a single chord lane derived
from Demucs' `other` stem (guitar + keys + piano + everything
harmonic minus bass and vocals). On multi-instrument tracks the
strongest chord-carrying stem is often *not* the catch-all `other`
bucket:

- A keys-led pop song bleeds keyboard harmony into `other`, swamping
  the guitar's chord voicings.
- A song with bass-led riff lines (e.g. funk) has the bass stem
  carrying the harmonic root contour, but the ribbon ignored it.
- After the pan-split shipped for the desktop helper, `guitar_left` /
  `guitar_right` lanes existed as separate stem files but were never
  routed into the chord detector.

The fix: run chord detection on every available stem, persist all
lanes, and let the user pick which lane the ribbon follows.

## Solution

### Stems analyzed

Every available stem participates:

- The 4 Demucs htdemucs stems: `other`, `bass`, `vocals`, `drums`.
- Pan-split products from the desktop helper: `guitar_left`,
  `guitar_right` (only when stereo input + pan-split fires + ≥2 output
  files are produced).

Each stem is run through the same `detect_chords` pipeline (or
`detect_chords_with_key` for the `other` lane, which also yields
`detected_key`). They share an identical `bass_audio` reference
(extracted from the bass stem) and beat grid (`beats_s`). The only
input difference between lanes is the primary chroma source.

### Default lane

`other` — preserves bit-identical behaviour for existing users. The
JAM UI state `state.chordLaneStem` defaults to `'other'` on first
load (no `localStorage` entry). After the user changes it via the
dropdown, the choice is persisted under
`toneforge.jam.chordLaneStem`.

### UI

A new `<select id="chord-lane-stem-select">` lives in the existing
`#chord-snap-row`, next to the "Snap chords to beats" checkbox. It
is hidden when only one lane is available (legacy bundles, mono
single-source mixes). When visible, options are populated from
`Object.keys(state.rawChordsByStem)` sorted alphabetically.

Selecting a different lane re-renders the chord ribbon immediately
via `buildChordRibbon(activeChordArray())`.

## Cost trade-off

Chord detection (librosa chroma + Viterbi HMM) is the dominant
single-stage cost in the analysis pipeline. Naively running it 4-6
times serially would 4-6× the chord-detection stage timing.

Mitigation: per-stem detection runs through a
`concurrent.futures.ThreadPoolExecutor`, matching the parallelisation
pattern used by `midi_stems` extraction. librosa and numpy release
the GIL on the heavy DSP paths, so the wall-clock cost on a typical
4-core dev laptop comes in at roughly 1.5-2× the single-stem time
(dominated by the slowest single stem), not 5-6×.

### Drums / vocals lanes

Chord detection on drums and vocals stems produces mostly noise —
neither carries clean chord voicings. This is expected and surfaced
intentionally as a transparency feature: the user opts in to those
lanes by selecting them, and the resulting noisy ribbon is the
honest signal that "this stem isn't chord-carrying".

A future enhancement could add a per-lane confidence/density badge
("low chord density — likely not a chord lane") next to each
dropdown option. Out of scope for this milestone.

## Bundle contract

`SongUnderstanding` gains two additive fields:

```python
chords_by_stem: Mapping[str, Tuple[Chord, ...]] = field(default_factory=dict)
chords_beat_snapped_by_stem: Mapping[str, Tuple[Chord, ...]] = field(default_factory=dict)
```

The legacy `chords` / `chords_beat_snapped` fields stay populated
with the `other`-stem lane. Old bundles without the new fields load
with empty dicts (default factory) — no crashes, no missing-key
errors.

The bundle assembler's `_iter_chords_by_stem` helper:

- Returns `{}` when the raw value is not a Mapping (legacy bundles).
- Skips non-string keys defensively (malformed inputs don't crash
  the build).
- Returns `()` (empty tuple) for `None` values, distinguishing
  "stem present, no chords detected" from "stem absent".

## Pipeline routing

**Local engine** (`backend/local_engine/analysis_worker.py`): runs
the pan-split before chord detection, so `chord_input_stems` may
include `guitar_left` / `guitar_right` in addition to the 4 Demucs
stems.

**Unified pipeline** (`backend/tone_forge/unified_pipeline.py`):
does *not* run a pan-split. The unified path operates on the 4
Demucs stems only. C1 degrades gracefully — `_detect_chord_lane`
iterates whatever stems the pipeline made available.

If the unified pipeline ever grows a pan-split stage, the per-stem
chord detection loop will pick up the additional stems automatically
with no further wire-up.

## JAM UI state

```javascript
state.rawChordsByStem = {
  other:   { fixed: [...], snapped: [...] },
  bass:    { fixed: [...], snapped: [...] },
  vocals:  { fixed: [...], snapped: [...] },
  drums:   { fixed: [...], snapped: [...] },
  guitar_left?:  { fixed: [...], snapped: [...] },
  guitar_right?: { fixed: [...], snapped: [...] },
};
state.chordLaneStem = 'other';  // localStorage-persisted
```

`activeChordArray()` reads `state.rawChordsByStem[state.chordLaneStem]`
first, with a defensive fallback to the legacy `state.rawChords`
when the per-stem dict is empty (legacy bundles).

`syncChordLaneStemSelect()`:

- Hides the `<label id="chord-lane-stem-label">` when ≤1 lane.
- Falls back to `'other'` when the persisted value isn't in the
  current bundle's available lanes.
- Repopulates `<option>` elements on each call.

## Invariants locked by tests

`backend/tests/test_chord_lanes_per_stem.py`:

1. `_detect_chord_lane` calls the detector once per stem.
2. The legacy `fixed` array equals `fixed_by_stem["other"]`.
3. Missing stems don't appear in `fixed_by_stem`.
4. `AnalysisResult.to_dict()` round-trips the per-stem dicts.
5. The new fields are omitted from `to_dict()` when unset (keeps
   history JSON small for drum-only / instrumental-only sources).

`backend/tests/test_chord_lanes_bundle.py`:

1. Persisted `chords_by_stem` round-trips through the bundle
   assembler with typed `Chord` tuples.
2. Legacy bundles (no `chords_by_stem` field) load with empty dicts.
3. Legacy `chords` field and new `chords_by_stem` are independent.
4. Malformed non-string keys are dropped, not crashed on.
5. The frozen-dataclass default factory returns independent dicts
   across bundle instances (no shared mutable default aliasing).

## Out of scope

- **Per-section auto-promotion of the chord lane** based on
  `dominant_stem` (Stage B song-form output) — would let the ribbon
  follow `bass` during a bass-feature section and switch back to
  `other` during the chorus. Future Stage C feature.
- **Server-side pan-split** in the unified pipeline. Currently a
  desktop-helper-only feature.
- **Per-lane confidence/density badge** in the dropdown.
- **Per-lane export** to MIDI / lead-sheet formats — exports still
  use the active lane only.
