# Phase 2 Validation Report

**Status:** TEMPLATE — populated by `scripts/run_phase2_benchmark.py` once
rendered WAVs and the benchmark corpus land. Do not hand-edit the auto-filled
sections; they will be overwritten.

**Question being answered:** Does the current 5-chain recommendation system
produce sensible rankings on real music after replacing placeholder
fingerprints with measured fingerprints?

---

## 0. Rendering provenance (operator-filled)

Filled in by operator after rendering. Claude reads this for context but
does not modify it.

| Field | Value |
|---|---|
| Rendering option used | (A Connect / B Ableton+M4L / C 3rd-party amp-sim) |
| DI source clip | `<path or description>` |
| Render directory | `<path>` |
| Sample rate / bit depth | `<e.g. 48 kHz / 24-bit>` |
| Date rendered | `<ISO timestamp>` |

**Sit-with-reference confirmation (one line per chain):**

- `tfc.clean_strat`: _<one-line operator comment>_
- `tfc.edge_of_breakup`: _<one-line operator comment>_
- `tfc.classic_rock`: _<one-line operator comment>_
- `tfc.modern_gain`: _<one-line operator comment>_
- `tfc.ambient`: _<one-line operator comment>_

---

## 1. Fingerprint generation log

_Auto-filled by `python3 scripts/render_chain_references.py --verbose`._

```
<contents of /tmp/phase2_render.log>
```

All 5 fingerprint JSONs must report `source: "rendered_reference"` after this
section is filled in. If any still say `hand_authored_estimate`, the
corresponding WAV was missing — STOP and re-render.

---

## 2. Per-chain delta — placeholder vs measured

_Auto-filled by `run_phase2_benchmark.py delta-report`._

For each chain:

```
Chain: tfc.<id>  (display: "...")
  feature           placeholder   measured   delta
  brightness        0.XX          0.XX       ±0.XX
  warmth            0.XX          0.XX       ±0.XX
  air               0.XX          0.XX       ±0.XX
  attack_ms         XXXX          XXXX       ±XXXX
  decay_ms          XXXX          XXXX       ±XXXX
  sustain_ratio     0.XX          0.XX       ±0.XX
  harmonic_ratio    0.XX          0.XX       ±0.XX
  pitch_stability   0.XX          0.XX       ±0.XX
  z-norm distance(placeholder → measured): X.XXXX
```

**Aggregate:**

| Stat | Value |
|---|---|
| Total z-norm shift (sum across 5 chains) | _XX.XX_ |
| Largest shift | _tfc.X (X.XX)_ |
| Smallest shift | _tfc.X (X.XX)_ |
| Chains whose top-2 features stayed in correct rank | _N / 5_ |

**Q: Were the original estimates directionally accurate?**

_Answer derived from the table above. Specifically:_

- A chain is "directionally accurate" if its measured top-2 strongest
  features (relative to other chains) match the placeholder's top-2.
- Per-feature deltas exceeding 2σ of the catalog distribution are flagged.

_<Claude writes the answer here with evidence pointing to specific rows.>_

---

## 3. Alcest re-test

_Auto-filled by `run_phase2_benchmark.py alcest`._

Stem: `/var/folders/.../tmpzqz3k5um_other_center.wav`
Tempo: 104 BPM · Key: E Minor

### Comparison table

| Field | Phase 1 (placeholder) | Phase 2 (measured) | Δ |
|---|---|---|---|
| Top match (raw) | `tfc.ambient` | _<...>_ | _<...>_ |
| Distance to top | 30.26 | _<...>_ | _<...>_ |
| Distance to runner-up | 33.04 | _<...>_ | _<...>_ |
| Margin | 0.08 | _<...>_ | _<...>_ |
| Confidence | 0.12 | _<...>_ | _<...>_ |
| Tier | `low` | _<...>_ | _<...>_ |
| Apply | `tfc.classic_rock` (fallback) | _<...>_ | _<...>_ |

### Answers to the 5 brief questions

1. **Does Ambient remain the top recommendation?** _Y / N_
2. **Did the distance collapse from ~30 toward the expected range?** _from 30.26 → X.XX_
3. **Did confidence improve?** _from 0.12 → 0.XX_
4. **Did tier improve?** _low → <tier>_
5. **Is the recommendation still directionally correct?** _Y / N + rationale_

### Full recommendation object (measured)

```json
<wire dict from guitar_catalog.to_wire_dict(...)>
```

---

## 4. Benchmark corpus (operator-supplied)

_Source: `backend/PHASE2_BENCHMARK_CORPUS.json`_

| # | Artist | Song | Expected | Genre tag | Reason |
|---|--------|------|----------|-----------|--------|
| 1 | _<...>_ | _<...>_ | _<...>_ | _<...>_ | _<...>_ |
| 2 | _<...>_ | _<...>_ | _<...>_ | _<...>_ | _<...>_ |
| ... |

