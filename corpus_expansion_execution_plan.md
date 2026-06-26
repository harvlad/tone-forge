# Corpus Expansion — Execution Plan

> **Scope.** Planning and validation only. No production code changes,
> no threshold tuning, no classifier work, no SSM design. The goal of
> this document is to ensure that when Phase 0B signal discovery is
> re-run against an expanded corpus, the corpus foundation is
> trustworthy.
>
> **Inputs.** Phase 0B (`song_form_signal_discovery.md`), Phase 0C
> (`song_form_phase0c.md`, `song_form_similarity_feasibility.md`),
> Phase 0D forensics (`phase0d_stem_bundle_blocker.md`) and
> implementation (`phase0d_implementation_report.md`,
> `backend/bench/corpus_expand.py`,
> `backend/tone_forge/unified_pipeline.py:select_pipeline_config`),
> the trial-corpus fixture
> (`backend/tests/fixtures/song_trial_corpus.json`), and the
> Phase 0B coverage record
> (`backend/song_form_signal_discovery/corpus_coverage.json`).

---

## A. Corpus composition

### A.1 Selection rubric

A song earns a slot only if it (a) contributes a *structural pattern*
not already covered, (b) addresses an *operational risk* the
calibration corpus must surface before the classifier is built, or
(c) is required by Phase 0B's existing decision (`D + F` substrate
validation across genres). Songs are otherwise additive cost without
calibration value.

### A.2 Recommended minimum expansion set (7 songs)

| # | Slug | Status today | Structural pattern | Why needed | Risk de-risked |
|---|---|---|---|---|---|
| 1 | `stairway_to_heaven` | **Already analyzed** as bundle `73b5931b` (Phase 0D validation, 6 stems, 90 s window) | Dynamic-arc through-composed; quiet-intro → loud-outro | Re-uses Phase 0D's existing validation bundle as the "known-good" reference for the expanded corpus. No re-analysis required if stems still on disk; flagged otherwise. | Validates the 0D fix end-to-end. Provides the 6-stem reference against which 4-stem fallbacks can be compared. |
| 2 | `hotel_california` | In trial corpus; never analyzed | Canonical verse-chorus-bridge with extended dual-lead outro | The most common rock structure. D + F should solve this trivially; failure here invalidates 0B's primary finding. | Confirms the baseline case. If D/F separability collapses on a clear verse-chorus song, the substrate isn't ready. |
| 3 | `wish_you_were_here` | In trial corpus; never analyzed | Through-composed with extended acoustic intro distinct from body | Tests F (drum density) as the primary signal when D (vocal RMS) is degraded by vocal/acoustic bleed in Demucs. Strategy §7.5 calls this out as the vocal-stem-quality risk. | Confirms F carries weight independently when D is noisy. Without this, every D-driven decision in 0C is single-signal-fragile. |
| 4 | `romance_de_amor` | In trial corpus; never analyzed | Solo polyphonic nylon guitar, AB modulation, fully instrumental | Trial corpus explicitly flags it as the **critical edge case** (`song_trial_corpus.json:83`): "polyphonic guitar — melody floats above accompaniment in the same stem. Classifier trichotomy DOES NOT cleanly apply." D has no vocal stem; F has no drum stem. | Surfaces the "D-and-F both silent" failure mode *before* the classifier ships. If the discovery script produces NaNs or near-saturation on this, signal handling needs an explicit "fall through to `unknown`" path. |
| 5 | `whats_my_age_again` | **Already analyzed** as bundle `29b31695` (Phase 0B) | Verse-chorus pop-punk with EDM-vocabulary "drop" sections | Phase 0B's second corpus song; D separability landed at 0.58. Re-confirm post-0D that its bundle still satisfies the 4-stem floor. | Locks the Phase 0B baseline. If the bundle's stems no longer round-trip after 0D, signal discovery cannot be replicated. |
| 6 | `rumjacks_irish_pub_song` | Not analyzed; surfaced as the screenshot regression case in this conversation | Refrain-driven Celtic folk with energy-fade outro; dynamic-range rock instrumentation | The known failure case (intro / buildup / verse / unknown for a 4-minute song with no chorus). This is the regression target a real classifier must beat. | Without it, "did the new classifier actually fix the Rumjacks failure?" is unanswerable. With it, that question is a single corpus assertion. |
| 7 | `seven_nation_army` | Not analyzed | Riff-loop with minimal harmonic variation; structural change via drum entry/exit | Phase 0B §9.1 explicit gap: no riff-loop song in the current set. Chord SSM saturates on this form; D + F is the *only* way to find structure. | Confirms D + F still works when the chord progression is constant. Without it, riff-loop songs are an unmeasured failure mode for the classifier. |

