// SequencerClock.swift
//
// Step-based clock for the sequencer (D-023 Phase 3). Monitors
// TransportClock and fires callbacks at step boundaries. The
// clock computes which step should be playing at any song-time
// and notifies listeners when the step changes.
//
// Key design decisions:
//   - Does NOT own a timer; instead, is polled from the main
//     display link loop or audio callback. This keeps timing
//     aligned with the render cycle and avoids timer drift.
//   - Swing is applied here: odd-numbered steps (the off-beat
//     16ths) are delayed by a fraction of the step duration.
//   - Pattern looping is handled here: step index wraps to
//     stepCount when the pattern repeats.
//   - The clock is pure logic (no audio) — it just says "now
//     we're on step N" and the SequencerPlayer fires triggers.
//
// Thread-safety: `tick()` should be called from the main thread.
// All state is behind a lock for safe reads from other threads.

import Foundation

// MARK: - Delegate

/// Protocol for receiving step change notifications.
public protocol SequencerClockDelegate: AnyObject {
    /// Called when the clock advances to a new step.
    /// - Parameters:
    ///   - clock: The clock that advanced.
    ///   - step: The new step index (0..<stepCount).
    ///   - isDownbeat: True if this step is beat 1 (step 0, 4, 8...).
    func sequencerClock(
        _ clock: SequencerClock,
        didAdvanceTo step: Int,
        isDownbeat: Bool
    )

    /// Called when the pattern loops back to step 0.
    func sequencerClockDidLoop(_ clock: SequencerClock)
}

// MARK: - Clock

/// Step clock that tracks position within a pattern.
public final class SequencerClock: @unchecked Sendable {

    // MARK: - Configuration

    /// Number of steps in the pattern (8, 16, or 32).
    public var stepCount: Int {
        get { lock.lock(); defer { lock.unlock() }; return _stepCount }
        set {
            lock.lock(); defer { lock.unlock() }
            _stepCount = max(1, newValue)
            // Reset if we're past the new step count
            if _currentStep >= _stepCount {
                _currentStep = 0
            }
        }
    }

    /// BPM for timing calculations.
    public var bpm: Double {
        get { lock.lock(); defer { lock.unlock() }; return _bpm }
        set {
            lock.lock(); defer { lock.unlock() }
            _bpm = newValue
            _stepDuration = Self.stepDuration(bpm: _bpm)
        }
    }

    /// Swing amount (0 = straight, 0.5 = max swing).
    /// Delays odd-numbered steps (off-beat 16ths) by swing × stepDuration.
    public var swing: Float {
        get { lock.lock(); defer { lock.unlock() }; return _swing }
        set {
            lock.lock(); defer { lock.unlock() }
            _swing = min(max(newValue, 0), 0.5)
        }
    }

    /// Whether the clock is looping or one-shot.
    public var isLooping: Bool {
        get { lock.lock(); defer { lock.unlock() }; return _isLooping }
        set { lock.lock(); defer { lock.unlock() }; _isLooping = newValue }
    }

    // MARK: - Delegate

    public weak var delegate: SequencerClockDelegate?

    // MARK: - State

    private let lock = NSLock()
    private var _stepCount: Int
    private var _bpm: Double
    private var _swing: Float = 0
    private var _isLooping: Bool = true
    private var _isRunning: Bool = false
    private var _currentStep: Int = 0
    private var _startSongSeconds: Double = 0
    private var _stepDuration: Double

    /// True when the clock is actively tracking steps.
    public var isRunning: Bool {
        lock.lock(); defer { lock.unlock() }
        return _isRunning
    }

    /// Current step index (0..<stepCount).
    public var currentStep: Int {
        lock.lock(); defer { lock.unlock() }
        return _currentStep
    }

    /// Duration of one step in seconds at current BPM.
    public var stepDuration: Double {
        lock.lock(); defer { lock.unlock() }
        return _stepDuration
    }

    // MARK: - Init

    public init(stepCount: Int = 16, bpm: Double = 120) {
        self._stepCount = max(1, stepCount)
        self._bpm = bpm
        self._stepDuration = Self.stepDuration(bpm: bpm)
    }

    // MARK: - Control

    /// Start the clock at the given song time.
    /// - Parameters:
    ///   - songSeconds: Song time that maps to `fromStep`'s boundary.
    ///   - fromStep: Step to resume from (default 0). The start time
    ///     is shifted back so `songSeconds` lands exactly on this
    ///     step's boundary — used by pause/resume.
    public func start(at songSeconds: Double, fromStep step: Int = 0) {
        lock.lock(); defer { lock.unlock() }
        _isRunning = true
        _stepDuration = Self.stepDuration(bpm: _bpm)
        let clamped = min(max(step, 0), _stepCount - 1)
        _currentStep = clamped
        _startSongSeconds = songSeconds - Double(clamped) * _stepDuration
    }

    /// Stop the clock.
    public func stop() {
        lock.lock(); defer { lock.unlock() }
        _isRunning = false
    }

    /// Reset to step 0 without stopping.
    public func reset() {
        lock.lock(); defer { lock.unlock() }
        _currentStep = 0
    }

    // MARK: - Tick

