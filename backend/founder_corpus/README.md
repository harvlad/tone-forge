# Founder Validation Corpus

This directory is the load-bearing trust artifact behind every other
Jam feature. The premise is simple:

> A small, fixed set of songs the founder has personally validated as
> "the analyzer got this right", re-run end-to-end through the
> pipeline on demand, with per-field deltas reported. Anything that
> drifts on this corpus is a regression in something guitar-facing —
> by construction.

Without this, every other quality claim ("riff extraction works",
"tone-to-rig is accurate", "stem mix is clean") is unverified. With
it, regressions surface in one place, with the founder's ear as the
arbiter rather than an abstract score.

## Layout

```
founder_corpus/
  manifest.yaml          # the registry — single source of truth
  expected/              # one ground-truth JSON per entry
    <song_id>.json
  audio/                 # founder-curated real audio (gitignored)
    <song_id>.wav
  reports/               # generated Markdown reports
    latest.md            # tracked; always reflects the last run
    YYYY-MM-DDTHH-MM-SSZ.md   # gitignored; historical
  baselines/             # (future) per-entry persisted "last known good"
  README.md              # this file
  .gitignore
```

Synthetic seed audio lives at `../tests/_generated/*.wav` and is
referenced from the manifest by relative path. Real founder-curated
audio is committed nowhere — it goes in `audio/` (gitignored) on the
operator's machine.

## Running

From the repo root:

```bash
# Everything (recommended for ad-hoc audits)
python backend/scripts/run_founder_validation.py

# Just smoke-tier entries (fast subset)
python backend/scripts/run_founder_validation.py --tier smoke

# Custom report path
python backend/scripts/run_founder_validation.py --report-path /tmp/run.md

# Quiet mode (suppresses per-entry stderr progress)
python backend/scripts/run_founder_validation.py --quiet
```

Exit codes:

- `0` — all hard-gated fields passed (soft warns are OK)
- `1` — at least one hard-gated field failed (regression)
- `2` — harness error (manifest unparseable, missing audio, etc.)

Reports always land at `reports/latest.md` plus a timestamped sibling.
Only `latest.md` is tracked in git; the timestamped runs are local.

## CI

A pytest at `backend/tests/test_founder_corpus_integrity.py` runs on
every commit and verifies:

1. The manifest parses.
2. Every entry's `expected` JSON parses and validates.
3. Every entry's `audio` path exists.
4. Every comparator key in every expected JSON is recognised by the
   harness (no typos).

It does **not** run the pipeline. Running the pipeline is the
operator's job (manual or in a separate slower CI lane); the integrity
test exists so that "the corpus is malformed" never silently passes by
slipping through code review.

## Adding an entry

Adding an entry is a deliberate decision. Every entry here is
something we will defend on a per-commit basis.

1. Drop the WAV in `audio/` (or reference a stable existing fixture
   under `../tests/_generated/`).
2. Run the pipeline once on the audio to see what the analyzer
   currently produces. Use that as the *baseline* — not the
   *expectation*. The expectation is what the **founder** says is
   right, not what the pipeline currently emits.
3. Create `expected/<id>.json` with the fields the founder is willing
   to defend. Schema:

   ```json
   {
     "schema_version": 1,
     "song_id": "your_id",
     "source_notes": "human-readable provenance",

     "duration_s":             {"value": 12.3, "tolerance_s": 0.5,  "gate": "hard"},
     "tempo_bpm":              {"value": 120.0, "tolerance_bpm": 2.0, "gate": "hard"},
     "key":                    {"value": "C major", "allow_relative_minor": true, "gate": "soft"},
     "detected_type":          {"value": "guitar", "gate": "hard"},
     "section_count":          {"value": 4, "tolerance": 1, "gate": "soft"},
     "chord_count":            {"min": 4, "max": 20, "gate": "soft"},
     "guitar_midi_note_count": {"min": 8, "max": 200, "gate": "soft"}
   }
   ```

   All top-level keys are **optional**: only fields present are
   checked. Default gate is `soft` (warn-only); promote to `hard`
   only once the founder is willing to fail CI on a regression.

4. Add a row to `manifest.yaml`:

   ```yaml
   - id: your_id
     audio: audio/your_id.wav    # or ../tests/_generated/your_id.wav
     expected: expected/your_id.json
     tier: smoke                  # or 'full'
     notes: "One-line description of what makes this entry interesting"
   ```

5. Run the harness and confirm the report shows `your_id — PASS`. If
   it doesn't, either the expected values are wrong (re-validate by
   ear) or there's a real regression to fix.

6. Commit the manifest + expected JSON. Do **not** commit `audio/`
   contents.

## Removing an entry

Also a deliberate decision: it means the founder no longer treats
this song as a regression-worthy reference. Do not remove entries to
silence failing tests. If a test starts failing, either:

- The pipeline regressed → fix the pipeline.
- The expectation was wrong → re-listen, update the expected JSON.

Removing is reserved for "this song is no longer interesting to
defend" — e.g. it was a stand-in that has been superseded by a better
reference.

## What this corpus is **not**

- **Not a training set.** No model is fit on it. It is held out
  exclusively for human-arbitrated regression detection.
- **Not a benchmark for marketing.** Scores here are not "X% accurate
  vs. competitor Y." They are "no regression on the founder's
  reference set."
- **Not a substitute for the founder's ear.** When a field starts
  failing, the right answer is to *re-listen*, not to widen the
  tolerance to make the test pass.
