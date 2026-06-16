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
5. **Not a corpus expander.** M1 uses only the 4 existing
   fixtures in `backend/tests/fixtures/chord_groundtruth/`.
   Corpus expansion is M2.
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
```

Outputs a `RunRecord` JSON to `bench/runs/<run_id>.json` and a
human-readable summary to stdout (suppressed by `--quiet`,
replaced by just the JSON path with `--json-only`). With no
`--config`, the default `DetectorConfig()` reproduces the pre-M1
behaviour bit-for-bit; the corpus mean should be 0.7897.

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
```

Produces a sweep directory with:

* `baseline.json` -- the baseline `RunRecord` (when computed fresh)
* `candidate_<idx>.json` -- one `RunRecord` per candidate
* `accepted.json` -- candidates that cleared the acceptance gate,
  sorted by corpus delta (descending)
* `index.csv` -- per-candidate flat summary for ad-hoc inspection

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
├── corpus.py                # CorpusFixture + iter_corpus_fixtures
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

* Phase 1 corpus expansion -- first post-M1 milestone (M2).
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
