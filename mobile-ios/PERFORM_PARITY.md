# Perform Parity — closing the Launchpad gap

Benchmark: Novation/Focusrite **Launchpad — Music & Beat Maker** (iOS).
Our comparable: `mobile-ios` Tone Forge **Jam** ("iOS spin-off of the
Launchpad experience").

Our edge (keep leaning in): remix *your* song not their loops; pads are
the song's real chords, always in key; full producer pipeline behind the
toy (MIDI extract + gear match + Ableton export). Positioning line:
**"Turn any song into a playable instrument."**

Their edge = the three gaps this doc specs.

Ship order (cheapest-highest-impact first):
**instant gratification → performance FX → MIDI → Ableton Link.**

---

## Current state (grounded 2026-07-23)

- Master FX = studio bus only: `FXSettings` (EQ / comp / reverb / delay),
  insert `mainMixer → masterEQ → masterComp → output` + parallel
  reverb/delay send (`AudioEngine.buildMasterFXGraph`). **No
  performance/DJ FX.**
- `TransportClock` = song-seconds + rate. Beat/bar derived via `BarMath`
  from `tempoBpm` / `beatsPerBar` (song analysis). `SequencerClock` has
  bpm + step phase.
- MIDI **IN** exists in the app target: `USBLaunchpadTransport` +
  `MIDIKeyboardTransport` (hardware pads/keys → contribution bus). The
  `.notConnected` stub is only the pure-`ToneForgeEngine` fallback. Gap =
  MIDI **OUT** (clock + notes → external gear) and Ableton Link.
- **No Ableton Link** (LinkKit) anywhere.
- Bounce path excludes master FX (documented limitation) — perf FX won't
  record until bounce reworked (tracked under record/share, separate gap).

---

## Spec 1 — Performance FX (DJ FX)

Momentary, beat-synced, gesture-driven. Distinct from studio bus FX.
New master stage BEFORE the studio chain:

```
mainMixer → perfFXChain → masterEQ → masterComp → output
```

### New: BeatClock (ToneForgeEngine)
```swift
public struct BeatClock {
    var beatPhase: Double      // 0..1 within current beat
    var barPhase: Double       // 0..1 within current bar
    var beatDuration: Double   // 60/bpm/rate
    func nextBoundary(subdiv: Double) -> Double  // song-sec of next 1/N grid line
}
```
Reads `TransportClock.nowSongSeconds` + song `tempoBpm`/`beatsPerBar`. No
tempo → rate knob falls back to free Hz.

### v1 FX set (ship 5)
| FX | Impl | Gesture |
|----|------|---------|
| Filter | `AVAudioUnitEQ` 1 band `.resonantLowPass`/`.resonantHighPass` | XY pad X=cutoff Y=reso, hold |
| Gater | beat-synced gain square-wave on `perfGateMixer`, BeatClock phase | hold; subdiv 1/4·1/8·1/16 |
| Stopper | ramp `clock.setRate → 0` over N beats + pitch drop + gain fade | hold=brake, release=snap |
| Flanger | `AVAudioUnitDelay` 1–10ms LFO-mod delayTime, feedback ~60% | hold |
| Delay-throw | ramp existing `masterDelay` mix up while held, high feedback | hold=throw |

**Stutter / beat-repeat deferred v1.1** — needs ring-buffer capture + loop
node. Hardest. Not faked.

### Model (mirror FXSettings.swift)
```swift
public struct PerfFXParams: Codable, Sendable { ... }  // static config
public struct PerfFXState { ... }                      // live momentary flags + filterXY
```
New `PerformanceFXChain` owns insert nodes, subscribes to BeatClock,
applies momentary state at buffer rate.

### Files
- new `ToneForgeEngine/BeatClock.swift`
- new `Audio/PerformanceFXChain.swift`
- `AudioEngine.swift`: insert perfFX stage (rewire `mainMixer → perfFX → eq`)
- `JamView`: 2 FX rows (momentary pads + XY surface for filter)

---

## Spec 2 — Ableton Link + MIDI

Independent sync masters. Priority: **Link > MIDI-clock-in > internal.**

### A. Ableton Link — DRAFTED (reconciler tested; controller stubbed)
Needs Ableton **LinkKit** (`github.com/Ableton/LinkKit`, free license —
signup + app registration; add as xcframework). NOT vendored: adding it
requires accepting Ableton's Link license.

- `LinkReconciler` (engine, pure, 7 tests): stretch ratio
  (`linkBpm/songBpm`, clamped), shortest signed phase-nudge (wraps to
  ±½ bar), phase-lock tolerance.
- `LinkController` (app): owns the `ABLLink` session + a 50 ms poll loop
  feeding tempo/phase into the transport via injected sinks
  (`applyStretch`, `nudgeSeconds`). Guarded by `canImport(ABLLink)` —
  **inert stub until LinkKit is vendored** (`isAvailable == false`, all
  no-ops), so the repo builds without the SDK.

LICENSE (checked against LinkKit LICENSE.md, "Ableton Link SDK License
v2.0"): "You may not … distribute the Link SDK or parts of it in any
way." So LinkKit **must not be committed** to this repo — each dev
fetches it and accepts the license. The repo also ships **headers only,
no prebuilt lib** — build the static lib from its CMake/makefile.

SETUP:
  1. `git clone github.com/Ableton/LinkKit` into a gitignored
     `Vendor/LinkKit`; build the lib per its makefile.
  2. Add the lib + `LinkKit/` headers to project.yml (a target that is
     absent on a fresh clone → keep it optional).
  3. Import in the bridging header → `canImport(ABLLink)` flips true.

ABLLink signatures were verified against the cloned `ABLLink.h` — this
fixed two wrong calls in the first draft (`ABLLinkEnable` and
`ABLLinkNumPeers` don't exist; use `ABLLinkSetActive` /
`ABLLinkIsEnabled` / `ABLLinkIsConnected`). Still not compiler-checked
(app target blocked on the iOS platform).

Remaining: stem time-stretch coupling (reuse timePitch path), start/stop
callback sync, chop/loop quantize to Link boundary
(`BeatClock.nextBoundary`), UI toggle, on-device verify.

### B. CoreMIDI OUT (IN already ships)
MIDI IN done: `USBLaunchpadTransport` + `MIDIKeyboardTransport` already
route hardware → contribution bus.

OUT — DRAFTED (engine-tested; app target unbuilt until iOS platform):
- `MIDIOutEncoder` + `MIDIClockGenerator` (engine, pure, 8 tests): byte
  encoders + 24 PPQN pulse math phase-locked to BeatClock.
- `MIDIOutTransport` (app): virtual source "Tone Forge Jam" via
  `MIDISourceCreateWithProtocol` + `MIDIReceived`; clock / start / stop /
  continue / note-on / note-off.
- `AudioEngine`: `setMIDIClockOutEnabled`, 1 ms clock driver emitting
  crossed pulses, Start/Stop/Continue on play/pause/stop, span
  re-anchored on seek.

Wired:
- Pad trigger → note-out from `ModeCoordinator.execute` (synthNoteOn/
  Off + padSynthNote; jam pads auto-release so a note-off is scheduled
  one beat later). Replays don't re-emit.
- Settings toggle "MIDI clock output" (@AppStorage "midiClockOut"),
  re-armed in bootAudio.

Remaining:
- Jitter: clock rides a main-thread timer; sample-accurate host-time
  scheduling is the follow-up.
- external clock-IN (follow, not just controller notes): optional later.
- On-device verification (whole app target blocked on iOS platform).

### Files
- new `Audio/MIDITransport.swift` (CoreMIDI; replaces stub in `LaunchpadTransport.swift`)
- new `Audio/LinkController.swift` + `LinkKit.xcframework`
- `TransportClock.swift`: external-sync mode (tempo/phase can be driven, not only internal)

Sequence: MIDI first (no external dep, unblocks hardware). Link second
(needs SDK license + stretch reconcile).

---

## Spec 3 — Instant gratification

First run = empty library; deep analyze = 2–4 min. Four fixes:

1. **Bundled demo songs** (biggest win). Ship 1–3 pre-analyzed bundles in
   Resources (`analysis.json` + per-stem AAC + peaks). First launch:
   library populated, tap → Perform instantly, zero network. `BundleStore`
   already loads bundles — add local-first load path.
   ⚠️ **BLOCKED on licensing** — needs original / CC0 / commissioned
   tracks. Decide source before build.
2. **Starter sample packs.** `PacksBrowserView` + virtual packs exist.
   Ship 2 free packs so grid has sounds with no song loaded.
3. **Progressive analysis (Quick-first).** Backend has Quick (~5s:
   tempo+key+waveform) vs Deep. On `analyze-url-stream`: return Quick
   immediately → user taps in-key pad synth while stems/chords stream in.
4. **First-run → Perform.** Skip Library on first launch; drop into a demo
   song mid-playback + coach-mark. Kill the empty-state cliff.

### Files
- Resources `DemoBundles/` (blocked on licensing)
- `BundleStore.swift`: local-bundle load path
- `ImportCoordinator.swift`: Quick→Deep progressive states
- `RootView.swift`: first-run route to Perform

---

## Spec 4 — Jam Samples (Ableton Session-view clips) — SHIPPED

A third Jam pad-mode, `Samples`, that turns the loaded song's own chops
into launchable clips — "remix your song" on the grid + Launchpad.

### Grid
- **All stems in one grid.** `AppState.jamSampleFlatPads` flattens every
  Song DNA pack's chops (pack order, then padIdx) into one ordering the
  on-screen grid, the Launchpad mapping, and the LED mirror all share —
  so on-screen pad N == hardware pad N == LED N.
- Each pad is labeled **stem + chop** (Drums · Beat 1, Bass 1, C, Am…)
  and tinted by stem family.
- Empty state when the song has no chops.

### Clip behavior (Session-view)
- **Tap / Latch** toggle (persisted). Latch = tap on / tap off (loops);
  Tap = plays while held.
- **Launch quantization.** Clips wait for the next bar so multiple pads
  start together on the downbeat — driven by the ONE transport clock, so
  clips sync to each other AND to the song when it plays. Section/phrase
  chops loop (`loopPointSec = 0`); chord chops are one-shot stabs.
- **Sample jam ≠ song.** Launching a clip while stopped rolls the CLOCK
  (for sync + loop) but NOT the song stems — a synced sample jam with no
  song. The song starts independently via the transport play button; its
  stems join the already-rolling clock, aligned. Stop halts the clock.
- **First launch is immediate** — the transport starts at that launch, so
  the first clip is beat 1 (no bar wait); later launches quantize.
- **Armed / playing indicators.** Pads show an hourglass + orange border
  while queued for the downbeat, flipping to ↻ + bright fill when playing
  (`SampleVoicePool.pendingPadKeys` / `ringingPadKeys`).

### Launchpad
- Hardware pads trigger the clips (`ModeCoordinator` maps bus pad events
  onto the flat chop list when the Jam surface is in Samples mode).
- LEDs mirror clip state: dim = idle, amber = armed, bright = playing.

### Scheduler policy
- Launch quantization now honors the grid for one-shots too (was forced
  immediate) when a quantize grid is set and the transport runs — applies
  to Jam Samples, Contribute pads, and sequence-pad launches. Quantize
  `.off` keeps the instant drum-machine feel.
- Buffers decode on song load (not lazily on first tap) so the first hit
  isn't a silent `padNotFound` racing an in-flight decode.

### Related fixes
- Master compressor bypassed when neutral (it was hard-limiting at
  -20 dB, squashing the whole mix — quiet master + samples ducking).
- Demo v2: 16 s / 8 bars with drums/bass/other loop packs so Samples
  shows real loops; chord chops feed the Chords tab.
- Chords tab marks the song's actual progression (green dot) so the
  diatonic palette reflects the specific song, not just the key.

### Deferred
- Sample-jam UI when no song (playhead/chord readout stay parked).
- Per-clip length / warp; stutter/beat-repeat perf FX (spec 1 v1.1).
