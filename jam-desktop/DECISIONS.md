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

## D-014: Clean .build on Swift version mismatch

**Date:** 2026-07-15
**Decision:** when build fails with "module compiled with Swift X.X
cannot be imported by the Swift Y.Y compiler", run `rm -rf .build`
before rebuilding.
**Why:** SwiftPM caches compiled modules keyed by Swift version. Xcode
updates or toolchain switches leave stale `.swiftmodule` files that
cannot be imported by the new compiler. The fix is always a clean build;
no incremental workaround exists.

## D-015: Build workflow — swift build then build_app.sh

**Date:** 2026-07-15
**Decision:** after code changes, run both: `swift build` (~4s, compile
check) then `./build_app.sh` (~65s, full app bundle to `dist/Jamn.app`).
**Alternatives:** swift build only (no runnable app); build_app.sh only
(slow feedback on compile errors).
**Why:** swift build catches syntax/type errors fast without waiting for
full release compilation and bundle assembly. build_app.sh produces the
actual testable app with resources (ML models, samples, audio bundles)
and ad-hoc signing. Both steps required for complete verification.

## D-016: Melody guide on the wavetable synth

**Date:** 2026-09-04
**Decision:** the server-extracted song melody
(`SongBundle.melody`, additive) becomes a desktop "Melody" toolbar
toggle: `SessionController` builds a
`ToneForgeEngine.MelodySequencePlayer` at session attach with
`DesktopSynthNode` as the voice (it already matched the `MelodyVoice`
protocol; conformance is an empty extension) and advances it from the
existing 30 Hz display pump (`tick()`), `gainScale` 0.7 so the synth
sits under the stems. `stopEverything()` and paused ticks silence it;
the toggle hides when the bundle has no melody lane.
**Alternatives:** a dedicated melody clock (rejected: the display pump
already drives sequencer sync at the right cadence); a separate synth
voice (rejected: the jam-pad wavetable synth carries the song-derived
patch, which is exactly the tone the melody should have).
**Why:** playback is note-edge diffing over a monophonic sequence —
cheap enough for the pump, and the same code path the mobile client
uses, keeping the two ports in lock-step. NB: the RootView toolbar
builder is at SwiftUI's 10-element cap — Melody lives inside a
`ToolbarItemGroup` with Beat; a new top-level `ToolbarItem` breaks the
build with "extra argument in call".

## D-017: One "Launchpad" pad surface with a 16/64 grid toggle

**Date:** 2026-09-09
**Decision:** collapse the two pad surfaces into a single "Launchpad".
`LaunchpadPanelView` (the rich chop/kit surface: quantize, layers, FX,
transport, Auto/Drum Kit, radial menus, and the physical Launchpad Pro
MK3 LED mirror via `LaunchpadController`) is the base and gains a
16 ⇄ 64 pad-count toggle. The separate "Jam Pads" surface
(`JamPadGridView` + `KeyScalePickerView`, a 12-pad in-key wavetable
grid) is removed: its toolbar item, sidebar entry, `showJamPads` state,
sheet routing, and both view files are deleted.

`LaunchpadController` gained a `padCount` property (16 or 64, default
64). It is a DISPLAY + hardware-LED concern only — assignments for pads
beyond the count are retained, so toggling back to 64 restores the whole
grid untouched. On the merged grid, 16-pad mode is a compact 4×4 showing
the first 16 pads (idx 0–15, the Auto Kit's footprint); display cells map
row-major onto the SAME `LaunchpadPad` indices the controller and
hardware already use (`padIdx = dRow*cols + dCol`, decoded to
`LaunchpadPad(row: padIdx/8, col: padIdx%8)`), so triggering,
assignments, swaps, and the LED mirror are identical in both views —
only the visible pad count changes. `repaint()` darks every
out-of-range pad so a connected Launchpad mirrors exactly what is on
screen; `padDown` ignores out-of-range presses (a hardware hit on a dark
pad is a no-op); and shrinking 64 → 16 silences any voice sounding in a
now-hidden cell so a held loop can't ring on unstoppably.

**Alternatives:** keep both surfaces (rejected: redundant pad grids,
one of which — Jam Pads — was the weaker in-key-only variant with no
hardware mirror and no kit UI); make Jam Pads the base and fold the
Launchpad panel into it (rejected: the panel is the far richer surface —
folding the other direction would have re-implemented kit UI, quantize,
layers, FX, radial menus and the hardware LED mirror); re-layout the
controller's `assignments` map for 16-pad mode (rejected: `padCount`
as a pure display/LED window keeps one index space, so the hardware
`repaint()` and every pad-idx call site stay unchanged and 64↔16 is
non-destructive). Supersedes the two-surface split implied by D-016's
"jam-pad" wording — `JamInKeyModel` (the wavetable synth) stays: it
still backs the sequencer synthChord voice and the Melody guide; only
its dedicated pad *grid* is gone.
**Why:** one surface named "Launchpad" removes the "which pad grid?"
confusion, and the 16/64 toggle gives the compact 4×4 (matching the
16-pad Auto Kit and the mobile 4×4 convention) without losing the full
8×8 — both faithfully mirrored to hardware.

## D-018: "Add from another song" (Borrow) promoted to the Launchpad + Melody part

**Decision:** Promote the cross-song Borrow feature — real, tempo- and
key-matched loops from your OTHER analyzed songs — to a first-class
action ON the merged Launchpad surface (D-017), as an "Add from song"
button in the panel's second control row, and add a **Melody** part
(stem `vocals`). This mirrors what shipped on web (`kit.js`: a
"+ Add from another song" picker with Beat / Bass / Chords / Melody,
Melody = stem `vocals`).

