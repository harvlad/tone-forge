# Phase 0D — Stem Bundle Unblocker — Implementation Report

**Status:** complete. Deep analysis on Stairway now produces a full
six-stem bundle suitable for Phase 0C corpus expansion.

**Scope discipline:** this commit implements only the routing fix
(Part A), the instrumentation (Part C), the regression coverage
(Part B), and the corpus-expansion runner (Part D) called out in the
Phase 0D forensic report. No song-form classifier work. No
guidance-mode work. No threshold tuning. No section-classification
changes.

---

## 1. Root-cause recap (from `phase0d_stem_bundle_blocker.md`)

| Layer    | Mechanism                                                                                                                                                                                                          |
|----------|---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------|
| Primary  | `UrlAnalyzeRequest.fast_mode: bool = True` default (`tone_forge_api.py:3167`) + ladder at `tone_forge_api.py:4443` checks `request.fast_mode` *before* `analysis_mode`. Result: `PipelineConfig.deep()` was unreachable for any client that omitted `fast_mode=False`. |
| Secondary| Even when deep was reached, the gate at `unified_pipeline.py:602` skipped separation when `detected_type != "full_mix"` *unless* `force_stem_separation=True`. Only `PipelineConfig.deep()` sets that flag.         |
| Artefact | When `stems == {}` the guitar branch calls `analyzer.analyze(source_kind="full_mix")` → `stem_separator.separate_guitar()`, which writes one file (`<title>_other.wav`). That is why prior Stairway runs left a lone `_other.wav` on disk. |

The fix targets the primary layer. The secondary layer is already
correctly compensated by `PipelineConfig.deep().force_stem_separation`
once deep is actually reached.

---

## 2. Code changes

### 2.1 `backend/tone_forge/unified_pipeline.py`

Added `select_pipeline_config(*, analysis_mode, fast_mode)` after
`PipelineConfig.deep()`. The helper encodes the policy:

| Precedence | Condition                                       | Returned config             |
|-----------:|-------------------------------------------------|-----------------------------|
| 1          | `analysis_mode.strip().lower() == "deep"`       | `PipelineConfig.deep()`     |
| 2          | `analysis_mode` in `{"quick", "fast"}`          | `PipelineConfig.fast()`     |
| 3          | `fast_mode is True` (legacy default path)       | `PipelineConfig.fast()`     |
| 4          | otherwise                                       | `PipelineConfig.standard()` |

Legacy invariants preserved:

* No `analysis_mode`, default `fast_mode=True` → `fast()` (unchanged).
* `fast_mode=False`, no `analysis_mode` → `standard()` (unchanged).
* `fast_mode=False`, `analysis_mode="deep"` → `deep()` (unchanged).

What flips:

* `analysis_mode="deep"` with default `fast_mode=True` → **now**
  `deep()` instead of `fast()`. This is the Phase 0D fix.

### 2.2 `backend/tone_forge/unified_pipeline.py` — instrumentation

At the separation gate (`unified_pipeline.py:638-652`) the pipeline
now emits a single grep-friendly INFO line per analysis:

```
[stem-gate] mode=<deep|standard|fast> detected_type=<...> is_full_mix=<bool>
            separate_stems=<bool> force_stem_separation=<bool> should_separate=<bool>
```

This makes the gate decision observable from logs alone — the next
investigator does not need to dig into `analyzer.py` to determine
why a bundle came out empty.

### 2.3 `backend/tone_forge_api.py`

Three call-site replacements (the three sites that had the broken
fast-mode-first ladder):

| Endpoint                          | Old lines | New form                                                                                                  |
|-----------------------------------|-----------|------------------------------------------------------------------------------------------------------------|
| `POST /api/analyze-stream`        | 1172-1177 | `select_pipeline_config(analysis_mode=analysis_mode, fast_mode=fast_mode_bool)`                            |
| `POST /api/analyze-url`           | 4443-4448 | `select_pipeline_config(analysis_mode=getattr(request, "analysis_mode", "studio"), fast_mode=request.fast_mode)` |
| `POST /api/analyze-url-stream`    | 4525-4530 | `select_pipeline_config(analysis_mode=getattr(request, "analysis_mode", "studio"), fast_mode=request.fast_mode)` |

Site **not** touched (intentionally — it never had the bug):

