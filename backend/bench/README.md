# bench/ - ToneForge chord-detector benchmark + sweep harness

The evidence engine for the self-improving chord-recognition
platform. Runs the production chord detector against a curated
corpus of fixtures, measures a panel of accuracy + cost metrics,
and supports parameter sweeps that propose changes to
`DetectorConfig` for human review.

## Design principles

1. **Read-only against `tone_forge`.** `bench` imports from
   `tone_forge.analysis` (`detect_chords_from_audio`,
   `DetectorConfig`, `chord_eval`). `tone_forge` must NEVER import
   from `bench`. Enforced by
   `backend/tests/test_bench_import_boundary.py`.
2. **`song_validation` is untouched.** That subsystem owns
   runtime curation; the benchmark is out-of-band evidence
   generation and the two never share a process.
3. **Not exposed via HTTP.** Benchmarking happens out-of-band of
   the production API surface. No FastAPI route, no JAM UI
   hook.
4. **Not a decision engine.** Sweeps produce evidence; a human
   reviews `runs/sweep_<id>/accepted.json` and decides whether to
   change `DetectorConfig` defaults in a separate commit.
5. **Curator-driven corpus expansion.** M2 added schema v2,
   split-aware filtering, and the `python -m bench.corpus`
   curator CLI. M2 does NOT bulk-import third-party datasets
   (Isophonics / McGill Billboard / MIREX / Songsterr); each
   needs its own licensing review (deferred to M2.5+).
6. **No new runtime dependencies.** The only new dependency is
   `PyYAML`, declared in `requirements-dev.txt` (NOT
   `requirements.txt`). Production has no compile- or run-time
   awareness of `bench`.

## CLI

### `python -m bench.benchmark`

Run the corpus benchmark once under a single `DetectorConfig`.

```
python -m bench.benchmark
    [--config <path.json>]    # optional DetectorConfig JSON override
    [--corpus <dir>]          # alternative fixtures dir
    [--output <path>]         # default: bench/runs/<run_id>.json
    [--quiet | --json-only]
    [--no-require-audio]      # include fixtures with no audio on disk
    [--split S]               # restrict to fixtures with split=S
                              # (M2.5; repeatable; default: all splits)
```

Outputs a `RunRecord` JSON to `bench/runs/<run_id>.json` and a
human-readable summary to stdout (suppressed by `--quiet`,
replaced by just the JSON path with `--json-only`). With no
`--config`, the default `DetectorConfig()` reproduces the pre-M1
behaviour bit-for-bit; the corpus mean should be 0.7897.

`--split` (M2.5) restricts the corpus to fixtures whose
`split` field is one of the listed values (see "Schema v2"
below). The recorded `RunRecord.splits` lets downstream
sweep comparisons stay apples-to-apples. Without `--split`,
every fixture loads (the M1 invariant).

DetectorConfig override JSON looks like::

    {
      "diatonic_bias": 0.15,
      "bass_root_bias": 0.05
    }

Unknown fields raise loudly. Fields not mentioned inherit
`DetectorConfig()` defaults.

### `python -m bench.sweep <space.yaml>`

Run a parameter sweep against a baseline.

```
python -m bench.sweep <space.yaml>
    [--baseline <run_id|path>]   # comparison anchor; default: fresh baseline
    [--workers N]                # advisory; sweep is currently serial
    [--output <dir>]             # default: bench/runs/sweep_<id>/
    [--corpus <dir>]             # propagated to benchmark
    [--split S]                  # restrict corpus to fixtures with
                                 # split=S; applied uniformly to the
                                 # baseline and every candidate
                                 # (M2.5; repeatable; default: all)
```

Produces a sweep directory with:

* `baseline.json` -- the baseline `RunRecord` (when computed fresh)
* `candidate_<idx>.json` -- one `RunRecord` per candidate
* `accepted.json` -- candidates that cleared the acceptance gate,
  sorted by corpus delta (descending)
* `index.csv` -- per-candidate flat summary for ad-hoc inspection

### `python -m bench.corpus` (M2.4)

Corpus-curator CLI. Three subcommands.

