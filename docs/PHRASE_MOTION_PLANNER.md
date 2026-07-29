# Phrase-Level Biomechanical Motion Planner — Design Proposal

Design only. No code, no implementation, no solver changes. The static MPFB solver is
now a validated platform (one authoritative skeleton, contact-driven, contact-faithful,
rejects impossible, emergent compact behaviour, no chord hacks). The next leap is
MOTION: optimize a whole phrase as one continuous, physically-believable movement, not N
independent poses stitched together. This is movement PLANNING, not interpolation.

---

## 0. The shift in problem statement

Current: `for note in phrase: pose = solve(contacts(note))` — every pose is memoryless
except a weak "distance from previous" term. A guitarist does the opposite: they read
ahead, keep fingers down, prepare the next finger early, shift the whole hand rarely and
deliberately, and let the wrist lead. The realism gap is now temporal, not postural.

Desired: `trajectory = solve(all contacts over [t0, tN], with anticipation)` — one
optimization over the phrase (or a look-ahead window), minimizing effort/velocity while
honouring each note's contact at its time and rewarding the behaviours below.

## 1. Literature review

**Trajectory optimization (the engine).** Direct collocation is the standard method:
discretize time into knots, make the pose at each knot a decision variable, enforce
dynamics/contacts as constraints, minimize an effort/smoothness objective; solve one
large sparse NLP. Mature and well-documented (MIT *Underactuated Robotics* Ch.10;
"Direct Collocation Methods for Trajectory Optimization in Constrained Robotic Systems",
arXiv 2304.12908). Recent humanoid work (RAPTOR, arXiv 2409.00303) shows smooth,
physically-feasible whole-body trajectories with robust convergence and explicit
kinematic constraints — directly analogous to a hand over a fretboard.
**Guitar-specific prior art.** Handrix (ElKoura & Singh, SCA 2003) already generated
fretting-hand motion for a passage with coupled/sympathetic finger motion — the canonical
precedent that phrase-level planning is tractable and worthwhile.
**Guitar pedagogy = the objective terms, in words.** Economy of motion (keep moves
small; efficiency, not force, makes speed — *Pumping Nylon*); guide fingers (keep a
finger on the string through a shift to reduce travel and preserve orientation); "sticky
fingers"/anchoring (fingers stay near the board, down until needed); plan-one-chord-ahead
(slow changes are a preparation problem — decide the next shape before the beat); the "C"
hand shape with joints mid-range so any finger can flex/extend at will. Each is a
formal cost or constraint below.

