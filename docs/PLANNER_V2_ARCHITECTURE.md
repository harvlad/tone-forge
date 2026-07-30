# Planner V2 — clean-sheet architecture design (review draft)

Architecture only. No code, no implementation effort, no migration, no backward
compatibility. Assumes nothing about the current planner except the empirical findings from
M0–M8, the trajopt work, the optimizer audit, the comfort-model study, and the task-relevance
literature review. The question this document answers: **is a second-generation planner
architecture scientifically justified before any engineering begins?**

## 0. Empirical premises (established, not assumed)
- P1. The benchmark is trustworthy and deterministic (M2/M5): hard-gate × weighted-soft
  Movement Score, byte-reproducible, regression diff. It can measure any planner.
- P2. Memoryless naive-with-warm-start is a strong baseline; whole-phrase optimization beats
  it only narrowly (one_shift_scale shift 2→1), and regresses on already-settled phrases (M7).
- P3. The optimizer is approximately faithful; the residual drift is dominated by the
  objective, not the solver (falsification: non-convergence real but secondary; the drifted
  point is genuinely lower-cost).
- P4. The comfort model is angle-only, additive, equal-weight across fingers, with NO task
  relevance and NO anchor/contact-role concept. It predicts exactly the observed behaviour:
  the hand chases the most-strained (moving) finger; a comfortable anchor has zero authority.
- P5. V1 lumps CONTACT (feasibility) and COMFORT into one soft weighted sum, so comfort can
  trade against contact PLACEMENT. Every surveyed motor-control/robotics framework instead
  separates task from comfort structurally (task first; comfort in the task null space).

## 1. Define the problem (what the planner actually solves)
- **Inputs:** a phrase = an ordered set of MOMENTS; each moment is a set of required CONTACTS
  `(string, fret, finger, role)` plus timing `(t, dur)`; a fixed fingering (V1 invariant);
  instrument geometry; optional style/reference acceptance region.
- **Outputs:** a Trajectory = time-ordered hand STATES (root transform + finger/thumb/palm
  configuration), dense enough to resample, feasible at every moment.
- **Decision variables:** the hand state per knot (and, in a whole-phrase formulation, the
  coupling between knots).
- **Constraints (the TASK):** every required contact of a moment is satisfied within tolerance
  for the duration it is active; no board penetration; no self-collision; joint limits. These
  are what MUST hold — they are not preferences.
- **Objectives (COMFORT / STYLE):** among all task-satisfying states, prefer low biomechanical
  effort (strain, synergy-consistency, coupling), smooth motion between knots, and minimal
  UNNECESSARY hand relocation.
- **What the task is:** keeping the required fingertips on their frets for their durations.
- **What comfort is:** a preference over the redundant freedom that remains AFTER the task is
  met. It ranks task-satisfying states; it must never be able to relocate the hand away from a
  position that already satisfies the task unless doing so is required to satisfy a FUTURE task.
- **What should remain invariant:** an established, task-satisfying contact (an anchor held
  across moments) — its fingertip–fret placement should be stable until an upcoming contact
  makes movement necessary.

## 2. Architectural principles (each traceable to a premise)
1. **Separate task from comfort structurally** — task is achieved first; comfort acts only in
   the leftover freedom. (P4, P5, literature.)
2. **Contacts that must hold are CONSTRAINTS, not soft penalties.** (P5.)
3. **Comfort must not be able to redefine or relocate the task.** (P4/P5 — the exact failure.)
4. **Behaviour must EMERGE from the objective/constraint structure**, not from per-phrase or
   per-finger special-casing. (Increment discipline throughout M0–M8.)
5. **The optimizer should faithfully solve the stated model**; if behaviour is wrong, fix the
   MODEL, not the solver. (P3.)
6. **Deterministic, reproducible optimization** with reported conditioning/convergence. (P3,
   falsification found ill-conditioning + non-convergence — a V2 must surface these.)
7. **The benchmark stays frozen** and is the sole arbiter; V2 is measured against the pinned
   naive/trajopt baselines. (P1.)
8. **Task relevance is explicit** — the model must represent that some contacts matter more
   (anchors, held notes) than transient posture. (P4, literature.)

## 3. Proposed V2 architecture (high level)
A layered planner with a strict task/comfort separation and an explicit contact model.

- **Contact layer.** First-class contacts with a ROLE and a LIFETIME: `required` (a note that
  must sound now), `anchor/held` (persists across several moments), `guide` (transient
  assist), `free` (unused finger). A contact carries the geometry it demands and the interval
  over which it must hold. (Generalises the current PointContact by adding role + lifetime.)
- **Task layer.** Turns active contacts into task variables/constraints per moment: fingertip
  in its contact region, no penetration, no collision, joint limits. The anchor's contact is a
  task that spans multiple moments (a persistent task), not re-decided per moment.
- **Comfort layer.** Effort/synergy/coupling/palm posture — a preference over the redundancy
  that survives the task layer. Structurally SUBORDINATE to the task layer (see §4).
- **Motion layer.** Cross-moment smoothness and minimal-necessary relocation over the whole
  phrase; operates on the sequence of task-satisfying states.
