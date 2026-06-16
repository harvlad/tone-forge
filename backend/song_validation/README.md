# song_validation

Offline song-validation and learning subsystem for JAM's chord /
section / key engine. Lives entirely outside the user-facing
runtime path.

## Status

Phases 1–20 shipped. 214 tests covering the public surface; full
backend suite green.

| Phase | Module | Public surface |
| ----- | ------ | -------------- |
| 1 | `store` | `Store` + 6-table sqlite schema |
| 1 | `ingestion.bundle` | `ingest_analysis_bundle`, `AnalysisBundleError` |
| 2 | `ingestion.tab` | `ingest_tab_source`, `TabSourceError` |
| 3 | `alignment.grid` | `align_grid`, `AlignmentError` |
| 4 | `disagreement.classifier` | `classify_disagreement`, `classify_alignment` |
| 5 | `metrics.aggregate` | `aggregate_metrics` |
| 6 | `reports.queries` | `where_is_jam_wrong`, `where_are_tabs_wrong`, `engine_version_diff`, `dominant_failure_class` |
| 7 | `pipeline` | `validate_song`, `validate_songs`, `PipelineError` |
| 9 | `training.corpus` | `iter_high_confidence_progressions`, `corpus_stats` |
| 10 | `queue.file_queue` | `enqueue_bundle`, `enqueue_tab`, `drain_queue`, `QueueError` |
| 11 | `cli` / `__main__` | `python -m song_validation` operator CLI |
| 12 | `reports.song_detail` | `inspect_song` (per-song drilldown) |
| 13 | `disagreement.reclassify` | `reclassify_all_alignments`, `reclassify_song` |
| 14 | `disagreement.calibration` | `confidence_calibration_report` (LIKELY_TAB_ERROR threshold tuning) |
| 15 | `training.exporter` | `export_corpus`, `read_corpus_snapshot`, `CorpusExportError` (JSONL snapshot for offline ML) |
| 16 | `maintenance` | `list_songs`, `purge_song`, `vacuum_store` (operator housekeeping) |
| 17 | `reports.engine_song_diff` | `engine_version_song_diff` (per-song cross-engine diff: which individual songs improved or regressed) |
| 18 | `alignment.dtw` | `align_dtw`, `DEFAULT_DTW_STEP_SEC` (DTW-based aligner tolerating absolute-time drift between JAM and tab) |
| 19 | `reports.temporal_trends` | `disagreement_trends_over_time`, `ingestion_trends_over_time`, `TemporalReportError` (time-windowed failure-mix + ingestion volume) |
| 20 | `reports.aligner_diff` | `aligner_diff_report` (per-song delta between two `aligner_kind`s: which individual songs DTW improved vs grid). Also: `alignment_results.aligner_kind` column with forward-only migration. |

Deferred (per directive):
- Harmony language model itself — architecture + training loop.
  Inputs would come from `training.iter_high_confidence_progressions`.
- Section-anchored / beat-anchored / tempo-warping aligners. DTW
  (Phase 18) covers absolute-time drift; section anchors and beat
  grids are higher-fidelity follow-ups when section/beat metadata
  is reliable on both sides.
- HTTP layer that calls `enqueue_bundle` from Connect-client uploads
  and a long-running worker daemon around `drain_queue`. The queue
  primitives are in place; production wiring (cron / systemd /
  supervisord) is an infra concern.

## Critical invariants

Per the architecture directive, this subsystem MUST:

- Never be on the runtime / playback / analysis hot path.
- Never block playback or analysis.
- Never consume realtime GPU resources.
- Treat tabs as evidence to weigh, never as ground truth ("do not
  train directly from all tabs").

The engine remains fully autonomous: nothing in `tone_forge_api.py`
imports this package. A future worker/queue commit will pull from
this subsystem; the runtime path stays unaware.

## Data flow

```
Connect client      (uploads analysis_bundle.json)
        │
        ▼
ingest_analysis_bundle ───►  analysis_results row
                              song row (upserted)
Tab fetcher
        │
        ▼
ingest_tab_source     ───►  tab_sources row

                  validate_song(song_id, store)
                              │
                              ▼
                  align_grid              ───►  alignment_results
                                                 disagreements (UNKNOWN)
                              │
                              ▼
                  classify_alignment      ───►  disagreements (classified)
                              │
                              ▼
                  aggregate_metrics       ───►  engine_metrics row

Reports                                  ◄───  engine_metrics + disagreements
```

## Schema

Six tables, all under `~/.toneforge/song_validation.db` (default).
Foreign keys enforced via `PRAGMA foreign_keys = ON` per connection.

- `songs`              canonical song identity.
- `analysis_results`   one row per JAM analysis bundle.
- `tab_sources`        one row per ingested tab progression.
- `alignment_results`  one row per (analysis, tab) pair processed.
- `disagreements`      per-timestamp mismatches with classification.
- `engine_metrics`     per-engine-version score card.

See `store.py:_SCHEMA_DDL` for column-level detail.

## Disagreement taxonomy

`disagreement.DisagreementClass` (string enum):

| Label | When the classifier fires |
| ----- | ------------------------- |
| `BOUNDARY_ERROR` | jam_chord equals the tab's previous or next chord at this timestamp |
| `EXTENSION_COLLAPSE` | same root, one symbol carries an extension the other dropped (C vs Cmaj7) |
| `SLASH_CHORD_COLLAPSE` | same root + quality, one side has a slash bass the other dropped (C vs C/G) |
| `KEY_CONTEXT_ERROR` | enharmonic-equivalent roots, same quality (C# vs Db) |
| `LIKELY_TAB_ERROR` | tab `source_confidence` below threshold (default 0.4) and no other rule fires |
| `UNKNOWN` | fallback when no rule fires |

Rule order matters; the classifier is conservative — false labels
mislead the engine improvement loop, so unmatched rows stay
`UNKNOWN`.

## Metric definitions

`engine_metrics` columns, aggregated across every alignment for one
engine version:

- `agreement_rate` — total agreements / total grid points.
- `boundary_accuracy` — `1 - (BOUNDARY_ERROR count / total_points)`.
- `slash_chord_accuracy` — `1 - (SLASH_CHORD_COLLAPSE count / total)`.
- `extension_accuracy` — `1 - (EXTENSION_COLLAPSE count / total)`.

If a version has zero alignments, the row is upserted with all
metric columns NULL so consumers can detect "not enough data".

## Quick start

```python
from song_validation import Store, validate_song
from song_validation.ingestion import (
    ingest_analysis_bundle,
    ingest_tab_source,
)

store = Store()  # ~/.toneforge/song_validation.db

# 1. Ingest the artifacts.
analysis_id = ingest_analysis_bundle(bundle_dict, store)
tab_id = ingest_tab_source(tab_dict, store)

# 2. Run the pipeline.
result = validate_song("song-id", store)

# 3. Read the reports.
from song_validation.reports import where_is_jam_wrong
print(where_is_jam_wrong(store))
```

## Tests

```
pytest backend/tests/test_song_validation_store.py
pytest backend/tests/test_song_validation_ingestion.py
pytest backend/tests/test_song_validation_tab_ingestion.py
pytest backend/tests/test_song_validation_alignment_grid.py
pytest backend/tests/test_song_validation_alignment_dtw.py
pytest backend/tests/test_song_validation_disagreement_classifier.py
pytest backend/tests/test_song_validation_metrics.py
pytest backend/tests/test_song_validation_reports.py
pytest backend/tests/test_song_validation_pipeline.py
pytest backend/tests/test_song_validation_training_corpus.py
pytest backend/tests/test_song_validation_queue.py
pytest backend/tests/test_song_validation_cli.py
pytest backend/tests/test_song_validation_song_detail.py
pytest backend/tests/test_song_validation_reclassify.py
pytest backend/tests/test_song_validation_calibration.py
pytest backend/tests/test_song_validation_corpus_export.py
pytest backend/tests/test_song_validation_maintenance.py
pytest backend/tests/test_song_validation_engine_song_diff.py
pytest backend/tests/test_song_validation_temporal_trends.py
pytest backend/tests/test_song_validation_aligner_diff.py
```

## Operator CLI

The entry point is `python -m song_validation`. All output is JSON
on stdout (`--pretty` for indent=2). `--db PATH` overrides the
default `~/.toneforge/song_validation.db`.

```
python -m song_validation drain ./queue --auto-validate
python -m song_validation enqueue-bundle ./queue bundle.json
python -m song_validation validate song-123
python -m song_validation report jam-wrong --top-n 6
python -m song_validation report engine-diff v1.0 v1.1
python -m song_validation report engine-song-diff v1.0 v1.1
python -m song_validation report engine-song-diff v1.0 v1.1 --limit 25
python -m song_validation report aligner-diff grid dtw
python -m song_validation report aligner-diff grid dtw --limit 25
python -m song_validation report trends-disagreements --bucket day
python -m song_validation report trends-disagreements --bucket week --since 2025-01-01
python -m song_validation report trends-ingestion --bucket month
python -m song_validation report inspect song-123
python -m song_validation reclassify --likely-tab-error-threshold 0.5
python -m song_validation reclassify --song-id song-123 --no-reaggregate
python -m song_validation report calibrate
python -m song_validation report calibrate --candidate-thresholds 0.3,0.4,0.5
python -m song_validation corpus stats --min-alignment-score 0.85
python -m song_validation corpus export ./corpus_snapshot.jsonl
python -m song_validation corpus export ./snap.jsonl --min-alignment-score 0.9 --min-tab-confidence 0.8
python -m song_validation store list-songs --limit 50
python -m song_validation store purge-song song-123
python -m song_validation store vacuum
```

Each phase has its own test file pinning the contract. The pipeline
and training-corpus test files exercise end-to-end behaviour.
