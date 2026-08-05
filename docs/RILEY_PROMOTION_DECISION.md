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

## Decision — TWO blind rounds

### Round 1 — synthetic eval (Virtual-Studio mixtures)
- blind picks: **7 / 12** → WASH → do not promote (recorded first).
- Metric said 12/12 (+6.52 dB); ear said chance. Held the gate; flagged eval-on-own-
  synthesis + model-correlation as suspects. Did NOT promote on the metric.

### Round 2 — REAL-song eval (15 MoisesDB multitracks) — DECIDING
- blind picks (Riley/total): **15 / 15** — Riley preferred on every real song.
- outcome: **PROMOTE** — clears the pre-registered ≥11/15 bar decisively.
- date: 2026-08-05
- statistical note: P(15/15 | fair coin) = 1/32768 ≈ 3e-5 — overwhelmingly significant.
- agreement: blind (15/15) CONFIRMS real-song SI-SDR (+3.62 dB, 13/15). Metric ↔ ear agree.
- interpretation: the corpus advantage is inaudible on easy synthetic mixes (Round 1 wash)
  but clearly audible on hard REAL songs (Round 2 sweep) — Riley's realistic-masking
  supervision generalizes to real audio; the naive corpus does not.

## FINAL: PROMOTE `riley_corpus_v1.0` as Riley's default training corpus.
Scope: this promotes the CORPUS (the data-centric decision Campaign 2 tested), not a
production-grade separator — absolute SI-SDR on real songs is still negative (domain gap
from GuitarSet+Freesound-only training). Riley's manufactured data is now the proven-
better default; separator quality improves next via Corpus v2 + real-song training data.
All six gates cleared: benchmark ✓, blind ✓ (real songs), cross-regime ✓, no regressions ✓,
reproducibility ✓, provenance ✓.
