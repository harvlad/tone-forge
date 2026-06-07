# Reconstruction Trial Plan

**Purpose:** answer the primary product question
**"Does retrieval reduce reconstruction time enough to matter?"**
by measuring *realised* reconstruction effort with and without
retrieval-assisted starting points.

V3 already established that the top-5 are *perceived* useful (workhorse
`any_top5_usable = 100%`). This harness measures the next step:
whether perceived usefulness converts to realised effort reduction
inside the actual Ableton workflow.

---

## 1. Hypotheses

**H1 (primary):** Median time-to-acceptable in the retrieval arm is
materially lower than in the control arm.

- **Material** means: a credible producer would call the speed-up
  "obviously worth doing." We pre-commit to **≥ 30% reduction in median
  time-to-acceptable** as the bar. (Sub-30% reductions are real but
  cheap to lose to interaction friction; ≥ 30% is hard to lose.)

**H2 (secondary):** Success rate is higher in the retrieval arm.

- **Material:** ≥ 15 percentage-point lift in success rate within the
  per-trial time cap.

**H3 (secondary):** Satisfaction score is higher in the retrieval arm.

- **Material:** ≥ 1.0 point lift on the 1–5 Likert scale.

No null hypothesis testing — n is too small for that to be meaningful.
We are looking for effect sizes large enough to be visible without
formal inference.

---

## 2. Experimental design

### 2.1 Arms

- **Retrieval arm.** Operator is shown the top-5 retrieved presets for
  the reference audio. They pick one, drag it onto an Analog device
  in Live, tweak, and export.
- **Control arm.** Operator is shown nothing. They open Live, drop a
  default empty Analog patch, and reconstruct from scratch.

### 2.2 Sample size

- **Initial sample:** 10 trials per arm (20 total). A minimal-viable
  pilot uses 5 trials per arm; results are reported but the scorer
  flags the verdict as "preliminary" below 10 per arm.
- **Sound-type quota** (workhorse): 3 bass, 3 lead, 2 pad, 2 other.
  "other" is included because it represents the long tail of the
  workhorse cohort and was reaffirmed as in-scope for this trial round.
  Same quota applied per arm so the arms are matched on sound_type
  difficulty.
