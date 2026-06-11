# Song Understanding — Capability Map

**Priority 8 deliverable, second pass.** Investigation-only.
Companion to `SONG_UNDERSTANDING_INVESTIGATION.md`. **No code changes
accompany this commit.**

## 0. Scope & relationship to the prior doc

The first investigation (`SONG_UNDERSTANDING_INVESTIGATION.md`) audited
the 13-field `SongUnderstanding` DTO and reported per-field feasibility
against three producers and two consumers. It concluded:

- Four spectral fields (`brightness`, `warmth`, `attack`, `decay`) are
  the highest-leverage change because they have a *named consumer in
  the policy layer* waiting for them.
- `tuning` / `capo_fret` / `difficulty` / `motifs` were correctly
  declared in the contract but had no consumer and no labels — out of
  scope for that pass.

This document widens the lens. Rather than asking "which existing DTO
field can we fill," it asks: **given the full signal surface the
platform already computes today, which guitarist-facing features can
we ship without inventing new analysis?** It then ranks the 7 target
features in the original prompt against eleven adjacent opportunities
the inventory surfaced. No new contracts are proposed — that's the
follow-on `SongUnderstanding`-field design pass. This is the
capability map underneath that design.

The prior doc is correct on its narrower question. This doc does
**not** retract its findings. It does revise one judgment:
**difficulty estimation is not out of scope; it's a rubric
composition over signals the platform now has.** See §3.3.

## 1. Master signal inventory

The platform's analysis surface, organised by producer subsystem.
Counts are coarse — many "signals" are actually small dataclasses
with their own sub-fields. The point of this section isn't to
re-enumerate every field but to anchor §3 and §4 against a shared
map.

| Producer | Module(s) | Representative signals | Status |
|---|---|---|---|
| Tempo / beat grid | `unified_pipeline`, descriptor blobs | `tempo_bpm`, `beats_s`, `downbeats_s`, beat-grid IOI variance | shipped; downbeats often empty |
| Key & mode | `unified_pipeline`, descriptor blobs | `key` (root+mode string), key confidence (hardcoded 0.5) | shipped (root only); confidence not measured |
| Chord lane | `analysis/chord_detector.py`, `chords.py` | per-chord `{symbol, root, quality, bass, start_s, end_s, confidence}` + chroma | shipped; lane density varies |
| Sections | `analysis/sections.py` | section boundaries + labels (`intro/verse/chorus/bridge/outro`), per-section feature aggregates | shipped |
| Production style | `analysis/reference_analyzer.py` | reverb (size, decay, wet/dry), delay (timing, feedback), groove/swing/syncopation, energy curve, layering, genre hints, FX characteristics | shipped — **parallel pipeline, doesn't reach `SongUnderstanding`** |
| Synth behaviour | `analysis/synth_behavior.py` | supersaw stack, octave layering, glide, arpeggiator pattern, sidechain detection, vibrato params, polyphony estimate | shipped |
| MIDI extraction (top-level) | `midi_extractor.py`, `midi/extraction_pipeline.py`, `midi/ensemble_extractor.py` | `MIDIExtractionResult` (notes + pass statistics + detector agreement + arbitration outcome) | shipped |
| Per-note features | `midi/passes/*` | per-note `{pitch, velocity, start, duration, confidence}` + 13-field provenance + 12-flag `NoteFlag` enum (octave-doubled, harmonic-recovered, beat-grid-filtered, etc.) | shipped |
| Profile classification | `midi/profile_classifier.py`, `profiles.py` | 11-field `ClassificationFeatures` (onset density, transient sharpness, sustain ratio, harmonic stability, polyphony estimate, dynamic range, low-freq ratio, etc.) → profile id + 24 config params | shipped |
| Spectral validation | `midi/spectral_validator.py` | per-note spectral acceptance + 9 supporting signals | shipped |
| Pitch stability | `midi/pitch_stability.py` | 13 fields including `vibrato_rate_hz`, `vibrato_depth_cents`, `has_chorus`, `modulation_type`, per-note pitch σ | shipped |
| Stem separation | `stem_separator.py`, `stem_model.py` | 4-stem WAV (drums/bass/vocals/other) + per-stem identity tags + pan-split metadata (`side_ratio`, `lr_correlation`) | shipped |
| Stem fingerprint | `fingerprint/*` | 5 spectral + 4 temporal + 4 modulation + 3 rhythmic + 3 timbral features → 128-dim L2-normalised embedding | shipped |
| Tone fingerprint | `tone/guitar_catalog.py` | 8-feature canonical vector (brightness, warmth, air, attack_ms, decay_ms, sustain_ratio, harmonic_ratio, pitch_stability) + validity mask | shipped |
| Retrieval geometry | `tone/guitar_catalog.py`, `tone/policy.py` | z-normalised L2 distance, calibrated confidence, margin, `ConfidenceTier` (HIGH / MEDIUM / LOW / UNKNOWN), fallback chain policy | shipped |
| Provenance / quality | `provenance.py`, quality analysis | per-result lineage, quality scores, profiling metadata | shipped |
| Detector ensemble | `midi/ensemble_extractor.py`, `detector_arbitration.py` | per-detector contributions, agreement score, arbitration verdict | shipped |
| Instrumentation log | `tone_log.jsonl` | 3 event types (retrieval, calibration, recommendation) | shipped |

