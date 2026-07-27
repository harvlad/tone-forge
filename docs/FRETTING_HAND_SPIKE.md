# Fretting-Hand Visualization Spike — Technical Recommendation

Status: recommendation + spike implemented (G → D → Em → C)
Scope: Learn/Practice hand overlay only. No Practice redesign, no Lab, no transcription.

---

## 1. Why the current procedural-2D approach fails

The current `HandSilhouetteView` draws a hand outline directly from four fingertip
points. Everything else — knuckle row, palm, webbing, curvature — is invented per
frame from hand-tuned constants in **UI pixel space**. Consequences:

- No joints. A "finger" is a bezier ribbon, so there is no MCP/PIP/DIP flexion,
  no joint limits, and no way to bend plausibly for short reaches (planks, stubs).
- No hand unity. Each finger solves independently from its tip; the palm is
  derived from whatever the fingers did, so wide chords stretch anatomy instead of
  re-posing the wrist.
- No physical frame. The neck used uniform fret spacing and the hand used
  string-gap multiples as its unit, so every proportion problem was patched with
  another magic constant. Dozens of tuning rounds produced "recognizable" but never
  "anatomical".
- Transitions are graphics morphs. Interpolating outline control points can never
  read as a guitarist's hand, because there is no skeleton whose motion is being
  interpolated.

Drawing the silhouette IS the wrong problem. The silhouette must be a *projection
of a pose*.

## 2. Recommended architecture

```
chord symbol
  → GuitarVoicing (string/fret) + ChordFingering (finger identity, barre)
  → FingerTargets in GUITAR SPACE (mm): x = physical fret position, y = string, z = 0
  → HandPoseSolver (analytic constrained IK, pure Swift, ~µs)
       wrist/MCP-row placement → per-finger 3-segment flexion solve → limits
  → HandPose (3D joint positions, mm)
  → oblique orthographic projection into the UI neck rect
  → capsule/hull silhouette renderer (existing JAMN soft-outline style)
```

Runtime = small skeleton + analytic IK + 2D rendering. No ML, no 3D engine, no new
dependencies. Animation = animate the **targets** (SwiftUI `Animatable` already
interpolates fingertips) and re-solve the pose every frame, so intermediate frames
are always anatomically valid and the whole hand moves as one articulated unit.

## 3. Option comparison

| Option | What it solves | iOS fit | Runtime cost | Effort | Anatomy | Animation | License |
|---|---|---|---|---|---|---|---|
| SwiftUI procedural 2D (current) | drawing only | native | ~0 | sunk | poor — proven | morphs | n/a |
| **Custom Swift skeleton + analytic IK (chosen)** | pose generation | native, iOS 17 OK | µs/frame | days | good (anthropometric tables) | targets→resolve = natural | none |
| RealityKit `IKComponent` | full-body IK + rendering | **iOS 18+ only** (app targets iOS 17); macOS 15+ (desktop targets macOS 14) | scene + offscreen render pass | medium-high | good with a rigged asset | good | free |
| SceneKit + manual IK | 3D scene mgmt | deprecated-ish, no built-in IK worth using | scene render | high | same as custom | same | free |
| MediaPipe Hand Landmarker | pose **capture** from video | iOS pod ~30 MB | heavy if runtime | low (offline) | real human data | n/a | Apache-2.0 |
| Apple Vision `VNDetectHumanHandPoseRequest` | pose capture, 21 pts, 2D + confidence (no reliable depth) | native | heavy if runtime | low (offline) | real human data, weaker under neck occlusion | n/a | free |
| ManoMotion SDK | hand **tracking** (camera) | commercial SDK | heavy | integration + contract | n/a — tracks, doesn't generate | n/a | paid, contact-sales |
| MANO parametric model | anatomic mesh + pose space | Python/PyTorch, not iOS | n/a | n/a | excellent | n/a | **non-commercial only — blocked** |
| Blender/Rigify rigged hand | offline authoring/calibration | n/a (tooling) | none | low-medium | excellent reference | authoring only | GPL tool, output is ours |
| Hybrid: 3D skeleton + projected 2D silhouette (chosen) | best of both | native | µs | days | good | good | none |

Key disqualifiers: MANO license is non-commercial (explicitly prohibits
incorporation in a commercial product). ManoMotion solves tracking, not generation.
RealityKit IK requires raising both deployment targets and an offscreen render
pass just to get a silhouette — heavy machinery for a 2D instructional overlay,
worth revisiting only if we later want photoreal 3D.

