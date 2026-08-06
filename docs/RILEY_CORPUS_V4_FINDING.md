# Riley Corpus v4 — 6-source real-data scaling: PARITY with production stock

**Result: Riley's corpus-trained model reached parity with production stock htdemucs_6s
on real songs** — a marginal median edge, statistically a tie. 2026-08-06 · research-only
(MoisesDB) · ~$1 GPU.

## Benchmark v2.0 (15 real MoisesDB songs, SI-SDR)
| model | median | vs stock | per-track wins vs stock |
|---|---|---|---|
| stock htdemucs_6s (production) | +3.12 | — | — |
| **Riley v4** (htdemucs_6s FT, 110 real songs, 6-source) | **+3.73** | **+0.61** | **7 / 15** |
| Riley v3 (2-stem, 44 real) | +2.61 | −0.51 | — |
| Riley C2 (synthesis-only) | −2.35 | −5.47 | — |

## Honest verdict
- **Trajectory is decisive:** synthesis −2.35 → real+2-stem +2.61 → real+6-source+scale
  **+3.73**. Real data + matching stock's 6-source recipe closed a ~6 dB gap.
- **v4 ≈ stock (parity), NOT a clear win.** Median is +0.61 higher, but per-track it's
  **7/15 (a coin flip)**; the 8 losses are mostly tiny, with two larger losses on
  hard high-masking tracks. By the C2 lesson (a small median edge with a per-track tie
  became a blind wash), **+0.61 / 7-of-15 is not a promotable win** — a real-song blind
  vs stock would very likely be a wash.
- **What IS proven:** a model trained purely on Riley's assembled real corpus (110 songs)
  **matches** a production separator trained on far more data. The factory produces
  competitive supervision. Absolute quality is now positive and real-song-usable.

## What it takes to DECISIVELY beat stock (next levers)
1. **More real data** — 110 songs ≈ parity; stock saw far more. Scale the real corpus
   (more MoisesDB for research; commercial-clean real data for a shippable model).
2. **Longer / better recipe** — more epochs, LR schedule, the hard high-masking tracks
   (6b168, e62af: −7, −5 dB) are where v4 loses; targeted augmentation.
3. Only when v4′ shows a clear per-track majority + a **real-song blind win vs stock**
   → promote + wire as a SeparatorProvider + deploy.

## Deployability
Still research-only (MoisesDB CC-BY-NC-SA) → not shippable. And parity ≠ worth the swap
yet. A deployable win needs commercial-clean real data (license, or Virtual Studio
driven by real recorded stems) + a decisive margin.

Artifacts: corpus index `versions/riley_corpus_v2_real_index.json` (+ the 110-song
6-source set on volume `ds_v4_6src`); eval `campaigns/corpus_v4_eval.json`; ckpt hashes
`campaigns/corpus_v4_ckpt_sha256.txt`; model on volume `v4/checkpoints/`.
