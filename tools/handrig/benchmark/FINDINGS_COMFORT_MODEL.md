# Is the comfort model faithful-but-incomplete, or is the optimizer wrong?

Study of the FROZEN comfort objective as a scientific model (analysis only; nothing changed).
Success criterion answered at the end.

## 1. Current comfort model (mathematically)

For a hand state `s = (root r∈R^3, root_rot∈R^3, finger angles θ∈R^16, thumb∈R^4, meta∈R^4)`
the frozen objective is `J = Σ_k W_k · c_k(s)`. The FEASIBILITY terms (contact, board,
collision — W 8/40/30) force the fingertips onto the frets. The COMFORT terms are:

```
strain    W=2.4   Σ_f Σ_j w_j (θ_fj − θ*_j)^2         θ*=[28,0,45,30]°, w=[1,2,0.9,0.6]  (MCPflex,abd,PIP,DIP)
splay     W=2.0   Σ_f max(0,|θ_f,abd|−4°)^2  + 0.5 Σ_i (θ_i,abd−θ_{i+1},abd)^2
coupling  W=1.4   Σ_f max(0,|θ_f,DIP − (2/3)θ_f,PIP| − 15°)^2
manifold  W=2.2   || (θ−μ) − PᵀP(θ−μ) ||^2         P,μ = clean-room synergy PCA basis
thumb     W=2.0   0.01·||thumb_tip − (middleMCP_x, NECK_THICK, 0)||^2  + 1.5·max(0,(NECK−tip_y)/10)^2
wrist     W=0.4   root_rot_z^2
arch      W=2.5   Σ_i (meta_i − cup_i)^2,  cup=[.05,.09,.20,.30]·max(.4, mean_MCPflex/.7)
individ   W=0.8   1.5·(θ_pinkyMCP − θ_ringMCP)^2
conform   W=1.5   0.02·max(0, −arc + 4mm)^2,  arc = mean(MCP_y ends) − mean(MCP_y middle-pair)
```
Units: angles in radians; positions in mm; penalties dimensionless after weighting.

**Plain language — what this objective believes makes a hand comfortable:** every finger
should sit close to ONE common fretting-ready ready-curl (MCP~28°, PIP~45°, DIP~30°, near-zero
splay); joints should co-flex (DIP≈⅔·PIP); the whole finger vector should lie on the learned
synergy manifold; the palm should arch (ulnar fingers cup more) and wrap the neck; ring↔pinky
should move together; the thumb should sit behind the neck opposing the middle finger.
**Comfort is the SUM of each finger's deviation from a single shared ready pose.**

## 2. Implicit assumptions (with evidence status)

| # | Assumption | Status |
|---|---|---|
| A | Comfort is a function of JOINT ANGLES only; the root has no intrinsic comfort (couples in only via the angles needed to hold contacts). **Verified**: translating the root at fixed angles leaves every comfort term byte-identical. | modeling choice; **unknown** vs reality (real forearm/wrist has preferred postures) |
| B | All four fingers share ONE common neutral target `θ*`. | approximation; real per-finger rest postures differ — **partly unsupported** |
| C | Comfort is ADDITIVE & largely SEPARABLE across fingers (sum of per-finger penalties; coupling/synergy add mild interaction). | synergies/enslaving ARE real (Santello; Schieber) and partly modeled — **reasonable but incomplete** |
| D | EQUAL WEIGHT per finger — no finger is more important. | **unsupported** by task-relevance / minimal-intervention evidence |
| E | NO concept of contact ROLE — anchored vs moving vs transient contacts are identical. **Verified absent.** | **unsupported** for a phrase named for its anchor |
| F | Per-pose / memoryless: the comfort of a knot ignores neighbours; "sustained/held" is not in comfort (the resist term is a separate planner regulariser, not comfort). | intentional (static solver) but **incomplete** for motion |
| G | Effort ≈ minimum deviation from a neutral posture (a static-posture proxy). | a recognized family (min-work / min-torque-change) but **not** a metabolic/EMG model |

## 3. Observed behaviour explained by the model

Contribution + authority analysis on `sustained_anchor` (index s5f5 held; upper voice
middle→ring→middle):
- Comfort is angle-only (assumption A verified): root position matters only because holding
  the frets at a different root requires different finger angles.
