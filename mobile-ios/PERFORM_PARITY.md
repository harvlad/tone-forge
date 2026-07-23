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

### A. Ableton Link
Needs Ableton **LinkKit** (closed C++ SDK, free license — signup
required; add as binary xcframework).

`LinkController` (new):
- owns `ABLLink` session; publishes tempo + beat phase + start/stop over LAN.
- Link ON: session tempo drives playback; stems time-stretch to Link
  tempo via existing `timePitch` (rate = `linkBpm / songBpm`); downbeat
  aligns to Link phase.
- chop/loop launches quantize to Link boundary (`BeatClock.nextBoundary`).

### B. CoreMIDI OUT (IN already ships)
MIDI IN done: `USBLaunchpadTransport` + `MIDIKeyboardTransport` already
route hardware → contribution bus. Remaining = OUT via new
`MIDIOutTransport` (virtual source "Tone Forge Jam"):
- OUT clock: 24 PPQN from BeatClock + start/stop/continue → external gear follows.
- OUT notes: pad trigger → note-on out.
- external clock-IN (follow, not just controller notes): optional later.

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
