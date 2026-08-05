# Tier-2 Specialist Separator R&D Proposal

Status: **PROPOSAL ONLY — nothing trained, rented, provisioned, or contacted.**
Date: 2026-07-28. Author: research pipeline (Waves 1–3 + real-audio phase + three fresh research sweeps; sources cited inline or in the referenced agent reports in session records).
Companion evidence: `backend/lab_data/reports/discovery_2026-07/{wave2,wave3}_report.md`, experiment registry entries `wave3_separator_downstream`, `cross_stem_reconciliation_falsification`.

---

## 1. Executive recommendation

**Option C — both routes in parallel, weighted toward build:**
(a) send two zero-cost licensing probes (becruily Discord DM; MVSep/ZFTurbo email) — days of latency, no dollars, real option value;
(b) run the T0→T1 own-model experiment (≤ **$3** total) proving the fine-tune recipe moves **downstream** Riley/Kong metrics on CC-BY data.
Do **not** pursue SW-checkpoint licensing as a primary path: the checkpoint has **no known author, no license, no training-data provenance** — there is no counterparty to license from.

## 2. Evidence: separation is the bottleneck

Three independent measurements converge:
- **Wave-2 separation tax** (htdemucs_6s, solo-family stems): Guitar Riley F1 .602→.371; Piano Kong .430→.231 (specialist advantage neutralized); Bass .616→.513 (survives). Failure modes: bleed (guitar precision .648→.338; kong false-notes ×2.3, octave doubling).
- **Wave-3 SW result**: a better separator recovers ~half the tax (Guitar .371→.465, Piano .231→.348, bleed −35%) — the quality headroom **exists**.
- **Real audio (Lithium)**: vocal activity shifts guitar-transcription register +5 semitones; 34% of guitar events double concurrent vocal pitch-class (bass 26%); contamination *replaces* guitar content.

## 3. Why reconciliation was rejected

Per-note ownership on 10,612 separated-guitar predictions: 38% true / 11% attributable leaks / **50% unattributable garbage**; 33.5% of legitimate guitar GT is pitch-class-doubled by co-instruments; register deviation carries zero per-note signal. Deletion rules damaged F1 (.369→.333/.303); the **perfect-leak-deletion oracle ceiling is +2.4pp F1 with recall unchanged**. Post-transcription filtering cannot recover replaced content. Recovery must happen upstream.

## 4. Current baseline (htdemucs_6s)
MIT code + MIT weights (Meta) — the only fully-clean chain in the ecosystem. Known weak piano ("not working great" per its own README), guitar OK-ish with bleed. Production-safe, quality-limited.

## 5. SW experimental reference
Community BS-RoFormer 6-stem, MVSep leaderboard guitar SDR 9.05 / piano 7.83; our measured downstream gains above. **Legally a ghost** (see §6–7). Retained as an evaluation reference only, never production.

## 6. Route A — licensing existing separators

| Target | Status | Path |
|---|---|---|
| **SW checkpoint** | No author, no license, no data statement. jarredou (rehoster) disclaims involvement; HF account deleted; downstream "MIT" labels cover conversion code only | Trail: Audio Separation Discord + ZFTurbo (who serves it). Low probability |
| **becruily MelBand guitar** (45MB, best dedicated guitar model) | Author states: *"free to use as long as it's non commercial. If you need something else you can DM me on discord"* — **explicit commercial-negotiation invitation** | Discord DM `becruily`. No public pricing precedent |
| **MVSep paid API** | Terms: outputs commercially usable; no weight licensing program | turbo@mvsep.com (Roman Solovyev/ZFTurbo). Per-track API = dependency + latency + cost/track, but zero legal ambiguity on outputs; also the person most likely to know who SW is |
| Banquet, anvuew, etc. | NC data / GPL / rehosts | Not viable |

## 7. Exact licensing unknowns
SW: everything (author, weights license, training data). becruily: price, derivative rights, training data used (if NC data → taint transfers to us even with a license). MVSep API: whether the SW-quality models can be pinned + SLA. All marked UNKNOWN pending contact.

## 8. Outreach targets & draft questions (NOT SENT — approval required)
**becruily (Discord DM):** Are your guitar (and any piano) checkpoints available for commercial licensing? Price for (a) inference use, (b) fine-tune base with derivative rights? What training data (licensing status)? Attribution requirements?
**MVSep (turbo@mvsep.com):** Do you license model weights commercially or only API access? Can the API pin a model version? Volume pricing per track? Do you know the SW 6-stem author and whether commercial licensing is possible?

## 9–11. Route B — own model: architectures