- **Finger authority is NOT equal in effect.** At moment 1, shifting the root to its comfort
  minimum (−3 mm) lowers cost 0.171→0.140, and the ENTIRE gain is the RING finger's strain
  dropping (0.0130→0.0015, Δ=−0.0114); the index anchor's strain is FLAT (Δ=−0.0). Authority
  over hand position ∝ each finger's strain GRADIENT w.r.t. the root. The stretched moving
  finger has a large gradient and dominates; the comfortably-placed anchor has ~zero gradient
  and therefore ~zero authority.
- **Model prediction (section 7):** if perfectly optimized, this comfort model
  **chases the most-strained contact** — normally the furthest-reaching moving voice — and
  gives a well-placed anchor NO say in hand position. It does not preserve anchors, does not
  privilege held notes, and re-centres per moment on the strain-weighted finger average. This
  is exactly the observed inter-moment root wobble (the hand nudges toward the ring in m1 and
  back in m0/m2). The optimizer is faithfully executing this; the drift is the model's
  intended behaviour under its assumptions.

## 4. Comparison with the biomechanics / motor-control literature

- **Postural synergies** (Santello, Flanders & Soechting 1998): a few correlated joint
  patterns explain most hand postures. We DO model this (clean-room synergy manifold) plus
  ring↔pinky coupling and DIP≈⅔·PIP. **Aligned in spirit.**
- **Finger independence / enslaving** (Schieber; Kilbreath; Lang): individuated movements
  carry obligatory coupling to neighbours from passive-mechanical + neural sources. Our
  `individ`/`splay`/`coupling` terms are a coarse version. **Aligned, coarse.**
- **Minimal-intervention principle / uncontrolled manifold** (Todorov & Jordan 2002; Scholz &
  Schöner 1999; optimal feedback control): the sensorimotor system PREFERENTIALLY stabilizes
  TASK-RELEVANT variables and leaves task-irrelevant deviations uncorrected; variance is
  smaller in the task-relevant subspace. **Sharp difference:** our model has no task-relevance
  weighting. A human stabilizes the fretting anchor (task-critical — the note must keep
  sounding) and lets the rest of the hand vary; our comfort model will happily trade the
  anchor's placement to relieve a spare finger's strain, because the anchor and the free
  fingers carry equal weight. This is the single largest modelling gap the study exposes.
- **Effort/optimality models** (minimum-jerk Flash & Hogan 1985; minimum-torque-change Uno
  1989; minimum-endpoint-variance Harris & Wolpert 1998): our comfort is a static
  minimum-deviation-from-neutral proxy, not a dynamic effort or noise model. **Simplification,
  intentional** for a static per-pose solver.
- **Manipulation/grasping**: contacts have roles (support/anchor vs manipulate); anchored
  contacts are stabilized. Our model has **no contact-role concept.**

## 5. Open questions (no recommendations — questions only)
1. Should comfort weight fingers by TASK ROLE (anchor > free), as minimal-intervention would
   predict? Currently all equal.
2. Should a held/sustained contact confer positional AUTHORITY (or a stationarity preference)
   on the whole hand? Currently a comfortable anchor is silent.
3. Is "one shared neutral for all fingers" adequate, or do per-finger rest postures matter?
4. Should the wrist/forearm carry an intrinsic posture comfort so the root is not a free
   rider that only feels the fingers' contact-induced angles?
5. Is minimum-deviation-from-neutral the right effort proxy, vs a torque/EMG/variance model?

## Success criterion — the one distinction
**The planner is NOT behaving strangely because the optimizer is wrong.** The optimizer
approximately solves the objective; the falsification study showed it descends toward lower
cost, and this study shows that lower-cost point is the ring finger's comfort optimum. The
hand drifts because the **comfort model is faithfully expressing assumptions that are
themselves incomplete** — comfort is angle-only, additive, EQUAL-WEIGHT across fingers, and
has NO concept of an anchored or task-relevant contact. Under those assumptions, chasing the
most-strained (moving) finger while ignoring a comfortable anchor is the correct answer.

Confidence: ~85%. It rests on three directly-measured facts (comfort is angle-only; ring
carries −0.0114 of −0.0114 the authority while the anchor carries ~0; no anchor/role term
exists anywhere in the objective), not on interpretation. The residual ~15%: the study
covered `sustained_anchor` at the frozen weights; whether the same authority pattern holds
across other phrases/tunings was not exhaustively swept.
