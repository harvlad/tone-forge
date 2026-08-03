# Riley Data Factory — Architecture (Phase 2A)

The outcome of Phase 2A is **not a dataset**. It is the permanent pipeline that creates, curates, audits, augments, and continuously grows Riley's proprietary corpus. Every future asset — synthetic, licensed, commissioned, user-contributed — flows through it. The factory, not any dataset, is the moat.

**Design mandate:** reuse and extend existing Riley tooling; build new only where nothing exists. Architecture quality and maintainability over speed. This document is design + gap analysis + roadmap; implementation follows the roadmap in §12.

---

## 1. The atomic unit — the `Asset`

Everything in the factory is an **Asset**: one audio file + immutable provenance + metadata + audit verdict. Stages are pure transforms `Asset → Asset(s)` that **append** to lineage and never mutate the parent. The original is always recoverable.

```
Asset
  asset_id            content hash (sha256 of audio) — identity
  path                storage location
  kind                STEM | MIXTURE | DI | IR | CAPTURE(.nam) | BACKING
  role                guitar | vocals | drums | bass | keys | other | mix
  source_id           which SourceProvider admitted it (→ license via registry)
  license             pulled from training_data registry (single source of truth)
  lineage             ordered [(stage, transform, params, parent_asset_id)]  ← provenance
  metadata            the rich tag block (§5)
  audit               TrackAudit verdict + metrics (§4)  — None until audited
  admitted            bool  — only True after License+Audit+Quality all pass
```

**Provenance rule (load-bearing):** an augmented/generated Asset stores its full transform chain back to the real root recording. `lineage[0].parent_asset_id` chains to the clean DI. This makes "exactly how was this training example produced?" answerable forever — the same discipline `checkpoint_provenance` already applies to weights, pushed down to every audio file.

---

## 2. Architecture — the pipeline

```
                         ┌───────────────────────── COMMISSIONING (§9) ◀── BENCHMARK FEEDBACK (§8)
                         │   building-block specs                              regime failures →
                         ▼                                                     coverage requests
   SOURCES ──▶ [1] INGESTION ──▶ [2] LICENSE ──▶ [3] AUDITOR ──▶ [4] METADATA ──▶ [5] QUALITY
   Slakh                SourceProvider   VERIFY        admit/reject   enrich          SCORE
   GuitarSet            → RawAsset       (registry     (dataset_        tags          threshold
   Guitar-TECHS                          gate)          auditor)                        │
   EGFxSet                                                                              │ admitted stems
   licensed                                                                             ▼
   commissioned DI                                              [6] AUGMENTATION ENGINE (§6)
   user-contributed (future)                                    1 DI → N tonal variants
   sample libs (if EULA)                                        (NAM · IR · EQ · comp · verb · mic · room)
                                                                        │  (lineage preserved)
                                                                        ▼
                                                                [7] MIX GENERATOR (§7)
                                                                stems → mixtures, configurable masking
                                                                (ground truth known by construction)
                                                                        │
                                                                        ▼
                                                                [8] VALIDATION  (re-audit generated mixes —
                                                                    no unaudited data ever reaches GPU)
                                                                        │
                                                                        ▼
                                    [9] DATASET REGISTRY / ASSET CATALOG ◀──▶ COVERAGE ANALYSIS (§7-cov)
                                        every admitted asset + lineage        musical-space coverage map,
                                        + coverage index                      weak-cell detection
                                                                        │
                                                                        ▼
                                    [10] TRAINING MANIFEST  (build_manifest — license-gated selection,
                                         intended_use = commercial_eligible | research_only)
                                                                        │
                                                                        ▼
                                    [11] GPU TRAINING  (productized provision→train→checkpoint→ship,
                                         checkpoint_provenance embeds the manifest lineage)
                                                                        │
                                                                        ▼
                                    [12] BENCHMARK  ──▶ regime scorecard ──▶ (loops back to §8)
```

**Stage responsibilities (one line each):**

