// ModeCoordinator.swift
//
// The contribution engine's single executor. Subscribes to the
// ContributionEventBus, routes every event through ModeRouter, and
// executes the resulting AudioAction against SampleScheduler /
// WavetableSynthNode. NOTHING else may reach those executors (legacy
// LayerPlayer.triggerRaw replay is the one documented exception,
// D-015) — SampleScheduler's `contributionGuard` assertion enforces
// this in debug builds.
//
// Also owns everything the 8×8 grid needs to paint:
//   - appMode        — persisted via SampleSettingsStore.appModeRaw
//   - padVisuals     — 64 precomputed PadVisuals from the mode layout
//   - pressedPads    — lit pads, updated from the BUS (not the touch
//                      overlay) so hardware presses (P2) light the
//                      on-screen grid identically
//
// Grid context (D-016): a loaded bundle supplies the analysed
// beats/sections + persisted section gates; no bundle degrades to the
// sketch settings' synthetic tempo grid — "sketch" is just sample
// mode without a song.
//
// Pack pads occupy the TOP-LEFT 4×4 quadrant of the 8×8 grid (rows
// 8..5 top-down, cols 1..4) in both implemented modes; in hybrid the
// bottom half (rows 1–4) is the chromatic note surface, so the sample
// quadrant is identical across the two modes and muscle memory holds.
//
// The class is split across sibling files by concern (same type, same
// behavior — stored state all lives here):
//   ModeCoordinator+Layout.swift       — grid painting + pad bindings
//   ModeCoordinator+LocalSamples.swift — P3 mic pipeline + assignments
//   ModeCoordinator+Vocoder.swift      — P5 capture + carrier programs
//   ModeCoordinator+Transforms.swift   — P4 chains + bake
//   ModeCoordinator+PadEditing.swift   — long-press sheets + arrange

import AVFoundation
import Combine
import Foundation
import SwiftUI
import ToneForgeEngine

/// Sheet target for the pad-effects editor, produced by a long-press
/// on a bound sample pad. `Identifiable` so `.sheet(item:)` presents
/// a fresh editor per long-press.
struct PadEffectsTarget: Identifiable {
    let id = UUID()
    let packId: String
    let padIdx: Int
    let padName: String
    let manifestBaseline: SamplePadEffects?
    /// Grid position (PadIndex convention) so the editor's Preview
    /// button can re-fire the pad through the contribution bus.
    let gridRow: Int
    let gridCol: Int
}

/// Sheet target for the pad SOURCE sheet (P3): record a mic sample
/// onto an empty pad, assign an existing local sample, or manage the
/// one already assigned (classify override, remove, delete).
struct PadSourceTarget: Identifiable {
    let id = UUID()
    let gridRow: Int
    let gridCol: Int
    /// Non-nil when the pad already carries a local sample.
    let sample: PadSampleMetadata?

    var gridRaw: Int { gridRow * 10 + gridCol }
}

/// Union of the long-press sheets — `.sheet(item:)` needs one
/// Identifiable value.
enum PadSheetTarget: Identifiable {
    case effects(PadEffectsTarget)
    case source(PadSourceTarget)
    case trimmer(SampleTrimmerTarget)

    var id: UUID {
        switch self {
        case .effects(let t): return t.id
        case .source(let t):  return t.id
        case .trimmer(let t): return t.id
        }
    }
}

@MainActor
public final class ModeCoordinator: ObservableObject {

    // MARK: - Published grid state

    /// Current contribution mode. Set via `setMode` (persists +
    /// rebuilds the layout).
    @Published public private(set) var appMode: AppMode
    /// Precomputed visuals for the 8×8 grid, indexed
    /// `(row-1)*8 + (col-1)` (PadIndex convention, row 1 = bottom).
    /// internal(set): written by rebuildLayout (+Layout file).
    @Published public internal(set) var padVisuals: [PadVisual]
    /// PadIndex rawValues currently held down — fed from the BUS so
    /// every input surface lights the on-screen grid.
    @Published public private(set) var pressedPads: Set<Int> = []
    /// Live loop position per running sequence pad, keyed by PadIndex
    /// rawValue (row*10+col). Drives the grid's loop-pulse animation so
    /// sequence pads visibly advance in lock with their tempo.
    @Published public private(set) var sequencePulses: [Int: SequencePulse] = [:]

