# Riley Campaign 1 — Post-Mortem

**First Riley-native training campaign — data-centric A/B**
Campaign id: `riley_campaign_1_data_centric` · git `6089583` (frozen) · 2026-08-04 · GPU cost ~$2.10 (A40)

## Objective & hypothesis
One question: **does Riley's manufactured corpus (realism engineering ON) produce a
better guitar separator than a naive corpus built from the EXACT same source
material (realism OFF)?** Hypothesis: yes — realistic masking/dynamics/backing balance
give supervision closer to real mixtures.

## Methodology
- **Arm A** = `riley_corpus_v1.0` (hash df4167dc, 2,232 pairs, 12 realistic scenarios).
- **Arm B** = `riley_baseline_naive_v1.0` (hash 20c0811f, naive flat mixing) — SAME 250
  guitars + SAME backing pool; **capped to identical N (2,232)** so size ≠ variable.
- **Architecture FIXED**: htdemucs, config `b1_htdemucs_guitar_ft.yaml` (T2 recipe), 6s
  chunk, 8 epochs × 1500 steps, adam 9e-5, aug on — **identical both arms**. Dataset is
  the only variable. CPU dry-run passed before spend.
- **Eval**: held-out GuitarSet guitars (novel to both models), two mixture styles
  (`eval_real` realistic, `eval_flat` naive), SI-SDR vs true guitar. (Training-time
  valid SDR = nan, the known silent-reference contamination — ignored.)

## Results (SI-SDR dB vs true guitar; median over 12 held-out tracks)
| eval set | A (Riley) | B (naive) | Δ (A−B) |
|---|---|---|---|
| **eval_real** (deployment-relevant) | **−10.88** | −12.34 | **+1.46 (Riley wins)** |
| eval_flat | −17.74 | −8.89 | −8.85 (naive wins) |

- Best-case per-clip reaches ~+6 dB; **median negative for both → weak, inconsistent separation.**
- Final train loss: A **1.35** < B **1.76** (Riley supervision more learnable).

## Verdict
**Directional support, NOT a promotion.** On realistic mixtures (what real songs are),
Riley's data produced a modestly better separator (+1.5 dB). But:
- Absolute quality is **poor** (negative median SI-SDR) for both arms.
- The result is strongly **distribution-matching** — each model wins on its own training
  style; the flat-eval reversal shows the +1.5 dB is partly home-field, not pure data quality.
- No blind-listening promotion (moot at this quality level).

Against the matured promotion gate (benchmark + blind + cross-regime consistency + no
regressions) → **FAIL. Neither model is promoted.** The gate did its job.

## Root cause (evidence before retraining)
The limiting factor is **model capacity/recipe + data quantity, NOT (yet) corpus quality**:
1. **Trained from-init, not fine-tuned from a pretrained htdemucs.** `run_c1.sh` passed no
   `--start_check_point`, so htdemucs trained from scratch. T2's lesson: from-scratch at
   small scale underfits (mel-RoFormer B2 F1 0.312); a stock-init fine-tune is what won
   (B1). This is the single most likely cause of the poor absolute SI-SDR.
2. **Data quantity**: 2,232 pairs / 12k steps is small for htdemucs-from-init.
3. Guitar is a hard, high-variance target; 6s clips limit long-context cues.

The A/B COMPARISON is still valid (both arms identical) — and it leans Riley on the
deployment-relevant axis — but the shared weak baseline caps confidence.

## Next actions
1. **Re-run as a TRUE fine-tune** from a stock htdemucs init (2-stem guitar/other head),
   identical A/B — expected to lift absolute quality and sharpen the data signal.
2. **Scale the corpus** (Coverage Planner: high-masking scenarios, high-gain/palm-mute
   green ingest, then commission 7-string/baritone) → Corpus v2, re-freeze, re-run.
3. **Fixed real-song blind set** with guitar GT (the T2 set was lost to scratch — now
   covered by the [[artifact-durability-rule]]); needed for a promotable blind gate.
4. Keep everything durable (checkpoints on volume + committed hashes) — done.

## What we proved
- The **factory → freeze → GPU → eval → gate → post-mortem loop runs end-to-end**, cheaply
  ($2.10), reproducibly (corpus hashes ↔ checkpoint hashes ↔ campaign manifest).
- First real data-centric signal: **Riley's realistic data is directionally better on
  realistic mixtures**, and its supervision is more learnable (lower train loss) — worth a
  fine-tune re-run to confirm at usable quality.

Artifacts: corpora `lab_data/factory/versions/`, campaign `lab_data/factory/campaigns/`,
checkpoints on volume `campaign1/checkpoints/` (hashes committed), eval
`campaign_1_eval_results.json`.
