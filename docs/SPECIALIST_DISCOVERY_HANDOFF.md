# Handoff → Specialist Transcription Model Discovery

From: intent-driven analysis architecture review (see `INTENT_DRIVEN_ANALYSIS_ARCHITECTURE.md` v2).
Status: **advisory. Do not abandon your current transcription-model discovery.** Fold these questions in.

## Why you're getting this

Architecture review surfaced a possibly more fundamental **upstream** limitation than transcription-model choice.

**Current production separation:** `htdemucs_6s` gives **family-level** stems only —
`drums, bass, other, vocals, guitar, piano`.

It does **NOT** provide same-instrument source-instance separation. The existing
`decompose_stem_pan` (`backend/tone_forge/stem_separator.py:449`) is only a stereo
mid/side heuristic for hard-panned / double-tracked content — **not** general
lead/rhythm separation. Do not treat it as such.

Downstream effect: benchmark numbers on clean Slakh stems may **overestimate**
production accuracy, because real separated stems carry bleed and mix multiple
same-family parts into one stem.

## Research questions to incorporate

**A. Same-instrument source separation.** Credible existing networks/research for:
multiple-guitar separation; lead/rhythm guitar; lead/backing vocal; multiple-vocal;
piano/organ/synth; same-family source-instance separation generally.

**B. Downstream quality bottleneck.** Where practical, determine experimentally whether
transcription quality fails primarily from: (1) transcription-model capability,
(2) family-level stem contamination, (3) inability to isolate individual same-family
parts, or (4) combinations. **Specifically: could a better separator yield a larger
downstream transcription gain than replacing the transcription network?**

**C. Transcription input assumptions.** For every promising transcription candidate,
record what it expects: full mix / family-isolated stem / clean single instrument /
monophonic / polyphonic. Flag any model whose good numbers assume clean isolated input.

**D. Specialist separators / routing.** Assess whether separation itself should route:
`song → family target → best separator for that family+regime → candidate part →
best transcription specialist`, rather than assuming `htdemucs_6s` is universally optimal.

**E. Same-family oracle.** Where datasets provide ground-truth source instances, measure:
ideal isolated-source transcription **vs** Demucs family-stem transcription **vs** real
mixture transcription — to quantify the actual separation ceiling.

## Product priority (do not over-rotate)

- Same-instrument separation is **NOT** required for the current MVP. Architecture
  conclusion: **family-level targeting is sufficient for current Jam/Perform.**
- It matters for **future** capabilities, e.g. *"Practice Lead Guitar while keeping
  Rhythm Guitar in the backing."* Treat as **strategic R&D** unless evidence shows it's
  needed sooner.
- Note: current Jam/Perform/Learn consume **no per-note transcription** — they render
  from chords/key/sections/stems. High-quality transcription is future note-aware
  Practice/Learn R&D, not on today's critical path. So transcription-model wins are
  valuable for the roadmap, not for shipping the current product.

## Compute

- **Do not rent GPUs because of this amendment.** Continue the existing staged discovery.
- If source-separation candidates justify paid GPU benchmarking, include them in the
  eventual GPU candidate report with **estimated cost + expected information gain**.
- **No spend without explicit approval.**
