# Riley Phase II — Data Acquisition Strategy

Phase I proved the platform and reached **parity** with production stock; the binding
constraint is now **diverse, commercially-usable, real guitar data**. Phase II is a
data-acquisition project, not an engineering one. Design first; the platform stays
unchanged. 2026-08-07.

## Objective & success bar
Acquire enough diverse, **commercial-clean** real guitar data that a Riley-trained
separator **decisively beats** production `htdemucs_6s` and is **shippable**.
- Objective metric: on `eval_big` (≥42 clips, growing) a clear per-track majority + a
  positive mean Δ vs stock (+2.87 mean today), not a parity coin-flip.
- Deciding gate: **real-song blind win vs stock** (the matured gate, P2).
- Deployability gate: **commercial_training_allowed = true** through the whole lineage
  (MoisesDB/MedleyDB/Cambridge are all NC — research-only, cannot ship).

## Guiding principles (from Phase I)
- **Diversity before quantity.** MoisesDB (~200 songs) got us to parity; more of the
  *same* distribution plateaued (v5/v6). The gap is coverage, not sample count.
- **Real mixing/interaction is the payload** — synthesis-only regressed (−2.35). Prefer
  real recorded-together multitrack; use the Virtual Studio to *augment* real stems, not
  replace them.
- **Planner-driven.** The Coverage Planner already ranks gaps by separation impact and
  assigns an acquisition strategy per dimension. Phase II executes its priority list.
- **Prove the lever before spending.** Commissioning is the big cost — validate that
  *diverse real data beats stock* (cheaply, on research data) before paying for it.

## The diversity target matrix (Coverage Planner `TARGETS`, impact-weighted)
Highest-impact **empty/thin** regions in the current pool (acoustic-heavy, low-gain,
no tunings, pickup-unknown):
| dimension | high-impact targets (weight) | current | Planner strategy |
|---|---|---|---|
| tuning | baritone 2.4, 7-string 2.4, drop_c 2.2, 8-string 2.2 | none | **commission** |
| string_count | 7 (2.2), 8 (2.2), 12 (1.6) | 6 only | **commission** |
| masking_level | high 2.0, med 1.6 | thin | virtual_studio (real backing) |
| playing_style | metal 1.8, funk 1.4, ambient 1.4, blues 1.2 | rock/jazz/bossa/SS | license / commission |
| articulation | PinchHarmonics 1.8, PalmMute 1.6, Tapping 1.6 | some (Guitar-TECHS) | green + commission |
| gain | med 1.6, high 1.4 | all "low" | **commission** (crunch/edge DI) |
| acoustic_vs_electric / guitar_type | electric 1.5, distorted 1.6 | 96% acoustic | commission / license |
| recording_method | DI 1.8 | some | green |
| player_identity | many distinct players | few | **commission** (AirGigs/SoundBetter) |
Cost bands (Planner): low ≈ $500 · med ≈ $3,000 · high ≈ $12,000 per gap-batch.

## Acquisition tiers (mapped to Planner `_STRATEGY`)
**Tier 0 — Green / free (exhaust first, $0):** mine remaining EGFxSet effects + more
Guitar-TECHS techniques + more transform recipes → fills `amp_family`, `articulation`,
`recording_method`. On Hetzner, no spend. Do before paying for anything.

**Tier 1 — Research proof (non-deployable, ~$5 GPU):** add **MedleyDB** (~120 real
multitracks) + **Cambridge-MT** (100s, guitar-labelled) via user-assisted download (the
MoisesDB/Freesound pattern; datacenter-blocked → you fetch link, I range-extract). Build
a ~500-800-song diverse real corpus → retrain → `eval_big`. **This answers the pivotal
question: does *more diverse* real data beat stock, or is parity a hard ceiling?** NC/
educational, so not shippable — but it's the cheap business case for Tier 3.

**Tier 2 — License commercial-clean real multitrack (opportunistic):** survey/negotiate
licensable isolated-guitar multitrack — production-music libraries (SourceAudio), stem
marketplaces, direct artist/label deals. Phase-2 discovery already found this scarce; pursue
only where it fills a high-impact `playing_style`/genre gap cheaply.

**Tier 3 — Commission owned data ("Riley Studio", the deployable endgame):** pay session
players to record **isolated guitar + backing** across the exact gaps the Planner flags,
Riley-owned → commercial-clean by construction. The Planner auto-generates commissioning
briefs; execute in **impact-priority order**: tunings (baritone/7-8-string) + metal/
high-gain + pinch-harmonics/palm-mute + **many distinct players** (AirGigs/SoundBetter for
player_identity diversity). See `docs/RILEY_COMMISSIONING_PLAN.md`.

**Tier 4 — Manufacture/augment:** feed owned/licensed **real recorded-together** stems
into the Virtual Studio to multiply masking-regimes (real stems, realistic re-mix). Augments
real data; never the sole source (synthesis-mixing alone underperformed).

## Sequencing & decision gates
1. **Phase II-A (cheap, prove the lever):** Tier 0 (free) + Tier 1 (research proof).
   **GATE:** does the diverse real corpus beat stock on `eval_big` + a real-song blind?
   - *No decisive lift* → the ceiling is deeper than diversity; re-examine model capacity/
     eval before spending. (Don't commission into a plateau.)
   - *Decisive lift* → proven business case → proceed.
2. **Phase II-B (spend, deployable):** Tier 3 commissioning, Planner-prioritized, **staged
   small-first** — commission ONE high-impact batch (e.g. baritone/7-string + high-gain,
   ~$500-3k), fold in, re-run Planner + `eval_big`, confirm it *lifts the benchmark* before
   scaling to the next gap. Opportunistic Tier 2 licensing alongside.
3. Every acquired batch flows through the **unchanged platform**: `SourceProvider` →
   Dataset Auditor + License Registry gate → Coverage Planner re-scores gaps →
   Corpus vN frozen (hashed) → validated fine-tune → `eval_big` + real-song blind vs
   stock → promote only on a decisive, commercial-clean win → wire `SeparatorProvider` →
   deploy to jamn.app.

## Budget framing
- Tier 0: $0. Tier 1: ~$5 GPU + your download time. Tier 2: variable licensing.
- Tier 3 is the real spend: staged by Planner ROI. Start ≤ $3k (one batch) to prove
  commissioned data lifts the benchmark, then scale to the highest-ROI gaps. Do **not**
  pre-commit a large commissioning budget before Phase II-A confirms the lever.

## Risks
- **Commercial-clean scarcity** (confirmed) → commissioning is likely the only reliable
  deployable path; budget for it.
- **Commissioning cost/coordination** → mitigate with staged, Planner-prioritized batches.
- **Diversity vs quantity** → prioritize impact-weighted coverage, not raw song count.
- **Eval saturation** → grow `eval_big` as data grows (held-out, ≥40, real; guard against
  training/eval overlap) so it can still detect gains.
- **Parity may be model-bound, not data-bound** → Phase II-A's gate exists precisely to
  test this before committing commissioning $.

## Immediate next actions (Phase II-A, on your word)
1. Tier 0: sweep remaining EGFxSet/Guitar-TECHS green data into the pool ($0, Hetzner);
   re-run Coverage Planner to quantify exact current gaps vs `TARGETS`.
2. Tier 1: you grab MedleyDB + a Cambridge-MT guitar-song set (I'll name specifics);
   I range-extract on Hetzner, build a diverse real corpus, retrain, and run the
   beat-stock gate. This is the decision point for whether Phase II-B commissioning is justified.
