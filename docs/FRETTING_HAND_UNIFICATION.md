# MPFB-Skeleton Unification — results

The bridge concept is REMOVED. There is now ONE authoritative hand: the MPFB skeleton.
The biomechanical solver optimizes MPFB joint state directly, using FK validated against
Blender, so solver-space and render-space are the same geometry by construction.
NOT self-approved — the visual decision is yours.

## Pipeline (final)

contacts → contact regions → **MPFB skeleton FK** → biomechanical + synergy objective →
optimize MPFB joint state (root xform + finger/thumb eulers) → MPFB pose → render.
No abstract solver skeleton, no angle transfer, no per-finger correction table.

## Phase 1 — rig characterization

`mpfb_characterize.py` dumps the fretting bones (5 digits × 3 phalanges + wrist),
each with parent, length, and armature-space rest matrix (`matrix_local`), plus the base
fretting orientation. Machine-read from Blender, not hand-copied. Finger proximals hang
off metacarpals; at rest those cancel, so finger FK needs only the phalanx rest matrices.

## Phase 2 — Python MPFB FK, validated (THE gate)

`mpfb_fk.py` reproduces Blender's pose FK:
`M_pose[b] = M_pose[parent] @ M_local[parent]⁻¹ @ M_local[b] @ B[b]`.
`validate_fk.py` sets 40 random physiological poses, compares Python fingertip/joint
world positions to Blender's evaluated rig. **Pre-registered tol 0.5 mm. Result:
max discrepancy 0.0002 mm → PASS.** (A harness bug — capturing `matrix_world` before a
`view_layer.update()` — initially showed 587 mm; the FK itself was always exact.)

## Phase 3–6 — unified solver

`solver/mpfb_solver.py` optimizes a 26-D MPFB state: **root translation+rotation (6)** +
per-finger [MCP flex, MCP abduction, PIP, DIP]×4 (16) + thumb (4). Reuses the validated
physiological pieces expressed over MPFB DoF: contact REGIONS, fretting-ready neutral
strain, synergy manifold, splay/coupling, finger/finger + board collision. Root
placement is part of the solve (Phase 4) with a cheap move regulariser, so the hand
relocates rather than stretches. Thumb solved in the same skeleton, opposed behind the
neck (Phase 5). No MANO data, no chord logic, no per-finger hacks.

## Render == solver (permanent regression)

`mpfb_render.py` applies the solved MPFB state 1:1 to the rig and, before rendering,
asserts Blender fingertip == solver-recorded fingertip. **All fixtures: 0.000 mm.**
Render-space correctness can no longer diverge from solver-space correctness.

## Metrics (render space)

| Fixture | Status | Contact (render) | Collision | Board | Thumb behind | Strain |
|---|---|---|---|---|---|---|
| single | OK | 0.0 mm | 0 | 0 | yes | 0.03 |
| two | OK | 0.0 | 0 | 0 | yes | 0.04 |
| D | OK | 0.02 | 0 | 0 | yes | 0.17 |
| G | OK | 0.01 | 0 | 0 | yes | 0.25 |
| C | OK | 0.01 | 0 | 0 | yes | 0.12 |
| three (arb.) | OK | 0.0 | 0 | 0 | yes | 0.11 |
| wide stretch | OK | 0.01 | 0 | 0 | yes | 0.45 |
| impossible | **INFEASIBLE** | 115.8 | — | — | — | — |

8-note phrase (prev-chained) finger-move deltas: [0.3, 0.33, 0.14, 0.11, 0.15, 0.16,
0.01] — small, coherent successive poses; no explosions, no frame discontinuities.
Solve ~13–24 s/pose (offline; render_vs_solver == 0). Saved for a future trajectory
fixture.

## Success gates (brief's 17)

1. FK matches Blender within tol — **0.0002 mm** ✓
2. No abstract→MPFB angle bridge — removed ✓
3. Single-note compact natural improvement — ✓ (sheet, col 3)
4–6. D/G/C contacts correct in RENDER space — 0.01–0.02 mm ✓
7. Chords no longer bunch/fist — ✓ (fingers reach distinct frets)
8. Unused fingers compact — ✓
9. Whole-hand/root motion reaches contacts — ✓ (root in the solve; wide stretch)
10. Thumb opposed behind neck — ✓ all fixtures
11. Wide feasible stretch, no grotesque distortion — ✓
12. Impossible remains INFEASIBLE — ✓ (115.8 mm)
13. 8-note phrase coherent — ✓ (small deltas)
14. No chord-specific code — ✓
15. No per-finger correction tables — ✓
16. No MANO/non-commercial pose data — ✓
17. Render-space == solver-space geometry — ✓ (0.000 mm assertion)

## Honest remaining weaknesses (NOT a self-pass)

- **Naturalness still short of a seasoned player in places**: single-note fingers fan a
  little; ring/pinky read slightly stiff on some chords. Better than OLD/BRIDGE, not yet
  indistinguishable from a photo.
- **Thumb is geometrically behind the neck but its articulation is coarse** (a simple
  opposition curl), not a fully solved opposition against each finger span.
- **Interior contour lines** on the mesh are faint/occasionally noisy at strong curls
  (render language, deferred — Camera A frozen, not tuned here).
- **Solve time ~13–24 s/pose** — fine offline; needs analytic Jacobians before
  phrase-length trajectory optimisation.
- **F# barre** deferred (line-contact) — did not fall out for free; not attempted here.
- The **wide stretch** is feasible but the pinky reads slightly over-extended; the
  root-move-vs-stretch balance could prefer relocating a touch more.

## Visual gate — for YOUR decision

Two questions, both must be YES:
1. **Are the requested notes physically being fretted?** Quantitatively YES — render-
   space contact ≤ 0.02 mm on every fixture, verified by the render==solver assertion.
2. **Does it look like an experienced guitarist's fretting hand?** Materially improved —
   the unified column simultaneously hits the frets AND reads as a compact cupping hand,
   resolving the old contact-correct-XOR-natural trade. I am NOT self-passing #2; the
   dot-free `unified_sheet.png` and hand-only renders are for your judgment.

Deliverable: `tools/handrig/unification/unified_sheet.png` (OLD | BRIDGE | UNIFIED |
UNIFIED-hand-only, on single/D/G/C) + per-fixture renders + `mpfb_states.json`.
Not integrated into JAMN.
