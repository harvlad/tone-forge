# Riley Corpus v5 — scaling run (190 songs, longer recipe): NO improvement

**Result: v5 did not improve on v4 — landed in the parity band, marginally below stock.**
The 16-epoch run crashed at epoch 8; more data at ~8 epochs gave no clear gain.
2026-08-06 · research-only · ~$2 GPU.

## Benchmark v2.0 (15 real MoisesDB songs, SI-SDR median)
| model | median | vs stock |
|---|---|---|
| Riley v4 (110 songs, 8 ep, 6-src) | +3.73 | +0.61 |
| stock htdemucs_6s (production) | +3.12 | — |
| **Riley v5 (190 songs, ~8 ep, 6-src)** | **+2.79** (latest) / +2.56 (best) | −0.33 |
| Riley v3 (44 songs, 2-stem) | +2.61 | −0.51 |

## What happened
- **The longer recipe (16 epochs) crashed at epoch 8** — `num_workers 8` leaked
  semaphores / the process died mid-epoch-8. It shipped a ckpt + terminated. So v5 got
  ~8 epochs, not 16 — no longer-training benefit.
- **2× data (190 vs 110 songs) at the same ~8 epochs did NOT help** — v5 (+2.79) came in
  *below* v4 (+3.73), not above.

## Honest interpretation
- **We're in a parity band with high variance.** v3 +2.61, v4 +3.73, v5 +2.79, stock
  +3.12 — all within ~1 dB, and per-track comparisons are coin-flips. The v4 "+3.73"
  now looks like the high end of noise, not a robust edge. **No Riley model has a
  decisive, repeatable win over stock.**
- **"Best" ckpt selection is unreliable here:** training-time validation SDR is nan
  (silent-reference contamination), so MSST can't pick a true best epoch — best vs
  latest differ arbitrarily (v5 best +2.56 vs latest +2.79). Downstream benchmark is the
  only real judge.
- **The 15-track eval is likely too small/noisy** to separate models this close
  (~±1 dB). Distinguishing parity-band models needs a bigger eval and/or blind listening.

## Corrected takeaway (supersedes the v4 "beats stock" optimism)
Real data got Riley from broken (−2.35) to **parity** with production (~+2.6 to +3.7,
straddling stock's +3.12). But scaling to 190 songs + attempting 16 epochs did **not**
convert parity into a decisive win. The gains from real data plateaued around parity for
this model/recipe/eval.

## Next (evidence-based options, not another blind scale-up)
1. **Bigger, cleaner eval** (30-50 real songs) to reduce ±1 dB noise before trusting any
   median delta — we can't tell parity-band models apart on 15 tracks.
2. **Fix training-time validation** (real guitar-target SDR, not the nan/silent one) so
   "best" ckpt is meaningful — cheap, high-value.
3. **Actually complete a longer run** (num_workers 6 to avoid the crash; or pre-decode
   FLAC→WAV so num_workers 4 suffices) — 16 real epochs may still help; v5 never tested it.
4. Only if a robust median edge appears on the bigger eval → real-song blind vs stock.
   Otherwise: Riley matches production; a decisive beat likely needs materially more/
   better real data than MoisesDB's ~200 guitar songs.

Artifacts: eval `campaigns/corpus_v5_eval.json`; ckpts on volume `v5/checkpoints/`
(best a15412f2, latest d48dd78c); corpus `ds_v5_6src` (190 songs).