**Where:** a new `BorrowPickerView` (sheet) hosts the part selector
(Beat=`drums` / Bass=`bass` / Chords=`other` / **Melody=`vocals`**) plus
the ranked candidate-song list with key/tempo and the key-match hint
("harmonizes" ≥0.90 · "fits" ≥0.75 from the server's `harmonic` score).
`LaunchpadPanelView` gains a `showBorrowPicker` sheet + toolbar button.
On selecting a donor the sheet calls the SAME
`SessionController.loadBorrowLoops(donorId:stem:)` the Remix sheet
already used — which fetches the borrow pack, downloads the loop WAVs,
builds loopable file-backed `Chop`s (`assetId: "borrowfile:<pad>"`),
sets `launchpad.playbackMode = .loop` and `adoptAssignments(pairs)` —
so ranking, download and the pad-mount are one shared path, not a
second implementation. The sheet dismisses on a successful load so the
grid is immediately visible.

**Melody = `vocals`:** no engine change — `RemixClient.fetchBorrowCandidates`
/ `fetchBorrowPack` take `stem` as a free string, and the backend already
serves `stem=vocals` (harmonic-matched toplines, verified live). The
existing Remix-sheet Borrow picker also gained the `Melody`→`vocals` tag,
so both entry points offer the full part list. Its melodic-hint logic
keys on `borrowStem != "drums"`, which already covers `vocals`.

**Additive:** all existing Launchpad behavior (16/64 toggle, quantize,
triggering, hardware LED mirror, loop lock, layers, radial menus) is
untouched — the picker only adds assignments through the existing
`adoptAssignments` path. Borrow stays reachable from the Remix sheet too.

**Why:** cross-song sampling is the DJ core loop; burying it in a Remix
sub-section made it undiscoverable. The Launchpad is where pads live, so
the "put another song's part on the pads" action belongs there, and web
already set the four-part (incl. Melody) shape to match.

## D-019: Optional "Session" key/BPM target on the Launchpad (Borrow conform)

**Decision:** Add an OPTIONAL, OFF-by-default **Session** target to the
Launchpad — a key + BPM the user can dial in so that ADDED (borrowed,
D-018) parts conform to a shared session key/tempo instead of the host
song. Songs stay TRUE by default: the target is off unless opted in,
and even when on it ONLY changes borrowed donor loops. The loaded
song's own audio is never repitched or retimed. Mirrors what shipped on
web (`kit.js` `buildSessionControls` / `jamn.session.target`).

**UI (`LaunchpadPanelView`):** a `SessionTargetControls` cluster sits
directly under the "Add from song" row (it only affects added parts).
A `Session: off/on` toggle; when on it reveals a Key picker (12
chromatic roots × maj/min → the backend "G minor" form) and a BPM
`TextField`, plus a one-line hint: "Added parts conform to this
key/tempo. Your song plays true." Additive — 16/64, quantize, Play
mode, loop lock, Augment, hardware LED mirror, Add-from-song are all
untouched.

**State (`SessionTarget.swift` → `SessionTargetModel`):** an
`ObservableObject` persisted in `UserDefaults` (`jamn.session.*`),
default off. On FIRST enable it prefills key + tempo from the loaded
song's own `meta.detectedKey` / `meta.tempoBpm` — but fills blanks
only, never clobbering a target the user already dialed in — so opting
in changes nothing until the user retunes. `parse()` ports the web
enharmonic fold (flats → sharp roots) and free-form key parsing
("Gm", "Bb major" → root+quality); `targetKey`/`targetBpm` return nil
when off so requests stay byte-identical to today.

**Where the target hits the wire:** `SessionController.borrowCandidates`
and `loadBorrowLoops` gained optional `targetBpm: Double? = nil,
targetKey: String? = nil`. `BorrowPickerView` reads
`session.sessionTarget.targetBpm/targetKey` and passes them into BOTH
the candidate fetch (`load()`) and the donor load (`candidateRow`).
When the target is nil (Session OFF), both methods take the SHARED
`ToneForgeEngine.RemixClient` path unchanged. When set, they take a
jam-desktop-owned target-aware fetch that appends `?target_bpm=&target_key=`
to `GET /api/song/{id}/borrow-candidates` and `GET /api/song/{id}/borrow`
(`fetchBorrowCandidatesTargeted` / `fetchBorrowPackTargeted`).

**SHARED-CLIENT GAP (for the iOS agent):** the target-aware fetches are
a jam-desktop-local fork that exists ONLY because
`RemixClient.fetchBorrowCandidates` / `fetchBorrowPack` live in
mobile-ios (`ToneForgeEngine`) and this change could not edit them. The
clean fix is to add `targetBpm: Double? = nil, targetKey: String? = nil`
to those two shared methods (appending the same two query items), after
which the desktop OFF/ON branch and the two `*Targeted` helpers collapse
back into a single shared call — and iOS gets the Session target for
free. The forked helpers deliberately reuse the shared public DTOs
(`BorrowCandidate`, `SamplePack`) and `AuthContext`, so ranking,
download and pad-mount remain one path; only the two HTTP calls fork,
and only when a target is set.

**Why:** web already shipped the Session target; desktop parity means a
user who sets "G minor / 90 BPM" on web and opens the same jam on
desktop expects added parts to conform there too. Keeping it opt-in and
scoped to borrowed parts preserves the project's core promise that a
loaded song always plays true.

---

## D-020: Borrow lays out as current-on-top / divider / donor-below on the 64 grid (web parity)

**Decision:** A Borrow load now re-lays BOTH songs' loops onto the full
8×8 (64) grid the way web just shipped: the CURRENT song's loops fill
the top rows, ONE blank divider row separates them, and the DONOR's
loops start on the next full row. Every pad shows a small source-song
label (the current song's name on `initial` pads, the donor's on
`donor` pads) on top of the existing blue(#3B82F6)=current /
amber(#F59E0B)=donor tint. Supersedes the D-018 mount, which packed
both songs' loops row-major from pad 0 with no divider and no
source-song label.

**The pure arranger.** `arrangeBorrowLayout(_ pads:cols:)` in
`JamDesktopCore/Launchpad/BorrowLayout.swift` is the bit-parallel twin
of web's `kit.js arrangeBorrowLayout`: it partitions pads by `source`,
sorts each block by backend `padIdx`, puts `initial` on the top rows, a
blank divider row (`dividerRow`), then `donor` starting on the next full
row (`donorBase = (initialRows + 1) * cols`). When a full 4-stem borrow
(32 + 32) would need `initialRows + 1 + donorRows > 8`, the divider is
dropped (`dividerRow = -1`) and the donor block packs flush after the
initial one, so no donor pad is ever pushed off the grid. Pure +
covered by `BorrowLayoutTests` (single-stem 8+8, additive/no-drop,
32+32 divider-drop, uneven, padIdx order, only-initial).

**Controller.** New `LaunchpadController.adoptBorrowAssignments(_:)`
mounts at EXPLICIT grid slots (unlike `adoptAssignments`' row-major pack
from 0) so the divider row stays empty, records a per-pad
`borrowSourceLabels` map (exposed via `sourceLabel(for:)`), and forces
`padCount = 64` so a borrow expands the surface and never leaves the
user on a 16 grid that would hide donor pads. Any single-song grid swap
(`layout()`, `adoptAssignments`) clears the borrow labels. Triggering,
quantize, loop-lock, the 16/64 toggle and the hardware LED mirror are
untouched — a borrow pad is still a file-backed loop keyed by its
backend `padIdx` (`drumKitSampleFiles[chop.idx]`), only its grid *slot*
and label changed.

**Threading the source tag + names.** The backend borrow manifest tags
each pad `source: "initial"|"donor"`, but the shared `SamplePack` DTO
(mobile-ios, not editable from jam-desktop) has no such field, so
`SessionController.fetchBorrowRaw` decodes the tag from the SAME
response bytes into a sidecar `[padIdx: BorrowPadSource]`. This one raw
fetch replaces both the old `fetchBorrowPackTargeted` and the
RemixClient OFF branch (both dropped the tag); the OFF/ON difference is
still only whether `?target_bpm=&target_key=` are appended (D-019). The
donor's display name is threaded from the picker candidate
(`loadBorrowLoops(donorName:)` ← `c.name`), falling back to the pack
name with the backend's " · kit" suffix stripped (mirrors web
`borrowDonorName`); the host name is `attachedBundle.meta.title`.

**Source label UI.** `LaunchpadPanelView.PadCell` gained a
`sourceLabelOverlay` (top-leading, capsule, 8pt) rendered only when
`launchpad.sourceLabel(for:)` is non-nil — the web `.kit-pad-source`
twin. The tint still does the primary encoding; the label makes it
readable.

**SHARED-CLIENT GAP (unchanged from D-019):** the clean fix is still to
give `RemixClient.fetchBorrowPack` the target params AND surface the
per-pad `source` on `SamplePack`/`SamplePad`, after which `fetchBorrowRaw`
and the sidecar decode collapse into the shared client and iOS/plugin can
adopt the same 64/divider/source-label layout from one place.

**Why:** parity doctrine rule 4 — a borrow must *read* the same on every
surface. Web now makes the "your song on top, borrowed song below" shape
explicit and labels each pad's origin; desktop packing both blocks into
one undivided run was a divergence even though both "worked".

## D-021 — Live-capture arrangement (Rec pads-per-section, Play replays hands-free)

Ported the web Launchpad's live-capture arrangement (kit.js) to the native
desktop. Rec through a song captures which pads are ON in each section block;
Play replays that set hands-free at block boundaries; Clear forgets it.
Per-song state persists in `ArrangementStore` (a `jamdesktop.arrangements`
UserDefaults blob keyed by analysisId, holding the web-compatible serialized
map so a capture round-trips across surfaces).

The capture/replay math is NOT desktop-original: it lives in the shared
`ToneForgeEngine.ArrangementRuntime` (collapseSections / blockIndexAtTime /
diff / parse+serialize + the tick state machine), the same file iOS will use.
`ArrangementController` (JamDesktopCore) only wires that engine to
`LaunchpadController.activePads` (read), `TransportController` (time/isPlaying),
and pad arm/release. Replay arms via new `LaunchpadController.replayArm/
replayRelease` — a phase-locked loop launch that ignores the user's Tap/Loop
mode (the web twin is kit.js `armPadForReplay`), so a hands-free replay always
latches loops regardless of the current pad mode. Driven off the existing
`SessionController.tick()` 30 Hz pump; passive when neither Rec nor Play armed.

**Why:** parity doctrine rule 3/4 — a feature that shipped web-only is a
divergence. The shared engine (with 18 XCTest assertions mirroring
kit.test.mjs) is the forcing function that keeps desktop, iOS and web capture
semantics bit-identical instead of three drifting reimplementations.

**Alternatives:** a desktop-original tick loop (rejected — would re-derive the
same record/replay logic the web already has and could drift); temporarily
flipping `LaunchpadController.playbackMode` during replay (rejected — mutates
observable UI state and blips the mode toggle; a dedicated replayArm path is
deterministic).

## D-022 — Latched loops lock to one shared cycle (unison; web c726ba58 port)

**Bug:** with loops latched on the Launchpad, each pad looped at its OWN
section length — a 2-bar loop against a 3-bar loop ran on its own period and
the playheads drifted apart, visibly and musically out of phase.

**Fix (port of web commit `c726ba58`, `backend/static/padengine.js`):** every
loop pad with a REAL analyzer loop region now bakes its loop buffer to the
SHARED cycle — `LaunchpadController.loopLengthSeconds`, already defined as the
longest analyzer region on the grid. A shorter section repeats INSIDE that
cycle so all region pads share one period and restart together. The longest
pad already fills the cycle (no tiling); a body that doesn't divide the cycle
takes one wrap seam per cycle — the accepted trade for guaranteed phase-lock.

**Where:**
- `ToneForgeEngine.SeamlessLoop.tileToLength(_:targetFrames:)` — new pure
  helper, the Swift twin of padengine's `tileChannels()`: repeats an
  already-seam-baked body via `i % srcFrames` (continuous at every body wrap),
  returns the input unchanged for `target <= body`.
- `ChopPlayer.loopBuffer` — after `exactCrossfaded`, tiles the seam-baked body
  up to `round(cycleSec * 48k)` when a cycle is supplied and exceeds the body.
  `voice.loopFrames` reads the tiled length, so the on-pad playhead ring tracks
  the shared period (requirement 4).
- `ChopPlayer.trigger(_:…)` gains `cycleSeconds`; it tiles ONLY when the chop
  carries an analyzer region (`chop.loopScore != nil` — the exact set
  `loopLengthSeconds` is derived from). Threaded from
  `SessionController.onTrigger` as `launchpad.loopLengthSeconds`.

**Gated on a real region (web parity):** region-less loop pads — constant-tempo
bar-snapped chops (`loopScore == nil`) and borrow whole-buffer loops
(`trigger(file:…)`) — have no shared musical cycle, so they keep their own
length. `cycleSeconds` defaults to 0 (no tiling) on every path, so nothing
else changes. Tests: `SeamlessLoopExactLengthTests` +3 (exact multiple → clean
repeat; non-multiple → right length, one wrap seam; degenerate → unchanged).

**Alternatives:** recomputing a cycle inside ChopPlayer (rejected — would drift
from the controller's quantize grid, which already uses `loopLengthSeconds` for
`nextLoopBoundary`); tiling region-less pads to the same cycle (rejected — for
them `loopLengthSeconds` falls back to an arbitrary 8 s lattice, not their
period, matching the web gate).

## D-023 — Four desktop-Perform Launchpad parity closes vs web kit.js/remix.js

**Gap:** the native Launchpad had drifted from the web kit Launchpad on four
user-facing points. Each is now closed against its web anchor; all reuse the
existing SessionController/LaunchpadController methods (no transform or audio
reimplemented).

**1 — Flip + Remix reachable from the Launchpad** (web embeds the Remix bar
inline above the pads, `remix.js:18-21`). Added a **Flip** button next to
Auto/Drum Kit that calls `session.loadAutoKit(kind: "flip")` — the same path
`RemixSheetView`'s Flip row uses, staging the kind=flip kit onto the pads
(silent until tapped; desktop deliberately doesn't auto-start the flip beat,
see the flip branch in `SessionController.loadAutoKit`). Added a **✦ Remix**
button that opens the existing
`RemixSheetView` via a local `@State showRemix` + `.sheet` — the same idiom as
`showBorrowPicker`, so it's not a second presentation path.

**2 — Per-pad radial "Stop pad" + "Solo"** (`kit.js:3559` / `:3572`). Added
`.stopPad` / `.solo` to `PadRadialAction` and to the assigned/pack/sequence
rings. Stop pad → `LaunchpadController.replayRelease(padIdx)` (the single-pad
release: drops it from `activePads`, restores its light, fires `onRelease`).
Solo → `replayRelease` on every OTHER active pad (snapshot first — it mutates
`activePads`); like web it's not a latched state, a re-solo just has nothing
left to stop. Both are GATED exactly as the web ring gates them: Stop pad is
live only while THIS pad sounds, Solo only while ANOTHER pad sounds —
`PadRadialMenuState` gained `isSounding`/`anyOtherActive` and an `isEnabled()`
that dims the wedge and makes a disabled click a no-op (kit.js `disabled:`
flags).

**3 — Unified "Kill All"** (`kit.js:1168` killAll). The Launchpad's global
stop stopped pads + sequencer but left the SONG rolling (song-stop lived only
on `TransportBar`). Rewired the button to `session.stopEverything()` — which
already pauses the transport, stops the sequencer, and kills every pad/voice
(restoring taken-over stems) — and relabelled it **Kill All**. Its live-state
is now `killAllActive` (any pad, the beat, OR the transport playing), matching
web's always-actionable Kill All. `TransportBar`'s own stop is untouched.

**4 — Quantize Off / Beat / Bar** (`kit.js:1037`). The picker offered
QuantizeMode's full raw ladder (off · 1/8 · 1/4 · 1/2 · 1 bar · phrase); it now
offers only **Off / Beat / Bar**, with Beat → `.quarter` and Bar → `.bar`, via
a static `quantizeOptions` list. QuantizeMode keeps all its cases (used
elsewhere) — only what the Launchpad OFFERS changed. A persisted value that
lands off the three (1/8, 1/2, phrase) highlights nothing, mirroring web's
`highlightQuantize` when the value has no button.

**Where:** `Launchpad/PadRadialMenu.swift` (enum cases + label/image/color +
`isSounding`/`anyOtherActive`/`isEnabled` gating + dimmed render/click),
`Launchpad/LaunchpadPanelView.swift` (Flip/Remix buttons, `showRemix` sheet,
Kill All rewire + `killAllActive`, `quantizeOptions`, radial state gating +
`.stopPad`/`.solo` handlers). No engine change — `PadRadialMenuState`'s new
fields default, so other call sites and the preview compile unchanged.

**Alternatives:** threading a `showRemix` callback down from RootView/PerformView
(rejected — RemixSheetView only needs the session, so a local sheet is simpler
and matches the panel's other local sheets); a new SessionController "killAll"
(rejected — `stopEverything()` already IS web's killAll semantics); deleting
QuantizeMode's unused cases (rejected — they're model-level and read elsewhere).

## D-024 — Launchpad controls moved to a left rail so the pad grid fills the space

Restructured `LaunchpadPanelView` from a vertical stack (header + two wide
control rows + Session + cycle/arrangement, then the square `aspectRatio(1,.fit)`
grid) into an `HStack`: a fixed **300pt left rail** holds every control stacked
into labeled groups (View, Size, Mode, Quantize, Chops, Kits, Remix, Session
target, Jam), and the right column gives the pad grid all remaining width AND
height — so the square grid now grows instead of shrinking between dead side
margins. The two wide `controls`/`controlsRow2` HStacks were decomposed into
per-control view-builders (`killAllButton`, `gridLayersToggle`, `padCountPicker`,
`playbackModePicker`, `lockButton`, `augmentButton`, `quantizePicker`,
`chopsGroup`, `kitsGroup`, `remixGroup`, `jamGroup`) assembled by a new `rail`
via a `railSection(_:content:)` caption+group helper. Layout only — every
action, binding, disabled-state and sheet is unchanged; Remix / "+ Add from
another song" became full-width rail buttons (borrow keeps its accent border),
and the Quantize / Off-Beat-Bar picker is now segmented in the rail. The rail
scrolls if a short window can't show all groups; `.frame(maxWidth: embedded ?
.infinity : 800)` is unchanged (300 rail + ~470 grid fits the 800 floating case).

**Why:** the forced-square grid was tiny with huge empty side margins because the
stacked control rows ate the height. A left rail frees the whole right column for
the grid — the pads get much bigger — while keeping all controls one glance away.
## D-025 — Borrow pad toggle re-arranges (16 = best-of-both), never drops a song

**Failure mode.** A borrow ("Add from another song") lays BOTH songs onto the
64 grid: host on the top rows, a blank divider, donor below. Switching the
Launchpad to 16 pads only clipped the visible window to idx < 16 — the donor
lived below the fold, so the whole borrowed song vanished from the compact grid.

**Fix.** `arrangeBorrowLayout` (`Launchpad/BorrowLayout.swift`) is now
CAPACITY-aware — it takes `cols`×`rows`. At capacity ≥ 64 the full/divider path
is unchanged. Below 64 (the 4×4 16) it does BEST-OF-BOTH: the top
`capacity/2` = 8 pads of EACH song by score (performanceScore ?? loopScore ?? 0,
padIdx tie-break), initial block at slots 0..7, donor at 8..15, section order
(padIdx) restored within each block, no divider. One song short of its half →
the other fills the remainder by score. `BorrowPadRef` gained a `score` field.

**Toggle re-arranges, not clips.** `LaunchpadController` now RETAINS the full
borrow mount set (`borrowMounts`) and re-lays it whenever `padCount` changes
(`applyBorrowLayout(at:)` in the `didSet`), so 16 is a best-of-both re-arrange
and 64 restores the full layout from the retained set — not the clipped 16.
`BorrowMount` dropped its pre-baked `slot` and gained `source` + a `score`
(from the chop); the controller derives slots itself. `adoptBorrowAssignments`
opens on 64 and stores the mounts; `setChops`/`adoptAssignments` clear them.
`SessionController.loadBorrowLoops` builds the full mount list (source + score)
and hands it over — no pre-layout. Source-song labels + blue/amber tint survive
both directions.

**Where:** `Launchpad/BorrowLayout.swift` (`arrangeBorrowLayout` +
`BorrowPadRef.score`), `Launchpad/LaunchpadController.swift`
(`BorrowMount`, `borrowMounts`, `applyBorrowLayout(at:)`, `padCount.didSet`,
`adoptBorrowAssignments`), `SessionController.swift#loadBorrowLoops`. Pinned by
`Tests/JamDesktopCoreTests/BorrowLayoutTests.swift` (compact best-of-both +
underflow + toggle-to-16-then-back re-arrange). Web/iOS twins land in the same
change (`kit.js#arrangeBorrowLayout`/`layoutBorrowPads`,
`SampleBank#arrangeBorrowLayout` + `AppState.relayoutActiveBorrow`).

**Alternatives:** clipping to the best 16 by score regardless of source
(rejected — a strong host could erase the donor, the exact bug); re-fetching on
every toggle (rejected — the retained mount set re-lays with zero I/O).

## D-026: Launchpad regression suite — extract inline decisions into pure, pinned functions

**Date:** 2026-09-14
**Decision:** The recurring Launchpad bugs (all-pads-one-color borrow, borrow
decode dropping stem/source, pad-won't-stop, blank/mis-routed pads) all lived in
inline SwiftUI/closure code no test could reach. Extract each decision into a
pure function on the testable side and pin it, so the class of bug fails CI
instead of the user's eyes.

- **Borrow color-by-stem** → `LaunchpadController.borrowCategory(forStem:)`
  (JamDesktopCore). `LaunchpadPanelView.fillColor` calls it instead of an inline
  `switch`. Guards "all pads red" (a hard-coded stem once painted every borrow
  pad drums-red).
- **Borrow source/stem decode** → `decodeBorrowSources(from:Data)`
  (`Launchpad/BorrowSourcesDecode.swift`, JamDesktopCore). Moved out of the
  non-testable `SessionController.fetchBorrowRaw` (JamDesktop executable target);
  the wire structs moved with it. Guards the hard-coded `stem="drums"` and the
  dropped `source` tag.
- **Integration smoke** → `LaunchpadBorrowSmokeTests` runs the real controller
  through a borrow adoption headlessly: grid fills, pads carry distinct
  stem-colors + source labels, tap toggles active, re-tap stops, Stop clears all
  and fires the hard voice-stop.

**Where:** `Sources/JamDesktopCore/Launchpad/LaunchpadController.swift`
(`borrowCategory`), `Sources/JamDesktopCore/Launchpad/BorrowSourcesDecode.swift`,
`Sources/JamDesktop/Launchpad/LaunchpadPanelView.swift` (fillColor call site +
`accessibilityIdentifier`s: `launchpad-grid`, `pad-<idx>`, `pad-radial-menu`),
`Sources/JamDesktop/Perform/TransportBar.swift` (`transport-play`),
`Sources/JamDesktop/SessionController.swift` (calls `decodeBorrowSources`).
Pinned by `Tests/JamDesktopCoreTests/{LaunchpadControllerTests (borrow-category),
BorrowSourcesDecodeTests, LaunchpadBorrowSmokeTests}`. Runs via
`swift test`; CI gate added in `.github/workflows/native-tests.yml`
(jam-desktop job), which skips the PRE-EXISTING unrelated failure
`AppModelTests.testViewInventoryMatchesWebApp` (stale web-parity view inventory,
tracked separately).

**Alternatives:** a desktop XCUITest for the end-to-end path (rejected for now —
jam-desktop is a pure SwiftPM package with no `.xcodeproj`/app target, so
XCUITest can't launch it and can't run headless on CI; the accessibility ids are
added so a future Xcode app+UITest target can adopt it). The headless
controller-level smoke test reaches the same integration glue CI-safely.

## D-027: No XCUITest for jam-desktop yet — SwiftPM package has no app/UI-test target

**Date:** 2026-09-14
**Decision:** Do not stand up an XCUITest target for the desktop Launchpad now.
`jam-desktop` builds an executable via SwiftPM and hand-assembles `Jamn.app` in
`build_app.sh`; there is no `.xcodeproj`. XCUITest requires an Xcode app target
plus a UI-test bundle and a GUI/accessibility session, none of which a SwiftPM
package provides, and macOS CI runners can't drive app UI automation headlessly.
Standing that up means a whole XcodeGen project that would fork the working
`swift build`/`build_app.sh` flow.
**Instead:** key views carry stable `accessibilityIdentifier`s (`launchpad-grid`,
`pad-<idx>`, `pad-radial-menu`, `transport-play`) so the surface is UI-test-ready,
and the end-to-end behavior is covered headlessly by `LaunchpadBorrowSmokeTests`
(D-026). Revisit if/when a jam-desktop Xcode project is introduced.

## D-028: Pad phase-lock port from web — bar-grid launches, phase-locked joins, two-anchor divergence

**Date:** 2026-09-15
**Decision:** Port the web pad-timing fixes (padengine.js) to the desktop
engine, mirroring semantics exactly:

- **Loops quantize to the BAR grid, never sub-bar.** A multi-bar loop snapped
  to a beat starts mid-bar — out of phase with the song and every other loop
  even though each is "on a beat". `padDown` upgrades a sub-bar Quantize
  (off/1-8/1-4/1-2) to `.bar` for loop launches; one-shots keep the user's grid.
- **Lock launches wait ≤ 1 bar.** `nextLoopBoundary` and the free-run grid snap
  to `lockGridUnitSeconds` (one bar; full cycle only without tempo) instead of
  whole multiples of the ~6–8 s loop cycle, which read as "the pad doesn't play".
- **Phase-locked join.** A loop tapped mid-jam starts `phase` seconds INTO its
  baked body, `phase = (boundary − anchor) mod bodySec`, measured from the
  PRE-shift quantize boundary — measuring the per-pad onset shift back in as a
  buffer offset cancels the launch compensation and pads flam by their shift
  deltas. Threaded as `onTrigger`'s `lockPhaseSeconds`; ChopPlayer folds it and
  schedules the body tail once, then the whole body with `.loops`
  (AVAudioPlayerNode can't start a looping buffer mid-body).
- **Phase-aware playhead.** `loopProgress` adds the join phase — without it the
  drawn playheads desync over audio that IS locked (the exact readout bug that
  misled the web debugging).
- **Round-the-length frame counts.** Region frames = `round((end−start)·sr)`
  (`ChopPlayer.regionFrameCount`, shared by schedule + prewarm), never
  trunc-per-edge, whose ±1-frame mismatch against the tiled cycle walked
  phase-locked loops apart over minutes.

**Intentional divergence from web:** web keeps ONE `_lockAnchor` because its
AudioContext clock always runs; desktop song time freezes when the transport
stops, so there is no clock shared between rolling and free-run. Desktop keeps
two era anchors (`lockAnchorSongSeconds` for rolling, `freerunAnchorHostSeconds`
for stopped) and a free-run re-anchor clears both — cross-mode joins start a
fresh era instead of phase-locking to a lattice from a different clock domain.
Also `loopLockEnabled == false` skips the phase join entirely (web has no lock
toggle; the desktop toggle is documented as the phase-lock switch).

**Where:** `Sources/JamDesktopCore/Launchpad/LaunchpadController.swift`
(`lockGridUnitSeconds`, `nextLoopBoundary`, `padDown`, `replayArm`,
`onTrigger` signature), `Sources/JamDesktop/SessionController.swift` (rate
conversion of the lattice offset; Link launches zero it — Link owns that grid),
`Sources/JamDesktopAudio/ChopPlayer.swift` (`phaseLockFrames`,
`loopProgressValue`, `regionFrameCount`, `tailSegment`, phase-join scheduling).
Pinned by `LaunchpadControllerTests` (bar-grid quantize, single-bar free-run
wait, boundary-anchored phase, re-anchor resets, sub-bar upgrade) and
`ChopPlayerPhaseLockTests` (fold, phase-aware progress, frame rounding).

## D-029: Pad-timing convergence onto the web/iOS contract + Launchpad Edit Mode

**Date:** 2026-09-17
**Supersedes:** the D-028 clauses "`loopLockEnabled == false` skips the phase
join entirely" and "`nextLoopBoundary` snaps to `lockGridUnitSeconds`" (the
rest of D-028 stands, including the two-era anchor divergence).

iOS closed seven verified divergences against the web authority (mobile
commits 77231913, 38dc6e5e, 679a874e); this port converges desktop onto the
same contract. What was actually divergent here vs already correct:

- **Rolling lock boundaries now use the REAL bar grid.** `padDown`/`replayArm`
  route through `rollingLoopBoundary` → the shared `Quantizer.nextQuantized`
  (`.bar`): snap to the analyzer's downbeats, extrapolate FORWARD at tempo
  past the last one (late-song presses stayed on-grid on web/iOS, fired into
  a synthetic lattice here) and BACKWARD from grid[0] on long intros (web
  `_transportLaunchTime`, padengine.js:1014/:1023/:1047). The old
  k·bar-from-song-0 lattice was exactly the constant-tempo drift bug the web
  fixed — desktop never armed pads onto the song's real bars. Full-cycle
  lattice remains only with no bar data at all (web `_lockLaunchTime`
  fallback); its grace aligned 0.12 → 0.08 s (web LOOP_LOCK_GRACE_SEC).
- **EVERY looping trigger phase-joins its era** (web anchors/joins
  unquantized launches too — padengine.js:1166/:1269-1273). Lock off now
  means "no lattice wait" (the user's Quantize control governs, sub-bar
  floored to `.bar` via `loopQuantize`, `.off` = start NOW), never "no
  join". The lock toggle is the quantize switch for loops, not the
  phase-lock switch.
- **Silence = no sounding VOICE, not no latched pad.** The free-run re-anchor
  consults `isAnyVoiceSounding` (SessionController → ChopPlayer
  `soundingVoiceCount`) in addition to `activePads`; a still-ringing one-shot
  holds the lattice (web `_voices.size`, padengine.js:1154). ChopPlayer
  one-shots now free their slot on natural end via a gen-guarded
  `.dataPlayedBack` completion, so the count reflects what actually sounds.
- **Sample-accurate starts gated on live render clocks.** `play(at: hostTime)`
  is silently ignored by a player whose own `lastRenderTime` is invalid
  (iOS 679a874e) — and desktop attaches voices lazily, so a stack-another-pad
  quantized press routinely landed on a never-rendered node and armed
  forever. `scheduleStart` gates on engine + player render validity, falls
  back to a deferred dispatch `play()`, and parks a `pendingPlay` work item
  on BOTH paths as armed marker + cancel token (release kills a
  not-yet-started voice).
- **20 ms release fade** (`releaseFadeSec`, web release :1296 / iOS
  `releaseSlot`): `release`/`stopAll` free the slot immediately (accounting
  never waits out the fade) and ramp the voice mixer to zero before
  `stop()` — the latch toggle-off used to hard-cut mid-body. `reattach`
  keeps the hard stop (device-flap window).
- **Already correct from D-022/D-028** (verified, no change): shared-cycle
  tiling with the analyzer-region gate (`loopScore != nil` = web
  `hasRegion`), the CYCLE as the join divisor and playhead period, pre-shift
  boundary anchoring, phase-aware `loopProgress`, round-the-length frame
  counts, immediate latch-off release routing.

**Launchpad Edit Mode** (iOS 602a9043 parity): pencil chip in the panel
header (`launchpadEditChip`, persisted `jam.launchpad.editMode`, default
OFF). Edit OFF = pure performance pads — right-click radial, hover "⋯",
move mode and clear-all are disarmed (`LaunchpadController
.editAffordanceEnabled`, pinned in tests); empty-pad Add Sound stays in both
modes. Desktop had NO hold timer (right-click is its edit gesture), so
unlike iOS nothing ever cut a held voice — this is the semantic half of the
parity contract, not a latency fix.

**Where:** `LaunchpadController.swift` (`rollingLoopBoundary`,
`loopQuantize`, `isAnyVoiceSounding`/`surfaceIsSilent`, `padDown`,
`replayArm`, `editAffordanceEnabled`), `ChopPlayer.swift` (`scheduleStart`,
`fadeOutAndStop`, `.dataPlayedBack` one-shot completion, `pendingPlay`/
`fadeTask` voice state), `SessionController.swift` (sounding provider),
`LaunchpadPanelView.swift` (`launchpadEditChip`, PadCell `editing` gate).
Pinned by `LaunchpadControllerTests` (real-grid snap, forward/backward
extrapolation, lock-off instant + join, sounding-voice anchor hold, edit
gate) and `ChopPlayerFileReleaseTests` (immediate release accounting,
armed-start cancel, one-shot natural-end clear).

## D-030: Projects v1 — per-song workspaces on the shared snapshot contract

**Date:** 2026-09-18
**Decision:** Port iOS Projects (mobile c887452d) to desktop against the
SAME wire contract (`ToneForgeEngine/Projects/ProjectSnapshot.swift`) —
no desktop-only schema. A project file written on either platform decodes
on the other. The port's desktop-specific choices:

- **Sample-axis translation, everything else preserved.** The snapshot's
  `padAssignments` is mode → PadIndex(11..88, row 1 = bottom) → PadSlot;
  desktop has one surface with row-major 0..63 top-left indexing.
  `ProjectStateBridge` owns the bijection (the exact formula iOS lays
  pack pads with: `PadIndex.at(row: 8 - d/8, col: d%8 + 1)`) and
  snapshots/restores ONLY the "sample" axis; other modes' entries,
  `hiddenPads`, and the transform chain of unchanged sample slots
  round-trip verbatim through the last-restored snapshot.
- **Per-pad FX exist on desktop now.** New `PadFXStore`
  ("packId#padIdx", the iOS key form; JSON under Application
  Support/Jamn/padFX.json) consulted at trigger time via
  `SessionController.effectsForPad` — chop, borrow/drum FILE, MIDI-map
  and replay paths all pass it into ChopPlayer's existing
  `applyEffects` chain, which had been receiving `.neutral` forever.
  Keyed on the mounted pack's id (`activeGridPackId`) + `chop.idx`,
  gated on `chop.assetId != nil` so a stale pack id can't bleed FX
  onto a preset/fetched chop grid. No desktop FX editor yet — values
  arrive via restored workspaces (typically authored on iOS).
- **Section gates are LIVE, not just preserved.** `LaunchpadController.
  sectionGate` + `SectionResolver.isAllowed` at `padDown`/`replayArm`
  (playhead-position semantics, iOS SampleScheduler parity). Latch
  toggle-OFF is deliberately ungated — stopping must always work.
- **Borrows are content-addressed.** `loadBorrowLoops` retains a
  `BorrowContext` (donor + the mounted pads' backend
  `sourceLoopStartSec`/`sourceLoopEndSec`/`assetId`); capture builds
  `BorrowRef`s from it (host/span-less pads dropped — response padIdx
  is NEVER identity), restore re-requests the donor after the auto-kit
  lands and verifies by `BorrowRef.matches`; a drifted kit or missing
  donor surfaces a "Needs <donor>" banner, then the workspace's saved
  trigger mode is re-asserted over the mount's forced Latch.
- **Global-store swap semantics.** Desktop stores stay global (iOS's
  glue-over-stores approach): restore whole-value swaps assignment
  table / FX map / the song's chop edits + arrangement; reset-to-song
  empties them, deletes the working file and reloads the auto-kit.
  chopEdits is the one field desktop is CANONICAL for (per-analysisId
  ChopEditStore; iOS captures nil today).
- **Auto-save without ObservableObject.** The @Observable stores got
  explicit `onChanged` hooks (fired in `persist()`) + the controller's
  `onSurfaceSettingChanged`; the coordinator debounces ~2 s into
  `Application Support/Jamn/projects/working/{analysisId}.json`.
  Restore runs inside `SessionController.attach` BEFORE
  `arrangement.loadSong`/`applyChopEdits` read their stores.
- **v2 blank-canvas (nil baseSongId, landed concurrently in the shared
  contract) is load-blocked on desktop** with a notice — there is no
  desktop canvas surface yet; files still list and round-trip.

**Where:** `Sources/JamDesktopCore/Projects/` (ProjectStateBridge,
ProjectStore), `Sources/JamDesktopCore/Samples/PadFXStore.swift`,
`Sources/JamDesktop/Projects/ProjectCoordinator.swift`,
`Sources/JamDesktop/Library/ProjectsListView.swift`, SessionController
attach/kit/borrow seams. Pinned by `ProjectStateBridgeTests`
(translation bijection incl. the iOS-formula cross-check, round-trip,
preservation, tri-state gates, borrow content-match), `ProjectStoreTests`,
`PadFXStoreTests`, `LaunchpadSectionGateTests`.

## D-031: Hardware Launchpad session fixes — press/release latency, honest underpower heuristic, LED/screen color parity

**Date:** 2026-09-18
**Decision:** Four fixes from the first extended hardware (Launchpad Pro MK3)
session, One-Shot finger drumming:

- **A successful connect is NOT a flap.** The underpower heuristic counted
  every successful connect toward its 3-in-10-s threshold; CoreMIDI fires a
  BURST of setup-change notifications on one physical plug-in (device +
  entity + endpoint appearances), so a stable cable could bank ≥3 "flaps"
  and false-fire the "may be underpowered" banner. Flaps are now only
  torn-down links (unplug, endpoint re-enumeration) and failed connects;
  endpoint identity is the CoreMIDI ref alone (display names resolve late
  during enumeration — a property change is the same device, no reconnect);
  and 30 s of trouble-free connection self-clears the banner
  (`reviewStability`), so a one-time transient can't pin it for a session.
- **Press-time stamps reach the controller.** `StampedPadTransport`
  (JamDesktopCore-only widening of the shared seam — the ToneForgeEngine
  protocol is untouched): pad callbacks carry the clocks captured ON the
  MIDI receive thread, and `padDown` quantizes/phase-joins from the press
  instant instead of the main-queue arrival (10–50 ms late under grid
  repaint load). Instant gates (One-Shot/Follow) still fire ASAP — elapsed
  hop time is not recoverable without cutting into the attack, so it is
  not "compensated" by skipping buffer head. `JAM_PAD_LATENCY_LOG=1`
  prints press→trigger / padUp→release deltas for on-device measurement.
- **Loop bakes are cached and prewarmed.** Every mode loops the voice
  (D-029), so every press ran `loopBuffer` — onset-scan file read +
  exact-crossfade bake + cycle tile — and the FIRST press of each pad also
  missed `regionCache` (the bake reads a shifted, extended region whose key
  prewarm never warmed): ~200 ms of work in the touch path (measured, 8 s
  region). Baked bodies now cache keyed on pre-shift inputs
  (`loopCache`), and `prewarm` bakes the exact loop variant the live path
  requests (same crossfade floor — `ChopPlayer.defaultPadCrossfadeMs` —
  bar snap and tile gate). Measured: first press after prewarm ~2 ms,
  repeat presses ~1–12 ms (residual = AVAudioPlayerNode stop/schedule/play
  control cost, not decode).
- **Release fade runs off the main actor.** The 20 ms release ramp awaited
  the main actor between its 8 steps; a busy UI stretched it to 100+ ms of
  audible tail — "release sticks". The ramp is a detached task now (mixer
  volume + player stop are thread-safe; only slot bookkeeping hops back).
- **One display-color source.** Hardware LEDs painted raw backend
  colorHints while the screen painted category colors (and borrow grids lit
  as flat blue/amber source tints on hardware — the LED half of the
  22bd58b6 screen fix). `LaunchpadController.displayColorHint(for:at:)`
  (borrow-category → Riley-category → raw hint) now feeds BOTH the panel
  tiles and every hardware paint. Pulse quantization additionally gained
  the web-cited palette anchors (launchpad.js FAMILY_PULSE_PALETTE: white 3,
  cyan-blue 41, magenta 53) so pink/purple/slate pads no longer pulse
  red/blue — engine change shared with iOS in the same commit.

**Where:** `Sources/JamDesktopCore/Launchpad/USBLaunchpadTransport.swift`
(flap semantics, ref identity, `reviewStability`, stamped callbacks),
`LaunchpadController.swift` (`padDown(pressSongSeconds:pressHostTime:)`,
`displayColorHint`, latency log), `Sources/JamDesktopAudio/ChopPlayer.swift`
(`loopCache`, prewarm loop variant, detached release fade),
`Sources/JamDesktop/SessionController.swift` (prewarm params, shared
crossfade floor), `Sources/JamDesktop/Launchpad/LaunchpadPanelView.swift`
(fill/glow via displayColorHint), and
`mobile-ios/Sources/ToneForgeEngine/Launchpad/LaunchpadProMK3Protocol.swift`
(palette anchors). Pinned by `USBLaunchpadTransportTests` (burst, rename,
failed-connect, stable-clear, stamped clocks, palette hues),
`LaunchpadControllerTests` (stamped seam + press-stamp fire time, display
color through the press cycle, borrow LED category) and
`ChopPlayerLoopBakeCacheTests` (bake-once, prewarm-hit).

## D-032: Parked-voice rotation — the same-pad burst fix

**Date:** 2026-09-18
**Context:** With D-031 in place, single hardware taps measured healthy
(press→trigger 2–11 ms) but rapid same-pad hammering exploded:
press→trigger 138/314/516 ms, padUp→release up to 433 ms
(JAM_PAD_LATENCY_LOG on a Launchpad Pro MK3).

**Root cause (measured, not guessed):** per-step laps in `schedule()`
showed every sub-step sub-millisecond EXCEPT `AVAudioPlayerNode.play()`
— a flat ~10.7 ms per press, one full render quantum (512f @ 48 kHz):
play() blocks its caller until the render thread picks the start up. At
finger-drumming rates that alone eats the inter-press budget; with the
UI repaint each press also provokes, the main queue backlogs and
latencies climb monotonically — exactly the observed burst numbers.
Contributing: the old claim policy stole the SAME slot per key
(stop+play per press) and its idle scan examined only the FIRST
nil-key slot, so a still-fading voice there grew the pool on every
rapid press.

**Decision — never pay a node control call on the press path:**

- **Parked voices.** `warmUpPool()` (session attach + post-reattach via
  `onGraphReattached`) builds, wires and `play()`s the whole 16-voice
  pool with empty queues. A press on a parked node just
  `scheduleBuffer()`s — the buffer starts at the next render cycle, no
  play(). The park calls run on a detached task (16 × ~10.7 ms would
  hitch attach), flags land back gen-guarded.
- **Retrigger = rotate (web padengine parity).** A same-key trigger
  claims a FRESH parked voice; the superseded voice release-fades off
  the press path (fade begun AFTER the new takeover so the same-stem
  ref-count never crosses 0 — no duck/restore blip). The fade terminal
  stops AND re-parks the node, so a sustained jam recycles the pool.
- **Quantized launches still stop-then-play(at:).** A parked node
  cannot defer a buffer, so `schedule()` un-parks (stops) the node
  when the effective delay is quantized — computed BEFORE any buffer
  is queued (the bake now runs before the voice is touched).
- **VoiceGate epoch lock.** Press ↔ detached-fade coordination: the
  press advances the epoch atomically when reusing a slot; the fade's
  ramp steps and terminal stop run only while their epoch is current.
  The gate is never held through play() (that wait was measurable as
  20–46 ms press spikes at saturation); the residual sub-µs hole can
  at worst start one quantized loop early under full pool saturation.
- **loopProgress baseline.** A parked node's sample clock runs from its
  park play(), so voices record `startSampleTime` at launch and the
  playhead ring measures from there.

**Measured (headless, 8 s loop region, warm caches):** realistic
13 Hz hammer: 0.1–1.5 ms per press (was a flat ~11.6 ms each + main
pileup); single taps 0.1 ms; zero-gap saturation (~500 Hz, humanly
unreachable) still shows occasional 10–50 ms steal-path spikes —
accepted backpressure.

**Where:** `Sources/JamDesktopAudio/ChopPlayer.swift` (Voice.parked/
startSampleTime/gate, VoiceGate, schedule() restructure, claimVoice,
warmUpPool, fade terminal, immediatePlayCount seam),
`Sources/JamDesktop/SessionController.swift` (warm-up call sites).
Pinned by `ChopPlayerBurstTests`: zero press-path play() calls across a
12-cycle zero-gap hammer with bounded cost, retrigger keys exactly one
voice, fade terminal re-parks drained voices.

## D-033: Receive-thread fast release — the pad-up half of the burst fix

**Date:** 2026-09-18
**Context:** After D-032, hardware press→trigger measured flat
0.33–0.47 ms across a sustained same-pad hammer, but padUp→release
still spiked 54–145 ms interleaved with healthy 5–30 ms events.

**Root cause:** the main-path release HANDLING costs ~0.1 ms (benched)
— the spikes are the MIDI→main hop itself. Each press mutates
activePads → SwiftUI invalidates the full-window pad grid → the commit
runs on the main runloop between the press and its release; a pad-up
dispatched during that commit waits for it. The asymmetry is causal,
not lucky: presses always arrive AFTER the previous cycle's commit has
drained (the user's finger gap), while releases arrive 30–80 ms after
their own press — squarely inside the commit the press provoked. So
presses measured sub-millisecond while releases queued.

**Decision — begin the audible fade on the MIDI receive thread:**

- `USBLaunchpadTransport.setFastPadUpTap`: a lock-boxed `@Sendable`
  tap invoked ON the receive thread the instant a release message
  decodes (Note On vel-0 / Note Off, same mapping as `deliver()`),
  BEFORE the main hop. Audio-only; the stamped padUp still follows on
  main with all bookkeeping/LED/recording work.
- `ChopPlayer.padReleased(tag:)` (nonisolated): pad-tag → voice
  registry (`fastReleaseRefs`, unfair-locked; written at trigger time
  via the new `padTag:` parameter threaded through both `trigger`
  overloads). Begins the 20 ms mixer ramp on a detached task — ~µs on
  the calling thread. NO terminal stop/re-park: that stays with the
  authoritative main-path release, which always follows in the event
  stream.
- **Composition by epoch (VoiceGate):** every ramp step is
  epoch-guarded. The main release advances the voice's epoch — the
  fast ramp dies and the main ramp continues from the already-lowered
  volume (audio only ever fades downward). A re-press advances the
  epoch and re-registers the tag, so a stale fast ramp can never drag
  the new voice down. A fast release racing ahead of its press's main
  hop finds a stale epoch and no-ops — never wrong audio, at worst no
  fast benefit for that one event.
- Registry cleared on load/unload/reattach; only hardware pads use it
  (on-screen taps are main-originated — no hop to skip).

**Where:** `Sources/JamDesktopAudio/ChopPlayer.swift` (registry,
padReleased, padTag plumbing), `Sources/JamDesktopCore/Launchpad/
USBLaunchpadTransport.swift` (fast tap), minimal SessionController
wiring (tap install + padTag at the three trigger sites). Pinned by
`ChopPlayerBurstTests` (fade completes while the main actor is
deliberately blocked; epoch composition across release/retrigger;
sub-10 ms call bound under hammer) and `USBLaunchpadTransportTests`
(tap fires pre-hop, releases only, grid notes only).

## D-034: Pad hardware has ONE delivery authority — no synth notes on pad presses

**Date:** 2026-09-18
**Context:** On the hardware Launchpad, every pad press also played a
wavetable-synth note on top of the pad's loop — chromatic pitches
unrelated to the song, on every song. The web app had just root-caused
the same symptom (stale `instrument-melody` driver + a force-enabled
merged surface, fixed in 5d1e3bd0 by per-song driver reset + surface
arbitration). Separately, two phantom purple pack-pad tiles (speaker
glyph) sat among empty cells of the 64-grid on songs that never had
pack pads assigned.

**Root causes (three, not one):**

1. **Dual pad mapping — the synth note.** `MIDIKeyboardTransport`
   (default routing `.synth`: every note voices `DesktopSynthNode`)
   excluded only the endpoint named exactly "LPProMK3 MIDI" or a
   display name containing "Launchpad Pro MK3". The MK3 exposes THREE
   USB interfaces (DAW / MIDI / DIN), and CoreMIDI decorates display
   names LATE during a plug-in burst (D-031: "LPProMK3 MIDI" →
   "Launchpad Pro MK3 LPProMK3 MIDI"). In that window the DAW/DIN
   interfaces matched neither branch, the keyboard transport connected
   to them, and a later name resolution is not guaranteed another
   setup-changed rescan — the stale connection stuck for the session.
   Every grid note then double-delivered: USBLaunchpadTransport →
   LaunchpadController → chop loop, AND MIDIKeyboardTransport →
   `.midiNote` → `synthNode.noteOn(midi: <grid note 11–88>)`.
   Song-independent, hence "all songs". (NOT a regression from
   8f41ff17/b3727853: the stamped seam correctly REPLACES the legacy
   pad callbacks — `padEvent` calls stamped OR legacy, never both —
   and `usb.onContribution` has no consumer. The exclusion hole
   predates both; today was simply the first extended hardware
   session.)
2. **Phantom pack tiles.** `ProjectCoordinator.songDidActivate`
   early-returned when a song had no working project, leaving the
   GLOBAL `PadAssignmentStore`/`PadFXStore` holding the previous
   song's workspace. Observed on this machine: one song's working
   project carries two packPad refs (house-classic #5, lo-fi-hiphop
   #7) — restored once, they surfaced on every project-less song.
   Visual leak only: pack pads voice through PackPadPlayer→ChopPlayer,
   never the synth.
3. **Stale melody guide (the web bug's literal twin).**
   `melodyGuideEnabled` survived song switches — attach() rebuilt the
   player but never reset the toggle, so a per-song synth-note guide
   mode played the NEXT song's melody uninvited once the transport
   rolled.

**Decision:**

- **Single delivery authority (arbitration):** `isLaunchpad` now
  matches the port-family fragment "LPProMK3" (USB string descriptor,
  present on all three interfaces at every enumeration stage) in name
  or display name, plus the decorated-name fallback. While the pad
  grid owns the Launchpad, a hardware pad press reaches ONLY
  LaunchpadController — the note/chord synth never fires on pad
  presses; notes sound only on their own explicitly selected surface
  (iOS Notes | Chords | Samples semantics, web arbitration parity).
  The `.synth` route remains for genuine keyboards — that is its job.
- **Per-song reset of the note-synth layer:** attach() resets
  `melodyGuideEnabled` (via `SongActivationPolicy`, pure + pinned) and
  calls `synthNode.allNotesOff()` so no guide/keyboard note rings
  across a load (web 5d1e3bd0 parity).
- **Per-song store swap:** activation WITHOUT a workspace now swaps
  the global stores to defaults (`ProjectStateBridge.activateFresh` —
  assignment table + FX map only; analysisId-keyed stores are
  untouched: they cannot leak and clearing them would delete
  pre-Projects state). iOS's coordinator has the same early-return
  inherit and the same three-port exclusion shape in its
  MIDIKeyboardTransport — flagged as follow-ups, not silently
  diverged.

**Where:** `Sources/JamDesktopCore/Launchpad/MIDIKeyboardTransport.swift`
(`launchpadPortFamily`, `isLaunchpad`),
`Sources/JamDesktopCore/Projects/ProjectStateBridge.swift`
(`activateFresh`), `Sources/JamDesktopCore/Session/SongActivationPolicy.swift`,
`Sources/JamDesktop/Projects/ProjectCoordinator.swift` (no-workspace
swap), `Sources/JamDesktop/SessionController.swift` (attach-time synth
reset). Pinned by `MIDIKeyboardTransportTests` (all three interfaces
excluded in decorated AND pre-resolution name states; grid notes
injected at every LP endpoint produce zero events — no synth note on a
pad press), `ProjectStateBridgeTests` (`activateFresh` clears global
stores, leaves per-song stores), and `SongActivationPolicyTests`.