### A.3 Held in reserve (analyze if budget permits)

| Slug | Pattern | Why optional |
|---|---|---|
| `yesterday_beatles` | AABA acoustic-with-vocal | 0B §9.1 names AABA as a missing form. Lower priority than the 7 above because Stairway's loose AABA-expansion partially covers it. |
| `disco_of_doom` | Project original; segmenter test song | Already used in `segmenter_followup_probes.md` boundary work. **No ground-truth section labels exist** — would have to be hand-labeled before becoming calibration-useful. Defer until a separate labeling pass. |
| `simulated_life` | Project original; segmenter test song | Same status as `disco_of_doom`. No ground-truth labels in any fixture. |
| `demolition_warning`, `jump_and_die`, `lets_make_it_pain`, `pub_feed` | Project originals with **chord** ground truth at `backend/tests/fixtures/chord_groundtruth/` | Chord-level ground truth exists but no section-level ground truth. Useful for chord-detector regression, not for song-form calibration. Defer. |

### A.4 Songs deliberately excluded

| Slug | Why excluded |
|---|---|
| `sex_on_fire` (b640c78a) | Already in 0B baseline. Not in the *expansion* set; treated as a fixed reference point that must not regress. |

### A.5 Composition summary

- **Hard floor:** 7 songs (the table in A.2).
- **Forms covered:** through-composed, verse-chorus-bridge,
  acoustic-vocal-bleed, instrumental-polyphonic, EDM-flavored
  pop-punk, refrain-folk-with-fade, riff-loop. Six distinct
  structural patterns — sufficient for Phase 0C's "≥4 forms"
  precondition.
- **Pre-analyzed reuse:** 2 of 7 already exist as bundles
  (`stairway_to_heaven` 73b5931b, `whats_my_age_again` 29b31695)
  if their stems survive on disk; otherwise re-analyze.
- **New analyses required:** 5 of 7 (`hotel_california`,
  `wish_you_were_here`, `romance_de_amor`, `rumjacks_irish_pub_song`,
  `seven_nation_army`).

---

## B. Persistence strategy

### B.1 Findings

| Question | Answer | Evidence |
|---|---|---|
| Where does `corpus_expand.py` write stems? | Into the macOS ephemeral tempdir (`/var/folders/.../T/toneforge_stems_<rand>/`). The runner does **not** pass `output_dir` to the separator; it accepts the default. | `backend/bench/corpus_expand.py` makes no `output_dir=` argument; `stem_separator.py:95, 213, 292, 386` all default to `Path(tempfile.mkdtemp(prefix="toneforge_stems_"))`. |
| Do those stems survive long enough for later phases? | **No guarantee.** macOS evicts `/var/folders/.../T/` on reboot and may purge it sooner. Phase 0B's `corpus_coverage.json` explicitly flags this: *"Stem directories are in /var/folders (macOS tempdir), which can be evicted... If a future run finds the stem dirs missing, the songs need to be re-analysed."* | `backend/song_form_signal_discovery/corpus_coverage.json:42`. Also, 30+ `toneforge_stems_*` dirs are visible in `/var/folders/.../T/` right now — accumulated across past runs with no eviction policy beyond the OS's. |
| Does the bundle's `stems_paths` field point to those tempdir paths? | **Yes**, directly. The Stairway validation bundle `73b5931b` records `stems_paths` entries like `.../toneforge_stems_57ky1gd8/...wav` — when that tempdir is evicted, the bundle becomes a dead pointer. | `phase0d_implementation_report.md:217-225`. |
| Can signal discovery reliably consume them? | **No.** Phase 0B already ran into this and worked around it by being run *before* eviction. The pattern is not reproducible on a fresh checkout, after a reboot, or on a CI box. | Same coverage JSON note + first-hand 0B execution constraint. |

### B.2 Recommendation: **Option B — persist stems to a stable corpus location before expansion proceeds.**

The persistence question is binary, and Option A (current state) was
already rejected by the team writing Phase 0B. Re-running signal
discovery on tempdir-resident stems is a reproducibility hazard, not
a calibration baseline.

