# Path A — methodology hardening (post-Phase-I)

Fixed the two methodology gaps from the parity verdict + ran one clean 13-epoch run.
Outcome: fixes work, **parity is reconfirmed** — a cleaner/longer run does not beat stock.
2026-08-07 · ~$2.7 GPU.

## What was done
1. **Dataloader crash fix — `num_workers 6`.** v5's `num_workers 8` leaked semaphores and
   died at epoch 8. v6 ran cleanly to **epoch 13** (stopped by the run script's 6 h
   internal `timeout`, not a crash). Throughput ~1.1-1.6 s/it (still dataloader-bound;
   full FLAC→WAV pre-decode was blocked by volume space — 99% full).
2. **Validation fix — nanmean patch (PARTIAL).** Patched `compute_metric_avg` to skip-nan
   so the scheduler metric is no longer nan (was → best-ckpt never updated). This fixed
   the *avg* (0.0 instead of nan) but **guitar SDR itself stays nan** — MSST's internal
   SDR metric is fragile on our valid data (distinct from our robust `sisdr()`). So
   best-ckpt is still not tracking true guitar quality (v6_best +2.31 < v6_latest +2.34).
   **Conclusion: the real validation is the downstream eval_big benchmark, not MSST's
   internal metric.** A full fix = replace MSST's valid metric with a robust SI-SDR
   (deferred; low value given eval_big already serves as the judge).

## Result — eval_big (42 clips) vs stock htdemucs_6s (+2.74 median / +2.87 mean)
| model | median | mean | wins | mean Δ | P |
|---|---|---|---|---|---|
| v6_latest (13 ep, clean, valid-fix) | +2.34 | +2.66 | 19/42 | −0.20 | 0.78 |
| v6_best | +2.31 | +2.68 | 15/42 | −0.18 | 0.98 |
| (ref) v4_latest | +2.96 | +2.84 | 21/42 | −0.02 | 0.56 |

**v6 is slightly below stock — same parity band as v4/v5.** The clean, longer,
methodology-fixed run **did not beat stock**. This *reconfirms* the Phase I verdict: with
the available data (MoisesDB, exhausted), Riley reaches parity, not superiority; a
cleaner recipe doesn't change it.

## Takeaways (fold into Phase I conclusions)
- **num_workers 6 is the stable setting** (8 crashes; 4 starves the GPU). Pre-decode
  FLAC→WAV would remove the dataloader bottleneck but needs volume headroom.
- **MSST's internal validation metric is unreliable here** — always judge on eval_big
  (≥40 clips, mean+median+sign-test). Best-ckpt from MSST cannot be trusted; use eval_big
  to pick, or just use latest.
- **The data ceiling is real and recipe-independent.** Crash-free, validation-fixed, 13
  clean epochs still land at parity. Path A confirms: the lever is data (Phase II), not
  the pipeline.

Artifacts: eval `campaigns/eval_big_riley_v6.json`; v6 ckpts on volume `v6/checkpoints/`
(best `9547a8e2…` / latest `c09c8df8…`); run script `run_v6.sh` (num_workers 6 + valid
nanmean patch).
