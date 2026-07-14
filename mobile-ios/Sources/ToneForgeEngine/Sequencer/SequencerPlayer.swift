// SequencerPlayer.swift
//
// Step sequencer playback engine (D-023 Phase 3). Owns a pattern,
// monitors the clock, and triggers samples at step boundaries.
//
// Trigger paths:
//   - packPad → ContributionEventBus.padDown/padUp (existing path)
//   - bundleChop → delegate.playChop (for audio engine integration)
//   - localSample / customURL → delegate.playSample (future)
//
// The player is @MainActor because it publishes to ContributionEventBus
// (which is @MainActor) and updates observable state for UI binding.
//
// Clock integration: the player either owns its own clock (standalone
// mode) or syncs to an external TransportClock (song-sync mode).

import Foundation

// MARK: - Delegate

/// Protocol for audio triggers that don't go through ContributionEventBus.
@MainActor
public protocol SequencerPlayerDelegate: AnyObject {
    /// Called when a bundle chop should play.
    /// - Parameters:
    ///   - player: The sequencer player.
    ///   - presetKey: The chop preset (e.g., "harmonic").
    ///   - chopIndex: Index into the preset's chops.
    ///   - velocity: Trigger velocity (0–1).
    ///   - pan: Track stereo pan (-1…+1).
    func sequencerPlayer(
        _ player: SequencerPlayer,
        playBundleChop presetKey: String,
        chopIndex: Int,
        velocity: Float,
        pan: Float
    )

    /// Called when a local sample should play.
    func sequencerPlayer(
        _ player: SequencerPlayer,
        playLocalSample id: UUID,
        velocity: Float,
        pan: Float
    )

    /// Called when a custom URL sample should play.
    func sequencerPlayer(
        _ player: SequencerPlayer,
        playURL url: URL,
        startSec: Double?,
        endSec: Double?,
        velocity: Float,
        pan: Float
    )

    /// Called when a pack pad should play. Triggers the pad straight
    /// from its pack (loading it if needed) — independent of the grid's
    /// current layout, so a track sounds regardless of the fronted pack.
    func sequencerPlayer(
        _ player: SequencerPlayer,
        playPackPad packId: String,
        padIdx: Int,
        velocity: Float,
        pan: Float
    )

    /// Called when a synth chord should play (key/diatonic chord voiced
    /// on the pad synth — no sample source, works without a song).
    func sequencerPlayer(
        _ player: SequencerPlayer,
        playSynthChord symbol: String,
        octaveShift: Int,
        velocity: Float
    )
}

// MARK: - Player

@MainActor
public final class SequencerPlayer: ObservableObject {

    // MARK: - Published State

    /// The pattern being played (observable for UI binding).
    @Published public var pattern: SequencerPattern {
        didSet {
            clock.stepCount = pattern.stepCount.rawValue
            clock.swing = pattern.swing
            if let bpm = pattern.bpmOverride {
                clock.bpm = bpm
            }
        }
    }

    /// Current step for UI highlighting.
    @Published public private(set) var currentStep: Int = 0

    /// Whether the sequencer is playing.
    @Published public private(set) var isPlaying: Bool = false

    // MARK: - Dependencies

    /// Event bus for pack pad triggers.
    private let eventBus: ContributionEventBus

    /// Clock for step timing.
    private let clock: SequencerClock

    /// Internal wall-clock driver for standalone playback. Nil when
    /// the player is song-synced (external ticks drive the clock).
    private var driver: DispatchSourceTimer?

    /// Dedicated queue for the standalone driver so step timing does
    /// not jitter when the main thread is busy (clock is thread-safe;
    /// step callbacks hop back to MainActor in the delegate conformance).
    private static let driverQueue = DispatchQueue(
        label: "com.toneforge.sequencer.driver",
        qos: .userInteractive
    )

    /// Step to resume from after pause() (0 = start of pattern).
    private var pausedStep: Int = 0

    /// Delegate for non-bus audio triggers.
    public weak var delegate: SequencerPlayerDelegate?

    /// BPM provider (typically song tempo from TransportClock context).
    /// Used when pattern.bpmOverride is nil.
    public var songBPM: Double = 120 {
        didSet {
            if pattern.bpmOverride == nil {
                clock.bpm = songBPM
            }
        }
    }

