# Riley Corpus v2 — Real-Data Finding

**Thesis proven: real training data is the lever.** A fine-tune on real songs recovers
the separator from *broken* (synthesis-only) to *near-parity with production stock*.
2026-08-05 · research-only (MoisesDB CC-BY-NC-SA).

## Benchmark v2.0 (15 real MoisesDB songs, SI-SDR median)
| model | SI-SDR | vs stock |
|---|---|---|
| stock htdemucs_6s (jamn.app production) | **+3.12** | — |
| **Riley v3 — real-data fine-tune** | **+2.61** | −0.51 |
| Riley C2 — synthesis-only fine-tune | −2.35 | −5.47 |
| naïve C2 | −5.97 | −9.09 |

**Real data closed the synthesis→real gap by +4.96 dB** (−2.35 → +2.61) — from negative
(worse-than-nothing) to positive (actually separating), landing **within 0.5 dB of
stock** — off a *tiny* run: 44 songs / 178 min / 8 epochs / ~2 h / ~$1.

## What this establishes
1. **The data was the whole problem.** Synthesis-only (GuitarSet+Freesound) fine-tuning
   *regressed* stock htdemucs on real songs (−2.35). Swapping in **real** training data —
   same recipe, same size class — jumped it +4.96 dB to near-parity. The synthesis→real
   domain gap, not the architecture or recipe, was the ceiling.
2. **Beating stock is now a scaling problem, not an unknown.** v3 reached +2.61 with 44
   songs; stock trained on far more real multitrack. More real data + a wider recipe
   (6-source, longer clips, more epochs) is the clear path past +3.12.
3. **The factory's mission sharpens:** manufacture/acquire **commercial-clean REAL**
   data. MoisesDB proved the mechanism but is non-commercial (research-only) — a
   deployable model needs real data Riley can ship on (licensed real multitrack, or
   the Virtual Studio driven by real recorded stems rather than synthesis).

## Honest limits
- v3 is **research-only** (MoisesDB NC) — NOT deployable.
- Still **below stock** (−0.51 dB) — parity, not a win yet.
- Same failure shape as stock on hard tracks (dense/low-guitar): −7.4, −5.7 dB outliers.

## Next
- Scale real training data (more MoisesDB songs for the *research* proof-of-ceiling;
  commercial-clean real data for the *deployable* model).
- Try the 6-source recipe (match stock's setup) + longer clips.
- Target: exceed stock +3.12 on Benchmark v2.0, then a real-song blind vs stock, then
  wire as a SeparatorProvider + deploy.

Artifacts: corpus `versions/riley_corpus_v2_real_index.json`; eval
`campaigns/corpus_v2_real_eval.json`; ckpt hashes `campaigns/corpus_v2_ckpt_sha256.txt`;
model on volume `v3/checkpoints/`.
