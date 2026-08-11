# Riley Phase II-A — Diversity Gate Result (NEGATIVE)

**Verdict: diverse real data did NOT beat stock on `eval_big`. Do not commission into
a plateau.** 2026-08-08.

## What was tested
The Phase II-A pivotal question: *does more **diverse** real data beat stock, or is parity
a hard ceiling?* (see `RILEY_PHASE_II_STRATEGY.md`). Corpus **v7 = 275 real songs**:
- 190 MoisesDB (the v5 real corpus that reached parity), plus
- **85 Cambridge-MT** multitracks — the diversity MoisesDB lacked (metal, blues, funk,
  punk, country), built via windowed 6-source extraction (`build_cambridge2.py`).

Fine-tuned `htdemucs_6s` from stock, same validated recipe (c5 config, 100% backbone
transfer, 16 epochs, `num_workers 6`). ckpt sha256 `c2c5c41b…`. GPU cost ≈ $3.

## Result (`eval_big`, 42 held-out MoisesDB guitar clips, SI-SDR)
| model | median | mean | per-track win vs stock |
|---|---|---|---|
| stock htdemucs_6s | **+2.84** | **+2.74** | — |
| riley_v7 (275 diverse) | +1.92 | +2.07 | **14/42 (33%)** |
| riley_v6 (190 MoisesDB) | +2.31 | +2.68 | ~parity (prior) |

Mean Δ **−0.67 dB**, median Δ **−0.10 dB**. Adding the diverse Cambridge distribution
did **not** lift `eval_big` — it slightly regressed it vs the MoisesDB-only v6.

## Interpretation
Two live hypotheses, not distinguished by this run:
1. **Ceiling is model-bound, not data-bound.** More/diverse data does not move a fixed
   `htdemucs_6s` fine-tune past stock → diversity is not the binding lever.
2. **Eval blind spot.** `eval_big` is **100% MoisesDB**. It cannot see gains that diverse
   data would produce on *out-of-MoisesDB* real songs — the actual generalization question.
   Cambridge is a different mic/mix/genre regime; training on it can pull the model off the
   MoisesDB eval distribution while helping elsewhere we can't currently measure. The
   near-zero **median** Δ (−0.10) says most clips are a coin-flip; the mean is dragged by a
   few large losses.

## Decision (per Phase II-A gate)
**No decisive lift → do NOT proceed to Tier-3 commissioning yet.** The strategy doc's gate
is explicit: don't spend into a plateau. Before any commissioning $:
1. **Fix the eval blind spot first.** Build a **diverse held-out eval** (Cambridge held-out
   clips + any non-MoisesDB real guitar) so the benchmark can actually detect diverse-regime
   gains. Re-score v6, v7, stock on it. This is the cheap, decisive next step — it tells us
   which hypothesis is true.
2. If v7 **beats stock on the diverse eval** → hypothesis 2, diversity works, eval was
   blind → proceed to staged Tier-3 with a diversity-aware benchmark.
3. If v7 **still ≤ stock on a diverse eval** → hypothesis 1, ceiling is model-bound →
   re-examine model/recipe (capacity, longer schedule, per-source loss) before spending.

## Tiebreaker — DIVERSE held-out eval (ran 2026-08-08, ~$3)
Built **39 fresh Cambridge-MT clips never trained on** (metal/heavy/punk/country/rock —
the diverse regime), scored stock + v6 (MoisesDB-only) + v7 (diverse) on it:

| model | median | mean | wins vs stock | mean Δ | median Δ |
|---|---|---|---|---|---|
| stock htdemucs_6s | **+3.30** | +2.74 | — | — | — |
| riley_v6 (190 MoisesDB) | +2.15 | +2.18 | **12/39 (31%)** | −0.56 | −0.58 |
| riley_v7 (275 diverse) | +3.03 | +1.68 | **21/39 (54%)** | −1.06 | **+0.09** |

**Two things are now true:**
1. **Diversity IS a real lever — the eval blind spot was real.** On diverse songs, v7
   (diverse-trained) clearly beats v6 (MoisesDB-only): 21/39 vs 12/39 wins, median Δ vs
   stock +0.09 vs −0.58. Training on Cambridge helped on Cambridge-distribution audio,
   exactly the gain `eval_big` (MoisesDB-only) structurally could not see. Hypothesis 2
   confirmed in part.
