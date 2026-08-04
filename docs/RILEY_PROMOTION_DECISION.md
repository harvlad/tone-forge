# Riley Promotion Decision — Corpus v1.0 (Campaign 2)

**Status: PENDING blind listening.** Objective gate cleared; final human gate outstanding.
This document pre-registers the decision rule BEFORE blind results, so the outcome
cannot be rationalized after the fact.

## Objective gate — CLEARED
| requirement | status | evidence |
|---|---|---|
| benchmark improvement | ✅ | eval_real Riley +3.56 vs naive −2.96 dB SI-SDR |
| large held-out improvement | ✅ | +6.52 dB median advantage |
| cross-regime consistency | ✅ | Riley wins **12/12** held-out realistic tracks |
| complete provenance | ✅ | corpus↔ckpt hashes, campaign manifest (`CAMPAIGN_2_BASELINE.json`) |
| complete reproducibility | ✅ | frozen configs + seed 42 + committed hashes |
| campaign archived | ✅ | postmortems + eval + ckpts durable on volume |

## Remaining gate — blind listening (HUMAN)
Package: `hetzner:/mnt/HC_Volume_106533567/factory/campaign2/blind_package/`
12 tracks × { `mixture.wav`, `guitar_truth.wav`, `sep_X.wav`, `sep_Y.wav` }.
X/Y randomized per track; `BLIND_KEY.json` sealed (X→riley|naive per track).

**Protocol:** for each track, hear `mixture` then compare `sep_X` vs `sep_Y` against
`guitar_truth`; pick the cleaner guitar (fewer artifacts, less bleed, more continuous).
Record picks, THEN reveal the key.

## Pre-registered decision rule
- **Blind AGREES** (Riley preferred on a clear majority, ≥8/12): **PROMOTE**
  `riley_corpus_v1.0` as Riley's default training corpus. Objective + perceptual concur.
- **Blind DISAGREES** (naive preferred, or a wash ≤6/12 despite +6.5 dB SI-SDR): **DO NOT
  PROMOTE.** Document the metric↔perception disagreement and investigate (SI-SDR can
  reward energy match while perception penalizes artifacts/bleed — the exact lesson from
  the AudioShake/B1 retrospective). The gate stays evidence-based.
- **Partial** (7/12): treat as a wash → do not promote; gather more blind tracks first.

## Decision (recorded after blind)
- blind picks (Riley/total): **7 / 12** (naive preferred on tracks 01,02,05,06,11)
- outcome: **DO NOT PROMOTE** — 7/12 is a wash under the pre-registered rule (≥8 required).
- date: 2026-08-05
- notes: Blind DISAGREES with the objective metric. SI-SDR ranked Riley ahead on **12/12**
  tracks (+6.52 dB median); human blind is **7/12 ≈ chance**. Per the pre-registered
  disagreement branch, the gate is NOT weakened: promotion withheld, disagreement
  documented, root cause investigated. See `RILEY_CAMPAIGN_2_FINAL_REPORT.md`.
- statistical note: 7/12 under a fair coin has P(≥7)=0.39 — indistinguishable from chance.
  No evidence of a perceptible Riley advantage on this eval set.
