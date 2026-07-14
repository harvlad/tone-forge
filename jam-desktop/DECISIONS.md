# Decision log — jam-desktop

Chronological. Each entry captures a decision, the alternatives
considered, and the reason. Do not delete entries — supersede them
with a new entry that references the old one. Format follows
`mobile-ios/DECISIONS.md`.

## D-001: Native SwiftUI rewrite, new top-level `jam-desktop/` package

**Date:** 2026-07-13
**Decision:** Phase 3 desktop is a full-parity native SwiftUI macOS
app in a new top-level SwiftPM package, replacing the web jam UI
(`backend/static/jam.js`, ~13.9k lines) and the `jam-app/` WKWebView
wrapper.
**Alternatives:** keep jam-app (WKWebView) and grow a JS bridge;
fold a second executable into `connect/Package.swift`.
**Why:** the web UI's audio path (Web Audio) can't do low-latency
monitoring or pitch-preserving tempo-stretch; a bridge to Connect.app
adds a process boundary for every interaction. A fresh package keeps
Connect's release cadence untouched while path-depending on
`connect` (ConnectCore) and `mobile-ios` (ToneForgeEngine) so the
algorithmic core is shared, not re-written.

## D-002: Three-layer targets — Core / Audio / App

**Date:** 2026-07-13
**Decision:** `JamDesktopCore` (pure logic, Foundation only),
`JamDesktopAudio` (AVFoundation + CoreMIDI), `JamDesktop` (SwiftUI).
**Why:** Core stays headless-testable with plain `swift test` — all
view models, bridge frames, transport math, chord/tab models and the
Launchpad controller run without an audio device or window server.
Mirrors the mobile Engine/Mobile split that made the iOS suite fast.

## D-003: Transport authority — hybrid local-first over connect-bridge

**Date:** 2026-07-13
**Decision:** the desktop app owns audio and the transport clock
(audio clock = ground truth) and mirrors state over
`/ws/connect-bridge` exactly as jam.js does: `transport_state`
throttled while playing + immediate on discrete changes,
`session_data` + `load_stems` on attach, `connect_state` /
`latency_report` / `input_meter` from the monitor path. Inbound peer
frames apply last-writer-wins. Hello:
`{role: "connect", client_kind: "jam-desktop", protocol_version: 2}`.
**Alternatives:** make the backend's `session/transport.py` reducer
authoritative (it exists but is NOT wired into the bridge dispatch
loop); invent a new desktop-only protocol.
**Why:** zero backend changes, and a co-open browser jam pairs with
identical semantics to today's web↔Connect pairing. Dual-authority
races resolve last-writer-wins — the existing de-facto behavior.

## D-004: In-process audio via ConnectCore + minimal additive seam

**Date:** 2026-07-13
**Decision:** audio runs in-process on ConnectCore's `AudioEngine`.
ConnectCore gained exactly two additive members: a public `avEngine`
accessor and an `onGraphRebuilt` callback fired after successful
device reconfig. Stems play through a new `DesktopStemPlayer`
(port of mobile `StemPlayer`: per-stem player→gain→submix→
`AVAudioUnitTimePitch`, 0.5–1.0 rate, bypass at 1.0, scheduleSegment
seek, solo/mute matrix), attached to the shared `AVAudioEngine`.
**Alternatives:** grow ConnectCore's buffer-based stem API with
seek/solo/tempo/loop; run Connect.app as a helper process.
**Why:** ConnectCore's stem API is deliberately small and shared with
the shipping Connect.app — growing it risks that product. External
node attachment behind `onGraphRebuilt` survives device flaps (the
seam re-attaches the stem subgraph after each rebuild). TimePitch
gives pitch-preserving practice tempo — an improvement over the web
player's playbackRate.

## D-005: apply_chain ack embeds the resolved chain spec (backend)

**Date:** 2026-07-13
**Decision:** the one deliberate backend change of Phase 3:
`/ws/connect-bridge`'s `apply_chain` ack now embeds the resolved
chain spec, so the requesting client programs its local DSP straight
from the ack instead of re-fetching or waiting for the broadcast
echo.
**Alternatives:** desktop re-fetches the chain by id after acking;
parse the peer-broadcast copy.
**Why:** the requester needs the spec synchronously to program DSP
without a second round-trip; existing clients ignore unknown ack
fields, so the change is additive and protocol-version-neutral.
Covered by `backend/tests/test_connect_bridge_apply_chain.py`.

