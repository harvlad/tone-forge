# Reconstruction Trial Report — `<run-id>`

> **Fill-in instructions.** Copy this file into
> `preset_catalog_output/reconstruction_trials/` and rename to
> `report_<run-id>.md` (e.g. `report_2026-06-10.md`). Fill placeholders
> wrapped in `<...>`. Leave any auto-generated tables in place once the
> machine-generated `reconstruction_trial_report.{json,md}` exist —
> this document is the human narrative that accompanies them.

---

## 1. Run metadata

| Field             | Value                                         |
|-------------------|-----------------------------------------------|
| Run ID            | `<YYYY-MM-DD or similar>`                     |
| Operator          | `<name / handle>`                             |
| Plan version      | `RECONSTRUCTION_TRIAL_PLAN.md @ <git sha>`    |
| Runner version    | `reconstruction_trial_runner.py runner-v1`    |
| Catalog used      | `<catalog file, e.g. catalog_v2.json>`        |
| Live version      | `<Live 12.x.y Standard>`                      |
| Start date        | `<YYYY-MM-DD>`                                |
| End date          | `<YYYY-MM-DD>`                                |

### 1.1 Pre-committed parameters (locked before trial 1)

| Parameter                        | Value                                            |
|----------------------------------|--------------------------------------------------|
| Per-trial time cap               | 15 min                                           |
| Per-arm trial count              | `<10 / 5 / actual>`                              |
| Sound-type quota per arm         | 3 bass · 3 lead · 2 pad · 2 other                |
| Acceptable-sound criterion       | `<operator's own self-consistent rule>`          |
| Arm assignment policy            | Alternate by trial index (T1=retrieval, T2=ctrl) |
| H1 threshold                     | ≥ 30 % reduction in median time-to-acceptable    |
| H2 threshold                     | ≥ 15 pp lift in success rate                     |
| H3 threshold                     | ≥ 1.0 Likert satisfaction lift                   |

---

## 2. Trial inventory

| #  | Arm        | Target preset_id | sound_type | Outcome | t_to_acc (s) | Notes |
|----|------------|------------------|-----------:|:-------:|-------------:|-------|
|  1 | retrieval  | `<...>`          | `<...>`    | `<...>` | `<...>`      | `<...>` |
|  2 | control    | `<...>`          | `<...>`    | `<...>` | `<...>`      | `<...>` |
|  3 | retrieval  | `<...>`          | `<...>`    | `<...>` | `<...>`      | `<...>` |
| ...| ...        | ...              | ...        | ...     | ...          | ...   |

> Run `python3 scripts/score_reconstruction_trials.py` to materialise
> the auto-generated aggregate report. Copy the headline numbers into §3
> below.

---

## 3. Aggregate results (from `reconstruction_trial_report.md`)

### 3.1 Per-arm summary

| Arm        | n  | n_success | success_rate | median t_to_accept | median t_to_export | mean_satisfaction |
|------------|---:|----------:|-------------:|-------------------:|-------------------:|------------------:|
| Retrieval  | `<...>` | `<...>` | `<...>` | `<... s / ... min>` | `<... s / ... min>` | `<... / 5>` |
| Control    | `<...>` | `<...>` | `<...>` | `<... s / ... min>` | `<... s / ... min>` | `<... / 5>` |

### 3.2 Cross-arm deltas

| Metric                          | Control | Retrieval | Δ (retrieval − control) |
|---------------------------------|--------:|----------:|------------------------:|
| Median time-to-acceptable       | `<...>` | `<...>`   | `<... %>`               |
| Success rate                    | `<...>` | `<...>`   | `<... pp>`              |
| Mean satisfaction               | `<...>` | `<...>`   | `<... Likert>`          |

### 3.3 Retrieval-arm-only diagnostics

| Metric                                | Value                |
|---------------------------------------|----------------------|
| Median time-to-selection              | `<... s / ... min>`  |
| Median "tweak time" (acc − selected)  | `<... s / ... min>`  |
| Selected-rank distribution            | `<#1:.. #2:.. ...>`  |
| Tweaks-bucket distribution            | `<0-5:.. 6-15:.. ...>` |

> **Interpretation cue.** If `time-to-selection` is small (say <60 s) but
> total time-to-acceptable is high, the bottleneck is tweak cost, not
> retrieval-presentation cost. If selection itself is slow, the top-5
> UX (or the catalog's discriminating power) is the bottleneck.

---

## 4. Hypothesis verdicts

| Hypothesis                        | Threshold        | Observed         | Pass? |
|-----------------------------------|------------------|------------------|:-----:|
| H1 — time-to-acceptable reduction | ≥ 30 %           | `<...>`          | `<...>` |
| H2 — success-rate lift            | ≥ 15 pp          | `<...>`          | `<...>` |
| H3 — satisfaction lift            | ≥ 1.0 Likert     | `<...>`          | `<...>` |

**Overall verdict:** `<STRONG PASS / WEAK PASS / FAIL>`

- STRONG PASS = H1 PASS AND (H2 OR H3 PASS).
- WEAK PASS = H1 PASS only, or (H2 PASS AND H3 PASS) without H1.
- FAIL = H1 FAIL.

---

## 5. Operator narrative

### 5.1 What went well

`<2-4 sentences describing trials where retrieval clearly helped: which sound_types, which top-5 ranks were picked, qualitative wins.>`

### 5.2 What went poorly

`<2-4 sentences on the worst trials: failures, cap-hits, mis-matches between the retrieved top-5 and the reference.>`

### 5.3 Friction observed

`<UX or workflow frictions noticed during the run, separate from "retrieval bad / good". E.g. drag-and-drop ergonomics, audition-speed UI gaps, ALS-export hiccups.>`

### 5.4 Pattern in mis-selections (retrieval arm)

`<Did the operator regret a top-5 pick? Was rank-1 usually right, or was it rank-3+? Any sound_type for which the top-5 systematically missed?>`

---

## 6. Decisions and next actions

| Decision                                      | Owner    | Status      |
|-----------------------------------------------|----------|-------------|
| Promote retrieval into product UI             | `<who>`  | `<y/n/defer>` |
| Address top-5 audition friction               | `<who>`  | `<...>`     |
| Investigate tweak-cost (parameter-diff tool)  | `<who>`  | `<...>`     |
| Extend trial to Drift / Collision / Electric  | `<who>`  | `<...>`     |
| Re-run reconstruction trial with N=20         | `<who>`  | `<...>`     |

---

## 7. Artifacts and reproducibility

- Trial JSONs: `preset_catalog_output/reconstruction_trials/trial_*.json`
- Machine report: `preset_catalog_output/reconstruction_trials/reconstruction_trial_report.{json,md}`
- Plan: `backend/RECONSTRUCTION_TRIAL_PLAN.md`
- Runner: `backend/scripts/reconstruction_trial_runner.py`
- Scorer: `backend/scripts/score_reconstruction_trials.py`
- V3 target source: `preset_catalog_output/retrieval/v3_top5_usefulness_rating.json`
- Catalog: `preset_catalog_output/catalog/catalog_v2.json`

To rerun the scorer after collecting more trials:

```bash
python3 scripts/score_reconstruction_trials.py
```

To re-run a single trial (alternating arms):

```bash
python3 scripts/reconstruction_trial_runner.py run --arm retrieval --target <preset_id>
python3 scripts/reconstruction_trial_runner.py run --arm control   --target <preset_id>
```
