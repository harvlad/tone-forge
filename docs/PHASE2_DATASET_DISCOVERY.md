# Riley Phase 2 — Comprehensive Dataset Discovery & Acquisition

Evidence base: a 6-stream parallel research sweep (academic/MIR · commercial licensors · Creative-Commons communities · guitar-specific DI/amp/tab · overlooked institutions · synthetic & build-economics). This is a research deliverable, not an implementation. Every acquisition candidate is measured against Riley's actual need and the project's **hard license gate** (`backend/lab/training_data.py`).

---

## 0. The one structural finding everything rests on

Riley needs a specific, rare thing: **real, isolated guitar stems, in a full-band mixture, genre-diverse, and commercially clean for AI training.** The sweep shows that intersection **does not exist off the shelf**, because the field splits cleanly in two:

- **Dedicated guitar datasets** (GuitarSet, EGDB, GOAT, Guitar-TECHS, EGFxSet, GuitarDuets…) are built for *transcription/synthesis*: isolated guitar with tabs/MIDI, **no mixture to separate from**.
- **Mixture datasets** either **bury guitar inside an "other" stem** (MUSDB18, DSD100) or give a real labeled guitar stem but are **non-commercial** (MoisesDB, MedleyDB).

Only **MoisesDB** (real, NC) and **Slakh2100** (synthetic, CC-BY) ship a labeled guitar stem in mixture context; only **GuitarDuets** is a purpose-built guitar-vs-guitar separation set.

**Two-layer license test** (both must pass to ship in a commercial model):
1. **Packaging license** — is the dataset CC-BY / permissive vs CC-BY-NC / ND / academic-only?
2. **Recording clearance** — were the underlying recordings ever cleared for commercial AI training? (A CC-BY *wrapper* around YouTube-scraped audio fails layer 2.)

**Passes both cleanly:** Slakh2100 (synthetic), GuitarSet, Guitar-TECHS, EGFxSet, NSynth, MUSAN, Spheres (no guitar). **Passes layer 2, NC on layer 1 (negotiable):** MoisesDB. **Everything else fails one or both.**

The consequence drives the whole recommendation: **the clean real-in-mixture guitar corpus must be either licensed (few viable sellers) or manufactured (real DI + augmentation) — it cannot simply be downloaded.**

---

## 1. Comprehensive dataset catalogue

Rating = Riley usefulness. "Clean" = passes both license layers for a shipped commercial model.

### 1a. Mixture datasets (guitar-in-context)
| Dataset | Owner | Guitar stem | Real/Synth | Scale | License | Clean? | Rating |
|---|---|---|---|---|---|---|---|
| **MoisesDB** | Moises/Music.AI | ✅ acoustic/clean/distorted sub-stems | real | 240 songs, ~14.5h, 12 genres | CC-BY-NC-SA (commercial **negotiable**) | L2 only | **Excellent** (finetune/validate) |
| **MedleyDB v1+2.0** | NYU MARL | ✅ ac/clean/distorted labels | real | 196 songs, ~14h | CC-BY-NC-SA | ✗ (NC) | Good (R&D) |
| **Slakh2100** | Northwestern IAL | ✅ + aligned MIDI | **synthetic** | 2100 tracks, ~145h | **CC-BY-4.0** | ✅ | **Good** (pretrain only) |
| **MUSDB18-HQ** | SigSep | ❌ (in "other") | real | 150 songs | CC-BY-NC-SA / academic | ✗ | Good (benchmark comparability) |
| **DSD100** | SiSEC | ❌ | real | 100 | deferred/unclear | ✗ | Skip (superseded) |
| **ACMID** (2025) | community | ✅ separate ac + elec | real (YouTube-crawl) | 737h retained | **none — scrape-your-own** | ✗ (copyright risk) | Limited (legal risk) |

