# Reconstruction Trial — Session Plan (LOCKED)

This file is the operator's pre-commitment record for the 20-trial
reconstruction experiment described in `RECONSTRUCTION_TRIAL_PLAN.md`.

§1 and §2 are LOCKED as of the timestamp below. Post-hoc adjustment
invalidates the pre-registration.

---

## 1. Acceptable-sound criterion (LOCKED)

> "I would commit this as the final reconstruction of the reference,
> knowing my own taste, with no further tweaking expected."

Applied uniformly across all 20 trials, both arms.

**Locked at (UTC):** `2026-06-04T17:49:18Z`

---

## 2. Target order (LOCKED)

10 targets, 4-4-2 sound-type quota. Each target runs twice (once per arm),
20 trials total, arms alternate by trial index starting with retrieval.

| #   | Arm        | Target preset_id                                  | Sound type | Status |
|-----|------------|---------------------------------------------------|------------|--------|
| T1  | retrieval  | analog_muted_dark_bass                            | bass       | [ ]    |
| T2  | control    | analog_muted_dark_bass                            | bass       | [ ]    |
| T3  | retrieval  | analog_saw_detune_sync_lead                       | lead       | [ ]    |
| T4  | control    | analog_saw_detune_sync_lead                       | lead       | [ ]    |
| T5  | retrieval  | synth_essentials_analog_johnny's_soft_pad         | pad        | [ ]    |
| T6  | control    | synth_essentials_analog_johnny's_soft_pad         | pad        | [ ]    |
| T7  | retrieval  | analog_saw_filter_bass                            | bass       | [ ]    |
| T8  | control    | analog_saw_filter_bass                            | bass       | [ ]    |
| T9  | retrieval  | synth_essentials_analog_carlos_gets_tripped       | lead       | [ ]    |
| T10 | control    | synth_essentials_analog_carlos_gets_tripped       | lead       | [ ]    |
| T11 | retrieval  | synth_essentials_analog_dalmation_bass            | bass       | [ ]    |
| T12 | control    | synth_essentials_analog_dalmation_bass            | bass       | [ ]    |
| T13 | retrieval  | analog_square_sync_lead                           | lead       | [ ]    |
| T14 | control    | analog_square_sync_lead                           | lead       | [ ]    |
| T15 | retrieval  | analog_fifth_pad                                  | pad        | [ ]    |
| T16 | control    | analog_fifth_pad                                  | pad        | [ ]    |
| T17 | retrieval  | analog_saw_pure_muted_bass                        | bass       | [ ]    |
| T18 | control    | analog_saw_pure_muted_bass                        | bass       | [ ]    |
| T19 | retrieval  | analog_dual_osc_buzz_lead                         | lead       | [ ]    |
| T20 | control    | analog_dual_osc_buzz_lead                         | lead       | [ ]    |

Sound-type counts: bass=4, lead=4, pad=2 (per quota).
Arm counts: retrieval=10, control=10 (balanced).
Each target appears once in each arm.

**Locked at (UTC):** `2026-06-04T17:49:18Z`

---

## 3. Per-trial commands (in order)

Copy-paste one block at a time. Do not skip ahead.

```bash
# T1
python3 scripts/reconstruction_trial.py run --arm retrieval --target analog_muted_dark_bass
# T2
python3 scripts/reconstruction_trial.py run --arm control   --target analog_muted_dark_bass
# T3
python3 scripts/reconstruction_trial.py run --arm retrieval --target analog_saw_detune_sync_lead
# T4
python3 scripts/reconstruction_trial.py run --arm control   --target analog_saw_detune_sync_lead
# T5
python3 scripts/reconstruction_trial.py run --arm retrieval --target "synth_essentials_analog_johnny's_soft_pad"
# T6
python3 scripts/reconstruction_trial.py run --arm control   --target "synth_essentials_analog_johnny's_soft_pad"
# T7
python3 scripts/reconstruction_trial.py run --arm retrieval --target analog_saw_filter_bass
# T8
python3 scripts/reconstruction_trial.py run --arm control   --target analog_saw_filter_bass
# T9
python3 scripts/reconstruction_trial.py run --arm retrieval --target synth_essentials_analog_carlos_gets_tripped
# T10
python3 scripts/reconstruction_trial.py run --arm control   --target synth_essentials_analog_carlos_gets_tripped
# T11
python3 scripts/reconstruction_trial.py run --arm retrieval --target synth_essentials_analog_dalmation_bass
# T12
python3 scripts/reconstruction_trial.py run --arm control   --target synth_essentials_analog_dalmation_bass
# T13
python3 scripts/reconstruction_trial.py run --arm retrieval --target analog_square_sync_lead
# T14
python3 scripts/reconstruction_trial.py run --arm control   --target analog_square_sync_lead
# T15
python3 scripts/reconstruction_trial.py run --arm retrieval --target analog_fifth_pad
# T16
python3 scripts/reconstruction_trial.py run --arm control   --target analog_fifth_pad
# T17
python3 scripts/reconstruction_trial.py run --arm retrieval --target analog_saw_pure_muted_bass
# T18
python3 scripts/reconstruction_trial.py run --arm control   --target analog_saw_pure_muted_bass
# T19
python3 scripts/reconstruction_trial.py run --arm retrieval --target analog_dual_osc_buzz_lead
# T20
python3 scripts/reconstruction_trial.py run --arm control   --target analog_dual_osc_buzz_lead
```

For each trial the harness will:

1. Print the reference WAV path.
2. (Retrieval arm only) Print the top-5 with preset names + audio paths.
3. Prompt ENTER at three sentinels: `t_start`, `t_acceptable`,
   `t_export`. 15-minute cap; type `cap` instead of ENTER if exhausted.
4. Collect: `selected_rank` (retrieval arm only), `tweaks_estimate`
   bucket (0-5, 6-15, 16-30, 30+), `success` y/n, `satisfaction` 1-5,
   notes, optional `exported_als_path`.
5. Atomic-write `preset_catalog_output/reconstruction_trials/trial_<id>.json`.

---

## 4. Stop and score (after T20)

```bash
python3 scripts/score_reconstruction_trials.py
```

Exit codes:

- `0` — STRONG PASS or WEAK PASS
- `1` — FAIL
- `2` — insufficient sample

Locked thresholds (from `RECONSTRUCTION_TRIAL_PLAN.md`):

| H1 time-to-acceptable reduction | ≥ 30 % median   |
| H2 success-rate lift            | ≥ 15 pp         |
| H3 satisfaction lift            | ≥ 1.0 Likert    |

No threshold edits after T1 starts.

---

## 5. Operator notes during run

(Append free-text observations here — not part of the pre-registration,
just a log to consult if a trial's behaviour was unusual.)
