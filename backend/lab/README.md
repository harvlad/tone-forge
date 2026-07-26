# Jam Transcription Lab

Local-first research infrastructure for evaluating neural transcription
models. Core principle: **inference is expensive, analysis is cheap —
separate them completely.**

```
AUDIO -> MODEL INFERENCE -> IMMUTABLE PREDICTION CACHE -> LOCAL ANALYSIS
```

Once inference has run for (audio, model, checkpoint, config), the
network never runs again for that combination. Changing evaluation
parameters (onset tolerance, matching rules, oracles, routing, CIs)
only recomputes cheap local matching — never inference.

## Layout

```
backend/lab/            code (this package)
backend/lab_data/       all state (Parquet + JSON; heavy dirs gitignored)
  corpus/manifest.parquet   canonical stem registry (stable IDs, hashes)
  gt/v1/<midi_hash>.parquet normalized GT notes (content-addressed)
  predictions/<model>/<key>.parquet + .json   immutable prediction cache
  matches/<key>.parquet     match cache (keyed on eval params too)
  features/<name>/<ver>/    lazy audio feature cache
  experiments/experiments.jsonl   research memory (append-only)
  models/registry.json      model registry (license, status, ...)
  tiers/                    frozen tier selections
  jobs/                     GPU job bundles + pending queue
  legacy/                   rescued pre-lab artifacts (provenance flagged)
```

## CLI (run from backend/)

```
python3 -m lab corpus build|status
python3 -m lab models list
python3 -m lab cache status
python3 -m lab benchmark basic_pitch --class Bass --tier scout
python3 -m lab compare basic_pitch mt3 --class Bass --tier scout
python3 -m lab oracle basic_pitch mt3 --tier validated100
python3 -m lab validate                      # reproduce known numbers
python3 -m lab tiers --tier scout --class Bass
python3 -m lab pending-gpu [--add MODEL --tier T --class C]
python3 -m lab estimate-gpu MODEL --tier T --gpu "RTX 4090" --price 0.40
python3 -m lab export-gpu-job MODEL --tier T
python3 -m lab import-results lab_data/jobs/job_...
python3 -m lab experiments list
python3 -m lab query "SELECT ... FROM corpus|gt_notes|predictions|matches"
```

## Cache identity

Prediction key = sha256(audio_hash, model_id, model_version,
checkpoint_hash, adapter_version, canonical inference config).

- changed audio / checkpoint / config / adapter → new key (miss)
- changed onset tolerance / matching → match cache recomputes, prediction cache untouched
- interrupted runs resume missing stems only (`runner.missing_stems`)
- entries are never deleted; `--force` moves them to `*.invalid.*`
- failures recorded per stem; a failed stem is retried next run, never a hit

## Tiers (deterministic from seed, selections frozen in lab_data/tiers/)

sanity 3 / smoke 8 / scout 40 / benchmark 150 / full (all dev) per class;
`heldout` = Slakh test split, FROZEN — never tune against it;
`validated100` = the exact 100-stem set of the validated BP/MT3 benchmark.

## Remote GPU flow (provider-neutral, disposable workers)

1. `pending-gpu` — inspect batched missing work (no provisioning)
2. `estimate-gpu` — preflight: stems, cached/missing, audio minutes,
   runtime + cost from measured throughput. **User approves.**
3. `export-gpu-job` — bundle with ONLY missing stems' audio + a copy of
   this package + standalone worker
4. rent machine, rsync bundle, `python lab_worker/worker.py --bundle .`
   (resumable, incremental atomic writes, per-stem failure records,
   verifies audio hashes + checkpoint hash)
5. rsync results/ back, `import-results` (validates before caching,
   provenance `remote_job:<id>`), **terminate machine**

Nothing in the lab ever provisions or pays for machines.

## Adding the next model

1. Write `lab/adapters/<name>_adapter.py` subclassing `ModelAdapter`:
   wrap the model's OFFICIAL inference API (never reimplement windowing —
   that's how the 1.21x timing-stretch bug happened), define
   `inference_config()` (everything that changes output),
   `checkpoint_hash()`, `available()`.
2. Register it in `lab/adapters/__init__.py:_load`.
3. `python3 -m lab models add <name> --fields '{"license": "...", ...}'`
4. `python3 -m lab benchmark <name> --tier sanity` (3 stems, local if CPU-ok)
5. Promote through smoke → scout → benchmark; early-termination hints
   fire automatically vs the incumbent. GPU-only models: `pending-gpu`
   → `estimate-gpu` → user approval → export/import.

## Validation

`python3 -m lab validate` recomputes the validated benchmark from cached
predictions and compares against the frozen reference numbers
(BP recall 42.3%, MT3 11.0%, Bass split, oracle 47.2%). Material
mismatch = broken harness → STOP and investigate; never explain it away.
