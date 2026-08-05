# Fretting-Hand Pose Diagnosis — why it still looks like per-finger targeting

Camera A is frozen and solved the projection problem. The remaining failure is the
whole-hand POSE. This diagnosis explains why, before any change.

## THE STRUCTURAL FINDING (reframes the whole increment)

**The Camera-A renders you are judging are NOT produced by the biomechanical solver.**
There are two entirely disconnected hand systems in the tree:

1. **The renderable hand** (`tools/handrig/blender_mpfb_rig.py`): the MPFB mesh posed by
   **Blender per-finger IK constraints** — `add_ik()` creates ONE `IK` constraint per
   finger, each chained to a single point target, plus a pole. That is, by construction,
   **independent fingertip targeting**. There is no coupling, no synergy, no whole-hand
   prior, no shared cost — four separate 3-DoF IK chains each yanked to a point. The
   renders "look like an anatomical hand whose individual fingers reach target
   coordinates" because that is *literally* what they are.

2. **The biomechanical solver** (`tools/handrig/solver/*.py`, increments 1–2): the free
   26-DoF + soft synergy-manifold model with contact/collision/strain costs. It has
   never touched the MPFB mesh. It is rendered only as a matplotlib stick figure. All the
   synergy/coupling work is stranded off the render path.

So the fix is not only to tune weights — it is to **unify**: route the (improved)
biomechanical solver's whole-hand pose onto the MPFB rig via forward kinematics (no
Blender IK), and render that at Camera A. The solver becomes authoritative; the MPFB
mesh is skinned to the solved skeleton.

## Secondary diagnosis — the solver's OWN naturalness weaknesses

Even once connected, the current scipy solver would still look targeted, because:

- **Contact-point weight dominates.** `W.contact = 6.0` on a squared-mm point residual;
  a finger 10 mm off costs 600. Naturalness terms are `strain 0.35`, `manifold 1.2`,
  `splay 0.9`, `coupling 1.5`. Contact outweighs naturalness ~5–15×, so the optimizer
  nails each tip and treats pose quality as a rounding error. A 0.05 mm contact is
  achieved at any anatomical cost.
- **Targets are exact POINTS, not regions.** `PointContact.residual = ‖tip−target‖²` —
  zero spatial freedom. The tip is pinned to one mm, leaving no room for the whole hand
  to settle into a low-strain configuration inside the physically-valid fret area.
- **Rest/neutral prior is an OPEN hand.** `strain_penalty` pulls unused fingers toward
  MCP 15°, abd 0, PIP 20°, DIP 13° — a barely-curled, near-flat hand, not a
  fretting-ready curl. And its weight (0.35) is negligible, so unused fingers drift to
  whatever the seed/collision leave — a spread, open gesture (the single-note failure).
- **No cupping / neck-approach prior.** Palm orientation is free wrist rotation with only
  `wrist_cost = rz²·0.5` (discourages twist). Nothing pulls the palm to cup the neck or
  the MCP row to flex toward the board — so the hand never adopts a global fretting set;
  it stays a flat hand poking at points.
- **Limited coupling.** Only DIP≈⅔·PIP is enforced as a cost; ring/pinky independence
  and neighbour-flexion correlation live only in the (weakly-weighted) manifold.
- **Manifold too weak to matter.** At weight 1.2 vs contact 6.0, the synergy attraction
  cannot pull the hand onto the natural manifold against the contact pull.

Net: contact-point domination + no region freedom + open neutral + no cupping + weak
manifold ⇒ behaves as per-finger IK with cosmetic naturalness sprinkles. Combined with
the render actually being Blender per-finger IK, the unnaturalness is over-determined.

## Fix plan (this increment)

1. **Contact REGIONS, not points.** Zero penalty inside the physically-valid fret area
   (correct string ±tolerance in z, behind the wire within the fret slot in x, pad within
   a depth band in y); quadratic only outside. This hands the spatial freedom to the
   whole-hand/strain/manifold terms. Feasibility = tip inside the region.
2. **Rebalance so naturalness dominates INSIDE the region.** Region penalty is 0 when
   valid, so strain/manifold shape the pose freely once contacts are satisfied.
3. **Fretting-ready neutral + stronger strain/manifold** so unused fingers collapse to a
   compact ready curl and the hand stays on the coupled synergy manifold.
4. **Cupping / neck-approach prior**: reward MCP-row flexion toward the board and a
   palm-cups-neck wrist orientation (general, geometry-driven — not chord-specific).
5. **Whole-hand repositioning for stretches**: penalize near-full finger extension so
   wide contacts move the wrist rather than splaying fingers.
6. **Unify**: solved joint angles → MPFB rig bones (FK) → Camera A render, with a
   hand-only (guitar removed) variant as the harsh gate.

All GENERAL — no chord names, no per-finger IK, no authored poses, no MANO data.
