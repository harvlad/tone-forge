# Riley Benchmark v3 (42-clip) — definitive verdict: PARITY with production stock

The measurement fixes (bigger eval + consistent scoring) resolve the parity-band
ambiguity. **Riley's best model is statistically indistinguishable from production stock
htdemucs_6s on real songs — a coin-flip, no win, no regression.** 2026-08-06.

## What changed (the fixes)
- **Eval expansion:** Benchmark v2.0 was 15 clips (±~1 dB noise, couldn't separate
  parity-band models). Rebuilt as **eval_big = 42 clips from 21 held-out MoisesDB songs**
  (15 original + the 6 remaining unused full-band; MoisesDB is now exhausted — 200
  trained + 15 + 6 + 1 = 222). 2 guitar-active windows/song.
- **Reliable comparison ("validation fix" in practice):** training-time val SDR is nan
  (silent-ref) so MSST's "best" ckpt is meaningless. The trustworthy judge is this
  downstream benchmark, scored *identically* across all models (best AND latest, to show
  variance), with a paired sign-test.

## Results — eval_big (42 clips), guitar SI-SDR
| model | median | mean | vs stock: wins / mean Δ / P(≥wins\|chance) |
|---|---|---|---|
| stock htdemucs_6s (production) | +2.74 | +2.87 | — |
| Riley v4_latest | +2.96 | +2.84 | **21/42 · −0.02 dB · P=0.56** |
| Riley v4_best | +3.23 | +2.78 | 19/42 · −0.08 dB · P=0.78 |
| Riley v5_latest | +2.66 | +2.50 | 19/42 · −0.37 dB · P=0.78 |

## Verdict
- **Riley v4 = parity with stock.** 21/42 wins is a literal coin flip; mean Δ ≈ 0
  (−0.02 to −0.08 dB); P≈0.5-0.8 (no significance). The v2.0 "v4 +3.73 vs +3.12"
  (+0.61) was **small-sample noise** — on 42 clips it vanishes.
- **v5 (more data, crashed 16-ep run) is marginally worse**, not better.
- **Confirmed:** real data took Riley from broken (−2.35) to **parity** with a
  production separator trained on far more data. That is the real, rigorous result.
  **No decisive win exists; scaling MoisesDB plateaued at parity.**

## Why parity, not a win — and what a win would need
- **Data exhausted:** MoisesDB has only ~222 guitar songs; we used them all. 190→parity;
  there's no more MoisesDB to scale into.
- To *beat* stock (trained on far more/broader real multitrack) needs **materially more
  and more-diverse real data** than MoisesDB offers — additional licensed real
  multitrack sources, or the Virtual Studio driven by real recorded stems (not
  synthesis) at scale. More epochs alone won't do it (v5 crash aside, the plateau is
  data-diversity-bound).
- Secondary, cheap improvements before any more GPU: **fix training-time validation**
  (guitar-target SDR, not nan) so best-ckpt is real; **complete one clean longer run**
  (num_workers 6). But expect parity, not a leap, on the same ~200 songs.

## Standing status
Riley matches production; it does not beat it on available data. Not deployable as an
improvement (parity ≠ worth swapping). The factory + data-centric loop are validated;
the binding constraint is now **quantity/diversity of commercial-clean real data**, the
factory's original thesis. Artifacts: `campaigns/eval_big_{index,stock,riley}.json`,
eval on volume `eval_big/` (42 clips).
