# Experimental Specialist Pipeline Integration

Status: **Implemented — INTERNAL/EXPERIMENTAL. Default behavior unchanged.**
Relation: first practical productionization step after Specialist Discovery Wave 3
(`backend/lab_data/reports/discovery_2026-07/wave3_report.md`, overall gate A).
Implements the specialist-router seam of `INTENT_DRIVEN_ANALYSIS_ARCHITECTURE.md`
(§5 capability routing, §21 registry promotion) at minimum viable scope; feedback
design follows `MUSICAL_PLAYABILITY_EVALUATION.md` (pairwise preference, no scores).

## What exists

```
POST /api/analyze-upload  (+ engine, target_family multipart fields — optional)
        │  payload → JobRegistry → /api/engine/claim descriptor
        ▼
local_engine/remote_worker.run_job
        │  engine: job payload > TONEFORGE_ANALYSIS_ENGINE env > "current"
        ▼
local_engine/analysis_worker.run_file_analysis(engine, target_family)
        │
        ├─ engine == "current"  → EXACT existing pipeline (default; untouched)
        │
        └─ engine == "experimental_specialist"
             ├─ separation: htdemucs_6s (MIT; guitar/piano stems exist)
             │              fallback → htdemucs on failure (recorded)
             ├─ routing: tone_forge/specialist/router.resolve(family)
             │           from specialist_registry.json (human-promoted,
             │           license-guarded; Lab never writes it)
             ├─ target stem → specialist transcriber (official inference,
             │           pinned config) — riley_bass | riley_guitar | kong_piano
             ├─ REGISTER NORMALIZATION as explicit stage
             │           (register_passthrough v1: shift 0 — sounding pitch
             │            preserved for real audio; Slakh +12 stays a Lab rule)
             ├─ all other stems → current extractors, unchanged
             └─ result["analysis_engine"] + result["specialist_provenance"]
```

## Key files
- `backend/tone_forge/specialist/` — `specialist_registry.json`, `registry.py`
  (license guard), `router.py` (RoutingRequest/Decision, config_hash),
  `runner.py` (official-inference wrappers, midi-dict shape identical to
  `extract_midi_hybrid`), `normalization.py`, `feedback.py`.
- Edits: `analysis_worker.py` (engine resolution, 6s separation, specialist
  branch with recorded fallback, provenance), `remote_worker.py` (param
  passing + env), `tone_forge_api.py` (form fields, claim descriptor,
  `/api/debug/specialist-*` endpoints).
- Tests: `backend/tests/test_specialist_integration.py`.

## How to use (internal)
1. On the engine-worker machine: `export TONEFORGE_ANALYSIS_ENGINE=experimental_specialist`
   and `export TONEFORGE_TARGET_FAMILY=bass` (or guitar/keys). Restart worker.
   Every analyzed song then uses the experimental engine — no app change needed.
2. Or per-request: send `engine=experimental_specialist&target_family=bass`
   multipart fields to `/api/analyze-upload` (iOS: additive `extraFields` in
   `JobClient.submit`; not yet wired into any UI — deliberate).
3. A/B: analyze the same song twice (once per engine). Each run is a separate
   job + history entry (production has no analysis cache, so both persist —
   cache isolation comes free). Open either session in JAMN.
4. Inspect: `GET /api/debug/specialist-provenance/{history_id}` (engine,
   registry/config versions, per-stage timings, failures, midi methods).
   Target-stem audio auditable via the session's stem URLs.
5. Feedback after actually playing: `POST /api/debug/specialist-feedback`
   `{verdict: BETTER|SAME|WORSE, song_hash, target_family, tags[...], note}`.
   Stored at `backend/data/feedback/specialist_feedback.jsonl`.

## Guarantees
- **Default = current.** No env, no field → byte-identical behavior.
- **License gate in code**: `yourmt3` (Y2) and `bs_rofo_sw` (UNKNOWN) are in the
  registry with BLOCKED status; `resolve()` raises rather than run them.
- **Failure safety**: routing/registry errors → current pipeline (reason
  recorded in provenance); specialist inference failure on a stem → current
  extractor fallback for that stem + `specialist_failure` recorded; separator
  failure → htdemucs fallback recorded. Nothing leaves a song stuck.
- **UI never sees model names** — provenance lives in the result JSON /debug
  endpoints only; SessionBundle builders ignore the extra keys.
- **Normalization is a stage** with its own provenance; raw sounding-pitch
  output is preserved by default (the real-audio register rule is
  deliberately unresolved — see registry notes).

## What experimental changes for the musician TODAY
Same played experience except: separation runs htdemucs_6s (6 stems — guitar
and piano become real stems instead of living in "other"). Transcription
artifacts differ, but no iOS surface consumes per-note MIDI yet (grounded in
the intent doc §15/16) — they are generated-and-stored for the future
note-aware Practice experience. That is intentional: this phase validates the
chain end-to-end on real songs and accumulates provenance + feedback.

## Not productionized (deliberate)
- Default engine flip; any consumer-facing selector UI.
- yourmt3 (legal review Y2), BS-Rofo-SW (license unknown).
- User-supplied-stem import path: architecture supports it conceptually
  (a supplied stem would bypass `separate_all_stems` and feed the router
  directly), but the import flow has no stem-upload UX — DOCUMENTED ONLY.
- Slakh +12 as a production rule; per-stem register estimation.
- Note-practice UI; SongBundle note fields.

## Known debt
- `keys` family routes to a piano-validated model (kong); organ/synth-heavy
  keys stems are outside its evidence — caveat recorded in provenance.
- Kong runs CPU-only in the runner (device selection conservative).
- The engine flag is job-global; per-family multi-target analysis is one
  target per run for now.
- specialist runner tempo defaults to 120bpm in the MIDI container when the
  session tempo isn't threaded through (note times are absolute seconds and
  unaffected; only the MIDI meta tempo is cosmetic).