| # | Stage | Responsibility | Rejects on |
|---|---|---|---|
| 1 | Ingestion | normalize any source into `RawAsset` via one abstraction | unreadable/format |
| 2 | License Verification | stamp license from registry; block unregistered | unregistered / NC-in-commercial |
| 3 | Auditor | quality gate + metric computation | silent/clipped/leaky/dup/mislabelled |
| 4 | Metadata | attach the rich regime tag block | — (enrich) |
| 5 | Quality Scoring | composite score + admit threshold | quality_score < policy |
| 6 | Augmentation | one clean DI → many realistic variants, lineage-tracked | — (expand) |
| 7 | Mix Generator | stems → mixtures at targeted masking difficulty | — (generate) |
| 8 | Validation | re-audit generated mixtures | generated mix fails audit |
| 9 | Dataset Registry / Catalog | durable catalog of admitted assets + coverage index | — |
| 10 | Training Manifest | license-gated selection into a manifest | commercial gate (existing) |
| 11 | GPU Training | provision → train → checkpoint (provenance-stamped) | — |
| 12 | Benchmark | regime scorecard, drives §8 feedback | — |

---

## 3. Ingestion — one abstraction for every source (Phase 2)

Mirror the proven `ModelAdapter` / `SeparatorProvider` Protocol pattern already in the codebase. A new `SourceProvider` Protocol; one implementation per source type. Downstream stages never know which source an asset came from.

```python
# backend/lab/factory/sources.py  (NEW)
@runtime_checkable
class SourceProvider(Protocol):
    id: str                       # "slakh2100" | "guitarset" | "commission:2026Q1" | "user:<id>"
    dataset_key: str              # → training_data registry (license single-source-of-truth)
    def capabilities(self) -> SourceCapabilities: ...   # kinds/roles offered, real/synth, has_mixture
    def iter_assets(self) -> Iterable[RawAsset]: ...     # lazy; (audio_path, kind, role, source_tags)
    def health(self) -> bool: ...                        # is the source reachable/present
```

Concrete providers (all identical interface): `SlakhSource`, `GuitarSetSource`, `GuitarTechsSource`, `EGFxSetSource`, `LicensedDatasetSource` (generic dir + manifest), `CommissionedDISource`, `UserContribSource` (future, consent-gated), `SampleLibSource` (EULA-gated). Adding a source = one class, zero downstream change.

---

## 4. Dataset Auditor as a first-class stage (Phase 3)

**Exists** (`backend/lab/dataset_auditor.py`, built this phase; validated: catches near-silent, mislabelled-instrument, leakage, duplicates). Promote from library to pipeline stage:
- add a batch driver `audit_source(provider) → [TrackAudit]` + admit/reject routing;
- persist verdicts to the catalog (§9);
- **hard rule: no asset without `audit.verdict == PASS` may enter a manifest.** Enforced at the manifest boundary, not by convention.

No refactor of the metric code needed; it already computes occupancy/RMS/clipping/leakage/dups/silence/label-sanity/rolloff/dynamic-range/quality. Add only the batch+persistence wrapper and a REVIEW-triage queue.

---

## 5. Metadata (Phase 4)

**Exists** inside the auditor's `TrackAudit` (the 14 regime tags: genre, guitar_type, gain, pickup, tempo, key, vocal_density, guitar_occupancy, masking_score, difficulty, quality_score, recording_type, synthetic/real, license). Extend with:
- `augmentation_history` — the lineage chain (from the Asset model), so every variant records the amp/IR/EQ it came from;
- `recording_source` — provider id + commission-spec id;
- source-provided ground-truth tags (a commission knows the true pickup/tuning/technique → overrides audio proxies, clears the `estimated` flag).

Provenance-preserving by construction: augmentation appends, never overwrites; the clean root's metadata is immutable.

---

## 6. Augmentation Engine (Phase 5) — **build new**

Modular transforms, each `Asset(STEM/DI) → Asset(STEM)` with lineage. One clean DI → hundreds of realistic supervised variants. This is the force-multiplier that makes a cheap DI seed corpus large.

```python
# backend/lab/factory/augment.py  (NEW)
class Augmentation(Protocol):
    id: str
    def apply(self, asset: Asset, rng_seed: int) -> Asset: ...   # appends lineage
```
Modules (ordered as a signal chain): `Reamp(nam_capture)` (NAM amp models) → `CabIR(ir_file)` (impulse convolution) → `PickupEQ` → `GainStage` → `Compressor` → `Reverb`/`Delay` → `MicPosition` → `RoomAmbience` → `NoiseFloor`. Each is deterministic given (asset, seed) → reproducible. A `Recipe` = an ordered list of modules with param ranges; sampling a recipe over a DI yields N variants, each fully traceable and each pointing back to the recoverable original.

License note baked in: NAM captures + IRs carry per-file license — the engine records each capture/IR's provenance and refuses any flagged non-commercial when building a `commercial_eligible` manifest (same gate philosophy as the dataset registry).