## D-006: Launchpad — reuse the mobile MIDI seam, split across targets

**Date:** 2026-07-13
**Decision:** the Launchpad stack reuses ToneForgeEngine's protocol
layer (`LaunchpadProMK3Protocol`, `LaunchpadTransport`, `Quantizer`,
`ChopsClient`) unchanged. The mobile `MIDIInterface` seam is ported
split in two: the protocol + endpoint types live in `JamDesktopCore`
(no CoreMIDI import → headless tests via `FakeMIDIInterface`), the
`CoreMIDIInterface` implementation lives in `JamDesktopAudio`. The
on-screen 8x8 panel calls `LaunchpadController.padDown/padUp`
directly — the same methods the hardware transport calls — so screen
and device are interchangeable; there is no second transport.
**Why:** the mobile seam is hardware-verified (endpoint names, mode
SysEx, vel-0 releases, LED batching, underpower heuristic); porting
it verbatim keeps that verification. The Core/Audio split preserves
the headless test boundary (D-002).

## D-007: jam-app deprecated; packaging clones proven scripts

**Date:** 2026-07-13
**Decision:** `jam-app/` (WKWebView wrapper) is deprecated with a
README pointer and stays in-tree until jam-desktop parity sign-off.
`jam-desktop/build_app.sh` clones `jam-app/build_app.sh` (dev .app
assembly, ad-hoc sign) and adds the codesign/notarize/DMG stages from
`connect/build_release.sh` behind a `--release` flag, minus Sparkle
(jam-desktop has no auto-update yet). Hardened-runtime builds carry
`Resources/JamDesktop.entitlements` (audio-input) so the monitor mic
path survives signing.
**Why:** both scripts are battle-tested; forking beats abstracting a
shared script three apps would fight over.

## D-008: layer recording — empty padMapping, grid resolved at fire time

**Date:** 2026-07-13
**Decision:** desktop takes reuse the engine's frozen SessionCapture
v1 wire format with `appMode = .sample` and an EMPTY `padMapping`.
Desktop chop pads can't be expressed as `PadSampleReference`
(packPad / localSample / sequence only), but the grid is
deterministic from the song bundle + chop edits for a
`songBackendId`, so replay (`ReplayExecutor`) and bounce
(`SessionBounceService`) resolve pad coordinates against the CURRENT
Launchpad grid; unassigned pads no-op. Events are published from
`SessionController.onTrigger/onRelease` (not
`USBLaunchpadTransport.onContribution`) with `timestamp = quantized
fire-at` so replays land on the grid the way the take sounded, and
screen + hardware pads capture identically. Pad addressing uses the
ContributionEvent convention (`PadEventMapping`), so files decode on
iOS and vice versa.
**Alternatives:** extend the frozen wire enum (breaks iOS decoders);
wire the transport's pre-stamped events (raw press times, misses
screen pads).
**Why:** cross-platform recordings without touching the frozen v1
schema; one capture point covers every input path. Bounce mix
topology (deterministic pure-Swift renderer) intentionally differs
from the live musicBus graph — offline exports trade FX parity for
bit-identical repeat renders.

## D-009: Jam pads, wavetable synth and pack browser (Phase 5)

**Date:** 2026-07-13
**Decision:** desktop jam surface comprises three pieces: a JamInKeyModel
(pure Core logic, mirrors iOS JamSettingsStore) hosting 12 performance
pads over the engine's JamInKeyLayout/JamPadGrid12Mapping; a
DesktopSynthNode (AVAudioSourceNode hosting WavetableSynth, connects to
musicBus.input so master FX color the synth); and a PacksModel +
PackPadPlayer pair (pack catalog fetch, streaming download, pad
triggering via the existing SequencerChopTriggering seam). v1
simplification: pack pads are one-shot file triggers — loopPointSec,
chokeGroup and per-pad manifest effects are ignored; song-derived pads
(stemSlice, no filename) are deferred.
**Alternatives:** re-implement the iOS SampleScheduler (adds quantize +
hold/toggle + section gating); embed per-voice delay/filter on pack
pads (iOS SampleVoicePool pattern).
**Why:** WavetableSynth is allocation-free and already validated;
reusing SequencerChopTriggering for pack playback shares the voice
pool and keeps mapping-tests free of AVAudioEngine. One-shot triggers
cover 90%+ of use while cutting scope — loop/choke land in a follow-up
with the full scheduler port.

