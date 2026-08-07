# Riley Integration — Status & Remaining Gaps

Riley (the `tone_forge/performance` Musical-Graph engine) is being wired in as
Jamn's canonical musical-intelligence layer. Production separator is **untouched**
(stock `htdemucs_6s`); this work is entirely downstream of separation. No
training / benchmark / separator-promotion work.

## Done (branch `feat/riley-integration`)

| Area | Change | Commit |
|---|---|---|
| **Launchpad (primary)** | `/chops` phrase mode sources ranked, grid-aligned, loop-scored `PerformanceAsset`s from the Musical Graph (with each loop's optimized seam + crossfade) instead of RMS/time slicing. Non-breaking fallback to legacy slicers when no graph. | `8b0c6dd` |
| **Content-addressing** | Worker now sets `content_hash` (source-audio sha256) before graph derivation — previously the graph cached under the literal `"pending"`, defeating deterministic/replayable caching. | `cc78d29` |
| **Canonical understanding** | `SongUnderstanding.motifs` (previously an empty seat with no producer) now populated from Riley's discovered Patterns via `_build_motifs`. Connected the dead `to_motifs`/`motifs_for` bridge. | `cc78d29` |
| **Gapless playback** | Shared `Chop` model (mobile + desktop) decodes Riley loop fields; desktop Launchpad trigger uses `chop.loopable` + measured `chop.crossfadeMs` instead of a kind-heuristic + hardcoded 15 ms. | `aad32f4` |

Tests: `tests/test_contribute_chops_riley.py` (3), `tests/test_bundle_motifs.py`
(2), `tests/test_performance.py` (6) — all green.

## Duplicate analysis found by the audit

These are places Jamn derives musical info independently of Riley. Migrated where
low-risk; the rest are documented here rather than removed blind (removing them
needs full-pipeline validation this environment can't run — chord-sync / result
parity regressions are the risk).

1. **Double beat-tracking** — `analysis_worker.py:~1118` runs a second raw
   `librosa.beat.beat_track` on the `other` stem for chord beat-snapping, duplicating
   `beat_tracking.track_beats` at `~1490`. Deduping means computing the mix beat grid
   *before* chord detection and reusing it. **Deferred:** the chord path runs first;
   reordering is a real pipeline change that needs an end-to-end regression run.

2. **Two parallel full pipelines** — `local_engine/analysis_worker.py::run_file_analysis`
   (worker/subprocess) and `tone_forge/unified_pipeline.py::UnifiedPipeline` (server) are
   independent reimplementations kept consistent by hand-cited line-parity comments.
   **Gap:** the true "one engine" end-state folds both behind a single canonical
   RileyAnalysisEngine. Large; out of scope for this milestone.

3. **Legacy chop slicers** (`contribute_chops._chops_from_vocal_phrases` / `_sections` /
   `_downbeats`) are now **fallback-only** when a graph is present. They can be retired
   once every live analysis reliably carries a `performance_graph`.

## Remaining hookups (small, deferred for build/test access)

- **Mobile gapless chops:** the mobile `/chops` path converts `Chop` → `SamplePad`; it
  should map `chop.loopScore` → `pad.loopScore` so the existing `SampleScheduler`
  crossfade path (`SampleScheduler.swift:578`) picks it up. Desktop already does this
  directly. (iOS build/test not available in this environment.)

## Hard locks (per spec)

- Production separator = stock `htdemucs_6s`, unchanged.
- `SeparatorProvider` (`tone_forge/separation/`) exists but is unplugged; every live
  path calls `separate_all_stems` directly. Wiring it is the clean future seam for
  separator promotion — **not** done here (no separator promotion this milestone).