    // MARK: - Init

    public init(
        pattern: SequencerPattern = SequencerPattern(),
        eventBus: ContributionEventBus
    ) {
        self.pattern = pattern
        self.eventBus = eventBus
        self.clock = SequencerClock(
            stepCount: pattern.stepCount.rawValue,
            bpm: pattern.bpmOverride ?? 120
        )
        self.clock.swing = pattern.swing
        self.clock.isLooping = pattern.isLooping
        self.clock.delegate = self
    }

    // MARK: - Transport

    /// Start playing from step 0.
    /// - Parameters:
    ///   - songSeconds: Current song time (used only when `sync` is true).
    ///   - sync: When true, the player expects external `tick(songSeconds:)`
    ///     calls to advance the clock (song-locked). When false (default),
    ///     the player runs its own wall-clock driver for standalone playback.
    public func play(at songSeconds: Double = 0, sync: Bool = false) {
        startPlayback(at: songSeconds, sync: sync, fromStep: 0)
    }

    /// Stop playback.
    public func stop() {
        guard isPlaying else { return }
        stopDriver()
        clock.stop()
        isPlaying = false
        pausedStep = 0

        // Release any held notes (if we add hold mode later)
        releaseAllTriggers()
    }

    /// Pause playback (resume with resume(at:)).
    public func pause() {
        guard isPlaying else { return }
        pausedStep = clock.currentStep
        stopDriver()
        clock.stop()
        isPlaying = false
    }

    /// Resume from the step where pause() left off.
    public func resume(at songSeconds: Double) {
        guard !isPlaying else { return }
        startPlayback(at: songSeconds, sync: true, fromStep: pausedStep)
    }

    private func startPlayback(at songSeconds: Double, sync: Bool, fromStep step: Int) {
        guard !isPlaying else { return }

        // Update BPM from song if no override
        if pattern.bpmOverride == nil {
            clock.bpm = songBPM
        }

        isPlaying = true
        currentStep = step

        if sync {
            // Song-synced: external transport drives tick().
            clock.start(at: songSeconds, fromStep: step)
            fireStep(step)
        } else {
            // Standalone: drive the clock ourselves from a monotonic base.
            let now = ProcessInfo.processInfo.systemUptime
            clock.start(at: now, fromStep: step)
            fireStep(step)
            startDriver()
        }
    }

    /// Tick the clock from an external transport (song-sync mode).
    /// No-op for standalone playback, which is driven internally.
    /// - Parameter songSeconds: Current song time.
    public func tick(songSeconds: Double) {
        guard isPlaying, driver == nil else { return }
        clock.tick(songSeconds: songSeconds)
        // One-shot patterns stop the clock internally when finished.
        if !clock.isRunning { stop() }
    }

    // MARK: - Standalone Driver

    /// Start the internal wall-clock driver (~4 ms cadence on a
    /// dedicated queue — main-thread stalls no longer delay ticks).
    private func startDriver() {
        stopDriver()
        let clock = self.clock
        let timer = DispatchSource.makeTimerSource(queue: Self.driverQueue)
        timer.schedule(deadline: .now(), repeating: .milliseconds(4), leeway: .milliseconds(1))
        timer.setEventHandler { [weak self] in
            // Background queue: SequencerClock is lock-protected; step
            // callbacks hop to MainActor in the delegate conformance.
            let now = ProcessInfo.processInfo.systemUptime
            clock.tick(songSeconds: now)
            // Auto-stop one-shot patterns when the clock finishes.
            if !clock.isRunning {
                Task { @MainActor in
                    self?.stop()
                }
            }
        }
        driver = timer
        timer.resume()
    }

    /// Cancel the internal driver if running.
    private func stopDriver() {
        driver?.cancel()
        driver = nil
    }

    // MARK: - Step Triggers

    private func fireStep(_ step: Int) {
        currentStep = step

        let triggers = pattern.triggersAt(step: step)
        for (trackIndex, velocity) in triggers {
            let track = pattern.tracks[trackIndex]
            fireChopReference(track.chopRef, velocity: velocity, pan: track.pan)
        }
    }

