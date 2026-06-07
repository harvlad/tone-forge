# End-to-End Reconstruction Ship Plan — Revised

**Date:** 2026-06-06
**Goal:** Shortest path to a downloadable reconstruction ALS.

> Audio → MIDI → Preset → Download `.als`

**Highest-risk assumption to retire first:** *whether users perceive value
in receiving an editable Ableton project generated from reference audio.*
That risk is not retrieval quality. Retrieval is already validated
(V2-H1 PASS, V2-H2 PASS, V3 workhorse PASS, 268-preset corpus integrity
verified). Treat retrieval as production-ready and ship the workflow.

---

## 1. Strategy: ship in two phases

**Phase 1 — Hardcoded preset demo (this week).**
Wire the entire export path end-to-end using a single fixed Analog `.adv`
file. This proves the whole `Audio → MIDI → ALS download → open in Live`
chain on real user audio, without taking on any retrieval-integration
risk.

**Phase 2 — Swap in retrieval (immediately after Phase 1 ships).**
Replace the hardcoded preset with a call to V2 retrieval. The export
endpoint, frontend, ALS structure, and tests do not change; only the
preset-selection function changes.

Rationale: every hour spent debugging V2 wiring before the export path
is proven is an hour spent on the wrong risk. The user-visible feature
"download a reconstruction" is the experiment. Make the experiment cheap
to run.

---

## 2. Phase 1 feasibility — can we hardcode a preset?

**Yes, trivially.** The audit confirms:

- `PresetInfo` (`tone_forge/preset_catalog/preset_discovery.py:147`)
  requires only `preset_id, name, instrument, category, sound_type,
  path (to .adv), source`. All are static strings except `path`.
- `create_preset_als(preset, notes, tempo)`
  (`tone_forge/preset_catalog/preset_als_generator.py:447`) takes that
  `PresetInfo` plus a `List[TestNote]` and a tempo, and returns gzipped
  ALS bytes containing the spliced preset device block + a MIDI clip.
- Every V2 catalog row already stores an absolute `provenance.preset_path`
  to its source `.adv`. Pinning one row is a single constant.

**Phase-1 default preset** (recommended): `analog_thick_chord_pad`,
sourced from
`/Applications/Ableton Live 12 Standard.app/Contents/App-Resources/Core Library/Devices/Instruments/Analog/Synth Pad/Thick Chord Pad.adv`
(SHA-1 `eeb40055...`). Rationale: neutral, harmonically agnostic, audibly
"alive" on any MIDI input, and already in the V2 catalog so we know the
`.adv` parses and renders cleanly. Swap-out cost in Phase 2 is one line.

**No new dependencies.** `mido` is already in the dep tree via
`midi_extractor`; that handles base64 `.mid` → notes for the existing
export path.

---

## 3. Revised implementation sequence (Phase 1)

Ordered for earliest working demo. Each step ends with a runnable
artifact.

### Step 1 — `format=reconstruction` export branch

- In `tone_forge_api.py` at `tone_forge_api.py:1947`, add:

  ```python
  elif format_type == "reconstruction":
      if not request.full_result:
          raise HTTPException(400, "Full analysis result required")
      result = preset_export.export_reconstruction_als(
          request.full_result,
          request.preset_name,
      )
  ```

- No other API changes. `ExportRequest` already carries `full_result`
  and `midi_data` (which we will source notes from).
- **Effort:** 15 min. **Exit criterion:** request hits the branch and
  returns a 501-style stub.

### Step 2 — `export_reconstruction_als` (hardcoded preset)

- New function in `tone_forge/preset_export.py`:

  ```python
  def export_reconstruction_als(full_result: dict, preset_name: str)
      -> ExportedPreset:
      preset = _phase1_default_preset()           # hardcoded PresetInfo
      notes  = _notes_from_full_result(full_result)
      tempo  = float(full_result.get("tempo_bpm", 120.0))
      als_bytes = create_preset_als(preset, notes, tempo=tempo)
      return ExportedPreset(
          filename=f"{preset_name}.als",
          format="reconstruction",
          content=base64.b64encode(als_bytes).decode(),
          content_type="application/octet-stream",
      )
  ```