Sources: [IKComponent](https://developer.apple.com/documentation/realitykit/ikcomponent),
[RealityKit skeletons & IK](https://developer.apple.com/documentation/realitykit/game-development-character-skeletons),
[MANO license](https://mano.is.tue.mpg.de/license.html).

## 4. Recommended hand skeleton

19 solved joints (thumb posed, not solved):

```
wrist
per finger (index/middle/ring/pinky): MCP, PIP, DIP, tip
thumb: CMC, MCP, IP, tip  — posture model, behind neck
```

Segment lengths from anthropometric means (mm, male 50th percentile, scalable):

```
             proximal  middle  distal   MCP offset along knuckle arch
index          45        25      22        +27 from middle
middle         50        29      24         0
ring           46        27      24        -20
pinky          37        20      19        -38
palm: wrist→middle-MCP ≈ 95, knuckle arch radius ≈ 110
```

Joint limits: MCP flexion −20°…90°, PIP 0°…110°, DIP 0°…80°, DIP ≈ 2/3·PIP
coupling (natural tendon coupling), MCP abduction ±15°.

## 5. Recommended guitar physical coordinate system

Millimetres, origin at the nut on the neck centreline, X toward the bridge,
Y across the fingerboard (low E → high E), Z off the board toward the viewer.

```
scaleLength      = 648 mm
fretFromNut(n)   = scaleLength * (1 - 2^(-n/12))     // equal temperament
string spacing   = 35 mm E→e at nut → 52 mm at saddle (linear taper);
                   within a 4-fret window we use the window-centre spacing
                   (error < 4%, invisible at UI scale)
neck thickness   ≈ 21 mm (thumb sits at Z ≈ −21)
finger contact   = 30% of a fret slot behind the wire
```

The UI transform maps the window's [wire(base−1), wire(base+3)] span onto the
neck rect — fret columns now visibly narrow toward the bridge, and one px/mm
scale factor sizes the hand. `NeckGeometry` keeps its API (`fretX`, `stringY`,
`wireX`) so dots, arrows and board all inherit physical spacing.

## 6. Chord → fingertip-target mapping

Already largely present, kept and formalized:

- `GuitarVoicing.shape(symbol:)` → string/fret (+ pinned barre forms).
- `ChordFingering.assign` → **finger identity** (1–4) + `barreStrings/barreFret`.
  Finger identity is explicit — curated table for canonical shapes, conventions
  (e.g. E-shape minor barre = ring+pinky) in the heuristic. Never inferred from
  dot order.
- New: `FingerTarget { finger, string, fret, kind: press|barre|rest }` produced in
  guitar space. Muted/open strings yield no target; unused fingers get `rest`
  posture, not a target.

## 7. IK strategy

Analytic per-finger solve inside a global two-pass wrist fit — no iterative
full-body solver needed at this articulation count:

1. Place the MCP row: orientation from the target span (wide chords rotate the
   knuckle line toward the neck axis), height Z ≈ 30 mm over the board, Y just
   below the high-E edge.
2. Per finger: flexion plane through MCP and target; 1-D bisection on PIP flexion
   with DIP = 2/3·PIP coupling and MCP making up the remainder; clamp limits.
   Closed form + bisection ⇒ deterministic, µs-cheap, no jitter.
3. If any finger is short/over-reached, translate/rotate the wrist toward the
   deficit and re-solve (2 passes suffice for guitar reaches).
4. Barre: index is a special chain — extended (PIP ≈ 10°), laid across the
   strings; MCP near the high-E side, tip at the low-E barre string.
5. Rest fingers: fixed natural curl (MCP 30°, PIP 45°, DIP 25°) hovering 15 mm
   over the strings beside the fretting cluster.

## 8. Thumb strategy

Posture model, never a solve target: thumb pad opposes the middle-finger MCP at
Z ≈ −neckThickness, X ≈ middle-MCP X. Projected, it is occluded by the board —
we draw nothing (debug view shows its assumed position). Thumb-over-neck becomes
a separate explicit pose regime later, never the default.

## 9. Current→next chord animation

Animate **targets**, not joints, and re-solve per frame:

- `ChordTransition.analyze` already classifies Stay/Move/Lift/Place per finger.
- Anchor fingers: targets unchanged ⇒ solver keeps them planted while the wrist
  drifts — exactly what a guitarist does.
- Moving fingers: target follows release → travel → land phases (lift = +Z and
  slight extension, travel = arc, land = drop). SwiftUI `Animatable` fingertip
  interpolation + per-frame solve gives overlapping motion for free; the existing
  looper choreography (`handLifted`, hand-leads-dots) plugs in unchanged.

## 10. 3D→2D silhouette rendering

Oblique orthographic projection: `(x, y, z) → (x, y + 0.3·z)` (px/mm-scaled) —
front view with a subtle depth cue so lifted fingers and the hovering knuckle row
read correctly. Then the existing JAMN soft-outline language: per-finger capsule
chains through projected MCP→PIP→DIP→tip with tapered radii, palm hull from wrist
ellipse + knuckle arch, opaque dark fill, glow rim, back-to-front finger layering
with hidden-rim trimming. No mesh, no textures, no photorealism.

## 11. RealityKit: runtime or authoring?

Neither, for now. Runtime is blocked by deployment targets (iOS 17/macOS 14 vs
iOS 18/macOS 15 requirement) and is overkill for a 2D overlay. As an authoring
tool it adds nothing over Blender. Revisit only if the product later wants a
true 3D hand.

## 12. MediaPipe / Vision: runtime or capture-only?

Capture/validation only, never runtime. Runtime inference for a deterministic
instructional overlay is waste. Offline, MediaPipe (Apache-2.0, world-space 3D
landmarks) is preferred over Vision (2D + confidence, weaker under neck
occlusion) for extracting reference poses from video of a real guitarist.

## 13. Real-guitarist pose library?

Yes, small and offline — but **after** the spike. ~15 captures (open chords,
E/A-shape barres, a power chord) processed through MediaPipe → normalized joint
angles → used to (a) validate solver output, (b) tune rest postures, MCP-row
height and wrist angles. Not an app dependency; a calibration table checked into
the repo.

## 14. Files/classes that change

- `ToneForgeEngine/NeckPlay/GuitarPhysicalGeometry.swift` — NEW: physical guitar
  space + UI transform.
- `ToneForgeEngine/NeckPlay/HandPoseKit.swift` — NEW: skeleton spec, targets,
  solver, pose types, projection.
- `ToneForgeEngine/NeckPlay/GuitarNeckPlay.swift` — `NeckGeometry` internals go
  physical (API unchanged); `HandSilhouetteView.draw` becomes: tips → targets →
  solve → render (Animatable tips interface unchanged, so LearnView, sheets,
  loopers, toggle all work untouched); debug overlay flag.
- `ChordFingering` — unchanged (already provides finger identity + barre).
- Tests: `NeckHandRenderHarness` gains debug-mode renders; Learn goldens re-record.

## 15. Prototype (spike) plan

1. Physical geometry + `NeckGeometry` swap. Verify board renders with tapered
   fret columns.
2. Skeleton spec + solver, unit-testable pure functions.
3. Renderer: debug skeleton view first (joints, bones, targets, neck edge,
   thumb ghost), silhouette second.
4. Harness renders G, D, Em, C in both modes + mid-transition frames (D→G etc.).
5. Iterate solver constants against the debug view only; silhouette is never used
   to debug the skeleton.

## 16. Estimated complexity

- Geometry: ~120 LOC. Solver + skeleton: ~350 LOC. Renderer rework: ~200 LOC.
- 1–2 days of solver-constant iteration against the debug view.
- Risk-adjusted: small; all pure Swift, no dependency, no target bump.

## 17. Risks

- 2.5D projection of a truly 3D posture can still look subtly wrong (fingers
  crossing strings they don't play). Mitigation: debug view + pose priors from a
  small capture library.
- Barre + stacked-finger chords stress the planar-flexion assumption; may need a
  per-finger abduction pass.
- Transition arcs through target space can graze wrong strings mid-flight;
  acceptable for the spike, fixable with via-points later.
- Left-handed mode: whole pipeline is mirrorable by negating guitar-space Y and
  the projection X; no assumptions baked in beyond one `mirrored` flag (not built
  now, kept possible).

## 18. Licensing blockers

- MANO: **blocked** (non-commercial license).
- ManoMotion: commercial contract required; not needed.
- MediaPipe: Apache-2.0 — fine (offline tooling only).
- RealityKit/Vision: Apple platform frameworks — fine.
- Everything shipped in the spike: first-party code, no third-party assets.