    private func fireChopReference(
        _ ref: ChopReference,
        velocity: Float,
        pan: Float
    ) {
        switch ref {
        case .packPad(let packId, let padIdx):
            // Trigger the pad straight from its pack via the delegate.
            // (padIdx is the pack's own 0-based pad index, not a grid
            // coordinate — routing it through the bus as row/col would
            // hit the wrong cell and ignore packId entirely.)
            delegate?.sequencerPlayer(
                self,
                playPackPad: packId,
                padIdx: padIdx,
                velocity: velocity,
                pan: pan
            )

        case .bundleChop(let presetKey, let chopIndex, _):
            delegate?.sequencerPlayer(
                self,
                playBundleChop: presetKey,
                chopIndex: chopIndex,
                velocity: velocity,
                pan: pan
            )

        case .localSample(let id):
            delegate?.sequencerPlayer(
                self,
                playLocalSample: id,
                velocity: velocity,
                pan: pan
            )

        case .customURL(let url, let startSec, let endSec):
            delegate?.sequencerPlayer(
                self,
                playURL: url,
                startSec: startSec,
                endSec: endSec,
                velocity: velocity,
                pan: pan
            )

        case .sequence:
            // Nested sequences are not triggered from within a pattern.
            // A sequence is playable only when assigned to a pad.
            break

        case .synthChord(let symbol, let octaveShift):
            delegate?.sequencerPlayer(
                self,
                playSynthChord: symbol,
                octaveShift: octaveShift,
                velocity: velocity
            )
        }
    }

    private func releaseAllTriggers() {
        // Release any held triggers if we add sustain mode later
        // For now, one-shot samples don't need explicit release
    }

    // MARK: - Pattern Editing (forwarded)

    /// Toggle a step in a track.
    public func toggleStep(track: Int, step: Int) {
        guard track >= 0, track < pattern.tracks.count else { return }
        pattern.tracks[track].toggleStep(at: step)
    }

    /// Set velocity for a step.
    public func setStepVelocity(track: Int, step: Int, velocity: Float) {
        guard track >= 0, track < pattern.tracks.count else { return }
        pattern.tracks[track].setStepVelocity(at: step, velocity: velocity)
    }

    /// Add a track for the given chop reference.
    public func addTrack(for chopRef: ChopReference, name: String? = nil) {
        pattern.addTrack(for: chopRef, name: name)
    }

    /// Remove a track.
    public func removeTrack(at index: Int) {
        pattern.removeTrack(at: index)
    }

    /// Reassign a track's sound (chop reference) and, optionally, its
    /// display name. Steps/velocities are preserved — only what the
    /// track triggers changes. Used to correct a Beat Capture track's
    /// drum role after the pattern is in the editor.
    public func setTrackChop(
        track: Int, chopRef: ChopReference, name: String? = nil
    ) {
        guard track >= 0, track < pattern.tracks.count else { return }
        pattern.tracks[track].chopRef = chopRef
        if let name { pattern.tracks[track].name = name }
    }

    /// Toggle mute on a track.
    public func toggleMute(track: Int) {
        guard track >= 0, track < pattern.tracks.count else { return }
        pattern.tracks[track].isMuted.toggle()
    }

    /// Toggle solo on a track.
    public func toggleSolo(track: Int) {
        guard track >= 0, track < pattern.tracks.count else { return }
        pattern.tracks[track].isSoloed.toggle()
    }
}

// MARK: - SequencerClockDelegate

extension SequencerPlayer: SequencerClockDelegate {
    public nonisolated func sequencerClock(
        _ clock: SequencerClock,
        didAdvanceTo step: Int,
        isDownbeat: Bool
    ) {
        Task { @MainActor in
            self.fireStep(step)
        }
    }

    public nonisolated func sequencerClockDidLoop(_ clock: SequencerClock) {
        // Pattern looped - could notify UI for visual feedback
    }
}

// MARK: - Preview Helpers

extension SequencerPlayer {
    /// Preview a single track by triggering its chop once.
    public func previewTrack(_ trackIndex: Int) {
        guard trackIndex >= 0, trackIndex < pattern.tracks.count else { return }
        let track = pattern.tracks[trackIndex]
        fireChopReference(track.chopRef, velocity: track.volume, pan: track.pan)
    }

    /// Preview a chop reference directly.
    public func previewChop(_ ref: ChopReference, velocity: Float = 1.0) {
        fireChopReference(ref, velocity: velocity, pan: 0)
    }
}
