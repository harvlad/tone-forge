# V2 Cross-Instrument Validation Plan

**Status:** PREPARED, **NOT EXECUTED**.
**Purpose:** Determine whether the seg8 fingerprint generalises beyond
Analog to the other three Live native synths (Operator, Wavetable, Drift).
**Trigger to execute:** explicit operator approval of the render-time
budget below.

V3 established that retrieval is *usable within Analog* for workhorse
classes (`RETRIEVAL_GATE_PASS.md`). V2 asks the next question: does that
property hold cross-engine, or is the fingerprint accidentally
Analog-specific?

---

## 1. Hypotheses

**V2-H1 (primary):** Within-engine LOO retrieval performance on Operator,
Wavetable, and Drift is comparable to Analog (no engine collapses to
random).

**V2-H2 (primary):** Cross-engine retrieval works — given a query in one
engine, the top-5 contains ≥ 1 same-`sound_type` neighbor (any engine)
at a rate comparable to within-Analog.

**V2-H3 (secondary):** Workhorse `any_top5_usable` rate (V3-style human
rating) on a small subset of cross-engine targets is ≥ 70%, the same
gate Analog passed.

H1 measures "does the embedding hold its shape inside the new corpus?"
H2 measures "does the embedding put audibly similar sounds near each
other regardless of engine?" H3 (optional, deferred) measures "is the
embedding *usefully* cross-engine, not just numerically clustered?"

---

## 2. Required render counts

These are the corpus sizes proposed for V2. They are bounded by the
preset libraries that ship in Live Suite 12.

| Engine    | Source folders                          | Target count | Notes                          |
|-----------|-----------------------------------------|-------------:|--------------------------------|
| Operator  | Core Library / Operator                 | ~120         | full Core Library              |
| Wavetable | Core Library / Wavetable                | ~150         | full Core Library              |
| Drift     | Core Library / Drift                    | ~80          | smaller library                |
| **Total** |                                         | **~350**     | excludes the 99 already-Analog |

Quotas inside each engine: prefer balance across bass / lead / pad /
keys / fx / percussion / other. If an engine library skews (e.g. Drift
is largely lead-biased), record the skew rather than padding artificially.

Provenance for each preset must mirror the Analog catalog: `preset_path`,
`adv_sha1`, `als_path`, `als_sha1`, `wav_sha1`, `decoded_audio_sha1`,
`test_sequence_name`. The Catalog Integrity Gate (six criteria from
`RENDER_PIPELINE_RCA.md` §7) must pass on each engine's catalog
**before** any retrieval evaluation is reported.

---

## 3. Estimated render time

Based on the Analog baseline of ~25 minutes for 99 presets
(`scripts/auto_export_presets.py`, ~15 seconds per preset including
GUI focus + dialog handling + render):

| Engine    | Count | Estimated wall-clock |
|-----------|------:|---------------------:|
| Operator  | ~120  | ~30 min              |
| Wavetable | ~150  | ~38 min              |
| Drift     | ~80   | ~20 min              |
| **Total** | ~350  | **~90 min**          |

Plus buffer for restarts on misfires: budget **2 hours of dedicated
Ableton time** with the workstation idle (no keyboard/mouse interference).

If the operator's `auto_export_presets.py` hit-rate is < 100% on any
engine, expect 1 retry pass per engine (~10–15 min each). Total worst
case: **~3 hours**.

---

## 4. Retrieval evaluation protocol

Once all four engine catalogs are rendered and pass the Integrity Gate:

### 4.1 Within-engine LOO sweep (V2-H1)

For each engine independently:

1. Compute seg8 embeddings for every preset in that engine.
2. Leave-one-out cosine retrieval: each preset's top-5 nearest
   neighbors *within the same engine*.
3. Report:
   - top-1 same-`sound_type` accuracy
   - top-5 hit rate (≥ 1 same-`sound_type` in top-5)
   - per-`sound_type` hit-rate breakdown
   - hub-collapse stats: max in-degree at k=10, median in-degree
4. Wilson 95% CI on top-1 accuracy.

**V2-H1 PASS criteria** (per engine):