```
python -m bench.corpus stats [--fixtures-dir DIR] [--split S] [--json]
    Tabulate counts per split / genre / license, total duration,
    fixture roster. Plain text by default; `--json` emits a
    machine-readable summary on stdout.

python -m bench.corpus validate <fixture.json>
    Run `bench.schema.validate_fixture_json` against a fixture
    JSON. Print errors to stderr and exit 1 on any failure;
    print "OK" and exit 0 when the file is valid.

python -m bench.corpus add --json <path> --other <audio.wav>
                          [--bass <audio.wav>] [--name NAME]
                          [--measure-floor]
                          [--fixtures-dir DIR]
                          [--audio-dir DIR]
    Curator workflow:
    1. Validate <json> against schema v2.
    2. Resolve fixture name (from --name or the JSON's "song" slug).
    3. Copy --other and --bass audio into <audio-dir>/<name>/.
    4. Update the JSON's source_audio_other_stem / _bass_stem to
       point at the copies (relative to backend/ when possible).
    5. If --measure-floor: run the production detector under the
       default DetectorConfig, compute triad_relaxed_wcsr vs the
       JSON's regions, write the value rounded DOWN to 0.01 into
       regression_floor_triad_relaxed. Mirrors the M1 pub_feed
       pattern (0.2257 measured -> 0.22 pinned).
    6. Write the final JSON to <fixtures-dir>/<name>.json.
```

See `bench/CURATION.md` for the end-to-end playbook (audio
preparation, region annotation, validate/add invocation,
licensing review).

## Schema v2 (M2.0)

Fixture JSONs gained six optional fields in M2. Defaults
preserve M1 behaviour (legacy v1 JSONs load unchanged).

| Field              | Type           | Default        | Vocab / Notes |
| ------------------ | -------------- | -------------- | ------------- |
| `schema_version`   | `int`          | `1`            | `{1, 2}`. Missing or `1` = legacy. |
| `split`            | `str`          | `"test"`       | `{train, val, test, holdout}`. M1 fixtures are all `test` (held-out regression anchors). |
| `genre`            | `str` / null   | `null`         | Free-form (`"rock"`, `"punk"`, ...). `null` surfaces in stats as `"unspecified"`. |
| `license`          | `str`          | `"first-party"`| `{first-party, cc-by-4.0, cc-by-sa-4.0, public-domain, proprietary, other}`. |
| `tags`             | `list[str]`    | `[]`           | Free-form. Curator-meaningful labels (`"power-chords"`, `"baseline-captured"`, ...). |
| `curated_by`       | `str` / null   | `null`         | Free-form attribution. |
| `added_at_unix`    | `int` / null   | `null`         | Optional; written by `bench.corpus add`. |

### Split semantics

* `test` -- held-out regression anchor. **Never** swept
  against. Drift here is a regression.
* `train` -- sweep-optimised. Sweeps that improve `train`
  corpus mean (without dropping any `test` fixture beyond
  the per-fixture cap) are candidates for promotion.
* `val` -- intermediate signal. Used for cross-checking
  candidates that look good on `train` before promoting.
* `holdout` -- never touched by sweeps **or** test runs.
  Reserved for periodic end-to-end audits.

M2 ships the metadata rail. Statistically-principled
splitting (stratification, k-fold, leak prevention) is M3+
when the corpus is large enough for it to matter.

## YAML sweep-space schema

```yaml
strategy: random              # grid | random | coordinate_descent
seed: 1729                    # used by random; ignored for grid
budget: 64                    # max candidates for random

acceptance:
  corpus_metric: wcsr_triad_relaxed_mean
  corpus_must_strictly_improve: true
  max_per_fixture_drop_pp: 5.0    # absolute percentage points
  max_runtime_factor: 2.0         # wall_seconds_mean cap
  max_memory_factor: 1.5          # peak_rss_mb_max cap

parameters:
  diatonic_bias:   {type: float, range: [0.05, 0.20], step: 0.025}
  bass_root_bias:  {type: float, range: [0.00, 0.10], step: 0.025}
  # ... etc, one entry per DetectorConfig field to sweep
```

See `bench/spaces/baseline_neighborhood.yaml` for a worked
example. Parameter names must match `DetectorConfig` field names
exactly; unknown names raise.

### Strategy semantics