- **Optimization layer.** Solves the layered problem with reported determinism, conditioning,
  and convergence diagnostics (the audit made these first-class outputs, not hidden).
- **State representation.** Same 30-DoF hand state (validated FK; render==solver) — this piece
  survived every investigation and is retained by evidence, not habit.

**Information flow:** phrase → contact layer (roles/lifetimes) → task layer (constraints per
moment, anchors as persistent tasks) → comfort layer (ranks the null space) → motion layer
(couples knots) → optimization layer (solves, reports diagnostics) → Trajectory → frozen
benchmark.

## 4. Representing task relevance — alternatives (no choice yet)
**Option A — Hard hierarchy (stack-of-tasks / lexicographic; Escande–Mansard–Wieber; Khatib
null-space).** Contacts are hard constraints at the top; comfort optimized strictly within
their null space; anchors are top-level persistent constraints.
- *Advantages:* strongest guarantee (comfort can never move the task); anchors provably stable;
  directly matches the dominant robotics pattern; interpretable priority order.
- *Disadvantages:* can be brittle/infeasible near reach limits; hard boundaries introduce
  non-smoothness (the audit already saw kink-sensitivity); needs a feasibility fallback.
- *Assumptions:* the task set is consistently satisfiable; a well-defined priority order exists.
- *Failure modes:* infeasibility when a required contact is barely reachable; chatter at
  constraint activation.

**Option B — Continuous (prescribed) relevance (weighted hierarchy / soft-with-large-margin).**
Each contact carries a relevance weight (anchor ≫ transient); comfort weight bounded well below
task weight, so comfort influences posture but cannot overpower a held contact.
- *Advantages:* smooth, always feasible; tunable; a small, well-understood change from V1 in
  form (but with an explicit relevance axis V1 lacks).
- *Disadvantages:* only APPROXIMATE priority — a heavy enough comfort term could still perturb a
  light task (the exact V1 disease, merely pushed further away); relevance weights are hand-set.