## D-010: Learn scoring — chord practice with per-section pass tracking

**Date:** 2026-07-13
**Decision:** desktop Learn mode reuses ToneForgeEngine's pure scoring
core (LearnScorer, LearnPress, LearnPassResult, SongLearnProgress,
SectionProgress) verbatim; UI-facing session state lives in
JamDesktopCore/Learn/LearnSessionModel.swift. Progress persists to
App Support/toneforge/learnProgress/{analysisId}.json via
LearnProgressStore (same wire format and sectionKey normalisation as iOS).
Chord presses voice through DesktopSynthNode.playChord; loop-wrap
detection fires passCompleted via onChange(of: transport.positionSeconds).
**Alternatives:** replay-based scoring (buffer presses, score offline);
hardware-only practice (Launchpad chord pads).
**Why:** hit/miss flash requires immediate per-press evaluation; reusing
the engine's pure scoring keeps tests cheap and wire format cross-platform.
Loop-wrap detection via position delta is simpler than wiring a transport
callback through EngineController. Chord voicing through the existing
WavetableSynth shares the musicBus path with jam pads.

## D-011: Studio P4 deep-mode — local engine probe + named SSE

**Date:** 2026-07-13
**Decision:** Studio gains optional deep analysis via local GPU engine
(127.0.0.1:7777). Health probe (`/health`, 2s timeout) sets
`localEngineStatus`; available enables "Deep Analyze (GPU)" button.
NamedSSEParser handles the analyze-deep stream's named events (`event:
start`, `progress`, `complete`, `error`) separate from the data-only SSE
the standard analyze-stream uses (parseSSELine). LocalEngineClient conforms
to both LocalEngineProbing and DeepAnalyzing protocols, multipart-posts
the file with optional trim fields, streams DeepAnalysisEvent values via
AsyncThrowingStream. On complete, loads the result via standard history
endpoint so one renderer serves all paths.
**Alternatives:** poll /health continuously; parse named events inline in
existing SSE parser; add deep initiation to hosted backend.
**Why:** health probe runs once on task attach — cheaper than polling.
Named SSE parser stays small and isolated (9 tests); existing parseSSELine
unchanged. Local engine keeps GPU work off hosted backend while the deep
toggle/display reuses the same StudioModel renderer as trimmed runs.

## D-012: TransportAudioSink main-actor isolation

**Date:** 2026-07-13
**Decision:** `TransportAudioSink` protocol gains `@MainActor` attribute
to match `EngineController`'s isolation. Previously the protocol was
non-isolated, causing Swift 6 warnings and potential data races when
`TransportController` (also @MainActor) called audio sink methods.
**Alternatives:** mark conformance methods nonisolated (breaks
EngineController's graph state access); wrap all calls in Task @MainActor
(indirection, timing drift).
**Why:** protocol and conformer share the same actor; explicit isolation
removes the warning and ensures all transport-to-audio calls dispatch
correctly. The crash during song loading (objc_msgSend to deallocated
object during SwiftUI body evaluation) was caused by actor-crossing
without proper isolation.

## D-013: MIDI keyboard transport for generic note controllers

**Date:** 2026-07-13
**Decision:** `MIDIKeyboardTransport` connects to all MIDI sources EXCEPT
Launchpad Pro MK3 interfaces (owned by USBLaunchpadTransport). Note On/Off
emit ContributionEvent.midiNote for wavetable synth routing; routing mode
`.samplePads(baseNote:)` maps notes to sample-grid padDown/padUp for
LPD8/MPD-style pad boxes. Control Change surfaced via callback but not
routed to audio (future knob/fader mapping hook).
**Alternatives:** single unified transport for all MIDI (grid notes would
double-fire); per-device config (too much surface for v1).
**Why:** port of iOS MIDIKeyboardTransport; Launchpad exclusion prevents
double-firing when both transports see the same device. Note routing enum
keeps the common case (synth) simple while supporting pad boxes. 12 tests
verify discovery, note routing, CC passthrough, and Launchpad exclusion.