| Option | Chain | Assessment |
|---|---|---|
| **B1: fine-tune htdemucs_6s** (guitar/piano heads) on clean data | MIT code + **MIT weights (Meta)** + our CC-BY data — cleanest possible | Arch older; ceiling below RoFormers, but any tax reduction is pure win; cheapest defensible chain |
| **B2: train small Mel-Band-RoFormer single-target (guitar) from scratch** on clean data | MIT trainer (ZFTurbo) + our data; weights 100% ours | 45MB-class proven sufficient (becruily); modern arch, matches SW family that demonstrated the headroom; no pretrained base needed at this size |
| B3: fine-tune community roformer checkpoints | Weights/data provenance unclean across the board (Kim/ZFTurbo ckpts = MUSDB-trained, unstated licenses) | Rejected for production; acceptable only as private recipe-calibration reference |
| Bandit/SCNet/etc. | SCNet MIT but MUSDB-trained VDBO; others NC/irrelevant | Rejected |

**Proposal: run B1 and B2 through the ladder; keep whichever moves downstream metrics more per dollar.** Single-target (one model per instrument) over 6-stem: smaller, cheaper, and §12's routing logic applies.

## 12. Specialist separation tree
Yes — architecture should permit `family → specialist separator → specialist transcriber`. Evidence rhymes with transcription: class-specialists beat generalists (becruily guitar > htdemucs guitar; SW piano ≫ htdemucs piano). The existing specialist registry/router seam extends naturally (a `separators` table already exists in `specialist_registry.json`). Bass stays htdemucs (survives fine). No implementation now.

## 13–14. Datasets (full matrix in agent report; decisive facts)

| Dataset | Guitar/Piano stems | Real? | License | Commercial |
|---|---|---|---|---|
| MoisesDB | yes (incl. distorted/clean, grand/EP) | real | CC-BY-NC-SA | **NO** |
| MedleyDB 1/2 | yes | real | CC-BY-NC-SA | **NO** |
| MUSDB18-HQ | no (folded into "other") | real | educational-only | **NO** |
| Cambridge-MT (~500 multitracks) | yes | real | educational; **per-contributor commercial negotiation invited** | negotiable |
| RawStems (2025) | yes | real | inherits Cambridge-MT terms | **NO** |
| **Slakh2100** | yes | synthetic | **CC-BY** (Kontakt-render asterisk) | yes* |
| **AAM** (3,000 tracks) | yes | synthetic | **CC-BY** | yes* |
| **GuitarSet** | solo guitar | **real** | **CC-BY** | yes |
| **GuitarDuets** (2025) | two-guitar | real+synth | CC-BY (verify Zenodo field) | yes — only same-instance GT anywhere |
| StemGMD (drums) | — | synthetic | CC-BY | yes |
| MAESTRO (piano) | solo | real | CC-BY-NC-SA | NO |
| **Paid**: SourceAudio/Musical AI (14M cleared tracks, stems subset), Rightsify GCX | yes | real | negotiated AI-training license | yes ($) |

**Bottom line: no free permissive real-recording guitar/piano stem corpus exists.** Clean production paths: (1) paid corpus license, (2) Cambridge-MT contributor outreach (~100 best multitracks), (3) **synthetic backbone self-rendered with EULA-clean instruments** (FluidR3/GeneralUser MIT soundfonts, Salamander piano CC-BY, VSCO2 CC0 — NOT Kontakt/Spitfire, whose EULAs now prohibit ML training) + small commissioned real set (10–30 work-for-hire band recordings = modest budget, clean forever).
**Real-vs-synthetic risk:** synthetic-only training reproduces our synthetic→real transfer uncertainty. Mitigation: mix-on-the-fly with real solo corpora (GuitarSet + commissioned), aggressive production-realism augmentation (§19), and real-audio validation gates (§24). NC datasets: usable internally for benchmarking only, firewalled from any shipped weights.

## 15–18. Training strategies per instrument
- **Guitar**: target stem = guitar; interference mixes MUST include vocals (the measured killer regime — synthesize vocal interference from any clean vocal source or DDSP-singing since Slakh lacks vocals), keys, bass spectral overlap; regimes: clean/distorted/acoustic/double-tracked/power-chords (Slakh patch metadata + self-rendered distortion chains give labeled coverage). Note: riley_guitar's *transcription* weakness on distortion is a separate problem — do not attribute transcription failures to the separator in evaluation (clean-stem control isolates this).
- **Piano**: piano vs {vocals, guitar, synth, organ} interference; sustain-pedal-heavy passages; evaluate whether piano and organ/synth/EP need separate targets (Slakh subfamily labels make this measurable before any training).
- **Vocal interference**: primary augmentation axis for both targets.
- **Distorted guitar**: separator-side = train with distorted renders; transcription-side = separately queued (riley fine-tune / regime routing) — NOT conflated here.

