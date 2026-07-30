# Task relevance in redundant motor systems — literature synthesis

Model research only. How does the literature decide WHAT stays stable while everything else
varies? No implementation, no objective proposed. References at the end.

## Mathematics extracted (section 2)

**Operational-space / task-priority null-space control** (Khatib 1987; Nakamura 1987;
Siciliano & Slotine 1991; Dietrich/Ott/Albu-Schäffer 2015). Joints `q∈R^n`, task
`x=f(q)∈R^m`, Jacobian `J=∂f/∂q`.
```
  q̇ = J⁺ ẋ_des  +  (I − J⁺J) z
```
`J⁺` achieves the task; `N = I − J⁺J` projects an arbitrary secondary objective `z`
(e.g. `z = −∇comfort`) into the task NULL SPACE so it cannot perturb the task. Multi-level:
```
  q̇ = J₁⁺ẋ₁ + (J₂N₁)⁺(ẋ₂ − J₂J₁⁺ẋ₁) + …   (each task projected into higher tasks' null space)
```
Dynamically consistent version uses the inertia-weighted inverse `J̄ = M⁻¹Jᵀ(JM⁻¹Jᵀ)⁻¹`,
`τ = JᵀF + Nᵀτ₀`. **Task-relevant = the operational coordinates `x`. Minimized = task error.
Held invariant = `x`. Redundancy → null space, where comfort/posture lives.**

**Stack of Tasks / Hierarchical QP** (Mansard & Chaumette; Escande, Mansard & Wieber 2014).
Lexicographic: minimize level-1 residual; among its minimizers minimize level-2; … A cascade
of QPs, `min ||wₖ||²  s.t.  Aₖq̇ − bₖ = wₖ` (equality) or `Aₖq̇ ≤ bₖ` (inequality), each
subject to the optimality of all higher levels. Strict priority: a lower task NEVER perturbs a
higher one. **Task relevance is PRESCRIBED per level by the designer; comfort is the lowest
level.**

**Uncontrolled Manifold** (Scholz & Schöner 1999). Performance variable `P = g(e)` of
elemental variables `e`. `UCM = {e : g(e)=P*}` = null space of `∂g/∂e`. Variance is split into
`V_UCM` (within manifold, task-irrelevant) and `V_ORT` (orthogonal, task-relevant); a synergy
stabilizes `P` iff `V_UCM > V_ORT`. **Descriptive**: it identifies which variable is stabilized
from observed variance, rather than prescribing a controller.

**Minimal Intervention Principle / Optimal Feedback Control** (Todorov & Jordan 2002). Minimize
`E[Σ task-cost + effort]` under noise; the optimal feedback law corrects deviations ONLY in
task-relevant directions and leaves task-irrelevant deviations uncorrected (correcting them
spends effort for no task gain). **Task relevance is CONTINUOUS and EMERGENT from the cost's
sensitivity**, and reproduces the UCM variance structure.

**Grasp maintenance / in-grasp manipulation** (stable-grasp planning; in-grasp manipulation by
superposition; sequential grasp-conditioned manipulation). Fingertip–object contacts are held
as EQUALITY constraints / high-priority tasks (rolling or fixed), while a secondary controller
reorients or manipulates in the remaining freedom: `controller = stability(hold grasp) ⊕
manipulation(null space)`. **A held contact is a persistent constraint the secondary task must
not violate.**

## Task hierarchy — how priority is enforced (section 3)
- **Hard constraints** (equality/inequality in a QP): task cannot be violated; strongest.
- **Lexicographic / stack-of-tasks**: strict priority via null-space projection or cascaded
  QP; lower tasks provably cannot disturb higher.
- **Null-space projection**: geometric enforcement — secondary objectives ride in `(I−J⁺J)`.
- **Soft weighted sum**: all objectives added with weights; priorities are only *approximate*
  and trade against each other (a heavy secondary CAN perturb a light primary). Weakest form.
Trade-off: hard/lexicographic guarantee the primary but can be brittle/infeasible; soft
weighting is smooth and always feasible but gives no guarantee — exactly the regime our model
is in.

## Anchors — the established analogue (section 4)
A sustained guitar anchor HAS a well-established analogue: a **high-priority (or hard-
constrained) persistent contact task**, with posture/comfort resolved in its null space. In
grasp literature this is "maintain the initial grasp while performing a second manipulation" —
the held contact is a top-level constraint, the moving voice a secondary task. In whole-body
control it is a foot/hand contact task kept at priority while posture optimizes below it. The
concept "a task-critical contact stays put while non-critical DoF vary" is standard; it simply
does not exist in our objective.

## Human motor control — what humans stabilize (section 5)
Evidence that humans preferentially stabilize TASK-RELEVANT variables: UCM variance
decomposition (V_UCM > V_ORT for the task variable); OFC/minimal-intervention (task-irrelevant
deviations left uncorrected); grasp-force studies (grip vs manipulation forces scale
differentially to task requirements); endpoint/CoM stabilization (e.g. CoM in sit-to-stand);
postural synergies with obligatory neighbour coupling (Santello; Schieber). For a held fret,
the task-relevant variable is the fingertip–fret contact (the note must keep sounding);
humans would stabilize THAT and let hand/forearm posture vary within its null space.