2. **But diversity alone only reaches PARITY, not a beat.** Even on the favorable diverse
   eval, v7 vs stock = 21/39 (one-sided binomial **p=0.375, NOT significant**), median tie
   (+0.09), mean −1.06 (a few catastrophic clips = high variance). The ~parity band holds
   across BOTH evals. Stock htdemucs_6s is a hard bar; Hypothesis 1 (model/data ceiling near
   stock) also has support.

## Decision
**Not a commissioning green light, not a dead plateau — a proven direction.** Diversity moves
the model the right way on the target distribution but stops at parity. The cheap levers are
not exhausted, so do NOT spend on Tier-3 yet:
1. **Scale diverse data (cheap, $0 data + ~$3 GPU).** Only **85 of ~287 fresh Cambridge
   guitar songs** were used. v7>v6-on-diverse says more diverse real data is the live lever —
   pull the rest of Cambridge (+ MedleyDB), retrain, re-score on the diverse eval.
2. **Adopt the diverse eval as a standing gate** (grow to ≥60 clips to cut the ±1 dB noise
   that makes 21/39 ambiguous). `eval_big` alone is blind to the diversity lever.
3. **Only if scaled-diverse still can't pass stock** → the ceiling is model-bound → revisit
   capacity/recipe before any commissioning $.

## Scaled-diverse retrain — v8 (ran 2026-08-08, ~$3) — DIVERSITY LEVER CONFIRMED
Built **120 more fresh Cambridge songs** (used 205 of ~287 total) → corpus **v8 = 395 real
songs** (190 MoisesDB + 205 Cambridge), same recipe. Grew the diverse eval to **52 fresh
held-out clips**. Scored stock/v7/v8:

| model | median | mean | wins vs stock | mean Δ | sign-test p |
|---|---|---|---|---|---|
| stock htdemucs_6s | +3.34 | +2.28 | — | — | — |
| riley_v7 (275 songs) | +3.08 | +2.02 | 28/52 (53%) | −0.26 | 0.339 |
| **riley_v8 (395 songs)** | **+3.49** | **+3.17** | 28/52 (53%) | **+0.89** | 0.339 |

v8 vs v7 head-to-head: **30/52 v8 better, mean Δ +1.15**.

**This is the strongest Riley result to date.** For the first time a Riley model's central
tendency is **above stock on BOTH mean (+0.89) and median** on a held-out eval. Scaling
diverse data 85→205 Cambridge songs added **+1.15 mean over v7** — a clear monotonic
data→quality trend. Diversity is not just a real lever, it's a *productive* one.

**Honest caveat:** per-track win rate is still 28/52 (53%), **sign-test p=0.339 — NOT
statistically significant.** The mean gain comes from v8 winning *big* where it wins and
*losing small* where it loses (fewer catastrophic clips), not from winning more clips. So:
**aggregate-quality beat, not yet a per-track-decisive beat.** Fewer disasters is itself a
perceptual win, which is exactly the regime where blind listening—not the sign test—decides.

## Decision (updated)
The lever is confirmed and the aggregate crossed stock. Two parallel moves, still **no
commissioning spend yet**:
1. **Run the real-song BLIND vs stock on v8** (the matured deciding gate, P2). Metrics are
   now favorable enough that blind listening is the right arbiter — if ears prefer v8, this
   is deployable-quality direction and *justifies* commissioning the commercial-clean version.
2. **Keep scaling free diverse data** (rest of Cambridge + MedleyDB) to push the sign test
   to significance. Trend says more diverse data keeps helping.
3. Only commission (Tier-3, staged) once blind confirms — then for the *shippable* corpus
   (Cambridge/MoisesDB are NC/research-only; a deployed model needs commercial-clean data).