    // MARK: - Shared state (internal: the +Concern files read/write these)

    /// Unowned: AppState owns the coordinator; identical lifetimes.
    unowned let app: AppState

    /// The immutable layout snapshot both ModeRouter and the painter
    /// consume. Rebuilt on mode/pack/song/chord change.
    var layout: any GridLayoutProviding = EmptyLayout()

    /// PadIndex.rawValue → (packId, padIdx-within-pack) for the bound
    /// sample quadrant. Unbound grid pads no-op.
    var padBindings: [Int: (packId: String, padIdx: Int)] = [:]

    /// Transient session-replay overlay (P6, D-015): the recorded
    /// session's padMapping resolved to scheduler keys. Consulted
    /// ONLY for `isReplay` events, so a loaded session plays through
    /// the pads it was recorded with even after the user re-binds the
    /// live grid. Cleared when replay stops.
    var replayBindings: [Int: (packId: String, padIdx: Int)] = [:]

    /// Currently previewing pad from the pack browser (for stop
    /// functionality; see +PadEditing).
    var previewingPad: (packId: String, padIdx: Int)?

    /// P4: owns rendered transform buffers + loop flags; fills the
    /// scheduler's transformResolver/loopResolver seams.
    let transformHost = PadTransformHost()

    /// D-023: running SequencerPlayers behind "sequence pads". Each
    /// pad's player re-publishes packPad triggers to the same bus, so a
    /// sequence pad plays other pads over time. Layered by pad index.
    lazy var sequencePadManager = SequencePadManager(
        eventBus: app.contributionBus,
        patternStore: app.sequencerPatternStore,
        delegate: app
    )

    // MARK: - Jam Samples (PERFORM_PARITY)

    /// Fire a song chop from the Jam Samples grid. Jam mode routes pad
    /// events to the synth, so this bypasses the ModeRouter — but it
    /// still goes through the coordinator, satisfying the scheduler's
    /// `contributionGuard` (an assert that trips on direct trigger
    /// calls) while keeping quantize + section gating intact.
    public func triggerJamSample(padIdx: Int, packId: String, latch: Bool) {
        isExecuting = true
        defer { isExecuting = false }
        let s = app.sampleScheduler
        // Launchpad clip feel: launches wait for the next downbeat so
        // multiple pads start together. Latch = tap on/off (loops);
        // Tap = plays while held. Save/restore the scheduler's shared
        // hold/quantize around the SYNCHRONOUS trigger so Contribute's
        // settings are untouched (trigger schedules its launch inline).
        let savedHold = s.holdMode, savedQ = s.quantize
        s.holdMode = latch ? .toggle : .hold
        s.quantize = .bar
        _ = s.trigger(padIdx: padIdx, packId: packId)
        s.holdMode = savedHold
        s.quantize = savedQ
    }

    /// Release a held (Tap-mode) Jam sample on finger-up.
    public func releaseJamSample(padIdx: Int, packId: String) {
        app.sampleScheduler.release(padIdx: padIdx, packId: packId)
    }

    // MARK: - Private

    /// True only while this coordinator is executing a routed
    /// AudioAction — the scheduler's contributionGuard reads it.
    private var isExecuting = false

    private var busToken: ContributionEventBus.Token?
    private var cancellables: Set<AnyCancellable> = []

    /// Mirror Jam Samples clip state onto the Launchpad LEDs: dim tint =
    /// idle, amber bright = armed (queued), full tint = playing. Chop i
    /// sits at padVisuals[i] (reading order), matching `jamSampleAt`.
    /// No-op unless the Jam surface is in Samples mode.
    func refreshJamSamplesLEDs() {
        guard appMode == .jamInKey, app.jamSettings.padMode == .samples,
              let dna = app.selectedSongDnaPack else { return }
        let pads = dna.pack.pack.pads.sorted { $0.padIdx < $1.padIdx }
        let packId = dna.pack.pack.packId
        var v = Array(repeating: PadVisual.off, count: 64)
        for (i, pad) in pads.prefix(64).enumerated() {
            let key = SamplePadKey(packId: packId, padIdx: pad.padIdx)
            let playing = app.sampleVoicePool.ringingPadKeys.contains(key)
            let armed = app.sampleVoicePool.pendingPadKeys.contains(key)
            let hint: UInt32 = armed ? 0xFF8000 : Self.familyColor(pad.family)
            v[i] = PadVisual(colorHint: hint, isBright: playing || armed)
        }
        padVisuals = v
    }

