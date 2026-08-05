# Riley Campaign 2 — Final Report & Promotion Decision

**Decision: DO NOT PROMOTE (this campaign).** Blind listening is a wash (7/12); it
disagrees with the objective metric. The promotion gate is upheld, not weakened.
2026-08-05 · git baseline `91dc044`.

## Outcome
| gate | result |
|---|---|
| SI-SDR (objective) | Riley +3.56 vs naive −2.96 dB, **Riley 12/12 tracks**, +6.52 dB |
| **Blind listening (human)** | **7/12 Riley-preferred — a wash (≈ chance)** |
| Verdict | **metric ↔ ear DISAGREE → hold, investigate** |

Listener's own words: *"a lot were very hard to tell the difference."* Naive was
preferred on 5/12 (tracks 01,02,05,06,11). P(≥7 of 12 | fair coin) = 0.39 — no
statistically detectable perceptual advantage.

## Why they disagree (evidence-based, before any retrain)
**1. Both arms are near-identical models → outputs perceptually indistinguishable.**
Both fine-tuned from the SAME stock htdemucs (100% shared backbone) for only 8 epochs.
Short fine-tunes stay close to the shared prior *and to each other* — the corpus nudges
weights slightly, not into audibly different solutions. This is the B1 lesson restated:
*a fine-tune of a shared checkpoint is highly correlated with its sibling.* The
listener hearing "hard to tell the difference" is direct confirmation.

**2. The SI-SDR gap is real but sub-perceptual, and likely inflated by eval-on-own-synthesis.**
`eval_real` mixtures were built by Riley's OWN Virtual Studio. SI-SDR rewards precise
scale/energy reconstruction of *that synthesis* — so the arm trained on the matching
distribution (Riley) scores a small, consistent numeric edge on all 12. That edge is
~sub-perceptual (both models make similar artifacts/bleed), so the ear doesn't follow.
SI-SDR measured "matches my own synthesis," not "cleaner guitar to a human."

**3. No real-commercial-song eval exists.** The T2 held-out real set (mixture + guitar
GT) was lost to scratch. Both the metric and this blind ran on Riley-synthesized
mixtures — neither settles deployment quality on real music.

## What is / isn't proven now
- **Proven:** the validated fine-tune recipe works (both arms went from C1's broken
  negative SI-SDR to positive). Transfer learning was the dominant fix.
- **Proven:** on Riley-synthesized held-out mixtures, Riley's corpus yields a consistent
  SI-SDR edge.
- **NOT proven:** that edge is *perceptible*, or that it holds on *real songs*. The
  corpus-quality claim from the C2 post-mortem is **downgraded to: numerically favorable,
  perceptually unconfirmed.** Campaign 1's "+1.46 dB" and Campaign 2's "+6.52 dB" are
  both metric-only; neither has cleared blind.

## The methodology win (this is the real result)
The gate caught a **metric false-positive** for the third time (B1, AudioShake, now C2).
SI-SDR 12/12 + "+6.52 dB" looked promotion-grade; blind said chance. Had we promoted on
the metric, Riley would have shipped an unproven corpus as default. **Blind listening
remains the only reliable judge** (memory: numeric scores don't predict perception).

## Next actions (evidence before change)
1. **Build a DURABLE real-song eval set** (real commercial/CC mixtures + isolated guitar
   GT) — the actual deployment test the project has lacked since T2. This is the
   blocker; do it before any more training. (Coverage/factory unaffected.)
2. **Re-blind on real songs** with both C2 checkpoints → settles the perceptual question
   on deployment-relevant audio.
3. **Make the arms less correlated if a real difference is wanted:** longer fine-tune,
   or a larger corpus gap (Corpus v2 with genuinely different supervision), so the data
   effect can exceed the shared-backbone floor. Only after (1)-(2).
4. Do NOT promote Riley v1.0 as default on current evidence. Do NOT retrain blindly.

## UPDATE (2026-08-05) — real-song eval built → PROMOTED
The missing deployment eval was built: 15 real MoisesDB multitracks (mixture = sum of
all stems, guitar_GT = sum of guitar stems) — a real mix, not Virtual-Studio synthesis,
so the eval-on-own-synthesis objection is removed.
- **Real-song SI-SDR:** Riley −2.35 vs naive −5.97 = **+3.62 dB, 13/15** (corpus
  advantage reproduces on real audio).
- **Real-song blind: 15/15 Riley-preferred** (P ≈ 3e-5). DECISIVE, and it *confirms* the
  metric — metric ↔ ear now agree on the deployment-relevant distribution.
- Reconciliation with the Round-1 synthetic wash (7/12): the corpus advantage is
  inaudible on easy synthetic mixes but clearly audible on hard real songs — Riley's
  realistic-masking supervision generalizes; the naive corpus does not. The synthetic
  wash was a true-negative *for the synthetic distribution*, not for deployment.
- **DECISION: PROMOTE `riley_corpus_v1.0` as the default training corpus** (see
  `RILEY_PROMOTION_DECISION.md`, `lab_data/factory/DEFAULT_CORPUS.json`). Scope: the
  corpus (better data) — absolute separator quality on real songs is still negative
  (domain gap; both models trained only on GuitarSet+Freesound), improved next via
  Corpus v2 + real training data.
- Methodology payoff: the gate's discipline (reject the metric-only 12/12, demand a
  real-song blind) turned a false-looking promotion into a *correctly earned* one.

## CRITICAL CAVEAT (2026-08-05) — vs stock htdemucs_6s (jamn.app production)
The A/B was Riley-corpus vs naïve-corpus (both fine-tuned from stock). Scoring the
**production** separator (stock `htdemucs_6s`, 6-source, what jamn.app runs) on the same
15 real songs:

| real-song guitar SI-SDR (median) | value |
|---|---|
| **stock htdemucs_6s (production)** | **+3.12 dB** |
| Riley C2 (manufactured corpus) | −2.35 dB |
| naïve C2 | −5.97 dB |

**Stock beats the Riley model by ~5.5 dB and is positive where both our fine-tunes are
negative.** Fine-tuning stock htdemucs on our synthesis-only corpus (GuitarSet+Freesound,
2-stem, 6 s) **regressed** real-song performance below the stock starting point. Riley's
data regressed it *less* than naïve data (the real +6.52 dB / 15-0 A/B — the factory
works), but "less damage" is still worse than production.

**Consequences (honest):**
- The Riley model is **NOT deployable** — shipping it would badly regress jamn.app. Do
  not wire it as a SeparatorProvider.
- The corpus promotion stands as a **data-quality** result (Riley data > naïve data),
  NOT a "we have a better separator" claim.
- **Root cause is now precise:** the corpus is synthesis-only; stock trained on massive
  *real* multitrack. To *beat* stock, the factory must add real training data
  (MoisesDB-style — now accessible via the remotezip range trick) and likely keep the
  6-source setup / longer clips rather than the narrow 2-stem/6 s recipe.

## Status of frozen artifacts
Campaign 2 stays the frozen reference (recipe validated, hashes locked). Its *promotion
status* is **withheld — perceptually unconfirmed**. The comparison bar for future
corpora is unchanged, but the target metric must be re-validated against a real-song
blind set, not synthesis-SI-SDR alone.
