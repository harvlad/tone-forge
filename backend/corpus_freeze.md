# Corpus Freeze — Phase 0C Canonical Reference

**Status:** FROZEN as of 2026-06-21 (Step 5 of the post-Phase-0D workflow).

This document pins the bundle set used as evidence for the signal-taxonomy
investigation (`signal_taxonomy_report.md`, Decision B). Any future
signal-discovery work that claims to *replace*, *extend*, or *override*
the taxonomy's conclusions must re-run on these exact bundle IDs first.
Adding new songs is fine; *removing* canonical songs is a corpus
regression and requires re-stating Decision B.

---

## A. Canonical 6 — the taxonomy reference

These are the bundles the Step 4 taxonomy harness operated on. Every
number in `signal_taxonomy_report.md` traces back to these IDs. They
are the canonical training/calibration set for the H-centric milestone.

| Slug                  | Bundle ID  | Genre                  | Sections | Source URL                                                       |
|-----------------------|------------|------------------------|----------|------------------------------------------------------------------|
| stairway_to_heaven    | `73b5931b` | classic_rock           | 12       | https://www.youtube.com/watch?v=QkF3oxziUI4                      |
| hotel_california      | `07320370` | classic_rock           | 9        | https://www.youtube.com/watch?v=dLl4PZtxia8                      |
| wish_you_were_here    | `9fb65b01` | classic_rock           | 8        | https://www.youtube.com/watch?v=K6qj09OHvjw                      |
| romance_de_amor       | `5365ab83` | classical_solo_guitar  | 15       | https://www.youtube.com/watch?v=I7XnCPtfa14                      |
| sex_on_fire           | `b640c78a` | indie_rock             | 20       | https://www.youtube.com/watch?v=RF0HhrwIwp0                      |
| whats_my_age_again    | `29b31695` | pop_punk               | 8        | https://www.youtube.com/watch?v=K7l5ZeVVoCA                      |

Genre coverage: 4 distinct (classic_rock×3, classical_solo_guitar,
indie_rock, pop_punk). Acknowledged narrow; broader coverage is the
extended-5 set's job (Section B). The canonical 6 already exercise the
two known signal-collapse modes:

- **Romance de Amor** — solo classical, no vocals/drums. Confirms
  D/F's arrangement-signal failure mode.
- **What's My Age Again** — homogeneous 4-chord vocab over a pop-punk
  arrangement. Confirms H2's structural-signal failure mode (worst H2
  in the canonical set, 0.198).

These two are the corpus's *adversarial* songs and must remain frozen
in. Removing either invalidates the failure-mode evidence behind
Decision B's CONDITIONAL classification of H.

---

## B. Extended 5 — future-validation reserve

These bundles carry `corpus_run: True` from earlier Phase 0C trial runs
but were **not** included in Step 4's taxonomy harness. They are not
part of the H-decision evidence base. They exist as a held-out
sanity-check set the H-centric extractor can be validated against
*after* it has been built (Step 6 → Step 7 transition), without
introducing data leakage.

| Slug          | Bundle ID  | Inferred genre tag (for future use) | Source URL                                          |
|---------------|------------|-------------------------------------|-----------------------------------------------------|
| enter_sandman | `6f5f5634` | metal                               | https://www.youtube.com/watch?v=CD-E-LDc384         |
| skinny_love   | `d7804a68` | indie_folk                          | https://www.youtube.com/watch?v=ssdgFoHLwnk         |
| get_lucky     | `422f5db5` | disco/funk                          | https://www.youtube.com/watch?v=5NV6Rdv1a3I         |
| humble        | `5dc3da17` | hip_hop                             | https://www.youtube.com/watch?v=tvTRZJ-4EyI         |
| bad_guy       | `8ccdf229` | electropop                          | https://www.youtube.com/watch?v=DyDfgMOUjCI         |

Genre coverage on the extended-5: metal, indie_folk, disco/funk,
hip_hop, electropop. Five distinct genres outside the canonical 6's
reach. They will materially stress-test the H-centric extractor's
generalization in Step 7.

---

## C. Exclusion list (corpus pollution — must be filtered)

`backend/song_form_phase0c/` contains 25 `C_composite_DF__*.json`
artifacts from prior trial runs. The cross-corpus aggregate
(`cross_song_stability.csv`) currently includes all 25 because the
Phase 0C harness eligible-bundle filter was permissive. The following
bundles are **non-canonical** and must be excluded from any
forward-looking corpus aggregate:

- All `untitled__*` bundles (no slug, no source URL — produced by ad-hoc
  uploads during development; 14 bundles).
- All `sex_on_fire__*` bundles except `b640c78a` (4 stale variants from
  pre-Phase-0D pipeline iterations).
- All `the_rumjacks_an_irish_pub_song_*` bundles (2 trial bundles for an
  earlier section-quality investigation; not on canonical list).
- All `stairway_to_heaven__*` bundles except `73b5931b` (the Phase 0D
  canonical Stairway is the one with `corpus_run: True`).

These 25−11 = **14 polluting bundles** must not appear in any
"freeze-respecting" aggregate. The corpus-freeze-respecting filter is:

```python
def is_canonical_or_extended(history_entry) -> bool:
    return (
        history_entry.get("corpus_run") is True
        or history_entry.get("id") in {
            "73b5931b",  # stairway (Phase 0D canonical)
            "b640c78a",  # sex_on_fire (Phase 0C canonical)
            "29b31695",  # whats_my_age_again (Phase 0C canonical)
        }
    )
```

(The three explicit IDs are pre-`corpus_run`-flag bundles that landed
before the marker was added; they are otherwise indistinguishable from
trial bundles by metadata alone.)

---

## D. Re-derivation contract

Any future script that claims to reproduce the Step 4 taxonomy must:

1. Load only the 6 bundle IDs in Section A.
2. Use the persisted JSON bundle's `chords` and `sections` arrays — no
   audio re-decode. (The taxonomy harness is purely structural; audio
   re-decode would invalidate the determinism guarantee.)
3. Use `chord["start_s"]` / `chord["end_s"]` / `chord["symbol"]` and
   `section["start_s"]` / `section["end_s"]` exactly as persisted.
4. Re-emit `step4_taxonomy_table.csv` and `step4_taxonomy_persection.json`
   with the same column/key ordering used in the originals (so they
   diff cleanly).

If the re-run produces numerically different output, the discrepancy
must be explained before any taxonomy claim is updated.

---

## E. Out-of-scope for this freeze

- Threshold calibration (Step 6+ territory).
- Adding more songs to the canonical 6 (would require restating the
  failure-mode evidence base in `signal_taxonomy_report.md` Section C).
- Re-running D/F on the extended 5 (deferred until the H-centric
  extractor exists; otherwise we generate disposable numbers).
- Fixing the `.als` export `note_data[:4]` runtime regression observed
  in the dev server log — that's a separate triage item not gated on
  the corpus freeze.
- The `slug` / `genre` columns currently read `unknown` in
  `cross_song_stability.csv` for the three pre-`corpus_run` canonical
  bundles. This is a metadata-inference bug in the Phase 0C harness,
  not a corpus-freeze concern; the numerical signal columns are
  correct.