### B.3 Proposed directory structure

```
backend/data/corpus_stems/
├── _meta.json                       # provenance + integrity index (committed)
├── stairway_to_heaven/              # one dir per slug (NOT committed)
│   ├── source.wav                   # the yt-dlp output that was analyzed
│   ├── drums.wav
│   ├── bass.wav
│   ├── vocals.wav
│   ├── other.wav
│   ├── guitar.wav                   # 6s only; absent on 4-stem fallback
│   └── piano.wav                    # 6s only
├── hotel_california/
│   └── ...
└── ...
```

`_meta.json` schema (committed; the source-of-truth provenance file):

```json
{
  "schema_version": 1,
  "songs": {
    "stairway_to_heaven": {
      "bundle_id": "73b5931b",
      "source_url": "https://www.youtube.com/watch?v=QkF3oxziUI4",
      "duration_s": 90,
      "downloaded_at": "2026-06-20T16:01:08",
      "analysis_mode": "deep",
      "demucs_model": "htdemucs_6s",
      "stem_floor_satisfied": true,
      "stems": {
        "drums":  {"sha256": "...", "bytes": 15874900},
        "bass":   {"sha256": "...", "bytes": 15874900},
        "vocals": {"sha256": "...", "bytes": 15874900},
        "other":  {"sha256": "...", "bytes": 15874900},
        "guitar": {"sha256": "...", "bytes": 15874900},
        "piano":  {"sha256": "...", "bytes": 15874900}
      },
      "source": {"sha256": "...", "bytes": 7937000}
    }
  }
}
```

### B.4 Lifecycle policy

- **Writes:** `corpus_expand.py` writes once per `--label`, into
  `backend/data/corpus_stems/<slug>/`. No automatic deletion.
- **Bundle pointers:** the bundle's `stems_paths` field must
  reference the stable path, not the tempdir. (This is a follow-up
  commit, not implemented as part of this plan.)
- **`.gitignore`:** add `backend/data/corpus_stems/*/` (the slug
  directories). Commit `backend/data/corpus_stems/_meta.json`. The
  audio is the team member's local artifact; the provenance is the
  shared truth.
- **Integrity check at signal-discovery time:** discovery scripts
  must read `_meta.json`, verify each declared file exists with the
  declared sha256, and refuse to proceed on mismatch.
- **Cleanup:** operator-only. No automatic eviction, no LRU.
- **Re-analysis:** a `--force` flag on `corpus_expand.py` overwrites
  an existing slug dir; otherwise the runner refuses to overwrite.

### B.5 What this plan does *not* change

- The macOS tempdir default in `stem_separator.py` stays as-is for
  non-corpus runs (HTTP-driven analyses). Persistence is a
  corpus-only concern.
- The `[stem-gate]` log line from Phase 0D is unaffected.
- The `select_pipeline_config` helper is unaffected.
- No change to `unified_pipeline.py`, `analyzer.py`, or any
  analysis module.

---

## C. Corpus acceptance criteria

Objective gates. The corpus is **complete and trustworthy** when
*all* of the following pass. Any failure blocks the next step
(per-stem `SectionFeatures` extension).

### C.1 Quantitative gates

| # | Gate | Pass condition | Fail condition |
|---|---|---|---|
| C.1.1 | **Song count** | ≥ 7 bundles tagged `corpus_run: True` in `backend/data/history.json` matching the slugs in A.2. | Fewer than 7 of the named slugs persisted. |
| C.1.2 | **Genre coverage** | At least one bundle for each of: verse-chorus-bridge, through-composed, instrumental-polyphonic, riff-loop, refrain-folk, acoustic-vocal-bleed. | Any of the six form categories has zero bundles. |
| C.1.3 | **Stem floor** | 100% of corpus bundles have `{drums, bass, vocals, other}` present as files at the paths declared in `_meta.json`. | Any bundle missing one of the four required stems. |
| C.1.4 | **6-stem preference** | ≥ 5 of 7 bundles have all 6 `htdemucs_6s` stems. | Fewer than 5 — investigate `htdemucs_6s` cache state. |
| C.1.5 | **Bundle ↔ stems linkage** | Every bundle's `stems_paths` field resolves to an existing file on disk at the time of acceptance. | Any bundle has a dead pointer. |

### C.2 Reproducibility gates