**Headline.** The platform already computes substantially more about
each song than `SongUnderstanding` exposes. The
`reference_analyzer.ProductionStyle` output and the per-note MIDI
provenance + `ClassificationFeatures` are the two largest dormant
reservoirs.

## 2. The capability question

Rephrased: *given (a) the signal table above, and (b) the seven
target features in the prompt, which features are signal-bounded
(need new analysis) vs. wiring-bounded (existing signals not yet
composed) vs. label-bounded (need ground truth)?*

| Target feature | Bound by | Effort class |
|---|---|---|
| Tuning detection | label + reliability (guitar-stem MIDI accuracy) | Medium |
| Capo detection | label + cascade on tuning | High (cascade) |
| Difficulty estimation | **wiring** (rubric composition over existing signals) | Medium |
| Motif / repeating-phrase detection | wiring (`librosa.segment.recurrence_matrix` is free) | Low-Medium |
| Riff extraction | wiring (motif + guitar-stem MIDI + section boundaries) | Medium |
| Section similarity | **wiring** (pairwise distance over per-section aggregates) | Low |
| Practice-oriented guidance | orchestration of all of the above | High |

None of the seven targets is signal-bounded. Two (tuning, capo) are
label-bounded. The other five are wiring or orchestration.

## 3. Per-feature deep dives

### 3.1 Tuning detection

**Status today.** `SongUnderstanding.tuning: Optional[str]` declared,
never populated. Prior doc concluded label-blocked.

**Reframe.** The natural input is not "detect the open string from
the audio" — too ambiguous, especially on processed signals. The
reliable input is **pitch-population statistics on the guitar
stem's MIDI**:

- All notes the guitarist plays.
- Their pitch-class histogram.
- The minimum sounding pitch (the lowest note the guitar plays in
  the song) — open low-string pitch with high probability if the
  guitar plays an open chord anywhere.
