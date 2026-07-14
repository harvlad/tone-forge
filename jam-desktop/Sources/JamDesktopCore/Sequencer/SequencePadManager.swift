// SequencePadManager.swift
//
// Manages multiple concurrent sequencer patterns running on pads.
// Each pad can have one pattern playing; tapping a sequence-assigned
// pad starts/stops its pattern. Patterns share a single audio delegate
// (SequencerAudioAdapter) so all triggers route through the same
// voice pool.

import Foundation
import Combine
import ToneForgeEngine

@MainActor
public final class SequencePadManager {

    // MARK: - Dependencies

    private let eventBus: ContributionEventBus
    private let patternStore: SequencerPatternStore

    /// Audio delegate for chop/synth/pack triggers.
    public weak var delegate: SequencerPlayerDelegate?

    // MARK: - Active Players

    /// Running players keyed by pad index.
    private var active: [Int: SequencerPlayer] = [:]

    /// Step subscriptions for pulse updates.
    private var stepSubs: [Int: AnyCancellable] = [:]

    // MARK: - Callbacks

    /// Called when a pad's pulse state changes (for UI animation and LED).
    /// Parameters: (padIdx, pulse or nil if stopped).
    public var onPulse: ((Int, SequencePulse?) -> Void)?

    // MARK: - Init

    public init(
        eventBus: ContributionEventBus,
        patternStore: SequencerPatternStore
    ) {
        self.eventBus = eventBus
        self.patternStore = patternStore
    }

    // MARK: - Control

    /// Start a pattern on a pad. If already running, restarts.
    public func start(patternId: UUID, padIdx: Int, songBPM: Double) {
        guard let pattern = patternStore.pattern(id: patternId) else {
            NSLog("[SequencePadManager] Pattern not found: %@", patternId.uuidString)
            return
        }

        // Stop any existing player on this pad
        stop(padIdx: padIdx)

        // Create new player
        let player = SequencerPlayer(pattern: pattern, eventBus: eventBus)
        player.songBPM = songBPM
        player.delegate = delegate
        active[padIdx] = player

        // Subscribe to step updates for pulse animation
        let stepCount = pattern.stepCount.rawValue
        let bpm = pattern.bpmOverride ?? songBPM
        let secondsPerStep = bpm > 0 ? 15.0 / bpm : 0.125  // 15 = 60/4 (quarter note at BPM)

        stepSubs[padIdx] = player.$currentStep
            .receive(on: DispatchQueue.main)
            .sink { [weak self] step in
                guard let self, self.active[padIdx] != nil else { return }
                let pulse = SequencePulse(
                    step: step,
                    stepCount: stepCount,
                    secondsPerStep: secondsPerStep
                )
                self.onPulse?(padIdx, pulse)
            }

        // Start standalone playback
        player.play(sync: false)
        NSLog("[SequencePadManager] Started pattern '%@' on pad %d", pattern.name, padIdx)
    }

    /// Stop a pattern on a pad.
    public func stop(padIdx: Int) {
        guard let player = active.removeValue(forKey: padIdx) else { return }
        stepSubs.removeValue(forKey: padIdx)?.cancel()
        player.stop()
        onPulse?(padIdx, nil)
        NSLog("[SequencePadManager] Stopped pad %d", padIdx)
    }

    /// Stop all running patterns.
    public func stopAll() {
        for padIdx in active.keys {
            stop(padIdx: padIdx)
        }
    }

    /// Check if a pad has a pattern running.
    public func isActive(padIdx: Int) -> Bool {
        active[padIdx]?.isPlaying == true
    }

    /// Toggle a pattern on a pad.
    public func toggle(patternId: UUID, padIdx: Int, songBPM: Double) {
        if isActive(padIdx: padIdx) {
            stop(padIdx: padIdx)
        } else {
            start(patternId: patternId, padIdx: padIdx, songBPM: songBPM)
        }
    }

    /// All currently active pad indices.
    public var activePads: [Int] {
        Array(active.keys)
    }
}