## Comparison table (section 6)
| | **Our comfort model** | Minimal intervention / OFC | Uncontrolled manifold | Operational-space / task-priority | Stack-of-tasks / HQP | Grasp maintenance |
|---|---|---|---|---|---|---|
| Primary objective | weighted sum of contact + comfort | expected task cost + effort | (descriptive; none) | task-space error | lexicographic task residuals | grasp stability + manipulation |
| Task relevance | **none** (all terms co-weighted) | continuous, **emergent** from cost | identified post-hoc from variance | **prescribed** task coords | **prescribed** per level | contact = constraint |
| Redundancy | absorbed by soft comfort terms | left uncontrolled if task-irrelevant | large variance in UCM | null-space projector `(I−J⁺J)` | null space of higher tasks | freedom for manipulation |
| Comfort | first-class additive penalty | effort term in the cost | n/a | secondary/null-space posture | lowest priority level | secondary |
| Contacts | **soft** penalty (W=8), co-weighted with comfort | task cost | performance variable | high-priority task | high level / hard | equality constraint |
| Null-space behaviour | none explicit; comfort perturbs everything | task-irrelevant dims free | variance channelled into UCM | posture rides in null space | strict non-interference | manipulation in null space |
| Predicted on sustained_anchor | hand chases most-strained finger; comfortable anchor silent | stabilize the anchor contact, vary the rest | anchor contact = low-variance; hand posture = high-variance | anchor = priority task, hand posture optimizes below it | anchor top level, upper voice next, comfort last | hold anchor contact, move the free finger |

## Recurring design patterns (section 7)
1. **Stabilize task variables FIRST**, optimize comfort SECOND.
2. **Comfort/posture lives in the NULL SPACE** of the tasks and cannot perturb them.
3. Priority is enforced by **projection, lexicographic QP, or hard constraints** — not by soft
   co-weighting.
4. Task relevance is either **emergent from a cost** (OFC/UCM) or **prescribed by a hierarchy**
   (stack-of-tasks); both keep it separate from comfort.
5. **Contacts that must hold are constraints, not soft penalties.**
6. Redundancy is resolved explicitly (pseudoinverse + null-space projector).

## FINAL SYNTHESIS

**1. Common principles across the literature.** Every framework separates *what must be
achieved* (task) from *what is merely preferred* (comfort/posture), and enforces that
separation structurally — task first (hard or high-priority), comfort strictly second, in the
task's null space. Task relevance is represented either as an explicit hierarchy or as an
emergent property of a cost/feedback law. Redundancy is where comfort is allowed to act,
precisely because it cannot disturb the task there.

**2. How our current comfort model differs.** Our model has NO task/comfort separation: contact
(feasibility) and comfort are both SOFT additive penalties in one weighted sum (contact W=8,
comfort terms W≈1.4–2.5). There is no hierarchy, no null-space projection, no contact
constraint, and no task-relevance concept. Comfort can and does trade against contact
*placement* (the hand relocates to relieve a finger), because nothing forbids a secondary
objective from moving the primary. It is the "soft weighted sum" regime — the weakest form of
prioritization every surveyed framework improves upon.

**3. What existing frameworks would predict for sustained_anchor.** All of them predict the
opposite of what we observe: the anchor fingertip–fret contact is the task-relevant / high-
priority / constrained variable, so it is held stable while the hand posture and the moving
voice vary in its null space. OFC/UCM: the anchor is low-variance, the free DoF high-variance.
Operational-space/stack-of-tasks: the anchor is a priority task, comfort optimizes beneath it.
Grasp maintenance: hold the anchor contact, move the free finger. Our model alone predicts the
hand drifting to relieve the moving finger while the (comfortable) anchor has no authority.

**4. Open research questions (ranked).**
1. **Should the fretting contact be a task/constraint with comfort resolved in its null space,
   rather than a soft penalty co-weighted with comfort?** (Highest — this is the structural fork
   the whole literature turns on.)
2. Should task relevance be BINARY (hard constraint), PRESCRIBED-continuous (weights/levels), or
   EMERGENT from a cost (OFC)?
3. Should finger/contact authority EMERGE from a task-relevance formulation, or be PRESCRIBED
   from the phrase's anchor labels?
4. Should comfort live ENTIRELY in the null space (never trade against contact placement), or
   remain a soft term with bounded influence?
5. Is an "anchor" a PERSISTENT task across moments (temporal hierarchy / grasp-maintenance) or a
   per-moment property?
6. Does the UCM/OFC prediction — stabilize the contact, let the hand vary — actually match real
   guitarists (empirical validation)?

No recommendation is made. The purpose is to map the design space before deciding whether the
next comfort model should represent task relevance explicitly.

## References
- Khatib (1987), *A unified approach for motion and force control* — operational-space formulation.
- Nakamura, Hanafusa & Yoshikawa (1987); Siciliano & Slotine (1991) — task-priority redundancy.
- Dietrich, Ott & Albu-Schäffer (2015), *An overview of null-space projections for redundant, torque-controlled robots*, IJRR. https://elib.dlr.de/101443/2/NullspaceSurvey.pdf
- Escande, Mansard & Wieber (2014), *Hierarchical quadratic programming*, IJRR 33(7). https://journals.sagepub.com/doi/10.1177/0278364914521306
- Scholz & Schöner (1999), *The uncontrolled manifold concept*, Exp Brain Res 126:289–306. https://link.springer.com/article/10.1007/s002210050738
- Todorov & Jordan (2002), *Optimal feedback control as a theory of motor coordination*. http://people.biology.ucsd.edu/gauthier/control/todorov2.pdf ; minimal-intervention evidence https://pubmed.ncbi.nlm.nih.gov/19369362/
- Santello, Flanders & Soechting (1998), *Postural hand synergies for tool use*, J Neurosci 18(23). https://www.jneurosci.org/content/18/23/10105
- Schieber & colleagues, finger independence. https://www.jneurosci.org/content/jneuro/20/22/8542.full.pdf ; https://journals.physiology.org/doi/full/10.1152/jn.00480.2004
- In-grasp / sequential grasp-conditioned manipulation (contact maintenance during secondary manipulation). https://pmc.ncbi.nlm.nih.gov/articles/PMC11297474/