    public init(app: AppState) {
        self.app = app
        self.appMode = AppMode(rawValue: app.sampleSettings.appModeRaw) ?? .sample
        self.padVisuals = Array(repeating: .off, count: 64)
        // Build an initial layout from whatever state exists so a
        // never-booted AppState (snapshot tests assign currentBundle
        // directly) still paints a correct grid.
        rebuildLayout()
    }

    // MARK: - Lifecycle

    /// Wire the bus subscription + scheduler guard and push the grid
    /// context. Called once from `AppState.bootAudio` after the audio
    /// graph is up. Idempotent.
    public func start() {
        guard busToken == nil else { return }
        busToken = app.contributionBus.subscribe { [weak self] event in
            self?.handle(event)
        }
        app.sampleScheduler.contributionGuard = { [weak self] in
            self?.isExecuting ?? true
        }
        #if canImport(AVFoundation)
        app.sampleScheduler.transformResolver = { [weak self] base, packId, padIdx in
            self?.transformHost.resolve(
                base: base, packId: packId, padIdx: padIdx
            ) ?? base
        }
        #endif
        app.sampleScheduler.loopResolver = { [weak self] packId, padIdx in
            self?.transformHost.loops(packId: packId, padIdx: padIdx) ?? false
        }
        // Surface per-pad loop position to the grid for the pulse
        // animation. nil clears the pad when its sequence stops.
        sequencePadManager.onPulse = { [weak self] padIdx, pulse in
            guard let self else { return }
            if let pulse {
                self.sequencePulses[padIdx] = pulse
            } else {
                self.sequencePulses.removeValue(forKey: padIdx)
            }
        }
        applyGridContext()
        syncLocalBuffers()
        syncTransforms()
        rebuildLayout()

        // Mirror Jam Samples clip state onto the Launchpad: repaint LEDs
        // when a chop arms/plays/stops, and on entering/leaving Samples.
        Publishers.Merge(
            app.sampleVoicePool.$ringingPadKeys.map { _ in () },
            app.sampleVoicePool.$pendingPadKeys.map { _ in () }
        )
        .receive(on: DispatchQueue.main)
        .sink { [weak self] in self?.refreshJamSamplesLEDs() }
        .store(in: &cancellables)

        app.jamSettings.$padMode
            .receive(on: DispatchQueue.main)
            .sink { [weak self] mode in
                guard let self else { return }
                if mode == .samples { self.refreshJamSamplesLEDs() }
                else { self.rebuildLayout() }  // restore normal LED frame
            }
            .store(in: &cancellables)
    }

    // MARK: - Mode

    /// Switch modes. Unimplemented modes are ignored (the menu
    /// disables them, this is the backstop). Persists to the settings
    /// blob and silences held synth notes so nothing rings across the
    /// mode change.
    ///
    /// Running sequence pads are intentionally NOT stopped here: their
    /// players fire through the bus/delegate independent of the grid
    /// layout, so a loop keeps playing when the user switches tabs
    /// (Contribute↔Jam) or modes. They stop only on explicit intent —
    /// tapping the pad, transport stop-all, or clearing the assignment.
    public func setMode(_ mode: AppMode) {
        guard mode.isImplemented, mode != appMode else { return }
        appMode = mode
        app.sampleSettings.appModeRaw = mode.rawValue
        // Remember the last contribute-family mode separately so the
        // Contribute surface tab restores sample/hybrid after a visit
        // to Jam in Key (which also writes appModeRaw).
        if mode == .sample || mode == .hybrid {
            app.sampleSettings.lastContributeModeRaw = mode.rawValue
        }
        app.wavetableSynthNode.allNotesOff()
        // Local assignments are per-mode but scheduler buffers are
        // keyed by grid pad alone — swap them with the mode. Same
        // for armed transform renders.
        syncLocalBuffers()
        syncTransforms()
        rebuildLayout()
        // Jam in Key runs the metronome from JamSettingsStore; other
        // modes use the song/sketch settings. Re-sync on every switch.
        app.syncMetronome()
    }