**Genre coverage check:**

| Genre | Count | Status |
|---|---|---|
| Shoegaze / post-rock | _N_ | _OK / WARNING_ |
| Ambient guitar | _N_ | _OK / WARNING_ |
| Classic rock | _N_ | _OK / WARNING_ |
| Blues | _N_ | _OK / WARNING_ |
| Indie | _N_ | _OK / WARNING_ |
| Modern gain | _N_ | _OK / WARNING_ |
| Metal | _N_ | _OK / WARNING_ |
| Clean / jangle | _N_ | _OK / WARNING_ |
| Edge-of-breakup | _N_ | _OK / WARNING_ |

---

## 5. Ranking validation

_Auto-filled by `run_phase2_benchmark.py validate`._

| # | Artist | Song | Expected | Top 1 (d, conf) | Top 2 (d) | Top 3 (d) | Tier | Pass? |
|---|--------|------|----------|-----------------|-----------|-----------|------|-------|
| 1 | _<...>_ | _<...>_ | _<...>_ | _<...>_ | _<...>_ | _<...>_ | _<...>_ | _✓ / ✗_ |
| 2 | … | … | … | … | … | … | … | … |

**Aggregate accuracy:**

| Metric | Value | N |
|---|---|---|
| Top-1 accuracy | _XX.X%_ | _M / N_ |
| Top-2 accuracy | _XX.X%_ | _M / N_ |
| Top-3 accuracy | _XX.X%_ | _M / N_ |

**Per-genre breakdown:**

| Genre | N | Top-1 | Top-3 |
|---|---|---|---|
| Shoegaze / post-rock | _N_ | _X / N_ | _X / N_ |
| Ambient guitar | _N_ | _X / N_ | _X / N_ |
| Classic rock | _N_ | _X / N_ | _X / N_ |
| Blues | _N_ | _X / N_ | _X / N_ |
| Indie | _N_ | _X / N_ | _X / N_ |
| Modern gain | _N_ | _X / N_ | _X / N_ |
| Metal | _N_ | _X / N_ | _X / N_ |
| Clean / jangle | _N_ | _X / N_ | _X / N_ |
| Edge-of-breakup | _N_ | _X / N_ | _X / N_ |

---

## 6. Failure analysis

_One subsection per row where Pass? == ✗._

### 6.x Failure: `<artist>` — `<song>`

| Field | Value |
|---|---|
| Expected | _tfc.X_ |
| Actual top-1 | _tfc.X (d=X.XX, conf=0.XX)_ |
| Margin | _0.XX_ |
| Tier | _<low / medium / high>_ |

**Query feature vector (8):**

```
brightness=X.XX  warmth=X.XX  air=X.XX  attack_ms=XXXX
decay_ms=XXXX    sustain_ratio=X.XX  harmonic_ratio=X.XX  pitch_stability=X.XX
```

**Full distance ranking:**

```
1. tfc.X  d=X.XX
2. tfc.X  d=X.XX
3. tfc.X  d=X.XX
4. tfc.X  d=X.XX
5. tfc.X  d=X.XX
```

**Probable cause:** _<one paragraph>_

**Failure class:** `catalog` | `feature` | `stem_selection` | `ranking` | `calibration`

---

## 7. Go / No-Go decision

| Metric | Threshold | Observed |
|---|---|---|
| Top-1 accuracy | ≥ 70% (GO) · ≥ 50% (Conditional) | _XX%_ |
| Top-3 accuracy | ≥ 90% (GO) · ≥ 80% (Conditional) | _XX%_ |
| Ambient → top-1 for shoegaze/post-rock | required for GO | _Y / N_ |

**Verdict:** _GO / CONDITIONAL GO / NO-GO_

**Supporting evidence:**

_<numbered list of the strongest 3–5 data points from §5 and §6 supporting the verdict>_

**Single biggest issue identified:**

_<one paragraph naming the highest-leverage thing to fix next, whether GO or NO-GO>_

---

## 8. Reproducibility

| Artifact | Path | Purpose |
|---|---|---|
| Placeholder snapshot | `backend/PHASE2_PLACEHOLDER_FINGERPRINTS.json` | Step 2 delta baseline |
| Render log | `/tmp/phase2_render.log` | Step 2 audit trail |
| Tone instrumentation log | `backend/data/tone_log.jsonl` | Step 5 instrumentation echo |
| Benchmark corpus | `backend/PHASE2_BENCHMARK_CORPUS.json` | Steps 4–5 input |
| Benchmark per-song results | `backend/PHASE2_BENCHMARK_RESULTS.jsonl` | Steps 5–6 raw data |
| This report | `backend/PHASE2_VALIDATION_REPORT.md` | Human-readable summary |

To re-run: see `PHASE2_VALIDATION_KIT.md` §11.
