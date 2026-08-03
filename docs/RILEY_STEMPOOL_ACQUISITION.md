# Riley Stem Pool — Acquisition Roadmap & Commercial Sourcing Strategy (Phase 3)

The factory is frozen; the bottleneck is the corpus. This is the plan to grow a
**legally-ownable-forever, fully-audited** Stem Pool. Every candidate is judged on
the Phase-3 principle: *ownership, quality, diversity, provenance* — not quantity.
Evidence base: `docs/PHASE2_DATASET_DISCOVERY.md` (6-stream survey). No integration
here — identification, pricing, rights, suitability, and outreach order.

## Decision criteria (a source enters the pool only if all hold)
1. **Ground-truth stems**, not AI-separated (training a separator on separated output is circular — the #1 trap).
2. **Commercial + AI-training rights** that survive scrutiny (both master and composition; model-distribution allowed).
3. Real isolated **guitar** (or real backing for the Virtual Studio) with usable **provenance**.
4. Passes the **Dataset Auditor** + **license gate** (`training_data` registry) — nothing bypasses the pipeline.

## Priority 1 — Green (immediately usable, in the registry)
| Source | Content | License | Pool role | Status |
|---|---|---|---|---|
| **GuitarSet** | 360 real **acoustic** excerpts, per-string + mic | CC-BY-4.0 / MIT | acoustic guitar targets + DI-ish source | **INGESTING (Phase 3 seed)** |
| **Guitar-TECHS** | real **electric** DI + amp + multi-mic, per-string MIDI | CC-BY-4.0 | electric DI targets (the acoustic-gap fill) | download + ingest next |
| **EGFxSet** | real-HW **effect** timbres (isolated notes) | CC-BY-4.0 | distortion/effect augmentation bank | download + ingest |
| **Slakh2100** | synthetic multitrack + guitar stems | CC-BY-4.0 | **pretraining priors only** (synthetic) | selective (large; R2) |
| **MUSAN** | noise/interference | CC-BY-4.0 | augmentation (harden separator) | optional |

Priority-1 alone gives real acoustic + real electric DI + effect coverage, all
commercial-clean. **This is the first permanent pool.** Verify the AMBER items
(GuitarDuets CC variant; EGDB/GOAT access terms) before promoting them from
firewalled to green.

## Priority 2 — Commercial licensing (research + outreach; no integration)
Ranked by fit (clean rights × real guitar stems × negotiability). Prices are
order-of-magnitude from the Phase-2 anchors (SourceAudio ~$1.25M avg deal;
academic relicense $10k–100k; enterprise music-data six–seven figures).

| Target | What to ask for | Rights posture | Est. cost | Priority | First action |
|---|---|---|---|---|---|
| **GuitarSet commercial relicense** (NYU/MARL) | broaden the already-clean isolated acoustic guitar to a commercial grant + possible expansion | academic clean; relicense likely | **~low-$10k** | **1 (do first)** | email MARL: commercial license + expansion interest |
| **SourceAudio** (+ Musical AI) | a guitar-focused slice of **ground-truth** (not AI) stems, cleared for AI training | product IS a training license | ~$50–150k pilot | 2 | request guitar-stem-granularity + ground-truth confirmation, pilot quote |
| **Datarade sellers** (Soundsnap etc.) | "tracks w/ stems, cleared for ML" — verify ground-truth guitar | transactional, fast quote | ~$10–100k | 3 | RFQ 2–3 sellers; demand ground-truth guitar stems |
| **Music.AI / Moises** (MoisesDB commercial) | commercial license of the best real guitar-stem taxonomy | recordings clean; NC public, negotiable | inquiry (est. $X0k) | 4 | sales inquiry (we already have an API-stub relationship) |
| **becruily MelBand-RoFormer Guitar** | (model, not data) private commercial weights license | non-commercial default | negotiate | watch | Discord/DM if a model-license path reopens |
| **MassiveMusic (bespoke)** | commission isolated guitar to spec (100% clean rights) | work-for-hire, fully owned | project | fallback | brief if licensed stems fall short |

**Hard rule:** confirm **ground-truth vs AI-separated** before any payment (AudioShake/Moises *separations* are unusable as targets). Get **perpetual-for-trained-weights** language + model-distribution rights.

## Priority 3 — Riley-owned recordings
See `docs/RILEY_COMMISSIONING_PLAN.md`. This is the durable moat: data Riley owns
outright, coverage-gap-driven, expandable forever. Lean DI economics (Phase 2):
~$75–150/song via AirGigs/SoundBetter with explicit DI + AI-training rights →
100–500 real DIs = $7.5k–$75k, each expandable ×N via the Transform Engine + Virtual
Studio.

## Roadmap (compounding, infra-first — the factory already exists)
- **Now (Phase 3):** ingest Priority-1 green (GuitarSet acoustic seeded; then Guitar-TECHS electric DI, EGFxSet). First permanent pool + coverage report → expose gaps.
- **Q+1:** open the two cheap high-value licensing calls (GuitarSet relicense, SourceAudio pilot RFQ). Launch the first commissioning campaign against the biggest coverage gap (real electric DI — see plan).
- **Q+2:** close one commercial deal for real *backing* stems (unblocks the Virtual Studio's real-music mixtures → the first genuine "manufactured beats baseline" train). Expand commissioning to weak regimes surfaced by the benchmark.
- **Q+3→:** consent-gated crowdsource flywheel (opt-in app users) layered on the owned bootstrap. Corpus compounds; each new source flows through the same pipeline.

## What NOT to do
- No bulk-scraping (Cambridge-MT / karaoke-version / game rips — infringing or ToS-violating).
- No AI-separated stems as targets. No sample-library audio without a written AI-training rider (EULAs mostly forbid it).
- No GPU spend on temporary corpora — the next campaign trains on assets Riley keeps forever.
