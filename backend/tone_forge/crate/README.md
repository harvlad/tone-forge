# Vinyl Crate

A shared, curated, **read-only** donor pool of legally-clean tracks (CC0 /
CC-BY from Jamendo, Free Music Archive, ccMixter). Users **search/browse** it by
rich metadata and Jamn **matches** it to the current session — surfaced as
crate-digging. It **reuses the borrow engine** (`performance/borrow.py`): a
crate track's stored analysis is adapted into a borrow *entry* and fed to the
SAME donor ranker + render/mount path a user's own songs use. Nothing about
loop rendering, caching, or client mounting is new; only the *pool* is
(shared + curated + license-bearing).

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
