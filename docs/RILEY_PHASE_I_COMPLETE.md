# Riley Phase I — COMPLETE (Archive & Freeze)

**Frozen 2026-08-06 · tag `phase-1-complete`.** Reference implementation. Future work
compares against this. Do not continue optimizing Phase I.

## The three questions
1. **Can Riley manufacture better training data?** → **YES** (Campaign 2: Riley corpus >
   naïve corpus from the same source; real-song blind 15/15, P≈3e-5).
2. **Do the improvements reproduce on real deployment audio?** → **YES** (real MoisesDB
   songs; real data took Riley from broken −2.35 dB to parity).
3. **Does Riley outperform production stock on currently-available commercial-clean
   data?** → **NOT YET.** Expanded 42-clip benchmark: v4_latest 21/42 wins, mean Δ
   −0.02 dB, P=0.56 = **statistical parity, not superiority.** (The 15-clip "+0.61 dB"
   was noise.)

## Proven
- Data Factory manufactures better data than naïve manufacturing.
- Real training data is the dominant lever; synthetic-only is insufficient (regresses).
- The htdemucs fine-tune recipe is correct; Benchmark methodology (≥40 clips) is trustworthy.
- The Promotion Gate blocked **three** false promotions (B1, AudioShake, Campaign-2 synthetic).
- Riley trains a separator to **statistical parity** with production stock.

## Not proven
- Riley beats production stock on available data. Evidence = parity.

## Most important finding
The limiting factor is no longer architecture / optimizer / GPU / recipe / infra /
evaluation — all validated. It is now: **commercially-usable, diverse, real guitar
training data.** MoisesDB is exhausted (222 guitar songs, all used). That is Phase II's
target.

## Frozen inventory (hashes are the identity)
| Asset | Ref / hash |
|---|---|
| Default corpus | `riley_corpus_v1.0` corpus_hash `df4167dc…`, manifest `c8364133…` |
| Naïve control | `riley_baseline_naive_v1.0` corpus_hash `20c0811f…` |
| Benchmark v2.0 (15-clip) | rollup `8a70ed4c…` (`benchmarks/benchmark_v2.0.json`) |
| Benchmark v3 (42-clip) | `campaigns/eval_big_{index,stock,riley}.json` |
| Production bar | stock `htdemucs_6s`: +2.74 median / +2.87 mean (42 clips) |
| Campaign 2 (promoted corpus) | ckpts A `c5f189e0…` / B `b6a3aba8…`; blind rollup `245c40ca…` (15/15) |
| Corpus v4 (best real, parity) | 6-src, 110 songs; ckpt best `af425862…` |
| Corpus v5 (scaling, no gain) | 6-src, 190 songs; best `a15412f2…` / latest `d48dd78c…` |
| Recipe | `c4_htdemucs6s.yaml` (6-source, bottom_channels 0, 100% transfer from htdemucs_6s) |
| Stock pretrained (6-src) | `htdemucs_6s_pretrained.ckpt` sha `8cc326fb…` |

Frozen components (do not redesign): Data Factory, Virtual Studio, Asset system, Catalog,
Coverage Planner, Corpus Validator, Promotion methodology, Evaluation scripts
(`score_*.py`, `build_eval_big.py`), Engineering Principles (P1-P13,
`RILEY_ENGINEERING_PRINCIPLES.md`). Canonical audio + checkpoints on Hetzner volume
`/mnt/HC_Volume_106533567/factory/{v4,v5,campaign2,eval_big}/`; repo holds indexes+hashes.

## Cost ledger (whole program)
Campaigns 1+2 ≈ $4; Corpus v2/v3/v4 proofs ≈ $2; v5 scaling ≈ $2; infra saga ≈ $1.3.
Total GPU ≈ **$10**. Volume €13.84/mo. Balance ~$13.

## Phase II — data acquisition (design first, don't start building)
Objective: **acquire substantially more diverse, commercially-usable real guitar stems.**
Priority = **diversity before quantity** (players, studios, genres, pickups, tunings,
gain structures, masking regimes, recording styles). The platform stays unchanged unless
benchmark evidence names a real bottleneck. Candidate routes (from the earlier plan):
research proof via user-assisted MedleyDB + Cambridge-MT (non-deployable, proves the
lever) → then commercial-clean owned data via the Riley Studio commissioning plan
(`docs/RILEY_COMMISSIONING_PLAN.md`) for a deployable beat.

Phase I = a reproducible AI training platform. Phase II = feed it better data.