## 19. Augmentation (priority-ordered to observed failures)
Vocal bleed injection → mastering compression/limiting → reverb/room → distortion/amp-sim chains → stereo width/double-tracking → EQ overlap → transcode artifacts (YouTube-grade AAC roundtrip — matches our actual input path). ZFTurbo trainer ships an augmentation framework (`docs/augmentations.md`); most of this is config, not code.

## 20. Loss/objective
Stage 1: standard masked-spectrogram/waveform L1 losses (trainer default). Stage 2 (evaluation, not backprop): downstream Riley/Kong metrics as the *selection* criterion between checkpoints. Future (flagged, not proposed): differentiable downstream loss via frozen specialist — technically plausible (both torch), engineering-heavy; only if Stage-2 selection proves the signal matters.

## 21–23. Evaluation design
Existing Lab machinery is already exactly this: candidate separator → derived dataset (`slakh_sep_*` pattern) → cached specialist transcription → validated matcher → **separation-tax scorecard**:

| | CLEAN | HTDEMUCS | SW (ref) | CANDIDATE |
|---|---|---|---|---|
| Guitar Riley F1 | .602 | .371 | .465 | ? |
| Piano Kong F1 | .430 | .231 | .348 | ? |
| Bass Riley F1 | .616 | .513 | — | (control: must not regress) |
| % clean recovered (guitar) | 100% | 62% | 77% | target ≥ SW |

Downstream metrics per candidate: recall/precision/F1/octave/false-notes-per-sec/onset error/PRL. Slakh protocol: same 15 solo-family stems (frozen) + scout-40 for finalists. All cached, all resumable.

## 24. Real-audio protocol (design only — corpus NOT built)
10–20 songs spanning clean/distorted guitar, guitar+vocals, piano-forward, dense/sparse, old/modern production. Per song: htdemucs vs candidate through the SAME specialist → Derived Audio blind pairwise (BETTER/SAME/WORSE + tags) via the existing feedback endpoint; provenance already distinguishes variants. Question asked: "which musical representation is more correct," never "which sounds nicer."

## 25. User-supplied-stem control
Retained as permanent upper bound (+24–86% relative F1 vs separated). Every eval reports the candidate's position between htdemucs and clean-stem.

## 26–29. GPU economics (verified July 2026)
Sweet spots: **RTX 4090 community ~$0.30–0.34/hr** (T0–T2); **L40S 48GB $0.79–0.99/hr** (runs stock 48GB configs unmodified — T3+); A100 80GB $1.19–1.49/hr. Per-second billing (RunPod/Vast), free egress (RunPod/Lambda). Key anchor: **fine-tunes converge <15k steps** (Mel-RoFormer follow-up paper) vs 50k+ from scratch; community full-size training precedent = days-not-weeks on consumer cards. Inference: ~$0.006/track on 4090-class — negligible; per-song cost is dominated by our existing pipeline, not the separator.

## 30. Staged ladder (estimates from verified pricing; every stage answers a question)

| Stage | Question | Hardware | Wall | Max cost |
|---|---|---|---|---|
| **T0** pipeline verification | loader/loss/ckpt/eval runs end-to-end? | 4090 community | <1h | **<$1** |
| **T1** overfit sanity | can B1/B2 learn 5 tracks at all? | 4090 | 1–3h | **<$2** |
| **T2** mini fine-tune | do downstream Riley/Kong metrics MOVE on sep15? | 4090 | 4–14h | **$5–10** |
| **T3** serious run | can we beat SW's downstream numbers? | L40S | 1–3d | **$25–70** |
| T4 production candidate | separate proposal after T3 evidence | — | — | — |

## 31. Stop/falsification gates (pre-registered)
- T2: candidate fails to beat htdemucs downstream on sep15 → STOP, revise recipe before any T3 spend.
- Any stage: SDR up but downstream F1 flat → STOP (wrong objective — investigate).
- Guitar up, Piano down → switch to per-instrument specialists (§12), not universal replacement.
- T3 gains ≪ SW's demonstrated gains → diagnose dataset/arch gap before more spend.
- Licensing probe returns a viable becruily/MVSep deal cheaper than remaining ladder+time → re-run build-vs-buy with real numbers.
- Sunk cost never justifies the next stage.

## 32–33. Checkpoints & Lab integration
Trainer checkpoints every N steps to persistent volume; download+verify before terminate (existing remote-job bundle discipline). Each candidate checkpoint = a separator entry with config hash → derived dataset via the existing `slakh_sep_*` flow → cached predictions → scorecard. Zero new infrastructure.

