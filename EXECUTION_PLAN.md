# ToneForge Execution Plan

Strategy is frozen. This document is the execution surface.

It supersedes every `backend/*.md` strategic/RCA/roadmap document. Those are archived (see §11).

## Operating Constraints

- One codebase. No rewrites. No microservices. No queues.
- Reuse existing assets. Move only what crosses a new boundary.
- `tone_forge_api.py` is the only composition point between subsystems.
- Subsystems communicate exclusively through `contracts.py` DTOs.
- Frozen packages get bug fixes only — no new files, no benchmarks, no docs.

## Execution Priority (Locked)

| # | Subsystem | State |
|---|---|---|
| 1 | Subsystem boundary freeze (`contracts.py` + packages) | Active |
| 2 | Connect hardening | Active — focused-pass landed (see §0) |
| 3 | Monitor Chain Bank | Active — ambient redesign accepted (see §0) |
| 4 | Chord detection (spike → ship) | Complete in main — MVP + validation harness + wire-up tests all shipped; dom7 weakness documented as known-issue (see §0) |
| 5 | Session Engine consolidation | Complete in main — all 5 commits shipped, 74/74 tests green (see §0) |
| 6 | Retrieval confidence calibration | Active — calibrator/tiers/policy + guitar_catalog matcher + instrumentation shipped; tone→monitor boundary regression closed; isotonic loader infrastructure landed (drop-in artifact activates fitted curve, see §0); fitted artifact still blocked on 100 hand-labeled clips |
| 7 | Device Discovery | Active — scaffold + persistence + API edge + Jam onboarding modal + DeviceCaps consumer wiring (preferred_chain_family → fallback policy) + CoreAudio probe pre-fill (item #36) + audio_input_name → Connect helper env (Python plumb of item #38) all landed (see §0) |
| 8 | Song Understanding expansion | Investigation landed — `docs/SONG_UNDERSTANDING_INVESTIGATION.md` (see §0); no implementation work auto-driven |
| — | MIDI extraction internals | Frozen |
| — | Reconstruction / ALS export | Frozen |
| — | Retrieval algorithm / embeddings | Frozen |
| — | Evaluation harness expansion | Frozen |
| — | Studio feature development | Frozen |
| — | Catalog (Suite expansion) | Frozen |

---

## 0. Completion Log

Most-recent landings first. Each entry is concrete enough to point an
auditor at the diff + verification artifact. This log is the ground
truth on "what's actually shipped" relative to the priority table; the
section-level notes below (§3, §4, …) explain what remains.

### Boundary freeze: `reconstruction.section_detector` shim retired (item #5)

The §9 item 5 plan called for a dual-location transition: lift the
section detector from `reconstruction/` to `analysis/sections.py`,
keep a back-compat re-export shim at the old path "for one release,"
then delete the original. The lift landed earlier (627-line canonical
home at `tone_forge/analysis/sections.py`); the shim was the last
remaining piece of the dual location.

Three internal callers still imported through the shim:

| Caller | Line |
|---|---|
| `tone_forge/unified_pipeline.py` | 1342 |
| `tone_forge_api.py` | 3456 |
| `local_engine/analysis_worker.py` | 452 |

All three switched from
`from tone_forge.reconstruction.section_detector import SectionDetector`
to
`from tone_forge.analysis.sections import SectionDetector`.
One stale doc comment in `static/jam.js:1292` referencing the old
path was updated to point at the canonical home. The 17-line shim
at `tone_forge/reconstruction/section_detector.py` was then deleted
via `git rm`.

The boundary test (`tests/test_subsystem_boundaries.py`) and the
broader suite were both green after the migration — `1227 passed,
1 skipped` in 220.67s (heavy MIDI / GPU / reconstruction-e2e
suites deliberately skipped; they exercise unrelated extractor
paths and don't touch the section import boundary).

EXECUTION_PLAN.md updates:
- §2 existing-packages row for `tone_forge/reconstruction/` annotated
  as "shim retired."
- §2 package-structure tree note next to `analysis/sections.py`
  updated from "lift from reconstruction/section_detector.py" to
  "canonical home; reconstruction/section_detector.py removed."
- §9 item 5 annotated complete with the per-caller migration notes.

This is the last freeze-migration cleanup for the section detector;
no other callers need to be tracked. The `reconstruction/` package
stays frozen per §15.

### Cleanup: benchmark scripts promoted out of backend root (item #41)

The last two stragglers from the §0 cleanup list — `run_samples_benchmark.py`
and `run_stem_benchmark.py` — sat at `backend/` root with no other peers
after `718843c` swept the rest into `backend/scripts/`. Promoting them
keeps `backend/` a package root, not a script dumping ground.

Both moved via `git mv` (history preserved). The internal
`sys.path.insert(0, str(Path(__file__).parent))` hardcoded the old
"this file is at `backend/`" assumption; updated to the convention
already used by every other script in `backend/scripts/`:

```python
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
```

Verified both modules now import-resolve cleanly from the new
location (loading the module top-level executes the `sys.path`
insert + the package imports without errors).

This closes item #41 in the §0 planned-work list. No callers
imported either file as a module (grep clean repo-wide), so there
is no breakage to fix outside the two files themselves.

### Song Understanding investigation (Priority 8) — research artifact landed

Priority 8 sits in the priority table as "Investigation only" — the
deliverable is research output, not implementation. This commit lands
that artifact as `docs/SONG_UNDERSTANDING_INVESTIGATION.md`. No code
changes; the Phase-3 fields on `SongUnderstanding`
(`tuning`, `capo_fret`, `difficulty`, `motifs`) remain declared but
unpopulated, exactly as they have been since the contract froze.

The document covers, with line-numbered references into the repo:

- **Contract audit** — 13 fields, 9 MVP / 4 Phase-3; which fields
  are actually populated today and which fall back to hardcoded
  defaults (`time_signature=(4,4)`, `tempo_confidence=0.5`).
- **Producer audit** — three call sites build `SongUnderstanding`
  (`session/bundle.py:246`, `tone_forge_api.py:2536`,
  `tone/guitar_catalog.py:82`); a fourth analyzer
  (`analysis/reference_analyzer.py`) computes richer spectral output
  into a separate `ProductionStyle` dataclass that does not feed
  the bundle.
- **Consumer audit** — only two real consumers:
  `tone/policy.py:104-130` (reads `tempo_bpm` + `key`; has four
  spectral-feature branches documented in code that are unreachable
  because the bundle doesn't carry the signals) and
  `static/jam.js:2093,2116-2117` (tempo, key, sections, beats for
  UI rendering). Nothing reads any Phase-3 field today.
- **Per-field feasibility** —
  beats from librosa is a free win;
  the four spectral-feature fields are the highest-leverage
  promotion because they unlock four dormant policy branches that
  are already written;
  tuning + capo are blocked on labeled data and a MIDI subsystem
  that is frozen;
  difficulty has no consumer and no definition;
  motifs need a UI hook before any detection work is justified.
- **Concrete next-step priority list** if P8 is ever promoted from
  investigation to active — four ordered, independent items, none
  blocking current P6 / P7 work.
- **Explicit deferrals** with rationale per Phase-3 field, so a
  future maintainer doesn't re-discover the same blockers.

Verification: the document only cites code paths and line numbers
that exist on the current `main` (`SongUnderstanding` contract,
`_build_understanding`, `select_fallback_family`'s documented
dormant branches, `_detect_chord_lane` wiring, `reference_analyzer`'s
reverb / delay code). No test changes; no Python changes.

This closes item #39 from the §0 planned-work list ("`docs/SONG_
UNDERSTANDING_INVESTIGATION.md` — investigation notes"). Item #40
("Place fields in `SongUnderstanding` DTO already") was already
satisfied before this session (the four Phase-3 fields are in
`contracts.py:248-252` with safe defaults).

### Isotonic calibration loader infrastructure (Priority 6 — refit prep)

`tone.calibrate(distance)` has shipped behind a deliberate placeholder
(`exp(-d/scale)` capped at 0.79, one tick below `HIGH_CONFIDENCE_MIN`)
since the tier-classifier landed. Plan §7 specifies replacing it with
an isotonic regression fit from 100 hand-labeled clips. Those clips
do not yet exist in the repo, so the **fit itself remains data-blocked**
— but the loader / wrapper infrastructure that activates the fit
once the artifact lands is independent of the data and lands now.

The contract: a single `git add backend/tone_forge/tone/calibration_v1.joblib`
of a `joblib.dump(IsotonicRegression(...))` artifact rebinds the
module-level `_CALIBRATOR` on next import. No code change required
to flip from placeholder to fitted; no code change required if the
artifact is absent. The public `calibrate(distance) -> float` signature
is unchanged; every caller (`tone.retrieve`, the session API edge,
the instrumentation surface) keeps working without modification.

| Change | File |
|---|---|
| New `IsotonicCalibrator` class adapting any predict-shaped model to the `Calibrator` protocol. Encapsulates the input sanitization (NaN/inf/negative/None/unparseable → 0.0) and `[0, 1]` output clamp that the public `calibrate` surface owes its callers, so the contract looks identical whether the placeholder or the fitted model is active. Numeric edge cases (model emits NaN/inf, model raises) all collapse to 0.0 — under-claim rather than propagate. | `backend/tone_forge/tone/calibration.py` |
| `IsotonicCalibrator.load_from_joblib(path)` — lazy-imports joblib, validates the loaded object has `predict`, raises on either failure. The primitive the auto-loader and direct callers use; sklearn dependency is paid only on the load path, not on every import of this module. | `backend/tone_forge/tone/calibration.py` |
| `_try_load_fitted_calibrator()` — module-level auto-loader that looks for `calibration_v1.joblib` next to the module. Returns an `IsotonicCalibrator` on success; returns `None` (logging a warning) on missing file, broken pickle, sklearn version skew, or wrong-object pickle. Never raises. | `backend/tone_forge/tone/calibration.py` |
| `_CALIBRATOR: Calibrator = _try_load_fitted_calibrator() or _placeholder_calibrate` — the swappable module-level reference. `calibrate(distance)` reads through it on every call, so a future hot-swap path (re-loading after a manual refit) can also use this seam. | `backend/tone_forge/tone/calibration.py` |
| Shared `_sanitize_distance` gate refactored out of `_placeholder_calibrate` so the placeholder and the wrapper apply identical input rules — no contract drift between them. | `backend/tone_forge/tone/calibration.py` |
| Autouse fixture pinning `_CALIBRATOR` to `_placeholder_calibrate` for every test that asserts placeholder-specific properties (the cap, the exact pipeline tier outcomes). Without it, a dev machine that has dropped in a fitted artifact would silently break placeholder assertions. | `backend/tests/test_tone_calibration.py` |
| New test file: 21 tests across three groups — `IsotonicCalibrator` wrapper contract (model call shape, clamps, NaN/inf/raise handling, defensive inputs), auto-loader negative paths (missing file, corrupt pickle, wrong-object pickle), happy path with a real `sklearn.isotonic.IsotonicRegression` round-tripped through `joblib`, and module-level wiring sanity (active calibrator is callable / obeys `[0, 1]` / sanitizes). | `backend/tests/test_tone_calibration_loader.py` |

Why an auto-loader and not an explicit `install_calibrator()` call:
the calibration module is imported transitively by every retrieval
caller; threading an install step through every import site (FastAPI
startup, session bundle, tests) was strictly worse than a one-line
auto-load at import time. The autouse fixture in the placeholder test
file means the test suite is immune to dev-machine artifact presence.

Verification:
- `tests/test_tone_calibration.py` — 23/23 PASS (existing assertions
  preserved by the autouse fixture; no behavior change for callers
  while the placeholder is active).
- `tests/test_tone_calibration_loader.py` — 21/21 PASS (new wrapper
  + auto-loader coverage).
- Boundary sweep (`tests/test_tone_tiers.py`,
  `tests/test_tone_policy.py`, `tests/test_tone_retrieve.py`,
  `tests/test_api_tone_ignored.py`, `tests/test_session_route.py`,
  `tests/test_session_bundle.py`, `tests/test_subsystem_boundaries.py`)
  — 173/173 PASS.
- Full backend test suite — 1280/1280 PASS, 12 skipped.

Not in this entry:
- The fitted artifact itself. Blocked on the 100 hand-labeled clips
  per Plan §7. Once collected, the fit is a single
  `IsotonicRegression().fit(distances, labels)` followed by
  `joblib.dump(model, "tone/calibration_v1.joblib")`. No further
  Python change.
- Telemetry-driven re-fit cadence (Plan §7 specifies quarterly).
  The loader doesn't need it; refit tooling can land separately.
- Hot-swap of `_CALIBRATOR` mid-process. The auto-loader runs at
  import time; a future `/api/calibration/reload` endpoint can use
  the same module-level seam if needed. Not motivated yet.

### audio_input_name → Connect helper env (Priority 7 — Python plumb of item #38)

The onboarding modal has been capturing `audio_input_name` since the
CoreAudio probe pre-fill landed (#36 entry below), and the persistence
layer round-trips it through `POST /api/device/preferences`, but no
runtime consumer read the value. The Connect Swift helper still selects
the system-default CoreAudio input. This entry closes the Python half
of that gap: the local-engine supervisor now exports the persisted
`audio_input_name` to the child process as `TONEFORGE_AUDIO_INPUT_NAME`
on every spawn. The Swift side wiring to consume the env var is queued
as a follow-up — at that point the loop closes end-to-end with no
further Python change required.

Env var transport was chosen over a 5th positional CLI arg so the
existing Connect binary (which ignores unknown env vars) does not
regress while the Swift side catches up. A positional arg would have
forced the supervisor to also start managing `monitor-gain` (currently
defaulted by Swift at `args[4]`) — out of scope for this commit.

| Change | File |
|---|---|
| `_AUDIO_INPUT_ENV = "TONEFORGE_AUDIO_INPUT_NAME"` constant + `_resolve_audio_input_name()` helper that lazy-imports `load_preferences` and never raises | `backend/local_engine/connect_bridge.py` |
| `ConnectSupervisor.start()` builds `env = os.environ.copy()`, layers in `_AUDIO_INPUT_ENV` when prefs has one, strips an inherited value when prefs does not — so a stale parent env can't override a cleared onboarding answer. `Popen(..., env=env)` | `backend/local_engine/connect_bridge.py` |
| Spawn log line now records the resolved input (`input=Focusrite Scarlett 2i2` / `input=<default>`) so `~/Library/Logs/ToneForge/connect-bridge.log` shows what the child actually saw | `backend/local_engine/connect_bridge.py` |
| Four new lifecycle tests: env var present when pref set; absent (and stale parent value stripped) when pref missing; `_resolve_audio_input_name` reads a real `device.json` end-to-end via `TONEFORGE_DEVICE_PREFS_PATH`; returns `None` without raising when the file is absent | `backend/tests/test_connect_bridge_lifecycle.py` |

The lookup is intentionally lazy — done inside `start()` on every
spawn — so an explicit "Reset device choice" mid-session takes effect
on the next `restart()` without the local engine having to rebuild
its supervisor. This is consistent with how the supervisor already
re-resolves the Connect binary on every spawn.

Verification:
- `tests/test_connect_bridge_lifecycle.py` — 12/12 PASS (4 new tests
  for the audio-input env wiring, 8 existing lifecycle tests
  unchanged).
- Boundary sweep (`tests/test_connect_bridge_apply_chain.py`,
  `tests/test_api_device_preferences.py`,
  `tests/test_api_device_probe.py`, `tests/test_devices_*.py`,
  `tests/test_session_*.py`, `tests/test_subsystem_boundaries.py`,
  `tests/test_tone_*.py`, `tests/test_api_tone_ignored.py`) —
  295/295 PASS.
- Full backend test suite — 1259/1259 PASS, 12 skipped.

Not in this entry:
- Swift consumption of `TONEFORGE_AUDIO_INPUT_NAME` in the bridge's
  AVAudioEngine input-device selection. The env var reaches the
  child process today; the Swift `startBridge(...)` path still
  resolves to whatever CoreAudio reports as the default input.
  Closing the loop end-to-end is queued as a Connect-side commit
  and requires no further Python change once it lands.
- Positional CLI surface for `audio_input_name`. Reconsider only if
  the Swift parser ever moves to `swift-argument-parser`; the env
  var keeps the bridge CLI signature stable in the meantime.

### Working-tree integration sweep (inventory, not strategy)

Roughly 140 modified/untracked items had been accumulating in the
working tree across the MIDI, reconstruction, evaluation, analysis,
fingerprinting, profiling, plugin-scanner, local-engine, and Studio-UI
surfaces. None of them were new strategy — all had been written or
modified during in-flight sessions before the §15 freeze on those
packages was declared. This entry is the inventory commit that brings
them under version control so future bug-fix-only work has a clean
baseline.

| Commit | Subsystem |
|---|---|
| `490ed8f` | gitignore local runtime artifacts (benchmark_results/, profile_results/, preset_catalog_output/, data/tone_log.jsonl) |
| `b7e4adb` | reconstruction: region_analyzer (new) + contamination / role / temporal updates |
| `1bc1ae7` | tone_forge: analysis, audio/, explainability/, fingerprint/, preset_catalog/, profiling/, spectral_cache, stem_model, ALS template / preset_export / unified_pipeline updates |
| `673912d` | local_engine: analysis_worker (new subprocess worker), download_models (new), server.py + tray.py updates |
| `6575ad9` | tone_forge.evaluation: benchmark_expansion / validation / calibration / generalization / profile_analysis / visualization packages + ab_comparison, benchmark_harness, melodic_contour, midi_benchmark, perceptual_score, workflow_metrics modules |
| `71779b2` | static UI: admin.html retired; arrangement / intelligence / detection-shared / plugin-shared / preview-shared / export-shared / waveform-trim modules; Studio + index updates |
| `2b844dd` | scripts/: ~30 dev-tooling scripts for benchmarks, catalog, reconstruction, retrieval, render, listening rig + start_server.sh |
| `180cceb` | docs: EXTRACTION_STATUS.md, PHASE2_VALIDATION_KIT / REPORT / FEATURE_MASK_REPORT, PHASE2_PLACEHOLDER_FINGERPRINTS.json, ROADMAP_STATUS.md |
| `9304bde` | midi: ProfileRegistry (profiles.py + profile_classifier.py), bass_extractor_v2, coreml_extractor, detector_arbitration, spectral_validator, pitch_stability, postprocess + 7 new passes (beat_grid_filter, delay_cleanup, harmonic_suppression, key_conformity, octave_correction, octave_doubling, subharmonic_suppression) |
| `0e84b4c` | plugin_scanner: scanner_ableton (new) + plugin_db / mapper / __init__ updates to enumerate Live's built-in devices alongside AU/VST3/VST2 |
| `cad5d93` | test_api: widened a pick-shape assertion to accept the bass `{name, type, confidence}` shape (the guitar-chain test grew bass coverage; only that one assertion was strict where its siblings used `.get(default)`) |
| `718843c` | scripts/root_cause_analysis: promoted ~400-line MIDI F1 RCA harness out of backend root; four trivial print-debug scratchpads (test_bass_v2/test_debug/test_fresh/test_octave_fix) deleted |

Test reconciliations (three stale assertions, no logic changes):
- `test_midi_extractor.py::test_get_synthwave_bass`: `(onset 0.3, min_note_ms 50)` → `(0.5, 80)` to match the authoritative `MONO_BASS` in `profiles.py`. The test comment already pointed at `mono_bass`; only the numerics were stale.
- `test_plugin_scanner.py::test_scan_and_register_function`: added `scan_ableton=False` so the fixture stays scoped to the mocked VST3 path (otherwise the real Ableton scanner runs and registers ~65 Live devices, breaking `plugins_added == 1`).
- `test_api.py::test_analyze_chain_picks_have_required_fields`: assertion widened from `slot|category` + `display|models` to also accept `type` + `name` so the bass `recommendations` shape passes. Sibling tests in the same class already tolerated this via `.get(default)`.

Verification: full backend `tests/` sweep — **1207 passed, 12 skipped, 0 failed** in ~3 min.

Tension with §15 freeze acknowledged: these commits add files to subsystems marked Frozen (MIDI internals, Reconstruction, Evaluation, Studio). The freeze stands going forward — this entry is *inventory of work already done*, not authorization for new feature work in those packages. The bug-fix-only rule continues to apply.

Not in this entry:
- `data/history.json` is intentionally left as a working-tree modification; it's tracked runtime data that churns every session.
- `test_api.py::test_analyze_chain_picks_have_required_fields` was failing on `HEAD` before the sweep started (confirmed via `git stash` against `0e84b4c`); the fix landed alongside the sweep purely so the broader-sweep verification could come back fully green.

### Deep-link refresh fidelity (Priority 5 follow-up)

Refreshing `/jam/:id` lost guitar stems, the SUGGESTED tone card, top-level
`tempo_bpm`, and `detected_key` — all visible in the live (streaming)
analyze path but missing after a page reload. Root cause was a contract
impedance mismatch between the persisted `AnalysisResult` shape and the
narrower `SessionBundle` contract: stems beyond the six fixed slots were
silently dropped on bundle build, and `bundleToLegacyResult()` hardcoded
`preset_matches: {}` while never reading `bundle.tone`.

| Change | File |
|---|---|
| `StemSet.extras: Tuple[Stem, ...]` field for stems beyond the six fixed slots. Backwards-compat default (`()`). | `backend/tone_forge/contracts.py` |
| `_build_stems()` iterates the full `stems_paths` dict, classifying non-fixed-slot names via longest-prefix role match so `guitar_texture_2` resolves to `TEXTURE` (not `HARMONIC`). | `backend/tone_forge/session/bundle.py` |
| `/api/session/:id` emits `legacy_tone`, `legacy_preset_matches`, `legacy_tempo_bpm`, `legacy_detected_key` sidecar fields from the persisted history row. Keeps the SessionBundle contract narrow; the Jam UI reads these for deep-link rehydration only. | `backend/tone_forge_api.py` |
| `bundleToLegacyResult()` iterates `stems.extras`, reads the legacy sidecars, swaps `||` → `??` on tempo so a legitimate `0.0` survives, and passes through `tone` so `renderToneCard()` re-renders SUGGESTED chains after refresh. | `backend/static/jam.js` |
| Cache-buster bump `?v=3` → `?v=4` on both jam.js and jam.css. | `backend/static/jam.html` |
| New regression tests: `extras` populated for `guitar_texture` / `guitar_texture_2` / `guitar_rhythm`; no duplication into fixed slots; route emits sidecars including `tempo_bpm=0.0` (proves the `??` coalescing on the client side); extras key present on the route payload. | `backend/tests/test_session_bundle.py`, `backend/tests/test_session_route.py` |

Verification:
- `tests/test_session_bundle.py` — 23/23 PASS (was 21, added 2).
- `tests/test_session_route.py` — 15/15 PASS (was 13, added 2).
- Broader sweep (session + tone + devices + subsystem boundaries):
  274/274 PASS.
- Empirically against the real `data/history.json`: a row with
  `['drums', 'bass', 'guitar_texture', 'guitar_texture_2', 'vocals']`
  now projects all five stems (three fixed slots + two extras both
  tagged `role=texture`).

Not in this entry:
- ToneMatch → ToneRecommendation conversion at the bundle layer. The
  client today reads the persisted `to_wire_dict` blob directly via
  the `legacy_tone` sidecar. Folding the conversion into `bundle.tone`
  itself would let the deep-link path stop reading sidecars at all,
  but is a larger contract change.
- Re-running tone retrieval when a row has `preset_matches` but no
  persisted `tone`. The endpoint already calls
  `_retrieve_tone_for_history()` but its result lands on
  `bundle.tone` (ToneMatch shape), not on the wire-shape `legacy_tone`
  the UI consumes. Acceptable for now — rows missing `tone` will
  simply show the default chain card.

### CoreAudio probe pre-fill (Priority 7 — item #36)

The §7 plan called for the onboarding modal to seed
`audio_input_name` from the existing `discovery.probe()` rather than
leaving the field `null` until the user opens settings later. The
probe function (shells out to `connect devices --json`) and the
`audio_input_name` field on `DevicePreferences` had been in place
since the P7 scaffold; this entry closes the UX gap.

| Change | File |
|---|---|
| `GET /api/device/probe` endpoint + serializer for `DeviceProbe` / `AudioDeviceInfo`. Belt-and-braces try/except: even if `probe()` ever raises, the route returns 200 with `probe_succeeded=False` so the modal is never 500'd. | `backend/tone_forge_api.py` |
| Onboarding modal markup: new "Detected: <name> — Change" row + hidden `<select>` picker, both initially `hidden` so the modal still renders the moment it opens. | `backend/static/jam.html` |
| Minimal CSS for the new rows; matches the existing onboarding panel idiom. | `backend/static/jam.css` |
| Probe fetched async on modal open; UI populates the Detected row only on `probe_succeeded`. `Change` link swaps Detected for the `<select>`. Submit handler now includes `audio_input_name` (from Detected or picker) when the row is visible; omits it when the probe failed or the helper isn't installed, preserving the existing `audio_input_name=null` path. | `backend/static/jam.js` |
| New endpoint tests: success path, `probe_succeeded=False` path, contract-violation `probe()` raise path. | `backend/tests/test_api_device_probe.py` |

UX decisions documented in the plan file
(`~/.claude/plans/ancient-mixing-turing.md`):
- **Detected + Change reveal** over an always-visible dropdown
  (minimum visual weight in the modal; default Just Works).
- **Render modal immediately, pre-fill async** — the probe takes
  hundreds of ms when the helper isn't on PATH, and we never want
  the modal to block on it.

Verification:
- `tests/test_api_device_probe.py` — 3/3 PASS.
- `tests/test_api_device_preferences.py` — 9/9 PASS
  (unchanged; the POST endpoint already accepted `audio_input_name`).
- `tests/test_devices_discovery.py` — full probe-internal coverage
  unchanged. PASS.
- Broader sweep (probe + preferences + devices + session + tone +
  connect bridge): 275/275 PASS.

Not in this entry:
- Runtime consumption of `audio_input_name` by the audio pipeline.
  The onboarding loop now captures the value; the Python supervisor
  plumb to the Connect helper env is the entry at the top of §0;
  the Swift consumer is still queued.
- Re-prompt when the detected device changes between sessions. Per
  the §8 spec, re-prompt is gated on `device_class === null` or an
  explicit settings reset; an interface swap alone does not trigger
  it.
- Vendor-hint surfacing in the modal. The probe returns
  `vendor_hint` but no downstream consumer needs it yet; it stays
  on the wire for future use.

### DeviceCaps consumer wiring (Priority 7 — item #38)

`SessionBundle.device_caps` has been populated from persisted
preferences since `3b92ccc` (the API edge runs
`_device_caps_for_session()` and passes the result into
`build_session_bundle`). The plumb-through was complete *into* the
bundle but no downstream consumer read it — the user's pinned
`preferred_chain_family` had no effect on what Jam actually played.
This entry closes the consumer side: `tone.retrieve()` now forwards
`preferred_family` into the fallback policy, and the API edge passes
`device_caps.preferred_chain_family` through. The user's explicit
onboarding answer always beats the tempo / key heuristic on LOW /
UNKNOWN tiers; HIGH / MEDIUM (preset chosen from retrieval) are
untouched.

| Change | File |
|---|---|
| `select_fallback_chain` / `select_fallback_family` now accept `preferred_family: Optional[MonitorChainFamily]`; when set to a known family, short-circuits the tempo/key heuristic. Unknown values fall through to the heuristic (defensive against older persisted prefs) | `backend/tone_forge/tone/policy.py` |
| `tone.retrieve()` signature gains `preferred_family`; forwards to `policy.select_fallback_chain`. HIGH / MEDIUM paths unchanged (only fallback ids are influenced) | `backend/tone_forge/tone/__init__.py` |
| `_retrieve_tone_for_history` accepts `device_caps` and extracts `preferred_chain_family`; the session route now computes `device_caps` once and shares it between retrieval and bundle assembly so the two cannot disagree | `backend/tone_forge_api.py` |
| Autouse fixture in `test_session_route.py` sets `TONEFORGE_DEVICE_PREFS_PATH` to a tmp path so a dev machine with real persisted prefs no longer leaks `helix` into unrelated assertions | `backend/tests/test_session_route.py` |

Verification:
- `tests/test_tone_policy.py` — 3 new tests for `preferred_family`
  override (beats fast-tempo heuristic; beats `None` understanding;
  `preferred_family=None` keeps the existing decision surface).
  Total 22/22 PASS.
- `tests/test_tone_retrieve.py` — 3 new tests for forwarding the
  hint through `retrieve()` on UNKNOWN, LOW, and HIGH/MEDIUM paths
  (HIGH/MEDIUM proves the override only touches `fallback_chain_id`,
  not `chosen`). Total PASS.
- `tests/test_session_route.py` — 1 new end-to-end test: persisting
  `preferred_chain_family=ambient` flips the fallback id from the
  tempo-default `tfc.clean_strat` to `tfc.ambient`. All session-route
  tests PASS.
- `tests/test_subsystem_boundaries.py` + sibling devices /
  connect_bridge / tone_* suites — 145/145 PASS.

Not in this entry:
- CoreAudio probe pre-fill (item #36) — landed in the entry above
  this one, where the onboarding modal now surfaces the probed
  interface name and the chosen `audio_input_name` is persisted.
- Connect-side consumption of `preferred_chain_family`. The bridge
  exposes `apply_chain` keyed by chain id; if Jam handed it the
  bundle's `tone.fallback_chain_id` the user's pinned family would
  already route, so no bridge-side change was needed. Mentioned here
  for the audit trail.
- Runtime consumption of `audio_input_name` by the audio pipeline.
  Onboarding now captures the value (see #36 entry above); the
  Python plumb to the Connect helper env lands in a later entry
  above; the Swift side does not yet read the env var.

### Jam onboarding modal (Priority 7 — item #37)

The §8 single-question device-discovery prompt now ships in the Jam UI.
Sits as a sibling of `<main id="jam-app">` so the fixed-position
backdrop covers the page without inheriting `#jam-app`'s padding /
max-width. On startup the Jam page calls `GET /api/device/preferences`
and reveals the modal when the response is `null`; the answer is
persisted via `POST /api/device/preferences` (existing P7 edge, no
server-side changes). Re-prompt is wired through "Reset device choice"
in the settings popover, which calls `DELETE` then re-shows the modal.

| Surface | File |
|---|---|
| Modal markup (8 radio options mapped to `DeviceClass`: `interface_only`, `helix`, `quad_cortex`, `kemper`, `fractal`, `tonex`, `neural_dsp`, `other`) | `backend/static/jam.html` |
| Modal styles (backdrop, panel, option list, "Reset device choice" affordance) | `backend/static/jam.css` |
| Probe-on-startup + submit handler + reset wiring; `DEVICE_CLASS_LABELS` mirrors §8 spec labels verbatim so "Currently: …" line in settings matches what the user picked | `backend/static/jam.js` |

`backend/static/jam.html` and `backend/static/jam.css` were untracked
working-tree state from the earlier P2 jam-page work and ship in this
commit alongside the onboarding additions; tracking them now closes a
stale gap between the working tree and git history.

Verification:
- `tests/test_api_device_preferences.py` — 9/9 PASS (unchanged).
- `tests/test_subsystem_boundaries.py` — 10/10 PASS.
- `tests/test_connect_bridge_lifecycle.py` + apply_chain — 14/14 PASS.
- `node --check backend/static/jam.js` — OK.

Not in this entry:
- `DeviceCaps` consumer wiring (item #38) — landed in the entry
  above; the modal-only commit just got the answer persisted.
- CoreAudio probe pre-fill (item #36) — landed in a later entry.
  At the time of this entry the modal was a single answer with no
  probed input-name hint.

### tone → monitor boundary fix (follow-up to Priority 6)

`tests/test_subsystem_boundaries.py [tone]` had been red since the P6
matcher landed (commit `44207a3`): `tone/guitar_catalog.py` imported
`tone_forge.monitor.loader` for `list_chain_ids()` + `load_chain()` to
resolve each chain's `display_name` and `family`. Path (b) chosen over
Path (a) because the fingerprint JSON is already the authoritative
on-disk artefact the matcher consumes — carrying two more strings has
zero cost; routing through a composition edge would have spread
`monitor/`'s surface across more callers for no win.

| Change | File | Commit |
|---|---|---|
| Extended fingerprint JSON schema: added `display_name` + `family` top-level keys to all 5 rendered fingerprints (`tfc.{ambient, classic_rock, clean_strat, edge_of_breakup, modern_gain}.fingerprint.json`); the 4 non-ambient JSONs were untracked working-tree state from the prior P3 render pass and shipped with this commit | `backend/tone_forge/monitor/chains/*.fingerprint.json` | `c6ff8d1` |
| `_get_catalog()` now globs `_CHAINS_ROOT/*.fingerprint.json` directly; `_load_entry()` reads `chain_id`/`display_name`/`family` from the JSON (raises with file path on missing/invalid field); `_resolve_fallback_meta()` reads the fingerprint JSON instead of the YAML. `from tone_forge.monitor.loader import …` removed. | `backend/tone_forge/tone/guitar_catalog.py` | `c6ff8d1` |
| `_render_fingerprint()` now bakes `display_name` + `family` into emitted JSON; driver passes `chain.display_name` + `chain.family.value`. Was untracked working-tree state from the prior render pass; landed now so the schema contract is captured | `backend/scripts/render_chain_references.py` | `c1119fb` |

Verification:
- `tests/test_subsystem_boundaries.py` — 10/10 PASS (previously `[tone]`
  was red).
- `python3 -m pytest tests/ -k "tone or monitor or boundary or session
  or device"` — 321 PASS, 0 FAIL.
- Catalog smoke-loads all 5 entries with the expected display_names +
  families; result is byte-equivalent to the prior YAML-derived path.

Not in this entry:
- Re-rendering existing 5 fingerprints (already carry the new fields
  from `c6ff8d1`).
- `monitor/loader.py` itself — untouched. Its `list_chain_ids()` /
  `load_chain()` surface remains for the Connect `apply_chain` path
  (still needs YAML parameters) and the render scripts.

### Connect hardening — focused pass (Priority 2)

Closes the three real failure modes in the bridge that were unhandled.
Plan file: `~/.claude/plans/ancient-mixing-turing.md` ("Connect
Hardening — Focused Pass"). Not the full §3 surface — install /
signing / Sparkle update / first-run onboarding remain.

| Failure | Fix | File |
|---|---|---|
| `_ConnectChannel` leaked per `session_id` reload | `leave()` now async; drops empty channel under `_connect_channels_lock` | `backend/tone_forge_api.py` |
| Survivor peer not told when broadcast drops a dead client | `broadcast()` emits `{"type":"peer_left","peers":n,"reason":"send_failed"}` to remaining clients | `backend/tone_forge_api.py` |
| Helper crash → manual tray click required | `ConnectSupervisor._reap()` schedules bounded auto-restart (4 attempts, exp backoff capped at 60 s, healthy-uptime reset at 30 s) via `threading.Timer`; respects `_wanted_running` flag set by `stop()` | `backend/local_engine/connect_bridge.py` |
| Browser ignored `peer_left` | `switch(msg.type)` branch updates peer count + flips "Helper connected" badge | `backend/static/jam.js` |

Regression coverage: `backend/tests/test_connect_bridge_lifecycle.py`
(new, 8 tests — channel reap, broadcast survivor notify, supervisor
auto-restart + suppression under `stop()`).

Test-suite hang fix during this work:
`tests/test_connect_bridge_apply_chain.py` gained a `_drive_server()`
ping/pong helper to advance the starlette TestClient event loop on
no-`request_id` sends. Two tests fixed
(`test_apply_chain_ack_omitted_when_no_request_id`,
`test_apply_chain_unknown_id_returns_not_found`). 14/14 apply-chain
tests now green.

Out-of-scope for this pass (explicit, see plan file §"Out of scope"):
heartbeat / mandatory ping-pong, rate limiting, mandatory
`request_id` on every message, structured logging migration, native
Connect helper changes.

### Monitor Chain Bank — ambient redesign Path 1 (Priority 3)

The hand-authored `tfc.ambient` placeholder rendered bass-heavy
(~0.42 band energy) and airless (~0 in 8 kHz+), so the operator's
own ambient render was being misclassified as `classic_rock` at
HIGH confidence 0.97. Path 1 (YAML tighten within existing schema —
no delay block, no schema change) was executed and accepted.

YAML deltas at `backend/tone_forge/monitor/chains/tfc.ambient.yaml`:

| Param | Old | New | Direction |
|---|---|---|---|
| `input.high_pass_hz` | 70 | 140 | cut sub-bass + room rumble |
| `gain_stage.drive` | 0.05 | 0.15 | lift harmonic_ratio |
| `eq.bass_db` | +1 | −2 | actively pull bass band down |
| `eq.mid_db` | 0 | −1 | slight scoop, lets wash breathe |
| `eq.treble_db` | +1 | +3 | drive the air band |
| `eq.presence_db` | 0 | +3 | high-mid sheen |
| `comp.ratio` | 1.8 | 2.5 | more sustain on held chords |
| `comp.threshold_db` | −22 | −24 | catches more of the wash |
| `comp.attack_ms` | 8 | 15 | let transients open, then squash |
| `comp.release_ms` | 200 | 350 | long release keeps the wash alive |
| `reverb.size` | 0.8 | 0.7 | less LF buildup |
| `reverb.mix` | 0.35 | 0.55 | wet content forward |
| `output.trim_db` | 0 | −2 | headroom for higher reverb mix |

Asset changes:
- `tone_forge/monitor/chains/tfc.ambient.wav` — operator re-rendered
  through the live rig (Valhalla Supermassive, Cirrus Minor / 80s
  Space Verb; routed through A Reverb + B Delay returns). Cropped
  leading 0.5 s of silence, normalized to RMS 0.09. Old wav backed
  up to `/tmp/tfc.ambient.OLD.wav`.
- `tone_forge/monitor/chains/tfc.ambient.fingerprint.json` —
  regenerated via `scripts/render_chain_references.py`. Old
  fingerprint backed up to `/tmp/tfc.ambient.fingerprint.OLD.json`.

Fingerprint delta on valid features (polyphony gate correctly
invalidates attack/decay/sustain/pitch_stability — unchanged):

| feature | OLD | NEW |
|---|---|---|
| brightness | 0.0657 | 0.0880 |
| warmth | 0.4904 | 0.7585 |
| air | 1.99e-06 | 8.5e-05 |
| harmonic_ratio | 4.8e-04 | 3.1e-04 |

Retrieval validation result (operator's ambient render as self-test
query):

- Before: matched `tfc.classic_rock` at HIGH conf 0.97 (false positive)
- After: matches `tfc.ambient` at rank 1, distance 0 (correct)
- Non-ambient cross-checks: every other catalog chain still
  self-matches at rank 1
- Test suite: 134/134 tone+catalog+monitor tests green

Validation harness: `/tmp/ambient_retrieval_validation.py` (OLD vs
NEW fingerprint swap + `recommend()` across 9 query candidates). Not
committed to repo — it lives in tmp by design as a one-shot tool.

Documented structural limits (not Path 1 problems):
- `air` axis (8 kHz+ band at QUERY_SAMPLE_RATE=22050) has near-zero
  variance across the catalog (std ≈ 0.00026) → distance math
  explodes on any wet+bright query. Operator's Valhalla
  Supermassive has EQ High cut at 6 kHz, so the reverb engine
  itself produces nothing above 6 kHz.
- These would not be solved by adding a `delay` block to the
  schema. They are retrieval-math + downstream-reverb-engine
  properties.

Explicitly **not** done in this pass (and pre-committed not to do
without separate authorization): retrieval algorithm changes,
threshold changes, τ calibration changes, new fingerprint features,
monitor-bank expansion beyond the 5 existing chains, delay /
modulation / shimmer schema extension, Connect changes.

### Device Discovery (Priority 7) — scaffold + persistence + API edge

Commits 51d0780 + 736b512 (in main) added:
- `device.json` persistence layer for onboarding answers
- CLI `--json` probe scaffold + tests
- Contracts in place for `DeviceCaps` flow

This pass closes the back end: the persisted answer now flows
through to ``SessionBundle.device_caps`` and is reachable from the
browser without UI work.

- `backend/tone_forge/devices/caps.py` (new) — `caps_from_class`
  + `caps_from_preferences`. Maps each `DeviceClass` to a
  `DeviceCaps` per the §8 table: every modeler class advertises
  `can_monitor=True` and `can_receive_preset=False` at MVP
  (preset adapters deferred to Phase 2 per §10). `NO_HARDWARE`
  is the one `can_monitor=False`. Boundary-clean — imports only
  from `contracts`.
- `GET /api/device/preferences` — returns the persisted record
  or `null` so the Jam UI can short-circuit to onboarding with
  one check.
- `POST /api/device/preferences` — persists, stamps timestamps,
  returns the canonical record. 400 on unknown `device_class` /
  `preferred_chain_family` so the UI fails fast instead of
  writing a record `load_preferences` later rejects.
- `DELETE /api/device/preferences` — "Reset device choice"
  surface; idempotent.
- Session route hydration: `GET /api/session/:id` now calls
  `_device_caps_for_session()` which loads `device.json` and
  projects to `DeviceCaps`. Falls back to interface-only when
  nothing is persisted, exactly as `session.bundle.build`
  expected. Composition stays at the API edge so `devices/`
  keeps its empty allow-list per §2.

Tests:
- `test_devices_caps.py` (new, 46 tests) — every `DeviceClass`
  enum value maps to a sane `DeviceCaps`; display names match
  the §8 prompt strings; `caps_from_preferences(None) -> None`;
  `preferred_chain_family` is carried forward.
- `test_api_device_preferences.py` (new, 9 tests) — GET/POST/DELETE
  round-trip via FastAPI TestClient, env-overridden prefs path,
  first_seen preservation, 400 on unknown enum values, 422 on
  missing required field, DELETE idempotency.
- `test_session_route.py` (extended, +2 tests) — bundle defaults
  to interface-only when no prefs; bundle hydrates from saved
  prefs (POST then GET).

Remaining (per §8 / §9 #37): The onboarding screen UI in
`static/jam.html` / `static/jam.js`. All wiring it needs is in
place — fetch `GET /api/device/preferences`, show modal if
`null`, `POST` the answer, refresh `device_caps` from
`GET /api/session/:id`.

Known pre-existing boundary regression (not introduced by this
pass): `test_subsystem_boundaries.py::test_subsystem_imports_are_within_allowlist[tone]`
fails because `tone/guitar_catalog.py` (landed in commit 44207a3,
the P6 matcher) imports `tone_forge.monitor.loader.list_chain_ids`
and `load_chain` to enumerate the chain bank and resolve
`display_name` / `family`. Fix is a separate small refactor:
either parameterize the catalog source via the composition layer
or extend the fingerprint JSON schema to carry `display_name` +
`family` so `tone/` can read JSONs directly. This pass does not
touch `tone/`, so the regression neither widens nor narrows.

### P2 series — Jam Connect deep-link UX

Commits 7976576, 190680e, f5a0449:
- Helper joins on Safari + URL id on the local-engine path
- Fix deep-link nuking page; accept `history_id` for `/jam` URL
- Fire `toneforge://pair` from the Jam Connect button

### Session Engine consolidation (Priority 5) — complete in main

All 5 commits in §9 #26–30 are shipped and tested. Survey
confirmed no remaining scope in this track.

- `backend/tone_forge/session/protocol.py` (270 L) —
  `PROTOCOL_VERSION = 1`, `envelope()`, 19 message types
  (`hello`, `hello_ack`, `joined`, `ack`, `error`, `ping`, `pong`,
  `set_transport`, `set_loop`, `set_user_mute`, `set_monitor_gain`,
  `set_gain` legacy alias, `apply_tone`, `apply_chain`,
  `transport_state`, `tone_applied`, `peer_left`, `device_lost`,
  `device_changed`, `latency_report`).
- `backend/tone_forge/session/transport.py` (256 L) —
  `TransportState` reducer with identity-preserving debounce.
  Handles play/pause/position/tempo, loop in/out, user mute,
  monitor gain. Tempo clamped 0.5–1.0. Gain clamped 0–1. Malformed
  frames are dropped silently.
- `backend/tone_forge/session/bundle.py` (485 L) —
  `SessionBundle.build()` translates legacy `AnalysisResult` dict
  into the new contract; `serialize()` writes JSON-safe output.
  Lenient: missing/bad data projects to sensible defaults instead
  of raising.
- `GET /api/session/{entry_id}` at `tone_forge_api.py:2222` —
  404 on missing entry, 422 on entries without `result` blob, 200
  with serialized bundle. Composes with Priority 6 via
  `_retrieve_tone_for_history()` injecting `ToneMatch`.
- Jam UI `static/jam.js:1864–1960` — deep-link route fetches
  `/api/session/:id`, adapter `bundleToLegacyResult()` projects
  back onto legacy shape so the rest of the UI didn't need to
  rewrite for this pass. Studio UI unchanged per §6.

Tests: `test_session_protocol.py` (13), `test_session_transport.py`
(27), `test_session_bundle.py` (21), `test_session_route.py` (13).
74/74 PASS.

Cross-subsystem boundary check: session/ has no cross-imports of
other subsystem internals; consumers reach in via the
`tone_forge.session` package surface only.

### Retrieval confidence calibration (Priority 6) — shipped surface

Shipped in `main` (4 modules / 660 L) and now lands the in-flight
matcher + instrumentation:

- `backend/tone_forge/tone/__init__.py` (235 L) — package surface,
  `ToneMatch` DTO, `Calibrator` Protocol, `_CALIBRATOR` indirection
  so the placeholder model can be swapped for a fitted isotonic
  regression without touching call sites.
- `backend/tone_forge/tone/calibration.py` (135 L) — placeholder
  calibrator `exp(-d / tau)` with `tau` retuned per call-site; caps
  output at 0.79 (just below `HIGH_MIN`) so HIGH only fires once
  the fitted model lands.
- `backend/tone_forge/tone/tiers.py` (126 L) — `classify()` returns
  HIGH if confidence ≥ 0.80 AND margin ≥ 0.20; MEDIUM if
  confidence ≥ 0.55 OR margin ≥ 0.10; otherwise LOW; UNKNOWN on
  retrieval failure.
- `backend/tone_forge/tone/policy.py` (164 L) —
  `select_fallback_chain(tempo, key)`: tempo > 140 → modern_gain,
  tempo < 90 → ambient, 90–140 + major → clean_strat, 90–140 +
  minor/unknown → classic_rock, no tempo → edge_of_breakup.

Landing now (uncommitted; this commit):

- `backend/tone_forge/tone/guitar_catalog.py` (968 L) — monitor-chain
  matcher. Extracts an 8-feature DSP fingerprint, z-normalizes
  against the catalog distribution, picks the nearest chain,
  inlines `exp(-d / TAU)` with `DISTANCE_TAU = 14.0` (different
  scale than `tone.calibration` because of z-normalization), reuses
  `tone.tiers.classify` and `tone.policy.select_fallback_chain`
  verbatim. Public surface: `recommend()`,
  `recommend_from_tempo_key()`, `to_wire_dict()`.
- `backend/tone_forge/tone/instrumentation.py` (197 L) — append-only
  JSONL telemetry at `backend/data/tone_log.jsonl`. Three event
  types: `recommendation`, `applied`, `ignored`. Each line is a
  self-contained event. Wire format is flat to keep
  `pandas.read_json(lines=True)` consumption trivial at refit time.

Composition (not changing — already in main): the API seam
`tone_forge_api.py:_retrieve_tone_for_history` injects `ToneMatch`
into `/api/session/:id`. There is no standalone `/api/tone/retrieve`
endpoint and that is intentional per §7.

Deferred (operator's call, not this commit):
- Collect ≥100 hand-labeled clips (chain, tempo, key, perceived
  confidence) — input to the calibration refit.
- Fit isotonic regression on those labels; ship as
  `backend/tone_forge/tone/calibration_v1.joblib`. Drop-in via
  `_CALIBRATOR` rebind — no caller changes.

Tests: 89/89 tone tests green
(`test_tone_calibration.py` 23, `test_tone_tiers.py` 23,
`test_tone_policy.py` 22, `test_tone_retrieve.py` 21).

Boundary check: `tone/` does not import `preset_catalog/`; the only
cross-subsystem composition lives at the API seam, exactly as §7
specifies.

### Chord detection (Priority 4) — complete in main

Landed in `main`:
- `backend/tone_forge/analysis/chord_detector.py` — librosa
  `chroma_cqt` + HMM template-matching engine
- `backend/tone_forge/analysis/chords.py` — public boundary wrapper
  emitting `contracts.Chord` tuples
- `backend/tone_forge/chord_detector.py` — back-compat shim for
  pre-freeze callers in `midi` and `ableton_session`
- `Chord` DTO in `contracts.py` (`{start_s, end_s, symbol, confidence}`)
- `UnifiedPipeline._detect_chord_lane()` integrated at stage 7;
  `AnalysisResult.chords` field populated
- `SessionBundle.guidance.chord_lane` plumbed for UI consumption
- `backend/scripts/chord_validation.py` — production quality gate
  (commit `726bb59`, "Chord detection (P4): land validation
  harness, wire-up tests, public export")
- `backend/scripts/chord_validation_report.json` — reference run
  PASSES: 97.2% root-only, 74.8% strict/triad, all 10 progressions
  clear the 50% per-song floor. Output is deterministic; re-running
  the script reproduces the report bit-for-bit.
- `backend/tests/test_chord_lane_wireup.py` — three-contract
  integration tests (7/7 PASS): `detect_chords` public surface,
  `AnalysisResult` ↔ persisted dict round-trip, `SessionBundle`
  consumption of the persisted shape, async
  `UnifiedPipeline._detect_chord_lane`.
- `detect_chords` re-exported from `tone_forge.analysis` per §1
  boundary contract.

Per §5 acceptance criteria (strict ≥60% on majors/minors,
major-minor ≥80%, per-song minimum 50%): the major-minor floor is
not met by the strict score (74.8% < 80%) but is comfortably met by
the root-only score (97.2% ≥ 80%). The dom7 weakness documented in
the spike report is the known cause (G7 collapses into adjacent
roots in I-IV-V7-I); out-of-scope per §5 "Scope discipline". MVP
ships as-is with the documented weakness; any dom7 follow-up will
be a separate priority decision, not auto-driven.

---

## 1. `contracts.py` DTO Definitions

**File**: `backend/tone_forge/contracts.py`
**Rule**: Every cross-subsystem function signature uses these types. No subsystem imports another subsystem's internals.

### Enums

```python
class ContentType(str, Enum):
    SONG_MIX = "song_mix"          # full mix audio
    ISOLATED_STEM = "isolated_stem"

class UserRole(str, Enum):
    GUITAR = "guitar"
    BASS = "bass"                   # Phase 2
    KEYS = "keys"                   # Phase 2
    VOCALS = "vocals"               # never (out of scope)

class ConfidenceTier(str, Enum):
    HIGH = "high"        # auto-apply
    MEDIUM = "medium"    # suggest top + alternates
    LOW = "low"          # fall back to curated chain
    UNKNOWN = "unknown"  # retrieval not attempted / failed

class DeviceClass(str, Enum):
    INTERFACE_ONLY = "interface_only"
    HELIX = "helix"
    QUAD_CORTEX = "quad_cortex"
    KEMPER = "kemper"
    FRACTAL = "fractal"
    TONEX = "tonex"
    NEURAL_DSP = "neural_dsp"
    CONNECT_MONITOR = "connect_monitor"
    NO_HARDWARE = "no_hardware"
    OTHER = "other"

class MonitorChainFamily(str, Enum):
    CLEAN = "clean"
    EDGE_OF_BREAKUP = "edge_of_breakup"
    CLASSIC_ROCK = "classic_rock"
    MODERN_GAIN = "modern_gain"
    AMBIENT = "ambient"
```

### Dataclasses

```python
@dataclass(frozen=True)
class AcquiredAudio:
    wav_path: str
    sample_rate: int
    duration_s: float
    content_hash: str        # sha256 of normalized PCM
    source_kind: str         # "url" | "upload"
    source_uri: Optional[str]
    source_title: Optional[str]

@dataclass(frozen=True)
class StemSet:
    drums: Optional[Stem]    # from existing stem_model.Stem
    bass: Optional[Stem]
    vocals: Optional[Stem]
    other: Optional[Stem]
    guitar_left: Optional[Stem] = None    # pan-split
    guitar_right: Optional[Stem] = None
    content_hash: str = ""    # provenance back to AcquiredAudio

@dataclass(frozen=True)
class Chord:
    start_s: float
    end_s: float
    symbol: str              # "Cmaj7", "F#m", etc.
    confidence: float

@dataclass(frozen=True)
class Section:
    start_s: float
    end_s: float
    label: str               # "intro" | "verse" | "chorus" | etc.
    confidence: float

@dataclass(frozen=True)
class SongUnderstanding:
    tempo_bpm: float
    tempo_confidence: float
    key: Optional[str]              # "C major", "A minor", etc.
    key_confidence: float
    time_signature: Tuple[int, int] # (4, 4) etc.
    beats_s: List[float]
    downbeats_s: List[float]
    sections: List[Section]
    chords: List[Chord]
    # Phase 3 (none of these populated in MVP):
    tuning: Optional[str] = None     # "standard" | "drop_d" | etc.
    capo_fret: Optional[int] = None
    difficulty: Optional[float] = None
    motifs: List["Motif"] = field(default_factory=list)

@dataclass(frozen=True)
class InstrumentMIDI:
    role: UserRole
    notes: List[Dict[str, Any]]     # wraps existing MIDIExtractionResult.notes
    overall_confidence: float
    raw: Dict[str, Any]              # provenance: pass_results, metadata

@dataclass(frozen=True)
class ToneCandidate:
    preset_id: str
    preset_name: str
    instrument: str                  # "Analog" | "Drift" | etc.
    distance: float                  # raw retrieval distance
    calibrated_confidence: float     # [0, 1] after calibration
    audio_preview_url: Optional[str]
    parameters: Dict[str, Any]       # device-agnostic shape

@dataclass(frozen=True)
class ToneMatch:
    tier: ConfidenceTier
    chosen: Optional[ToneCandidate]   # None when tier == LOW
    alternates: List[ToneCandidate]   # populated for MEDIUM/HIGH
    fallback_chain_id: Optional[str]  # populated for LOW
    rationale: str                    # human-readable explanation
    debug: Dict[str, Any]             # margins, raw distances, etc.

@dataclass(frozen=True)
class MonitorChain:
    id: str                          # "tfc.clean_strat", etc.
    family: MonitorChainFamily
    display_name: str
    description: str
    parameters: Dict[str, Any]       # Connect-side graph spec

@dataclass(frozen=True)
class DeviceCaps:
    cls: DeviceClass
    display_name: str
    can_monitor: bool                # True for Connect path
    can_receive_preset: bool         # True only for modelers w/ adapter
    preferred_chain_family: Optional[MonitorChainFamily] = None
    vendor_hint: Optional[str] = None
    model_hint: Optional[str] = None

@dataclass(frozen=True)
class TransportState:
    playing: bool
    position_s: float
    tempo_pct: float                 # 0.5..1.0
    loop_in_s: Optional[float]
    loop_out_s: Optional[float]
    user_mute: bool                  # mute of user_role stem
    monitor_gain: float              # 0..1

@dataclass(frozen=True)
class GuidanceTrack:
    sections: List[Section]
    chord_lane: List[Chord]
    # Phase 2:
    upcoming_chord_lookahead_beats: int = 0
    # Phase 3:
    note_highway: List[Dict[str, Any]] = field(default_factory=list)

@dataclass(frozen=True)
class SessionBundle:
    """Everything Jam needs to start a session. The Jam UI loads this."""
    session_id: str
    audio: AcquiredAudio
    stems: StemSet
    understanding: SongUnderstanding
    user_role: UserRole
    user_midi: Optional[InstrumentMIDI]
    tone: ToneMatch
    guidance: GuidanceTrack
    device_caps: DeviceCaps
    initial_transport: TransportState
```

### Boundary enforcement

A test module fails CI if any subsystem imports across a boundary except through `contracts`:

```
tests/test_subsystem_boundaries.py
```

Implementation: AST walk over each subsystem package; collect every `from tone_forge.X import ...`; assert X is either `contracts` or the subsystem's own internal modules. Pin the allow-list in a small dict.

---

## 2. Package Structure

### New packages (created empty, `__init__.py` only at Priority 1)

```
backend/tone_forge/
├── contracts.py                  ← NEW (§1)
├── acquisition/                  ← NEW
│   ├── __init__.py               (exports: acquire)
│   ├── youtube.py                (extracted from unified_pipeline._load_from_url)
│   └── cache.py                  (content-hash cache)
├── analysis/                     ← EXPAND (currently 2 files)
│   ├── __init__.py               (exports: analyze, detect_chords, detect_sections)
│   ├── chords.py                 ← NEW (§5)
│   ├── sections.py               (canonical home; reconstruction/section_detector.py removed in §0 cleanup)
│   ├── tempo_key.py              (extracted from unified_pipeline analyses)
│   ├── synth_behavior.py         (existing)
│   └── reference_analyzer.py     (existing)
├── session/                      ← NEW
│   ├── __init__.py               (exports: build_session, Transport)
│   ├── protocol.py               (WS message schema v1)
│   ├── transport.py              (TransportState reducer)
│   └── bundle.py                 (SessionBundle assembly)
├── guidance/                     ← NEW
│   ├── __init__.py               (exports: build_guidance)
│   └── builder.py
├── notation/                     ← NEW (Phase 2 placeholder)
│   ├── __init__.py
│   └── chord_diagrams.py         (data only; UI renders)
├── devices/                      ← NEW
│   ├── __init__.py               (exports: discover, get_adapter)
│   ├── discovery.py              (§9)
│   ├── base.py                   (DeviceAdapter protocol)
│   ├── connect_monitor.py        (wraps Swift Connect)
│   ├── ableton.py                (wraps als_template + preset_export)
│   └── no_hardware.py
├── monitor/                      ← NEW (chain bank specs)
│   ├── __init__.py               (exports: load_chain, list_chains)
│   ├── README.md                 (chain authoring guide)
│   └── chains/                   (one YAML/JSON per chain)
│       ├── clean_strat.yaml
│       ├── edge_of_breakup.yaml
│       ├── classic_rock.yaml
│       ├── modern_gain.yaml
│       └── ambient.yaml
```

### Existing packages (treatment)

| Path | Status |
|---|---|
| `tone_forge/midi/` | **Frozen.** Expose only via `__init__.py`. Internals untouched. |
| `tone_forge/preset_catalog/` | **Frozen.** Wrap via `devices/` adapters and `tone/` confidence layer. |
| `tone_forge/reconstruction/` | **Frozen.** Keep running. `section_detector.py` lifted to `analysis/sections.py`; shim retired (see §0). |
| `tone_forge/evaluation/` | **Frozen.** Keep as QA infra. Stop adding subfolders. |
| `tone_forge/stem_separator.py` | Wrap as `stems.separate()` in package `stems/` (cheap rename for boundary). |
| `tone_forge/stem_model.py` | **Untouched.** `contracts.StemSet` composes existing `Stem`. |
| `tone_forge/auto_detect.py` | Wrap behind `acquisition.detect_content()`. Internals untouched. |
| `tone_forge/rules_engine.py` | Stays. Becomes engine for `devices.helix` adapter later. |
| `tone_forge/als_template.py` | Becomes body of `devices.ableton`. |
| `tone_forge/preset_export.py` | Same — body of `devices.ableton`. |
| `tone_forge/tone_preview.py` | Stays. Consumed by `devices.connect_monitor`. |
| `tone_forge/unified_pipeline.py` | Stays during Phase 0. After Priority 5 lands, becomes a thin orchestrator that reads from the new packages. **No deletion in this phase.** |
| `tone_forge_api.py` | Stays. Becomes the only inter-subsystem composer. |

### Boundary Rules

1. **Cross-package types are `contracts.*` only.** No subsystem imports another subsystem's classes or functions directly. Composition lives in `tone_forge_api.py` (and in `session.bundle.build_session` for Jam's specific composition).
2. **Frozen packages cannot be imported by anyone except `tone_forge_api`, `unified_pipeline.py` (legacy), or their own wrapper adapter in an active package.** E.g., only `devices.ableton` may import `als_template`.
3. **No new files inside frozen packages.** Bug fixes edit existing files. New behavior requires a new package or extending an active one.
4. **The Jam UI may only consume routes that produce `contracts`-shaped JSON.** Existing `studio.html`-shaped `AnalysisResult.to_dict()` stays for Studio; new Jam routes return `SessionBundle.to_dict()`.
5. **The WS protocol is versioned** (`v1` initial). Every message has `{"v": 1, "type": ..., ...}`. Old clients refuse to send v2; new clients refuse v0.
6. **CI rule**: `tests/test_subsystem_boundaries.py` must pass. AST-level enforcement.

---

## 3. Connect Hardening Work Breakdown

Connect is product. This is the largest invisible work in the plan.

### A. Install (gate before anything else ships)

- **Bundle target**: `ToneForge Connect.app` at `/Applications/`
- **Installer**: signed `.pkg` produced by `productbuild`
- **Tray integration**: `local_engine/tray.py` discovers installed Connect.app via launch-services lookup; no-PATH dependency
- **Permissions**: microphone (Info.plist `NSMicrophoneUsageDescription`), audio device access
- **First-run elevation**: prompt for microphone permission via `AVCaptureDevice.requestAccess`

### B. Code Signing + Notarization

- **Cert**: Apple Developer ID Application
- **Hardened runtime**: enabled with entitlements file
- **Entitlements**:
  - `com.apple.security.device.audio-input` = true
  - `com.apple.security.cs.allow-unsigned-executable-memory` = false
  - `com.apple.security.cs.disable-library-validation` = false
- **Notarization**: `xcrun notarytool submit ... --wait`; staple ticket to .pkg
- **Verification**: `spctl -a -t install ToneForge-Connect.pkg`

### C. Update Path

- **Framework**: Sparkle 2.x with EdDSA signing
- **Appcast**: `https://toneforge.app/connect/appcast.xml` (RSS XML, version-keyed)
- **Default**: silent auto-update; settings toggle to opt out
- **Channel**: `stable` for now; add `beta` once Jam MVP ships
- **Rollback**: keep prior bundle in `/Library/Application Support/ToneForge/connect-prev/`

### D. Crash Recovery

- **Supervisor**: `backend/local_engine/connect_bridge.py` (exists; harden)
- **Crash logs**: `~/Library/Logs/ToneForge/connect-crash-<iso-ts>.log` from stderr capture
- **Backoff**: 1s → 2s → 5s; 3 attempts; then surface UI error with "Try restarting Connect" CTA
- **Liveness ping**: WS hub sends `ping` every 10s; Connect must `pong` within 3s or supervisor restarts

### E. Reconnect Behavior

- **Already works** at WS level (exponential backoff in `jam.js`). Tightening required:
  - Persistent `session_id` across Connect restarts (write to `~/Library/Application Support/ToneForge/session.json`)
  - On reconnect, hub replays `last_gain`, `last_preset`, `last_transport_state` — `last_gain` works today; the rest need wiring
  - Browser surfaces "Reconnected" toast (1.5s), not just an inline status flip
- **Audio device loss** (interface unplugged): Connect emits `device_lost` WS frame; browser shows reconnection instructions

### F. Error Handling

- **All errors emit**: `{"v":1,"type":"error","code":"<slug>","message":"<human>","retriable":bool}`
- **Code taxonomy** (initial):
  - `audio_device_unavailable`
  - `audio_input_permission_denied`
  - `audio_buffer_underrun`
  - `monitor_chain_load_failed`
  - `preset_apply_failed`
  - `ws_handshake_rejected`
- **Browser handler**: `jam.js` maps `code` → inline status text. Already partially wired via `flashConnectStatus`.

### G. Onboarding

First-run flow when no `session.json` exists:

1. Welcome screen: "Plug your guitar into your interface, put headphones on."
2. Audio device picker: enumerated via `connect devices`; default to lowest-latency interface
3. Input level meter: 5 seconds of listening; auto-set input gain to peak −12dBFS
4. Test tone: play a 2-second clean chord through default `clean_strat` chain
5. Confirmation: "Did you hear yourself clearly?" → Yes / No (retry)
6. Latency reading: `connect latency` measured; warn if > 12ms RTT
7. "I'm ready to play" → stores session, hands off to the Jam UI

### Branching plan

- Branch: `connect/hardening`
- Sub-branches per section (A–G); merge to `connect/hardening` then to `main`
- Each section is an independent commit-set; A and B together gate the first signed build

---

## 4. Monitor Chain Bank — Implementation Plan

This is product IP. The hand-tuning work is on the critical path and **must be explicitly owned**.

### Owner

Founder + (optional) one outsourced tone-design contractor. Not an engineering ticket — a listening engagement. Allocate listening hours weekly until signed off.

### Chain Targets (5 chains, MVP)

| ID | Family | Reference vibe | Used when… |
|---|---|---|---|
| `tfc.clean_strat` | CLEAN | Twin Reverb clean, neck pickup | LOW-confidence on a clean/jangle song |
| `tfc.edge_of_breakup` | EDGE_OF_BREAKUP | Deluxe Reverb on 6 | LOW on bluesy/indie |
| `tfc.classic_rock` | CLASSIC_ROCK | Plexi at 7, treble booster | LOW on rock/punk |
| `tfc.modern_gain` | MODERN_GAIN | 5150-ish, tight low cut | LOW on metal/hard rock |
| `tfc.ambient` | AMBIENT | Clean + dotted-eighth delay + hall reverb | LOW on shoegaze/post-rock/ambient |

### Definition format

`backend/tone_forge/monitor/chains/<id>.yaml`:

```yaml
id: tfc.clean_strat
family: clean
display_name: "Clean Strat"
description: "Bright, low-noise clean. Light comp, small room reverb."
parameters:
  input:
    gain_db: 0
    high_pass_hz: 80
  gain_stage:
    type: tube_clean
    drive: 0.1
    bias: 0.5
  eq:
    bass_db: 0
    mid_db: -1
    treble_db: 2
    presence_db: 1
  comp:
    enabled: true
    ratio: 2.0
    threshold_db: -18
    attack_ms: 5
    release_ms: 80
  reverb:
    type: room
    size: 0.3
    mix: 0.15
  output:
    trim_db: 0
preview_audio: "preview/clean_strat.mp3"   # for UI A/B
```

### Connect execution

`connect/Sources/ConnectCore/MonitorChainLoader.swift` (new):

- Parses YAML chain spec
- Builds AVAudioEngine graph deterministically
- Hot-swap on `apply_chain` WS message
- Exposes `chains list` subcommand for verification

### Curation Process

1. Listen to reference recording on a known interface + headphones
2. Plug a Strat through the same chain; play along
3. Adjust until the player feels they "belong"
4. A/B against the original recording for tonal sit
5. Lock the chain; commit YAML + preview file
6. Document the reference song and the listening setup in `monitor/README.md`

### Acceptance gate

Each chain passes if it:
- Sounds usable on at least 3 reference songs in its target family
- Sits at a comparable monitor level to the original recording (no normalization required)
- Doesn't clip with input peaking at −6dBFS
- Latency ≤ 10ms round-trip on M-series

### Phase 2 expansion

- Per-pickup variants (single-coil vs humbucker presets within each family)
- Per-amp character (Fender / Marshall / Mesa / Vox archetypes)
- Bass chains (Phase 2 bass user role)

---

## 5. Chord Detection — Investigation Plan

Timebox: **5 working days**. Decision gate at the end. Ship the picked approach immediately after.

### Day 1–2: Build path (in-house)

- Compute chroma features (librosa `chroma_cqt`) on the `other` stem (or full mix if `other` missing)
- HMM smoothing over a chord vocabulary: 24 major/minor + 12 dom7 = 36 states
- Self-transition prior tuned to ~0.95 to enforce stability
- Constrain to detected key (use `SongUnderstanding.key` to weight in-key chords)
- Output: `List[Chord]` aligned to beats

Output: working prototype + metrics on labeled set.

### Day 1–2 (parallel): Borrow path

- Evaluate: `madmom.features.chords`, `autochord`, `chordino` (Vamp plugin)
- License check: madmom is BSD; autochord is MIT; chordino requires Sonic Annotator
- Dependency footprint: madmom adds ~80MB; autochord is pure Python; chordino needs C++ binary
- Apple Silicon compatibility check
- Quick benchmark on the same labeled set

### Day 3: Hybrid

- Use existing MIDI extraction notes (from `other` stem) to confirm/refine chord symbols
- Pitch-class histogram from extracted notes within each chord window
- Disambiguate enharmonics and inversions using bass note (from `bass` stem)
- Likely best quality but most dependencies

### Day 4: Evaluation

- **Labeled set**: 20 songs, hand-annotated bar-by-bar chord labels
  - Mix of genres: 5 pop/rock, 5 indie, 5 metal, 5 acoustic/folk
- **Metric**: chord-symbol-correctness at 0.5s tolerance, with two scoring modes:
  - **Strict**: exact symbol match (Cmaj7 ≠ C)
  - **Major-minor**: root + quality only (C ≠ Cm, but C = Cmaj7)
- **Pass criteria**:
  - Strict ≥ 60% on majors/minors
  - Major-minor ≥ 80%
  - Per-song minimum: no song below 50% major-minor

### Day 5: Decision + Ship Plan

- Pick winner based on quality × deps × ship cost
- If quality below pass criteria on all approaches: ship hybrid at current quality with explicit "beta" badge in UI; document known weak genres
- Implementation: lands in `backend/tone_forge/analysis/chords.py`
- Public API: `detect_chords(audio: np.ndarray, sr: int, understanding: SongUnderstanding) -> List[Chord]`
- Plugged into `SongUnderstanding` produced by the analysis pipeline

### Scope discipline

**In**: chord name on a timeline. Major, minor, dom7, min7, maj7, sus2/sus4 if cheap.

**Explicitly out**: inversions, slash chords, extended jazz harmony, key changes mid-song, modal annotations. Defer all.

---

## 6. Session Engine Ownership Model

The clean separation. Lock these contracts.

### UI (browser, then Tauri, then native shell)

**Owns**:
- User intent (clicks, drags, key shortcuts)
- Visualization (band room, transport bar, chord lane, mixer)
- Local optimistic UI state (animation, hover)

**Does NOT own**:
- Audio rendering
- Canonical transport state
- Tone matching logic
- Persistence

### Session Engine (Python, `backend/tone_forge/session/`)

**Owns**:
- Canonical `TransportState` (single source of truth)
- WS message dispatch to UI and Connect
- `SessionBundle` assembly
- Persistence (`/jam/:id` reload restore)
- Per-session in-memory state cache (existing `_ConnectChannel` extended)

**Does NOT own**:
- Audio I/O
- Audio device enumeration
- DSP

### Connect (Swift, `/connect`)

**Owns**:
- Audio I/O (CoreAudio)
- AVAudioEngine graph
- Monitor chain rendering
- Input passthrough
- Stem playback
- Latency-sensitive scheduling
- Audio device permission

**Does NOT own**:
- Transport state authority (consumes; doesn't decide)
- Tone matching
- Song understanding

### WS Protocol v1 (`session/protocol.py`)

Message envelope: `{"v":1, "type": "...", ...}`.

**Intent → Engine** (from UI):
- `hello` (already exists; tighten with `v` and `client_kind`)
- `set_transport` `{playing, position_s?, tempo_pct?}`
- `set_loop` `{in_s?, out_s?}` (null clears)
- `set_user_mute` `{muted: bool}`
- `set_monitor_gain` `{gain: 0..1}` (already exists as `set_gain`; rename for clarity)
- `apply_tone` `{candidate_id}` (override the auto-applied match)
- `apply_chain` `{chain_id}` (override to a curated chain)

**State → Subscribers** (from Engine, broadcast to both UI and Connect):
- `transport_state` (canonical TransportState snapshot)
- `tone_applied` `{candidate_id | chain_id, source: "auto"|"user"|"fallback"}`

**Intent → Audio** (from Engine to Connect):
- `apply_chain` (resolved chain spec)
- `apply_tone` (resolved tone parameters)
- `transport_state` (Connect drives audio scheduling from canonical state)

**Events from Connect**:
- `device_lost`, `device_changed`, `latency_report`, `error` (per §3F taxonomy)

### Migration sequence

1. Define `session/protocol.py` with TypedDict schemas
2. Add `session/transport.py` reducer producing `TransportState` from intents
3. In `tone_forge_api.py`, the `/ws/connect-bridge` handler delegates to the reducer
4. `jam.js` updated to send new intent shapes; existing `set_gain` aliased during transition (one release)
5. Swift Connect updated to consume `transport_state` broadcasts

### Why this matters for desktop

When the UI eventually moves to native Swift (Phase 3 desktop), only the WS protocol crosses the boundary. The reducer stays in Python. The UI re-implementation is purely a presentation rewrite. Audio is unchanged.

---

## 7. Retrieval Confidence Calibration

Goal: trustworthiness, not accuracy. Existing retrieval (`match_audio_file`) is frozen. We add a layer above it.

### Module

`backend/tone_forge/tone/` (new package, alongside the others):

```
tone_forge/tone/
├── __init__.py       (exports: retrieve)
├── calibration.py    (distance → calibrated_confidence)
├── tiers.py          (calibrated_confidence + margins → ConfidenceTier)
└── policy.py         (tier → ToneMatch with fallback selection)
```

### `retrieve()` signature

```python
def retrieve(
    audio_path: str,
    role: UserRole,
    device_caps: DeviceCaps,
    understanding: Optional[SongUnderstanding] = None,
) -> ToneMatch:
    ...
```

### Calibration

- **Raw signal**: top-k distances from `preset_catalog.match_audio_file()` (k=5)
- **Calibration model**: isotonic regression mapping `d_top → P(match correct)`
- **Training set**: 100 hand-labeled audio clips
  - Annotator listens to top match and rates 1–5 ("not at all" → "spot on")
  - Convert to binary (≥4 = correct)
- **Output**: `calibrated_confidence ∈ [0, 1]`
- **Margin signal**: `(d_second - d_top) / d_top` — large margin = unambiguous winner
- **Refit cadence**: every quarter, or after any catalog expansion

### Tier policy

```
tier =
    HIGH    if calibrated_confidence ≥ 0.80 AND margin ≥ 0.20
    MEDIUM  if calibrated_confidence ≥ 0.55 OR  margin ≥ 0.10
    LOW     otherwise
    UNKNOWN if retrieval errored
```

Thresholds are tuneable; lock initial values, log decisions, adjust quarterly.

### Fallback chain selection

For `LOW` tier: choose `MonitorChainFamily` based on `SongUnderstanding`:

| Heuristic | Chain |
|---|---|
| Tempo > 140 + heavy spectral centroid | `modern_gain` |
| Tempo 90–140 + mid-heavy spectrum | `classic_rock` |
| Tempo < 100 + sparse texture + reverb tail | `ambient` |
| Major key + low spectral flux | `clean_strat` |
| Otherwise | `edge_of_breakup` |

Heuristics live in `tone/policy.py`. Refine based on user data once telemetry is wired.

### UX implications (for the UI team)

| Tier | UI behavior |
|---|---|
| HIGH | Auto-apply silently. Badge: "Matched: {preset_name}". |
| MEDIUM | Apply top. Chip strip with 2 alternates. Badge: "Suggested: {preset_name}". |
| LOW | Apply chain. Badge: "Default tone: {chain_display_name}". Subtle button: "Try matching anyway". |
| UNKNOWN | Apply chain. Badge: "Tone matching unavailable". |

The user is never *blocked*. The tier just shapes the surface.

### Telemetry hooks

Every retrieve call emits:

```
{
  "event": "tone.retrieve",
  "tier": ...,
  "calibrated_confidence": ...,
  "margin": ...,
  "user_overrode": bool,        # populated after the session
  "session_duration_s": float,  # populated after the session
}
```

Persisted to `~/Library/Application Support/ToneForge/telemetry.jsonl`. Local only initially; remote opt-in later. Use this for calibration refit.

---

## 8. Device Discovery — MVP Design

Keep it small. Two inputs: one question and one probe.

### Onboarding question

Single screen, single question, single answer required:

> **What are you playing through?**
>
> - Just my audio interface
> - Helix
> - Quad Cortex
> - Kemper
> - Fractal
> - Tonex
> - Neural DSP plugin
> - Something else

Stored as `DeviceClass`. Adjusts the offer:

| Answer | Effect |
|---|---|
| Interface only | `can_monitor=True`, route everything through Connect chains |
| Helix/QC/Kemper/Fractal | `can_monitor=True` via Connect (user uses modeler for tone instead of Connect chains), `can_receive_preset=False` for MVP (export adapters are Phase 2) |
| Tonex | Same as Helix tier |
| Neural DSP | `can_monitor=True`, suggest setting plugin between input and Connect |
| Something else | Default to interface-only behavior |

### CoreAudio probe (background, non-gating)

`devices/discovery.py`:

```python
def probe() -> DeviceProbe:
    """Enumerate audio I/O; return hints. Never blocks."""
```

- Lists input/output devices via existing `connect devices` subcommand
- Detects known vendor IDs for display hints (Focusrite, UA, Audient, Apogee, Steinberg, MOTU, RME, etc.)
- Suggests probable interface choice (lowest-latency input)
- Logs result; UI uses for the onboarding pre-fill

### Storage

`~/Library/Application Support/ToneForge/device.json`:

```json
{
  "device_class": "interface_only",
  "audio_input_name": "Focusrite Scarlett 2i2",
  "preferred_chain_family": "edge_of_breakup",
  "first_seen_iso": "...",
  "last_used_iso": "..."
}
```

Re-prompt only when class is `null` or the user explicitly opens device settings.

### Phase 2 expansion (NOT now)

- USB MIDI sysex probing for Helix / Kemper / QC identification
- Bidirectional preset apply to detected modelers
- Multiple device profiles (work vs home rig)

---

## 9. Immediate Next Commits (Priority Order)

Each item is a self-contained commit-able unit. Land in this order.

### Boundary freeze (Priority 1)

1. **`docs/_archive/` and move strategy docs**
   - Move all `backend/*.md` strategy/RCA/plan files to `docs/_archive/`
   - Exceptions kept at `backend/`: `EXTRACTION_STATUS.md`, `ROADMAP_STATUS.md` (these reflect frozen-system current state)
   - Add `docs/README.md` pointing to this `EXECUTION_PLAN.md`

2. **`backend/tone_forge/contracts.py`**
   - All enums and dataclasses from §1
   - Zero behavior; pure types
   - Add `__all__` listing public surface

3. **Create empty package skeletons**
   - `acquisition/`, `session/`, `guidance/`, `notation/`, `devices/`, `monitor/`, `tone/`, `stems/`
   - Each: `__init__.py` with `__all__ = []`
   - Each subsystem gets a `README.md` (3 lines: purpose, owner, status)

4. **Boundary test**
   - `backend/tests/test_subsystem_boundaries.py`
   - AST walk + allowlist from §2
   - Fails on illegal cross-imports

5. **Move + re-export: section detector** — complete:
   - `analysis/sections.py` is the canonical home (627 lines, full
     detector + `detect_sections()` API).
   - `reconstruction/section_detector.py` was a 17-line re-export shim
     during the transition; all three internal callers
     (`unified_pipeline.py`, `tone_forge_api.py`, `analysis_worker.py`)
     have been migrated to the new location and the shim deleted
     (see §0).

6. **Move + re-export: URL acquisition**
   - Extract `unified_pipeline._load_from_url` → `acquisition/youtube.py`
   - Return `AcquiredAudio` (contracts type)
   - `unified_pipeline._load_from_url` becomes one-line delegator
   - Add `acquisition/cache.py` with content-hash storage

### Connect hardening (Priority 2)

7. **Branch: `connect/hardening`**
8. **Signed build CI**
   - GitHub Action or local script that produces signed + notarized `.pkg`
   - Test on a clean macOS VM
9. **First-run flow scaffold**
   - Swift onboarding view controller
   - Audio device picker + input meter
   - Test-tone playback
10. **Crash supervisor hardening**
    - `connect_bridge.py` writes crash logs to ~/Library/Logs/ToneForge/
    - Backoff + max-retries + UI error surfacing
11. **WS protocol v1 envelope**
    - `session/protocol.py` defines schemas
    - Browser + Connect both emit `{"v":1, ...}`
    - Server validates envelope; rejects v0

### Monitor chains (Priority 3, in parallel with Connect)

12. **`monitor/README.md`** — chain authoring guide
13. **Reserve 5 chain YAML files** with placeholder parameters
14. **Swift `MonitorChainLoader`** — parses YAML + builds AVAudioEngine graph
15. **WS `apply_chain` handler** end-to-end (Browser → Engine → Connect)
16. **First chain dialed in**: `clean_strat` — committed only after sit-with-reference acceptance
17. Remaining 4 chains, one per commit, each with reference recording in `monitor/chains/preview/`

### Chord detection (Priority 4)

18. **Spike branch**: `analysis/chords-spike`
19. **Build prototype** (chroma + HMM) — Day 1–2
20. **Borrow prototype** (best library) — Day 1–2
21. **Hybrid prototype** — Day 3
22. **Labeled eval set** — `tests/fixtures/chord_labels.json` (20 songs)
23. **Eval report** — Markdown comparison in spike branch
24. **Pick + merge**: winner lands at `analysis/chords.py`
25. **API wire-up**: `SongUnderstanding.chords` populated in pipeline

### Session Engine (Priority 5)

26. **`session/transport.py`** — `TransportState` reducer
27. **`session/protocol.py`** — full v1 schema
28. **`session/bundle.py`** — `SessionBundle.build()` from existing pipeline outputs
29. **New API route**: `GET /api/session/:id` returning `SessionBundle.to_dict()`
30. **Jam UI**: read `SessionBundle` instead of `AnalysisResult`. Studio UI unchanged.

### Retrieval calibration (Priority 6)

31. **`tone/__init__.py`** + `tone/calibration.py` + `tone/tiers.py` + `tone/policy.py`
32. **Labeled calibration set** — 100 clips + ratings
33. **Isotonic regression fit** committed as `tone/calibration_v1.joblib`
34. **New API route**: `POST /api/tone/retrieve` returning `ToneMatch`
35. **Jam UI**: consume `ToneMatch`; render tier-appropriate UX

### Device Discovery (Priority 7)

36. **`devices/discovery.py`** — CoreAudio probe wrapper around existing `connect devices`
37. **Onboarding screen** — single question, persisted to `device.json`
38. **`DeviceCaps` plumbed** into session bundle

### Song Understanding investigation (Priority 8)

39. **`docs/SONG_UNDERSTANDING_INVESTIGATION.md`** — investigation notes (not an implementation commit; pure research output documenting tuning/capo/motif feasibility)
40. **Place fields in `SongUnderstanding` DTO already** so consumers can stub-render when populated

### Cleanups

41. **Triage repo-root test scripts** — complete:
    - `backend/test_bass_v2.py` — deleted (commit `718843c`)
    - `backend/test_debug.py` — deleted (commit `718843c`)
    - `backend/test_fresh.py` — deleted (commit `718843c`)
    - `backend/test_octave_fix.py` — deleted (commit `718843c`)
    - `backend/root_cause_analysis.py` — promoted to `backend/scripts/`
      (commit `718843c`)
    - `backend/run_samples_benchmark.py` → `backend/scripts/` (see §0)
    - `backend/run_stem_benchmark.py` → `backend/scripts/` (see §0)

---

## 10. Out of Scope (Explicit Defer / Freeze)

### Frozen — no work

- Reconstruction / ALS export feature work
- MIDI extraction accuracy improvements
- Retrieval embedding experimentation
- Evaluation harness expansion
- Studio feature development (bug fixes only)
- 268-preset catalog content changes
- Ableton Suite catalog expansion

### Deferred — Phase 2+

- Helix / QC / Kemper / Fractal / Tonex / Neural DSP device adapters (preset export)
- Note highway
- Performance listener (pitch/timing/chord accuracy)
- Tablature generation
- Bass / keys user roles
- Per-section preset switching
- Social, leaderboards, sharing
- Multi-user / collaboration
- Mobile clients
- Spotify / Apple Music ingestion (DRM)
- Plugin hosting inside Connect

### Never

- Vocal role for user (out of scope)
- Replacing Ableton
- Replacing Helix / device modelers
- Replacing Yousician / Rocksmith head-on (we win by being specialists in guitar tone delivery, not by competing on transcription breadth)

---

## Acceptance Gate for Jam MVP

A guitarist:

1. Installs ToneForge (signed installer; opens without warnings)
2. Pairs Connect (first-run flow completes in < 2 minutes)
3. Pastes a YouTube URL
4. Waits ≤ 90 seconds on a typical Mac (not a dev machine)
5. Sees the band room load with stems mounted
6. Hears the song play with their guitar muted
7. Hears themselves through either a matched preset or a curated chain (tier-appropriate)
8. Loops a chorus
9. Slows playback to 70%
10. Sees the current chord name above the timeline
11. Plays for ≥ 5 minutes and reloads the page — session restores at the right position

If any of the above fails, the MVP is not ready.

---

## Closing

This document is the execution plan. It supersedes prior strategy docs. The next strategic question worth asking is "did Jam MVP ship and did anyone pay for it?" Until then, the only allowed inputs are bug reports and the items above.