| Metric                    | Threshold                          |
|---------------------------|------------------------------------|
| top-5 hit rate (workhorse)| ≥ 60% (Analog V1 baseline was 67%) |
| top-1 accuracy            | ≥ Analog top-1 − 10 pp             |
| max in-degree at k=10     | ≤ 25                               |

Any engine that fails all three metrics is treated as a partial fail
(report it, but continue to V2-H2 with that engine flagged).

### 4.2 Cross-engine retrieval (V2-H2)

Combined corpus: 99 Analog + 120 Operator + 150 Wavetable + 80 Drift
≈ 449 presets.

1. Compute seg8 over the union.
2. For each preset, LOO retrieval over the full union.
3. Report per-engine *query* slices: when the query is from engine E,
   what fraction of top-5 share E vs other engines?
4. Same-`sound_type` hit rates *regardless of engine*.

**V2-H2 PASS criteria:**

| Metric                                                   | Threshold |
|----------------------------------------------------------|-----------|
| Workhorse top-5 same-`sound_type` hit rate (any engine)  | ≥ 60%     |
| Same-engine bias (top-1 from same engine as query)       | < 70%     |

The same-engine bias bound is a tripwire for hidden engine-cluster
collapse. If the top-1 is from the same engine > 70% of the time, the
fingerprint is plausibly an engine-identifier rather than a tone
descriptor, and cross-engine retrieval cannot be trusted regardless of
the headline same-`sound_type` rate.

### 4.3 Cross-engine V3 spot check (V2-H3, optional)

Only run if V2-H1 and V2-H2 pass.

1. Sample 4–6 workhorse queries per non-Analog engine (so 12–18 total).
2. Top-5 retrieval from the **full** cross-engine corpus.
3. Operator rates each top-5 neighbor with the same V3 rubric
   (would-start / no / ?) using the existing
   `scripts/listening_rig_server.py` + `listening_rig.html`
   (it is corpus-agnostic; just point it at a new
   `v3_cross_engine_*.json` query set).
4. Score with `scripts/score_v3_usefulness.py`.

**V2-H3 PASS criterion:** workhorse `any_top5_usable` ≥ 70%.

This is the same bar Analog cleared. Hitting it cross-engine is the
strongest evidence we can collect at this corpus scale that the
fingerprint is genuinely tone-descriptive.

---

## 5. Overall V2 verdict

| Outcome                  | Definition                              | Implication                                                    |
|--------------------------|-----------------------------------------|----------------------------------------------------------------|
| **STRONG PASS**          | H1 PASS for all 3 engines AND H2 PASS  | seg8 generalises. Promote as the production embedding.         |
| **WEAK PASS**            | H1 PASS for ≥ 2 of 3 engines AND H2 PASS| seg8 mostly generalises. Investigate the weak engine.          |
| **FAIL (engine-specific)** | H1 FAIL on ≥ 1 engine, H2 PASS        | Engine-specific deficit. Possibly a corpus issue; do not promote yet. |
| **FAIL (cross-engine)**  | H2 FAIL                                  | seg8 is Analog-specific. Do not promote. Re-open representation work. |

V2-H3 is informational only; H1 + H2 jointly decide promotion.

---

## 6. Pre-conditions to execute

Before kicking off V2:

1. **Operator render-time approval.** ~2-3 hours of dedicated Ableton
   time committed.
2. **`auto_export_presets.py` proven on Operator/Wavetable/Drift.** The
   existing driver was only validated on Analog. A 10-preset dry run per
   engine confirms the GUI flow (focus, dialog handling) works for that
   engine. This is mechanical, not perceptual.
3. **Catalog Integrity Gate green on a per-engine pilot (~10 presets).**
   Confirms the ALS embed logic (`preset_als_generator.py`) handles each
   engine's `.adv` schema. If it fails on Operator/Wavetable/Drift, the
   `.adv` embed code needs an engine-specific branch before batch render.
4. **Workhorse retrieval gate frozen.** `RETRIEVAL_GATE_PASS.md`
   already records this; V2 must not regress workhorse metrics on
   Analog when the corpus is expanded.

Pre-conditions 2 and 3 together are an estimated **30 minutes of
Ableton time** before the V2 batch render is committed to. They are
the V2 equivalent of the Single-Preset Equivalence Test that gated the
Analog rebuild.

