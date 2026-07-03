# Song-Form Classifier — Design

> Stage B of the H2-First Section Naming milestone. Sits on top of
> Stage A (`tone_forge/analysis/section_naming.py`), which already
> derives `INTRO / VERSE / CHORUS / BRIDGE / OUTRO` from H2 chord-
> trigram recurrence roles.
>
> Stage B refines those labels using **per-stem evidence** —
> vocal activity, drum density, energy ramp shape — to disambiguate
> labels that H2 alone cannot see.

## Status

| Item | Status |
|------|--------|
| Design doc skeleton | shipped — B1 |
| `SectionType.INSTRUMENTAL` enum value | shipped — B1 |
| `SongFormAggregates` module | shipped — B2 |
| `song_form.refine_section_types` | shipped — B3 |
| Pipeline wire-in (both backends) | shipped — B4 |
| No-vocals-stem misfire fix | shipped — B5a |
| `energy_z` aggregate + Pass 0 edge-demotion | shipped — B5b |
| Canonical-corpus regression test scaffold | shipped — B5c |
| Canonical-6 ground-truth labels populated | **pending** — needs canonical bundles in `data/history.json` |
| Threshold calibration sweep against canonical-6 | **pending** — blocked on ground truth |

## Premise

Stage A's H2 → SectionType derivation gets right what chord-trigram
recurrence can see:

* Recurring trigrams → ANCHOR → CHORUS (the song's hook progression)
* Single-occurrence trigrams at the song edges → INTRO / OUTRO
* Trigrams that recur partially or share suffix structure →
  DEVELOPMENT → VERSE

But Stage A is blind to:

1. **Vocal presence.** A guitar-solo "chorus" that uses the
   same chord progression as the sung chorus shows up as
   another ANCHOR → CHORUS. Musically it's an *instrumental*
   pass over the chorus changes.
2. **Energy/density ramp shape.** A VERSE that ramps in
   intensity into the next CHORUS is a *prechorus*. H2 can't
   distinguish ramped DEVELOPMENT from flat DEVELOPMENT.
3. **Drum-lane texture.** A section where the drums drop out
   inside an otherwise high-energy passage is a *breakdown*.
   This is a per-stem signal H2 doesn't consume.
4. **Transition character.** A short energy ramp at the *end*
   of one section into the *start* of the next isn't a section
   itself — it's a transition annotation (`SectionTransition.type`).

Stage B's job is to use the per-stem MIDI features that already
exist (`section_features.SectionFeatures`, computed per stem in
`unified_pipeline.py:840-861`) to apply these four refinements.

## Signal taxonomy

Stage B consumes a small per-section aggregate computed from the
per-stem `SectionFeatures` rows. Four scalars, one record per
section:

| Aggregate | Source | Used to detect |
|-----------|--------|----------------|
| `vocal_activity_score` | vocals-stem `lead_activity_score × voiced_frame_ratio` | INSTRUMENTAL |
| `drum_density_per_s` | drums-stem `note_count / duration_s` | BREAKDOWN (and as input to density z-score) |
| `drum_density_z` | robust z-score of `drum_density_per_s` across the song (median + MAD, with a floor to avoid all-zero-drum songs) | BREAKDOWN |
| `energy_ramp_into_next` | `(next.energy_mean − this.energy_mean) / this.energy_mean` | PRECHORUS, BUILDUP transition |
| `energy_z` | robust z-score of `energy_mean` across the song (median + MAD, stdev fallback; no density floor) | Edge-demotion (riff-uniform-song INTRO/OUTRO recovery) |

When a stem is absent (drums-only song, instrumental track), the
relevant signal stays `0.0` and Stage B abstains — Stage A's label
survives.

## Refinement rules

Stage B applies six rules to Stage A's output, in this order:

0. **Edge-demotion — CHORUS at first/last position with
   `energy_z < edge_energy_z_ceiling` → INTRO/OUTRO.**
   Catches riff-uniform songs where every section shares the same
   chord progression (e.g. Birds of Tokyo — "If This Ship Sinks"):
   H2 sees ANCHOR everywhere, Stage A maps every section to CHORUS,
   but a clearly-lower-energy edge gives away the true intro/outro.
   Runs first so that low-vocals edges (e.g. a quiet intro with
   whisper-soft vocals) are demoted to INTRO before rule 1 would
   otherwise re-classify them as INSTRUMENTAL.
   One-sided: only demotes CHORUS at the edges; never promotes.

1. **CHORUS + `vocal_activity_score < vocal_silence_ceiling` →
   INSTRUMENTAL.**
   One-sided threshold; only flips CHORUS down to INSTRUMENTAL,
   never the reverse. Gated on
   `any(a.vocal_activity_score > 0 for a in aggregates)` so the
   rule no-fires on songs whose bundle has no vocals stem at all
   (B5a fix).

2. **VERSE + next section is CHORUS + `energy_ramp_into_next >
   prechorus_ramp_floor` → PRECHORUS.**
   The VERSE-immediately-before-CHORUS with ramp pattern. Looks at
   refined Stage A output, so an INSTRUMENTAL chorus (rule 1) is
   not treated as a chorus for prechorus detection (matches musical
   intuition — instrumental passes don't have prechoruses).

3. **`drum_density_z < breakdown_z_ceiling` →
   BREAKDOWN.**
   Applies to any current type; the drum-lane drop is the dominant
   signal. A section that's already INTRO/OUTRO stays as-is — edges
   override breakdowns.

4. **CHORUS → VERSE demotion.**
   Fires only when Stage A produced at least
   `verse_demotion_min_choruses` CHORUSes — below that the
   intra-CHORUS median is too noisy to trust and the pass
   abstains. Computes the intra-CHORUS medians of `energy_z`
   and `vocal_activity_score`; any CHORUS whose `energy_z` is
   at least `verse_demotion_z_offset` below the median **and**
   whose `vocal_activity_score` is below
   `verse_demotion_vocal_ratio × median_vocals` is demoted to
   VERSE. Both signals must independently agree — energy_z
   alone false-positives on songs with a quiet chorus variant;
   vocal_activity_score alone false-positives on instrumental
   passes Rule 1 didn't catch.
   One-sided: never promotes VERSE to CHORUS. Preserves at
   least one CHORUS in the song (sanity check inside the loop).
   Motivating case: pop-punk / folk / any genre where verse
   and chorus share the same chord progression, so H2 chord-
   trigram recurrence collapses to ANCHOR on every section
   (Paramore "That's What You Get" is the canonical fixture).
   Runs before the transition annotator so buildup detection
   sees the final labels.

5. **Transitions ending into a CHORUS with `energy_ramp_into_next >
   buildup_ramp_floor` → `SectionTransition.type = "buildup"`.**
   Modifies the transition, not the section. Existing buildup
   transitions are preserved.

Each rule is conservative: when the signal is ambiguous, the
Stage A label survives.

## Thresholds (initial values, calibrated in B5)

```python
SongFormThresholds(
    vocal_silence_ceiling       = 0.15,
    prechorus_ramp_floor        = 0.25,
    breakdown_z_ceiling         = -1.0,
    buildup_ramp_floor          = 0.40,
    edge_energy_z_ceiling       = -1.0,
    verse_demotion_min_choruses = 4,
    verse_demotion_z_offset     = 0.35,
    verse_demotion_vocal_ratio  = 0.75,
)
```

The Pass 4 fields are conservative initial values — I'd rather
leave a real verse mislabelled as CHORUS than smuggle a chorus
into VERSE. `verse_demotion_z_offset = 0.35` ≈ half a MAD-scaled
standard deviation; `verse_demotion_vocal_ratio = 0.75` gives
real headroom for pop-punk verses (sung with similar intensity
to the chorus) while still catching hushed / whispered / low-
density verse vocals. Full calibration will follow once
canonical-6 ground truth grows `verse_indices` for the pop-punk
bundle (`29b31695`).

These defaults are placeholders. B5c shipped the regression test
scaffold (`backend/tests/test_song_form_canonical.py`) and the
real-world Birds-of-Tokyo regression that locks down the visible
"every section labeled chorus" fix. Full calibration — sweeping
the four thresholds against canonical-6 ground truth — is
**blocked on populating the canonical bundles in
`data/history.json`** and hand-labelling the
`SONG_FORM_GROUND_TRUTH` recall floors in
`test_song_form_canonical.py`. The canonical bundles are still
absent at the time B5c shipped; the test infrastructure skips
gracefully and waits.

## Calibration corpus

Same six bundles as `test_role_classifier_canonical.py`:

| Bundle ID | Slug | Why it's calibration-useful |
|-----------|------|------------------------------|
| `73b5931b` | Stairway to Heaven | Long instrumental intro; vocals enter late → INSTRUMENTAL recall test |
| `07320370` | Hotel California | Long instrumental outro (solo) → INSTRUMENTAL recall test |
| `9fb65b01` | Wish You Were Here | Repeated CHORUS with stable vocals → INSTRUMENTAL must NOT fire (precision) |
| `5365ab83` | Romance de Amor | Fully instrumental → every section INSTRUMENTAL (if any CHORUS at all) |
| `b640c78a` | Sex on Fire | Clean verse-chorus-verse with vocal presence throughout → no INSTRUMENTAL |
| `29b31695` | What's My Age Again | Pop-punk with prechorus ramp → PRECHORUS recall test |

Ground truth is embedded in `test_song_form_canonical.py` as
`SONG_FORM_GROUND_TRUTH`, keyed by bundle ID, with three lists per
song: `instrumental_indices`, `prechorus_indices`,
`breakdown_indices`. Empty lists mean "no assertion" — the test is
a recall floor, not a precision lock. Precision drift is caught by
re-running the existing `test_section_naming_canonical.py` Stage A
test (which asserts the Stage A vocabulary set); if Stage B
incorrectly flips a CHORUS to BREAKDOWN, that test catches it
because BREAKDOWN is outside Stage A's vocabulary.

## Validation methodology

Per canonical bundle:

1. Full chain runs: `extract_h2 → classify_roles →
   derive_section_types → aggregate_song_form → refine_section_types`.
2. Refined labels compared against `SONG_FORM_GROUND_TRUTH`:
   - For each `instrumental_indices` entry, assert the refined
     label at that index is `INSTRUMENTAL`.
   - Same for `prechorus_indices` and `breakdown_indices`.
3. Stage A non-regression: derived count matches decision count;
   at least one CHORUS or INSTRUMENTAL remains (every canonical
   bundle has at least one ANCHOR role).

Precision/recall numbers aren't computed in the test; the test is
binary pass/fail on the labelled positions. When ground truth grows
(via held-out validation bundles in a follow-up plan), precision
becomes meaningful.

## Open questions

- **"Instrumental verse" — does H2 + vocal-silence catch it?** If
  the song has a verse-shaped section (DEVELOPMENT role) with no
  vocals, should it be VERSE-with-no-vocals or some new
  INSTRUMENTAL_VERSE? Current decision: leave it as VERSE.
  INSTRUMENTAL is reserved for the CHORUS-shaped instrumental
  pass; that's the most musically salient case.
- **PRECHORUS chains.** What if two consecutive VERSE sections
  both ramp into CHORUS? Current decision: only the section
  immediately before CHORUS becomes PRECHORUS; the earlier
  VERSE stays.
- **BREAKDOWN at song edges.** A section labelled INTRO that has
  low drums shouldn't become BREAKDOWN — the edge label is more
  important. Rule 3 leaves edges alone.
- **~~CHORUS → VERSE demotion~~** — **shipped in Pass 4.**
  Previously the classifier could not distinguish verse from
  chorus when H2 chord-trigram recurrence collapsed to ANCHOR on
  every section (pop-punk / folk / any genre where verse and
  chorus share the same chord progression). Pass 4 uses the
  per-stem `energy_z` and `vocal_activity_score` signals — both
  must independently agree — to demote low-signal CHORUSes to
  VERSE. Initial thresholds are conservative; canonical-corpus
  calibration will follow.

- **~~CHORUS → VERSE demotion by vocal pitch~~** — **shipped in
  Pass 4b.** Pass 4 covers the case where verses are quieter
  and less vocally busy than choruses, but it misses shared-
  progression songs where verse and chorus are performed at
  matched intensity (driven pop-punk being the reference case:
  Paramore "That's What You Get" — 12/14 sections came out as
  CHORUS after Pass 4). Pass 4b introduces a new upstream
  signal — vocal pitch statistics per section — derived from
  the vocals-stem MIDI notes already computed in
  `compute_section_features`. Two new `SectionFeatures` fields
  (`pitch_median_semitones`, `pitch_range_semitones`) flow into
  two new `SongFormAggregates` fields
  (`vocal_pitch_median_semitones`, `vocal_pitch_range_semitones`).
  A CHORUS is demoted to VERSE only when BOTH the median and the
  p90-p10 spread dip below the intra-CHORUS cohort — a
  conservative AND gate:

    * median-only would over-fire on brief low-pitch chorus
      moments (a chorus opening on a low note);
    * range-only would flip monotone choruses;
    * both together match the actual verse fingerprint — the
      singer sits lower AND has less pitch mobility.

  0.0 is the "no evidence" sentinel and is excluded from both
  the cohort and the candidate set; instrumental sections,
  no-vocal-note sections, and non-vocal songs all abstain
  cleanly. Preserves at least one CHORUS, same guardrail as
  Pass 4. Thresholds default to `verse_pitch_semitone_offset =
  2.0` (≈ a whole tone below cohort median) and
  `verse_pitch_range_ratio = 0.75` (matching the Pass 4 vocal
  ratio). Canonical-6 anchors are unchanged: the pass either
  finds no dip meeting both gates on those bundles (typical
  case) or is filtered out by the pitch-evidence guard on
  instrumental sections.

## Boundary considerations

`tone_forge/analysis/song_form.py` and
`tone_forge/analysis/song_form_aggregates.py` import only:

- `tone_forge.analysis.sections` (own subsystem)
- stdlib + numpy

They do **not** import `tone_forge.song_form` (the existing H2
classifier package), `tone_forge.stems`, or any other subsystem.
Per-stem features cross into Stage B as a plain
`Mapping[str, Sequence]` (a duck-typed `SectionFeatures` row); the
composition layer (`unified_pipeline.py`,
`local_engine/analysis_worker.py`) reads the concrete features and
hands them off.
