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
| `SongFormAggregates` module | pending — B2 |
| `song_form.refine_section_types` | pending — B3 |
| Pipeline wire-in | pending — B4 |
| Canonical-corpus regression + threshold calibration | pending — B5 |

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

When a stem is absent (drums-only song, instrumental track), the
relevant signal stays `0.0` and Stage B abstains — Stage A's label
survives.

## Refinement rules

Stage B applies four rules to Stage A's output, in this order:

1. **CHORUS + `vocal_activity_score < vocal_silence_ceiling` →
   INSTRUMENTAL.**
   One-sided threshold; only flips CHORUS down to INSTRUMENTAL,
   never the reverse.

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

4. **Transitions ending into a CHORUS with `energy_ramp_into_next >
   buildup_ramp_floor` → `SectionTransition.type = "buildup"`.**
   Modifies the transition, not the section. Existing buildup
   transitions are preserved.

Each rule is conservative: when the signal is ambiguous, the
Stage A label survives.

## Thresholds (initial values, calibrated in B5)

```python
SongFormThresholds(
    vocal_silence_ceiling = 0.15,
    prechorus_ramp_floor  = 0.25,
    breakdown_z_ceiling   = -1.0,
    buildup_ramp_floor    = 0.40,
)
```

These defaults are placeholders. B5 sweeps them against the
canonical-6 hand-labelled ground truth and locks the final values
back into this section of the doc.

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