- *Assumptions:* a weight schedule generalises across phrases without per-phrase tuning.
- *Failure modes:* mis-scaled weights re-introduce drift; ill-conditioning from large weight
  ratios (the audit's 350:1 stiffness is exactly this).

**Option C — Emergent relevance (optimal feedback control / minimal-intervention; Todorov–
Jordan; UCM).** Relevance is not prescribed; it emerges from a task cost + effort under a model
of variability. The controller stabilises task-relevant directions and leaves the rest free.
- *Advantages:* most human-like and principled; anchor stability emerges rather than being
  labelled; continuous relevance for free; matches the strongest motor-control evidence.
- *Disadvantages:* highest research risk; needs a noise/variability model and a stochastic or
  feedback formulation we have never built; hard to validate without human data.
- *Assumptions:* a tractable cost reproduces guitarist behaviour; the static solver can be
  recast in an OFC-like frame.
- *Failure modes:* unfalsifiable tuning; behaviour sensitive to the assumed noise model.

**Option D — Hybrid (hard contacts + comfort in null space + a persistent-anchor task).** Hard
constraints only for the currently-sounding contacts and held anchors; everything else (comfort,
smoothness) strictly in the null space; relevance is binary at the constraint level and
continuous within the null-space objective.
- *Advantages:* captures A's guarantee for the few things that must hold, with B's smoothness
  for the rest; anchors are persistent tasks; smallest set of hard constraints → less brittle.
- *Disadvantages:* two mechanisms to reason about; still needs a feasibility fallback.
- *Assumptions:* the "must hold" set is small per moment (true for guitar: 1–4 contacts).
- *Failure modes:* boundary between hard and null-space objectives mis-drawn.

## 5. Success (architectural properties, not scores)
- **Stable anchors:** a held contact does not move while other voices change, WITHOUT an
  anchor-specific hack — it falls out of task/comfort separation.
- **Minimal-necessary motion:** the hand moves only when a future contact requires it.
- **Emergent behaviour:** no per-phrase, per-finger, or chord-specific rules.
- **Deterministic & diagnosable:** reproducible solutions with reported conditioning/convergence.
- **Explainable:** every motion attributable to a task or a null-space preference.
- **Literature-consistent:** matches the universal task-first / comfort-in-null-space pattern.
- **Benchmark-robust:** improvements register on the frozen suite without gaming it (hard gates
  intact, no metric-specific contortion).

## 6. Failure analysis (per option, critical)
- **A (hard hierarchy):** risk = infeasibility/chatter at reach limits and non-smooth activation
  (the audit already flagged kink-sensitivity); unknown = how often guitar phrases hit
  infeasibility; validation = feasibility sweep across the suite + a fallback policy.
- **B (continuous):** risk = it is V1's disease deferred, not cured — approximate priority can
  still let comfort move the task; large weight ratios reproduce the 350:1 ill-conditioning the
  audit found; unknown = whether one weight schedule generalises; validation = weight-sensitivity
  and conditioning study.
- **C (emergent/OFC):** risk = large research effort with unproven payoff; needs a variability
  model and human data we lack; unknown = tractability + validation; validation = human-motion
  comparison (currently absent).
- **D (hybrid):** risk = mis-drawn hard/soft boundary; unknown = right membership of the "must
  hold" set (does a guide finger belong?); validation = ablate contact-role membership on the
  suite.
- **Cross-cutting research gap (all options):** we have NO external human-motion validation set.
  Every "realistic" claim currently rests on the frozen references, which are single-labeller
  (M4 flagged this). This limits how strongly ANY architecture can be validated for realism.

## 7. Comparison to Planner V1 (differences, not scores)
| dimension | Planner V1 | Planner V2 (proposed family) |
|---|---|---|
| Problem formulation | single soft weighted sum (contact + comfort together) | task/constraint layer + subordinate comfort in the null space |
| Task representation | contact as a soft penalty (W=8) | contact as a constraint / high-priority task, with role + lifetime |
| Comfort representation | first-class additive terms co-weighted with contact | preference over the post-task redundancy only |
| Optimization assumptions | smooth unconstrained-ish bowl (violated: ill-conditioned, kinks, non-converged) | constrained/hierarchical solve with reported diagnostics |
| Task relevance | none (all fingers/contacts equal) | explicit (hard, continuous, or emergent per option) |
| Expected behaviour | hand chases most-strained finger; anchor silent | anchor held; motion only when a future contact requires it |
| Extensibility | new behaviour needs new soft terms (risk of Goodhart) | new behaviour = new task/role or null-space objective |
| Scientific consistency | soft-sum is the weakest prioritization form | matches the universal task-first pattern |
| Implementation complexity (high level) | low | higher (constrained/hierarchical solver, contact roles) |

## 8. Decision matrix (qualitative; not intuition-scored — read with §6/§7)
| architecture | scientific grounding | biomech plausibility | interpretability | expected robustness | extensibility | research risk | engineering risk | key unknowns |
|---|---|---|---|---|---|---|---|---|
| **V1 (retain, fix optimizer only)** | weak (soft-sum) | low (anchor silent) | medium | low (drift persists) | low (add soft terms) | low | low | none — but P3 says optimizer isn't the cause |
| **A hard hierarchy** | strong (robotics) | medium–high | high | medium (brittle at limits) | high | medium | medium–high | infeasibility frequency, activation non-smoothness |
| **B continuous relevance** | medium | medium | medium | medium (weight-dependent) | medium | low–medium | low–medium | weight generality, conditioning |
| **C emergent (OFC/UCM)** | strong (motor control) | high | medium (opaque) | unknown | high | high | high | tractability, human-data validation |
| **D hybrid (hard contacts + null-space comfort + persistent anchor)** | strong | high | high | medium–high | high | medium | medium | hard/soft boundary, role membership |

## 9. Recommendation
**B in the prompt's lettering = "Build Planner V2 around explicit task hierarchy" — specifically
the hybrid form (Option D): hard/high-priority constraints for currently-sounding contacts and
persistent anchors, with comfort and smoothness resolved strictly in their null space.**

Justification, from the investigations rather than novelty:
- **A (retain V1, fix only the optimizer) is ruled out by evidence.** The falsification showed
  the optimizer is approximately faithful and the drifted state is genuinely lower-cost (P3);
  the comfort study located the cause in the MODEL's lack of task/comfort separation (P4/P5).
  Fixing the optimizer cannot fix a model that prefers the drift.
- **The one structural defect every investigation converges on** is that comfort can relocate
  the task. Only a task/comfort SEPARATION removes it, and that separation is the universal
  pattern across operational-space control, stack-of-tasks, UCM, OFC, and grasp maintenance.
- **Within the hierarchy family, the hybrid (D) is preferred over pure hard (A) or continuous
  (B):** guitar has very few must-hold contacts per moment (1–4), so a small hard set + null-
  space comfort gets A's guarantee without A's brittleness, and avoids B's "V1-disease-deferred"
  approximate priority and its large-weight-ratio ill-conditioning (the exact 350:1 stiffness
  the audit measured). The anchor becomes a persistent contact task — an established analogue,
  not a bespoke hack.
- **C (emergent/OFC) is the most principled long-term target but is NOT yet justified to build:**
  it needs a variability model and human-motion validation we do not have (the cross-cutting
  research gap in §6). It should be kept as the second-generation refinement once a hard/null-
  space V2 exists and an external validation set is built.

**Caveat, stated plainly:** the CURRENT magnitude of the defect is small (one_shift win 2→1;
anchor drift sub-centimetre, partly numerical). The justification for V2 is STRUCTURAL, not the
size of today's symptom — the soft-sum formulation will bite harder as phrases add real anchors,
barres, chords, and slides, where comfort moving the task becomes a first-order error. If the
project's near-term scope stays at single notes and simple lines, V1 is adequate and V2 can wait;
if it moves toward the richer phrase behaviour the roadmap targets, V2 (hierarchy/hybrid) is
scientifically justified to design now.

**One evidence gap that should run in parallel regardless of choice:** an external human-motion
validation set. Without it, "realistic" remains judged against single-labeller references, which
bounds how strongly any V2 can be validated for believability.

STOP — design document only. No implementation, no migration, no planner/optimizer/objective
change. For architectural review.
