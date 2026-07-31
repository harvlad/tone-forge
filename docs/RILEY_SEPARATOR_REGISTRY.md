# Riley Separator Portfolio — specialist registry

The goal is NOT one best separator. It is a portfolio of specialists with **different failure
modes**, routed by acoustic regime. Correlation with Stock is the enemy — a model earns a place
only if it **clearly beats Stock in ≥1 regime** in blind listening. Populated by the agile
sprint loop (install → Riley pipeline → blind A/B → characterize → keep/archive). Benchmark rank
is not the criterion; complementary strengths are.

Legend: license status for PRODUCTION use. Blind-A/B evidence noted per regime.

---

## KEEP — Stock HTDemucs (`htdemucs_6s`)
- **Architecture:** Demucs v4 (hybrid conv + cross-domain transformer). 6-stem incl. guitar.
- **License:** MIT code + MIT weights (Meta). **CLEAN for production.**
- **Install:** `demucs` pip / bundled; `demucs -n htdemucs_6s`.
- **Role:** DEFAULT / incumbent. Everything is measured against it.
- **Strengths (blind A/B):** layered/busy mixes (PSB 4/4 vs B1), clean guitar under vocal,
  compressed modern mixes, dense distorted. Consistent, safe.
- **Weaknesses:** guitar sinks/bleeds under prominent vocals in the DISTORTED regime (Lithium
  verses — where B1 alone beat it); **acoustic guitar is weak** (prism guitar RMS ~0.019,
  under-extracted); leaves guitar-active gaps in sparse mixes (tycho 28% active).
- **Best regimes:** default everywhere; strong on layered/dense/clean/compressed.
- **Failure modes:** vocal bleed on distorted verses; acoustic under-extraction; sparse-mix gaps.

## ARCHIVED — B1 (htdemucs_6s fine-tune, T2)
- **Architecture:** Demucs (fine-tune of htdemucs_6s). **Same architecture as Stock.**
- **License:** clean (MIT base + CC-BY Slakh) — but irrelevant, archived.
- **Why archived:** **too correlated with Stock** (it IS fine-tuned Stock). Sprint-4 proved a
  bad ensemble partner: routing = wash (P3), fusion = marginal on Lithium but **regresses** on
  clean/dense/layered (P4b: 3 equal / 3 stock-better / 0 win). Where it agrees with Stock =
  redundant; where it disagrees = usually worse.
- **Isolated strengths (T2/T2.1):** distorted rhythm guitar + male vocal ENTRY (Lithium verses,
  4/4 vs Stock). Synthetic F1 +0.072. But regime-specific and did NOT generalize (T2.2: 7 stock
  / 1 B1 / 2 equal across 4 songs); **fails acoustic** (prism near-silent, RMS 0.008).
- **Lesson:** a fine-tune of Stock cannot be Stock's ensemble partner. Need a DIFFERENT arch.

## COMPLEMENTARY (capability) / BLOCKED (license) — BS-Roformer-SW (`BS-Roformer-SW.ckpt`)
- **Architecture:** BS-RoFormer (band-split transformer). 6-stem incl. guitar. **Genuinely
  different from Demucs → makes different mistakes.**
- **License:** **UNLICENSED "legal ghost"** — no author/license/training-data provenance
  (Tier-2; jarredou only rehosted). **BLOCKED for production.** The blocker is licensing, NOT
  capability.
- **Install:** `pip install audio-separator`; `Separator().load_model("BS-Roformer-SW.ckpt")`.
  ~2 min/song via ONNX, even on CPU.
