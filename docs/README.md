# ToneForge Documentation

This directory holds active documentation. Strategy is **frozen**.

## Active

- [`/EXECUTION_PLAN.md`](../EXECUTION_PLAN.md) — the current execution plan. Supersedes all archived strategy documents.
- [`/backend/EXTRACTION_STATUS.md`](../backend/EXTRACTION_STATUS.md) — current state of the (frozen) MIDI extraction pipeline.
- [`/backend/ROADMAP_STATUS.md`](../backend/ROADMAP_STATUS.md) — current state of the (frozen) retrieval / reconstruction systems.

## Archived

`docs/_archive/` contains earlier strategy, RCA, milestone, and trial-plan documents that are no longer load-bearing. They are kept for historical reference. Do not extend them; do not write new ones in the same spirit. New planning lives in `EXECUTION_PLAN.md`.

## Rules

1. Strategy is frozen. Do not add new strategy documents.
2. RCA / debugging notes for frozen subsystems do not belong here. Use commit messages.
3. New documentation for active subsystems (Connect, Jam, Session, Monitor Chains, Chord Detection) lives next to the code, e.g. `backend/tone_forge/monitor/README.md`.
