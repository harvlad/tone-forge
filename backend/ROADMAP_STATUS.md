# Roadmap Status — Retrieval & Reconstruction

**Last update:** after V3 listening pass scored PASS for workhorse classes.

This document is the **state-of-record** for retrieval/reconstruction
work. It supersedes the earlier readiness reviews on the questions of
retrieval gate status and reconstruction-blocking factors.

---

## 1. Current state at a glance

| Track                                  | Status                       | Authoritative reference            |
|----------------------------------------|------------------------------|------------------------------------|
| Catalog Integrity (Analog, 99 presets) | PASS                         | `RENDER_PIPELINE_RCA.md` §7        |
| V1 stability (within-Analog LOO)       | PASS                         | `RETRIEVAL_GATE_PASS.md` §2        |
| V3 human usefulness (workhorse)        | **PASS (19/19 = 100%)**      | `RETRIEVAL_GATE_PASS.md` §3        |
| V3 human usefulness (minor cohort)     | Partial (3/5 = 60%, n small) | `RETRIEVAL_GATE_PASS.md` §3        |
| V2 cross-instrument validation         | PLAN ONLY, not executed      | `V2_CROSS_INSTRUMENT_PLAN.md`      |
| FX label audit                         | Worksheet ready, 0/6 rated   | `scripts/score_fx_label_audit.py`  |
| Reconstruction trial (Analog)          | Framework ready, 0/20 trials | `RECONSTRUCTION_TRIAL_PLAN.md`     |
| Production embedding (`stem_fingerprint.py`) | UNCHANGED              | (no promotion until V2 + recon PASS) |

---

## 2. Retrieval gate — current call

**Retrieval gate is PASSED for workhorse classes** (bass, lead, pad,
other) on the Analog corpus.

Evidence chain:
1. Catalog rebuild closed the audio-collapse bug (99 unique decoded
   SHA-1s, integrity gate green).
2. V1 within-Analog LOO retrieval is stable across K-folds
   (sound_type 0.811–0.826) with workhorse Wilson 95 % CI [0.742, 0.890].
3. V3 listening pass: workhorse `any_top5_usable` = 100 % vs the
   pre-committed 70 % threshold.

The 0.7 V3 threshold and the workhorse / minor cohort split were
locked **before** ratings began (see `RETRIEVAL_GATE_PASS.md` §1), so
the PASS is not a post-hoc fit.

**Retrieval quality is therefore no longer a blocker for reconstruction
testing on workhorse Analog targets.** The next gate is realised
workflow effort, not retrieval similarity.

---

## 3. Reconstruction track — unblocked

Reconstruction trials on workhorse Analog targets are unblocked. The
trial framework (`RECONSTRUCTION_TRIAL_PLAN.md`,
`scripts/reconstruction_trial.py`,
`scripts/score_reconstruction_trials.py`) is ready to run with the
pre-committed thresholds:

| Hypothesis                        | Threshold              |
|-----------------------------------|------------------------|
| H1 time-to-acceptable reduction   | ≥ 30 % median          |
| H2 success-rate lift              | ≥ 15 pp                |
| H3 satisfaction lift              | ≥ 1.0 Likert           |

A FAIL on H1 means perceived usefulness (V3 PASS) did not survive the
realised Ableton workflow — that outcome would re-open the diagnosis
described in `RECONSTRUCTION_TRIAL_PLAN.md` §4.

---

## 4. What remains blocked, and on what

| Item                               | Blocked on                                              |
|------------------------------------|---------------------------------------------------------|
| Promoting seg8 to production       | V2 STRONG/WEAK PASS **and** Analog reconstruction PASS  |
| Cross-engine reconstruction trials | V2 PASS **and** Analog reconstruction PASS              |
| FX representation work             | FX label audit (operator must rate 6/6 worksheet items) |
| Keys retrieval improvement         | Operator/Wavetable corpus expansion (most keys live there) → V2 |
| Percussion retrieval evaluation    | Larger percussion corpus (V3 n=2 is too small)          |
| New representation experiments     | V2 FAIL outcome (not pre-licensed)                      |
| CLAP / learned-embedding research  | V2 FAIL outcome (not pre-licensed)                      |

---

## 5. Document supersession

These earlier documents remain on disk as historical record. Where they
conflict with this status doc, **this doc wins** for the indicated
topics:

| Earlier doc                            | Status                                                |
|----------------------------------------|-------------------------------------------------------|
| `RECONSTRUCTION_READINESS_REVIEW.md`   | Pre-V3, pre-seg8. Its conclusion ("retrieval mediocre, embedding collapsed") was true of the 128-dim production embedding under measurement *at that time*. The current seg8 embedding used in V1/V3 evaluation is a separate path that has not been promoted to production. The "retrieval not ready" verdict in that doc is **superseded by §2 above for the seg8 evaluation path**, and is **still accurate** for the unmodified production 128-dim embedding. |
| `REPRESENTATION_AUDIT.md`              | Sets a numeric gate (top-1 sound_type ≥ 80 %, etc.) on the 128-dim embedding. That gate is **superseded** by the human-usefulness gate (V3 `any_top5_usable` ≥ 70 % workhorse), which is more directly tied to product value than the geometric proxies. The 128-dim gate is retained only as a fall-back metric if a V2 evaluation reopens that question. |
| `AUDIO_COLLAPSE_FINDING.md`            | Closed by the Catalog Integrity rebuild. No further action.|
| `RENDER_PIPELINE_RCA.md`               | Still the canonical reference for the Catalog Integrity Gate. **Not superseded.**|
| `EXTRACTION_ROADMAP.md`, `EXTRACTION_STATUS.md`, `MILESTONE_EXTRACTION_FLOOR.md` | Concern MIDI extraction, not retrieval / reconstruction. **Not affected.**|

---

## 6. Authoritative references

- `RETRIEVAL_GATE_PASS.md` — full gate-pass evidence record.
- `RECONSTRUCTION_TRIAL_PLAN.md` — reconstruction trial design and thresholds.
- `V2_CROSS_INSTRUMENT_PLAN.md` — cross-instrument validation plan (not executed).
- `preset_catalog_output/retrieval/v3_score_report.md` — raw V3 ratings result.
- `scripts/score_v3_usefulness.py` — V3 scorer (gate threshold 0.70 hard-coded).
- `scripts/reconstruction_trial.py`,
  `scripts/score_reconstruction_trials.py` — trial harness + aggregator.
- `scripts/score_fx_label_audit.py` — FX label audit scorer.

---

## 7. Pre-committed do-not list (until further notice)

These constraints are in force until either V2 executes or a
reconstruction trial outcome forces a re-think:

- Do **not** modify production `stem_fingerprint.py` or any code path
  that consumers depend on for the 128-dim embedding.
- Do **not** begin new representation experiments (alternative
  pooling, CLAP, learned heads).
- Do **not** re-tune V3 / reconstruction thresholds post-hoc.
- Do **not** run reconstruction trials on non-workhorse `sound_type`
  targets — they are excluded from the H1/H2/H3 aggregate by design.

---

## 8. Next actions, in order

1. Run the Analog reconstruction trial set (20 trials,
   `RECONSTRUCTION_TRIAL_PLAN.md` §6.1 runbook). Outcome decides
   whether retrieval is promoted into the product.
2. Complete the FX label audit (operator rates 6 worksheet items;
   `scripts/score_fx_label_audit.py` produces the verdict).
3. On operator approval of the ~3-hour Ableton block,
   execute V2 (`V2_CROSS_INSTRUMENT_PLAN.md` §10 runbook).