    // MARK: - Touch input (on-screen grid adapter)

    /// On-screen grid touch-down. Stamps hostTime BEFORE publishing so
    /// the LatencyProbe (P7) measures the true touch→attack path.
    public func touchPadDown(row: Int, col: Int) {
        // Sequence pads don't sound themselves — they run a sequencer
        // that re-fires other pads. Intercept before the normal bus
        // publish; respect the global Hold/Toggle setting.
        if let patternId = sequencePatternId(row: row, col: col) {
            handleSequencePadDown(
                patternId: patternId,
                padIdx: PadIndex.at(row: row, col: col).rawValue
            )
            return
        }
        app.contributionBus.publish(ContributionEvent(
            source: .touch,
            kind: .padDown(row: row, col: col),
            timestamp: app.audioEngine.clock.nowSongSeconds,
            hostTime: mach_absolute_time()
        ))
    }

    /// On-screen grid touch-up.
    public func touchPadUp(row: Int, col: Int) {
        if sequencePatternId(row: row, col: col) != nil {
            handleSequencePadUp(padIdx: PadIndex.at(row: row, col: col).rawValue)
            return
        }
        app.contributionBus.publish(ContributionEvent(
            source: .touch,
            kind: .padUp(row: row, col: col),
            timestamp: app.audioEngine.clock.nowSongSeconds,
            hostTime: mach_absolute_time()
        ))
    }

    // MARK: - Sequence pad routing (D-023)

    /// The saved-pattern id assigned to a grid pad, or nil. Public so the
    /// Sequence Builder can load an existing sequence for in-place editing.
    public func assignedSequenceId(row: Int, col: Int) -> UUID? {
        sequencePatternId(row: row, col: col)
    }

    /// The saved-pattern id assigned to this grid pad, or nil.
    private func sequencePatternId(row: Int, col: Int) -> UUID? {
        let grid = PadIndex.at(row: row, col: col)
        guard grid.isValid,
              let slot = app.padAssignmentStore.slot(mode: appMode, padIdx: grid.rawValue),
              case .sequence(let patternId) = slot.ref
        else { return nil }
        return patternId
    }

    /// Pad-down on a sequence pad. Toggle: flip run state. Hold: start.
    private func handleSequencePadDown(patternId: UUID, padIdx: Int) {
        let bpm = app.currentBundle?.meta.tempoBpm ?? app.sketchSettings.tempoBpm
        switch app.sampleSettings.holdMode {
        case .toggle:
            if sequencePadManager.isActive(padIdx: padIdx) {
                sequencePadManager.stop(padIdx: padIdx)
            } else {
                sequencePadManager.start(patternId: patternId, padIdx: padIdx, songBPM: bpm)
            }
        case .hold:
            sequencePadManager.start(patternId: patternId, padIdx: padIdx, songBPM: bpm)
        }
    }

    /// Pad-up on a sequence pad. Hold: stop. Toggle: no-op.
    private func handleSequencePadUp(padIdx: Int) {
        if app.sampleSettings.holdMode == .hold {
            sequencePadManager.stop(padIdx: padIdx)
        }
    }

    // MARK: - Bus handling + execution

    private func handle(_ event: ContributionEvent) {
        // Pressed-pad mirror for the grid painter — every source,
        // including replays, lights the pads it plays.
        switch event.kind {
        case .padDown(let row, let col):
            let pad = PadIndex.at(row: row, col: col)
            if pad.isValid { pressedPads.insert(pad.rawValue) }
        case .padUp(let row, let col):
            let pad = PadIndex.at(row: row, col: col)
            if pad.isValid { pressedPads.remove(pad.rawValue) }
        case .midiNote, .gap:
            break
        }

        // Jam Samples: hardware (Launchpad) pad events arrive on the bus.
        // When the Jam surface is in Samples mode, map them onto the
        // song's chops (Launchpad-clip behavior) instead of the synth.
        // On-screen Samples pads bypass the bus (direct calls), so this
        // only catches hardware — no double-trigger.
        if appMode == .jamInKey && app.jamSettings.padMode == .samples {
            switch event.kind {
            case .padDown(let row, let col):
                if let (padIdx, packId) = jamSampleAt(row: row, col: col) {
                    triggerJamSample(padIdx: padIdx, packId: packId,
                                     latch: app.jamSettings.sampleLatch)
                }
                return
            case .padUp(let row, let col):
                if !app.jamSettings.sampleLatch,
                   let (padIdx, packId) = jamSampleAt(row: row, col: col) {
                    releaseJamSample(padIdx: padIdx, packId: packId)
                }
                return
            default:
                break
            }
        }

        let action = ModeRouter.resolve(event, mode: appMode, layout: layout)
        execute(action, for: event)
    }