---

## 7. Mix Generator + Coverage (Phases 6, 7)

### Mix Generator — **build new**
```python
# backend/lab/factory/mixgen.py  (NEW)
def generate_mix(guitar: Asset, backing: dict[role, Asset], *, masking: MaskingProfile,
                 seed: int) -> Asset:   # kind=MIXTURE, ground-truth stems referenced in lineage
```
Procedural, **targeted supervision not random mixing**. `MaskingProfile` presets map to Riley's real weak regimes: `light_vocal_mask`, `heavy_vocal_mask`, `dense_synth_mask`, `multi_guitar`, `busy_metal`, `sparse_acoustic`. The profile controls relative stem levels, spectral overlap, and count — so we can *manufacture the exact difficulty the benchmark says we fail*. Ground truth is perfect by construction (we summed known stems). Generated mixtures are re-audited (§8) before admission.

### Coverage Analysis — **build new**
```python
# backend/lab/factory/coverage.py  (NEW)
```
Measure **coverage of musical space, not song count.** A coverage vector over dimensions {guitar_type × technique × tuning × genre × masking_regime × tempo_bucket × key}. The catalog (§9) is grouped into cells; each cell shows admitted-asset count → an ASCII coverage map with weak cells flagged:
```
Acoustic fingerstyle █████████   High-gain rhythm ██████   Drop tuning ███
Jazz comping ██   Slide █   Female-vocal masking ██   Heavy-synth masking ████
```
Weak cells become the input to commissioning (§9) and the benchmark feedback loop (§8).

---

## 8. Benchmark Feedback Loop (Phase 8) — **wire existing + new glue**

Benchmark scripts **exist** (`scripts/run_stem_benchmark.py` etc.). New glue: after each benchmark run, map per-regime failures onto coverage cells and emit a structured **CoverageRequest**:
```
Need: 25 clean Strat performances · female-vocal masking · 90–110 BPM · pop · medium compression
```
The benchmark thereby *drives acquisition*. A CoverageRequest routes either to the Mix Generator (if we hold the raw stems to synthesize the regime) or to Commissioning (if we lack the underlying real material).

---

## 9. Dataset Registry / Catalog + Commissioning (Phases 9-reg, 9)

### Asset Catalog — **build new (distinct from the license registry)**
`training_data.py` is the **license** registry (dataset → license facts). The factory also needs an **asset** catalog (every admitted Asset + lineage + metadata + audit + coverage-cell). New `backend/lab/factory/catalog.py` — a durable, queryable store (JSONL/SQLite, mirroring `experiments.jsonl` discipline). The manifest builder selects from this catalog.

### Commissioning Framework — **build new**
```python
# backend/lab/factory/commission.py  (NEW)
```
Think **reusable musical building blocks, not songs.** A `BuildingBlockSpec` = {technique (open/barre/power chords, arpeggios, palm-mute, harmonics, slides, bends, vibrato, tapping, fingerstyle, funk/metal rhythm, blues/jazz phrases, ambient), guitar_type, tuning, tempo_range, key_set, n_takes, capture_reqs (DI mandatory + optional amp/mic), rights_terms (perpetual AI-training + DI delivery)}. One DI building block → (× augmentation recipes) × (× mix profiles × backings) = hundreds–thousands of supervised examples. Commissioning maximizes **reuse per recording**, and its briefs are generated from coverage gaps (§7-cov) and benchmark requests (§8).

---

## 10. GPU Training (Phase 11) — **refactor existing**

The Wave-4 runs proved the mechanism (provision → stage → train → checkpoint → ship → auto-terminate, watchdog, monitors) but as **ad-hoc scripts** (`run_w*.sh`, `pod_entry_w*.sh`, `watchdog_w*.sh`, `provision_w*.py`). Productize into `backend/lab/training/`: an `ExperimentSpec` (arch + manifest + hparams + base-ckpt, content-hashed), a provisioner (RunPod GraphQL, cascade SECURE→COMMUNITY), the resumable pod-entry + heartbeat + watchdog patterns, and `checkpoint_provenance` stamping (already exists). Mirrors the `lab/remote/jobs.py` bundle/worker discipline that already exists for inference jobs.

---

## 11. Gap analysis — exists / refactor / build