- **Blind A/B (Sprint 5, Lithium):** **SW 3 wins / 1 equal / 0 loss, ALL high confidence.**
  Beats Stock on the vocal-masked verses (Stock's weakness — the exact thing B1 could NOT fix)
  AND on the clean intro riff; ties only the loud chorus. THE opposite of B1.
- **Strengths:** distorted rhythm guitar, vocal-masked verses, clean electric riffs. Cleaner
  vocal-bleed rejection than Stock.
- **Weaknesses / failure modes:** **electric-biased** — routes ACOUSTIC guitar into the "other"
  stem (guitar stem was silent on prism). Not an acoustic specialist.
- **Key lesson:** stock↔SW guitar waveform correlation = 0.904 (as high as B1's) YET clearly
  audibly better → **numeric correlation ≠ perceptual complementarity; ears decide.** A metric
  would have wrongly archived SW.
- **Verdict:** proves the ensemble thesis (a decorrelated model clearly complements Stock).
  Cannot ship as-is. Next sprint = a CLEAN way to capture this advantage.

## WIRED but DID-NOT-GENERALIZE — AudioShake (`api:audioshake`, guitar model)
- **Architecture:** proprietary (AudioShake API). Different from Demucs → decorrelated by
  construction. Dedicated `guitar` / `guitar_electric` / `guitar_acoustic` models.
- **License:** CLEAN under AudioShake commercial API terms (`api_terms`) — production-legal,
  unlike SW (ghost)/becruily (non-commercial). Licensing was never the blocker; QUALITY is.
- **Access:** `x-api-key` on `api.audioshake.ai`; upload `/assets` → `POST /tasks` → poll
  `/tasks/{id}` → download `output[].link`. Wired live in `api_audioshake.py`. ~1 credit/min/stem
  (credit→USD plan-dependent). Provider stays available for on-demand use; NOT auto-selected.
- **Milestone 3 (exp_20260731_062148_8f65d0):** looked strong — AudioShake 7 / stock 1 / 0 equal
  on lithium (electric) + prism (acoustic), 8 windows.
- **Milestone 5 confirmation (exp_20260731_081712_90f2a7) — REVERSED IT.** Same blind harness, 10
  pre-registered windows on the T2.2 diverse set (tycho ambient, lornashore deathcore, psb
  synthpop — where B1 also collapsed): **stock 8 / AudioShake 1 / 1 equal, stock 4 high-conf vs
  AudioShake 0.** Combined M3+M5 = 8 AudioShake / 9 stock / 1 equal → **a wash.**
- **Failure mode:** in dense / synth-led / extreme-distortion mixes AudioShake OVER-extracts —
  its guitar stem is often LOUDER than stock (e.g. psb guitar_under_vocal 7071 vs 4895 RMS) yet
  judged worse: it pulls non-guitar content / bleed into the stem. Robust only on mainstream
  guitar-forward material (the M3 songs), which is not enough to route on.
- **Verdict:** exact same false-positive shape as B1 (great on 2 songs, lost on the diverse set).
  `confidence="unproven"`, `regimes_strong={}`. **Do NOT enable routing.** Kept wired as an
  on-demand provider only. Lesson reinforced: a 2-song blind win is NOT a regime win — the
  broader-confirmation gate is load-bearing and caught this before it shipped.

## CANDIDATE (clean path) — Sprint 6 target: HOLE-FILL + DE-PUMP (NOT de-bleed)
- **Corrected by Sprint 5.5:** SW's win is separation-hole-filling (87%) + de-pumping (33%),
  NOT vocal-bleed removal (6%). A de-bleed pipeline would target the wrong dimension and fail.
- **Clean mechanism to prototype:** where Stock's guitar stem DROPS OUT (low energy while the
  mix still has guitar-band energy), reconstruct the missing guitar from a clean decorrelated
  source (mix guitar-band content, or a clean model's continuous instrumental/other estimate),
  and smooth the guitar envelope to remove pumping. All production-clean (Demucs MIT + DSP, or
  Spleeter/Open-Unmix MIT). Copies SW's BEHAVIOUR (continuity + envelope stability) not its
  weights.
- **Status:** Sprint 6 prototype. Blind A/B the hole-filled+de-pumped guitar vs Stock on the
  verse windows where Stock has holes.

---

## Registry findings so far
- Guitar-stem separation is rare: only Stock (clean/Demucs) and SW (ghost/RoFormer) output guitar.
- No **clean + different-architecture + guitar-stem** model exists in the ecosystem (confirms
  Tier-2 dataset/licensing research).
- Therefore the clean multi-model path is likely **Stock + a decorrelated *clean vocal remover*
  used to de-bleed**, not a second guitar model. SW is the research ceiling-check for whether
  decorrelation helps at all.


---

## Sprint 5.5 — SW behavioural profile (WHY it wins)
Measured on the A/B-labeled Lithium windows (SW won verse_16/26 + intro; tied loud chorus):
- **Separation holes = the dominant factor (87%):** Stock guitar drops out in 24% of verse
  frames (disappears under vocals); SW 3%. **SW keeps the guitar continuous.**
- **Pumping (33%):** SW's guitar envelope tracks the vocal less → doesn't duck when the vocal
  enters.
- **Vocal bleed only 6%; tonal 5%; attack ~same; HF slightly worse (negligible).**
- **Causal proof:** the TIE window (loud chorus) has ZERO holes for both → SW wins exactly where
  stock has holes, ties where it doesn't.
- **Design consequence:** a de-bleed pipeline would target the 6% dimension and FAIL. The clean
  reproduction target is **hole-filling + de-pumping** (continuity + envelope stability), NOT
  vocal removal. Copy the behaviour, not the implementation.


## Sprint 6 — continuity reconstruction (DSP) = FAIL
Granular continuity-fill of stock guitar holes: blind A/B recon 0 win / 1 equal / 3 stock-better.
- Sanity PASS (loud chorus equal-H → only holes touched).
- Failure: granular-loop fill of distorted guitar = buzzy/periodic artifacts WORSE than the hole.
- **Learning:** SW fills holes with REAL recovered guitar (natural); DSP can only FABRICATE
  (artificial). The missing guitar is absorbed into the VOCALS stem — unrecoverable by
  interpolation. The 87%-hole advantage needs actual CONTENT RECOVERY = a clean decorrelated
  SEPARATOR, not post-hoc DSP repair. Cheap-repair path ARCHIVED.

---

## Acquisition survey (mid-2026) — how to obtain a clean decorrelated guitar separator
Research→acquisition. **No fully-clean, self-hostable, decorrelated guitar-stem model exists.**
Every open guitar-capable RoFormer fails on weights-license or data-provenance. Options, ranked:

**Commercial APIs (return a guitar stem under commercial terms) — the realistic clean unblock:**
- **AudioShake** — guitar stem; API + real-time SDK + **on-prem enterprise** deploy (a licensed
  "self-host" middle ground); already powers third-party products (LANDR Stems, djay Pro) so
  product-embedding is a proven path. **Price UNKNOWN (enterprise/custom, contact
  info@audioshake.ai).** Data-provenance not public → pin in contract. Strongest rights pedigree.
- **Music.AI / Moises** — explicit guitar stems (acoustic/electric) & parts (rhythm/solo).
  **~$0.10/min ($0.095 Pro)**, published rate. Marketed as licensed-data B2B API. Turnkey.
- **LALAL.ai** — guitar stems, ~$0.15/stem/min; **product-embedding rights UNVERIFIED** — check ToS.

**Private license (self-host, needs negotiation):**
- **becruily MelBand-RoFormer Guitar** — architecturally ideal (RoFormer, decorrelated, purpose-built
  guitar). MIT code, but **weights non-commercial by default** ("DM on Discord" for other terms;
  no published price; training data undisclosed). Only self-hostable decorrelated guitar model,
  but not clean without a signed private license + data warranty.

**No path:** BS-RoFormer-SW (ByteDance never released official weights; community mirrors
unlicensed; no contact/licensing channel). Do not ship self-hosted.

**Train own (last resort):** no 2025-26 academic release pairs a general guitar separator with
clean data + permissive weights; would require the Tier-2 clean-data plan.

**Recommendation:** pursue an **API provider (AudioShake or Music.AI) as the first production
provider** — plugs straight into the SeparatorProvider interface (`api:audioshake`/`api:moises`),
blind-A/B-gated per regime on quality × cost. AudioShake's on-prem SDK is the best "licensed but
self-hosted" fit. Parallel: probe becruily for a private commercial license (self-host path).
Sources: music.ai/pricing, audioshake.ai, lalal.ai, HF becruily discussion #9.