    /// Map a Launchpad grid cell (row/col 1…8) to a Jam Samples chop by
    /// reading order — top-left pad = first chop. Nil past the chop count.
    private func jamSampleAt(row: Int, col: Int) -> (padIdx: Int, packId: String)? {
        guard let dna = app.selectedSongDnaPack else { return nil }
        let pads = dna.pack.pack.pads.sorted { $0.padIdx < $1.padIdx }
        let index = (row - 1) * 8 + (col - 1)
        guard index >= 0, index < pads.count else { return nil }
        return (pads[index].padIdx, dna.pack.pack.packId)
    }

    private func execute(_ action: AudioAction, for event: ContributionEvent) {
        // Sketch-record count-in: while the transport runs the
        // negative lead bar, live input is suppressed entirely so
        // nothing sounds or gets captured before songTime 0. Replays
        // pass through (a saved layer playing back is not the user
        // jumping the gun).
        if app.isCountingIn, !event.isReplay { return }

        isExecuting = true
        defer { isExecuting = false }

        switch action {
        case .triggerSample(let raw):
            guard let binding = binding(for: raw, isReplay: event.isReplay)
            else { return }
            if event.isReplay {
                // Replays bypass quantize/section gates — the live
                // trigger would re-snap already-snapped timestamps
                // (or gate them out entirely), the exact silent-
                // replay bug LayerPlayer hit (see makeLayerPlayer).
                _ = app.sampleScheduler.triggerRaw(
                    padIdx: binding.padIdx, packId: binding.packId
                )
            } else {
                app.sampleScheduler.trigger(
                    padIdx: binding.padIdx, packId: binding.packId
                )
            }

        case .releaseSample(let raw):
            guard let binding = binding(for: raw, isReplay: event.isReplay)
            else { return }
            app.sampleScheduler.release(
                padIdx: binding.padIdx, packId: binding.packId
            )

        case .synthNoteOn(let midi, let velocity, let isChordTone):
            // Chord tones get a small accent so the bright pads sound
            // bright too.
            let vel = min(1.0, velocity + (isChordTone ? 0.15 : 0))
            app.wavetableSynthNode.noteOn(midi: midi, velocity: vel)
            // Mirror to external gear (PERFORM_PARITY spec 2B); no-op
            // unless MIDI out is enabled. Replays don't re-emit — the
            // live pass already sent them.
            if !event.isReplay { app.audioEngine.midiSendNoteOn(midi: midi, velocity: velocity) }

        case .synthNoteOff(let midi):
            app.wavetableSynthNode.noteOff(midi: midi)
            if !event.isReplay { app.audioEngine.midiSendNoteOff(midi: midi) }

        case .padSynthNote(let midi, let velocity):
            // Jam in Key pads voice through the PadSynth (same sound
            // as the degree/chord pads). Router velocity is 0…1;
            // PadSynth expects MIDI-style 0…127.
            let vel = Float(max(0, min(1, velocity)) * 127)
            app.padSynth.triggerNote(midi: midi, velocity: vel)
            // Mirror to external gear. Jam pads auto-release (no pad-up
            // action), so schedule a matching note-off after a musical
            // beat — otherwise external gear hangs the note.
            if !event.isReplay {
                app.audioEngine.midiSendNoteOn(midi: midi, velocity: velocity)
                let beat = app.audioEngine.performanceFX.beatClock.beatDuration ?? 0.5
                DispatchQueue.main.asyncAfter(deadline: .now() + beat) { [weak app] in
                    app?.audioEngine.midiSendNoteOff(midi: midi)
                }
            }

        case .none:
            break
        }
    }