### 1b. Guitar-specific isolated (stem sources / regime coverage — no mixture)
| Dataset | Guitar type | Scale | License | Clean? | Rating / best use |
|---|---|---|---|---|---|
| **GuitarSet** | acoustic, per-string | 360 clips ~3h, 6 players | **CC-BY-4.0 / MIT code** | ✅ | **Excellent** — clean acoustic stem source + benchmark |
| **Guitar-TECHS** (2025) | electric, DI+amp+multi-mic+MIDI | 3 players, ~4GB public | **CC-BY-4.0** | ✅ | **Excellent** — clean electric DI, finetune/transcription |
| **EGFxSet** | electric notes, 12 real HW FX | ~12.5h, 8,970 files | **CC-BY-4.0** | ✅ | Good — real distortion **timbre bank** (aug) |
| **GuitarDuets** (2024) | classical, guitar-vs-guitar stems | ~3h | CC-BY-4.0 *(verify variant)* | ✅? | **Good/Excellent** — hardest same-timbre case |
| **EGDB** | electric DI + 6 amp renders | ~2h→~12h | **unknown — contact author** | ? | Good (distortion) — clear license first |
| **GOAT** (2025) | electric DI + tab, amp-aug | 5.9h→29.5h | **gated / TBD** | ? | Good (largest DI) — confirm access terms |
| **GAPS** | classical (audio NOT bundled) | ~14h, 200+ performers | CC-BY-NC-SA + uncleared audio | ✗ | Good acoustic benchmark (R&D) |
| **GuitarDuets/IDMT-SMT-Guitar** | ac+elec notes/techniques | small | **CC-BY-NC-ND** (worst) | ✗ | Research-only (ND likely bars trained models) |
| **NSynth** (guitar) | notes, 16kHz | 305k notes | CC-BY-4.0 | ✅ | Limited (16kHz aug) |

### 1c. Regime-coverage engines (not stems — augmentation infrastructure)
| Source | What it gives | License | Rating |
|---|---|---|---|
| **NAM + TONE3000 / Tonocracy** | thousands of DI↔amp captures + IRs (open-source NAM) | per-capture varies; NAM software open | **Excellent** distortion engine (verify per file) |
| **GuitarML (Proteus etc.)** | reamp tooling + a few captures | GPL/MIT | Good (method) |
| **IR cab libraries** (OwnHammer, York, free packs) | cab/mic convolution diversity | commercial per-license; prefer free packs | Good (cab regime) |
| **IK TONEX/ToneNET, PositiveGrid ToneCloud** | huge tone libs, **proprietary/locked** | restrictive ToS | Poor (not portable/redistributable) |

### 1d. Symbolic / auxiliary (indirect or non-guitar)
DadaGP (26k tabs, copyright-encumbered, no audio) · SynthTab (render pipeline blueprint) · SCORE-SET · MUSAN (**CC-BY** noise/interference aug — clean) · Spheres (**CC-BY-SA** orchestral, leakage-free *method* worth copying, no guitar) · DALI/JAAH/WJazzD/MTG-Jamendo/MedleyVox/NUS-48E/FiloBass (vocal/bass/mixed-only or uncleared audio — marginal).

---

## 2. Commercial opportunity report

Purpose-built **AI-training-data licensors now exist** — a category that didn't a year ago. Ranked by fit:

| Vendor | What they sell | Guitar-stem granularity | Cost band | Fit |
|---|---|---|---|---|
| **SourceAudio** (+Musical AI) | 14M cleared tracks, ground-truth **and** AI stems, MIDI, metadata | **unconfirmed** — must demand ground-truth isolated guitar | ~$1.25M avg deal; **pilot slice est. $50–150k** | **Best** clean-rights + stems candidate |
| **GuitarSet commercial relicense** (NYU/MARL) | the cleanest isolated *acoustic* guitar audio | ✅ per-string isolated | **~low-$10k** | **Highest ROI / lowest risk** |
| **Datarade sellers** (Soundsnap et al.) | "50k tracks w/ stems, cleared for ML" | verify per seller (ground-truth vs AI-derived) | **~$10–100k** | Good — fast price discovery |
| **Rightsify / GCX** | 100%-owned catalog, 4.4M hrs | **unconfirmed stems** | $X0k slice → low-$m | Good *if* stems confirmed |
| **MoisesDB commercial** (Music.AI) | the best real guitar-stem taxonomy | ✅ ac/clean/distorted | inquiry (unpublished) | Good — clean recordings, negotiable |
| **MassiveMusic (bespoke)** | commissions isolated guitar to spec | ✅ built to order | project pricing | Good fallback (100% clean rights) |
| **Epidemic/Artlist/Universal PM** | single-owner catalogs w/ stems | mix-derived, guitar not guaranteed | mid-5 to 7 figures | Limited–Good (slow, cautious) |

**Partnership (non-obvious real-multitrack holders):** QMUL C4DM (Open Multitrack Testbed + GOAT — actively wants contributions), **Weathervane Music** (nonprofit, pro guitar stems, single negotiable owner), **Fraunhofer IDMT** (licenses data to industry beyond its public CC-NC-ND release), music-production schools (Berklee/SAE/McGill — huge unreleased archives, rights-gated/slow).