    /// Advance the clock based on current song time.
    /// Call this from the main display link loop or audio callback.
    /// - Parameter songSeconds: Current song time in seconds.
    /// - Returns: The current step index, or nil if not running.
    @discardableResult
    public func tick(songSeconds: Double) -> Int? {
        lock.lock()
        guard _isRunning else {
            lock.unlock()
            return nil
        }

        let elapsed = songSeconds - _startSongSeconds
        if elapsed < 0 {
            let step = _currentStep
            lock.unlock()
            return step
        }

        // Calculate raw step position
        var rawStep = Int(elapsed / _stepDuration)

        // Swing: odd steps (off-beat 16ths) fire late by
        // swing × stepDuration. Hold the previous even step until the
        // swung boundary passes so the delegate callback for the odd
        // step lands at the delayed time. Step counts are even
        // (8/16/32), so raw-step parity matches wrapped-step parity
        // across loops.
        if rawStep % 2 == 1, _swing > 0 {
            let swungStart = (Double(rawStep) + Double(_swing)) * _stepDuration
            if elapsed < swungStart { rawStep -= 1 }
        }

        // Handle looping
        var newStep: Int
        if _isLooping {
            newStep = rawStep % _stepCount
        } else {
            newStep = min(rawStep, _stepCount - 1)
            // Stop after last step in one-shot mode
            if rawStep >= _stepCount {
                _isRunning = false
                lock.unlock()
                return nil
            }
        }

        // Check if step changed
        let previousStep = _currentStep
        let didLoop = _isLooping && rawStep > 0 && newStep < previousStep

        if newStep != previousStep || didLoop {
            _currentStep = newStep

            // Calculate if this is a downbeat (every 4 steps = quarter note)
            let isDownbeat = newStep % 4 == 0

            lock.unlock()

            // Notify delegate (outside lock to avoid deadlock)
            if didLoop {
                delegate?.sequencerClockDidLoop(self)
            }
            delegate?.sequencerClock(self, didAdvanceTo: newStep, isDownbeat: isDownbeat)

            return newStep
        }

        lock.unlock()
        return newStep
    }

    /// Calculate the song-time when a given step should trigger.
    /// Applies swing to odd-numbered steps (off-beat 16ths).
    public func triggerTime(forStep step: Int, from startTime: Double) -> Double {
        lock.lock(); defer { lock.unlock() }

        let baseTime = startTime + Double(step) * _stepDuration

        // Apply swing to odd-numbered steps (1, 3, 5...): the
        // off-beat 16ths land late, on-beats (0, 2, 4...) stay put.
        if step % 2 == 1 {
            let swingDelay = Double(_swing) * _stepDuration
            return baseTime + swingDelay
        }

        return baseTime
    }

    /// Calculate which step corresponds to a given song time.
    /// Useful for UI position display.
    public func stepAt(songSeconds: Double) -> Int? {
        lock.lock(); defer { lock.unlock() }
        guard _isRunning else { return nil }

        let elapsed = songSeconds - _startSongSeconds
        if elapsed < 0 { return 0 }

        let rawStep = Int(elapsed / _stepDuration)
        if _isLooping {
            return rawStep % _stepCount
        }
        return min(rawStep, _stepCount - 1)
    }

    /// Phase within the current step (0..<1).
    /// Useful for step animation.
    public func stepPhase(at songSeconds: Double) -> Double {
        lock.lock(); defer { lock.unlock() }
        guard _isRunning, _stepDuration > 0 else { return 0 }

        let elapsed = songSeconds - _startSongSeconds
        if elapsed < 0 { return 0 }

        // Fractional position inside whichever step `elapsed` falls in.
        // (Works across pattern loops — no dependence on the wrapped
        // _currentStep, which broke after the first loop.)
        let phase = (elapsed / _stepDuration).truncatingRemainder(dividingBy: 1)
        return max(0, min(1, phase))
    }

    // MARK: - Private

    private static func stepDuration(bpm: Double) -> Double {
        // 4 steps per beat (16th notes)
        guard bpm > 0 else { return 0.125 } // fall back to 120 BPM
        let beatsPerSecond = bpm / 60.0
        return 1.0 / (beatsPerSecond * 4)
    }
}

// MARK: - Step Timing Info

extension SequencerClock {
    /// Information about step timing for UI display.
    public struct StepInfo: Sendable {
        /// Current step index (0..<stepCount).
        public let step: Int
        /// Phase within the step (0..<1).
        public let phase: Double
        /// Whether this is a downbeat (step 0, 4, 8...).
        public let isDownbeat: Bool
        /// Song time when this step started.
        public let startTime: Double
    }

    /// Get current step info at the given song time.
    public func stepInfo(at songSeconds: Double) -> StepInfo? {
        lock.lock()
        guard _isRunning, _stepDuration > 0 else {
            lock.unlock()
            return nil
        }

        let elapsed = songSeconds - _startSongSeconds
        let startSongSeconds = _startSongSeconds
        guard elapsed >= 0 else {
            lock.unlock()
            return StepInfo(
                step: 0,
                phase: 0,
                isDownbeat: true,
                startTime: startSongSeconds
            )
        }

        let rawStep = Int(elapsed / _stepDuration)
        let step = _isLooping ? rawStep % _stepCount : min(rawStep, _stepCount - 1)
        // startTime must use the unwrapped rawStep — using the wrapped
        // step index would pin startTime inside the first loop pass.
        let stepStart = startSongSeconds + Double(rawStep) * _stepDuration
        let phase = (songSeconds - stepStart) / _stepDuration

        lock.unlock()

        return StepInfo(
            step: step,
            phase: max(0, min(1, phase)),
            isDownbeat: step % 4 == 0,
            startTime: stepStart
        )
    }
}
