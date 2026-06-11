# Song Understanding — Investigation Notes

**Priority 8 deliverable.** Investigation-only, per EXECUTION_PLAN.md row 8
("Song Understanding expansion — Investigation only"). This document
records the current state, the per-field feasibility findings, and the
concrete decisions about what (if anything) should graduate from
Phase-3 status into MVP. **No code changes accompany this commit.**

## 1. Contract audit

`SongUnderstanding` (backend/tone_forge/contracts.py:231) has 13 fields:

| Field | Group | Default | Populated today? |
|---|---|---|---|
| `tempo_bpm` | MVP | required | Yes — extracted by `_build_understanding` from descriptor / guitar / bass blobs, falls back to `0.0` |
| `tempo_confidence` | MVP | required | Hardcoded `0.5` when tempo present, `0.0` otherwise |
| `time_signature` | MVP | required | **Hardcoded `(4, 4)`** in every producer |
| `beats_s` | MVP | required | Pass-through from legacy `result["beats_s"]`; often empty |
| `downbeats_s` | MVP | required | Pass-through; often empty |
| `sections` | MVP | required | Pass-through via `_iter_sections`; often empty |
| `chords` | MVP | required | Pass-through via `_iter_chords`; populated when `unified_pipeline._detect_chord_lane` ran |
| `key` | MVP | `None` | Extracted from descriptor / guitar / bass blobs |
| `key_confidence` | MVP | `0.0` | Hardcoded `0.5` when key present |
| `tuning` | Phase 3 | `None` | **Never populated** |
| `capo_fret` | Phase 3 | `None` | **Never populated** |
| `difficulty` | Phase 3 | `None` | **Never populated** |
| `motifs` | Phase 3 | `()` | **Never populated** |

**Phase-3 fields are pre-declared in the DTO** (item #40 of the §0
completion log). This means populating them later requires no contract
churn — only producer changes and consumer wiring.

## 2. Producer audit

Three call sites construct a `SongUnderstanding`:

1. **`session/bundle.py:246` (`_build_understanding`)** — the primary
   path. Reads from a persisted legacy `AnalysisResult` dict. All
   confidence values are conservative defaults (`0.5` or `0.0`), not
   measured.
2. **`tone_forge_api.py:2536`** — minimal builder for the tone
   retrieval fallback when no SessionBundle is available. Tempo and
   key only; everything else empty.
3. **`tone/guitar_catalog.py:82`** — legacy `recommend_from_tempo_key`
   fallback. Tempo and key only.

The richer **`analysis/reference_analyzer.py`** module produces a
separate `ProductionStyle` dataclass (line 94) with reverb profile,
delay pattern, groove/swing, energy class, layering, FX
characteristics. This output does **not** feed `SongUnderstanding`
today — it lives in a parallel pipeline used by reference-match
analysis. Bridging the two is a non-trivial wiring decision (see §4).

## 3. Consumer audit

Only two real consumers exist:

1. **`tone/policy.py:104-130`** (`select_fallback_family`) — reads
   `tempo_bpm` and `key` only. The function has explicit comments
   (lines 107-122) documenting the four spectral-feature branches it
   *would* take if `SongUnderstanding` carried them:
   - `tempo > 140 + heavy spectral centroid → modern_gain`
   - `tempo < 100 + sparse texture + reverb tail → ambient`
   - `mid-tempo + major key + low spectral flux → clean_strat`
   - `mid-tempo + minor/unknown → classic_rock`
   These are unreachable today because the spectral signals are not
   in the bundle. The policy degrades by collapsing each rule to the
   tempo+key proxy.

2. **`static/jam.js:2093,2116-2117`** — reads `tempo_bpm`, `key`,
   `sections`, `beats_s` for UI rendering (header label, timeline
   bar, click track). No consumption of chords, time_signature, or
   any Phase-3 field.

**Implication**: the per-field investigation should weight
*policy-relevant* and *UI-renderable* fields higher than
purely-analytical ones, because nothing else consumes them.

## 4. Per-field feasibility

### 4.1 Beat grid (`beats_s`, `downbeats_s`)

**Verdict: feasible now; modest wiring cost.**

`reference_analyzer.py:185` already calls `librosa.beat.beat_track`
to estimate tempo. The same call returns a beat-frame array that can
be converted to seconds with one line. Downbeats require a heavier
estimator (e.g. `madmom.features.downbeats` or
`librosa.beat.plp` + meter inference); not currently in the
dependency surface.

**Cost**: small. Plumb the beat-track result into the persisted
`AnalysisResult` dict so `_build_understanding` picks it up.

**Open question**: do we ship beats-only (cheap), or beats +
downbeats (adds madmom dependency, ~80MB)? The Jam UI consumes both
fields but degrades gracefully on empty downbeats.

**Recommendation**: Ship `beats_s` from librosa as a free win.
Downbeats deferred until madmom appears in another priority's
dependency closure.

### 4.2 Spectral features for policy (centroid, flux, texture, reverb tail)

