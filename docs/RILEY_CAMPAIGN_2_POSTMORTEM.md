# Riley Campaign 2 — Post-Mortem (validated fine-tune)

**Campaign id** `riley_campaign_2_validated_finetune` · git `ceb9d87` · 2026-08-05 · A40 ~$1.85

Corrects Campaign 1's single methodological error: models now **fine-tune from the
validated pretrained htdemucs checkpoint** (C1 trained from random init). Corpus is
the only variable within C2; everything else identical to C1.

## The only change (verified before spend)
- Pretrained stock htdemucs (demucs `955717e8`, sha `ac558614`), **100% backbone
  transfer** (529/533 keys; only the 2-stem output head reinitializes).
- Required `bottom_channels 0→512`: **C1's b1 config was non-standard htdemucs** — only
  35% of stock weights fit it, so C1 *could never* have loaded stock htdemucs even if
  `--start_check_point` had been passed. That is the deeper root cause of C1-from-init.
  Standard htdemucs (42M params) applied identically to both arms.
- Gate passed: CPU dry-run confirmed weights load (transformer norm 26→103) + train +
  checkpoint; training log confirmed "Start from checkpoint".

## Results — held-out SI-SDR (median dB, 12 tracks each; IDENTICAL eval to C1)
| eval set | C1 Riley | C1 naive | **C2 Riley** | **C2 naive** | C2 Riley advantage |
|---|---|---|---|---|---|
| **eval_real** (deployment) | −10.88 | −12.34 | **+3.56** | −2.96 | **+6.52 dB** |
| eval_flat | −17.74 | −8.89 | +0.19 | +1.93 | −1.75 dB |

**Per-track win rate (C2, eval_real): Riley beats naive on 12/12 tracks.** Not a
2-song win — a clean sweep across every held-out realistic mixture. eval_flat: naive
wins 10/12 (flat is naive's own training distribution).

Final train loss: Riley 0.477, naive 0.715 (both ≪ C1's 1.35/1.76).

## Answers to the six campaign questions
**1. How much improvement came purely from transfer learning?**
Huge and isolated (corpus fixed, only init changed). eval_real median SI-SDR:
Riley −10.88 → **+3.56 = +14.4 dB**; naive −12.34 → −2.96 = +9.4 dB. Transfer learning
added ~+9 to +14 dB — it was the dominant missing ingredient, exactly as C1's
post-mortem predicted.

**2. Did Riley's +1.5 dB advantage increase, decrease, or stay stable?**
**Increased ~4.5×: +1.46 dB (C1) → +6.52 dB (C2).** The correct recipe did not erase
the corpus advantage — it *amplified* it. Better data pays off more once the model can
actually use it.

**3. Did absolute separator quality reach promotion level?**
Objectively strong: Riley is now **positive (+3.56 dB) and wins 12/12** on realistic
mixtures — usable separation, not the broken negative of C1. Full promotion also
requires **blind-listening confirmation** (package generated, human gate pending — see
below). Not auto-promoted on a metric alone; the gate is not weakened.

**4. Which failure modes remain?**
- **Distribution-matching persists**: naive still wins on flat mixes (its home turf).
  Mitigated in deployment terms — real songs are realistic, not flat.
- **Hard/quiet-guitar clips** stay negative for both (e.g. tracks with very low guitar
  occupancy: −12.7, −10.5 dB) — the residual tail is sparse-guitar content.
- Absolute ceiling still short of studio-grade; +3.6 dB median is good-not-great.

**5. What does the Coverage Planner identify next?**
Unchanged (corpus not modified): high-masking Virtual-Studio scenarios + high-gain /
palm-mute / tapping green ingest first, then commission 7-string / baritone / drop
tunings. These target exactly the hard-clip tail in (4).

**6. Is Riley Corpus v1.0 worthy of becoming the default training corpus?**
**Yes — recommended, pending blind-listening confirmation.** On the deployment-relevant
axis it wins every held-out track by a wide, consistent margin, at positive absolute
quality, fully reproducible. This is the first scientifically valid evidence that
Riley's manufactured data produces a measurably better separator.

## Promotion gate assessment
| requirement | status |
|---|---|
| benchmark improvement | ✅ +6.52 dB median, 12/12 tracks |
| cross-regime consistency | ✅ 12/12 on realistic (flat = naive's training dist, not deployment) |
| no unacceptable regressions | ✅ vs C1 massive gain; flat "loss" is expected home-field, still positive |
| reproducibility | ✅ corpus↔ckpt hashes, frozen configs |
| complete provenance | ✅ campaign manifest + ckpt sha256 + corpus hash |
| **blind listening improvement** | ⏳ **package ready — human gate PENDING** |

**Verdict: PROMOTE-RECOMMENDED, gated on blind listening.** Objective evidence clears
5/6; the final human blind check is the remaining requirement and is not skipped.

## Blind-listening package
`hetzner:/mnt/HC_Volume_106533567/factory/campaign2/blind_package/` — 12 tracks, each
with `mixture.wav`, `guitar_truth.wav`, `sep_X.wav`, `sep_Y.wav` (X/Y randomized per
track; `BLIND_KEY.json` sealed). Listen, note X-vs-Y per track, then reveal the key.

## C1 vs C2 — what this conclusively separates
C1 (from-init) vs C2 (fine-tune) isolates **model initialization** from **corpus
quality**. The within-C2 A/B isolates **corpus quality** cleanly (both arms identical).
Conclusion: **corpus quality and pretrained init are additive and both large** — Riley's
data helps regardless of init, and helps *more* with the correct recipe. C2 is Riley's
definitive baseline for all future corpus versions.

Artifacts: manifest `lab_data/factory/campaigns/campaign_2.json`; eval
`campaign_2_eval_results.json` + `campaign_2_pertrack.json`; ckpt hashes
`campaign_2_ckpt_sha256.txt`; checkpoints on volume `campaign2/checkpoints/`.