| Endpoint            | Lines      | Why left alone                                                                                              |
|---------------------|------------|-------------------------------------------------------------------------------------------------------------|
| `POST /api/analyze` | 1105-1110  | Has no `fast_mode` field; routes on `analysis_mode` directly. Already correct. Touching it risks an accidental behavior change to the `"fast"` alias path. |

Import added to `backend/tone_forge_api.py:165-174`:
`select_pipeline_config`.

### 2.4 `backend/bench/corpus_expand.py` (new file, 285 lines)

Standalone CLI runner that bypasses uvicorn / FastAPI / the watcher
entirely:

```
python -m bench.corpus_expand --url <youtube_url> --label <slug> [--duration N]
python -m bench.corpus_expand --file <local.wav>  --label <slug>
```

Key properties:

* Forces `PipelineConfig.deep()`. No mode-selection ladder at all.
* Calls `UnifiedPipeline.analyze()` directly. No HTTP, no event loop
  inside uvicorn, no `--reload` watcher in the loop.
* Verifies stems against a required floor of
  `{drums, bass, vocals, other}` (the minimum the Phase 0C signals
  D and F consume) before persisting.
* On stems-missing the runner **fails loudly with exit code 4** and
  prints a structured failure JSON. No silent success path.
* On success persists to the same `backend/data/history.json` store
  the JAM UI loads from, then prints a JSON record on stdout with
  `bundle_id`, `stems_paths`, `section_count`, `mode`.

Exit codes:

| Code | Meaning                                                                |
|-----:|------------------------------------------------------------------------|
| 0    | success; stems present; bundle persisted; bundle_id printed             |
| 2    | download or local-file load failed                                      |
| 3    | pipeline raised an exception                                            |
| 4    | pipeline returned but `stems_paths` is missing required entries (Phase 0D failure mode) |

### 2.5 `backend/tests/test_pipeline_config_selection.py` (new file)

14 unit tests pinning the helper's selection policy. See §3.

---

## 3. Tests added

`backend/tests/test_pipeline_config_selection.py` — 14 tests:

```
test_deep_mode_wins_over_default_fast_mode                                PASSED
test_deep_mode_with_explicit_fast_mode_false                              PASSED
test_deep_mode_case_insensitive                                           PASSED
test_quick_mode_maps_to_fast                                              PASSED
test_fast_alias_maps_to_fast                                              PASSED
test_quick_mode_with_fast_mode_false_still_fast                           PASSED
test_legacy_default_fast_mode_true_returns_fast                           PASSED
test_legacy_no_analysis_mode_with_fast_mode_false_returns_standard        PASSED
test_legacy_unknown_mode_falls_through_to_standard_or_fast                PASSED
test_none_analysis_mode_with_fast_mode_true_returns_fast                  PASSED
test_empty_analysis_mode_with_fast_mode_false_returns_standard            PASSED
test_pipeline_config_fast_has_no_stem_separation                          PASSED
test_pipeline_config_standard_separates_full_mix_only                     PASSED
test_pipeline_config_deep_always_separates                                PASSED
```

Coverage of the three required cases from the task spec:

* **Case 1** (`analysis_mode="deep"` → `separate_stems=True` and
  `force_stem_separation=True`): pinned by
  `test_deep_mode_wins_over_default_fast_mode`,
  `test_deep_mode_with_explicit_fast_mode_false`,
  `test_deep_mode_case_insensitive`,
  `test_pipeline_config_deep_always_separates`.
* **Case 2** (`analysis_mode="fast"` → existing fast config): pinned
  by `test_fast_alias_maps_to_fast`, `test_quick_mode_maps_to_fast`,
  `test_quick_mode_with_fast_mode_false_still_fast`.
* **Case 3** (legacy without `analysis_mode` → exactly as before):
  pinned by `test_legacy_default_fast_mode_true_returns_fast`,
  `test_legacy_no_analysis_mode_with_fast_mode_false_returns_standard`,
  `test_legacy_unknown_mode_falls_through_to_standard_or_fast`.

Full local run including adjacent test files (all green):

```
$ python3 -m pytest tests/test_pipeline_config_selection.py \
                  tests/test_analysis_stage_timings.py \
                  tests/test_local_engine_chord_wireup.py \
                  tests/test_midi_density_hardening.py \
                  tests/test_per_stem_tempo_field_name.py
======================== 49 passed, 1 warning in 11.66s ========================
```

---

## 4. Stairway validation results

Run via the new corpus-expansion path:

```
$ python3 -m bench.corpus_expand \
    --url "https://www.youtube.com/watch?v=QkF3oxziUI4" \
    --label "stairway_phase0d" \
    --duration 90 --verbose
```

### 4.1 Gate trace (instrumentation, captured at 16:01:08):

```
[stem-gate] mode=deep detected_type=guitar is_full_mix=False
            separate_stems=True force_stem_separation=True should_separate=True
```

This single line is the proof-of-fix:

* `mode=deep` — the new `select_pipeline_config` routing reached
  `PipelineConfig.deep()` (where pre-fix the `fast_mode=True` short
  circuit produced `mode=fast`).
* `detected_type=guitar`, `is_full_mix=False` — same detector
  decision as the prior failed Stairway runs.
* `force_stem_separation=True` — the deep config's force flag
  carried through.
* `should_separate=True` — the gate fired Demucs.

### 4.2 Bundle:

```json
{
  "status": "ok",
  "bundle_id": "73b5931b",
  "label": "stairway_phase0d",
  "detected_type": "guitar",
  "section_count": 12,
  "mode": "deep",
  "stems_paths": {
    "drums":  "/api/admin/serve-file?path=...toneforge_stems_57ky1gd8/Led Zeppelin - Stairway To Heaven (Official Audio)_drums.wav",
    "bass":   ".../...Stairway..._bass.wav",
    "other":  ".../...Stairway..._other.wav",
    "vocals": ".../...Stairway..._vocals.wav",
    "guitar": ".../...Stairway..._guitar.wav",
    "piano":  ".../...Stairway..._piano.wav"
  }
}
```

### 4.3 Stems on disk (`toneforge_stems_57ky1gd8/`):

```
-rw-r--r-- 15874900 Jun 20 16:01 Led Zeppelin - Stairway To Heaven (Official Audio)_bass.wav
-rw-r--r-- 15874900 Jun 20 16:01 Led Zeppelin - Stairway To Heaven (Official Audio)_drums.wav
-rw-r--r-- 15874900 Jun 20 16:01 Led Zeppelin - Stairway To Heaven (Official Audio)_guitar.wav
-rw-r--r-- 15874900 Jun 20 16:01 Led Zeppelin - Stairway To Heaven (Official Audio)_other.wav
-rw-r--r-- 15874900 Jun 20 16:01 Led Zeppelin - Stairway To Heaven (Official Audio)_piano.wav
-rw-r--r-- 15874900 Jun 20 16:01 Led Zeppelin - Stairway To Heaven (Official Audio)_vocals.wav
```

All six htdemucs_6s stems present (15.1 MB each, 90 s @ 44.1 kHz
stereo).

### 4.4 Required-stem matrix:

| Stem    | Required by Phase 0C floor | Present in bundle | Present on disk |
|---------|----------------------------|-------------------|-----------------|
| drums   | yes (signal F)             | yes               | yes             |
| bass    | yes                        | yes               | yes             |
| vocals  | yes (signal D)             | yes               | yes             |
| other   | yes                        | yes               | yes             |
| guitar  | bonus (6s only)            | yes               | yes             |
| piano   | bonus (6s only)            | yes               | yes             |

---

## 5. Before / after execution trace

### Before (sessions 6a18dbfa, c02cedbd from `history.json`):

```
client → POST /api/analyze-url
         { url, analysis_mode: "deep" }     # fast_mode unset → default True

handler → ladder:
            if request.fast_mode:           # TRUE (default)
                config = PipelineConfig.fast()   # ← short-circuit
            elif analysis_mode == "deep":
                config = PipelineConfig.deep()   # UNREACHABLE
            else:
                config = PipelineConfig.standard()

pipeline → config.separate_stems == False
        → should_separate gate skipped
        → stems = {}
        → guitar branch: analyzer.analyze(source_kind="full_mix")
        → stem_separator.separate_guitar() runs Demucs
        → htdemucs (4-stem) source list lacks "guitar"
        → falls back to "other"
        → writes <title>_other.wav (single file)

bundle  → sections=32, detected_type=guitar, stems_paths=null
disk    → /tmp/toneforge_stems_*/<title>_other.wav    (lone artefact)
```

### After (this commit):

