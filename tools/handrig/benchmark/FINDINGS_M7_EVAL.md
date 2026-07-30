# M7 Evaluation — trajopt vs naive, rigorous close-out

Date: 2026-07-30. Frozen benchmark `suite.json` (benchmark_version 0.1.0, metric_version 0.0.0).
Reports: `results/movement_report.json` (naive, config_hash f7182046a7ee5c19),
`results/trajopt_report.json` (trajopt, config_hash b0508865888da7db). Gate: `check_m7.py`.
Nothing tuned/changed after seeing results. Objective: does whole-phrase trajectory
optimization beat memoryless per-note solving?

## VERDICT: B — MIXED

Trajopt shows two genuine phrase-level advantages on the single demonstration phrase, but
introduces a **pervasive hand-wander regression** on every still-hand phrase and does not
clear the pre-registered V1 gate. The architectural thesis (phrase-level > memoryless) is
**NOT established** by this evidence.

## Pre-registered V1 gate (check_m7.py) — 3 PASS / 4 FAIL / 1 deferred
| Gate | Result | Detail |
|---|---|---|
| 1 contact non-regression (hard) | PASS | all trajopt phrases feasible ≤6mm |
| 2 no hard-gate regression (hard) | PASS | no feasible→infeasible |
| 3 shift economy | PASS | suite-mean shift 0.40 → 0.20 |
| 4 finger reuse +25% | FAIL | 0.33 → 0.33 (fingering FIXED — gate structurally unmovable) |
| 5 travel −20% each | FAIL | tip 242→240 (−1%), root 18→23 (**worse**) |
| 6 anchor retention ≥0.9 | FAIL | 0.50 → 0.50 (fingering FIXED — structurally unmovable) |
| 7 Movement Score +0.15 | FAIL | 0.889 → 0.913 (**+0.024, 6× short**) |
| 8 visual | DEFERRED | render==solver 0.000mm established (M8); human call |

Hard gates (1,2) hold. Gate 7 — the aggregate quantitative bar — misses by 6×.
Gates 4 & 6 cannot move under fixed fingering (noted, not re-gated; per instruction we do
NOT soften the criteria — they stand as FAIL).

## Phrase-by-phrase (aggregate Movement Score)
| Phrase | naive | trajopt | Δ | verdict |
|---|---|---|---|---|
| single | 1.000 | 1.000 | 0.000 | SAME |
| repeated_note | 1.000 | 1.000 | 0.000 | SAME |
| string_crossing | 0.782 | 0.782 | 0.000 | SAME |
| one_shift_scale | 0.661 | 0.782 | **+0.121** | TRAJOPT BETTER |
| sustained_anchor | 1.000 | 1.000 | 0.000 | SAME |

Trajopt improves 1 of 5, ties 4, worsens 0 on score. But score-ties hide behaviour
differences (below).

## Item 3 — per-event shift quantification (one_shift_scale)
NAIVE:  ev0 s3f2 index 0.0mm | ev1 s3f4 ring 0.08mm | ev2 s3f9 index **60.7mm SHIFT contact 3.734mm** | ev3 s3f11 ring 30.8mm SHIFT contact 0.062mm → **2 shifts**
TRAJOPT: ev0 s3f2 index 0.0mm(contact 0.019) | ev1 s3f4 ring 5.4mm | ev2 s3f9 index **69.1mm SHIFT contact 0.070mm** | ev3 s3f11 ring **19.0mm (NOT shift, <20mm thresh)** → **1 shift**

Genuine positional commitment: trajopt makes ONE relocation to the upper position and holds
it for f11; naive relocates twice. BUT ev3 lands at 18.4mm (report) / 19.0mm (3D) — **just
under the 20mm shift threshold**. Near-tolerance; a stricter threshold would reclassify it.
Total root travel is NOT reduced (trajopt 93.5mm vs naive 91.6mm — slightly worse); the win
is in shift *count*, not travel economy.

## Item 4 — note-2 pose classification (naive 3.734mm vs trajopt 0.070mm)
Classification: **initialization / local-minimum issue, rescued by phrase context.**
The naive per-note solver warm-starts note-2 (s3f9) from the position-I pose of note-1; the
large jump lands it in a poor local basin (3.734mm — still within the 6mm gate, but visually
detached). Trajopt jointly optimizes with a shared root centroid + neighbour context, which
gives note-2 a better initialization and pulls it into a good basin (0.070mm). **This is a
legitimate, if modest, advantage of phrase-level planning — better per-pose conditioning via
global context — and is recorded, not normalized away.** Caveat: both are within the hard
gate, so it is a pose-quality gain, not a feasibility rescue.

## Item 7 — failure-mode falsification (the important finding)
**PERVASIVE HAND WANDER / QUIET-HAND VIOLATION.** On every phrase that should hold the hand
still, trajopt drifts the root where naive keeps it planted:
| Phrase | naive root travel | trajopt root travel | ratio |
|---|---|---|---|
| repeated_note | 0.01mm | 4.67mm | 467× |
| string_crossing | 0.52mm | 9.75mm | 19× |
| sustained_anchor | 0.29mm | 9.13mm | 31× |

`string_crossing` (all fret-4, one position) is the clearest: naive holds the hand within
0.5mm; trajopt wanders ~4mm up-neck and back (see `results/m7_root_trajectory.png`, right
panel). The naive memoryless solver reuses the prior pose and stays put; trajopt's L-BFGS-B
joint optimization (W_ROOT centroid + W_SMOOTH) finds marginally-lower-cost configs that move
the root a few mm. This is `POSITION_CHURN` / `quiet_hand` violation and it is GENERAL (3/3
still-hand phrases), masked only because magnitudes stay under the frozen tolerances (scores
tie). Trajopt also has higher root travel on ALL 5 phrases, incl. its own demo phrase.
Other predicted modes checked: no anchor-break, no contact-grazing (all ≤0.12mm), no shift-
refusal, no finger-flapping. The dominant real failure is the wander.

## What was learned
- Phrase context CAN improve individual pose conditioning (note-2: 3.734→0.070mm) — a real
  mechanism worth keeping.
- Trajopt reduces discrete shift COUNT on the one phrase built to need it (2→1), but not
  total travel, and partly via a near-threshold reclassification.
- The current trajopt objective (shared centroid + smoothness) actively harms hand stillness,
  introducing unnatural micro-wander everywhere. Memoryless pose-reuse is strictly better for
  still phrases.
- Score-ties are not behaviour-ties: the benchmark tolerances currently hide the wander.

## What remains unproven
- The core thesis (whole-phrase > memoryless) is unproven: 1/5 phrase improves, a pervasive
  regression exists, the V1 gate misses 6× on aggregate score.
- Whether the note-2 conditioning advantage generalizes beyond one large-interval jump.
- Whether a wander-free trajopt objective (penalize per-knot root motion, not just centroid
  deviation) would flip the picture — a design question for a future milestone, NOT pursued
  here per scope.

## Artifacts
- `results/m7_motion_naive_vs_trajopt.gif` — side-by-side demo-phrase motion (frozen
  render==solver frames, 1s/frame, no easing).
- `results/m7_root_trajectory.png` — hand-root along-neck vs event, naive vs trajopt, for
  one_shift_scale (commitment) and string_crossing (wander).
- `results/renders/best-one_shift_scale_{naive,trajopt}_{0..3}.png` — the 8 frozen frames.
- Reports + config hashes above. `/tmp/m7_analysis.py`, `/tmp/m7_artifacts.py` = generators.
