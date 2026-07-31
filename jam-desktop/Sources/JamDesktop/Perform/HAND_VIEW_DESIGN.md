# Hand View — architecture & the fingering seam

Hand View V1 is complete. This note fixes the boundaries so the renderer can outlive any change
in *where finger assignments come from*. **The renderer must require no changes when the
fingering provider changes.**

```
   Perform Timeline            ChordRibbonModel: chords (symbol, start, end) over song time
        │
        ▼
   Finger-Assignment Provider  -> per timeline segment: [FingerContact] (finger 1-4, string, fret)
        │                          (TODAY: HandFingering, a deterministic heuristic)
        ▼
   Animation layer             -> turns the discrete per-segment contacts into smooth per-finger
        │                          render states over time (plant / lift / move / arrive / shift)
        ▼
   Hand Renderer               -> pure presentation: draws the neck + fingers for a given time
```

## Responsibilities

**Hand Renderer** (`HandNeckView.draw`) — *pure presentation.* Given per-finger render states at a
time, it draws: the neck (nut-right, adaptive fret window), the wrist origin, light curved stems,
circular numbered fingertips, motion trail, arrival pulse. It owns the *visual language* (calm,
sticky, event-only motion, smootherstep) and **nothing about music theory or fingering origin.**
It never parses chords.

**Animation layer** (`HandNeckView.sample` + state helpers) — turns a provider's discrete
per-segment contacts + segment timing into a continuous per-finger state at time `t`: sticky
plant/lift, tempo-aware transitions into the next segment, arrival detection, on-board rest for
idle fingers. Consumes only `FingerContact` + segment start/end — **provider-agnostic.**

**Finger-Assignment Provider** — the ONLY theory-aware, swappable piece. Maps a timeline segment
(today: a chord) to `[FingerContact]`. Today = `HandFingering` (dots → distinct fingers by
ascending fret/string; documented approximations for barres / >4-note chords).

## The data contract (stable)
```
FingerContact { finger: Int(1...4); string: Int(0...5, low E=0); fret: Int(>=1) }
BarreSpan     { fret: Int; lo: Int; hi: Int }              // finger 1 across lo..hi strings
HandShape     { barre: BarreSpan?; fingers: [FingerContact] }
```
Per timeline segment: a `HandShape` — an optional index **barre** (one finger owning multiple
strings) plus the remaining `[FingerContact]` (open/muted carry no finger) — with the segment's
`start`/`end`. Everything downstream is built from this — it is the seam. The renderer draws a
barre as a real bar spanning its strings, never as duplicated fingers.

## Swapping the provider (no renderer change)
Any future provider yields the same `[FingerContact]`-per-segment stream; the animation + renderer
are untouched. Candidates:
- **Planner output** — biomechanical fingering from the (frozen) hand planner, if unfrozen.
- **Manual finger annotations** — user- or editor-supplied fingerings.
- **Guitar Pro / MusicXML import** — fingering carried in the file; also unlocks per-note LEAD
  (not just chords) since the segment can be a single note.
- **Machine-learned fingering** — a model producing `[FingerContact]` per segment.

## One recommended refactor when a 2nd provider arrives (NOT now)
Today `HandNeckView(chords:positionSeconds:)` calls `HandFingering` in its init — the provider is
inlined for V1. When a second provider lands, lift it out: pass a precomputed
`[(start, end, [FingerContact])]` (or a `FingerAssignmentProvider` protocol) into the view instead
of raw chords. That is the only change needed to make the provider fully pluggable; the draw/sample
code stays as-is. Do not do this speculatively — V1 is frozen.

## The overlay architecture (Perform v2)
The neck is the stage. Five independent display layers sit over one shared chord/playhead —
three drawn ON the neck, two as small reference cards beneath it:

- **Motion** — trajectories, ghost trail, arrival pulses, movement emphasis (on neck)
- **Animated Hand** — a realistic pose drawn *directly on the neck* at ~26% opacity; strings and
  dots stay fully visible on top. Anatomical context (wrist / palm / curvature), never dominant.
- **Finger Dots** — coloured contacts + finger-number identity; the primary instructional layer.
- **Chord Diagram** / **TAB** — supporting reference cards below the neck (verify the fingering).

Visual priority is fixed by layout: fretboard > dots+motion > hand > chord/TAB. Each layer is a
pure boolean into `HandNeckView` (`showDots` / `showMotion` / `showHand`) or a reference card; no
renderer knows whether another is enabled.

**Hand-overlay provider seam.** The overlay is provider-driven exactly like the fingering seam.
Today the provider is `HandPoseLibrary.spriteImage(for: symbol)` — a baked static pose per chord,
"good enough to establish the UX architecture." A future provider can supply planner-animated,
anatomically-driven hand motion; the renderer draws whatever pose source it's given at the current
time, so **the UI layout never changes when the hand provider changes.**

## V1 boundaries (intentional)
Approximate barre fingering; >4-fretted-note chords approximated; no per-note lead fingering;
slides / hammer-ons deferred until richer movement data exists. All are provider-side limitations —
better providers lift them without touching the renderer.
