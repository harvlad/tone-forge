# Biomechanical Pose Pass — results

Camera A frozen. This pass: diagnose why the render looked like per-finger targeting,
then improve the whole-hand pose via contact regions + synergy/strain, and unify the
solver with the renderable MPFB hand. NOT self-approved — for visual judgment.

## Diagnosis (see FRETTING_HAND_POSE_DIAGNOSIS.md)

The renders were **literally Blender per-finger IK** (`add_ik`: one IK constraint per
finger to a point target) — the biomechanical solver never touched the MPFB mesh. Plus
the solver's own contact-point domination (weight 8 vs naturalness ~0.35–1.2), exact
point targets (zero spatial freedom), an open-hand neutral, and no cupping prior.

## What was implemented (all general, no chord logic, no MANO)

1. **Contact REGIONS** (`contacts.py`): a fingertip is valid ANYWHERE in the fret slot
   (x behind the wire), on the correct string (±40% string gap in z), pad touching
   (y band). Zero penalty inside → the whole-hand terms shape the pose freely.
2. **Fretting-ready neutral** (`anatomy.py`): unused fingers pull to a compact ready
   curl (MCP 28°, PIP 45°, DIP 30°, ~0 splay), not a flat open hand; splay comfort
   tightened 8°→4°.
3. **Rebalanced solver** (`hand_solver.py`): strain 0.35→2.6, manifold 1.2→3.0,
   splay 0.9→2.0 — naturalness now dominates inside the free contact region.
4. **Unification bridge** (`export_poses.py` + `apply_poses.py`): the solver's joint
   angles drive the MPFB rig via forward kinematics (NO IK), rendered at Camera A with
   the frozen render language, with a hand-only (guitar removed) gate render.

## Metrics (solver, on its own skeleton)

| Fixture | Status | Contact (region) | Naturalness | Abduction° (i,m,r,p) |
|---|---|---|---|---|
| single | OK | 0.8 mm | 2.3 | 8,3,5,-1 |
| two | OK | 0.04 | 2.8 | -8,5,0,4 |
| D | OK | 0.06 | 3.6 | -22,0,6,4 |
| G | OK | 0.02 | 4.2 | -20,8,-5,-1 |
| C | OK | 0.0 | 3.8 | -8,-9,-11,2 |
| three | OK | 0.07 | 5.6 | -4,-2,-12,0 |
| wide | OK | 0.02 | 3.0 | 8,1,-2,-19 |
| impossible | **INFEASIBLE** | 60.4 | — | — |

Phrase (prev-chained) moves: [0, 27, 550, 2, 4, 3, 73, 16] — mostly small (anchored),
with jumps at position shifts (notes 2, 6) as expected. Solve ~8–16 s (offline).

## Honest assessment (NOT a self-pass)

**Improved:** on the solver's own model the naturalness rose and abduction dropped
(unused fingers more compact); the **single-note hand-only render now reads as a
compact hand with one finger extended** (vs the prior open splay) — the compact-unused
behavior emerges from the physiological prior, not a special case. Impossible fingering
still correctly INFEASIBLE. Region contacts + fretting-neutral are the right primitives.

**Blocking failure (the real finding):** the unification bridge is **not faithful**.
The biomechanical solver and the MPFB rig are DIFFERENT skeletons (different segment
frames, MCP offsets, rest orientation). Transferring the solver's joint angles onto a
pipeline-placed MPFB hand makes the fingers assume the right CURL but land at the wrong
place — so the rendered chords (D/G/C) **bunch below the board and no longer hit their
frets** (see `pose_sheet.png`, middle column). Naturalness went up; contact fidelity in
the render went DOWN. That is not a pass.

Root cause: joint angles solved for the solver's wrist/MCP geometry do not reproduce the
same fingertip world-positions on the MPFB skeleton. Faithful unification requires the
solver to optimize **on the MPFB skeleton itself** (its FK, its bone frames, its MCP
positions) so that angles AND placement are consistent with the actual fret targets —
rather than a stick-figure solver whose angles are copied over.

## Remaining weaknesses (explicit)

1. **Scipy↔MPFB skeleton mismatch** — the core blocker; chords don't hit frets in the
   render. Must port the solve onto the MPFB rig (or fit the solver skeleton to MPFB
   bone geometry) before the poses are trustworthy on the mesh.
2. **Chord compactness overshoots** — with contacts not enforced on the MPFB geometry,
   the fretting-neutral pulls chords into a fist; correct only once (1) makes contacts
   authoritative on the MPFB skeleton.
3. **Thumb** is a fixed curl in the bridge, not the solver's opposition — placeholder.
4. **Wrist repositioning** for wide stretches lives in the solver but the bridge uses
   pipeline placement, so it isn't expressed on the mesh yet.
5. **Solve time** ~8–16 s/pose — needs analytic Jacobians before phrase trajectories.
6. **Hand-only chords** (D/G/C) do not yet clearly read as fretting shapes — consequence
   of (1)/(2).

## Recommendation for the next increment

Port the biomechanical solve onto the MPFB skeleton so it is the single authoritative
model: run FK on the MPFB bones, compute contact-region / synergy / strain costs there,
optimize the MPFB pose directly, render. This removes the transfer entirely and is the
only way the naturalness gains and the contact fidelity hold at the same time. Keep
Camera A, render language, region contacts, and the fretting-ready synergy — they are
validated; the skeleton unification is the missing piece.

**Gate answer: does the NEW hand look like an experienced guitarist fretting?**
Single-note: materially closer. Chords: NO — they bunch because the transfer is
unfaithful. This increment advanced the pose model and found the true blocker, but does
NOT pass the visual gate. Deferring your approval to after the skeleton is unified.