* `grid` -- cartesian product of all axes. Watch out for
  combinatorial explosion (6 axes * 7 steps each = 117,649
  candidates).
* `random` -- shuffle the full grid with `seed` and keep the
  first `budget` candidates. Deterministic per seed.
* `coordinate_descent` -- anchor at each axis's median, then
  walk one axis at a time. Cost is `sum(len(axis)) - len(axes) + 1`
  candidates, much smaller than grid.

## Acceptance gate (M1.6)

A candidate is ACCEPTED iff ALL of:

1. `candidate.corpus.<corpus_metric> > baseline.corpus.<corpus_metric>`
   (when `corpus_must_strictly_improve: true`).
2. For every fixture: `baseline.wcsr_triad_relaxed -
   candidate.wcsr_triad_relaxed <= max_per_fixture_drop_pp/100`.
3. `candidate.corpus.wall_seconds_mean <=
   max_runtime_factor * baseline.corpus.wall_seconds_mean`.
4. `candidate.corpus.peak_rss_mb_max <=
   max_memory_factor * baseline.corpus.peak_rss_mb_max`.

A candidate is REJECTED if it fails ANY rule. The reject reason
is recorded on the candidate's `RunRecord.rejection_reason`.

The gate is a pure function (`bench.sweep.evaluate_acceptance`)
so test coverage in `test_bench_sweep_gate.py` exercises every
branch in isolation without running the detector.

## Metrics panel (M1.2)

Each `FixtureResult` carries:

| Field                          | Source                                       |
| ------------------------------ | -------------------------------------------- |
| `wcsr_triad_relaxed`           | `chord_eval.triad_relaxed_wcsr` (primary)    |
| `wcsr_strict`                  | `chord_eval.wcsr`                             |
| `chord_error_rate`             | `bench.metrics.chord_error_rate`             |
| `boundary_iou_0p5`             | `bench.metrics.boundary_iou` (tol_s=0.5)     |
| `region_stability_per_min`     | `bench.metrics.region_stability`             |
| `expected_calibration_error`   | `bench.metrics.expected_calibration_error`   |
| `wall_seconds`                 | `time.perf_counter` around detect call       |
| `peak_rss_mb`                  | `resource.getrusage(RUSAGE_SELF)`            |

`CorpusResult` aggregates the same metrics as unweighted means
across fixtures, plus `peak_rss_mb_max` (max, not mean) for the
memory acceptance check.

## Module layout

```
backend/bench/
├── __init__.py              # Public surface (currently empty by design)
├── __main__.py              # `python -m bench` subcommand dispatcher
├── README.md                # This file
├── CURATION.md              # M2.8 curator playbook
├── corpus.py                # CorpusFixture + iter_corpus_fixtures + curator CLI
├── schema.py                # Fixture-JSON validator (M2.2)
├── metrics.py               # Six pure metric functions
├── benchmark.py             # `bench.benchmark` CLI + run_benchmark
├── sweep.py                 # `bench.sweep` CLI + acceptance gate
├── store.py                 # RunRecord / FixtureResult / CorpusResult
├── spaces/                  # Example sweep-space YAML files
│   └── baseline_neighborhood.yaml
└── runs/                    # gitignored sweep outputs
```

## What this package explicitly does NOT do (M1.9)

These directives from the M1 plan are deferred to later milestones:

* Phase 1 corpus expansion substrate -- done in M2 (this milestone).
  Real-corpus ingestion (Isophonics / Billboard / Songsterr) is M2.5+.
* Phase 4 disagreement runtime DB -- M4.
* Phase 5 failure pattern mining -- M5.
* Phase 6 ranked alternatives in detector output -- M3.
* Phase 7 pluggable chroma frontend -- M3. The opt-in
  `quality_switch_penalty` / `hcdf_snap_radius_frames` levers in
  `DetectorConfig` are the templates.
* Phase 8 tab-guided training loop -- M4+.
* Phase 9 autonomous self-correction loop -- M6. M1 sweeps are
  operator-launched; no daemon, no auto-promotion.

The platform is "self-improving" in the M6 sense: it generates
evidence, surfaces failure patterns, and proposes parameter
adjustments. Final acceptance is always a human commit.