- Bend signatures from `pitch_stability` (a bend on a low E that
  resolves to F# implies that the low E is open or fretted low).

**Inputs needed.** Guitar-stem MIDI (have it), key (have it),
`pitch_stability` per-note bend/vibrato data (have it).

**What we don't have.** A reliable mapping from "minimum sounding
pitch" → "tuning name" for non-standard tunings — that requires
either curated ground truth (≥50 songs across drop D, half-step
down, DADGAD, open G, etc.) or a heuristic ladder of common
tunings ranked by Western-rock prior probability.

**Recommendation.** Ship a *conservative* detector that flags only
three states: `STANDARD`, `STANDARD_HALF_STEP_DOWN`, `DROP_D`.
Anything else → `UNKNOWN`. Surfaces value (these three cover ~80%
of rock catalog) without label risk. **Value: Medium-High.
Complexity: Medium. Defensibility: Low** (any DAW can do this).

### 3.2 Capo detection

**Status today.** Declared, never populated.

**Cascade dependency.** Capo detection only makes sense relative
to known tuning. With standard tuning + chord-shape inference,
"this song is in F but uses E-shape voicings → capo 1" works.
The signals are there: `chords` field gives chord symbols,
guitar-stem MIDI gives chord *voicings*, key gives the global
reference.

**Why this is harder than it looks.** The same `F` chord can be
played as an E-shape with capo 1, a barre F at fret 1, a D-shape
with capo 3, etc. Disambiguating requires fret-position estimation
— which we don't have today (no tab transcription).

**Recommendation. Defer indefinitely.** Or solve it UX-side: a
single dropdown "I'm using a capo at fret ___" beats any
algorithmic detection at this signal richness. **Value: Low-Medium.
Complexity: High. Defensibility: Low.**

### 3.3 Difficulty estimation

**Reframe vs. prior doc.** The previous investigation flagged
difficulty as out of scope because there was no consumer. That's
true at the contract level but misses the broader product
question: difficulty *is* the consumer of Jam's practice-guidance
loop. The right question is "can we composite the signals we
already have into a stable rubric?" The answer is yes.

**Proposed rubric (composite, not ML).**

| Dimension | Signal source | Notes |
|---|---|---|
| Rhythmic complexity | `tempo_bpm`, IOI variance from beat grid, `groove/swing/syncopation` from `reference_analyzer` | Faster + more syncopated → harder |
| Harmonic complexity | `chords` lane: count distinct symbols, count non-triad qualities, chord change rate | Jazz 7ths + frequent changes → harder |
| Technical density | Guitar-stem MIDI: notes-per-second, polyphony estimate, presence of bend/vibrato flags | Shred → harder |
| Endurance | Song duration × continuous-playing density (gaps from MIDI) | Long marathon riff → harder |
| Stamina inflection | Per-section `ClassificationFeatures` deltas — section-level difficulty curve | Surface where the hard part lives |

**Outputs.** Single 0-10 score + per-dimension breakdown + per-section
heat-map. The per-section heat-map is the **highest-leverage UX
artifact** — Yousician-style "the chorus is harder than the verse"
visualization is exactly what guitarists practising along want.

**Inputs needed.** All exist today. Wiring + a hand-tuned scoring
function. Calibration is a ladder of ~30 songs ranked by a
guitarist into easy/medium/hard buckets — a single afternoon's
labelling.

**Value: HIGH. Complexity: Medium. Defensibility: Medium.** Promote
from "out of scope" → P8.2 candidate.

### 3.4 Motif / repeating-phrase detection

**Free primitive.** `librosa.segment.recurrence_matrix(chroma_cqt)`
yields a self-similarity matrix per song. Off-diagonal blocks =
repeating segments. We compute chroma already (it's an input to
chord detection); the recurrence matrix is one call away.

**What this gives.** Audio-domain motifs at chroma resolution
(~100ms per frame). Useful for "the chorus motif repeats here, here,
and here" callouts. Not riff-precise — chroma collapses octave, and
the resolution is too coarse for note-by-note motif identification.

**Inputs needed.** Chroma (have it), recurrence matrix wrapper (~30
lines), a peak-picking + clustering pass to extract distinct motifs
(~100 lines).

**Contract.** `SongUnderstanding.motifs: Tuple[Motif, ...]` already
declared (`contracts.py:215-227`). One-line consumer-side surface.

**Value: Medium. Complexity: Low-Medium. Defensibility: Low.**

### 3.5 Riff extraction

**Why this is the highest-leverage guitarist-specific feature.**
"Find the riff" is the canonical guitarist task. Spotify can't do
it (no stem MIDI). Yousician has it only for curated tabs. The
platform has all three primitives in place:

1. Guitar-stem MIDI (note sequences, not just chroma).
2. Section boundaries (constrain riff search to sensible spans).
3. Self-similarity (`recurrence_matrix` for finding repetitions).

**Algorithm sketch.** Within each section, search the guitar-stem
MIDI for the longest repeated sub-sequence whose first occurrence
falls in that section. Score by length × repetitions ×
melodic-distinctiveness (entropy of pitch-class set). The
top-scoring sub-sequence per section is the "riff." Rank
sections by riff score. The song-level riff is the highest-ranked
section's winner.

**Inputs needed.** Guitar-stem MIDI (have it), sections (have it),
sub-sequence matching (new, ~150 lines), scoring rubric (new, ~50
lines).

**Output shape.** `Riff { start_s, end_s, midi_pitches,
section_label, repeat_count }`. Future Jam can isolate and loop
the riff for practice — the practice-guidance product fits
naturally on top.

**Value: HIGH. Complexity: Medium. Defensibility: Medium-High.**
This is where the platform's MIDI investment compounds.

### 3.6 Section similarity

**Why this is an easy win.** `analysis/sections.py` already
computes per-section feature aggregates. Pairwise distance between
sections — using the same z-norm L2 the tone-catalog uses — yields
"verse 1 ≈ verse 2" / "chorus 1 ≈ chorus 2" labelling at no
analytical cost.

**Inputs needed.** Per-section feature vectors (have them), z-norm
L2 (have it — `tone/guitar_catalog._znorm_l2`), threshold tuning.

**Use cases.** (a) Improve section labelling itself ("this section
the labeller called 'bridge' is actually a verse variant"). (b)
Surface to the user: "the second chorus is harmonically thicker
than the first" — a callout that demonstrates intelligence
without committing to a new contract field.

**Value: Medium-High. Complexity: Low. Defensibility: Low.**

### 3.7 Practice-oriented guidance

**The strategic anchor.** Every other feature on this list is a
component of practice guidance. The product question is: "given a
song the user wants to learn, what should the platform tell them
to do?"

**Composition.**

| Guidance type | Composed from |
|---|---|
| "Start here" pointer | Difficulty heat-map (§3.3) → lowest-difficulty section |
| "Loop the riff" | Riff extraction (§3.5) → primary riff timestamps |
| "Drill the chord change" | Chord lane + section similarity (§3.6) → high-frequency changes in a single section |
| "Watch the timing" | Tempo + IOI variance + groove/swing → flag swung sections that the user might play straight |
| "Tune your guitar" | Tuning detection (§3.1) — single-line |
| "Tone target" | Existing tone retrieval (HIGH-tier match) — already shipped |

**The product win.** None of these are individually novel. The
*composition* is — and the composition is the moat. No single
competitor combines tone matching + MIDI-grounded riff isolation
+ difficulty heat-map + chord-change drills inside one looped
practice surface.

**Inputs needed.** All component features above + Jam UX surface
to host the guidance. The UX is the bottleneck, not the signals.

**Value: HIGHEST. Complexity: High. Defensibility: HIGH.**

## 4. Adjacent opportunities

The inventory surfaced eleven feature ideas not in the original
prompt. Listed for the roadmap but not deeply analysed.

1. **Tone-to-rig translation.** The tone retrieval already returns
   a monitor chain. Surface the chain's pedal/amp settings as a
   text cheat-sheet ("Tube Screamer @ 11 o'clock, JCM800 channel 2,
   gain 7"). Value: High. Complexity: Low (just rendering).
2. **Stem-mix recipes.** Auto-generate "play along with just the
   drums + bass" mixes from the existing stem separation. Value:
   High for jam-along UX. Complexity: Low.
3. **Tempo curriculum.** Auto-generate a 70%/85%/100% tempo ladder
   from the original audio. Value: Medium. Complexity: Low.
4. **Chord-change drills.** From the chord lane, auto-isolate the
   hardest two-chord pair in the song and loop. Value: Medium-High.
   Complexity: Low.
5. **Pitch-class drone.** Generate a drone in the song's key from
   the detected key. Value: Medium (improvisation aid).
   Complexity: Trivial.
6. **Note-density heatmap.** Per-section guitar-stem notes-per-second
   as a visualization band. Value: Medium. Complexity: Low.
7. **Right-hand technique tagging.** From per-note flags + transient
   sharpness + bend/vibrato fields, infer "palm-muted," "tapping,"
   "hammer-on heavy." Value: Medium. Complexity: Medium (rubric +
   labels).
8. **Reverb / delay match.** `ProductionStyle.reverb` and `.delay`
   already exist. Surface to the user as monitor-chain effect
   suggestions. Value: Medium. Complexity: Low.
9. **Synth-behaviour callouts.** Surface detected supersaw stacks /
   sidechain / arpeggiator as "this part is a synth, not a guitar"
   — important onboarding signal for guitarists confused by a
   keyboard riff. Value: Medium. Complexity: Trivial.
10. **Cross-song progression library.** Group catalog songs by
    `key` + chord progression hash to surface "songs with the same
    chord progression as this one." Value: Medium-High.
    Complexity: Medium (hash design).
11. **Vibrato / bend mimicry exercise.** From `pitch_stability`'s
    `vibrato_rate_hz` / `vibrato_depth_cents` per note, surface
    "this note is a half-step bend with vibrato at 5 Hz" — a
    technique-isolation drill. Value: Medium. Complexity: Low.

## 5. Ranking matrix

Scored 1-5 on each axis. Strategic defensibility is "how hard
would a Spotify or Yousician have to work to match this?" — a
proxy for moat width.

| Feature | Guitarist value | Implementation complexity (lower=easier) | Defensibility | Composite |
|---|---|---|---|---|
| §3.7 Practice guidance | 5 | 4 (high) | 5 | **14** |
| §3.5 Riff extraction | 5 | 3 | 4 | **12** |
| §3.3 Difficulty estimation | 4 | 3 | 3 | **10** |
| Adjacent #2 Stem-mix recipes | 4 | 1 | 2 | **9** |
| Adjacent #1 Tone-to-rig translation | 4 | 1 | 3 | **9** |
| §3.6 Section similarity | 3 | 1 | 2 | **6** + easy-win bonus |
| §3.4 Motif detection | 3 | 2 | 2 | **5** |
| Adjacent #10 Progression library | 3 | 2 | 3 | **6** |
| Adjacent #4 Chord-change drills | 3 | 1 | 2 | **5** |
| §3.1 Tuning detection (conservative) | 3 | 3 | 1 | **5** |
| Adjacent #6 Note-density heatmap | 2 | 1 | 2 | **4** |
| Adjacent #8 Reverb/delay match | 2 | 1 | 3 | **4** + tone-loop bonus |
| Adjacent #3 Tempo curriculum | 2 | 1 | 1 | **3** |
| Adjacent #11 Vibrato mimicry | 2 | 2 | 3 | **4** |
| Adjacent #9 Synth-behaviour callouts | 2 | 1 | 2 | **3** |
| Adjacent #5 Pitch-class drone | 2 | 1 | 1 | **3** |
| Adjacent #7 Right-hand technique | 2 | 3 | 3 | **3** |
| §3.2 Capo detection | 2 | 4 | 1 | **0** — defer |

Composite = `value − (complexity − 3) + (defensibility − 3)`,
i.e. complexity penalty kicks in above 3, defensibility bonus
above 3. Rough heuristic — meant to guide sequencing, not score
products.

## 6. Dependency graph

```
                         ┌──────────────────────────────────┐
                         │   §3.7 Practice guidance (P8.x)  │
                         │       (orchestration layer)       │
                         └────────────────┬──────────────────┘
                                          │
        ┌───────────────────┬─────────────┼─────────────┬──────────────────┐
        │                   │             │             │                  │
        ▼                   ▼             ▼             ▼                  ▼
  §3.3 Difficulty     §3.5 Riff      §3.1 Tuning   §3.6 Section      Adjacent #1
  estimation          extraction     (conservative) similarity        Tone-to-rig
        │                   │                                              │
        │                   │                                              │
        │        ┌──────────┴────────┐                                     │
        │        │                   │                                     │
        ▼        ▼                   ▼                                     ▼
  ┌─────────────────────────────────────────┐                  ┌────────────────────┐
  │  EXISTING SIGNALS (no new analysis):    │                  │  Tone retrieval    │
  │   - tempo / beats / IOI variance        │                  │  (shipped)         │
  │   - chord lane                          │                  └────────────────────┘
  │   - sections + per-section aggregates   │
  │   - guitar-stem MIDI + per-note flags   │
  │   - ClassificationFeatures              │
  │   - pitch_stability (bend / vibrato)    │
  │   - chroma → recurrence_matrix          │
  │   - groove / swing / syncopation        │
  │   - reference_analyzer ProductionStyle  │
  └─────────────────────────────────────────┘
                                          ▲
                                          │
                              ┌───────────┴────────────┐
                              │  §3.4 Motif detection  │
                              │  (recurrence_matrix    │
                              │   composition only)    │
                              └────────────────────────┘

  Deferred (label-bounded or cascade-blocked):
   - §3.2 Capo detection (cascades on tuning + fret-position estimation we don't have)
```

Read this graph as: the **EXISTING SIGNALS** box is the base, every
feature above it composes from it without new analysis, and the
practice-guidance layer orchestrates the per-feature outputs into
the user-facing surface.

## 7. Recommended sequencing

Sequenced for compounding leverage — each step's outputs feed the
next without re-work.

**P8.1 — Wire the prior doc's spectral fields + section similarity.**
The four spectral fields are already the highest-leverage
*contract-level* change (per `SONG_UNDERSTANDING_INVESTIGATION.md`).
Section similarity is the cheapest new capability and uses the same
z-norm L2 the tone catalog already exports. Land them together to
prove the wiring shape.

**P8.2 — Riff extraction.**
The highest-defensibility single feature. Builds the sub-sequence
matching primitive that motif detection will also need. Surface a
new `SongUnderstanding.riffs` contract field; populate from
guitar-stem MIDI + sections.

**P8.3 — Difficulty estimation.**
Composite rubric over signals from P8.1 + P8.2. Per-section heat-map
is the UX win. Hand-tune the rubric against a 30-song calibration
set before declaring `difficulty` populated in the DTO.

**P8.4 — Motif detection.**
`librosa.segment.recurrence_matrix` wrapper + clustering. Populates
the already-declared `SongUnderstanding.motifs` field. Cheaper than
riff extraction because chroma is coarser, but useful for non-MIDI
contexts (e.g., vocal motifs).

**P8.5 — Conservative tuning detection.**
Three-state classifier (`STANDARD` / `STANDARD_HALF_STEP_DOWN` /
`DROP_D` / `UNKNOWN`) over guitar-stem MIDI pitch statistics.
Defensible only because it's surfaced inside the practice loop;
weak as a standalone product.

**P8.6 — Practice guidance UX.**
Compose all of the above into a Jam-page practice surface. This is
where the platform's compounding investment becomes visible to the
user. UX-driven, not analysis-driven — the signals are all in place
by this point.

**Adjacent quick wins** to schedule opportunistically against
above: stem-mix recipes (#2), tone-to-rig translation (#1),
chord-change drills (#4). Each is single-sprint and reinforces the
practice-guidance product.

**Explicitly out of sequence:**
- Capo detection (§3.2) — defer indefinitely; ship a UX dropdown.
- Right-hand technique tagging (#7) — defer until calibration
  labels exist.
- Cross-song progression library (#10) — defer until catalog
  growth justifies the hash design.

## 8. Open questions for product

These are the questions the prompt couldn't answer that the next
design iteration needs to resolve. Recording them here so the
investigation is complete on the analytical side and only product
input is missing.

1. **Difficulty scale shape.** 0-10 numeric? Easy/Medium/Hard
   bucket? Per-dimension breakdown surfaced or hidden? Heat-map
   default-on or expanded?
2. **Riff isolation UX.** Auto-loop on first play? Click-to-loop?
   How many riffs per song before the surface gets noisy?
3. **Practice-guidance scope.** Step-by-step lesson plan, or
   ambient annotations on the existing Jam timeline? Different
   products.
4. **Tuning detection failure mode.** If the detector returns
   `UNKNOWN`, do we silently omit the surface or prompt the user
   to declare it?
5. **Tone-to-rig translation format.** Free-text cheat-sheet, or
   structured pedal-by-pedal table? Affects monitor-chain schema.
6. **Stem-mix recipe presets.** "Drums only," "drums + bass," "no
   guitar" — fixed presets, or sliders?

## 9. Out of scope / non-goals

Recording explicitly so the next pass doesn't re-litigate.

- **Tab transcription.** Not in scope. Out-of-band — requires
  fret-position estimation that's a research problem on processed
  audio. The MIDI surface is what we work with; fret inference is
  a future investigation, not a P8 deliverable.
- **Lyrics / vocals analysis.** Vocal stem exists; lyrics
  recognition is outside the platform's mission.
- **Real-time analysis.** Everything in this map runs against a
  pre-analysed song. Streaming/realtime is its own architecture
  conversation.
- **Catalog expansion.** This map assumes the existing catalog and
  the existing analysis pipeline. New genre coverage is a separate
  axis.
- **ML-driven anything.** Every feature in §3 + §4 is rubric or
  heuristic. Adding a learned model is a separate investment with
  its own dependency graph (labels, training infra, calibration).

## 10. Source-of-truth pointers

For the next engineer picking this up, key entry points:

- **Contracts.** `backend/tone_forge/contracts.py` — `SongUnderstanding`
  at line 231, `Motif` at line 215, `ProductionStyle` not in
  contracts.py (lives in `analysis/reference_analyzer.py:94`).
- **Producers.** `session/bundle.py:_build_understanding` is the
  primary; `tone_forge_api.py:2536` and
  `tone/guitar_catalog.py:82` are fallback builders.
- **Consumers.** `tone/policy.py:104-130` (fallback family
  selection) and `static/jam.js:2093, 2116-2117` (UI rendering).
- **Reference analyzer.** `backend/tone_forge/analysis/reference_analyzer.py`
  — `ProductionStyle` at line 94; this is the dormant reservoir.
- **MIDI provenance.** `backend/tone_forge/midi/passes/*` — per-note
  flags are emitted across these passes; `base.py` defines the
  `NoteFlag` enum.
- **Tone catalog math.** `backend/tone_forge/tone/guitar_catalog.py` —
  `_compute_8_features`, `_znorm_l2`, `_get_catalog`. The same
  z-norm L2 is the natural primitive for §3.6 section similarity.
- **Prior investigation.** `docs/SONG_UNDERSTANDING_INVESTIGATION.md`
  — the contract-level audit this doc widens.

---

This document is research-only. Implementation of any feature
above requires its own design pass (contract changes, producer
wiring, consumer surface, tests). The capability map is a planning
artifact, not a commitment.