- `_phase1_default_preset()` returns the pinned `PresetInfo` for
  `Thick Chord Pad`. Single constant.
- `_notes_from_full_result(full_result)`: pull `midi_data.content`
  (base64 .mid), `mido.MidiFile.from_string(...)` → walk track 0 →
  convert ticks-to-beats → emit `TestNote(pitch, start_beats,
  duration_beats, velocity)`. Falls back to a 4-note sanity sequence
  if no MIDI is present (so the path still exits cleanly during early
  testing).
- **Effort:** 2–3 h. **Exit criterion:** `curl /api/export` with a saved
  analysis JSON returns base64 ALS bytes that gunzip into valid XML.

### Step 3 — Studio UI button "Reconstruct in Ableton"

- Add a button alongside the existing MIDI / Ableton export buttons in
  `static/studio.html` (or wherever the export toolbar lives in the
  studio view).
- On click, POSTs `{format: "reconstruction", full_result, preset_name}`
  to `/api/export` and triggers a download via the existing
  `downloadExport(...)` helper in `static/app.js` — same pattern used
  by MIDI/Ableton exports already in production.
- Disable the button until analysis with MIDI extraction completes.
- **Effort:** 1–2 h. **Exit criterion:** real user audio → click button
  → `.als` lands in `~/Downloads`.

### Step 4 — End-to-end smoke test

- `backend/tests/test_reconstruction_e2e.py`:
  1. Take 3 reference WAVs (one bass, one lead, one pad) from
     `preset_catalog_output/audio_v2/`.
  2. Run the analysis pipeline directly (no HTTP) to produce
     `full_result`.
  3. Call `export_reconstruction_als(full_result, "test")`.
  4. Assert: result is `ExportedPreset`; `base64.b64decode(content)` is
     gzip; gunzipped XML parses; contains a non-empty `KeyTrack` *and*
     a non-trivial Analog device block (reuse
     `preset_als_generator._assert_device_nontrivial` if accessible,
     or duplicate its check).
  5. Optional / manual pre-ship: open the three generated `.als` files
     in Live 12 Standard and confirm both the MIDI clip and the
     preset's device chain load.
- **Effort:** 1–2 h. **Exit criterion:** `pytest tests/test_reconstruction_e2e.py`
  passes; three manual ALS opens succeed.

### Step 5 — Stage timings + 2-minute guard

- Wrap the analyse / extract / build stages with
  `time.perf_counter()` markers. Add `stage_timings` to the analysis
  response. Surface the four numbers in the studio footer.
- Log a warning (no failure) if total wall-clock > 120 s so we can spot
  regressions early.
- **Effort:** 30 min. **Exit criterion:** response JSON includes
  `stage_timings.{decode, classify, midi, retrieve, als}` (the
  retrieve stage stays at ~0 ms during Phase 1).

### Step 6 — Ship Phase 1

After Steps 1–5 pass, ship the branch and put it in front of real users.
Capture qualitative reactions to "you uploaded audio and got an editable
Ableton project back" before doing any further engineering.

**Total Phase-1 effort:** 5–8 hours of focused work. Realistic single
developer ship in 2–3 working days.

---

## 4. Phase 2 — swap in retrieval

Triggered by: Phase 1 has shipped, and user response confirms the core
experience has value.

Phase 2 is intentionally tiny because Phase 1 absorbed all the plumbing
risk.

### Step 7 — V2-aware retrieval

- Add `catalog_version="v2"` flag to
  `preset_retrieval.match_audio_file` so it reads
  `preset_catalog_output/catalog/catalog_v2.json` (union) instead of
  the V1 per-engine catalogs.
- Default the public API stays on V1 so no other caller is affected.
- **Effort:** 1 h.

### Step 8 — Sound-type → engine mapping helper

- `tone_forge/preset_catalog/engine_routing.py`:
  `pick_engine_from_sound_type(sound_type: str) -> str`. Conservative
  defaults; unknown → `"Analog"`. Unit tests cover the seven sound
  types in the V2 corpus.
- **Effort:** 30 min.

