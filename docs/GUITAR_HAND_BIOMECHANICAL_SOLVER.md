# Guitar Fretting-Hand — Biomechanical Motion Solver (Design & Research)

Status: **research + design only.** No solver implemented. The existing open-chord
MPFB sprite work (commit `bfbfb9b`) is preserved and re-purposed as validation
fixtures. This document answers: *can JAMN specify string/fret contacts and have
anatomical constraints drive the rest of the hand into a plausible posture, for
ARBITRARY musical input — single notes, riffs, arpeggios, chords, barres, slides,
transitions?*

Short answer: **yes, as a hybrid — an offline trajectory-optimizing whole-hand solver
that bakes poses/sprites, plus a small runtime interpolator.** Details below.

---

## 0. Why the current architecture is not the endpoint

`chord name → authored pose / independent per-finger IK` fails two ways:
- **Independent IK crosses fingers.** Verified on barres: hitting the exact fret/string
  targets with per-finger IK produces splayed, crossing fingers because each finger
  solves alone, with no shared cost, no collision term, no coupling. (Attempt in
  `blender_mpfb_rig.py`; IK rotation-limit patch reduced crossing but broke contact
  accuracy — the two objectives fight when solved locally.)
- **It's keyed on chord NAME.** A melody note, a slide, an arpeggio, a power chord have
  no chord name. The library can't represent them.

The correct abstraction solves the WHOLE hand jointly for a set of timed contacts,
scored by anatomical validity — not fingertip error alone.

## 1. Candidate technologies

| # | Tech | What it gives us | Reuse mode | License |
|---|---|---|---|---|
| 1 | **MANO** (MPI) | 15 joints/45-DoF articulated statistical hand; PCA **pose synergies** (first ~15 PCs capture natural coupled motion); pose-dependent corrective blendshapes | pose PRIOR / synergy basis / mesh | **non-commercial only — BLOCKED as a shipped dependency** |
| 2 | **MS-MANO** (CVPR 2024) | musculoskeletal layer over MANO — ~30 muscle/tendon actuators impose physiologically realistic torque trajectories; kills the "unnatural joint-actuated" motion; BioPR sim-in-the-loop refiner | concepts + constraint formulation | research code; inherits MANO's license — **BLOCKED to ship, usable as reference** |
| 3 | **OpenSim hand/wrist model** (SimTK, v4.3) | 22 bodies, 23 DoF; **passive joint properties (ROM limits) per flex/extend DoF**, ab/adduction, thumb opposition, tendon coupling — measured human data | extract joint-limit + coupling TABLES (clean-room our own constraints) | **open, SimTK/Apache-style — usable; the data we want is citeable physiology, not code we must ship** |
| 4 | **OpenHands** (statistical finger-bone shape model) | anatomically valid bone proportions/variation | proportion validation | open |
| 5 | **Handrix** (ElKoura & Singh, SCA 2003) | the canonical prior art: procedural fretting-hand from a musical passage; **sympathetic (coupled) finger motion via k-NN over real hand configs**; multi-concurrent reaching-task skeletal control | architecture blueprint | paper (concepts, clean-room) |
| 6 | **Physics-RL guitarists** (SIGGRAPH Asia 2024 "Synchronize Dual Hands…"; CGF 2024 "Learning to Play Guitar with Robotic Hands") | imitation+RL two-hand playing from tablature, no mocap | too heavy for our need; informs the contact model | papers |
| 7 | **Robotics whole-hand IK** (e.g. Pinocchio/Drake-style constrained optimization; PCHands PCA synergy for N-DoF manipulators) | mature nonlinear constrained solvers; synergy dimensionality reduction | the solver ENGINE | open (BSD/MPL) |
| 8 | **Guitar fingering DP/A\*** (Sayegh optimal-path; Radicioni biomech; `guitar_dp`; A\*-Guitar polyphonic) | the FINGERING PLANNER (problem A), separate from posing | clean-room algorithm | papers + MIT-ish repos |