| # | Gate | Pass condition | Fail condition |
|---|---|---|---|
| C.2.1 | **Provenance completeness** | `_meta.json` carries `source_url`, `duration_s`, `analysis_mode == "deep"`, `demucs_model`, sha256 + bytes for every stem and the source WAV, for every slug. | Missing field for any slug. |
| C.2.2 | **Deterministic stems** | Re-running `corpus_expand` on the same source produces stem WAVs with sha256 identical to the first run. (Pin Demucs `shifts=0` — already done in commit `d59a6ea`.) | sha256 mismatch on any stem across two runs of the same source. |
| C.2.3 | **Deterministic boundaries** | Re-running produces the same `section_count` (boundary count). Detector internals are deterministic given identical input WAV. | Section count drift across runs of the same source. |
| C.2.4 | **`[stem-gate]` audit trail** | For every corpus run, captured stdout contains exactly one `[stem-gate] mode=deep ... should_separate=True` line. | Missing line, or `should_separate=False`, or `mode != deep`. |

### C.3 Calibration-readiness gates

| # | Gate | Pass condition | Fail condition |
|---|---|---|---|
| C.3.1 | **Phase 0B replication** | Re-running `backend/song_form_signal_discovery/run_discovery.py` against the expanded corpus reproduces D and F separability ≥ 0.55 on `sex_on_fire` and `whats_my_age_again`. | Either song's D or F separability collapses below 0.55. |
| C.3.2 | **Edge case handled** | `romance_de_amor` discovery output does not raise; vocal-stem D returns a defined value (typically near-zero across all sections) rather than NaN. | NaN, divide-by-zero, or unhandled exception in discovery on this song. |
| C.3.3 | **Regression visibility** | `rumjacks_irish_pub_song` bundle exists and is loadable; its current heuristic labelling can be compared against any future classifier output. | Bundle missing — the regression target becomes unmeasurable. |

### C.4 The single-sentence pass criterion

> **A reviewer can re-run `run_discovery.py` from a fresh checkout on
> a different machine, and produce identical separability statistics
> to the operator's run.**

If that statement is true, the corpus foundation is trustworthy. If
it is false for any reason — stems evicted, sha256 drift, missing
slug, dead pointer in a bundle — the corpus is not ready for the
per-stem `SectionFeatures` extension to land on top of it.

---

## D. Risks

Only risks that can **invalidate signal-discovery work**. Classifier-
design risks are out of scope per the directive.

### D.1 Ranked risks

