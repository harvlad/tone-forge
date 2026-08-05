# Riley Commissioning Plan — First Owned DI Campaign (Phase 3)

Data Riley owns outright: the durable moat. Commissioning is **coverage-gap-driven**
and **building-block-based** (reusable musical fragments, not songs), so one
recording generates hundreds of supervised examples through the Transform Engine
(re-amp × cab × EQ) and the Virtual Studio (× scenarios × masking). Feeds the same
pipeline as everything else — nothing bypasses license/audit/provenance/catalog.

## Priority update — after green-tier v2 (pool = 564 real assets)
The green tier already **partially filled** the original electric/pickup/gain gaps:
Guitar-TECHS added real electric DI + amp-mic; EGFxSet added 5 ground-truth pickup
positions + clean/distortion. So commissioning should now target **only what the
free green tier cannot give**, in priority order:
1. **Tunings** — everything ingested is standard tuning. Commission drop-D/drop-C/DADGAD/open-G.
2. **Medium / edge-of-breakup / crunch gain** — green tier is bimodal (clean vs high-gain pedals); the middle is empty.
3. **7-string / baritone / extended-range** — absent entirely.
4. **Musical-context clean electric DI across many players** — Guitar-TECHS DI is only 2 players / limited material.
(Difficulty + masking context come from the Virtual Studio + real backing, not commissioning.)

## Why this campaign, why now (the gap)
The Phase-3 seed pool is **GuitarSet — 100% real *acoustic*** guitar. Coverage will
therefore show a large hole in exactly what a modern separator most needs:
**real, clean *electric* DI** across pickups, tunings, gain intent, and techniques.
Recipes can *simulate* amp tones from a clean DI, but the DI itself must be **real
electric** (the synthetic→real gap is real). So the first owned campaign targets
**real clean electric DI building blocks** — the highest-leverage gap.

## Building blocks (the unit of commissioning — not songs)
Each block is a short, isolated, clean-DI performance of one technique, capturable
in minutes and reusable across every recipe/scenario:

open chords · barre chords · power chords · single-note riffs · arpeggios ·
palm-muting · harmonics · slides · bends · vibrato · tapping · fingerstyle ·
funk rhythm · metal rhythm · blues phrases · jazz comping · ambient textures ·
chugs/gallops · clean picking · double-stops

## Coverage axes to span (diversity > quantity)
- **Guitar type:** single-coil (Strat), humbucker (Les Paul), semi-hollow, baritone, 7-string.
- **Pickup position:** neck / middle / bridge (recorded per block where feasible — the one tag audio can't infer).
- **Tuning:** standard, drop-D, drop-C, DADGAD, open-G.
- **Gain intent:** clean / edge-of-breakup / crunch / high-gain (as *performance* dynamics; amp tone added later via NAM).
- **Playing dynamics:** soft → hard picking (feeds real dynamic range the auditor rewards).
- **Player diversity:** ≥ several players (micro-timing/feel variety) — the thing synthetic data cannot fake.

## Capture spec (what every deliverable must be)
- **Clean DI**, 48kHz/24-bit WAV, no amp/FX on the recorded file (reamp happens in-factory).
- 2–3 takes per block; ~30–60s each.
- Delivered with a **capture sheet**: guitar, pickup position, tuning, technique, gain intent, tempo, key.
- **Rights (non-negotiable in the contract):** perpetual, worldwide, sublicensable license to use the recordings to **train AI/ML models and distribute the resulting models**; work-for-hire / full assignment where possible; recorded per-contributor consent → maps to the provenance registry (`commercial_training_allowed: true`).

## Sourcing & economics (Phase-2 anchors)
- Marketplaces: **AirGigs / SoundBetter / Fiverr Pro** — session guitarists deliver isolated DI, ~**$75–150/song-equivalent**; specify DI + AI-training rights up front.
- A "session" = one player recording the full building-block set on 1–2 guitars/tunings → dozens of blocks per engagement.

## Pilot (validate the campaign, not scale)
- **20–30 guitarists**, targeting player + guitar + pickup + tuning diversity.
- **~100–200 DI building-block performances** total.
- **Budget:** ~$7.5k–$15k (lean tier) + ~25% ops (curation, capture-sheet QA, provenance entry, storage on Hetzner/R2).
- Each DI → (×recipes re-amp/cab/EQ) → (×scenarios × masking) = **hundreds of supervised pairs** per recording.

## Ingestion path (the same pipeline — no exceptions)
```
DI delivery + capture sheet
  -> CommissionedDISource (SourceProvider; dataset_key="riley_owned_2026Q1",
     registered commercial-clean; capture-sheet fields -> source tags incl. the
     otherwise-uninferable PICKUP + TUNING + GAIN INTENT)
  -> License verify (owned/clean) -> Dataset Auditor (reject bad takes) -> Metadata
     + regime tags -> Catalog
  -> Transform Engine (re-amp variants) + Virtual Studio (scenario mixtures)
  -> Coverage report re-run -> gap shrinks
```

## The self-directing loop (permanent)
```
Benchmark failure regime  ->  Coverage gap (coverage.py)  ->  Commissioning brief
  (e.g. "25 high-gain 7-string chug blocks, drop-C, bridge pickup, 120-160 BPM")
  ->  DI captured  ->  ingest+audit+catalog  ->  re-amp+scenario  ->  train  ->  benchmark
```
Coverage gaps generate briefs; recordings fill them; the benchmark verifies; the
next gap emerges. The corpus compounds and Riley improves as a consequence.

## First brief (draft — finalize against the real coverage report)
> **Need:** real clean **electric DI**, ~150 building-block takes. Guitars: Strat +
> Les Paul + one 7-string. Pickups: neck & bridge per block. Tunings: standard,
> drop-D, drop-C. Techniques: power chords, palm-mute chugs, single-note riffs,
> arpeggios, bends/vibrato, clean picking, funk rhythm, metal rhythm. Gain intent
> spread clean→high. ≥6 players. 48k/24-bit DI, 2 takes each, capture sheets,
> perpetual AI-training rights (work-for-hire).