## v9 — +61 targeted non-rock songs (ran 2026-08-09, ~$4) — TREND STRENGTHENS
Added 61 fresh **non-rock** Cambridge songs (Pop 26, Acoustic 15, Indie 12, Electronica 7,
HipHop 1 — the exact coverage gap) via the Mac-build-ship pipeline. Corpus **v9 = 456 songs**
(v8's 395 + 61). Same recipe. Scored stock/v8/v9 on the 52-clip diverse eval:

| model | median | mean | wins vs stock | mean Δ | sign-test p |
|---|---|---|---|---|---|
| stock htdemucs_6s | +3.34 | +2.28 | — | — | — |
| riley_v8 (395) | +3.49 | +3.17 | 28/52 | +0.89 | 0.339 |
| **riley_v9 (456)** | **+4.27** | **+4.11** | 29/52 (55%) | **+1.83** | 0.244 |

v9 vs v8: **31/52 v9 better, mean Δ +0.94** (p=0.106).

**The diversity trend is now monotonic and accelerating on aggregate quality:**
mean Δ vs stock **v7 −0.26 → v8 +0.89 → v9 +1.83**; median gap vs stock **+0.06 → +0.93**.
Just 61 targeted non-rock songs nearly **doubled** the mean margin and lifted the median
decisively above stock. v9 is clearly the best model to date on aggregate.

**Caveat unchanged:** per-track win rate still 29/52 (55%), **sign-test p=0.244 — not
significant.** v9 wins *big* where it wins and *loses small* elsewhere; the margin is large
(mean +1.83, median +0.93) but not spread across enough clips to pass the sign test. Note a
mild train/eval alignment (both are diverse Cambridge, held-out but same distribution family);
stock gets no such benefit, so the lead is real, but a real-song blind is the honest arbiter.

## Decision (updated again)
The lever is decisively confirmed and the aggregate lead over stock is now sizable. Highest-value
next step is unchanged and now clearly warranted: **run the real-song blind vs stock on v9.**
Metrics strongly favor Riley; ears decide deployability. In parallel, the remaining ~157
Cambridge songs (incl. Rock 94) would likely push the sign test to significance. Commission
(Tier-3) only after a blind win, for the shippable commercial-clean corpus.

## v9 REAL-SONG BLIND — WASH (perceptual parity with stock), metric overstated (2026-08-09)
Blind A/B, 12 diverse held-out songs, v9 vs stock, pre-registered ≥9/12:
**v9 3 · stock 8 · tie 1.** BUT: 8–3 of 11 decided is **two-sided binomial p=0.23 — NOT
statistically distinguishable from a coin-flip**, and the listener reported **guessing on most
pairs because A and B sounded the same.** Honest verdict = **WASH: v9 and stock are perceptually
near-indistinguishable**, NOT "stock clearly better" (an earlier over-read of the raw tally, since
corrected). (The nominal split leaned stock on acoustic/country/clean-rock; v9's 3 nominal wins
were heavier/electronic — within noise.)

**What this means:**
- **SI-SDR overstated v9's position** (+1.83 mean / +0.93 median) — that lead is largely
  train/eval distribution-alignment inflation, not a real perceptual advantage. But v9 is **not
  perceptually worse** than stock; it's ~equal. (Still the pattern that SI-SDR on train-aligned
  eval is a poor perceptual proxy — the gate correctly refused to over-promote on the metric.)
- **Diversity got a fully Riley-assembled diverse corpus to PERCEPTUAL PARITY with production
  stock** — reconfirms the Phase I parity finding, now at the perceptual level on diverse real
  audio. That's a genuine positive: Riley-native data ≈ production quality by ear.
- **Deployment call unchanged: a wash is not a reason to ship/commission.** No clear perceptual
  win over production, and the corpus is NC/research-only anyway → do NOT commission on this.

**Implications:**
- **Do NOT commission** — parity, not superiority; no reason to switch from production stock, and
  the data can't ship (NC). The SI-SDR gate would have greenlit Tier-3; the blind + significance
  correctly held it back.
- **To actually BEAT stock (not just match): the lever is the objective/architecture, not more
  data.** SI-SDR reference-matching plateaus at perceptual parity here. Beating stock perceptually
  would need a perceptual objective / different arch / independent perceptual eval — not more Cambridge.