| Stage | Status | Where | Work |
|---|---|---|---|
| **Asset model + lineage** | **BUILD** | `lab/factory/asset.py` | new dataclass + content-hash identity + lineage append |
| 1 Ingestion abstraction | **BUILD** | `lab/factory/sources.py` | `SourceProvider` Protocol + per-source classes (mirror `ModelAdapter`) |
| 2 License Verification | **EXISTS** | `training_data.dataset_facts` / `build_manifest` | wire as a stage; no new logic |
| 3 Dataset Auditor | **EXISTS → promote** | `lab/dataset_auditor.py` | add batch driver + persistence + admit/reject routing |
| 4 Metadata | **EXISTS → extend** | `dataset_auditor.TrackAudit` | add augmentation_history, recording_source, source-tag override |
| 5 Quality Scoring | **EXISTS** | `dataset_auditor.quality_score` | add admit-threshold policy config |
| 6 Augmentation Engine | **BUILD** | `lab/factory/augment.py` | NAM reamp · IR conv · EQ/comp/verb/mic/room; lineage; license-per-capture |
| 7 Mix Generator | **BUILD** | `lab/factory/mixgen.py` | procedural mixer + `MaskingProfile` presets |
| 8 Validation | **EXISTS (reuse)** | `dataset_auditor` | run auditor on generated mixtures |
| 9 Asset Catalog | **BUILD** (≠ license registry) | `lab/factory/catalog.py` | durable asset store + coverage index |
| 9 Commissioning | **BUILD** | `lab/factory/commission.py` | `BuildingBlockSpec` schema + brief generator |
| 7c Coverage Analysis | **BUILD** | `lab/factory/coverage.py` | musical-space cells + weak-cell detection |
| 8 Benchmark Feedback | **PARTIAL → glue** | `scripts/run_stem_benchmark.py` + new | failures → CoverageRequest routing |
| 10 Training Manifest | **EXISTS → extend** | `training_data.build_manifest` | select from asset catalog (not hand-passed tracks) |
| 11 GPU Training | **REFACTOR** | w4–w9 ad-hoc scripts → `lab/training/` | productize provision/entry/watchdog/spec |
| 12 Benchmark | **EXISTS** | `scripts/*benchmark*` | reuse |
| Provenance backbone | **EXISTS → extend down** | `training_data.checkpoint_provenance` + Asset.lineage | asset-level lineage complements weight-level |

**Net:** the *gates and provenance backbone already exist* (auditor, license registry, manifest builder, checkpoint provenance) — the factory's hard-won correctness layer is done. What's genuinely new is the **creative middle** (ingestion abstraction, augmentation, mix generation, coverage, commissioning) and **productizing GPU training**. No existing system is duplicated; every new module plugs into an existing gate.

---

## 12. Implementation roadmap (infrastructure before scale)

1. **Asset model + Ingestion + Catalog** (`asset.py`, `sources.py`, `catalog.py`) — the spine everything rides on. Ingest the green-tier CC sets (GuitarSet, Guitar-TECHS, EGFxSet, Slakh) through it end-to-end.
2. **Auditor as a stage** — batch-audit every ingested asset; persist verdicts; enforce admit/reject at the manifest boundary. *No GPU sees unaudited data.*
3. **Augmentation Engine** — NAM reamp + IR + EQ/comp first (the distortion multiplier); validate one clean DI → N traceable variants, originals recoverable.
4. **Mix Generator** — procedural mixer + masking profiles; re-audit generated mixtures; prove ground-truth correctness.
5. **Coverage Analytics** — coverage map over the catalog; surface weak cells.
6. **Commissioning workflow** — `BuildingBlockSpec` + brief generator driven by coverage/benchmark gaps; the DI + AI-training-rights contract template.
7. **Pilot** — 20–30 guitarists, 100–200 DI performances, run the full factory end-to-end: ingest → audit → augment → mix → catalog → manifest → train → benchmark → coverage feedback. Validate the *loop*, not the dataset size.

**Only after the pilot validates the factory** do we invest in large-scale recording or licensing.

---

## 13. Success criteria

Not "we own the biggest dataset." Rather: **we own the best process** — a validated, provenance-clean, coverage-aware loop where (a) any new source plugs in through one abstraction, (b) no unaudited or license-dirty data can reach a GPU, (c) one real DI yields hundreds of traceable supervised examples at targeted difficulty, (d) benchmark failures automatically become acquisition requests, and (e) every checkpoint's full data lineage is answerable forever. Future datasets simply flow through. The factory is the moat.
