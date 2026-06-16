# Corpus curation playbook

How to add a real-audio chord-detection fixture to the bench
corpus end-to-end. M2.4's `python -m bench.corpus add`
condenses most of this into one command; the steps below
are the source of truth for what that command does.

## What a fixture is

One fixture = one short song (or song excerpt) where:

* The chord progression is annotated as a list of
  `(start_s, end_s, label)` regions in a JSON file under
  `backend/tests/fixtures/chord_groundtruth/`.
* The mixed audio is available as pre-stemmed `other.wav`
  (everything-but-bass) and optionally `bass.wav`, under
  `backend/data/chord_groundtruth_audio/<name>/`.
* The fixture JSON pins a `regression_floor_triad_relaxed`
  value -- the floor below which the detector is considered
  regressed on this fixture.

Fixtures live in two directories so the audio (potentially
gigabytes, possibly licensed restrictively) can be
gitignored or kept separate from the lightweight metadata.

## Step 1 -- Audio preparation (out of scope for bench)

Bench does NOT run demucs / spleeter / source-separation
itself. The curator brings the audio in already stemmed.

* `other.wav`: drums + harmonic instruments + vocals
  (everything except bass). 22050 Hz mono recommended;
  higher SR is fine.
* `bass.wav` (optional): isolated bass stem. If present,
  the detector uses it for the bass-root prior.

For first-party recordings, demucs output works. For
third-party tracks, `bass.wav` may not be available; the
detector degrades gracefully.

## Step 2 -- Region annotation

Hand-author the JSON region list. Schema:

```json
{
  "song": "my_song_slug",
  "schema_version": 2,
  "split": "test",
  "genre": "rock",
  "license": "first-party",
  "tags": ["chorus-only", "drop-D"],
  "curated_by": "<your name>",
  "duration_s": 42.5,
  "regions": [
    { "start": 0.00,  "end": 4.00, "label": "Am" },
    { "start": 4.00,  "end": 8.00, "label": "F" },
    { "start": 8.00,  "end": 12.0, "label": "C" },
    { "start": 12.0,  "end": 16.0, "label": "G" }
  ],
  "regression_floor_triad_relaxed": 0.0,
  "source_audio_other_stem": "data/chord_groundtruth_audio/my_song_slug/other.wav"
}
```

Required fields (M1): `duration_s`, `regions`,
`regression_floor_triad_relaxed`.

Required v2 fields: `schema_version` (set to `2`),
`split` (`test` is conservative -- never optimised
against).

Region rules (validator-enforced):

* `0 <= start < end <= duration_s`
* `label` is a non-empty string
* `regression_floor_triad_relaxed` in `[0, 1]`

Region labels follow ToneForge's chord vocabulary
(`A`, `Am`, `C#m7`, `F/A`, etc; see `tone_forge.analysis`).

## Step 3 -- Validate the JSON

```
python -m bench.corpus validate path/to/draft.json
```

Exits 0 with `OK` on success, exits 1 and prints errors
on failure. Run this BEFORE invoking `add`; `add` runs
the same validator internally but it's faster to iterate
on the standalone command.

## Step 4 -- Land the fixture

```
python -m bench.corpus add \
    --json path/to/draft.json \
    --other path/to/other.wav \
    --bass  path/to/bass.wav  \
    --measure-floor
```

What this does:

1. Validates the JSON against schema v2.
2. Resolves the fixture name from `--name` or the JSON's
   `song` slug.
3. Copies `other.wav` and `bass.wav` into
   `backend/data/chord_groundtruth_audio/<name>/`.
4. Rewrites `source_audio_other_stem` /
   `source_audio_bass_stem` in the JSON to point at the
   copied files (relative to `backend/` when possible).
5. With `--measure-floor`: runs the production detector
   under the default `DetectorConfig`, computes
   `triad_relaxed_wcsr` vs the JSON's regions, and writes
   the value **rounded DOWN to 0.01** into
   `regression_floor_triad_relaxed`. This mirrors the M1
   `pub_feed` pattern (measured 0.2257 -> pinned 0.22).
6. Writes the final JSON to
   `backend/tests/fixtures/chord_groundtruth/<name>.json`.

The floor is conservative on purpose: it should pass on
the CURRENT detector and only ever be raised in a separate
commit after the detector has demonstrably improved on
that fixture.

## Step 5 -- Confirm the fixture loads

```
python -m bench.benchmark --split test
```

Should include the new fixture in the per-fixture summary
and report a `wcsr_triad_relaxed` value >= the floor.

```
python -m bench.corpus stats
```

Should show the fixture in the per-split / per-license
breakdown.

## Step 6 -- Commit

Commit the new JSON. The audio is intentionally NOT in the
repo by default (see `.gitignore`); first-party audio can
be checked in separately, third-party audio is the
curator's licensing problem.

If you do commit audio, prefer a SEPARATE PR for the audio
files vs the JSON metadata so reviewers can examine them
independently.

## Raising the floor over time

A fixture's `regression_floor_triad_relaxed` is the
acceptance bar. To raise it:

1. Verify the current detector consistently exceeds the
   new floor over multiple `bench.benchmark` runs.
2. Edit the JSON, raising the floor by at most the recent
   measured value rounded DOWN to 0.01.
3. Run `pytest backend/tests/test_chord_eval_regression.py`
   to confirm the new floor holds.
4. Commit with a message that cites the measured WCSR.

The asymmetry (raise floor manually, never lower) is a
ratchet: the detector can never quietly regress on a
fixture.

## Choosing a split

| Split   | When to use                                                   |
| ------- | ------------------------------------------------------------- |
| `test`  | Default. Held out from sweeps; drift = regression.            |
| `train` | Curator-marked OK to optimise against. Sweeps touch this.     |
| `val`   | Intermediate cross-check after a `train` candidate looks good.|
| `holdout` | Reserved for periodic audits; sweeps never see this.        |

The four M1 fixtures (`pub_feed`, `demolition_warning`,
`jump_and_die`, `lets_make_it_pain`) are all
`split: "test"`. The M2 plan deliberately did NOT promote
any of them to `train` -- the corpus is too small for a
statistically meaningful split. That decision is M3+.

## Licensing

Bench supports a closed vocab for the `license` field:

* `first-party` -- the curator (or their organisation)
  owns the recording. Default.
* `cc-by-4.0` -- Creative Commons Attribution.
  Attribution string belongs in `curated_by` or `tags`.
* `cc-by-sa-4.0` -- CC Attribution-ShareAlike. The
  ShareAlike clause has implications if you ever
  redistribute the fixture corpus; check with the
  upstream first.
* `public-domain`
* `proprietary` -- third-party copyrighted. ToneForge has
  NO licence to redistribute the audio; check it in only
  to a private fork.
* `other` -- anything not covered above. Use the
  description field of the JSON to disambiguate.

For any non-`first-party` license, the curator is
responsible for confirming bench's redistribution is
within the license terms. M2 does NOT auto-import any
third-party corpus precisely because each one needs this
review.

## What `bench.corpus add` explicitly does NOT do

* Run demucs / source separation.
* Hit the network. Audio is copied in from the local
  filesystem.
* Auto-generate regions. Region authoring is human work.
* Promote a fixture to `split: train`. The default is
  `test`; the curator must edit the JSON manually to
  change this (after due consideration).
* Lower an existing floor. The ratchet is one-way.