## 34–36. Production economics & on-device
45MB-class single-target model: server CPU feasible, Apple Silicon comfortably (becruily runs ~minutes/track on M-series; MLX ports show 2.5× further); Core ML port **plausible** (attention-based, static shapes — flag: 700MB-class = difficult, 45MB-class = realistic ANE candidate). Per-song server inference: cents at worst on rented GPU, near-zero marginal on the existing worker Mac.

## 37. Build-vs-buy matrix

| | Quality | Time | Cash | License risk | IP | Speed to product |
|---|---|---|---|---|---|---|
| A: stay htdemucs | known-poor piano | 0 | 0 | none | none | now (status quo) |
| B: license SW | proven | ? | ? | **currently impossible** (no counterparty) | none | blocked |
| B′: license becruily | likely good (guitar) | days–weeks | unknown ($500–5k plausible range) | medium (his training data unknown) | none; dependency | fast if cheap |
| C: fine-tune/train own (B1/B2) | headroom proven by SW | ~2–4 focused days through T3 | **≤$83 through T3** | low (chain by construction) | **weights + recipe + data pipeline** | weeks |
| D: from-scratch large | highest ceiling | weeks+ | $1–3k+ | low | max | slow — rejected now |
| E: hybrid specialists | best per-class | incremental | as C | as C | max | natural end-state |

**License-economics crossover:** becruily at ≤$1–2.5k with derivative rights + clean-data warranty beats building (my time > GPU dollars); above ~$5k or without data warranty, building wins — our T2 total cost is ~$10 and produces owned IP either way.

## 38. IP/know-how
Ownable: fine-tuned weights, dataset-curation + rendering pipeline, augmentation recipe, separator-routing table, human preference corpus. Know-how (moat-grade, already accumulated): downstream-first separator evaluation, separation-tax methodology, register normalization, falsification harness. Open-source deps: trainer (MIT), htdemucs base (MIT). Due-diligence story: "boring, defensible chain" achieved by construction under B1/B2.

## 39–41. Risks
License: Slakh's Kontakt-render asterisk (mitigate: self-rendered EULA-clean backbone for production weights); becruily's unknown training data. Technical: synthetic→real transfer (the big one — mitigated by augmentation + real-audio gates + commissioned set); single-founder bandwidth (mitigated by ladder's early stops). Dataset: vocal interference must be synthesized (Slakh has no vocals) — quality of that synthesis is untested.

## 42. Abandonment evidence
Would kill this track: T2/T3 candidates can't approach SW downstream numbers on matched data; OR real-audio validation shows synthetic-trained separators don't transfer regardless of augmentation; OR licensing lands SW-class quality with clean chain below crossover price; OR product evidence shows user-stem workflows dominate usage (separation becomes secondary).

## 43–45. First experiment
**T0+T1 combined session** (one rented 4090, one sitting): verify ZFTurbo trainer end-to-end on our data (B2 small mel-roformer guitar-target, Slakh-derived mixes, ~200 steps), then 5-track overfit for both B1 and B2. Artifacts: checkpoints + loss curves + one separated stem run through Riley via the Lab. **Max spend: $3.** PASS = losses converge, checkpoint loads, separated output transcribes without pathology → T2 decision unlocked with a real throughput number to price it. FAIL = pipeline/recipe defects identified for $3 instead of $80.

---

# FOUNDER DECISION GATE

**A — LICENSE/NEGOTIATE** (send becruily DM + MVSep email, wait before building)
**B — RUN T0/T1 OWN-MODEL EXPERIMENT** (≤$3, CC-BY data, recipe proof)
**C — BOTH IN PARALLEL** ← **RECOMMENDED** (probes cost $0 and days of latency; T0/T1 costs $3 and retires the recipe uncertainty regardless of what the probes return; the two answers together make the T2/T3 and buy-vs-build decisions with real numbers)
**D — STAY WITH HTDEMUCS** (defensible hold: bass carries the current demo; guitar/piano stay capped)
**E — OTHER**

## NEXT COMMAND IF APPROVED (Option C)
1. You send (or approve me drafting final text for) the two probes — nothing sent without your explicit go.
2. T0/T1: prepare job bundle locally (dataset manifest from Slakh CC-BY subset + trainer config for B1 htdemucs-FT and B2 small mel-roformer); **rent one RTX 4090 community instance (RunPod/Vast, ~$0.34/hr)**; expected duration ≤4h wall including setup; **hard cap $3** (per-second billing, terminate on completion); artifacts: 2 checkpoints, loss curves, 1 separated-stem→Riley Lab evaluation, measured steps/sec (prices T2 exactly).
3. PASS/FAIL as §43. Either way: report + registry entry + your call on T2.

**No GPU spend — even $0.10 — occurs without your explicit approval of step 2.**