Sources: [MIT Underactuated Ch.10](https://underactuated.mit.edu/trajopt.html) ·
[Direct collocation in constrained systems](https://arxiv.org/abs/2304.12908) ·
[RAPTOR humanoid trajopt](https://arxiv.org/abs/2409.00303) ·
[Humanoid planning/learning survey](https://arxiv.org/pdf/2501.02116) ·
[Handrix](https://www.dgp.toronto.edu/~gelkoura/noback/scapaper03.pdf) ·
[Economy of motion / guide fingers](https://www.siccasguitars.com/blogs/stories/classical-guitar-left-hand-technique) ·
[Plan-ahead / sticky fingers](https://www.classicalguitarcorner.com/left-hand-accuracy-and-positioning/) ·
[5 core principles](https://fretdojo.com/left-hand-guitar-technique-5-principles/).

## 2. Recommended architecture — planner ABOVE the solver

```
Song → Transcription → Fingering planner → PHRASE MOTION PLANNER → Biomechanical solver → Animation
                        (WHERE: string/       (WHEN/HOW the hand      (per-knot pose        (render the
                         fret/finger, DP/A*)   moves through time)     feasibility + FK)     solved knots)
```

Do NOT embed temporal reasoning inside the contact solver. Keep the static MPFB solver as
the per-knot pose authority (FK, contact regions, anatomy, collision — validated). The
motion planner is a NEW layer that owns time: it decides, over a look-ahead horizon,
which fingers stay down, when the hand shifts, when a finger prepares, and produces a
smooth joint TRAJECTORY whose every sampled knot is a valid static-solver pose. Clean
separation, each layer independently testable, and the planner can be swapped/upgraded
without touching the biomechanics.

## 3. Planner vs solver responsibilities

| | Static solver (exists) | Phrase motion planner (new) |
|---|---|---|
| Owns | one pose | the trajectory q(t) |
| Input | contacts at one instant | contact schedule over [t0,tN] + finger assignments |
| Enforces | contact region, joint ROM, collision, anatomy/synergy, thumb-behind | + temporal smoothness, velocity/accel bounds, guide-finger continuity, anchor holds, preparation lead |
| Objective | strain + manifold + arch/conform | Σ per-knot strain + **motion effort** (∫‖q̇‖²+‖q̈‖²) + shift penalty + reuse reward |
| Guarantee | pose feasible / INFEASIBLE | continuous, low-effort, anticipatory motion; every knot still feasible |

The planner CALLS the solver's FK/feasibility as its per-knot constraint set — it does not
reimplement biomechanics.

## 4. Formulation

Decision variables: MPFB joint state q at K collocation knots spanning the phrase (or a
sliding horizon of the next H notes). Between knots, a spline (cubic Hermite) so
velocity/accel are analytic. Then:

- **Hard constraints per note:** at the note's time knot, the assigned finger's tip lies
  in its contact region; joint ROM; no board/finger penetration (the static solver's
  constraints, re-used).
- **Continuity constraints:** collocation defects zero (spline consistent with velocity);
  bounded joint velocity and acceleration (relaxed wrist).
- **Soft objective** Σ over knots:
  - motion effort `w_v·∫‖q̇‖² + w_a·∫‖q̈‖²` — economy of motion, minimum unnecessary movement;
  - **finger-reuse / anchor**: reward a finger STAYING at its contact across consecutive
    notes it serves (keep fingers down); penalize lifting a finger that's needed again soon;
  - **guide-finger**: when a shift moves the hand, reward keeping one finger in light
    contact/orientation through the shift;
  - **preparation lead**: reward a finger being ALREADY near its next contact before the
    note time (prepare before needed) — a look-ahead term biasing q(t) toward q(t+Δ);
  - **whole-hand-first / wrist-leading**: reward reaching a new region by moving the
    root/wrist BEFORE stretching fingers (root moves early, fingers follow);
  - **shift economy**: penalize position shifts (root translation events) and reward
    reusing a hand position for a run of nearby notes;
  - **relaxed neutral between events**: unused fingers ride the fretting-ready curl (from
    the static model) — alive, near useful future positions, not frozen.
  Slides/hammer-ons/pull-offs later map to specific constraint patterns (a MOVING contact
  over an interval, or a same-finger fret change) — the contact primitives already have
  timing hooks; not designed in detail here.

All GENERAL — driven by the note/contact schedule, never by chord names.

## 5. Anticipation & look-ahead

The planner sees `pose(t−1), contacts(t), future contacts(t+1..t+H)`. Anticipation is
the preparation-lead + whole-hand-first terms plus the fact that a shift's cost is shared
across the whole window, so the optimizer naturally pre-positions for what's coming
rather than reacting. Horizon H is a tuning knob (phrase-length for offline; a rolling
window for future real-time).

## 6. Computational complexity

State ≈ 30 DoF × K knots. For a phrase of ~16 notes at, say, 4 knots/note → ~64 knots →
~1900 variables. Sparse NLP (block-banded in time) — well within IPOPT/SNOPT-class
solvers; direct collocation is designed for exactly this sparsity. **Offline: entirely
practical** (seconds to low-minutes per phrase). Cost drivers: FK per knot (already
fast/numpy) and the NLP Jacobian — which motivates the analytic-Jacobian work already
flagged. Solving N notes SIMULTANEOUSLY is the right call offline; a receding-horizon
window is the path to real-time later, but not needed for v1.

## 7. Commercially-usable tooling (no MANO, no non-commercial)

- **CasADi** (LGPL) — automatic differentiation + NLP interface; the natural choice for
  building the sparse trajectory NLP and getting exact Jacobians cheaply.
- **IPOPT** (EPL, commercial-OK) — the interior-point NLP solver CasADi drives.
- **scipy** (BSD) — adequate for an MVP direct-collocation prototype before CasADi.
- **Drake** (BSD) / **Pinocchio** (BSD) — reference implementations of direct collocation
  and rigid-body kinematics if we want batteries-included; optional.
- Everything reuses our own MPFB FK + physiological constants (clean-room). No MANO/
  MS-MANO data anywhere.

## 8. Pre-registered validation plan

Fixtures (contacts only, no chord names): the existing 8-note phrase; a 1-position run
(all notes one hand position — should show near-zero shifts + finger reuse); a
2-position run requiring exactly one deliberate shift (should show a guide finger + a
single whole-hand move, not per-note flailing); a wide interval alternation (should
anchor and reach, not re-grip each note); a repeated-note passage (finger stays down).
Impossible schedule (a shift faster than physically achievable) → the planner must return
INFEASIBLE for that segment, not teleport.

Pre-registered pass criteria (set BEFORE running): (a) every note's contact feasible at
its knot (≤ contact tol, render==solver as today); (b) total root-shift count ≤ the
minimal shifts a human would use on each fixture (hand-labelled ground truth); (c)
finger-reuse rate ≥ threshold on the repeated-note/1-position fixtures; (d) peak joint
velocity/accel below bounds (no snaps); (e) preparation: the finger for note n is within
X mm of its target by time(n)−Δ on ≥ Y% of notes; (f) impossible schedule → INFEASIBLE.

## 9. Objective metrics (report separately, never one score)

- CONTACT FIDELITY per knot (mm, render-space).
- MOTION ECONOMY: ∫‖q̇‖², ∫‖q̈‖², total finger travel, total root travel.
- SHIFT COUNT vs human-minimal.
- FINGER REUSE / ANCHOR rate.
- PREPARATION LEAD (avg ms/mm a finger is ready before its note).
- SMOOTHNESS: peak velocity/accel, jerk.
- FEASIBILITY: per-knot valid; impossible correctly rejected.
- SOLVE TIME / convergence.
- (Visual, human-judged): does the phrase read as one guitarist playing, not a puppet?

## 10. Implementation roadmap

1. **Kinematic trajopt MVP (scipy):** 2-note→8-note, effort + smoothness + per-knot
   feasibility only. Prove continuity + the render==solver invariant holds across a
   trajectory. Metrics: economy, smoothness.
2. **Behavioural terms:** add finger-reuse/anchor, shift economy, guide finger,
   preparation lead, whole-hand-first. Validate on the position-shift + repeated-note
   fixtures against pre-registered thresholds.
3. **CasADi + IPOPT + analytic Jacobians:** scale to full phrases, cut solve time.
4. **Articulations:** map slides/hammer-ons/pull-offs to contact-timing patterns
   (moving/interval contacts) — reuse the primitives' timing hooks.
5. **Receding-horizon** variant (foundation for real-time), still offline-baked for v1.
Each step: static solver UNCHANGED; planner sits above it.

## 11. Expected product impact

- Turns the platform from "static chord/pose renderer" into a **general virtual
  guitarist**: feed transcribed MIDI → get believable fretting-hand motion for arbitrary
  songs, the stated destination.
- The realism the last passes chased posturally is largely delivered by MOTION —
  anticipation, guide fingers, staying-down, wrist-leading read as "a real player" far
  more than another 2% of static pose quality.
- Reusable offline: bake trajectories per song → the app plays cheap precomputed motion
  (no on-device solving), matching the runtime budget already established.
- Foundation for teaching value: the planner's shift/guide/preparation decisions are
  exactly what a learner needs shown.

## 12. Recommendation

Proceed to roadmap step 1 (kinematic trajopt MVP over the existing 8-note phrase) on your
approval. Keep the static MPFB solver frozen as the per-knot authority. Do not resume
static-anatomy or camera work unless the motion results expose a genuine need. This is
the leap; the remaining realism gap is in time, not shape.