- **Sources:** targets drawn from the V3 workhorse PASS list (we know
  retrieval can find usable options for these; if retrieval fails to
  help here, it certainly won't help on harder targets).

### 2.3 Assignment

- Alternate arms by trial index (T1=retrieval, T2=control, T3=retrieval,
  ...). Avoids time-of-day / fatigue confounds biasing one arm.
- Operator may not see the source preset that generated the reference
  audio during the trial; only the rendered WAV.

### 2.4 Per-trial time cap

- **15 minutes** of total wall-clock from t_start. If unmet, trial is
  recorded as `success=false`, `t_export=null`.
- The cap is also a courtesy bound on operator time; 20 trials × 15 min
  = 5 hours hard ceiling.

### 2.5 Acceptable-sound criterion

Operator pre-commits to a self-consistent rule before trial 1 and uses
it for all 20 trials. Recommended phrasing:

> "I would commit this as the final reconstruction of the reference,
>  knowing my own taste, with no further tweaking expected."

The criterion is subjective on purpose — the bar is the operator's
own standard for "done." What matters is consistency, not external
calibration.

### 2.6 Blinding

Full blinding is infeasible (the operator knows which arm they're in).
Mitigations:

- The reference WAV is presented without a name or label that would
  identify its source preset, so the operator cannot pattern-match.
- The acceptable-sound criterion is pre-committed and uniform across
  arms.
- Arms alternate, not blocked, so operator energy/skill drift affects
  both arms equally.

---

## 3. Metrics

Captured per trial:

| Field                     | Source       | Notes                          |
|---------------------------|--------------|--------------------------------|
| `trial_id`                | harness      | timestamped + index            |
| `arm`                     | harness      | "retrieval" or "control"       |
| `target_preset_id`        | harness      | the preset that rendered ref   |
| `target_sound_type`       | harness      | bass / lead / pad / other      |
| `reference_wav`           | harness      | path                           |
| `top5_offered`            | harness      | retrieval arm only             |
| `selected_rank`           | operator     | 1-5 or null (control arm)      |
| `t_start_iso`             | harness      | wall-clock                     |
| `t_selected_iso`          | harness      | wall-clock; null in control    |
| `t_acceptable_iso`        | harness      | wall-clock; null if no PASS    |
| `t_export_iso`            | harness      | wall-clock; null if no PASS    |
| `time_to_selection_sec`   | derived      | t_selected − t_start (ret arm) |
| `time_to_acceptable_sec`  | derived      | t_acceptable − t_start         |
| `time_to_export_sec`      | derived      | t_export − t_start             |
| `tweaks_estimate`         | operator     | one of 0-5, 6-15, 16-30, 30+   |
| `success`                 | operator     | bool                           |
| `satisfaction`            | operator     | 1-5 Likert                     |
| `notes`                   | operator     | free text                      |
| `exported_als_path`       | operator     | optional; enables param diff   |

### 3.1 Reported aggregates (per arm)

- Median time-to-acceptable (only successful trials).
- Median time-to-export (only successful trials).
- Median time-to-selection (retrieval arm only; informational —
  decomposes time-to-acceptable into audition + tweak cost).
- Success rate (n_success / n_trials).
- Mean satisfaction (across successful trials).
- Tweaks distribution (counts by bucket).
- Selected-rank distribution (retrieval arm only).

### 3.2 Cross-arm deltas

- Median time-to-acceptable: control − retrieval.
- Success rate: retrieval − control.
- Satisfaction: retrieval − control.

H1/H2/H3 pass criteria applied to these deltas.

---

## 4. Pass / fail criteria

The harness reports a per-hypothesis verdict:

| Hypothesis                    | Material threshold              | Verdict   |
|-------------------------------|---------------------------------|-----------|
| H1 time-to-acceptable lift    | ≥ 30% reduction (median)        | PASS/FAIL |
| H2 success-rate lift          | ≥ 15 pp                         | PASS/FAIL |
| H3 satisfaction lift          | ≥ 1.0 Likert point              | PASS/FAIL |

**Overall verdict:**

- **STRONG PASS:** H1 PASS AND (H2 OR H3 PASS).
- **WEAK PASS:** H1 PASS only, or (H2 PASS AND H3 PASS) without H1.
- **FAIL:** H1 FAIL.

A FAIL on H1 (time-to-acceptable) means retrieval does not pay its way
in workflow time, even though V3 said the top-5 are *perceptually*
useful. That outcome would force a re-examination — the candidates
include: top-5 presentation friction (operator can't audition fast),
parameter-tweak friction (the gap between "starts close" and "matches"
is the dominant cost), or the metric is wrong.

---

## 5. Pre-registered analysis

Before running trial 1:

- Acceptable-sound criterion is locked.
- Pass thresholds (§4) are locked.
- Time cap (15 min) is locked.
- Sample plan (10/10 with sound_type quota) is locked.

After trial 20, the scorer is run on the fixed thresholds. No post-hoc
threshold adjustment.

If the result is ambiguous (e.g., H1 effect 20–30%), an extension to
n=20/20 may be added, but the same thresholds apply to the combined
sample. Do not stop early on positive results.

---

## 6. Implementation

Three artifacts ship with this plan:

- `backend/scripts/reconstruction_trial_runner.py` — interactive CLI
  harness used during each trial. Captures `t_start`, `t_selected`
  (retrieval arm only), `t_acceptable`, `t_export`, and the post-trial
  operator fields. Writes one trial JSON per invocation.
- `backend/scripts/score_reconstruction_trials.py` — reads all trials
  under `preset_catalog_output/reconstruction_trials/` and produces the
  aggregate report.
- `backend/reconstruction_trial_report_template.md` — fillable
  human-authored write-up to file alongside the auto-generated
  aggregate reports.

(The earlier `scripts/reconstruction_trial.py` predates the
`t_selected` capture and is preserved for backwards compatibility but
should not be used for new trials.)

Outputs:

- `preset_catalog_output/reconstruction_trials/trial_<id>.json` (one per
  trial).
- `preset_catalog_output/reconstruction_trials/reconstruction_trial_report.json`
- `preset_catalog_output/reconstruction_trials/reconstruction_trial_report.md`

### 6.1 Operator runbook

```
# Once, before trial 1:
python3 scripts/reconstruction_trial_runner.py list-targets
#   - prints workhorse V3 PASS queries grouped by sound_type
#   - operator picks 10 targets matching the 3-3-2-2 quota and writes
#     them to a trial plan file (or just memorises the order)

# Per trial (alternate arms; T1=retrieval, T2=control, ...):
python3 scripts/reconstruction_trial_runner.py run \
    --arm retrieval --target <preset_id>
# CLI walks operator through:
#   1. show reference WAV path
#   2. (retrieval arm) show top-5 with preset names + audio paths
#   3. press ENTER to mark t_start (begin auditioning)
#   4. (retrieval arm) press ENTER when a top-5 candidate is picked
#      -> t_selected; operator records the rank (1-5)
#   5. work in Live (drag preset on, tweak)
#   6. press ENTER when acceptable -> t_acceptable
#   7. press ENTER when exported -> t_export
#   8. prompt for tweaks bucket, success, satisfaction, notes,
#      optional exported_als_path
#   9. atomic-write trial_<id>.json

# After trial 20:
python3 scripts/score_reconstruction_trials.py
# Reads all trial_<id>.json files, computes per-arm aggregates,
# applies H1/H2/H3 thresholds, prints verdict, writes JSON + MD report.

# Then copy reconstruction_trial_report_template.md into the trials
# directory and fill in the operator's narrative summary.
```

### 6.2 Safety / hygiene

- Each trial JSON is atomically written (tmp + os.replace) so a crash
  mid-trial cannot corrupt earlier trials.
- Trial files include the harness git-commit SHA for reproducibility.
- The scorer refuses to produce a verdict if n_per_arm < 5 (insufficient
  for any informative comparison).

---

## 7. Out of scope for this trial round

- Reconstruction quality measurement (audio similarity between exported
  WAV and reference). The harness records the path but does not score
  audio fidelity; that is a follow-up evaluation under the
  reconstruction-quality track.
- Automated parameter-tweak counting via M4L / ALS-XML diff. The
  ``tweaks_estimate`` field is self-reported in coarse buckets.
- Cross-instrument trials. All targets are Analog. Operator,
  Wavetable, and Drift trials wait for V2 cross-instrument validation.
- FX / percussion targets. The minor cohort is not part of this trial
  set; revisit after FX label audit and percussion corpus expansion.

---

## 8. Expected outcomes — and what each implies

| Outcome    | What it means                                              | Next action                                            |
|------------|------------------------------------------------------------|--------------------------------------------------------|
| STRONG PASS | Retrieval works in the real workflow                       | Promote seg8 to production; build retrieval UI in app   |
| WEAK PASS   | Retrieval helps but the win is modest                       | Investigate top-5 presentation; address friction        |
| FAIL (H1)   | V3 perception did not survive realised workflow             | Diagnose: tweak-cost? audition-cost? wrong metric?      |
| FAIL (all)  | Retrieval is not yet useful in the product                  | Defer reconstruction feature; revisit after FX + V2     |

This experiment is decisive in either direction. It either justifies
promoting retrieval into the product or surfaces specific frictions
that further retrieval-metric work cannot address.