| # | Risk | Mechanism | Triggers Failure Mode | Mitigation (this plan) |
|---|---|---|---|---|
| D.1 | **Stems evicted between corpus expansion and signal discovery.** | macOS purges `/var/folders/.../T/toneforge_stems_*` on reboot or under disk pressure. | Discovery script can't read stems → empty SSMs → silent degradation of separability metrics. | §B.2 (Option B persistence). C.1.3 + C.1.5 catch dead pointers at acceptance. |
| D.2 | **Bundle's `stems_paths` records tempdir paths even after persistence move.** | Persistence writes to stable dir but the pipeline still records the temp path it actually wrote to. | Stems exist but discovery can't find them via the bundle. | C.1.5 catches this. Requires bundle-path rewrite in the follow-up persistence commit. |
| D.3 | **Demucs `htdemucs_6s` cache drift across machines.** | Local torch-hub cache may carry a different model checkpoint version than the operator's. | Discovery results aren't bit-comparable across reviewers; C.2.2 fails. | `_meta.json` records `demucs_model` string; out-of-band: lock the model checkpoint hash (follow-up). |
| D.4 | **yt-dlp picks a different audio variant on retry.** | `--audio-format wav --audio-quality 0` doesn't pin codec/bitrate of the source stream. YouTube may offer different opus/vorbis variants per request. | Source WAV sha256 changes → all downstream sha256s drift → C.2.2 fails on the source line. | `_meta.json` records source sha256; deterministic check at C.2.2 surfaces drift; operator's choice whether to accept or re-pull. |
| D.5 | **4-stem fallback for songs where `htdemucs_6s` import fails.** | `unified_pipeline.py:1018-1026` falls back to `htdemucs` (4 stems) on import failure. The fallback satisfies the floor but breaks 6-stem-dependent signals. | Bundles have inconsistent stem counts → Phase 0B signal E (instrumentation) becomes unstable across the corpus. | C.1.4 surfaces the 6-stem coverage ratio. ≥ 5 of 7 passes; below that triggers investigation. |
| D.6 | **`history.json` 100-entry cap silently evicts the Phase 0B baselines.** | `corpus_expand._add_to_history` calls `history = history[:100]` (line 114). With 100 entries already in history, every corpus run evicts the oldest entry. `sex_on_fire/b640c78a` and `whats_my_age_again/29b31695` are eviction-eligible. | Phase 0B reference bundles disappear; C.3.1 replication impossible. | **Operational** flag in the plan: before any corpus run, snapshot `history.json` and verify the two baseline bundle ids are preserved. Long-term: separate corpus store (out of scope here). |
| D.7 | **`romance_de_amor` produces NaN in D or F.** | Polyphonic instrumental → silent vocal stem → vocal RMS is 0 everywhere → 1 − (Δ/max(0,0,ε)) → defined but degenerate. Similar concern for F on songs with no drum onsets. | Discovery script error or undefined matrix entries → corrupted aggregate stats. | C.3.2 makes this an acceptance gate, not a runtime surprise. |
| D.8 | **Source duration drift.** | `corpus_expand` defaults `--duration 60`; Stairway validation used 90. Different durations produce different section counts (the report flags 32 vs 12 for Stairway at 30s vs 90s). | Cross-song comparisons become apples-to-oranges; section-aligned SSM cells reference different musical content. | Pin `duration_s` per slug in the corpus-composition plan (recommend 90 s for songs > 4 min, full track for shorter); record in `_meta.json` C.2.1. |
| D.9 | **Operator-side disk pressure on `backend/data/corpus_stems/`.** | 7 songs × ~16 MB/stem × 6 stems ≈ 700 MB. Not large by absolute standards, but `.gitignore`d files have no LFS protection. | Operator accidentally commits the WAVs, or accidentally deletes them. | `.gitignore` rule in §B.4. `_meta.json` sha256 verification re-detects deletion via C.1.3. |

### D.2 Risks deliberately out of scope

- **Classifier calibration risk** (over-fitting thresholds to 7 songs). Belongs to Phase 0C's classifier-build step, not here.
- **Demucs vocal-isolation quality on bleed-heavy songs** (Wish You Were Here). Surfaces during signal discovery; mitigation belongs to classifier design, not corpus expansion.
- **Section-detector boundary quality** (the Rumjacks "unknown" failure). The classifier's job to fix; corpus expansion only needs the bundle to exist so the failure is measurable.
- **C5/D5 chord-detector noise**. Different pipeline (`chord_detector`); not relevant to song-form substrate.
- **`detected_type` operational bias**. Phase 0D bypasses it for corpus runs via forced `deep()`; not a corpus-expansion risk.

### D.3 The one risk that, if unmitigated, invalidates everything else

**D.1 (stems evicted)**. Every other risk produces a recoverable
discrepancy or a quantifiable degradation. D.1 produces the silent
failure where the corpus appears to exist (bundles persisted,
provenance recorded) but cannot be consumed, and re-execution is the
only recovery. Mitigating it (Option B persistence) is the gating
prerequisite for everything in the rest of the roadmap.

---

## Appendix: ordered next actions implied by this plan

These are **planning observations**, not implementation commitments.
They follow directly from §A, §B, and §C.

1. Implement the persistence change in `corpus_expand.py` (pass
   `output_dir=corpus_stems_dir / slug` to the separator; write
   `_meta.json`). Required by D.1.
2. Add the `.gitignore` rule for `backend/data/corpus_stems/*/`.
   Required by D.9.
3. Snapshot `backend/data/history.json` before any corpus run.
   Required by D.6.
4. Verify Stairway validation bundle `73b5931b` and Sex On Fire
   `b640c78a` are still pointable (stems-on-disk) *or* schedule
   their re-analysis under the persistence policy. Required by
   §A.2 entries 1 + 5 + C.3.1.
5. Execute corpus expansion against the 7 slugs in §A.2,
   honoring `_meta.json` provenance writes per §B.3.
6. Verify C.1, C.2, C.3 gates pass.
7. Re-run `backend/song_form_signal_discovery/run_discovery.py`
   against the expanded corpus and confirm Phase 0B's D and F
   findings replicate.

Steps 5–7 are the corpus-expansion execution; steps 1–4 are the
prerequisites this plan identifies.