### Step 9 — Replace the hardcoded preset

- In `tone_forge/preset_export.py`, replace `_phase1_default_preset()`
  with a call that:
  1. Pulls the reference audio path from `full_result`.
  2. Calls `match_audio_file(..., catalog_version="v2",
     instrument=pick_engine_from_sound_type(...))`.
  3. Returns the rank-1 match as `PresetInfo`, loading the `.adv` path
     from the catalog row's `provenance.preset_path`.
- The frontend, API, and ALS-generation code do not change.
- **Effort:** 1–2 h.

### Step 10 — Re-run the smoke test

- The same `tests/test_reconstruction_e2e.py` now exercises real
  retrieval per target. Add an assertion that the chosen preset's
  `sound_type` matches the target's `sound_type` (workhorse cases).
- **Effort:** 30 min.

**Total Phase-2 effort:** 3–4 hours.

---

## 5. Latency budget (unchanged from earlier audit)

| Stage              | Budget | Typical observed                              |
|--------------------|-------:|-----------------------------------------------|
| Upload + decode    |   5 s  | 1–3 s for 30 s WAV                            |
| Stem / classify    |  10 s  | 1–5 s (heuristic path)                        |
| MIDI extraction    |  30 s  | 5–15 s per 30 s clip                          |
| Preset retrieval   |   1 s  | <100 ms (Phase 2) / 0 ms (Phase 1)            |
| ALS generation     |   2 s  | 100–300 ms                                    |
| HTTP overhead      |   5 s  | 1–2 s                                         |
| **Total**          | **53 s** | **~10–40 s typical** vs 120 s target        |

Comfortable headroom in both phases. The 2-minute claim is defensible
even with worst-case MIDI extraction.

---

## 6. Risks (updated for two-phase plan)