Sources: [MANO license](https://mano.is.tue.mpg.de/license.html) ·
[MANO PyTorch/DoF](https://github.com/otaheri/MANO) ·
[MS-MANO (CVPR'24)](https://arxiv.org/abs/2404.10227) ·
[OpenSim hand/wrist](https://www.biorxiv.org/content/10.1101/2021.12.28.474357.full.pdf) ·
[OpenHands](https://www.ncbi.nlm.nih.gov/pmc/articles/PMC11511744/) ·
[Handrix](https://www.dgp.toronto.edu/~gelkoura/noback/scapaper03.pdf) ·
[Synchronize Dual Hands (SA'24)](https://dl.acm.org/doi/full/10.1145/3680528.3687692) ·
[Real-time Guitar Hand Synthesis (MIG'25)](https://dl.acm.org/doi/10.1145/3769047.3769060) ·
[Optimal fingering DP](http://www.diva-portal.org/smash/get/diva2:668903/FULLTEXT01.pdf) ·
[guitar_dp](https://github.com/jgollub1/guitar_dp) ·
[PCHands synergies](https://arxiv.org/pdf/2508.07945).

## 2. Licensing verdict (decisive)

- **Ship-blocked:** MANO, MS-MANO, and any MANO-derived weights/blendshapes
  (non-commercial). We may READ the papers and reproduce *ideas* (synergy PCA,
  musculoskeletal torque limits) clean-room, but no MANO data/parameters in the app.
- **Ship-safe:** OpenSim/OpenHands joint-limit & coupling **numbers** (published
  physiology — facts, not copyrighted expression), Handrix concepts, robotics solver
  libraries (BSD/MPL) if any run offline, our own MPFB mesh (CC0), our own solver code.
- **Practical consequence:** the mesh stays **MPFB (CC0)**. MANO is a *reference* for
  the pose-space math, never a runtime asset. Our anatomical constraint tables are
  clean-room from OpenSim-published ranges.

## 3. Recommended architecture

```
MIDI / detected notes / chord events
        │
        ▼
(A) FINGERING PLANNER  — problem A, symbolic, cheap
    pitch → candidate (string,fret) → finger assignment → phrase-optimal path
    (DP/A* over a biomechanical transition cost; Sayegh/Radicioni-style)
        │  timed contact constraints:
        │  { string, fret, finger, tContact, tRelease, articulation }
        ▼
(B) WHOLE-HAND BIOMECHANICAL SOLVER — problem B, geometric+optimization, OFFLINE
    state = wrist(6) + palm/forearm + 4 fingers×(MCP flex, MCP abduct, PIP, DIP)
            + thumb(behind neck)   ≈ 26–30 DoF, reduced via synergy basis
    per keyframe: constrained nonlinear solve (contacts as constraints,
                  anatomy/collision/strain as costs)
    across time:  trajectory optimization (previous pose + look-ahead)
        │  continuous joint trajectory
        ▼
MPFB mesh (CC0)  — skinned by the solved skeleton
        │
        ▼
Offline bake → per-pose sprite / contour  (today's renderer)
        │
        ▼
JAMN silhouette renderer  (runtime; unchanged UI)
```

Two solvers, never conflated: **(A) WHERE to play, (B) HOW the hand reaches it.**

## 4. Fingering-planner boundary (problem A)

Input: note/chord events (pitch, time). Output: for each note, a `(string, fret,
finger)` plus contact/release times and articulation (fret / barre / mute / slide /
hammer / bend). Method: graph over candidate positions per note, **dynamic
programming / A\*** minimizing a transition cost (hand-span, position shift, finger
reuse, string skips, open-string bonuses) — established, monophonic-proven, polyphonic
via A\*. This is symbolic and cheap; it does NOT know hand geometry. It emits
constraints for (B). Crucially it may look ahead — a guitarist picks a fingering for
what comes next — so the planner returns a *phrase-optimal* path, and its output feeds
the solver's look-ahead term.

## 5. Hand-solver state representation (problem B)

Full DoF (~26): wrist translation(3) + rotation(3); per finger MCP flex, MCP
abduction, PIP flex, DIP flex (4×4=16); thumb CMC(2)+MCP+IP (4). Reduce the search
with a **synergy basis** (PCA over natural hand poses — the MANO idea, reproduced from
our own MPFB pose corpus + the validated chord fixtures) so the optimizer works in
~8–12 synergy dims + wrist, then projects back to full DoF. DIP is coupled to PIP
(~2/3) as a hard constraint, not a free variable. Represent joints as Euler within
anatomical ranges (not axis-angle) so limits are directly enforceable.

## 6. Anatomical constraints (from OpenSim-published ranges, clean-room)

- Per-joint ROM: MCP flex −20°…90°, MCP abduction ±15° (index/pinky wider), PIP
  0°…110°, DIP 0°…80°; thumb CMC opposition arc; wrist flex/extend + radial/ulnar.
- **Coupling / synergies:** DIP≈⅔·PIP; adjacent-finger abduction coupling (ring can't
  splay far without pinky); flexion synergies from the PCA basis. These are what stop
  crossing WITHOUT sacrificing contact accuracy — the whole hand moves as a coupled
  unit, exactly what independent IK lacked.
- **Strain penalty:** deviation from a neutral relaxed pose, weighted per joint.

## 7. Guitar-neck contact model

Reuse the existing physical geometry (`GuitarPhysical`, 648 mm scale,
equal-temperament wires, string taper, neck thickness). A contact = fingertip pad
tangent to string S at the fret-slot point behind wire F, with the pad normal into the
board (+depth), tip pressing to string height. Barre = index as a *line* contact
across a string range at one fret (not a point) — expressed as several colinear
contact constraints on the extended index. Non-contact fingers: hover clearance
constraint over the strings. Thumb: opposition constraint behind the neck (depth =
−neckThickness), never a visible target. Collision: fingers vs neck plane, fingers vs
fingers (capsule–capsule), palm vs neck edge.

## 8. Objective / cost function

Minimize, subject to hard contact + joint-limit + non-penetration constraints:

```
J = w_contact·Σ‖tip − target‖²          (soft residual after hard constraint)
  + w_strain ·Σ jointStrain²
  + w_move   ·‖pose − pose(t−1)‖²        (minimum change from previous frame)
  + w_prep   ·‖pose − poseHint(t+1)‖²    (bias toward the upcoming contact set)
  + w_collide·penetrationDepth²          (finger/finger, finger/neck)
  + w_synergy·offSynergyManifold²        (stay near natural coupled poses)
```

A geometrically valid fingertip solution that needs crossed fingers, out-of-range
rotation, implausible splay, or a front-of-neck thumb scores badly via the collision +
limit + synergy terms — i.e. it is rejected, satisfying the core requirement.

## 9. Trajectory-planning approach

Not frame-by-frame IK. **Direct collocation / trajectory optimization** over a short
horizon (the current note plus N look-ahead events): discretize time into knots,
variables = pose at each knot, contacts active on their `[tContact, tRelease]`
intervals, minimize Σ J over the horizon + smoothness (bounded joint velocity /
"anticipation"). Warm-start each solve from the previous knot; seed the whole
trajectory from the fingering-planner's positions. This gives continuity (fingers stay
planted when a note holds), anticipation (the hand pre-shapes for the next chord), and
minimum-strain motion — the guitarist behaviors the brief asks for. Off-the-shelf
NLP (IPOPT/SNOPT-style, or a lightweight Gauss-Newton on the synergy-reduced problem)
solves it offline in ms–seconds per phrase.

## 10. How the current MPFB/Blender work is reused

- **Mesh:** unchanged — MPFB CC0 hand, our scripted skeleton + skinning.
- **Neck geometry / contact math:** unchanged (`GuitarPhysical`).
- **Renderer:** unchanged — the solved trajectory drives the same sprite/contour bake
  and the same JAMN silhouette compositor.
- **Validated chord poses become FIXTURES:** the approved G/D/Em/C/A/Am/E/Dm poses are
  the solver's regression targets — feeding their contacts must reconverge to visually
  equivalent poses (§12). They also seed the synergy-basis PCA.
- The barre exploration stays as a documented negative result proving why independent
  IK is insufficient.

## 11. Prototype plan (offline, no UI changes)

1. **Constraint tables** — encode ROM + DIP/PIP coupling + synergy PCA (from MPFB pose
   corpus + fixtures) as data. Deliverable: `hand_constraints.json`.
2. **Static whole-hand solver** — single-frame constrained solve in Python (SciPy/NLP)
   over the synergy-reduced state; contacts hard, anatomy/collision/strain soft.
   Drive the MPFB skeleton, render the existing debug view.
3. **Fixture regression** — feed the 8 fixture chords' contacts; assert reconvergence
   to the approved poses (per-metric, §12).
4. **Progressive tests** (§ user list): 1 note → 2 fingers → open chord → F/F# barre →
   G→C → C→F → arbitrary 8-note phrase. Diagnostic render each (fretboard, skeleton,
   joint axes, targets, tip error, collision state, limit violations, wrist/palm,
   thumb, mesh).
5. **Trajectory pass** — add time: collocation across a phrase; verify continuity +
   anticipation.
6. Only after sign-off: bake sprites/contours per solved keyframe and swap the library
   backend behind the existing (unchanged) renderer.

## 12. Validation fixtures & metrics (report SEPARATELY, never just target error)

Fixtures: approved G, D, Em, C, A, Am, E, Dm poses (+ future barres). Report per test:
- **Contact accuracy** — tip-to-target mm per finger.
- **Anatomical validity** — count/severity of joint-limit violations.
- **Collision validity** — finger/finger + finger/neck penetration depth.
- **Pose naturalness** — distance off the synergy manifold; strain score;
  side-by-side vs fixture.
- **Temporal continuity** — joint-velocity smoothness; anchor-finger drift across a
  held note.

## 13. Expected difficulty / performance

- Static synergy-reduced solve: tractable, ms–seconds/frame offline (SciPy NLP).
- Trajectory optimization over a phrase: seconds offline; the hard part is tuning the
  cost weights and the synergy basis, not the solver.
- Biggest risks: (a) building a good synergy basis without MANO's corpus — mitigated by
  MPFB pose sampling + the fixtures; (b) collision term conditioning; (c) barre line
  contacts. All are engineering, none blocked by license.

## 14. iOS / runtime implications

The full NLP trajectory solver does **not** run on device. Runtime stays cheap:
precomputed pose/trajectory data + a small Swift interpolator + the existing 2D
silhouette compositor. No 3D engine, no ML inference, no new heavy dependency on
device — same runtime profile as today.

## 15. What stays offline / precomputed

Fingering planning for known songs, whole-hand solving, trajectory optimization, mesh
skinning, sprite/contour baking, synergy-basis PCA. All authoring-time in Blender/Python.

## 16. What can run in real time

Interpolating between precomputed solved poses along a known timeline; re-anchoring a
solved pose into the UI fret window; the silhouette draw. For *live* arbitrary MIDI
(no precompute), a heavily reduced synergy-space Gauss-Newton single-frame solve is
plausible in Swift later — but that's a v2; v1 precomputes.

## 17. Recommendation — pose library vs solver vs hybrid

**Hybrid, staged.**
- **Keep** the MPFB open-chord sprites shipping now (they're correct and cover most
  beginner content). Do NOT expand the hand-authored library further (barres included)
  — that path doesn't generalize and the effort is better spent on the solver.
- **Build** the offline whole-hand biomechanical solver (problem B) behind the existing
  renderer, with the fingering planner (problem A) as a separate module. This is the
  only architecture that answers the brief's core question — "JAMN names the contacts,
  anatomy assumes the posture" — for arbitrary input.
- **Reuse** everything already built (mesh, neck geometry, renderer, fixtures).
- **Ship path:** solver generates the same sprite/contour data the app already
  consumes, so integration is a data swap, not a UI change. Live on-device solving is a
  later, optional v2.

Net: the pose library was the right MVP; the biomechanical trajectory solver is the
right endpoint; the hybrid lets us ship continuously while building toward the general
virtual guitarist. Recommend proceeding to the **Prototype plan §11 step 1–3** on
approval — static solver + fixture regression — before committing to trajectory work.
