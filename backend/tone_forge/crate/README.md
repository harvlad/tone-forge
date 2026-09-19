# Vinyl Crate

A shared, curated, **read-only** donor pool of legally-clean tracks (CC0 /
CC-BY from Jamendo, Free Music Archive, ccMixter). Users **search/browse** it by
rich metadata and Jamn **matches** it to the current session — surfaced as
crate-digging. It **reuses the borrow engine** (`performance/borrow.py`): a
crate track's stored analysis is adapted into a borrow *entry* and fed to the
SAME donor ranker + render/mount path a user's own songs use. Nothing about
loop rendering, caching, or client mounting is new; only the *pool* is
(shared + curated + license-bearing).

## HARD RULE — never compromise on extraction quality

The crate is ingested **once** and matched/borrowed from **forever**. There
must never be a reason to re-analyse a crate track because we realised we
under-extracted it. So the ingest extracts the **complete** set of signals the
crate's purpose (intelligent match + best-stems borrow) can use — **stems,
melody, harmony, rhythm, key, structure, energy** — and **never drops one to
make a fleet run finish faster**.

- **Capture every data point, once.** `PipelineConfig.crate` is the full
  `deep()` analysis (+ the crate stem-serve base): stems, MAX-fidelity melody
  (the full ensemble, not basic-pitch-only), harmony, groove, key, structure,
  energy, **plus** every metric that could ever assist matching/ranking —
  `analyze_quality` (stem_quality/contamination/artifacts), `synth_behavior`
  (timbre), `provenance`, `waveform`. There is deliberately **no "skipped"
  list**.
- **Speed is a logistics problem, not a quality dial.** If a run is too slow:
  canary 1–2 pods to measure real per-track time, size the pod watchdog
  (`WATCHDOG_SEC`) to fit, add pods, or **make the slow stage fast** — never
  delete or downgrade a signal. The old offender was the ensemble MIDI path
  (torchcrepe + basic_pitch) running CPU-bound on the pod (~17 min/track); it
  was GPU-accelerated on 2026-09-07 (torchcrepe → CUDA, basic_pitch → ONNX-GPU
  with TensorFlow kept off) and now runs on the A40, so the fix was to
  GPU-accelerate it, **not** to fall back to basic-pitch or drop MIDI.
- **Pinned by CI.** `tests/test_crate.py::TestCrateExtractionQuality` asserts
  the crate captures everything `deep()` does for every analysis-signal flag —
  a change that drops melody/stems, reverts the ensemble to basic-pitch, or
  turns off any metric fails CI. The full pad-quality / best-**version**
  pipeline (flatness/collapse/parent veto + parent-vs-children duel in
  `serve.kit_payload`) applies to crate tracks: the admit gate stores a track
  only if that exact builder yields ≥1 surviving pad.

## Modules

| Module | Role |
|---|---|
| `registry.py` | Load/serve the shared catalog + license/analysis sidecars; `to_borrow_entry` bridges a CrateTrack → the borrow ranker's `{id,name,result}` shape. |
| `search.py` | Faceted metadata browse (genre/tempo/key/Camelot/mood/tags/stems/license) + free-text. Coexists with match ranking. |
| `match.py` | Session-aware **weighted, extensible** ranking that extends borrow's score. `_MATCH_WEIGHTS` names every signal; deferred seams sit at weight 0. |
| `ingest.py` | The ingestion pipeline (download → license sidecar → analyze → **blind-gate** → merge → register). Code only; the analyzer/downloader are injected. |

The real analyzer + downloader are wired in `backend/scripts/ingest_crate.py`
(which may import `unified_pipeline`) so this package stays a leaf over
`contracts` + `borrow` and the subsystem-boundary check holds.

## Storage (`backend/data/crate/`, override via `TONEFORGE_CRATE_DIR`)

```
registry.json          searchable manifest (one CrateTrack per row)
licenses/<id>.json     CrateLicenseRecord — the compliance artifact
analysis/<id>.json     full stored analysis (chords, sections, stems_paths,
                       performance_graph, melody) — the borrow donor `result`
```

Distinct from a user's own songs (`data/history.json`): the crate is GLOBAL
(no `owner_id`), immutable/curated, and license-bearing; the retention/delete
path never touches it and it is never owner-filtered.

## The match model (`score_crate`)

`score = (Σ wᵢ·cᵢ·sᵢ / Σ wᵢ·cᵢ) · 2^(−|semis|/6)`, gated first. Hard gates
(veto, never rank): octave-folded tempo band (`_STRETCH_LIMIT`), harmonic floor
(≥0.2) for melodic stems, clean-export filter (CC-BY-SA), meter clash (seam),
stem availability. Weighted sub-scores in `[0,1]`, each confidence-scaled:

| signal | weight | source | status |
|---|---|---|---|
| tempo | 0.25 | `tempo_bpm` (fold distance) | live |
| harmony | 0.28 | `harmonic_compat` (pc-histogram + key), ×`key_confidence` | live |
| melody | 0.15 | melody lane (register/scale/contour) | live |
| energy | 0.10 | aggregate RMS / bar_energies | live |
| genre | 0.10 | source genre/tags (similar ⇄ contrast) | live |
| instr | 0.12 | stem complementarity (fill the session's holes) | live |
| groove / timbre / vocal / loudness / chord_prog | 0 | groove.py / spectral / has_vocals / LUFS / chord lane | **seam** |

Adding or removing a signal is a one-line `_MATCH_WEIGHTS` edit. Normalizing by
`Σ(wᵢ·cᵢ)` keeps the score in `[0,1]` no matter which signals a pair has, so a
track missing melody/energy is ranked fairly on the rest. Chord-**progression**
matching is pre-declared at weight 0 — the later chord-accuracy fix turns it on
with a single non-zero edit.

## License doctrine

The CC license on each track (not any platform API) authorizes streaming +
derivatives + export, so tracks are self-hosted with a stored license record.
Attribution MUST display wherever a crate track appears (row + rendered pad).
`export_encumbered` is `True` only for CC-BY-SA (copyleft) and is the single
boolean the export path gates on — it is stored, never re-derived. A track
missing complete attribution is rejected at ingest, never soft-shipped.