**Verdict: feasible now via reference_analyzer bridge; non-trivial
contract decision.**

`reference_analyzer.py` already computes:
- **Reverb tail**: RT60 estimate via spectral decay (line 441-476).
  Produces `style.reverb_profile ∈ {dry, room, hall, infinite}` and
  `style.reverb_amount ∈ [0, 1]`. Direct match for the policy's
  "reverb tail" signal.
- **Delay**: autocorrelation-based detection (line 478-505).
- **Groove / swing**: IOI analysis on detected onsets.
- **Energy class** (`energy_type`): bright / warm / etc.

Not computed today:
- **Spectral centroid (raw)**: trivially added via
  `librosa.feature.spectral_centroid().mean()`.
- **Spectral flux (raw)**: trivially added via
  `np.diff(librosa.feature.spectral_flux(...))` or
  `librosa.onset.onset_strength`.
- **Texture density**: ambiguous metric. Candidates:
  notes-per-second (requires MIDI), spectral entropy, harmonic /
  percussive ratio. **Definition needs to be pinned before
  implementation.**

**Open question**: do we add four spectral fields to
`SongUnderstanding` (contract grows), or expose
`ProductionStyle` as a sibling field (`SongUnderstanding.style:
Optional[ProductionStyle]`), or bypass the contract and let policy
consume `ProductionStyle` directly through a parallel argument?

**Recommendation**: Add the four scalar features
(`spectral_centroid`, `spectral_flux`, `texture_density`,
`reverb_tail_s`) directly to `SongUnderstanding` as new optional
fields. Reasons:
1. The policy's branches already document exactly these four. The
   contract is the right home for "things `policy.select_fallback_family`
   reads".
2. Scalar floats keep the contract serializable; embedding
   `ProductionStyle` (which has 18+ fields including string enums)
   couples the contract to an analysis output that is itself
   in-flight.
3. The fields can land empty (Optional[float] = None); policy already
   degrades gracefully on missing signals.

**This is the single highest-leverage P8 outcome**: it unlocks the
four documented policy branches that `tone/policy.py` already has
written but cannot execute. Estimated wiring: small (add fields,
have `_build_understanding` read from a new `result["spectral"]`
dict produced upstream).

**Out of scope for this investigation**: who produces the
`result["spectral"]` dict. Probably `unified_pipeline` calls
`reference_analyzer.analyze` and forwards relevant fields, but the
exact integration point is a follow-up decision.

### 4.3 Tuning detection

**Verdict: not feasible without labeled data; defer to Phase 3.**

Candidate approaches surveyed:
1. **MIDI-based**: extract lowest-played notes per stem; check
   whether the open-string set matches standard (E2 A2 D3 G3 B3 E4)
   or a known drop tuning. Requires reliable MIDI extraction of the
   guitar stem — frozen subsystem, current accuracy unknown for
   open-string detection specifically.
2. **Spectral**: detect open-string fundamentals via peak detection
   on the 60-400 Hz band. Brittle in dense mixes.
3. **Pretrained**: no off-the-shelf "guitar tuning detector" in the
   open MIR ecosystem (madmom, librosa, essentia, basic-pitch all
   silent on this).

**Blocker**: no labeled dataset. Even a feasibility prototype needs
~20 hand-labeled clips spanning standard / drop-D / drop-C / open-G /
half-step-down to establish a baseline.

**Recommendation**: Phase 3, blocked on labeling effort. Add a TODO
in policy / UI only if a downstream consumer ever materializes
(today: zero consumers).

### 4.4 Capo detection

**Verdict: feasible *only* if tuning detection lands; otherwise
defer.**

Capo is a constant pitch shift of all open strings. Detection
requires knowing baseline tuning to spot the offset. The cleanest
formulation:
1. Detect open-string fundamentals.
2. If they form a transposed standard-tuning set, the offset is the
   capo fret.

In practice the MIDI extractor doesn't tell us *which* note is on an
open string vs. fretted, so this collapses to the same blocker as
tuning detection (§4.3).

**Recommendation**: Phase 3. Consider the UX alternative — let the
user declare capo on the Jam page when needed — before investing
detection effort.

### 4.5 Difficulty rating

**Verdict: out of scope; no definition, no consumer.**

No code today reads `difficulty`. No design document specifies what
"difficulty" means for ToneForge (player skill required? rhythmic
complexity? fret-position stretch?). No labeled corpus.
Comparable products (Yousician, Rocksmith) treat difficulty as a
product-team-curated rating, not an ML output.

**Recommendation**: Remove from priority list until a concrete
consumer is identified. The contract field can stay — it costs
nothing to leave declared.

### 4.6 Motif fingerprinting

**Verdict: out of scope for MVP; potentially useful Phase 3.**

The `fingerprint/` submodule exists but is designed for *preset*
matching (tone retrieval), not motif extraction (musical phrase
similarity). The two are unrelated algorithmically.

Open-source candidates for motif detection (audio-domain):
- **Repetition detection**: self-similarity matrix from librosa
  `librosa.segment.recurrence_matrix`. Outputs "this section
  resembles that section". Cheap, already used implicitly by
  `_analyze_sections`.
