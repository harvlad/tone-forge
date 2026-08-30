# Fretting-Hand Visual Diagnosis — why the sprite doesn't read as a hand

Gate: a screenshot WITHOUT the finger dots must immediately read as "a human left
hand fretting a guitar." The current MPFB sprite FAILS that gate. This is a
camera / projection / pose / render-style problem, not cosmetic polish.
**Contact-target accuracy (tips on dots) is NOT evidence of visual success** — the
current sprites hit their dots and still read as an unrecognizable dark paddle.

Reference used: the approved JAMN mockup `fingersilouettes.png` (a real fretting hand,
traced) at the EXACT intended viewing geometry, plus general fretting-hand photographic
geometry.

---

## What the reference (mockup) shows — the target geometry

- **Fingers are the dominant element**: four LONG, clearly-separated digits, each
  traceable from the palm knuckle up and over onto its fret, arching to press with the
  fingertip. Finger length ≈ half the hand height.
- **The arch is visible**: each finger rises from the MCP knuckle, curves, and the pad
  presses the string — you see the whole curved length, not a stub.
- **Palm is a clean rounded heel at the bottom**, minimized; the fingers fan up from it.
- **Render style is a rim-lit OUTLINE** on near-transparent fill: every silhouette edge,
  including the gaps BETWEEN fingers, is a bright thin line, so each finger is
  individually readable.
- **Left-hand read**: thumb behind the neck (a caption even says so), pinky side toward
  the bottom, the hand clearly cupping the neck from below/behind.
- **Subtle depth**: fingers overlap and foreshorten slightly → reads as a 3-D hand
  wrapping a cylinder, not a flat cutout.

## What the current sprite actually is

A solid dark blob: a large palm/back-of-hand mass filling the lower frame, with two or
three tiny finger-NUBS poking above a knuckle line. No traceable fingers, no arch, no
wrist angle, no wrap. It reads as a mitten or paddle.

## Root causes, ranked

### 1. CAMERA ANGLE + FINGER FORESHORTENING — the dominant failure
The Blender camera is orthographic at `y = −0.8`, rotation `(π/2, 0, 0)` — looking
straight along **+Y, into the board**. The fretting fingers curl so their distal
segments point **+Y (into the screen)** to press the strings. Result: the finger LENGTH
lies along the camera axis and is projected to near-zero — the fingers collapse into
stubs, and the back of the hand (a flat slab facing the camera) dominates. A straight
face-on view of a hand pressing away from you is the single worst angle for reading
finger articulation.
The mockup instead views the hand so the finger LENGTH and ARCH lie IN the image plane
(a slightly elevated, near-face-on-but-angled view) — which is exactly why its fingers
read as long articulated digits.
**Direction of fix:** elevate/angle the camera (look down onto the board at ~25–40°, or
along the neck), and/or re-orient the pose so each finger's sagittal (arch) plane is
roughly parallel to the image plane. The full arched length of every finger must be
visible; nothing critical may point down the camera axis.

### 2. RENDER STYLE — solid fill vs rim-lit outline
The sprite is an opaque dark fill with faint interior Freestyle marks. Even with a
perfect pose, a solid fill merges adjacent fingers into one mass — the inter-finger gaps
vanish. The mockup uses a transparent/low fill with a bright rim on EVERY silhouette
edge, including internal finger boundaries, so articulation is legible.
**Direction of fix:** render silhouette + internal contour EDGES (Freestyle silhouette
AND border/crease lines) as bright thin strokes over a low-opacity fill — not a solid
black shape. Each finger boundary must be an explicit line.

### 3. POSE — over-curl + palm-forward
The fingers are curled tightly (tips driven onto the board) and the dorsal hand faces
the camera flat. Real fretting fingers arch but keep visible length; the palm turns
partly edge-on so it doesn't present a big slab.
**Direction of fix:** reduce distal curl to what a real press needs, rotate the hand so
the palm is more edge-on to the camera (less slab), let the fingers fan and separate.

### 4. NECK-WRAP / DEPTH CUE — absent
The flat orthographic silhouette gives no signal that the hand cups a cylindrical neck:
no foreshortening, no side-of-hand, no visible thumb hint behind, no overlap between
hand and the neck's top/bottom edges that says "wrapping around." The thumb is fully
clipped, removing even the subtle "something is behind" cue.
**Direction of fix:** the chosen camera angle should reveal the wrap — fingers coming
over/around the neck edge, slight foreshortening, and a hint of the thumb or hand-side
behind the neck (occluded but present), so the brain reads a 3-D grip.

### 5. PROPORTION — palm dominates, fingers vanish
Consequence of (1)+(3): the visible area is ~70% palm/back-of-hand, ~30% stub fingers.
The mockup is the inverse. Fixing the camera/pose flips this automatically.

## What is NOT the problem

- Contact accuracy: tips are on the dots. Irrelevant to readability.
- Opacity / scale / anchor: cosmetic; deliberately NOT touched. Fixing those on top of a
  paddle just makes a lighter, better-placed paddle.
- The MPFB mesh itself: it is a real anatomical hand. The mesh is fine; we are viewing
  and rendering it wrongly.

## Recommended investigation order (no implementation yet)

1. **Re-shoot the camera.** Prototype 3–4 camera angles in Blender (elevated 30°,
   along-neck 3/4, low-front) against a real fretting pose and compare each dot-free
   render to the mockup by the human-recognition gate.
2. **Switch render to rim-lit outline** (silhouette + border/crease Freestyle, low fill).
3. **Relax the pose** (less distal curl, palm edge-on, fingers fanned) — re-using the
   existing MPFB rig/pose pipeline; contacts stay as targets but are no longer the
   success criterion.
4. Only once a DOT-FREE render reads as a left hand fretting a guitar, resume the app
   sprite integration and cosmetic tuning.

## Verdict

The sprite fails the human-recognition gate. The dominant cause is the flat
straight-into-the-board camera that foreshortens the fingers into stubs, compounded by a
solid-fill render that hides articulation and an over-curled palm-forward pose. All are
fixable with the existing MPFB mesh; none are cosmetic. Do not declare success until a
dot-free screenshot reads immediately as a human left hand fretting a guitar.