    /// Scheduler key for a routed pad event. Live events use the
    /// live grid bindings; replay events prefer the session overlay
    /// and fall back to the live binding (a session saved before its
    /// overlay concept existed, or a mapping entry that failed to
    /// restore, still plays whatever the grid holds now).
    private func binding(
        for raw: Int, isReplay: Bool
    ) -> (packId: String, padIdx: Int)? {
        isReplay ? (replayBindings[raw] ?? padBindings[raw]) : padBindings[raw]
    }

    // MARK: - Session capture + replay (P6, D-015)

    /// Snapshot of the current grid → sample bindings in SessionCapture
    /// terms, taken by the recorder at arm time. Pack pads reference
    /// (packId, padIdx); local pads resolve back to their sample UUID
    /// via the assignment store so the mapping survives re-binds.
    public func currentPadMapping() -> [PadAddress: PadSampleReference] {
        var mapping: [PadAddress: PadSampleReference] = [:]
        for (raw, binding) in padBindings {
            let pad = PadIndex(raw)
            guard pad.isValid else { continue }
            let addr = PadAddress(mode: appMode, pad: pad)
            if binding.packId == SampleScheduler.localPackId {
                guard case .localSample(let id)? = app.padAssignmentStore
                    .slot(mode: appMode, padIdx: raw)?.ref
                else { continue }
                mapping[addr] = .localSample(id: id)
            } else {
                mapping[addr] = .packPad(
                    packId: binding.packId, padIdx: binding.padIdx
                )
            }
        }
        return mapping
    }

    /// Install a loaded session's padMapping as the replay overlay.
    /// Pack pads map straight to scheduler keys (the caller preloads
    /// their packs); local samples decode from the store into the
    /// scheduler's local-buffer table under their grid index —
    /// samples deleted since the recording are skipped (their events
    /// fall back to whatever the live grid holds).
    public func applyReplayOverlay(_ session: SessionCapture) {
        replayBindings = [:]
        for (addr, ref) in session.padMapping where addr.mode == session.appMode {
            let raw = addr.pad.rawValue
            switch ref {
            case .packPad(let packId, let padIdx):
                replayBindings[raw] = (packId: packId, padIdx: padIdx)
            case .localSample(let id):
                guard let meta = app.padSampleStore.metadata(id: id)
                else { continue }
                replayBindings[raw] = (
                    packId: SampleScheduler.localPackId, padIdx: raw
                )
                Task { [weak self] in
                    guard let self,
                          let buffer = try? await self.app.padSampleStore
                              .loadBuffer(id: id),
                          self.replayBindings[raw]?.packId
                              == SampleScheduler.localPackId
                    else { return }
                    self.app.sampleScheduler.setLocalBuffer(
                        buffer, meta: meta, for: raw
                    )
                }
            case .sequence:
                // Sequence pads are not part of session replay overlays.
                continue
            }
        }
    }

    /// Drop the replay overlay and restore the LIVE mode's local
    /// buffers (the overlay may have loaded session samples into
    /// grid slots the live assignments don't own).
    public func clearReplayOverlay() {
        guard !replayBindings.isEmpty else { return }
        replayBindings = [:]
        syncLocalBuffers()
    }

    // MARK: - Grid context (D-016)

    /// Push the quantize/section context matching the loaded-song
    /// state into the scheduler. Bundle loaded → analysed grid +
    /// persisted gates; no bundle → sketch settings' synthetic tempo
    /// grid (sketch = sample mode without a song).
    public func applyGridContext() {
        if let bundle = app.currentBundle {
            app.sampleScheduler.quantize = app.sampleSettings.quantizeMode
            app.sampleScheduler.updateBundle(
                timeline: bundle.timeline, meta: bundle.meta
            )
            app.sampleScheduler.allowedSections =
                app.sampleSettings.sectionGates(for: bundle.analysisId)
        } else {
            app.sampleScheduler.quantize = app.sketchSettings.quantizeMode
            app.sampleScheduler.updateSyntheticContext(
                tempoBpm: app.sketchSettings.tempoBpm
            )
            app.sampleScheduler.allowedSections = nil
        }
    }
}