---

## 7. Outputs

Produced by V2 execution (none yet exist):

- `preset_catalog_output/catalog/catalog_operator.json`
- `preset_catalog_output/catalog/catalog_wavetable.json`
- `preset_catalog_output/catalog/catalog_drift.json`
- `preset_catalog_output/catalog/catalog_combined.json` (union)
- `preset_catalog_output/retrieval/v2_within_engine_report.{json,md}`
- `preset_catalog_output/retrieval/v2_cross_engine_report.{json,md}`
- `preset_catalog_output/retrieval/v2_integrity_gate.{json,md}` (per engine)
- (Optional, only on H1 + H2 PASS) `preset_catalog_output/retrieval/v3_cross_engine_*.json` rating set
- (Optional) `preset_catalog_output/retrieval/v3_cross_engine_score_report.{json,md}`

---

## 8. Decisions deliberately deferred

- **Embedding research.** V2 evaluates seg8 as it stands. No new
  pooling variants, no CLAP comparison, no learned heads. Those are
  gated behind a V2 FAIL.
- **Reconstruction trials on non-Analog engines.** The Analog
  reconstruction trial (`RECONSTRUCTION_TRIAL_PLAN.md`) must finish
  first; cross-engine reconstruction trials need both V2 PASS and an
  Analog reconstruction PASS to be worth running.
- **Promotion of seg8 to production code.** Production
  `stem_fingerprint.py` is unchanged. Promotion requires V2 STRONG
  or WEAK PASS *and* a passing Analog reconstruction trial.

---

## 9. Out of scope for V2

- FX label audit follow-ups (separate track; `score_fx_label_audit.py`).
- Keys corpus expansion (separate track; needs Operator/Wavetable
  catalogs first because both engines own most of the keys library).
- Percussion (n was 2 in V3; V2 will incidentally grow the percussion
  corpus, but a dedicated percussion eval still requires a future
  rating pass).
- Cross-instrument *reconstruction* timing trials (waits on Analog
  reconstruction outcome).

---

## 10. Trigger to execute

When the operator commits to a ~3-hour Ableton block and pre-conditions
§6.2 and §6.3 are met, V2 proceeds as a single sustained session:

```
# 0. Pilot validation (30 min, gated):
python scripts/build_preset_catalog.py generate \
    --instruments Operator --only "Analog Bell" \
    --out-dir preset_catalog_output/v2_pilot/operator
# (repeat for Wavetable and Drift on one preset each)
# Confirm catalog_integrity_gate passes per pilot.

# 1. Full per-engine render (~90 min, operator-attended):
python scripts/build_preset_catalog.py generate --instruments Operator
python scripts/auto_export_presets.py
python scripts/build_preset_catalog.py generate --instruments Wavetable
python scripts/auto_export_presets.py
python scripts/build_preset_catalog.py generate --instruments Drift
python scripts/auto_export_presets.py

# 2. Fingerprint + per-engine integrity gate (~5 min):
python scripts/build_preset_catalog.py fingerprint --instruments Operator
python scripts/build_preset_catalog.py fingerprint --instruments Wavetable
python scripts/build_preset_catalog.py fingerprint --instruments Drift

# 3. V2-H1 within-engine LOO sweep (~2 min):
python scripts/retrieval_eval_v2.py --mode within --engines Operator,Wavetable,Drift

# 4. V2-H2 cross-engine sweep (~2 min):
python scripts/retrieval_eval_v2.py --mode cross --engines Analog,Operator,Wavetable,Drift

# 5. (Optional, on H1+H2 PASS) V2-H3 listening rig:
python scripts/listening_rig_server.py \
    --queries preset_catalog_output/retrieval/v3_cross_engine_queries.json
# Operator rates 12-18 queries.
python scripts/score_v3_usefulness.py \
    --input preset_catalog_output/retrieval/v3_cross_engine_ratings.json
```

The two `retrieval_eval_v2.py` invocations are the only new scripts
required. They are deliberately not built ahead of execution — building
them is a 2-hour task and should be sequenced into the same week as
the render so they are fresh and the operator's batch render is not
left stale waiting for analysis tooling.