**Cost anchors (real, public):** SourceAudio 8 deals ≈ $10M/yr; Shutterstock AI-licensing $104M (2023); Meta MusicGen ≈ 20k hrs from Shutterstock+Pond5. Enterprise music-data deal band = **low-$100k → low-$millions**. Academic commercial relicense = **$10k–100k**. Sample-library buyout = **$50–300** (but usually no AI grant).

---

## 3. Licensing risk assessment

**Green (ship it):** Slakh2100, GuitarSet, Guitar-TECHS, EGFxSet, NSynth, MUSAN — CC-BY + clean recordings. Attribution required; avoid CC-BY-**SA** where output-copyleft ambiguity matters (prefer CC-BY / CC0 / MIT).

**Amber (verify before use):** GuitarDuets (confirm CC variant + real-recording provenance), EGDB (no published license — email author), GOAT (access-gated, license TBD). GuitarDuets/EGDB/GOAT are high-value electric/hard-case data blocked only by an unconfirmed clause — cheap to resolve by asking.

**Red (R&D/benchmark only — firewall from commercial weights):** MoisesDB, MedleyDB, MUSDB18 (CC-BY-NC-SA), IDMT-SMT-Guitar (CC-BY-NC-**ND** — ND arguably bars a trained model), GAPS. These stay in the provenance registry as `commercial_training_allowed: False`.