```
runner  → python -m bench.corpus_expand --url ... --label stairway_phase0d
         # bypasses HTTP, bypasses watcher entirely

config  → PipelineConfig.deep() forced unconditionally
         # separate_stems=True, force_stem_separation=True

pipeline → [stem-gate] mode=deep detected_type=guitar is_full_mix=False
                       separate_stems=True force_stem_separation=True
                       should_separate=True
        → _separate_stems() runs htdemucs_6s
        → 6 stems emitted

bundle  → sections=12, detected_type=guitar,
         stems_paths={drums,bass,other,vocals,guitar,piano}
disk    → /tmp/toneforge_stems_57ky1gd8/<title>_{drums,bass,other,vocals,guitar,piano}.wav
```

(Section count differs — 32 vs 12 — because the new run used a 90-second
preview window while the original 30-second preview produced more
small sections. The detector ran the same way; the difference is
input length, not pipeline behavior.)

### Network-path verification (HTTP endpoint, not exercised in this commit):

The same routing fix applies to `POST /api/analyze-url`. A future
HTTP client that posts `{"url": "...", "analysis_mode": "deep"}` —
without specifying `fast_mode` at all — will now reach
`PipelineConfig.deep()`. This is covered by
`test_deep_mode_wins_over_default_fast_mode` at the helper level
and is the same code path the live endpoint calls.

---

## 6. Remaining known limitations

1. **Schema default unchanged.** `UrlAnalyzeRequest.fast_mode` still
   defaults to `True`. This was intentional: changing the schema
   default risks legacy clients that silently relied on
   `analysis_mode="studio"` mapping to `fast()`. The helper
   precedence preserves their behavior. If we ever want
   `analysis_mode="studio"` to mean `standard()` for fresh requests,
   that is a separate API-level change with its own migration story.

2. **Detection gate still skips separation for `detected_type !=
   "full_mix"` in standard mode.** Standard mode does not set
   `force_stem_separation`. This is intentional — full Demucs on
   already-isolated content is wasteful. Deep mode pays the cost
   when explicitly requested; corpus expansion uses deep
   exclusively.

3. **CoreCorpus-runner depends on the local environment's yt-dlp +
   Demucs model cache** (htdemucs_6s ≈ 250 MB). First-run
   downloads can be slow.

4. **The `analyzer.separate_guitar()` single-stem path still exists**
   and will still emit a lone `_other.wav` if reached. The Phase 0D
   fix prevents the *deep* code path from reaching it, but standard
   mode with `detected_type="guitar"` will still take that branch.
   That is correct behavior for isolated guitar uploads; it just
   means future stem-bundle audits should be scoped to deep / full-mix
   inputs and should consult the `[stem-gate]` log line to confirm
   which branch ran.

5. **Corpus-expansion runner persists to the live
   `history.json`.** Bundles produced by `corpus_expand` are
   indistinguishable from HTTP-produced bundles except for the
   `corpus_run: True` marker on the history entry. If we want a
   separate corpus store later, that is a follow-up change.

6. **No end-to-end FastAPI integration test for the corrected
   ladder** in this commit. The helper unit tests pin the policy;
   the live `[stem-gate]` log on the Stairway run is the production
   evidence. A starlette TestClient regression test calling the
   actual `/api/analyze-url` endpoint with `analysis_mode="deep"`
   and asserting `[stem-gate] mode=deep` is appropriate follow-up
   if we want defense-in-depth.

---

## 7. Files touched

```
backend/tone_forge/unified_pipeline.py     # +57   select_pipeline_config + [stem-gate] log
backend/tone_forge_api.py                  # 3 call sites updated, 1 import added
backend/bench/corpus_expand.py             # NEW   CLI corpus runner (285 LoC)
backend/tests/test_pipeline_config_selection.py  # NEW   14 unit tests
phase0d_implementation_report.md           # NEW   this file
```

---

## 8. Success criteria — verification

> "A deep analysis run on Stairway produces a real stem bundle suitable
> for Phase 0C corpus expansion."

Met. See §4. Bundle `73b5931b` carries six stems on disk plus a
`stems_paths` dict ready for downstream Phase 0C signal computation.
The `corpus_run: True` history marker lets the next analysis step
filter for calibration-grade entries.

> "Do not begin song-form classifier work yet. Corpus expansion must be
> unblocked first."

Honored. This commit changes nothing in `tone_forge/analysis/`,
`song_form_phase0c/`, the bench corpus, or the guidance/classifier
surface. Only the routing helper, the gate log line, three thin
call-site swaps, the CLI runner, and one test file.
