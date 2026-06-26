# Phase 0D — Stem Bundle Blocker Forensics

> **Scope:** infrastructure investigation only. **No code was modified.**
> No classifier, song-form, guidance-mode, threshold, UI, or similarity
> code was touched. This document localises the failure precisely
> enough that a minimal corrective commit can be designed afterward,
> per the Phase 0D directive.

---

## 1. Executive summary

**Primary root cause (single, supported by evidence):**

> The `/api/analyze-url` request body defaults `fast_mode=True`
> (`backend/tone_forge_api.py:3167`), and the handler's mode-selection
> ladder at `backend/tone_forge_api.py:4443-4448` checks `fast_mode`
> **before** `analysis_mode`. Any client that sends only
> `{"url": "...", "analysis_mode": "deep"}` — including this session's
> two Stairway probes — is silently downgraded to `PipelineConfig.fast()`,
> which has `separate_stems=False`. Demucs never runs as a top-level
> pipeline stage, so the bundle's `stems_paths` stays `None`.

**Secondary mechanism (explains the on-disk `_other.wav`):**

> When the unified pipeline produces no top-level stems, the guitar
> analyzer transitively calls `stem_separator.separate_guitar(path)`
> from `backend/tone_forge/analyzer.py:168`. `separate_guitar` runs
> Demucs internally (so a 6-stem job's worth of compute is spent), but
> by design it writes only the single `'other'` source as
> `<title>_other.wav`. **That is the file we see on disk.** No
> race, no truncated write, no watcher interruption is required to
> explain the artefact; the pipeline is doing exactly what the code
> says.

**Tertiary risk (not the cause this time, but real):**

> Even if the `fast_mode` default were fixed, the standard-mode gate at
> `backend/tone_forge/unified_pipeline.py:602` would still skip
> separation for Stairway because `auto_detect` flags it as
> `detected_type="guitar"` → `is_full_mix=False`, and
> `PipelineConfig.standard()` has `force_stem_separation=False`. Only
> `PipelineConfig.deep()` overrides the detection gate.

**Watcher / file-watcher interaction:**

> The FastAPI `--reload` watcher was observed to restart the server
> mid-session (log lines 13617, 14230 of the captured server stdout).
> However, the `_other.wav`-alone artefact is fully explained by
> `separate_guitar`'s by-design behaviour and does **not** require a
> watcher-induced kill. Watcher restarts remain a real risk for any
> future genuine deep-mode run, but they are not Phase 0D's primary
> root cause.

Ranked answer to the directive's "choose exactly one primary root
cause":

1. **Primary:** `fast_mode=True` default silently overrides
   `analysis_mode="deep"`.
2. **Co-cause (operative whenever a client does set `fast_mode=False`
   without `analysis_mode="deep"`):** detection gate skips separation
   for single-instrument-classified audio.
3. **Side-effect that camouflages the failure:** `separate_guitar`'s
   single-stem write produces `<title>_other.wav` and makes the
   failure look like a "partial Demucs run" instead of a fully-bypassed
   pipeline stage.

---

## 2. Call graph

```
Client HTTP request
   │
   │ POST /api/analyze-url
   │ Body: {"url": "...", "analysis_mode": "deep"}    ← fast_mode omitted
   ▼
backend/tone_forge_api.py:4425  analyze_url_endpoint(request)
   │
   │  Parses UrlAnalyzeRequest (tone_forge_api.py:3163)
   │    fast_mode: bool = True                ← DEFAULT
   │    analysis_mode: str = "studio"
   │
   │  Mode-selection ladder (tone_forge_api.py:4443-4448):
   │    if request.fast_mode:                 ← TRUE by default
   │        config = PipelineConfig.fast()    ← TAKEN
   │    elif analysis_mode == "deep":         ← UNREACHABLE when fast_mode=True
   │        config = PipelineConfig.deep()
   │    else:
   │        config = PipelineConfig.standard()
   ▼
backend/tone_forge/unified_pipeline.py:161-174  PipelineConfig.fast()
   │    separate_stems=False                  ← demucs disabled
   │    force_stem_separation=False
   │    extract_midi=True
   ▼
backend/tone_forge/unified_pipeline.py:552  analyze_streaming(audio, config)
   │
   │  Stage 1 — _load_audio (line 578)
   │  Stage 2 — _detect_content (line 587)
   │             → calls auto_detect.detect_audio_type(path)
   │             → returns DetectionResult(detected_type="guitar",
   │                                       is_full_mix=False)
   │
   │  Stage 3 — STEM SEPARATION GATE (line 602):
   │    should_separate = config.separate_stems
   │                       and (detection.is_full_mix
   │                            or config.force_stem_separation)
   │    │                       │                  │
   │    │                       │                  │
   │    │       FALSE           FALSE              FALSE
   │    │   (fast mode)     (guitar detect)     (only deep sets)
   │    │
   │    └──────► should_separate = FALSE
   │             → _separate_stems NEVER called
   │             → stems = {}
   ▼
unified_pipeline.py:1145  _analyze_instruments(stems={}, audio_path, ...)
   │
   │    guitar_audio_path = stems.get("guitar",
   │                            stems.get("other", audio_path))
   │                                              ↑
   │                                              fallback: raw YT wav
   │    source_kind = "isolated_guitar" if stems else "full_mix"
   │                                              ↑
   │                                              "full_mix" because stems={}
   ▼
backend/tone_forge/analyzer.py:160-168  analyze(path, source_kind="full_mix")
   │
   │    if source_kind == "full_mix":
   │        analysis_path = stem_separator.separate_guitar(path)
   │                                              ↑
   │                                              hidden Demucs run
   ▼
backend/tone_forge/stem_separator.py:131-180  separate_guitar(audio_path)
   │
   │    model = get_model("htdemucs")           ← 4-stem model
   │    sources = apply_model(model, wav, ...)
   │
   │    source_names = list(model.sources)      ← ['drums','bass','other','vocals']
   │    if "guitar" in source_names:            ← FALSE (htdemucs has no guitar)
   │        stem_idx = ... ; stem_name = "guitar"
   │    elif "other" in source_names:           ← TRUE
   │        stem_idx = source_names.index("other")
   │        stem_name = "other"
   │
   │    output_path = output_dir / f"{audio_path.stem}_{stem_name}.wav"
   │    sf.write(output_path, ...)              ← WRITES ONE FILE
   │                                            ← "<title>_other.wav"
   ▼
On-disk artefact:
   /var/folders/.../toneforge_stems_<rand>/
       Led Zeppelin - Stairway To Heaven (Official Audio)_other.wav   ← THE FILE
```

Note that the bundle returned to the client still has `stems_paths=None`
because that field is set from the **top-level pipeline's** `stems`
dict (`unified_pipeline.py:1843-1855`), which was never populated.
The hidden `separate_guitar` run is invisible to the bundle —
its output is consumed only by the guitar analyzer.

---

## 3. Bypass matrix

Every branch that can produce the failure mode
"sections present, `stems_paths` absent":

| # | Gate / branch                       | File:line                                  | Condition that bypasses separation                                                  | Observed in Stairway? |
|---|-------------------------------------|--------------------------------------------|--------------------------------------------------------------------------------------|-----------------------|
| 1 | `fast_mode` default in request body | `tone_forge_api.py:3167`                   | Client omits `fast_mode`; defaults to `True`; handler picks `.fast()`.               | **YES — primary**     |
| 2 | `if request.fast_mode:` short-circuit | `tone_forge_api.py:4443-4444`            | Even when `analysis_mode="deep"` is set, `fast_mode=True` is evaluated first.        | **YES — primary**     |
| 3 | `PipelineConfig.fast()` field default | `unified_pipeline.py:165`                | `separate_stems=False` → gate at line 602 is always False.                           | **YES — primary**     |
| 4 | `PipelineConfig.standard()` default | `unified_pipeline.py:200, 201`              | `separate_stems=True` BUT `force_stem_separation=False` → gate depends on detection. | (Would apply if fast_mode disabled, analysis_mode="standard") |
| 5 | Detection gate / `is_full_mix`     | `unified_pipeline.py:602`                   | `(detection.is_full_mix or config.force_stem_separation)` must be True; both False → skip. | **YES — secondary**   |
| 6 | `auto_detect.detect_audio_type`    | `auto_detect.py:202, 292, 298, 559`         | Mix-score ≤ 0.5 → `is_full_mix=False`; instrument scores → `detected_type="guitar"`. | **YES — confirmed by bundle's `detected_type=guitar`** |
| 7 | `_separate_stems` exception catch  | `unified_pipeline.py:1018-1026`             | If htdemucs_6s import/load fails AND fallback `htdemucs` also fails → empty dict.    | NOT in this run (Demucs *did* run inside separate_guitar). |
| 8 | `_analyze_instruments` guitar branch | `unified_pipeline.py:1145-1149`           | When `stems == {}`, `source_kind="full_mix"` triggers `separate_guitar` indirectly.  | **YES — produced `_other.wav`** |
| 9 | `separate_guitar` single-stem write | `stem_separator.py:159-173`                | Always writes exactly one stem (`guitar` or `other`).                                | **YES — observed artefact**   |
| 10| `cached stems` (no such mechanism) | n/a                                        | No cache lookup exists; new request always re-runs the gate.                         | N/A                   |
| 11| WatchFiles reload mid-analysis     | uvicorn `--reload` default                  | Process restart can kill in-flight Demucs; could leave partial output.               | Not the cause here, but observed twice in log (lines 13617, 14230). |

---

## 4. Stairway forensics

### 4.1 Evidence sources

- `backend/data/history.json`, entries `6a18dbfa` (2026-06-20 14:58:35) and `c02cedbd` (2026-06-20 15:14:51): both `detected_type="guitar"`, `sections=32`, `stems_paths=null`.
- On-disk temp dirs `/var/folders/.../toneforge_stems_*/` for Stairway: each contains exactly one file, `Led Zeppelin - Stairway To Heaven (Official Audio)_other.wav` (52 MB, file timestamps 14:57 and 15:09 today, plus two older instances on Jun 18 00:29 and 00:46). No `_drums.wav`, `_bass.wav`, `_vocals.wav`, `_guitar.wav`, `_piano.wav`.
- Captured server stdout `/tmp/claude/.../bebc762.output`: two `POST /api/analyze-url HTTP/1.1 200 OK` returns at log lines 14178 (standard run, ≈297 s wall) and 14794 (deep retry, ≈388 s wall). MIDI prediction for the Stairway preview file appears at line 14238: `Predicting MIDI for /var/folders/.../toneforge_yt_6hc7qecw/Led Zeppelin - Stairway To Heaven (Official Audio).wav...`. **No log line in the entire 14 833-line captured stdout names `Demucs`, `htdemucs`, `separate_all_stems`, or `separate_guitar` — Demucs is silent at INFO level.**
- Two `WatchFiles detected changes in 'song_form_phase0c/run_phase0c.py'. Reloading...` events at log lines 13617 and 14230 — the reloader restarted the server between the two analyses and again after the first one completed.

### 4.2 Reconstruction — Standard run (POST returned 200 at log line 14178)

| Step | Event                                                                                                  | Evidence                                                  |
|------|--------------------------------------------------------------------------------------------------------|-----------------------------------------------------------|
| 1    | Client POSTs to `/api/analyze-url`. Request body — given the task name "Trigger analysis: Stairway to Heaven" — almost certainly omits `fast_mode`. | `tone_forge_api.py:3167` default applies → `fast_mode=True`. |
| 2    | Handler picks `PipelineConfig.fast()`.                                                                 | `tone_forge_api.py:4443`.                                  |
| 3    | yt-dlp downloads preview to `toneforge_yt_*/Led Zeppelin - Stairway To Heaven (Official Audio).wav`.   | Filename pattern matches `_download_youtube_audio` template `tone_forge_api.py:4369`. |
| 4    | `analyze_streaming` loads audio (line 578) and calls `_detect_content` (line 587).                      | Bundle records `detected_type="guitar"` — auto_detect ran. |
| 5    | Gate at `unified_pipeline.py:602`: `separate_stems=False` → `should_separate=False`. `_separate_stems` skipped.                                          | Bundle has `stems_paths=null`.                             |
| 6    | `_analyze_instruments` runs with `stems={}`. Guitar branch picks `source_kind="full_mix"`.              | `unified_pipeline.py:1147-1149`.                           |
| 7    | `analyzer.analyze(...source_kind="full_mix")` calls `stem_separator.separate_guitar(path)`.            | `analyzer.py:168`.                                         |
| 8    | `separate_guitar` runs htdemucs (4-stem), finds no `'guitar'` source, falls through to `'other'`, writes `<title>_other.wav`. | `stem_separator.py:156-173`, on-disk timestamp 14:57.       |
| 9    | Pipeline continues, builds 32 sections, returns 200.                                                   | Log line 14178.                                            |

The 297-second wall time is consistent with the YouTube download + Demucs inference inside `separate_guitar` + sectioning + MIDI extraction. **Demucs *was* invoked**, just through the hidden guitar-analyzer path, not through the top-level `_separate_stems` path.

### 4.3 Reconstruction — Deep retry (POST returned 200 at log line 14794)

The deep retry behaves identically because the request body still omits `fast_mode`. The handler at `tone_forge_api.py:4443` short-circuits on `fast_mode=True` before the `analysis_mode=="deep"` check at line 4445 ever runs. `PipelineConfig.deep()` is never invoked. The same `separate_guitar` path runs again at 15:09, producing a second `_other.wav` in a new `toneforge_stems_*` dir. Wall time 388 s is longer than the standard run mostly because of `_MAX_PREVIEW_DURATION=300` second clip length plus Demucs inference plus MIDI extraction on a slightly slower server start.

### 4.4 Demucs execution status (Q2 from the directive)

| Question                              | Answer                | Evidence                                                                                       |
|---------------------------------------|-----------------------|------------------------------------------------------------------------------------------------|
| Was Demucs invoked?                   | **YES** (in `separate_guitar`) | On-disk `_other.wav` is a Demucs source file; the file size + naming pattern fits the htdemucs `'other'` source output. |
| Was Demucs invoked as `_separate_stems` (the top-level pipeline stage)? | **NO**                | Bundle's `stems_paths` is `null` → top-level stage did not run.                                |
| Did Demucs return?                    | **YES**               | `separate_guitar` finished and wrote its output; `_analyze_instruments` and downstream stages completed; 200 returned. |
| Did Demucs throw?                     | **NO**                | No exception trace in log; pipeline reached `_build_result`.                                   |
| Was Demucs skipped entirely?          | **At the top level: yes.** At the guitar-analyzer level: no. |                                                                |

### 4.5 File-watcher contribution (Q5 from the directive)

The watcher restarted the server twice during these analyses (log lines 13617 and 14230). Both restarts happen **outside** the Demucs window for the respective runs — log line 14238 (Stairway MIDI prediction) is on the post-restart server process [57752], i.e. the deep retry started cleanly after the restart. The 5-min `_other.wav` files were written successfully by the prior process.

**Could a watcher reload leave only `_other.wav` behind, for a different run?** Yes, hypothetically. The path is:

1. Top-level `_separate_stems` is invoked (requires `should_separate=True`, i.e. fix the bypass).
2. The htdemucs_6s loop at `stem_separator.py:250-256` iterates through `model.sources` in order `['drums','bass','other','vocals','guitar','piano']`.
3. A reload kills the worker after `'drums.wav'` and `'bass.wav'` and `'other.wav'` have been flushed but before `'vocals.wav'`/`'guitar.wav'`/`'piano.wav'`.
4. The result on disk would be `_drums.wav` + `_bass.wav` + `_other.wav`, **not `_other.wav` alone**.

So the watcher cannot produce the observed single-`_other.wav` artefact. It remains a real risk for actual deep-mode runs once the request-mode bug is fixed.

---

## 5. Recommended fix (description only — not implemented per directive)

The minimal corrective change has three independent parts. Each is small and each addresses one of the stacked root causes; they are independently green and independently committable.

### 5.1 Fix the request-body default — primary

**Goal:** ensure `analysis_mode="deep"` actually reaches `PipelineConfig.deep()` through `/api/analyze-url`.

**Smallest change:** in `backend/tone_forge_api.py`, change the mode-selection ladder so `analysis_mode` is consulted **before** `fast_mode` when `analysis_mode` is explicitly set to `"deep"` or `"studio"`. Equivalent fix: change `UrlAnalyzeRequest.fast_mode` default from `True` to `False`, document the change in the request model, and audit existing clients (jam.html, debug.html, any Connect client) for places that relied on `True` as default. The latter is the more surgical fix because it does not change behaviour for any client that explicitly sends `fast_mode=True`.

**Files touched (estimated):**
- `backend/tone_forge_api.py` — 1 line (UrlAnalyzeRequest field default) OR 4-5 lines (ladder reordering).
- `backend/static/jam.js`, `backend/static/debug.js` (any other static client) — confirm they set `fast_mode` explicitly. Add the explicit value if absent.

**Verification:** existing `tests/test_pipeline_config_stem_url_base.py` covers `PipelineConfig.deep()` config shape. A new request-level test should POST `{"url": "https://...", "analysis_mode": "deep"}` (no `fast_mode`) and assert the resolved config has `force_stem_separation=True`. No mocking of Demucs required; the test asserts on the config object.

### 5.2 Add a corpus-expansion runner that bypasses the dev-server entirely — secondary

**Goal:** make the corpus expansion that Phase 0C needs survivable.

**Smallest change:** a single CLI under `backend/bench/corpus_expand.py` (or similar) that:

1. Accepts a YouTube URL + slug.
2. Calls `_download_youtube_audio` directly.
3. Constructs `PipelineConfig.deep()` (explicit, no HTTP transit).
4. Runs `pipeline.analyze(audio_path, config)` synchronously.
5. Writes the resulting bundle into `backend/data/history.json` (or a `phase0d/` sibling directory).

Run it from a non-watched shell. No FastAPI, no uvicorn, no WatchFiles. The watcher problem disappears for corpus-expansion jobs because the watcher is never in the process tree.

**Files touched (estimated):**
- New file `backend/bench/corpus_expand.py` (~60 lines).
- No edits to `unified_pipeline.py`, `stem_separator.py`, `auto_detect.py`, or any analysis module.

### 5.3 Defensive logging — diagnostic, optional

**Goal:** future failures of this class should be visible in the log.

**Smallest change:** add one `logger.info(...)` call at `unified_pipeline.py:603` recording the resolved gate state (`config.separate_stems`, `detection.is_full_mix`, `config.force_stem_separation`, `should_separate`). Today the log shows MIDI prediction but is silent on the separation decision; this single line would have made Phase 0D's investigation a 5-minute search instead of a multi-agent trace.

**Files touched:**
- `backend/tone_forge/unified_pipeline.py` — 1 line added.

### 5.4 What this fix does **not** do

The recommended fix does not:

- Modify the chord detector, song-form classifier, guidance-mode logic, or any signal-similarity code.
- Modify `auto_detect.py`. The current detection-gate behaviour for `PipelineConfig.standard()` is preserved; users who want forced separation must explicitly request deep mode.
- Modify `stem_separator.separate_guitar`. Its single-stem write is intentional for the guitar-analyzer fast path. The fix relies on never reaching it from the corpus-expansion entry point.

---

## 6. Success-criteria recap

| Criterion (from directive)                                            | Answer                                                                                                                   |
|-----------------------------------------------------------------------|--------------------------------------------------------------------------------------------------------------------------|
| Why can analysed bundles exist without stems?                         | `PipelineConfig.fast()` and `PipelineConfig.standard()`-with-detected-non-mix both produce empty `stems`. `stems_paths` is set only from the top-level `stems` dict (`unified_pipeline.py:1843-1855`). |
| Why did deep mode fail to force separation?                           | The `/api/analyze-url` handler's mode-selection ladder checks `fast_mode` **before** `analysis_mode`. With `fast_mode=True` default and the client omitting that field, the ladder short-circuits to `.fast()` and never reads `analysis_mode`. |
| Is the blocker configuration / routing / pipeline logic / Demucs execution / process lifecycle? | **Routing** (the handler ladder is the gate). Secondary: **pipeline logic** (the detection gate would still skip separation for guitar-detected songs even after the routing fix, unless deep mode is explicitly requested). The artefact appearance is from **pipeline logic** in the guitar-analyzer fast path. **Not** Demucs execution (Demucs ran successfully inside `separate_guitar`). **Not** process lifecycle (watcher restarts were observed but do not match the artefact pattern). **Not** configuration in the dataclass sense — the `PipelineConfig` factories are correct as written. |
| Primary root cause                                                    | `fast_mode=True` default + ladder-order bug at `tone_forge_api.py:4443`.                                                  |

---

## 7. Artefacts

- This document: `phase0d_stem_bundle_blocker.md`.
- Supporting forensic data already on disk:
  - `backend/data/history.json` — Stairway entries `6a18dbfa`, `c02cedbd`.
  - `/var/folders/t9/s8pg2yfx3g73nt0lzf6p40xc0000gn/T/toneforge_stems_*/Led Zeppelin - Stairway To Heaven (Official Audio)_other.wav` — four instances spanning Jun 18 and Jun 20.
  - `/tmp/claude/.../bebc762.output` — captured server stdout (14 833 lines).
- No new code, no new tests, no new bundles produced by Phase 0D.

---

## 8. Out of scope (explicit, to honour the directive)

This document does **not** propose, design, sketch, or implement:

- Any change to `auto_detect.py`, `chord_detector.py`, `chords.py`, `sections.py`, or any analysis module.
- Any change to `stem_separator.py` itself. `separate_guitar` is intentionally a single-stem writer and is left alone.
- Any classifier, guidance-mode, song-form, threshold, or UI change.
- Any change to the on-the-wire bundle schema.
- Any commit. Phase 0D is measurement and localisation only. The Recommended Fix in §5 is descriptive, not implemented.