**Black (do not ingest):** Cambridge-MT/karaoke-version **bulk-scraping** (ToS + per-artist rights), Rock Band/Guitar Hero **stem rips** (infringing commercial masters), ACMID/DALI/GAPS **audio** (YouTube/commercial, uncleared), DadaGP (user-transcribed copyrighted tabs), AI-**separated** stems as training targets (AudioShake/Moises output — *circular*, caps ceiling — the #1 technical trap flagged independently by two streams).

**Sample/VST EULA trap:** Splice, Loopmasters, Cymatics, NI, UJAM are royalty-free *for making music* but **silent or prohibitive on AI training** (FluffyAudio/Westwood/Audiobro explicitly ban it). Any VST-rendered synthetic corpus must use **only EULA-affirmed-ML instruments**, or it inherits a poison pill. This is exactly the ghost-license failure the separator program already hit.

---

## 4. Recommended acquisition priority list

1. **NOW ($0):** ingest all green-tier CC-clean sets — GuitarSet, Guitar-TECHS, EGFxSet, Slakh2100, MUSAN (+ GuitarDuets pending variant check). Run every track through the **Dataset Auditor** before use. Register each in `training_data.py`.
2. **NOW ($0 eng):** stand up the **augmentation engine** — NAM reamping + IR convolution + stem-remix/pitch/RIR — on the clean DI stems (Guitar-TECHS, EGDB/GOAT once cleared). This is the distortion/electric multiplier.
3. **Cheap unblocks (email, ~days):** clear EGDB, GOAT, GuitarDuets licenses; open **GuitarSet commercial relicense** (~$10k) and **MoisesDB commercial** inquiry (Music.AI).
4. **Lean build ($7.5–15k, ~100 songs):** commission real clean-DI guitar via AirGigs/SoundBetter with an explicit **DI + perpetual AI-training-rights** contract; diversify players/genres/tunings; expand each DI ×5–10 via the augmentation engine.
5. **Targeted license ($50–150k, if budget):** SourceAudio ground-truth-**guitar**-stem pilot (or Datarade seller) — **only after confirming stems are ground-truth multitrack, not AI-separated**.
6. **R&D corpus (firewalled):** MedleyDB/MoisesDB-NC/MUSDB for benchmarking + regression, never in shipped weights.
7. **Future flywheel:** consent-gated opt-in capture from Riley app users (explicit "train AI" opt-in, per-contributor provenance, deletion/retrainability by design).

---

## 5. Estimated cost by acquisition strategy

| Strategy | Cost | What you get | Verdict |
|---|---|---|---|
| Pure synthetic (Slakh) | ~$0 | 145h, clean license | **Fails alone** (proven domain gap) |
| Green-tier CC real guitar | ~$0 | small, mostly no-mixture, clean | Necessary, insufficient |
| Augmentation engine (NAM/IR/remix) | ~$0 eng | ×5–10 tonal expansion of any DI | **Force-multiplier** |
| **Lean DI build** 100 / 500 / 1000 songs | **$7.5–15k / $37.5–75k / $75–150k** (+~25% overhead) | real owned isolated guitar, expandable | **Best cost/control/rights** |
| GuitarSet commercial relicense | ~$10k | clean isolated acoustic, shippable | High ROI |
| MoisesDB commercial | inquiry (est. $X0k) | best real guitar-stem taxonomy in mixtures | High value if priced sane |
| SourceAudio pilot | $50–150k | large cleared stems (verify guitar granularity) | If funded |
| Full-multitrack build 500–1000 | **$0.5M–$5M** | real in-mixture w/ bleed | Only a small targeted subset |
| Crowdsource | low $/sample, high setup | most diverse real, slow ramp | Later flywheel |

---

## 6. Gap analysis — what still can't be sourced externally

Even after exhausting external options, one thing has **no clean off-the-shelf source**:

> **Real, isolated guitar stems, inside genre-diverse full-band mixtures, at scale, commercially clean** — especially in Riley's two documented weak regimes: **guitar-masked-under-vocals** and **acoustic-in-dense-mix.**

- Licensable candidates (SourceAudio/Rightsify) have **unconfirmed guitar-stem granularity** and may only offer AI-separated stems (unusable).
- MoisesDB is the closest real match but small (~14h) and NC-until-negotiated.
- Every large real-mixture corpus buries guitar in "other."

This irreducible gap is **exactly** what the lean-build path fills: **real DI guitar mixed into real/CC backing** manufactures in-mixture, isolated-target examples in the precise regimes we lack — cheaply, cleanly, and owned. The gap does **not** justify full-scale studio production; it justifies targeted DI acquisition + smart mixing.

---

## 7. Recommendation — Hybrid (evidence-based)

**Not primarily license** (the ideal real-in-mixture guitar-stem product barely exists and guitar granularity is unconfirmed). **Not primarily build-from-scratch full multitracks** ($0.5–5M, slow, unjustified). 

**→ A layered hybrid, weighted toward a cheap owned real-DI core + augmentation, topped with one targeted license:**

```
Layer 0  Synthetic pretraining        Slakh2100 (CC-BY)              — cheap priors, EULA-clean
Layer 1  Clean real guitar (ship)     GuitarSet, Guitar-TECHS,       — safe-to-ship targets + benchmark
                                       EGFxSet, GuitarDuets
Layer 2  Real DI seed + AUGMENTATION   commissioned DI ($7.5–75k)    — the electric/distortion engine,
         (NAM reamp + IR + remix)      × NAM/IR/stem-remix              owned, closes synthetic→real gap
Layer 3  Licensed real multitracks     MoisesDB-commercial and/or    — real in-mixture diversity,
                                       SourceAudio pilot                the documented weak regimes
Layer 4  Crowdsourced flywheel (future) opt-in app users             — compounding diversity over time
─────────────────────────────────────────────────────────────────────
R&D-only (firewalled): MedleyDB, MoisesDB-NC, MUSDB18 — benchmark/regression, never in weights
```

**Why each layer:** 0 gives cheap general priors; 1 gives clean shippable real targets and the benchmark; **2 is the core** — it's the only affordable way to own real, in-mixture, regime-targeted guitar data and directly answers the synthetic→real failure this project already proved; 3 buys real-world mixture diversity we can't manufacture; 4 compounds later at near-zero marginal cost.

### 3–5 year compounding roadmap
- **Y1:** Layers 0–1 ingested + audited; augmentation engine live; **lean DI build to ~500 songs** ($40–75k); GuitarSet commercial relicense + MoisesDB/SourceAudio inquiries. → first *shippable* Riley trained on clean data.
- **Y2:** close **one** license deal (MoisesDB or SourceAudio guitar-stem slice) for real-mixture diversity; DI build → ~2,000; regime-targeted commissioning against the failure catalogue (masking/acoustic).
- **Y3:** launch **consent-gated crowdsource** from the app; begin a small, deliberate **full-multitrack** subset for true bleed/mix-context; partner with QMUL/Weathervane.
- **Y4–5:** crowdsource flywheel dominates volume; corpus is majority owned + clean; Riley's guitar corpus becomes a **proprietary moat** no competitor can download — the strategic asset, compounding.

**Bottom line:** external options are now exhausted and the evidence is clear — **no download-and-train path clears the commercial gate**, but a **hybrid centered on a cheap owned real-DI core + augmentation, plus one targeted license, plus synthetic pretraining** does. Build the *core*, license the *diversity*, synthesize the *priors*, and let a consent-gated flywheel compound it. Internal collection is justified **only** in the cheap-DI form — not full studio production — and only because the specific in-mixture regimes cannot be sourced any other way.