| # | Risk                                                  | Phase | Severity | Mitigation |
|---|-------------------------------------------------------|-------|---------:|------------|
| R1 | `mido` cannot parse the existing `midi_data.content` blob | 1 | Medium | Smoke test in Step 2; fall back to default 4-note sequence if parse fails. The default sequence still produces a valid ALS, so the path never blocks. |
| R2 | Hardcoded preset's `.adv` is missing on a user's machine (different Live install path) | 1 | Medium | Ship the `.adv` bytes inside the repo (or recoverable from the V2 catalog's recorded SHA). Validate at server start. |
| R3 | Live 12 Standard refuses the generated ALS XML        | 1 | High | The same generator produced 99 Analog ALSes that Live successfully rendered for the V2 corpus. Pre-ship manual import of the three smoke-test ALSes confirms. |
| R4 | Users find a single neutral preset uninteresting and dismiss the demo | 1 | Medium | Document the Phase-2 follow-up *in the UI*: "currently using a default Analog patch; intelligent preset selection ships next." Sets the expectation that the workflow, not the patch, is the demo. |
| R5 | Phase-2 retrieval picks an engine that fails to render | 2 | Medium | Restrict V2 retrieval to Analog/Drift/Collision/Electric — already the catalog's universe. Fail loud if anything else is returned. |
| R6 | Lead-MIDI accuracy is low                              | 1+2 | Low | Acceptable for MVP. Label lead reconstructions "approximate" in the UI. |
| R7 | No automated end-to-end test today                     | 1 | High | Step 4 of Phase 1 is exactly this. |

---

## 7. Impact ranking (user-visible first)

| Rank | Work item                                | User-visible | Ship-readiness |
|------|------------------------------------------|--------------|----------------|
| 1 | `format=reconstruction` export branch       | Enables the whole feature       | 15 min |
| 2 | `export_reconstruction_als` (hardcoded)     | Makes the download real          | 2–3 h  |
| 3 | "Reconstruct in Ableton" studio button      | Makes it reachable from UI       | 1–2 h  |
| 4 | E2E smoke test                              | Prevents silent regressions      | 1–2 h  |
| 5 | Stage timings in response                   | Defends 2-min claim              | 30 min |
| 6 | V2 retrieval flag (catalog_version)         | Foundation for Phase 2 swap      | 1 h    |
| 7 | Sound-type → engine mapping                 | Phase-2 engine routing           | 30 min |
| 8 | Replace hardcoded preset with retrieval     | The "actual" reconstruction       | 1–2 h  |
| 9 | Re-run smoke test against retrieval         | Confidence post-swap             | 30 min |

Items 1–5 = Phase 1 = the demo.
Items 6–9 = Phase 2 = the swap.

---

## 8. MVP definition

### 8.1 Must-have for MVP demo (Phase 1)

- `format=reconstruction` export branch in `/api/export`.
- `export_reconstruction_als` returns a valid gzipped ALS containing
  the user's extracted MIDI + a fixed Analog preset.
- "Reconstruct in Ableton" studio button that downloads the ALS.
- Stage timings logged in the analysis response.
- E2E smoke test passing on 3 reference WAVs.
- Pre-ship manual verification: each of the three smoke-test ALSes
  opens in Live 12 Standard with both the MIDI clip and the preset
  loaded.

### 8.2 Nice-to-have (Phase 2)

- V2-aware retrieval swap.
- Sound-type → engine routing helper.
- Re-run smoke test against retrieval.
- Show the chosen preset name in the UI before download.

### 8.3 Future roadmap (not committed)

- Top-5 audition UI from the retrieval V3 work.
- Drum / percussion track reconstruction.
- Multi-track arrangement reconstruction.
- Confidence indicator on chosen engine.
- Cross-DAW exports (Logic, Bitwig).
- Suite-engine support (Operator / Wavetable / Meld / Tension).
- Quality lift on lead-MIDI extraction.
- Routine Reconstruction Trial runs as a quality regression check
  (harness already exists at
  `scripts/reconstruction_trial_runner.py`).

---

## 9. Ship sequence (calendar-light)

A focused two-track plan keyed on outcomes, not days.

**Phase 1 (target: ship in 2–3 working days)**
1. Add `format=reconstruction` branch + 501 stub. Commit.
2. Implement `export_reconstruction_als` with hardcoded preset. Land
   first happy-path curl test. Commit.
3. Wire studio UI button. Manual download test in Chrome + Safari.
   Commit.
4. Add E2E smoke test. Commit.
5. Add stage timings. Commit.
6. **SHIP.** Put in front of users. Collect reactions.

**Phase 2 (start the day Phase 1 ships)**
7. Add `catalog_version` flag to retrieval. Unit test. Commit.
8. Add sound-type → engine helper. Unit test. Commit.
9. Replace `_phase1_default_preset()` with retrieval call. Commit.
10. Re-run smoke test with retrieval enabled. Commit.
11. **SHIP Phase 2.**

One PR per step keeps reviews manageable and lets us roll back any
single change without losing the rest.

---

## 10. Out of scope for this plan

- Any further retrieval research, embedding upgrades, or catalog
  expansion. Retrieval is validated; the open risk is product, not
  retrieval.
- Top-5 audition UI (Phase 3 candidate, only if Phase 2 reveals that
  rank-1 is insufficient).
- Multi-track / drum / FX reconstruction.
- Suite-engine support.
- Job queue / async progress for `/api/export` — the export half is
  cheap; analysis already streams progress via SSE.

---

## 11. References

- This plan: `backend/E2E_RECONSTRUCTION_SHIP_PLAN.md`
- Combined ALS generator: `tone_forge/preset_catalog/preset_als_generator.py:447`
- Export router: `tone_forge_api.py:1947`
- Retrieval module: `tone_forge/preset_catalog/preset_retrieval.py:41`
- Audio ingest: `tone_forge_api.py:341`
- V2 corpus + retrieval status: `backend/V2_RETRIEVAL_EVAL_REPORT.md`
- Phase-1 default preset row (Analog Thick Chord Pad):
  `preset_catalog_output/catalog/catalog_analog_v2.json` (`preset_id`:
  `analog_thick_chord_pad`)
- Reconstruction Trial harness (post-ship validation only):
  `backend/scripts/reconstruction_trial_runner.py`,
  `backend/RECONSTRUCTION_TRIAL_PLAN.md`
