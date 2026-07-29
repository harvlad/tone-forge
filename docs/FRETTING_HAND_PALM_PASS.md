# Palm / Metacarpal Pass — results

Built on the unified MPFB solver (one authoritative skeleton). Adds hand-BODY mechanics:
metacarpal cupping, transverse arch, neck conformity, finger individuality. Camera A
frozen. No per-finger IK, no bridge, no authored poses, no chord heuristics, no MANO,
no sprite/camera tuning. NOT self-approved — visual decision is yours.

## Investigation

MPFB exposes four poseable **metacarpal bones** (`metacarpal1-4.L`, index→pinky, each
with skin weights). They were previously frozen. The FK was generalized to route the
finger chains through their metacarpal and **re-validated against Blender: 0.0001 mm**
with metacarpals posed. No separate palm-cup/carpal controls exist; the metacarpals are
the physically-correct palm DoF (they carry the transverse arch).

## Implementation (all general, over MPFB DoF)

State grew 26→30: added a metacarpal cupping DoF per finger. New costs:
- **Transverse metacarpal arch** (`arch`): reward graded cupping — ulnar metacarpals
  (ring, pinky) mobile and cup more than the fixed radial (index, middle), scaled up as
  the fingers flex. Provenance: documented transverse metacarpal arch. Makes the palm
  non-flat and the knuckle row non-rigid.
- **Neck conformity** (`conform`): reward the MCP-head row to wrap — outer fingers sit
  deeper behind the board than the middle pair, cupping around the neck rather than
  lying flat.
- **Finger individuality** (`individ`): pinky flexion follows ring (shared musculature);
  index left free — reduces the synchronized look. Synergy/coupling weights relaxed so
  fingers differentiate.

## Metrics

Contacts unchanged: render-space ≤ 0.03 mm on single/two/D/G/C/three/wide; impossible
still **INFEASIBLE** (115.7 mm); render==solver 0.000 mm on all. Thumb behind neck on
all. Solve ~17–27 s.

**Differentiation (joint-angle heat map, deg):** MCP flex is non-uniform per finger
(e.g. D: index 33, middle 44, ring 36, pinky 29 — middle proud). Metacarpal cup graded
across every fixture (pinky/ring 10–18° vs index/middle −1–7°) — the arch is active.
Abduction differentiates (wide-stretch pinky −14°).

**8-note phrase:** discrete solver outputs render as ONE coherent hand progressing —
whole-hand shifts along the neck per note, pressing finger changes, unused fingers stay
compact; no explosions or frame discontinuities. Finger-move deltas small.

## Diagnostic renders (in `palm_pass/`)

- rendered hand + hand-only per fixture
- `phrase_sheet.png` — 8 discrete phrase poses
- `heatmap.png` — joint-angle differentiation
- `palm_axes.png` — MCP-head wrap cross-section (cupping arc, y-depth vs z)
- `palm_beforeafter.png` — unified (before) vs palm-pass (after), single/D/G/C

## Honest assessment (NOT a self-pass)

**Gates the pass meets:** contact fidelity unchanged (1); impossible stays impossible
(2); MCP row less rigid — metacarpal arch now active and MCP flex non-uniform (4);
fingers differentiated — measurable in the heatmap, ring/pinky coupled, index freer (5);
single-note reads as holding/waiting, compact (6, improved); 8-pose sheet reads as one
hand progressing through music (8).

**Where it falls short of the gate:**
- **Palm wrap (gate 3) improved but visually SUBTLE at Camera A.** The metacarpal cup is
  real in 3-D (see `palm_axes.png`), but Camera A (elevated front) points partly along
  the cupping/wrap axis, so the wrap reads less than it would from a 3/4 view — which is
  frozen out. The palm is less flat than before but does not yet unmistakably say
  "wrapped around a cylinder."
- **Differentiation is numerically clear but visually modest** — a viewer sees a more
  natural hand, not obviously "each finger doing its own job."
- **Gate 7 (hand-only instantly reads 'guitarist' not generic grasp):** closer, still
  arguable. I would NOT self-pass it.
- Thumb articulation still a coarse opposition curl.
- Solve time ~20 s (offline; Jacobians needed before trajectory work).

**Net:** the palm pass is a real, measurable step (arch, conformity, individuality, all
emergent from costs — zero hacks) and the phrase coherence is encouraging for the
eventual MIDI-animation goal. But at the frozen Camera A the visual gain is honest-
modest; whether it crosses your "instant guitarist recognition" bar is your call. My
recommendation if it's short: the biggest remaining lever is the wrap READ, which is
partly a camera-vs-cupping-axis tension Camera A imposes — worth revisiting the
Camera-A-vs-slight-3/4 trade you froze, OR strengthening the conformity cost so the cup
is deep enough to read even head-on.

Deliverables committed; not integrated into JAMN.
