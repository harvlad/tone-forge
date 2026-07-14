// SequencePadManager.swift
//
// Owns the running SequencerPlayers behind "sequence pads" (D-023).
// A sequence pad has no audio buffer — pressing it starts a standalone
// SequencerPlayer whose packPad steps re-publish padDown/padUp to the
// same ContributionEventBus the grid uses. So a sequence pad "plays
// other pads" over time.
//
// Layering: one player per pad index, keyed in `active`. Multiple
// concurrent sequence pads each run their own wall-clock driver.
//
// Trigger semantics live in ModeCoordinator (respecting global HoldMode);
// this manager only starts/stops players.
//
// packPad tracks make sound via the bus; bundleChop/localSample/customURL
// tracks sound through `delegate` (AppState → SampleScheduler one-shots),
// set on every player at start time.

import Foundation
import Combine
import ToneForgeEngine

/// Per-pad loop position for grid animation. Emitted every step while a
/// sequence pad runs so the grid can pulse in lock with the loop tempo.
public struct SequencePulse: Equatable, Sendable {
    /// Current step (0..<stepCount).
    public let step: Int
    /// Total steps in the loop (8/16/32) — drives the meter resolution.
    public let stepCount: Int
    /// Seconds per step at the loop's effective tempo — lets the view
    /// pace its animation to the beat.
    public let secondsPerStep: Double

    public init(step: Int, stepCount: Int, secondsPerStep: Double) {
        self.step = step
        self.stepCount = stepCount
        self.secondsPerStep = secondsPerStep
    }

    /// True on quarter-note steps (0, 4, 8…) for downbeat flashes.
    public var isDownbeat: Bool { stepCount > 0 && step % 4 == 0 }
    /// Fractional loop progress (0..<1).
    public var progress: Double {
        stepCount > 0 ? Double(step) / Double(stepCount) : 0
    }
}

@MainActor
public final class SequencePadManager {

    private unowned let eventBus: ContributionEventBus
    private unowned let patternStore: SequencerPatternStore

    /// Non-bus audio sink (bundleChop/localSample/customURL). Set on
    /// each player so those track types sound when pad-triggered.
    public weak var delegate: SequencerPlayerDelegate?

    /// Grid-animation sink. Called with the pad's live `SequencePulse`
    /// on every step, and `nil` when the sequence stops. Set by
    /// ModeCoordinator to publish per-pad loop state to the grid.
    public var onPulse: ((Int, SequencePulse?) -> Void)?

    /// Running players keyed by grid pad index.
    private var active: [Int: SequencerPlayer] = [:]

    /// Per-pad step subscriptions feeding `onPulse`.
    private var pulseSubs: [Int: AnyCancellable] = [:]

    public init(
        eventBus: ContributionEventBus,
        patternStore: SequencerPatternStore,
        delegate: SequencerPlayerDelegate? = nil
    ) {
        self.eventBus = eventBus
        self.patternStore = patternStore
        self.delegate = delegate
    }

    // MARK: - Queries

    /// True if a sequence is currently running on this pad.
    public func isActive(padIdx: Int) -> Bool {
        active[padIdx]?.isPlaying ?? false
    }

    // MARK: - Control

    /// Start (or restart) the saved pattern on this pad. No-op if the
    /// pattern id is unknown. `songBPM` seeds tempo when the pattern has
    /// no bpmOverride.
    public func start(patternId: UUID, padIdx: Int, songBPM: Double) {
        guard let pattern = patternStore.pattern(id: patternId) else { return }
        stop(padIdx: padIdx)
        let player = SequencerPlayer(pattern: pattern, eventBus: eventBus)
        player.songBPM = songBPM
        player.delegate = delegate
        active[padIdx] = player

        // Publish loop position to the grid on every step. bpmOverride
        // wins (standalone loops carry their own tempo); else the song
        // BPM. 4 steps per beat → 15/bpm seconds per step.
        let stepCount = pattern.stepCount.rawValue
        let bpm = pattern.bpmOverride ?? songBPM
        let secondsPerStep = bpm > 0 ? 15.0 / bpm : 0.125
        pulseSubs[padIdx] = player.$currentStep.sink { [weak self] step in
            self?.onPulse?(padIdx, SequencePulse(
                step: step,
                stepCount: stepCount,
                secondsPerStep: secondsPerStep
            ))
        }

        player.play(sync: false)
    }

    /// Stop the sequence on this pad (if any) and drop the player.
    public func stop(padIdx: Int) {
        pulseSubs.removeValue(forKey: padIdx)?.cancel()
        guard let player = active.removeValue(forKey: padIdx) else { return }
        player.stop()
        onPulse?(padIdx, nil)
    }

    /// Stop every running sequence (panic / mode change / stop-all).
    public func stopAll() {
        let padIdxs = Array(active.keys)
        for sub in pulseSubs.values { sub.cancel() }
        pulseSubs.removeAll()
        for player in active.values { player.stop() }
        active.removeAll()
        for padIdx in padIdxs { onPulse?(padIdx, nil) }
    }
}
