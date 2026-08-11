# Riley Phase II — Closeout

**Phase II is closed. Verdict: Riley reaches near-parity with production `htdemucs_6s` but
does not beat it. The separator R&D is rested here.** 2026-08-11.

## The question Phase II asked
Phase I reached statistical parity with production stock. Phase II asked: **can we make a
Riley-native separator that decisively *beats* production stock — and if so, is the lever data
or the objective?**

## What was tested (both levers, exhausted)
| lever | runs | result |
|---|---|---|
| **Diverse real data** | v7 (275) → v8 (395) → v9 (456), scaling Cambridge-MT diversity | Climbed SI-SDR monotonically (mean Δ vs stock −0.26 → +0.89 → +1.83) but **zero perceptual gain** — v9 blind-tied/lost to stock. The SI-SDR climb was train/eval distribution-alignment inflation. |
| **Perceptual objective** | v10 (perceptual STFT loss) → v11 (10× weight + loudness-weighting) | Moved *perception* from clearly-behind (v9 waveform, stock 8–3) to **near-parity** (v11: 10/28 ties). But on a proper 28-clip blind, **stock still preferred 2:1 (12–6)**. |

## Final result — the deciding gate (real-song blind vs stock)
- v9 (waveform loss): stock preferred **8–3**.
- v10 (perceptual, timid): **5–4–3** (faint Riley edge, 12 clips).
- v11 (perceptual, 10×): **5–3–4** (12 clips) → looked like a faint win…
- **v11, 28-clip blind: v11 6 · stock 12 · tie 10 — stock preferred 2:1.** The small-sample
  edge was noise; the larger blind reversed it.

**Riley ties production on many songs (near-parity) but loses the decided majority ~2:1. Not
superiority. Not deployable. Not commissioned.**

## Why we stop (not a swing at architecture)
Two independent levers both land at "close but behind." The perceptual lever is the one that
moved the ear, and even a 10× push plateaued short of stock. The only untested lever is a
different *architecture* — a large investment with no guarantee against a strong, well-trained
production model. The evidence says the ceiling is real. We rest, as with the guitar-planner
closeout, rather than spend into a plateau.

## What Phase II produced (banked, reusable)
- **Gate discipline, hard-proven.** SI-SDR is a false-positive machine — it would have greenlit
  Tier-3 commissioning spend at v8/v9; the blind vetoed every time (5 metric-only false-positives
  caught overall: B1, AudioShake, C2-synthetic, 12-clip-v9, 12-clip-v11). **Small-n blinds
  mislead — always ≥24 clips.** This saved real money.
- **The objective>data finding.** Diverse data moved the metric, not the ear; the objective moved
  the ear. The single most useful result for any future separator work.
- **Reusable platform & assets:** the Data Factory, Coverage Planner, promotion gate, the
  Cambridge-MT catalog (`cambridge_catalog.json`, 632 songs), the Mac-build-ship acquisition
  pipeline (Cloudflare-blocked sources → compact local stems → Hetzner), all corpora (ds_v5–v9),
  eval sets (52-clip diverse), and blind consoles.
- **Boundary respected:** declined to automate around Cloudflare bot-detection (proxy /
  fingerprint / stealth); solved the acquisition legitimately (human browser + local build).

## Cost
~$35 GPU across the whole Phase II arc.

## Status
**Riley separator R&D: RESTED at near-parity with production.** Phase I (parity) and Phase II
(near-parity, objective>data, no decisive beat) both complete and frozen. The platform stands
for any future resumption; no further training/acquisition planned. Detailed run-by-run record:
`docs/RILEY_PHASE_II_A_FINDING.md`.