- **Symbolic motif mining**: requires MIDI; vast literature
  (pattern-matching on note sequences). No drop-in library.
- **Audio fingerprinting (Shazam-style)**: detects *exact* repeats,
  not musical motifs. Wrong tool.

The session-replay use case ("show me where the chorus riff
recurs") could be served cheaply by exposing the recurrence-matrix
output as `motifs`. Whether that's the right shape vs.
audio-fingerprint Motif tuples is a UX question.

**Recommendation**: Phase 3, contingent on a concrete UI hook. The
recurrence-matrix path is the cheap one to prototype first if the
priority is ever picked up.

## 5. Other observations

### 5.1 Hardcoded `time_signature=(4, 4)`

Every producer hardcodes `(4, 4)`. No consumer currently reads
`time_signature`. Detecting non-4/4 meters (3/4, 6/8, 7/8) requires
beat-grouping inference (madmom has a meter tracker; librosa does
not). **Mark as silently-broken-but-harmless**: no downstream wants
it. If a consumer ever needs meter, this is a small dedicated spike
(estimate: one day with madmom; tracked separately from the rest of
P8 if it ever lands).

### 5.2 Tempo confidence is fabricated

All producers set `tempo_confidence = 0.5` when a tempo is present
and `0.0` otherwise. The actual `librosa.beat.beat_track` does
return a confidence-shaped value but it is currently discarded at
the producer boundary. **Cheap improvement**: forward the
beat-track confidence (mapped to [0, 1]) instead of hardcoding 0.5.
Same applies to `key_confidence`.

### 5.3 Chord pipeline is wired

The Explore agent's preliminary survey suggested chords might be
"orphaned"; verification disproves that.
`unified_pipeline._detect_chord_lane` (line 1366) writes chords
into the persisted dict (line 1506), and `_build_understanding`
reads them. The Priority-4 chord landing already closed this loop.

## 6. Decisions

| Field | Decision | Rationale |
|---|---|---|
| `beats_s` from librosa | **Promote to MVP** | Free win; producer already calls beat_track. |
| `downbeats_s` via madmom | **Defer** | Adds ~80MB dep for one use case. |
| `tempo_confidence` measured | **Promote** | Bug-class; producer fabricates instead of forwarding. |
| `key_confidence` measured | **Promote** | Same as above. |
| 4 spectral fields | **Promote to MVP** | Single highest-leverage change; unlocks 4 dormant policy branches. Add as Optional[float] to contract. |
| `time_signature` detection | **Defer** | No consumer; dead field today. |
| `tuning` | **Phase 3** | Blocked on labeling. |
| `capo_fret` | **Phase 3** | Cascading from tuning. UX alternative preferred. |
| `difficulty` | **Indefinitely deferred** | No definition, no consumer. |
| `motifs` | **Phase 3** | Pending UI hook. |

## 7. Concrete next-step priority items

If P8 is ever promoted from investigation to active, the ordered
work list is:

1. **Forward measured confidence** for tempo and key (replace
   hardcoded `0.5`). Smallest change; bug-class.
2. **Wire librosa-derived `beats_s`** through `_build_understanding`.
3. **Spectral features for policy**: add four `Optional[float]`
   fields to `SongUnderstanding` (`spectral_centroid`,
   `spectral_flux`, `texture_density`, `reverb_tail_s`); have
   `_build_understanding` read them from a new persisted dict slot
   produced by `unified_pipeline` via `reference_analyzer.analyze`.
   Update `tone/policy.py` branch logic to consume the real signals
   instead of degrading.
4. **Downbeats / meter**: only if a consumer materializes.
5. **Tuning / capo / motifs / difficulty**: not before consumer
   identification + labeling effort.

The first three items are independent enough to land as separate
focused commits when picked up. None are blockers for current P6 /
P7 work.

## 8. Out of scope for this investigation

- Producing labeled datasets for tuning / capo / difficulty.
- Implementing any of the items in §7. This document is the
  research artifact; implementation is a separate priority
  decision.
- Changing the contract. Phase-3 fields stay declared.
- Bridging `ProductionStyle` into the bundle pipeline. That is the
  candidate integration point for §7 item 3 but the exact wiring
  belongs to whoever picks the work up.

## 9. References

- `backend/tone_forge/contracts.py:231-252` — `SongUnderstanding` DTO
- `backend/tone_forge/session/bundle.py:246-271` — primary producer
- `backend/tone_forge/tone/policy.py:104-130` — primary consumer +
  documented dormant branches
- `backend/tone_forge/analysis/reference_analyzer.py:432-505` —
  reverb / delay detection (the spectral-features candidate source)
- `backend/tone_forge/unified_pipeline.py:1366` —
  `_detect_chord_lane` (chord wiring closed)
- `backend/static/jam.js:2093,2116` — UI consumer (tempo, key,
  sections, beats)
- `EXECUTION_PLAN.md` row 8 + §0 items 39-40 — priority framing