## v10 — PERCEPTUAL-LOSS OBJECTIVE (2026-08-10) — the lever moves the EAR
Held data + recipe constant (v9's 456-song corpus, from stock, c5, 16ep); ONLY change =
add perceptual multi-resolution STFT term (`--loss masked_loss multistft_loss`, auraloss).
SI-SDR dropped as expected (v10 +2.91 mean vs v9 +4.11 — perceptual loss doesn't chase SI-SDR).
**Blind v10 vs stock (12 diverse held-out): v10 5 · stock 4 · tie 3 → dead-even wash (p=1.0),
v10 slight edge.** Vs the v9 blind (stock led 8–3), v10 **flips the direction**: stock-preferred
8/12 → 4/12, Riley-preferred 3/12 → 5/12. **v10 is the FIRST Riley model to not lose the blind
to production stock.**

**The finding: the OBJECTIVE is a real perceptual lever — data was not.** Data-scaling (v7→v9)
moved SI-SDR but not perception; changing the loss to perceptual moved the *ear* from behind-stock
to even/ahead. Not yet a decisive win (needed ≥9/12), so not deployable/commissionable — but this
is the most promising signal in the project, and the perceptual weight used (multistft_coef 0.001)
was conservative. Clear untested headroom: bump the coef 5–10×, add mel-weighting, longer schedule.
**Recommended next: v11 = push the perceptual loss harder → re-blind; may cross to a decisive win.**
Artifacts: `campaigns/v10eval_{stock,v9,v10}.json`; v10 ckpt sha 88e3ff9a; blind console
https://claude.ai/code/artifact/8755105d-5a1d-478f-b38b-1827b7a08391

## v11 + BIGGER BLIND — DEFINITIVE CLOSEOUT (2026-08-11)
v11 pushed the perceptual objective 10× harder (multistft_coef 0.01 + auraloss
`perceptual_weighting`). 12-clip blind gave a faint v11 edge (5·3·4) — but a **28-clip blind
reversed it: v11 6 · stock 12 · tie 10, stock preferred 2:1 among 18 decided** (p=0.238;
definitively not a v11 win — needed ≥18/28). The small-sample edge was noise (5th such
false-positive the blind gate caught).

**FINAL VERDICT: Riley does NOT beat production htdemucs_6s.** The perceptual objective moved
Riley from clearly-behind (v9 waveform, stock 8–3) to **near-parity** (10/28 ties; Riley at-least-
tied on 16/28) — but where the models differ, stock is still preferred ~2:1. Near-parity, not
superiority. **Both levers are now exhausted:** data (v7→v9) moved SI-SDR not perception; objective
(v10→v11) moved perception to near-parity then stopped short of stock.

**Decision: REST the separator R&D.** No deploy, no commission — Riley matches production on many
songs but doesn't beat it, and the corpus is NC/research-only anyway. Only untested lever left is a
different architecture (large investment, no guarantee). Value banked: the gate discipline
(small-n blinds mislead → always ≥24 clips; SI-SDR is a false-positive machine — it would have
greenlit Tier-3 spend repeatedly, the blind vetoed every time), the objective>data lesson, and the
whole platform/factory/pipeline/catalog/data as reusable assets.

## Status (final for Phase II-A)
Diversity is a real **SI-SDR** lever (v7→v8→v9 monotonic) that reaches **perceptual PARITY** with
production (blind wash, p=0.23, listener guessing) — not superiority, not a regression. Riley-native
diverse data ≈ stock by ear. **Phase II-A verdict: parity confirmed, no perceptual win → no
commissioning.** To beat stock, change the objective/architecture, not the data. Prior text below for record.

Diversity confirmed as a strong, monotonic lever: **v9 (456 songs) beats stock by +1.83 mean /
+0.93 median** on held-out diverse audio — the best Riley result yet, though not yet per-track
significant. Next = real-song blind on v9 (+ optional continued scaling). **Blind console (v9 vs stock, 12
diverse held-out songs, pre-registered ≥9/12):** https://claude.ai/code/artifact/d6bdf1b5-9459-44a5-a8ad-fae2782346f6 — awaiting user verdict. Artifacts:
`campaigns/evaldiv3_{stock,v8,v9}.json`, `corpus_v9_ckpt_sha256.txt` (v9 sha b2f4ee2), plus
v8's `evaldiv2_*` / `corpus_v8_ckpt_sha256.txt` (1c22c17) and v7-era files.
